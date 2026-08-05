# Solar-Sentinel-AIO v3 - Phase 5
# Gemini API & EVA Energy Map Architecture

# Stage 1: Downloader
FROM alpine:3.19 AS downloader
RUN apk add --no-cache curl

WORKDIR /downloads
COPY scripts/validate-sha256.sh /usr/local/bin/validate-sha256.sh
RUN chmod +x /usr/local/bin/validate-sha256.sh

# Grafana 10.2.3
ARG GRAFANA_VERSION=10.2.3
ARG GRAFANA_SHA256=c686606a6975481f4f108de44c4df3465251e4ee2da20e7c6ee6b66e5bdcf2da
RUN curl -fsSL https://dl.grafana.com/oss/release/grafana-${GRAFANA_VERSION}.linux-amd64.tar.gz -o grafana.tar.gz \
    && /usr/local/bin/validate-sha256.sh grafana.tar.gz ${GRAFANA_SHA256}

# InfluxDB 2.7.4
ARG INFLUXDB_VERSION=2.7.4
ARG INFLUXDB_SHA256=2a2ccca52e42fb483c93ac3672cb264ffb804fc0be33209e5d954dd755146288
RUN curl -fsSL https://dl.influxdata.com/influxdb/releases/influxdb2-${INFLUXDB_VERSION}_linux_amd64.tar.gz -o influxdb.tar.gz \
    && /usr/local/bin/validate-sha256.sh influxdb.tar.gz ${INFLUXDB_SHA256}

# Stage 2: Energy Guard (Custom Service)
FROM alpine:3.19 AS energy-guard-builder
WORKDIR /app
COPY config/energy-guard/guard.py energy-guard
RUN chmod +x energy-guard

# Stage 4: Final Image
FROM alpine:3.19

# Install runtime dependencies
RUN apk add --no-cache \
    python3=3.11.14-r0 \
    py3-pip=23.3.1-r0 \
    nodejs=20.15.1-r0 \
    npm=10.2.5-r0 \
    mosquitto=2.0.18-r0 \
    mosquitto-clients=2.0.18-r0 \
    supervisor=4.2.5-r4 \
    bash=5.2.21-r0 \
    curl=8.14.1-r2 \
    libc6-compat=1.1.0-r4 \
    ca-certificates=20250911-r0 \
    util-linux=2.39.3-r0 \
    smartmontools=7.4-r1 \
    git=2.43.7-r0 \
    netcat-openbsd=1.226-r0 \
    jq=1.7.1-r0 \
    # Build toolchain so pip/npm can compile wheels missing musl builds
    # (e.g. cryptography, h5py, lxml, better-sqlite3)
    build-base \
    musl-dev \
    python3-dev \
    linux-headers \
    openssl-dev \
    libffi-dev \
    rust \
    cargo \
    hdf5-dev \
    libxml2-dev \
    zlib-dev

# Create non-root user
RUN addgroup -S solar && adduser -S solar -G solar

# Create necessary directories
RUN mkdir -p /var/log/supervisor /etc/supervisor/conf.d /data /config /var/lib/influxdb2 /var/lib/grafana /mosquitto/data /mosquitto/log

# Copy requirements.txt and install Python dependencies
COPY requirements.txt /tmp/requirements.txt
# Pin homeassistant to specific version for stability
ARG HOMEASSISTANT_VERSION=2024.2.1
# Install project deps (with their transitive deps) first, then Home Assistant
# so HA's own pins win on any conflicts.
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages homeassistant==${HOMEASSISTANT_VERSION}

# Copy supervisord config
COPY supervisord.conf /etc/supervisor/supervisord.conf

# Install Node-RED + dashboard UI nodes
ARG NODERED_VERSION=3.1.3
ARG NODERED_DASHBOARD_VERSION=3.6.5
WORKDIR /usr/share/node-red
RUN echo '{"dependencies": {"node-red": "'${NODERED_VERSION}'", "node-red-dashboard": "'${NODERED_DASHBOARD_VERSION}'"}}' > package.json \
    && npm install \
    && npm ci --production \
    && ln -s /usr/share/node-red/node_modules/.bin/node-red /usr/local/bin/node-red

# Install Uptime Kuma
ARG UPTIME_KUMA_VERSION=1.23.11
WORKDIR /usr/share/uptime-kuma
RUN curl -fsSL https://github.com/louislam/uptime-kuma/archive/refs/tags/${UPTIME_KUMA_VERSION}.tar.gz -o uptime-kuma.tar.gz \
    && tar -xzf uptime-kuma.tar.gz --strip-components=1 \
    && rm uptime-kuma.tar.gz \
    && npm ci --production

# Copy InfluxDB
COPY --from=downloader /downloads/influxdb.tar.gz /tmp/influxdb.tar.gz
RUN tar -xzf /tmp/influxdb.tar.gz -C /usr/local/bin --strip-components=1 \
    && rm /tmp/influxdb.tar.gz

# Install Grafana
COPY --from=downloader /downloads/grafana.tar.gz /tmp/grafana.tar.gz
RUN mkdir -p /usr/share/grafana \
    && tar -xzf /tmp/grafana.tar.gz -C /usr/share/grafana --strip-components=1 \
    && rm /tmp/grafana.tar.gz \
    && ln -s /usr/share/grafana/bin/grafana-server /usr/local/bin/grafana-server

# Copy Open-Meteo is not built here - run it as an optional separate compose
# service using upstream's own image (see docker-compose.yml). The Energy Guard
# falls back to the public Open-Meteo API when it is not reachable.

# Copy Energy Guard
COPY --from=energy-guard-builder /app/energy-guard /usr/local/bin/energy-guard

# Copy configurations and scripts
COPY config/ /etc/
COPY setup.sh /usr/local/bin/setup.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY scripts/ /usr/local/bin/
COPY data/ /usr/share/solar-sentinel/data/

RUN chmod +x /usr/local/bin/setup.sh /usr/local/bin/entrypoint.sh /usr/local/bin/*.sh

# Symlink for Home Assistant
RUN ln -s /etc/homeassistant /config/homeassistant

# Set permissions
RUN chown -R solar:solar /data /config /var/lib/influxdb2 /var/lib/grafana /mosquitto /var/log/supervisor

# Expose ports
EXPOSE 8123 3000 1883 1880 3001

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD /usr/local/bin/healthcheck.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
