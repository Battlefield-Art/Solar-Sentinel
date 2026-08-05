#!/bin/bash
# /usr/local/bin/healthcheck.sh
# Docker health check for Solar-Sentinel-AIO v3 Phase 5

set -e

# Function to check HTTP endpoint
check_http() {
    curl -sf "http://localhost:$1" > /dev/null 2>&1
}

# Function to check MQTT broker via port
check_mqtt() {
    nc -z localhost 1883 > /dev/null 2>&1
}

# Function to check supervisor process
check_supervisor_process() {
    local proc="$1"
    supervisorctl status "$proc" 2>/dev/null | grep -q "RUNNING"
}

# Home Assistant
if ! check_http 8123; then
    echo "Health check failed: Home Assistant (port 8123) not responding"
    exit 1
fi

# InfluxDB
if ! check_http 8086/health; then
    echo "Health check failed: InfluxDB (port 8086) not responding"
    exit 1
fi

# Grafana
if ! check_http 3000/api/health; then
    echo "Health check failed: Grafana (port 3000) not responding"
    exit 1
fi

# Mosquitto (MQTT broker)
if ! check_mqtt; then
    echo "Health check failed: Mosquitto (port 1883) not responding"
    exit 1
fi

# Node-RED
if ! check_http 1880; then
    echo "Health check failed: Node-RED (port 1880) not responding"
    exit 1
fi

# Uptime Kuma
if ! check_http 3001; then
    echo "Health check failed: Uptime Kuma (port 3001) not responding"
    exit 1
fi

# Energy Guard via supervisorctl
if command -v supervisorctl >/dev/null 2>&1; then
    if ! check_supervisor_process energy-guard; then
        echo "Health check failed: Energy Guard process not running"
        exit 1
    fi
fi

# Hermes Agent via supervisorctl (Phase 5)
if command -v supervisorctl >/dev/null 2>&1; then
    if ! check_supervisor_process hermes-agent; then
        echo "Health check failed: Hermes Agent process not running"
        exit 1
    fi
fi

echo "All services are healthy"
exit 0
