#!/bin/bash
# /data/scripts/influx_backup.sh
# Daily InfluxDB snapshot (called from cron). Resolves the admin token at
# runtime so it works before/after the first-boot InfluxDB onboarding.

INFLUX_TOKEN="${INFLUXDB_TOKEN:-}"
if [ -z "$INFLUX_TOKEN" ] && [ -f "/data/influxdb/influx.env" ]; then
    . /data/influxdb/influx.env
    INFLUX_TOKEN="${INFLUXDB_TOKEN}"
fi

if [ -z "$INFLUX_TOKEN" ]; then
    echo "InfluxDB token unavailable; skipping backup"
    exit 1
fi

influx backup "/data/backups/influx_$(date +%Y%m%d)" \
    -t "$INFLUX_TOKEN" \
    --org "${INFLUXDB_ORG:-my-org}" \
    --host "${INFLUXDB_URL:-http://localhost:8086}"
