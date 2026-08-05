#!/bin/bash
# Solar-Sentinel-AIO v3
# Grafana launcher: waits for the InfluxDB bootstrap to render the datasource
# provisioning file so the InfluxDB datasource is ready before Grafana loads.

for i in $(seq 1 150); do
    if [ -f /etc/grafana/provisioning/datasources/influxdb.yaml ] && grep -q 'token: .' /etc/grafana/provisioning/datasources/influxdb.yaml; then
        break
    fi
    sleep 2
done

exec /usr/share/grafana/bin/grafana-server --config=/etc/grafana/grafana.ini --homepath=/usr/share/grafana
