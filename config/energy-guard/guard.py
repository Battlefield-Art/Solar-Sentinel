#!/usr/bin/env python3
"""
Solar-Sentinel-AIO v3 Phase 5
Energy Guard - EVA (Energy Value Architecture) Implementation

Sections:
A. Environment Configuration
B. Logging Setup
C. State Persistence
D. MQTT Setup with solar/ namespace
E. InfluxDB Connection
F. Forecast Engine
G. Decision Engine
H. EVA System (Registry, Phantom Cut, Optimal Window, Smart Lockout, Pattern Learning, Map Snapshot)
"""

import os
import sys
import time
import json
import logging
import signal
import threading
from datetime import datetime, timedelta
from pathlib import Path
import math

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
import requests
import schedule
import pytz
import pandas as pd
import numpy as np
from pvlib.location import Location
from logging.handlers import RotatingFileHandler

# ============================================================================
# SECTION A: ENVIRONMENT CONFIGURATION
# ============================================================================

MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", None)
MQTT_PASS = os.getenv("MQTT_PASS", None)

INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "my-org")

def _influx_token():
    token = os.getenv("INFLUXDB_TOKEN", "")
    if not token or token == "my-token":
        env_file = "/data/influxdb/influx.env"
        if os.path.exists(env_file):
            try:
                with open(env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("INFLUXDB_TOKEN="):
                            return line.split("=", 1)[1]
            except Exception:
                pass
    return token or "my-token"

INFLUXDB_TOKEN = _influx_token()
INFLUXDB_BUCKET_FORECAST = os.getenv("INFLUXDB_BUCKET_FORECAST", "solar_forecast")
INFLUXDB_BUCKET_STATE = os.getenv("INFLUXDB_BUCKET_STATE", "system_state")
INFLUXDB_BUCKET_EVA_NODES = os.getenv("INFLUXDB_BUCKET_EVA_NODES", "eva_nodes")
INFLUXDB_BUCKET_EVA_PATTERNS = os.getenv("INFLUXDB_BUCKET_EVA_PATTERNS", "eva_patterns")

LATITUDE = float(os.getenv("LATITUDE", 25.2048))
LONGITUDE = float(os.getenv("LONGITUDE", 55.2708))
TIMEZONE = os.getenv("TIMEZONE", "Asia/Dubai")

PANEL_EFFICIENCY = float(os.getenv("PANEL_EFFICIENCY", 0.215))
PANEL_TEMP_COEFF = float(os.getenv("PANEL_TEMP_COEFF", -0.0026))
PANEL_AREA = float(os.getenv("PANEL_AREA", 20.0))

NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh/solar_sentinel_guard_alerts")
OPEN_METEO_URL = os.getenv("OPEN_METEO_URL", "http://localhost:8080")
ELECTRICITY_RATE_USD_PER_KWH = float(os.getenv("ELECTRICITY_RATE_USD_PER_KWH", 0.15))

# SOC Thresholds
SOC_LOCKOUT = float(os.getenv("SOC_LOCKOUT", 20.0))
SOC_WARNING = float(os.getenv("SOC_WARNING", 40.0))
SOC_ADVISORY = float(os.getenv("SOC_ADVISORY", 60.0))
SOC_ABUNDANCE = float(os.getenv("SOC_ABUNDANCE", 90.0))

# EVA Configuration
EVA_PHANTOM_THRESHOLD_W = float(os.getenv("EVA_PHANTOM_THRESHOLD_W", 5.0))
EVA_OPTIMAL_WINDOW_HOURS = int(os.getenv("EVA_OPTIMAL_WINDOW_HOURS", 4))
EVA_LOCKOUT_HYSTERESIS_MIN = int(os.getenv("EVA_LOCKOUT_HYSTERESIS_MIN", 2))

SOC_HYSTERESIS = 2.0
POWER_HYSTERESIS = 100

TIER_ORDER = ["LOCKOUT", "WARNING", "ADVISORY", "NOMINAL", "ABUNDANCE"]

def tier_index(tier):
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return TIER_ORDER.index("NOMINAL")

# ============================================================================
# SECTION B: LOGGING SETUP
# ============================================================================

LOG_FILE = "/data/logs/guard.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logger = logging.getLogger("EnergyGuard")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=5)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(formatter)
logger.addHandler(stdout_handler)

# ============================================================================
# SECTION C: STATE PERSISTENCE
# ============================================================================

STATE_FILE = "/data/guard/guard_state.json"
REGISTRY_FILE = "/data/guard/eva_registry.json"

state = {
    "current_soc": 50.0,
    "current_watts": 0.0,
    "current_tier": "NOMINAL",
    "last_alert_tier": None,
    "previous_tier": None,
    "forecast_3h_avg": 0.0,
    "forecast_daily_kwh": {},
    "timestamp": datetime.now().isoformat(),
    "config": {
        "soc_lockout": SOC_LOCKOUT,
        "soc_warning": SOC_WARNING,
        "soc_advisory": SOC_ADVISORY,
        "soc_abundance": SOC_ABUNDANCE
    },
    "eva": {
        "nodes": {},
        "patterns": {},
        "recommendations": [],
        "last_optimal_window": None,
        "scheduled_devices": {},
        "last_window_started_notified": None,
        "phantom_cuts_performed": 0
    }
}

state_lock = threading.RLock()

def load_state():
    global state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                loaded = json.load(f)
                if "config" in loaded:
                    state["config"].update(loaded["config"])
                for k, v in loaded.items():
                    if k != "config":
                        state[k] = v
        except Exception as e:
            logger.error(f"Error loading state: {e}")

def save_state():
    state["timestamp"] = datetime.now().isoformat()
    try:
        with state_lock:
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving state: {e}")

# ============================================================================
# SECTION D: MQTT SETUP (solar/ namespace)
# ============================================================================

MQTT_TOPICS = {
    # Battery and Solar
    "battery_soc": "solar/battery/soc",
    "pv_power": "solar/pv/power",
    
    # Guard
    "guard_status": "solar/guard/status",
    "guard_config": "solar/guard/config/",
    "guard_command": "solar/guard/command",
    
    # Forecast
    "forecast_7day": "solar/forecast/7day",
    
    # Alerts
    "alerts_guard": "solar/alerts/guard",
    
    # Hermes
    "hermes_inbox": "solar/hermes/inbox",
    "hermes_outbox": "solar/hermes/outbox",
    
    # EVA
    "eva_map": "solar/eva/map",
    "eva_nodes": "solar/eva/node/#",
    "eva_command": "solar/eva/command",
    "eva_optimal_window": "solar/eva/optimal_window",
    "eva_device_schedule": "solar/eva/device/#",
    "eva_waste_analysis": "solar/eva/waste_analysis",
}

mqtt_client = mqtt.Client(client_id="solar_guard")
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Connected to MQTT Broker")
        client.subscribe(MQTT_TOPICS["battery_soc"])
        client.subscribe(MQTT_TOPICS["pv_power"])
        client.subscribe(MQTT_TOPICS["guard_config"] + "#")
        client.subscribe(MQTT_TOPICS["guard_command"])
        client.subscribe(MQTT_TOPICS["eva_nodes"])
        client.subscribe(MQTT_TOPICS["eva_command"])
        client.subscribe(MQTT_TOPICS["eva_device_schedule"])
        client.publish(MQTT_TOPICS["guard_status"], "ONLINE", retain=True)
    else:
        logger.error(f"Failed to connect to MQTT, rc={rc}")

def on_message(client, userdata, msg):
    global state
    try:
        payload = msg.payload.decode()
        with state_lock:
            if msg.topic == MQTT_TOPICS["battery_soc"]:
                state["current_soc"] = float(payload)
            elif msg.topic == MQTT_TOPICS["pv_power"]:
                state["current_watts"] = float(payload)
            elif msg.topic.startswith(MQTT_TOPICS["guard_config"]):
                key = msg.topic.split("/")[-1]
                state["config"][key] = float(payload)
                save_state()
            elif msg.topic.startswith(MQTT_TOPICS["eva_device_schedule"]):
                handle_eva_device_schedule(msg.topic, payload)
            elif msg.topic.startswith("solar/eva/node/"):
                parts = msg.topic.split("/")
                if len(parts) >= 5:
                    handle_eva_node_update(parts[3], parts[4], payload)
        if msg.topic == MQTT_TOPICS["guard_command"]:
            if payload == "FORCE_FORECAST":
                threading.Thread(target=update_forecast, daemon=True).start()
            elif payload == "FORCE_DECISION":
                threading.Thread(target=run_decision_engine, daemon=True).start()
        elif msg.topic == MQTT_TOPICS["eva_command"]:
            threading.Thread(target=handle_eva_command, args=(payload,), daemon=True).start()
    except Exception as e:
        logger.error(f"MQTT msg error: {e}")

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.will_set(MQTT_TOPICS["guard_status"], "OFFLINE", retain=True)

def mqtt_thread_func():
    while True:
        try:
            mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
            mqtt_client.loop_forever()
        except Exception as e:
            logger.error(f"MQTT Error: {e}. Retrying...")
            time.sleep(5)

# ============================================================================
# SECTION E: INFLUXDB CONNECTION
# ============================================================================

influx_client = None
write_api = None

def init_influx():
    global influx_client, write_api
    while influx_client is None:
        try:
            health = requests.get(f"{INFLUXDB_URL}/health", timeout=5)
            if health.status_code != 200:
                raise Exception(f"InfluxDB health check returned {health.status_code}")
            influx_client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
            write_api = influx_client.write_api(write_options=SYNCHRONOUS)
            logger.info("Connected to InfluxDB")
        except Exception as e:
            logger.error(f"InfluxDB init failed: {e}. Retrying...")
            time.sleep(10)

def safe_write(bucket, records):
    """Best-effort InfluxDB write that never raises (InfluxDB may be briefly down at boot)."""
    if not write_api:
        return
    try:
        write_api.write(bucket=bucket, record=records)
    except Exception as e:
        logger.warning(f"InfluxDB write failed ({bucket}): {e}")

# ============================================================================
# SECTION F: FORECAST ENGINE
# ============================================================================

def get_weather_forecast():
    urls = [
        f"{OPEN_METEO_URL}/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}&hourly=cloudcover,temperature_2m&timezone={TIMEZONE}",
        f"https://api.open-meteo.com/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}&hourly=cloudcover,temperature_2m&timezone={TIMEZONE}",
    ]
    for url in urls:
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200: return res.json()
        except Exception:
            continue
    return None

def update_forecast():
    logger.info("Updating Solar Forecast...")
    weather = get_weather_forecast()
    if not weather: return

    try:
        times = pd.to_datetime(weather['hourly']['time'])
        cloudcover = weather['hourly']['cloudcover']
        loc = Location(LATITUDE, LONGITUDE, tz=TIMEZONE)
        clearsky = loc.get_clearsky(times)
        
        forecast_data = []
        daily_yields = {}
        temps = weather['hourly'].get('temperature_2m')
        for i in range(len(times)):
            power = PANEL_AREA * clearsky['ghi'].iloc[i] * PANEL_EFFICIENCY
            if temps is not None:
                power *= (1.0 + PANEL_TEMP_COEFF * (temps[i] - 25.0))
            power *= (1 - 0.75 * (cloudcover[i] / 100.0) ** 3)
            power = max(0, power)
            day_str = times[i].strftime("%Y-%m-%d")
            daily_yields[day_str] = daily_yields.get(day_str, 0) + (power / 1000.0)
            forecast_data.append(Point("solar_forecast").time(times[i], WritePrecision.NS).field("power_w", float(power)))
            
        if write_api:
            safe_write(INFLUXDB_BUCKET_FORECAST, forecast_data)
        state["forecast_daily_kwh"] = daily_yields
        mqtt_client.publish(MQTT_TOPICS["forecast_7day"], round(sum(daily_yields.values()), 2))
        logger.info(f"Forecast updated. Next 3 days: {[daily_yields.get((datetime.now(pytz.timezone(TIMEZONE)) + timedelta(days=i)).strftime('%Y-%m-%d'), 0.0) for i in range(3)]}")
    except Exception as e:
        logger.error(f"Forecast engine error: {e}")

# ============================================================================
# SECTION G: DECISION ENGINE
# ============================================================================

def run_decision_engine():
    global state
    with state_lock:
        soc = state["current_soc"]
        watts = state["current_watts"]
        cfg = state["config"]
        current_tier = state["current_tier"]

        # Calculate worst_3day
        now_tz = datetime.now(pytz.timezone(TIMEZONE))
        forecast_daily = state.get("forecast_daily_kwh", {})
        next_3_days = [forecast_daily.get((now_tz + timedelta(days=i)).strftime("%Y-%m-%d"), 0.0) for i in range(3)]
        worst_3day = min(next_3_days) if next_3_days else 0.0

        def soc_tier(offset):
            if soc < cfg["soc_lockout"] + offset: return "LOCKOUT"
            if soc < cfg["soc_warning"] + offset: return "WARNING"
            if soc < cfg["soc_advisory"] + offset: return "ADVISORY"
            if soc > cfg["soc_abundance"] + offset and watts > 2000 + POWER_HYSTERESIS: return "ABUNDANCE"
            return "NOMINAL"

        def forecast_floor():
            if worst_3day < 0.8: return "LOCKOUT"
            if worst_3day < 2.5: return "WARNING"
            return "NOMINAL"

        floor = forecast_floor()
        entry_tier = soc_tier(0.0)
        candidate = entry_tier if tier_index(entry_tier) < tier_index(floor) else floor

        if tier_index(candidate) < tier_index(current_tier):
            # Becoming more restrictive: use raw entry thresholds
            new_tier = candidate
        else:
            # Holding or recovering: require hysteresis margin before relaxing.
            # The forecast floor only ever forces restriction, so ABUNDANCE is
            # only suppressed when the forecast itself is restrictive.
            recovery_tier = soc_tier(SOC_HYSTERESIS)
            if floor in ("LOCKOUT", "WARNING"):
                new_tier = recovery_tier if tier_index(recovery_tier) < tier_index(floor) else floor
            else:
                new_tier = recovery_tier

        if new_tier != current_tier:
            state["previous_tier"] = current_tier
            state["current_tier"] = new_tier
            logger.info(f"Tier transition: {current_tier} -> {new_tier}")

            # Handle EVA-aware lockout logic
            if new_tier == "LOCKOUT":
                eva_smart_lockout()
            elif current_tier in ["LOCKOUT", "WARNING"] and new_tier == "NOMINAL":
                unlock_all_appliances()

            publish_alert(f"Tier changed to {new_tier}. SOC: {soc}%, Forecast Worst: {worst_3day:.2f}kWh",
                          old_tier=current_tier, new_tier=new_tier)
        save_state()

# ============================================================================
# SECTION H: EVA SYSTEM
# ============================================================================

# H-1: Registry Management
def load_eva_registry():
    if os.path.exists(REGISTRY_FILE):
        try:
            with open(REGISTRY_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading EVA registry: {e}")
    return {}

def save_eva_registry(registry):
    try:
        with open(REGISTRY_FILE, 'w') as f:
            json.dump(registry, f, indent=4)
        logger.info("EVA registry saved.")
    except Exception as e:
        logger.error(f"Error saving EVA registry: {e}")

# H-2: Phantom Load Detection & Cutting
def eva_phantom_cut():
    """Detect and cut phantom loads - devices consuming power when reported OFF"""
    registry = load_eva_registry()
    nodes = state["eva"]["nodes"]
    cuts = 0
    
    for device_id, info in registry.items():
        if info.get("priority") == "PHANTOM":
            node = nodes.get(device_id, {})
            # If consuming power but HA says it's OFF
            if node.get("last_power", 0) > EVA_PHANTOM_THRESHOLD_W and node.get("last_state") == "OFF":
                mqtt_client.publish(f"solar/eva/node/{device_id}/cut", "CUT", retain=True)
                cuts += 1
                logger.info(f"EVA: Phantom cut performed on {device_id}")
    
    if cuts > 0:
        state["eva"]["phantom_cuts_performed"] += cuts
        publish_alert(f"EVA: Cut {cuts} phantom loads.", priority="default", tags="zap")
    
    return cuts

# H-3: Optimal Window Finder
def eva_optimal_window_finder():
    """Find the best 4-hour window for running heavy loads based on solar forecast"""
    logger.info("EVA: Finding optimal window...")
    if not influx_client: return
    
    query_api = influx_client.query_api()
    query = f'''
    from(bucket: "{INFLUXDB_BUCKET_FORECAST}")
      |> range(start: now(), stop: 48h)
      |> filter(fn: (r) => r["_measurement"] == "solar_forecast")
      |> filter(fn: (r) => r["_field"] == "power_w")
      |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
    '''
    try:
        tables = query_api.query(query)
        forecast_data = []
        for table in tables:
            for record in table.records:
                forecast_data.append({"time": record.get_time(), "power": record.get_value()})
        
        if len(forecast_data) < EVA_OPTIMAL_WINDOW_HOURS:
            logger.warning("Not enough forecast data for optimal window finder.")
            return

        max_yield = 0
        best_start_time = None
        
        for i in range(len(forecast_data) - EVA_OPTIMAL_WINDOW_HOURS + 1):
            window_power = sum(forecast_data[j]["power"] for j in range(i, i + EVA_OPTIMAL_WINDOW_HOURS))
            if window_power > max_yield:
                max_yield = window_power
                best_start_time = forecast_data[i]["time"]
        
        if best_start_time:
            window = {
                "start_time": best_start_time.isoformat(),
                "end_time": (best_start_time + timedelta(hours=EVA_OPTIMAL_WINDOW_HOURS)).isoformat(),
                "predicted_yield_wh": round(max_yield, 2)
            }
            state["eva"]["last_optimal_window"] = window
            mqtt_client.publish(MQTT_TOPICS["eva_optimal_window"], json.dumps(window), retain=True)
            logger.info(f"EVA: Optimal window found: {best_start_time}")
            save_state()
            
            # Notify Hermes
            mqtt_client.publish(MQTT_TOPICS["hermes_outbox"], json.dumps({
                "type": "optimal_window",
                "window": window,
                "timestamp": datetime.now().isoformat()
            }))
    except Exception as e:
        logger.error(f"Error in optimal window finder: {e}")

# H-4: Smart Lockout (EVA-Aware)
def eva_smart_lockout():
    """
    Smart lockout logic:
    - CRITICAL: Never lock
    - SHIFTABLE: Schedule for optimal window instead of immediate lock
    - LUXURY: Lock immediately
    - PHANTOM: Lock immediately (already handled by phantom cut)
    """
    registry = load_eva_registry()
    tier = state["current_tier"]
    optimal_window = state["eva"].get("last_optimal_window")
    
    scheduled = []
    locked = []
    
    for device_id, info in registry.items():
        priority = info.get("priority")
        should_lock = False
        
        if tier == "LOCKOUT":
            if priority in ["PHANTOM", "LUXURY"]:
                should_lock = True
            elif priority == "SHIFTABLE":
                # Schedule instead of lock
                if optimal_window:
                    schedule_msg = json.dumps({
                        "device_id": device_id,
                        "scheduled_time": optimal_window["start_time"],
                        "reason": "low_battery_shift"
                    })
                    mqtt_client.publish(f"solar/eva/device/{device_id}/schedule", schedule_msg, retain=True)
                    state["eva"].setdefault("scheduled_devices", {})[device_id] = optimal_window["start_time"]
                    scheduled.append(device_id)
                else:
                    # No optimal window, fall back to lock
                    should_lock = True
        elif tier == "WARNING":
            if priority in ["PHANTOM", "LUXURY"]:
                should_lock = True
        elif tier == "ADVISORY":
            if priority == "PHANTOM":
                should_lock = True
        
        if should_lock:
            mqtt_client.publish(f"solar/appliance/{device_id}/lock", "LOCK", retain=True)
            locked.append(f"{device_id}({priority})")
            logger.info(f"EVA Lock: {device_id} (Priority: {priority})")
    
    if scheduled:
        publish_alert(f"EVA: Scheduled {len(scheduled)} shiftable devices for optimal window", priority="low", tags="clock")
    if locked:
        logger.info(f"EVA Lock: {locked}")

def lock_appliances():
    """Legacy wrapper for compatibility"""
    eva_smart_lockout()

def unlock_all_appliances():
    """Unlock all appliances in the EVA registry"""
    registry = load_eva_registry()
    for device_id in registry:
        mqtt_client.publish(f"solar/appliance/{device_id}/lock", "UNLOCK", retain=True)
    logger.info("EVA Unlock: All appliances")

def handle_eva_device_schedule(topic, payload):
    """Record a device scheduled for the optimal window from MQTT"""
    parts = topic.split("/")
    if len(parts) < 4:
        return
    device_id = parts[3]
    try:
        data = json.loads(payload)
        scheduled_time = data.get("scheduled_time") if isinstance(data, dict) else None
        if not scheduled_time:
            scheduled_time = payload
        state["eva"].setdefault("scheduled_devices", {})[device_id] = scheduled_time
        logger.info(f"EVA: {device_id} scheduled for {scheduled_time}")
        save_state()
    except Exception as e:
        logger.error(f"EVA: bad schedule payload: {e}")

def eva_release_due_devices():
    """Unlock scheduled devices whose scheduled start time has been reached"""
    scheduled = state["eva"].get("scheduled_devices", {})
    if not scheduled:
        return
    now = datetime.now(pytz.timezone(TIMEZONE))
    due = []
    for device_id, sched_time in list(scheduled.items()):
        try:
            sched_dt = datetime.fromisoformat(str(sched_time))
            if sched_dt.tzinfo is None:
                sched_dt = sched_dt.replace(tzinfo=now.tzinfo)
            if sched_dt <= now:
                due.append(device_id)
        except Exception:
            continue
    for device_id in due:
        mqtt_client.publish(f"solar/appliance/{device_id}/lock", "UNLOCK", retain=True)
        del scheduled[device_id]
        logger.info(f"EVA: Released scheduled device {device_id}")
        publish_alert(f"EVA: Released scheduled device {device_id}", priority="low")
    if due:
        save_state()

def eva_release_scheduled_devices_now():
    """Manual release of all scheduled devices (RELEASE_SCHEDULED_DEVICES command)"""
    scheduled = state["eva"].get("scheduled_devices", {})
    for device_id in list(scheduled.keys()):
        mqtt_client.publish(f"solar/appliance/{device_id}/lock", "UNLOCK", retain=True)
        del scheduled[device_id]
        logger.info(f"EVA: Released scheduled device {device_id} (manual release)")
    save_state()

def eva_check_window_start():
    """Publish window_started event once the optimal window start time is reached"""
    window = state["eva"].get("last_optimal_window")
    if not window or not window.get("start_time"):
        return
    start = window["start_time"]
    if state["eva"].get("last_window_started_notified") == start:
        return
    try:
        start_dt = datetime.fromisoformat(start)
        now = datetime.now(pytz.timezone(TIMEZONE))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=now.tzinfo)
        if now >= start_dt:
            mqtt_client.publish("solar/eva/window_started", json.dumps(window), retain=True)
            state["eva"]["last_window_started_notified"] = start
            save_state()
            logger.info("EVA: Optimal window has started")
    except Exception as e:
        logger.error(f"EVA: window start check error: {e}")

# H-5: Pattern Learning from InfluxDB
def eva_pattern_learning():
    """Learn energy patterns from historical device usage"""
    logger.info("EVA: Learning energy patterns...")
    if not influx_client: return
    
    query_api = influx_client.query_api()
    
    # Learn average power consumption per device
    query = f'''
    from(bucket: "{INFLUXDB_BUCKET_EVA_NODES}")
      |> range(start: -7d)
      |> filter(fn: (r) => r["_measurement"] == "eva_nodes")
      |> filter(fn: (r) => r["_field"] == "power_w")
      |> group(columns: ["node_id"])
      |> mean()
    '''
    try:
        tables = query_api.query(query)
        patterns = {}
        for table in tables:
            for record in table.records:
                node_id = record.values.get("node_id")
                mean_power = record.get_value()
                patterns[node_id] = {
                    "avg_w": round(mean_power, 2),
                    "confidence": 0.85,
                    "last_learned": datetime.now().isoformat()
                }
                
                # Store pattern in InfluxDB
                if write_api:
                    point = Point("eva_patterns").tag("node_id", node_id).field("avg_w", float(mean_power))
                    write_api.write(bucket=INFLUXDB_BUCKET_EVA_PATTERNS, record=point)
        
        state["eva"]["patterns"] = patterns
        logger.info(f"EVA: Learned {len(patterns)} patterns")
        save_state()
        
        # Generate recommendations
        eva_generate_recommendations()
        
    except Exception as e:
        logger.error(f"Error in pattern learning: {e}")

def eva_generate_recommendations():
    """Generate EVA recommendations based on patterns"""
    recommendations = []
    patterns = state["eva"].get("patterns", {})
    nodes = state["eva"].get("nodes", {})
    
    for device_id, pattern in patterns.items():
        avg_w = pattern.get("avg_w", 0)
        node = nodes.get(device_id, {})
        current_power = node.get("last_power", 0)
        
        # High consumption anomaly
        if avg_w > 500 and current_power > avg_w * 1.5:
            recommendations.append({
                "type": "anomaly",
                "device_id": device_id,
                "message": f"Unusual consumption detected for {device_id}",
                "severity": "warning"
            })
        
        # Phantom load detection
        if avg_w < EVA_PHANTOM_THRESHOLD_W and avg_w > 0.5:
            recommendations.append({
                "type": "phantom",
                "device_id": device_id,
                "message": f"Possible phantom load: {device_id} ({avg_w:.1f}W standby)",
                "severity": "info"
            })
    
    state["eva"]["recommendations"] = recommendations
    if recommendations:
        logger.info(f"EVA: Generated {len(recommendations)} recommendations")

# H-6: Energy Map Snapshot
def eva_publish_map():
    """Publish full energy map to MQTT"""
    registry = load_eva_registry()
    
    # Enrich nodes with registry info
    enriched_nodes = {}
    for node_id, node_data in state["eva"]["nodes"].items():
        registry_info = registry.get(node_id, {})
        enriched_nodes[node_id] = {
            **node_data,
            "priority": registry_info.get("priority", "UNKNOWN"),
            "name": registry_info.get("name", node_id),
            "ha_entity": registry_info.get("ha_entity", "")
        }
    
    energy_map = {
        "timestamp": datetime.now().isoformat(),
        "system": {
            "soc": state["current_soc"],
            "tier": state["current_tier"],
            "pv_watts": state["current_watts"]
        },
        "nodes": enriched_nodes,
        "phantom_total": state["eva"]["phantom_cuts_performed"],
        "optimal_window": state["eva"]["last_optimal_window"],
        "recommendations": state["eva"]["recommendations"],
        "patterns_count": len(state["eva"]["patterns"])
    }
    
    mqtt_client.publish(MQTT_TOPICS["eva_map"], json.dumps(energy_map), retain=True)
    
    # Also write to InfluxDB for historical tracking
    point = Point("eva_map")
    point.field("soc", state["current_soc"])
    point.field("pv_watts", state["current_watts"])
    point.field("phantom_cuts", state["eva"]["phantom_cuts_performed"])
    safe_write(INFLUXDB_BUCKET_STATE, point)

# H-6b: Waste Analysis Publisher
def eva_publish_waste_analysis():
    """Publish energy waste analysis to MQTT for the Node-RED dashboard"""
    if not influx_client:
        return
    query_api = influx_client.query_api()
    query = f'''
    from(bucket: "{INFLUXDB_BUCKET_EVA_PATTERNS}")
      |> range(start: -24h)
      |> filter(fn: (r) => r["_measurement"] == "eva_patterns")
      |> filter(fn: (r) => r["_field"] == "avg_w")
      |> mean()
    '''
    try:
        tables = query_api.query(query)
        phantom_loads = []
        total_waste_w = 0.0
        for table in tables:
            for record in table.records:
                avg_w = record.get_value()
                node_id = record.values.get("node_id", "unknown")
                # Consider < 10W as potential phantom load
                if avg_w < 10.0:
                    phantom_loads.append({
                        "node_id": node_id,
                        "avg_watts": round(avg_w, 2),
                        "daily_kwh": round(avg_w * 24 / 1000, 3),
                        "monthly_cost_estimate": round(avg_w * 24 * 30 / 1000 * ELECTRICITY_RATE_USD_PER_KWH, 2)
                    })
                    total_waste_w += avg_w
        payload = {
            "phantom_loads": phantom_loads,
            "total_phantom_watts": round(total_waste_w, 2),
            "daily_waste_kwh": round(total_waste_w * 24 / 1000, 3),
            "monthly_cost_estimate_usd": round(total_waste_w * 24 * 30 / 1000 * ELECTRICITY_RATE_USD_PER_KWH, 2),
            "timestamp": datetime.now().isoformat()
        }
        mqtt_client.publish(MQTT_TOPICS["eva_waste_analysis"], json.dumps(payload), retain=True)
        logger.info(f"EVA: Published waste analysis ({len(phantom_loads)} phantom loads)")
    except Exception as e:
        logger.error(f"Error in waste analysis publish: {e}")

# H-7: Node Update Handler
def handle_eva_node_update(node_id, data_type, payload):
    """Handle incoming EVA node updates from MQTT"""
    if node_id not in state["eva"]["nodes"]: 
        state["eva"]["nodes"][node_id] = {"last_power": 0.0, "last_state": "UNKNOWN"}
    
    node = state["eva"]["nodes"][node_id]
    
    if data_type == "power": 
        node["last_power"] = float(payload)
        # Write to InfluxDB
        point = Point("eva_nodes").tag("node_id", node_id).field("power_w", float(payload))
        safe_write(INFLUXDB_BUCKET_EVA_NODES, point)
    elif data_type == "state": 
        node["last_state"] = payload

# H-8: Command Handler
def handle_eva_command(payload):
    """Handle EVA commands from MQTT"""
    commands = {
        "EVA_PHANTOM_CUT": eva_phantom_cut,
        "PUBLISH_MAP": eva_publish_map,
        "EVA_OPTIMIZE": eva_optimal_window_finder,
        "EVA_LEARN": eva_pattern_learning,
        "PUBLISH_WASTE_ANALYSIS": eva_publish_waste_analysis,
        "RELEASE_SCHEDULED_DEVICES": eva_release_scheduled_devices_now,
        "RELOAD_REGISTRY": lambda: logger.info("EVA: Registry reload requested")
    }
    
    if payload in commands:
        commands[payload]()
    else:
        logger.warning(f"Unknown EVA command: {payload}")

# H-9: Schedule Setup
def setup_eva_schedule():
    """Setup EVA scheduled tasks"""
    # Map updates
    map_interval = int(os.getenv("EVA_MAP_PUBLISH_INTERVAL_M", 1))
    schedule.every(map_interval).minutes.do(eva_publish_map)
    
    # Phantom cuts - hourly
    schedule.every().hour.do(eva_phantom_cut)
    
    # Optimal window - every 6 hours
    schedule.every(6).hours.do(eva_optimal_window_finder)
    
    # Pattern learning - daily at 2 AM
    schedule.every().day.at("02:00").do(eva_pattern_learning)
    
    # Recommendations generation - every 6 hours
    schedule.every(6).hours.do(eva_generate_recommendations)

    # Waste analysis publish - every 6 hours
    schedule.every(6).hours.do(eva_publish_waste_analysis)
    
    # Release due scheduled devices and check window start - every minute
    schedule.every().minute.do(eva_release_due_devices)
    schedule.every().minute.do(eva_check_window_start)

# ============================================================================
# ALERTS & NOTIFICATIONS
# ============================================================================

def publish_alert(msg, old_tier=None, new_tier=None, priority="default", tags=""):
    alert = {
        "message": msg, 
        "timestamp": datetime.now().isoformat(), 
        "soc": state["current_soc"]
    }
    if old_tier:
        alert["old_tier"] = old_tier
    if new_tier:
        alert["new_tier"] = new_tier
    
    mqtt_client.publish(MQTT_TOPICS["alerts_guard"], json.dumps(alert))
    
    try:
        requests.post(NTFY_URL, data=msg, headers={
            "Title": "Solar Guard",
            "Priority": priority,
            "Tags": tags
        }, timeout=5)
    except Exception as e:
        logger.error(f"ntfy publish failed: {e}")

# ============================================================================
# MAIN
# ============================================================================

def main():
    logger.info("Starting Energy Guard v3 Phase 5")
    load_state()
    
    threading.Thread(target=init_influx, daemon=True).start()
    threading.Thread(target=mqtt_thread_func, daemon=True).start()
    
    time.sleep(5)
    
    # Initial setup
    update_forecast()
    run_decision_engine()
    setup_eva_schedule()
    
    # Decision engine runs every 5 minutes
    schedule.every(5).minutes.do(run_decision_engine)
    
    # Also update forecast hourly
    schedule.every().hour.do(update_forecast)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
