VSN300 → PVOutput Bridge

A lightweight, self-contained Modbus-to-PVOutput bridge and live dashboard for some ABB / Power-One inverters (VSN300, UNO, Aurora, etc.).

Im using this on a 2012 week 35 manufactured inverter with a VSN300 modbus to TCP card in it, and it works. But due to changes in ownership of the company (Power-One, ABB Fimer, etc.) and subsequent changes in the Modbus registers implemented and used in these units (even though theyre SunSpec), means this may not work for you. Sometimes Modbus registers have an offset of 0, 70 or 80 (80 in this code configuration) or higher registers for 3 Phase models.
Heres the code, make your changes and use as required.

You can pull from Docker hub also **daviddeeds/vsn300-pvoutput**

Includes a local web dashboard with live charts for power, energy, voltage, and temperature.

Features

- Modbus TCP polling (single-phase ABB Aurora / VSN300)

- Automatic PVOutput uploads (power W, energy Wh, voltage V, temperature °C)

- Auto-refresh dashboard on port 8080

- Daily baseline reset & uptime tracking

- Safe JSON state persistence in /data

- Smart night-mode detection (voltage < 100 V)

- Docker-ready for Portainer or compose

Environment Variables

| Variable  | Default | Description |
| ------------- | ------------- | ------------- |
| MODBUS_HOST  | 192.168.1.123  | Inverter IP address |
| MODBUS_PORT  | 502  | Modbus TCP port |
| MODBUS_UNIT_ID  | 2  | Modbus unit ID |
| POLL_SECONDS  | 300  | Poll interval (seconds) |
| PVOUTPUT_API_KEY  | (required)  | Your PVOutput API key |
| PVOUTPUT_SYSTEM_ID  | (required)  | Your PVOutput system ID |
| STATE_DIR  | /data  | State directory |
| DRY_RUN  | false  | Test mode (no uploads) |
| DEBUG  | false  | Verbose logging |
| TZ  | Australia/Perth  | Your Local timezone |

Access the dashboard at:
http://192.168.1.123:8080 (your IP address replaces **192.168.1.123** !)

Persistent Data
- State and daily baseline files are stored in /data.
- At midnight (local TZ), a new baseline starts for daily energy.
- If the container restarts later, totals are recalculated automatically.

Licensing

© 2025 David Deeds — Licensed under the MIT License

Includes open-source components:
- Flask (BSD-3-Clause)
- Requests (Apache 2.0)
- pymodbus (MIT)
- Chart.js (MIT)

See LICENSES.txt for details.

Credits
- Developed by David Deeds

This is a simple renewable energy data integration & monitoring tool with little plans for future development but theres alot to take away from the code and calculations here.