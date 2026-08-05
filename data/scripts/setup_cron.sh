#!/bin/bash
# /data/scripts/setup_cron.sh
# Solar-Sentinel Cron Installer

# Clean old solar_sentinel entries idempotently
# Using a temp file for safety
crontab -l 2>/dev/null | grep -v "# solar_sentinel" > /tmp/current_cron || true

# Add the 6 cron jobs
cat <<EOF >> /tmp/current_cron
0 2 * * 0 /data/scripts/usb_backup.sh # solar_sentinel
0 3 * * * /data/scripts/influx_backup.sh # solar_sentinel
30 3 * * * find /data/backups -type d -name "influx_*" -mtime +30 -exec rm -rf {} + # solar_sentinel
0 1 * * 0 supervisorctl start healthcheck # solar_sentinel
0 4 1 * * find /data/logs -name "*.log" -exec truncate -s 0 {} + # solar_sentinel
0 9 * * 1 /data/scripts/usb_reminder.sh # solar_sentinel
EOF

# Install the new crontab
crontab /tmp/current_cron
rm /tmp/current_cron

echo "Cron jobs installed successfully."
