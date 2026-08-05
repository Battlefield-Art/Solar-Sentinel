#!/bin/bash
set -e

echo "Solar-Sentinel-AIO v3 Phase 5 - Setup"

# Create necessary directories
DIRS=(
    "/data/homeassistant"
    "/data/influxdb"
    "/data/influxdb/engine"
    "/data/grafana/logs"
    "/data/grafana/plugins"
    "/data/grafana/dashboards"
    "/data/mosquitto/data"
    "/data/mosquitto/log"
    "/data/node-red"
    "/data/uptime-kuma"
    "/data/scripts"
    "/data/logs"
    "/data/backups"
    "/data/agent"
    "/data/guard"
)

for dir in "${DIRS[@]}"; do
    if [ ! -d "$dir" ]; then
        echo "Creating directory: $dir"
        mkdir -p "$dir"
    fi
done

# Copy scripts from template to /data/scripts
if [ -d "/usr/share/solar-sentinel/data/scripts" ]; then
    cp -r /usr/share/solar-sentinel/data/scripts/* /data/scripts/
fi

# Copy RECOVERY.md
if [ -f "/usr/share/solar-sentinel/data/RECOVERY.md" ]; then
    cp /usr/share/solar-sentinel/data/RECOVERY.md /data/RECOVERY.md
fi

# Ensure all scripts are executable
chmod +x /data/scripts/*.sh 2>/dev/null || true

# Set proper ownership for directories before services start
echo "Setting directory permissions..."
chown -R solar:solar /data
chown solar:solar /data/grafana/dashboards
chown solar:solar /data/influxdb/engine
chown solar:solar /data/guard

# MQTT broker authentication (idempotent)
echo "Configuring MQTT credentials..."
MQTT_PASSWD_FILE="/etc/mosquitto/passwd"
if [ ! -f "$MQTT_PASSWD_FILE" ] && [ -n "$MQTT_USER" ] && [ -n "$MQTT_PASS" ]; then
    touch "$MQTT_PASSWD_FILE"
    mosquitto_passwd -b "$MQTT_PASSWD_FILE" "$MQTT_USER" "$MQTT_PASS"
    chown solar:solar "$MQTT_PASSWD_FILE"
    chmod 600 "$MQTT_PASSWD_FILE"
    echo "MQTT user '$MQTT_USER' configured."
elif [ ! -f "$MQTT_PASSWD_FILE" ]; then
    echo "WARNING: MQTT_USER/MQTT_PASS not set. Broker will reject clients until credentials are configured."
fi

# Run cron setup
if [ -f "/data/scripts/setup_cron.sh" ]; then
    echo "Installing cron jobs..."
    /data/scripts/setup_cron.sh
fi

# Initial configuration if missing
if [ ! -f "/data/homeassistant/configuration.yaml" ]; then
    echo "Initializing Home Assistant configuration..."
    mkdir -p /data/homeassistant
    cp -r /etc/homeassistant/* /data/homeassistant/
fi

if [ ! -f "/data/node-red/flows.json" ]; then
    echo "Initializing Node-RED flows..."
    mkdir -p /data/node-red
    sed -e "s|__MQTT_USER__|${MQTT_USER:-solar}|g" \
        -e "s|__MQTT_PASSWORD__|${MQTT_PASS:-solar123}|g" \
        /etc/node-red/flows.json > /data/node-red/flows.json
fi

# EVA Registry initialization (idempotent)
if [ ! -f "/data/guard/eva_registry.json" ]; then
    echo "Initializing EVA registry..."
    if [ -f "/usr/share/solar-sentinel/data/guard/eva_registry.json" ]; then
        cp /usr/share/solar-sentinel/data/guard/eva_registry.json /data/guard/eva_registry.json
    else
        # Create default EVA registry
        cat > /data/guard/eva_registry.json << 'EVAEOF'
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
    },
    "living_room_tv": {
        "name": "Living Room TV",
        "priority": "PHANTOM",
        "ha_entity": "switch.living_room_tv"
    },
    "coffee_maker": {
        "name": "Coffee Maker",
        "priority": "PHANTOM",
        "ha_entity": "switch.coffee_maker"
    }
}
EVAEOF
    fi
fi

# Hermes Agent Setup (idempotent)
if [ ! -f "/data/agent/hermes_history.json" ]; then
    echo "Initializing Hermes history..."
    echo "[]" > /data/agent/hermes_history.json
fi

# Copy hermes_agent.py from template to /data/agent (idempotent)
if [ -f "/usr/share/solar-sentinel/data/agent/hermes_agent.py" ]; then
    cp /usr/share/solar-sentinel/data/agent/hermes_agent.py /data/agent/hermes_agent.py
fi

if [ -f "/data/agent/hermes_agent.py" ]; then
    chmod +x /data/agent/hermes_agent.py
fi

# Ensure influxdb data directory exists
if [ ! -d "/data/influxdb/engine" ]; then
    mkdir -p /data/influxdb/engine
    chown solar:solar /data/influxdb/engine
fi

# Check for Gemini API Key
if [ -z "$GEMINI_API_KEY" ] && [ -z "$GOOGLE_API_KEY" ]; then
    echo "WARNING: GEMINI_API_KEY (or GOOGLE_API_KEY) is not set. Hermes AI agent will not function correctly."
    echo "Please set your Gemini API key in docker-compose.yml or environment."
else
    echo "Gemini API key is configured."
fi

echo "Setup complete."
