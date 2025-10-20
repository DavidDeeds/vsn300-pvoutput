🟢 VSN300 → PVOutput Bridge

A lightweight Python + Flask Docker container that polls an ABB Power-One VSN300 inverter via Modbus TCP and uploads live data to PVOutput.org
.
Includes a local web dashboard with live charts for power, energy, voltage, and temperature.

🚀 Features

Modbus TCP polling (single-phase ABB Aurora / VSN300)

Automatic PVOutput uploads (power W, energy Wh, voltage V, temperature °C)

Auto-refresh dashboard on port 8080

Daily baseline reset & uptime tracking

Safe JSON state persistence in /data

Smart night-mode detection (voltage < 100 V)

Docker-ready for Portainer or compose

⚙️ Environment Variables
Variable	Default	Description
MODBUS_HOST	192.168.1.220	Inverter IP address
MODBUS_PORT	502	Modbus TCP port
MODBUS_UNIT_ID	2	Modbus unit ID
POLL_SECONDS	300	Poll interval (seconds)
PVOUTPUT_API_KEY	(required)	Your PVOutput API key
PVOUTPUT_SYSTEM_ID	(required)	Your PVOutput system ID
STATE_DIR	/data	State directory
DRY_RUN	false	Test mode (no uploads)
DEBUG	false	Verbose logging
TZ	Australia/Perth	Local timezone

🐋 Quick Start
docker run -d \
  --name vsn300-pvoutput \
  -e PVOUTPUT_API_KEY=your_key \
  -e PVOUTPUT_SYSTEM_ID=your_sysid \
  -e MODBUS_HOST=192.168.1.220 \
  -e TZ=Australia/Perth \
  -p 8080:8080 \
  -v /path/to/vsn_data:/data \
  vsn300-pvoutput:latest


or use a simple docker-compose.yml:

version: "3.8"
services:
  vsn300-pvoutput:
    image: vsn300-pvoutput:latest
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      - PVOUTPUT_API_KEY=your_key
      - PVOUTPUT_SYSTEM_ID=your_sysid
      - MODBUS_HOST=192.168.1.220
      - TZ=Australia/Perth
    volumes:
      - ./vsn_data:/data


Access the dashboard at:
👉 http://<host-ip>:8080

📁 Persistent Data

State and daily baseline files are stored in /data.
At midnight (local TZ), a new baseline starts for daily energy.
If the container restarts later, totals are recalculated automatically.

⚖️ Licensing

© 2025 David Deeds — Licensed under the MIT License

Includes open-source components:
Flask (BSD-3-Clause), Requests (Apache 2.0), pymodbus (MIT), Chart.js (MIT)
See LICENSES.txt
 for details.

💬 Credits

Developed by David Deeds
Renewable energy data integration & monitoring tool.

✅ Tag: vsn300-pvoutput:latest | Port: 8080 | Volume: /data