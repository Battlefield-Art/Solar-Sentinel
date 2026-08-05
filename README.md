# Solar-Sentinel-AIO v3

Advanced Energy Management & Backup System.

## Quick Start

### Prerequisites
- **Docker + Docker Compose** on a Linux host (or Docker Desktop).
- x86_64 machine (the image downloads `linux-amd64` binaries).

### Installation
```bash
# 1. Clone
git clone https://github.com/menotbobbybrown/Solar-Sentinel.git
cd Solar-Sentinel

# 2. Configure secrets (CHANGE the default passwords!)
cp .env.example .env
#   edit .env: MQTT_USER/MQTT_PASS, GF_SECURITY_ADMIN_PASSWORD, GEMINI_API_KEY

# 3. Build and start
docker compose up -d --build

# 4. (Optional) build the self-hosted weather service
docker compose up -d --build open-meteo
```

### First-Boot Behavior
- **InfluxDB** is onboarded automatically — the admin token is generated and
  stored at `/data/influxdb/.admin_token` (also `/data/influxdb/influx.env`).
  Buckets are created with 5-year retention.
- **MQTT** auth is enforced. Guard, Hermes, Node-RED, Home Assistant, and the
  maintenance scripts all use the credentials from `.env`.
- **Grafana** admin password comes from `GF_SECURITY_ADMIN_PASSWORD`.

### After Boot — Wire Your Sensors (required for live data)
Open `config/homeassistant/automations.yaml`, **SECTION 6: DATA PUBLISHERS**,
and point the placeholder entities at your real sensors:
- `sensor.battery_soc` → `solar/battery/soc`
- `sensor.solar_power` → `solar/pv/power`
- `sensor.<device>_power` / `switch.<device>` → `solar/eva/node/<device>/...`

Without these, Energy Guard runs on defaults and EVA has no node data.

### Access
| Service | URL |
|---------|-----|
| Home Assistant | http://localhost:8123 |
| Grafana | http://localhost:3000 |
| Node-RED | http://localhost:1880 |
| Uptime Kuma | http://localhost:3001 |
| MQTT | localhost:1883 |

## Phase 3: USB Backup + 20-Year Hardening
This version includes comprehensive system hardening and a robust USB-based backup and recovery strategy designed for long-term reliability.

## Services Included
1. **Home Assistant** - Smart home automation platform.
2. **InfluxDB 2.7.4** - Time series database for energy metrics.
3. **Grafana 10.2.3** - Visualization and dashboards.
4. **Mosquitto 2.0.18** - MQTT broker.
5. **Node-RED 3.1.3** - Flow-based automation.
6. **Uptime Kuma 1.23.11** - Monitoring and uptime checks.
7. **Energy Guard** - Custom energy monitoring and protection service.

> **Weather (Open-Meteo):** Optional. `docker-compose.yml` includes an
> `open-meteo` service built from upstream's Dockerfile (slow first build). If
> you skip it or it is unreachable, Energy Guard automatically falls back to the
> public `api.open-meteo.com` — everything still works.

## Operational Guide

### Maintenance Scripts
All maintenance scripts are located in `/data/scripts/`:

| Script | Description |
|--------|-------------|
| `usb_backup.sh` | Auto-detects USB, creates compressed backup of `/data`, rotates last 8 backups. |
| `healthcheck.sh` | Monitors SSD SMART status, disk usage, service health, and data freshness. |
| `usb_reminder.sh` | Notifies via ntfy if no backup has been performed in the last 8 days. |
| `setup_cron.sh` | Idempotently installs the system maintenance schedule. |
| `laptop_hardening.sh` | **(Host Only)** Hardens Ubuntu host for 24/7 solar sentinel operation. |

### Cron Schedule
Maintenance is automated via the following schedule:

| Task | Schedule | Command |
|------|----------|---------|
| USB Backup | Weekly (Sun 02:00) | `usb_backup.sh` |
| Health Check | Weekly (Sun 01:00) | `healthcheck.sh` (via supervisor) |
| InfluxDB Snapshot | Daily (03:00) | `influx backup` |
| Prune Snapshots | Daily (03:30) | Delete snapshots > 30 days |
| Log Rotation | Monthly (1st 04:00) | Truncate all `.log` files |
| USB Reminder | Weekly (Mon 09:00) | `usb_reminder.sh` |

## Host Hardening (20-Year Hardening)
To prepare a new laptop host for 24/7 operation:
1. Copy `/data/scripts/laptop_hardening.sh` to the host.
2. Run as root: `sudo bash laptop_hardening.sh`.
3. This will disable sleep/suspend, ignore lid close, disable swap, set timezone (Asia/Dubai), and configure unattended-upgrades.

## Disaster Recovery
See `/data/RECOVERY.md` for full details.

### Quick Restore Reference
1. Prepare host with `laptop_hardening.sh`.
2. Install Docker.
3. Plug in USB backup.
4. Restore data: `tar -xzf /path/to/usb/solar_sentinel_backups/backup_xxxx.tar.gz -C /`
5. Restart: `docker-compose up -d`

## Configuration

### Port Map
| Service | Port | External URL |
|---------|------|--------------|
| Home Assistant | 8123 | http://localhost:8123 |
| InfluxDB | 8086 | http://localhost:8086 |
| Grafana | 3000 | http://localhost:3000 |
| Mosquitto | 1883 | localhost:1883 |
| Node-RED | 1880 | http://localhost:1880 |
| Uptime Kuma | 3001 | http://localhost:3001 |

### Environment Variables (First-Boot Checklist)
Copy `.env.example` to `.env` and set your values before the first boot:

- `MQTT_USER` / `MQTT_PASS`: MQTT broker credentials (auth is now enforced — **change the defaults**).
- `GF_SECURITY_ADMIN_PASSWORD`: Grafana admin password (**change the default**).
- `GEMINI_API_KEY`: Required for the Hermes AI agent.
- `INFLUXDB_ORG`: Organization name (default `my-org`).
- `INFLUX_ADMIN_PASSWORD`: Optional; if empty a random password is generated on first boot.

Notes:
- The InfluxDB admin token is **generated automatically on first boot** and stored at `/data/influxdb/influx.env` and `/data/influxdb/.admin_token`. Guard, Hermes, Grafana, and Home Assistant all read it from there — no need to set `INFLUXDB_TOKEN`.
- Buckets (`solar_forecast`, `system_state`, `eva_nodes`, `eva_patterns`) are created automatically with a 5-year retention (override with `INFLUX_RETENTION_SECONDS`).

### Wiring Your Sensors (required for live data)
Energy Guard and EVA consume real-time data over MQTT. Open
`config/homeassistant/automations.yaml`, go to **SECTION 6: DATA PUBLISHERS**, and
point the placeholder entities at your real Home Assistant sensors:
- `sensor.battery_soc` → battery state of charge (published to `solar/battery/soc`)
- `sensor.solar_power` → current PV production in W (published to `solar/pv/power`)
- `sensor.<device>_power` / `switch.<device>` → per-device power and state for each
  appliance in the EVA registry (published to `solar/eva/node/<device>/power|state`)

Without these, Guard runs on its defaults and EVA has no node data.

### USB Backup Inside Docker
The maintenance scripts run inside the container, so the host USB drive must be
visible. Either pass the device through, or mount it and export `USB_MOUNT`:
```bash
docker run ... -e USB_MOUNT=/media/usb \
  -v /media/usb:/media/usb:ro solar-sentinel-aio:v3
```
The host-side alternative is to run `data/scripts/usb_backup.sh` directly on the
host (see `data/scripts/laptop_hardening.sh`).

## Talking to Hermes
Hermes is the natural language interface for Solar-Sentinel-AIO. You can interact with it using three methods:

1. **Node-RED Chat UI:** Navigate to `http://localhost:1880/ui` and select the **Hermes AI Chat** tab.
2. **MQTT Direct:** Publish a message to `solar/hermes/inbox` and subscribe to `solar/hermes/outbox`.
3. **ntfy Webhook:** Hermes responses are also sent as push notifications via the configured ntfy topic.

### Example Commands
- *"Hermes, what is the current system status?"*
- *"Lock the washing machine until further notice."*
- *"What's the solar forecast for tomorrow?"*
- *"Set the battery lockout threshold to 15%."*

### Intelligence Engine
Hermes uses the **Google Gemini API** (Pro model) with 12 native tools for real-time system interaction.
- Requires `GOOGLE_API_KEY` environment variable.
- Uses function calling for status, forecast, and control.
- Integrated with EVA (Energy Value Analysis) for per-device mapping.

## Monitoring & Alerts
- **MQTT**: Health metrics are published to `solar/system/health_metrics`.
- **ntfy**: Critical alerts are sent to `ntfy.sh/solar_sentinel_alerts`.
- **Logs**: Detailed logs available in `/data/logs/`.
