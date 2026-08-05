#!/bin/bash
# /data/scripts/usb_backup.sh
# Solar-Sentinel USB Backup Script

LOG_FILE="/data/logs/backup.log"
mkdir -p /data/logs

MQTT_ARGS=()
[ -n "$MQTT_USER" ] && MQTT_ARGS=( -u "$MQTT_USER" -P "$MQTT_PASS" )
NTFY_URL="${NTFY_URL:-https://ntfy.sh/solar_sentinel_alerts}"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

log "Starting USB backup process..."

# Auto-detect USB drive via lsblk (override with USB_MOUNT when running
# inside the container with a host USB mounted at a known path)
if [ -z "$USB_MOUNT" ]; then
    USB_MOUNT=$(lsblk -nr -o MOUNTPOINT,TRAN | grep "usb" | awk '{print $1}' | head -n 1)
fi

if [ -z "$USB_MOUNT" ]; then
    # Fallback search in /media or /mnt
    USB_MOUNT=$(df -h | grep -E '/media/|/mnt/' | awk '{print $6}' | head -n 1)
fi

if [ -z "$USB_MOUNT" ] || [ "$USB_MOUNT" == "/" ]; then
    log "ERROR: USB drive not detected or not mounted."
    mosquitto_pub -h localhost "${MQTT_ARGS[@]}" -t solar/system/backup_status -m "FAILED: USB not detected" 2>/dev/null || true
    curl -s -d "USB Backup Failed: Drive not detected" "$NTFY_URL" > /dev/null 2>&1 || true
    exit 1
fi

log "USB detected at $USB_MOUNT"

BACKUP_DIR="$USB_MOUNT/solar_sentinel_backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="backup_$TIMESTAMP.tar.gz"

log "Creating compressed tar.gz of /data..."

# Create compressed tar.gz excluding caches and logs
# We use -C / to make the paths in tar relative to root or we can just backup /data
tar -czf "$BACKUP_DIR/$BACKUP_FILE" \
    --exclude="*/cache/*" \
    --exclude="*/logs/*.log" \
    --exclude="*/log/*.log" \
    --exclude="*/tmp/*" \
    /data

if [ $? -eq 0 ]; then
    log "Backup successful: $BACKUP_FILE"
    
    # Rotate to keep last 8 backups
    ls -t "$BACKUP_DIR"/backup_*.tar.gz | tail -n +9 | xargs rm -f 2>/dev/null
    
    # Write BACKUP_MANIFEST.txt
    {
        echo "Solar-Sentinel-AIO Backup Manifest"
        echo "==============================="
        echo "Timestamp: $(date)"
        echo "File: $BACKUP_FILE"
        echo "Host: $(hostname)"
        echo "Backups on drive:"
        ls -1 "$BACKUP_DIR"/backup_*.tar.gz
    } > "$USB_MOUNT/BACKUP_MANIFEST.txt"
    
    # Send ntfy notification
    curl -s -d "USB Backup Success: $BACKUP_FILE" "$NTFY_URL" > /dev/null 2>&1 || true
    
    # Publish MQTT status
    mosquitto_pub -h localhost "${MQTT_ARGS[@]}" -t solar/system/backup_status -m "SUCCESS: $BACKUP_FILE" 2>/dev/null || true
    
    log "Backup rotation completed. Kept last 8."
else
    log "ERROR: Backup failed during compression."
    mosquitto_pub -h localhost "${MQTT_ARGS[@]}" -t solar/system/backup_status -m "FAILED: Compression error" 2>/dev/null || true
    curl -s -d "USB Backup Failed: Compression error" "$NTFY_URL" > /dev/null 2>&1 || true
    exit 1
fi
