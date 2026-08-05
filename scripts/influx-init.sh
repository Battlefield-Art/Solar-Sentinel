#!/bin/bash
# Solar-Sentinel-AIO v3
# InfluxDB bootstrap: one-time onboarding, bucket creation, and shared env/grafana config.
# Run as root by supervisord after influxd is up.

set -e

INFLUX_URL="${INFLUXDB_URL:-http://localhost:8086}"
INFLUX_ORG="${INFLUXDB_ORG:-my-org}"
INFLUX_ADMIN_USER="${INFLUX_ADMIN_USER:-solar}"
INFLUX_ADMIN_PASSWORD="${INFLUX_ADMIN_PASSWORD:-$(head -c 16 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 24)}"
RETENTION_SECONDS="${INFLUX_RETENTION_SECONDS:-157680000}"
TOKEN_FILE="/data/influxdb/.admin_token"
ENV_FILE="/data/influxdb/influx.env"
DATASOURCE_FILE="/etc/grafana/provisioning/datasources/influxdb.yaml"
BUCKETS=("solar_forecast" "system_state" "eva_nodes" "eva_patterns")

# Wait for InfluxDB to become healthy
for i in $(seq 1 150); do
    if curl -sf "$INFLUX_URL/health" > /dev/null 2>&1; then
        break
    fi
    if [ "$i" -eq 150 ]; then
        echo "InfluxDB did not become healthy. Aborting bootstrap."
        exit 1
    fi
    sleep 2
done

# One-time onboarding
if [ ! -f "$TOKEN_FILE" ]; then
    allowed=$(curl -s "$INFLUX_URL/api/v2/setup" | jq -r '.allowed // "false"')
    if [ "$allowed" = "true" ]; then
        echo "Onboarding InfluxDB (org=$INFLUX_ORG)..."
        resp=$(curl -s -X POST "$INFLUX_URL/api/v2/setup" \
            -H "Content-Type: application/json" \
            -d "{\"username\":\"$INFLUX_ADMIN_USER\",\"password\":\"$INFLUX_ADMIN_PASSWORD\",\"org\":\"$INFLUX_ORG\",\"bucket\":\"solar_forecast\"}")
        token=$(echo "$resp" | jq -r '.auth.token // empty')
        if [ -z "$token" ]; then
            echo "InfluxDB onboarding failed: $resp"
            exit 1
        fi
        echo "$token" > "$TOKEN_FILE"
        chmod 600 "$TOKEN_FILE"
        echo "InfluxDB onboarded."
    else
        echo "InfluxDB already onboarded but no token file found. Restore /data/influxdb/.admin_token or set INFLUXDB_TOKEN."
        exit 1
    fi
fi

TOKEN=$(cat "$TOKEN_FILE")

# Resolve org ID
orgs_json=$(curl -s -H "Authorization: Token $TOKEN" "$INFLUX_URL/api/v2/orgs")
org_id=$(echo "$orgs_json" | jq -r --arg org "$INFLUX_ORG" '.orgs[] | select(.name==$org) | .id')
if [ -z "$org_id" ]; then
    echo "Organization '$INFLUX_ORG' not found in InfluxDB."
    exit 1
fi

# Create buckets idempotently
buckets_json=$(curl -s -H "Authorization: Token $TOKEN" "$INFLUX_URL/api/v2/buckets?orgID=$org_id")
for bucket in "${BUCKETS[@]}"; do
    exists=$(echo "$buckets_json" | jq --arg b "$bucket" '[.buckets[] | select(.name==$b)] | length')
    if [ "$exists" = "0" ]; then
        curl -s -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
            -d "{\"name\":\"$bucket\",\"orgID\":\"$org_id\",\"retentionRules\":[{\"type\":\"expire\",\"everySeconds\":$RETENTION_SECONDS}]}" \
            "$INFLUX_URL/api/v2/buckets" > /dev/null
        echo "Created bucket $bucket (retention ${RETENTION_SECONDS}s)"
    fi
done

# Shared env file for guard/hermes/HA
cat > "$ENV_FILE" <<EOF
INFLUXDB_TOKEN=$TOKEN
INFLUXDB_ORG=$INFLUX_ORG
INFLUXDB_URL=$INFLUX_URL
INFLUXDB_ADMIN_USER=$INFLUX_ADMIN_USER
INFLUXDB_ADMIN_PASSWORD=$INFLUX_ADMIN_PASSWORD
EOF
chmod 600 "$ENV_FILE"
chown solar:solar "$TOKEN_FILE" "$ENV_FILE"

# Render Grafana datasource provisioning with the real token
cat > "$DATASOURCE_FILE" <<EOF
apiVersion: 1
datasources:
  - name: InfluxDB_Solar
    type: influxdb
    access: proxy
    url: $INFLUX_URL
    isDefault: true
    editable: true
    jsonData:
      version: Flux
      organization: $INFLUX_ORG
      defaultBucket: solar_forecast
      tlsSkipVerify: false
    secureJsonData:
      token: $TOKEN
  - name: InfluxDB_State
    type: influxdb
    access: proxy
    url: $INFLUX_URL
    isDefault: false
    editable: true
    jsonData:
      version: Flux
      organization: $INFLUX_ORG
      defaultBucket: system_state
      tlsSkipVerify: false
    secureJsonData:
      token: $TOKEN
EOF

echo "InfluxDB bootstrap complete."
