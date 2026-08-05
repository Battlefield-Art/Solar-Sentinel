#!/bin/bash
# Solar-Sentinel-AIO v3
# Home Assistant launcher: waits for the InfluxDB bootstrap env file, then
# exposes the resolved host/port so the influxdb integration can load.

for i in $(seq 1 150); do
    if [ -f /data/influxdb/influx.env ]; then
        break
    fi
    sleep 2
done

if [ -f /data/influxdb/influx.env ]; then
    . /data/influxdb/influx.env
    export INFLUXDB_HOST="$(echo "$INFLUXDB_URL" | sed 's|https\?://||' | cut -d: -f1)"
    export INFLUXDB_PORT="$(echo "$INFLUXDB_URL" | sed 's|https\?://||' | cut -d: -f2 | cut -d/ -f1)"
fi

exec hass -c /data/homeassistant
