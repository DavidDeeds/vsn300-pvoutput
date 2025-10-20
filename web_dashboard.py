#!/usr/bin/env python3
import os, json, threading, logging, signal
from datetime import datetime, timedelta
from flask import Flask, jsonify, Response
import requests
from pymodbus.client.sync import ModbusTcpClient

# ========== CONFIG ==========
MODBUS_HOST = os.getenv("MODBUS_HOST", "192.168.1.220")
MODBUS_PORT = int(os.getenv("MODBUS_PORT", "502"))
MODBUS_UNIT_ID = int(os.getenv("MODBUS_UNIT_ID", "2"))
POLL_SECONDS = max(30, int(os.getenv("POLL_SECONDS", "300")))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
PV_API_KEY = os.getenv("PVOUTPUT_API_KEY", "")
PV_SYSTEM_ID = os.getenv("PVOUTPUT_SYSTEM_ID", "")
STATE_DIR = os.getenv("STATE_DIR", "/data")
os.makedirs(STATE_DIR, exist_ok=True)
STATE_PATH = os.path.join(STATE_DIR, "state.json")

# ========== LOGGING ==========
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("vsn300-pvoutput")

# ========== STATE ==========
state_lock = threading.Lock()
state = {
    "debug": DEBUG, "dry_run": DRY_RUN,
    "inverter_connected": False,
    "last_upload": None, "uptime_minutes_today": 0,
    "records": [],
    "ac_voltage": None, "grid_freq_hz": None, "inverter_temp_c": None,
    "energy_today_kwh": 0.0, "energy_total_kwh": None,
    "peak_power_w": 0, "status_code": None,
    "status_text": "Unknown", "status_class": "muted",
    "dq_text": "DATA OK", "dq_class": "ok",
    "_last_sample_ts": None, "_last_energy_wh": 0.0,
    "_midnight": None
}
stop_event = threading.Event()

# ---------- Helpers ----------
def _with_lock_read():
    with state_lock: return dict(state)

def u32_from_words(low, high):
    return ((int(high) & 0xFFFF) << 16) | (int(low) & 0xFFFF)

def today_midnight_local():
    n = datetime.now()
    return datetime(n.year, n.month, n.day)

def decode_status(code):
    m = {0:("Off","muted"),1:("Sleep","sleep"),4:("ON","ok"),5:("Fault","error"),
         91:("ON","ok"),92:("Sleep","sleep")}
    return m.get(code, ("Unknown","muted"))

def save_state():
    """Safely write the current state.json with proper UTF-8 and no NaN values."""
    try:
        with state_lock:
            tmp = dict(state)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(tmp, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())  # ensure data hits disk
    except Exception as e:
        log.warning(f"Save state fail: {e}")

def load_state():
    if not os.path.exists(STATE_PATH):
        return
    try:
        with open(STATE_PATH) as f:
            loaded = json.load(f)
        with state_lock:
            state.update(loaded)
            # Set a startup placeholder for data quality
            state["dq_text"], state["dq_class"] = "STARTING", "dq_warn"
        log.info("Loaded previous state.json")
    except Exception as e:
        log.warning(f"Load state fail: {e}")

def pvoutput_addstatus(power_w, energy_wh, voltage_v=None, temp_c=None):
    if DRY_RUN:
        msg = f"[DRY_RUN] PVOutput v1={energy_wh}Wh v2={power_w}W"
        if voltage_v is not None:
            msg += f" v6={voltage_v}V"
        if temp_c is not None:
            msg += f" v5={temp_c}C"
        log.info(msg)
        return True
    if not PV_API_KEY or not PV_SYSTEM_ID:
        log.warning("Missing PVOutput creds."); return False
    h={"X-Pvoutput-Apikey":PV_API_KEY,"X-Pvoutput-SystemId":PV_SYSTEM_ID}
    now=datetime.now()
    d={"d":now.strftime("%Y%m%d"),"t":now.strftime("%H:%M"),
       "v1":int(round(energy_wh)),"v2":int(round(power_w))}
    # include optional voltage and temperature
    if temp_c is not None:
        d["v5"] = round(temp_c, 1)     # °C
    if voltage_v is not None:
        d["v6"] = round(voltage_v, 1)  # Volts
    try:
        r=requests.post("https://pvoutput.org/service/r2/addstatus.jsp",
                        headers=h,data=d,timeout=10)
        ok=r.status_code==200 and not (r.text or "").upper().startswith("ERROR")
        log.info("PVOutput upload OK" if ok else f"PVOutput error {r.text.strip()}")
        return ok
    except Exception as e: log.warning(f"PVOutput upload exception: {e}"); return False

def detect_night(ac_voltage, inverter_connected):
    """Return (status_text, status_class, night_flag)"""
    if not inverter_connected or ac_voltage is None or ac_voltage < 100:
        return ("Night", "night", True)
    return ("ON", "ok", False)


# ---------- Modbus ----------
def read_regs(start,count):
    c=ModbusTcpClient(MODBUS_HOST,port=MODBUS_PORT,timeout=4)
    try:
        if not c.connect(): return None
        r=c.read_holding_registers(start,count,unit=MODBUS_UNIT_ID)
        if not r or r.isError(): return None
        return r.registers
    finally:
        try: c.close()
        except: pass

def read_legacy_block():
    """Read and decode Modbus registers from the inverter (VSN300 single-phase)."""
    regs = read_regs(80, 40)
    if not regs:
        return None
    if DEBUG:
        log.debug(f"Regs80–119: {regs}")
    try:
        # Basic telemetry
        v = round(regs[0] / 10.0, 1)          # 80: Voltage (×0.1)
        f = round(regs[6] / 100.0, 2)         # 86: Frequency (×0.01)
        t = round(regs[26] / 10.0, 1)         # 106: Temperature (×0.1)
        p = int(regs[4])                      # 84: Power (W)
        code = regs[8]                        # 88: Status code

        # Lifetime energy (SunSpec 40093–40094)
        low = regs[14]                        # 94 (low word)
        high = regs[15]                       # 95 (high word)
        sf_reg = regs[16]                     # 96 (scale factor)
        sf = int(sf_reg if sf_reg < 32768 else sf_reg - 65536)
        e_raw = (low << 16) | high            # Little-endian
        e_wh = e_raw * (10 ** sf)

        # Load or update baseline (for daily energy)
        baseline_file = os.path.join(STATE_DIR, "energy_baseline.json")
        today = datetime.now().strftime("%Y-%m-%d")
        baseline = {"day": today, "wh": e_wh}
        if os.path.exists(baseline_file):
            try:
                with open(baseline_file) as bf:
                    prev = json.load(bf)
                if prev.get("day") == today:
                    energy_today_wh = max(0, e_wh - prev.get("wh", e_wh))
                else:
                    energy_today_wh = 0
                    baseline = {"day": today, "wh": e_wh}
            except Exception as e:
                log.warning(f"Baseline read error: {e}")
                energy_today_wh = 0
        else:
            energy_today_wh = 0

        # Only update baseline file when it changes (new day)
        if not os.path.exists(baseline_file):
            with open(baseline_file, "w") as bf:
                json.dump(baseline, bf)
                bf.flush()
                os.fsync(bf.fileno())  # ensure baseline is written to disk
        else:
            try:
                with open(baseline_file) as bf:
                    prev = json.load(bf)
                if prev.get("day") != today:
                    with open(baseline_file, "w") as bf:
                        json.dump(baseline, bf)
                        bf.flush()
                        os.fsync(bf.fileno())  # ensure baseline is written to disk
            except Exception as e:
                log.warning(f"Baseline check failed: {e}")

        if DEBUG:
            log.debug(f"Decoded: V={v} F={f} P={p} E={e_wh:.2f}Wh SF={sf}")

        return {
            "ac_voltage": v,
            "grid_freq_hz": f,
            "inverter_temp_c": t,
            "power_w": p,
            "energy_lifetime_wh": e_wh,
            "energy_today_wh": energy_today_wh,
            "status_code": code
        }

    except Exception as e:
        log.warning(f"Decode error: {e}")
        return None

# ---------- Poller ----------
def poller_loop():
    log.info(f"Starting poller @ {MODBUS_HOST}:{MODBUS_PORT}, {POLL_SECONDS}s")
    load_state()
    while not stop_event.is_set():
        now = datetime.now()
        data = None
        # Reset baseline at midnight
        midnight = today_midnight_local()
        with state_lock:
            last_midnight = state.get("_midnight")
        if not last_midnight or datetime.fromisoformat(last_midnight) < midnight:
            log.info("🕛 Midnight rollover — resetting daily baseline")
            baseline_file = os.path.join(STATE_DIR, "energy_baseline.json")
            if os.path.exists(baseline_file):
                os.remove(baseline_file)
            with state_lock:
                state["_midnight"] = midnight.isoformat()
                state["uptime_minutes_today"] = 0  # ← Reset uptime each new day
                state["records"] = []  # ← Reset chart data for new day
        try:
            data = read_legacy_block()
            if data:
                with state_lock:
                    v, f, t, p, e_wh_life, e_wh_today, code = (
                        data[k] for k in (
                            "ac_voltage", "grid_freq_hz", "inverter_temp_c",
                            "power_w", "energy_lifetime_wh", "energy_today_wh", "status_code"
                        )
                    )
                    # Capture the previous sample timestamp BEFORE updating state
                    prev_ts_str = state.get("_last_sample_ts")

                    # Detect inverter state
                    st_txt, st_cls = decode_status(code)
                    night_txt, night_cls, is_night = detect_night(v, True)
                    if is_night:
                        st_txt, st_cls = night_txt, night_cls

                    # Update state WITHOUT _last_sample_ts here
                    state.update({
                        "ac_voltage": v if 150 <= v <= 270 else None,
                        "grid_freq_hz": f,
                        "inverter_temp_c": t,
                        "energy_today_kwh": round(e_wh_today / 1000, 3),
                        "energy_total_kwh": round(e_wh_life / 1000, 3),
                        "status_code": code,
                        "status_text": st_txt,
                        "status_class": st_cls,
                        "peak_power_w": max(state.get("peak_power_w", 0), int(p)),
                        "_last_energy_wh": float(e_wh_today),   # track today's Wh
                        "inverter_connected": not is_night,
                    })

                    # ---- Fix uptime drift on first poll ----
                    if not is_night and p > 5 and prev_ts_str:
                        try:
                            prev_ts = datetime.fromisoformat(prev_ts_str)
                            elapsed_min = (now - prev_ts).total_seconds() / 60
                            # Cap huge gaps (e.g., after downtime)
                            elapsed_min = min(elapsed_min, (POLL_SECONDS * 2) / 60.0)
                            if elapsed_min > 0:
                                state["uptime_minutes_today"] = round(
                                    state.get("uptime_minutes_today", 0) + elapsed_min, 1
                                )
                        except Exception:
                            # If parsing fails, skip increment this cycle
                            pass
                    # ----------------------------------------

                    # Set the new sample timestamp AFTER uptime calc
                    state["_last_sample_ts"] = now.isoformat()

                # Upload to PVOutput whenever inverter is awake (even at 0 W)
                if not is_night:
                    if pvoutput_addstatus(int(p), float(e_wh_today), voltage_v=v, temp_c=t):
                        state["last_upload"] = now.isoformat(timespec="seconds")
                else:
                    log.info("Nighttime — skipping PVOutput upload (inverter asleep)")

                # Append chart record (store today's Wh so chart shows kWh when /1000)
                rec = {
                    "timestamp": now.isoformat(timespec="seconds"),
                    "power_w": int(p),
                    "energy_wh": int(e_wh_today)
                }
                with state_lock:
                    if len(state["records"]) >= 288:
                        state["records"].pop(0)
                    state["records"].append(rec)
                save_state()

            else:
                # No data this cycle → mark Offline
                with state_lock:
                    state["inverter_connected"] = False
                    state["status_text"] = "Offline"
                    state["status_class"] = "muted"
                    state["ac_voltage"] = None
                    state["grid_freq_hz"] = None
                    state["inverter_temp_c"] = None
                save_state()
            # --- Data Quality / Freshness (always evaluate) ---
            with state_lock:
                age_s = (
                    (now - datetime.fromisoformat(state.get("_last_sample_ts", now.isoformat()))).total_seconds()
                    if state.get("_last_sample_ts") else 9999
                )
                if not state.get("inverter_connected", False):
                    dq_text, dq_class = "OFFLINE", "dq_off"
                elif age_s < POLL_SECONDS * 1.5:
                    dq_text, dq_class = "LIVE", "dq_ok"
                elif age_s < POLL_SECONDS * 3:
                    dq_text, dq_class = "STALE", "dq_warn"
                else:
                    dq_text, dq_class = "NO DATA", "dq_off"

                state["dq_text"] = dq_text
                state["dq_class"] = dq_class
        except Exception as e:
            log.warning(f"Poll error: {e}")
        stop_event.wait(POLL_SECONDS)
# ---------- Flask ----------
app=Flask(__name__)

@app.route("/")
def root():
    s = _with_lock_read()
    e_today = f"{s.get('energy_today_kwh',0):.3f} kWh"

    # --- Format timestamps without 'T' ---
    lp = s.get('last_upload', '—')
    if isinstance(lp, str) and 'T' in lp:
        lp = lp.replace('T', ' ')
    last_poll = s.get('_last_sample_ts')
    if last_poll:
        try:
            dt = datetime.fromisoformat(last_poll)
            last_poll = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            last_poll = str(last_poll).replace('T', ' ')
    else:
        last_poll = '—'
    # --- Format uptime as hours and minutes ---
    uptime_min = s.get('uptime_minutes_today', 0)
    hours, mins = divmod(int(uptime_min), 60)
    uptime_str = f"{hours}h {mins}m"
    # --- Build the HTML page ---
    html = f"""
<!doctype html><html><head>
<meta charset='utf-8'/>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<title>VSN300 → PVOutput ({s['status_text']})</title>
<script src='https://cdn.jsdelivr.net/npm/chart.js'></script>
<style>
body{{font-family:system-ui;background:#0b1020;color:#e6ecff;margin:20px;}}
.status{{background:#121a33;border-radius:12px;padding:16px;max-width:320px;min-width:300px;}}
.chart{{background:#121a33;border-radius:16px;padding:16px;flex-grow:1;height:65vh;}}
.pill{{padding:2px 8px;border-radius:12px;font-size:12px;margin-left:6px;}}
.ok{{background:#14331a;color:#7fff9c;}}
.sleep{{background:#0f203a;color:#8fc5ff;}}
.error{{background:#3a0f0f;color:#ff6b6b;}}
.muted{{color:#9fb0ff;}}
.night{{background:#1e2233;color:#b0c8ff;}}
.dq_ok{{display:inline-block;width:10px;height:10px;border-radius:50%;background:#00ff88;margin-left:8px;}}
.dq_warn{{display:inline-block;width:10px;height:10px;border-radius:50%;background:#ffbb00;margin-left:8px;}}
.dq_off{{display:inline-block;width:10px;height:10px;border-radius:50%;background:#ff4444;margin-left:8px;}}
</style></head><body>
<h2>
VSN300 → PVOutput 
<span class='pill {s['status_class']}'>{s['status_text']}</span>
<span class='{s.get('dq_class','dq_off')}' title='Data Quality: {s.get('dq_text','NO DATA')}'></span>
</h2>
<div style='display:flex;flex-wrap:wrap;gap:20px;align-items:flex-start;'>
<div class='status'>
<b>Power:</b> {s.get('records', [])[-1].get('power_w', 0) if s.get('records') else 0} W<br>
<b>Energy Today:</b> {e_today}<br>
<b>Lifetime Energy:</b> {s.get('energy_total_kwh','—')} kWh<br>
<b>AC Voltage:</b> {s.get('ac_voltage','—')} V<br>
<b>Temp:</b> {s.get('inverter_temp_c','—')} °C<br>
<b>Freq:</b> {s.get('grid_freq_hz','—')} Hz<br>
<b>PVOutput Mode:</b> {'DRY RUN' if s['dry_run'] else 'LIVE'}<br>
<b>Last Poll:</b> {last_poll}<br>
<b>Last Upload:</b> {lp}<br>
<b>Uptime:</b> {uptime_str}<br>
<a href='/raw' style='color:#8fc5ff;'>Diagnostics / raw</a>
</div>
<div class='chart'><canvas id='c'></canvas></div></div>
<script>
let ch;
async function load(){{const r=await fetch('/data');return r.json();}}
function draw(l,p,e){{
  if(!ch) {{
    ch = new Chart(document.getElementById('c'), {{
      type: 'line',
      data: {{
        labels: l,
        datasets: [
          {{
            label: 'Power (W)',
            data: p,
            yAxisID: 'y',
            borderColor: '#5a8e56',   // was 007f3f dark green
            tension: .25,
            fill: false
          }},
          {{
            label: 'Energy (kWh)',
            data: e,
            yAxisID: 'y1',
            borderColor: '#ccff69',   // was 00ff99
            backgroundColor: '#e1ffa5', // was rgba(0,255,153,0.15) soft green fill
            tension: .3,
            fill: true
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{
            offset: false,
            ticks: {{ color: '#e6ecff' }}
          }},
          y: {{
            beginAtZero: true,
            position: 'left',
            grid: {{ offset: false }},
            ticks: {{ color: '#e6ecff' }},
            title: {{
              display: true,
              text: 'Power (W)',
              color: '#e6ecff'
            }}
          }},
          y1: {{
            beginAtZero: true,
            position: 'right',
            grid: {{ drawOnChartArea: false, offset: false }},
            ticks: {{ color: '#e6ecff' }},
            title: {{
              display: true,
              text: 'Energy (kWh)',
              color: '#e6ecff'
            }}
          }}
        }},
        layout: {{ padding: 0 }},
        plugins: {{
          legend: {{
            labels: {{
              color: '#e6ecff',
              font: {{ size: 13, family: 'system-ui' }},
              padding: 12
            }}
          }},
          title: {{
            display: true,
            text: 'Live Power and Energy',
            color: '#e6ecff',
            font: {{ size: 16, weight: 'bold', family: 'system-ui' }},
            padding: {{ top: 8, bottom: 8 }}
          }},
          tooltip: {{
            titleColor: '#e6ecff',
            bodyColor: '#e6ecff',
            backgroundColor: 'rgba(18,26,51,0.9)',
            borderColor: '#00ff99',
            borderWidth: 1
          }}
        }}
      }}
    }});
  }} else {{
    ch.data.labels = l;
    ch.data.datasets[0].data = p;
    ch.data.datasets[1].data = e;
    ch.update();
  }}
}}
async function refresh(){{ 
  const d = await load(); 
  const r = d.records || []; 
  draw(
    r.map(x => (x.timestamp || '').slice(11,16)),
    r.map(x => x.power_w),
    r.map(x => (x.energy_wh || 0) / 1000)
  );
}}
refresh();
setInterval(refresh,60000);
</script></body></html>"""
    return Response(html, mimetype="text/html")

@app.route("/data")
def data():
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH) as f: return jsonify(json.load(f))
    except: pass
    return jsonify(_with_lock_read())

@app.route("/raw")
def raw():
    try:
        return jsonify({
            "timestamp": datetime.now().isoformat(),
            "regs_80_119": read_regs(80, 40)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------- Main ----------
def _sig(sig,frm): log.info(f"Signal {sig}"); stop_event.set()
if __name__=="__main__":
    signal.signal(signal.SIGTERM,_sig); signal.signal(signal.SIGINT,_sig)
    threading.Thread(target=poller_loop,daemon=True).start()
    log.info("Serving dashboard on 0.0.0.0:8080 (http://localhost:8080)")
    app.run(host="0.0.0.0",port=8080,threaded=True)
