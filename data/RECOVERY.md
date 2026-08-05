# Solar-Sentinel-AIO v3 Disaster Recovery Guide

This guide covers scenarios for restoring the Solar-Sentinel-AIO system in case of hardware or software failure.

> **InfluxDB token:** The admin token is generated on first boot and stored at
> `/data/influxdb/.admin_token` (also in `/data/influxdb/influx.env`). Use it in
> place of `YOUR_TOKEN`/`my-token` in the commands below:
> `docker exec solar-sentinel cat /data/influxdb/.admin_token`

---

## Scenario 1: Laptop dead with USB backup

**Goal:** Restore the entire system on new hardware.

1. Install Ubuntu on the new laptop.
2. Run `laptop_hardening.sh` from the USB backup to prepare the host.
3. Install Docker and Docker Compose.
4. Clone the Solar-Sentinel-AIO repository.
5. Plug in the USB backup drive.
6. Locate the latest `backup_YYYYMMDD_HHMMSS.tar.gz` on the USB.
7. Restore the `/data` directory:
   ```bash
   tar -xzf /path/to/usb/solar_sentinel_backups/backup_xxxx.tar.gz -C /
   ```
8. Start the containers:
   ```bash
   docker-compose up -d
   ```

---

## Scenario 2: SSD failed with USB backup

**Goal:** Replace SSD and restore system.

1. Replace the failed SSD.
2. Reinstall Ubuntu.
3. Follow the same steps as Scenario 1.

---

## Scenario 3: Container broken, /data intact

**Goal:** Rebuild and restart the containerized services.

1. If only the container is corrupted but the host `/data` volume is intact:
   ```bash
   docker-compose down
   docker-compose pull
   docker-compose up -d --build
   ```
2. The `setup.sh` script will run on start and ensure configurations are in place.

---

## Scenario 4: USB missing, SSD survived

**Goal:** Restore InfluxDB data from daily snapshots.

1. If the SSD is fine but you lost the USB backup and need to restore InfluxDB:
2. Look in `/data/backups/influx_YYYYMMDD/` for daily snapshots.
3. Use the influx restore command:
   ```bash
   docker exec -it solar-sentinel influx restore /data/backups/influx_YYYYMMDD/ --token $(docker exec solar-sentinel cat /data/influxdb/.admin_token)
   ```

---

## Scenario 5: Gemini/Hermes recovery

**Problem:** Hermes is unresponsive or fails to connect to the Gemini API.

**Symptoms:**
- Hermes logs show "GEMINI_API_KEY not set" or API errors
- Hermes status topic shows OFFLINE or no responses

**Troubleshooting Steps:**

### Step 1: Verify API Key
```bash
# Check if GEMINI_API_KEY is set
docker exec solar-sentinel env | grep GEMINI_API_KEY

# If not set, add to docker-compose.yml or environment
# GEMINI_API_KEY: "your-key-here"
```

### Step 2: Check Hermes Logs
```bash
docker exec solar-sentinel tail -f /data/logs/hermes.log
```

### Step 3: Test Gemini API Connectivity
```bash
docker exec solar-sentinel curl -s "https://generativelanguage.googleapis.com/v1beta/models" -H "Authorization: Bearer YOUR_API_KEY"
```

### Step 4: Restart Hermes
```bash
docker exec solar-sentinel supervisorctl restart hermes-agent
```

### Step 5: Check MQTT Connection
```bash
docker exec solar-sentinel supervisorctl status hermes-agent
docker logs solar-sentinel 2>&1 | grep -i mqtt
```

---

## Scenario 6: EVA Registry Corruption

**Problem:** The Energy Map shows no devices or the Registry is corrupted.

**Symptoms:**
- `solar/eva/map` topic returns empty nodes
- Guard logs show "Error loading EVA registry"

**Troubleshooting Steps:**

### Step 1: Check Registry File
```bash
cat /data/guard/eva_registry.json
```

### Step 2: Restore from Default
```bash
# Recreate default registry
cat > /data/guard/eva_registry.json << 'EOF'
{
    "washing_machine": {
        "name": "Washing Machine",
        "priority": "SHIFTABLE",
        "ha_entity": "switch.washing_machine"
    },
    "dishwasher": {
        "name": "Dishwasher",
        "priority": "SHIFTABLE",
        "ha_entity": "switch.dishwasher"
    },
    "water_heater": {
        "name": "Water Heater",
        "priority": "LUXURY",
        "ha_entity": "switch.water_heater"
    },
    "air_conditioner": {
        "name": "Air Conditioner",
        "priority": "LUXURY",
        "ha_entity": "switch.air_conditioner"
    },
    "ev_charger": {
        "name": "EV Charger",
        "priority": "SHIFTABLE",
        "ha_entity": "switch.ev_charger"
    }
}
EOF
```

### Step 3: Restart Energy Guard
```bash
docker exec solar-sentinel supervisorctl restart energy-guard
```

### Step 4: Trigger Map Publish
```bash
docker exec solar-sentinel mosquitto_pub -t solar/eva/command -m "PUBLISH_MAP"
```

---

## Scenario 7: InfluxDB EVA Buckets Missing

**Problem:** EVA metrics are not appearing in Grafana.

**Symptoms:**
- Grafana shows "No data" for EVA panels
- Guard logs show bucket errors

**Troubleshooting Steps:**

### Step 1: Verify Buckets Exist
```bash
docker exec solar-sentinel influx bucket list --token $(docker exec solar-sentinel cat /data/influxdb/.admin_token) --org my-org
```

### Step 2: Create Missing Buckets
```bash
docker exec solar-sentinel influx bucket create --name eva_nodes --org my-org --token $(docker exec solar-sentinel cat /data/influxdb/.admin_token)
docker exec solar-sentinel influx bucket create --name eva_patterns --org my-org --token $(docker exec solar-sentinel cat /data/influxdb/.admin_token)
```

### Step 3: Run Setup Script
```bash
docker exec solar-sentinel /usr/local/bin/setup.sh
```

### Step 4: Check Guard Logs
```bash
docker exec solar-sentinel tail -f /data/logs/guard.log | grep -i influx
```

---

## Scenario 8: MQTT Namespace Issues

**Problem:** Services not communicating due to topic mismatches.

**Symptoms:**
- Guard doesn't receive battery/PV updates
- Hermes not receiving messages
- Node-RED flows not working

**Troubleshooting Steps:**

### Verify All Topics Use solar/ Namespace
```bash
# Check subscribed topics
docker exec solar-sentinel mosquitto_sub -t '#' -v

# Common issues:
# - Old configs may use "energy/" instead of "solar/"
# - Home Assistant MQTT discovery may use different prefixes
```

### Fix MQTT Topics
```bash
# Update Home Assistant configuration.yaml
mqtt:
  broker: localhost
  discovery: false  # Disable auto-discovery to prevent namespace conflicts
```

---

## Scenario 9: Hermes Tool Execution Failures

**Problem:** Hermes responds but tools don't work.

**Symptoms:**
- Hermes responds but appliances don't lock/unlock
- Notifications not sent
- Forecast queries fail

**Troubleshooting Steps:**

### Step 1: Check MQTT Topics
```bash
# Test manual MQTT publish
docker exec solar-sentinel mosquitto_pub -t solar/appliance/washing_machine/lock -m "LOCK"
docker exec solar-sentinel mosquitto_pub -t solar/guard/config/soc_lockout -m "25.0"
```

### Step 2: Verify Guard is Running
```bash
docker exec solar-sentinel supervisorctl status energy-guard
```

### Step 3: Check Home Assistant Entities
```bash
# Verify switches exist in HA
curl -s http://localhost:8123/api/states | jq '.[] | select(.entity_id | startswith("switch."))'
```

---

## Scenario 10: Optimal Window Not Found

**Problem:** EVA optimal window finder not working.

**Symptoms:**
- `solar/eva/optimal_window` topic empty
- Guard logs show "Not enough forecast data"

**Troubleshooting Steps:**

### Step 1: Check Forecast Data
```bash
docker exec solar-sentinel influx query "from(bucket: \"solar_forecast\") |> range(start: -24h) |> limit(n:5)"
```

### Step 2: Verify Weather Service
```bash
# Local self-hosted open-meteo (if configured as a compose service):
curl -s "http://localhost:8080/v1/forecast?latitude=25.2048&longitude=55.2708&hourly=temperature_2m&timezone=Asia/Dubai"
# If unreachable, Energy Guard falls back to the public api.open-meteo.com.
```

### Step 3: Manually Trigger Optimization
```bash
docker exec solar-sentinel mosquitto_pub -t solar/eva/command -m "EVA_OPTIMIZE"
```

---

## Manual Operations Quick Reference

```bash
# Check Logs
docker logs solar-sentinel
docker exec solar-sentinel tail -f /data/logs/guard.log
docker exec solar-sentinel tail -f /data/logs/hermes.log

# Restart Services
docker exec solar-sentinel supervisorctl restart all
docker exec solar-sentinel supervisorctl restart hermes-agent
docker exec solar-sentinel supervisorctl restart energy-guard

# Manual Backup
docker exec solar-sentinel /data/scripts/usb_backup.sh

# InfluxDB CLI
docker exec -it solar-sentinel influx v1 shell
docker exec -it solar-sentinel influx bucket list
docker exec -it solar-sentinel influx query "from(bucket: \"eva_nodes\") |> limit(n:10)"

# Check Health
docker exec solar-sentinel /data/scripts/healthcheck.sh

# MQTT Commands
docker exec solar-sentinel mosquitto_pub -t solar/guard/command -m "FORCE_DECISION"
docker exec solar-sentinel mosquitto_pub -t solar/eva/command -m "EVA_PHANTOM_CUT"
docker exec solar-sentinel mosquitto_pub -t solar/eva/command -m "PUBLISH_MAP"
```

---

## First-Boot Checklist

- [ ] Verify USB drive is mounted and detected
- [ ] Check `GEMINI_API_KEY` is configured in environment
- [ ] Verify InfluxDB buckets are created
- [ ] Check MQTT broker is running
- [ ] Verify all services are ONLINE in Node-RED
- [ ] Test Gemini API connectivity
- [ ] Check Grafana dashboards are loading

---

## Emergency Rollback

If Phase 5 causes issues and you need to rollback:

1. Stop the container:
   ```bash
   docker-compose down
   ```

2. Restore previous configuration files from backup

3. Rebuild without Phase 5 changes:
   ```bash
   # Edit Dockerfile to remove Gemini dependencies
   # Edit requirements.txt to add Ollama dependencies
   docker-compose up -d --build
   ```
