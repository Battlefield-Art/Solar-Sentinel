#!/usr/bin/env python3
"""
Solar-Sentinel-AIO v3 Phase 5
Hermes AI Agent - Gemini API with Function Calling
12 Native Tools for Energy Management and EVA Control
"""

import os
import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path
import paho.mqtt.client as mqtt
import google.generativeai as genai
from google.generativeai import types
from influxdb_client import InfluxDBClient

# Configure logging
LOG_FILE = "/data/logs/hermes.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("HermesAgent")

# MQTT Configuration
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", None)
MQTT_PASS = os.getenv("MQTT_PASS", None)

# Gemini API Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

# InfluxDB Configuration
INFLUX_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUX_ORG = os.getenv("INFLUXDB_ORG", "my-org")

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

INFLUX_TOKEN = _influx_token()

WASTE_BUCKET = os.getenv("INFLUXDB_BUCKET_EVA_PATTERNS", "eva_patterns")
ELECTRICITY_RATE_USD_PER_KWH = float(os.getenv("ELECTRICITY_RATE_USD_PER_KWH", 0.15))

# MQTT Topics
INBOX_TOPIC = "solar/hermes/inbox"
OUTBOX_TOPIC = "solar/hermes/outbox"
STATUS_TOPIC = "solar/hermes/status"

# File paths
GUARD_STATE_FILE = "/data/guard/guard_state.json"
EVA_REGISTRY_FILE = "/data/guard/eva_registry.json"
HERMES_HISTORY_FILE = "/data/agent/hermes_history.json"

if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY not set. Hermes will not be able to function.")
    sys.exit(1)

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)


class HermesAgent:
    """Hermes AI Agent using Gemini API with Function Calling"""
    
    def __init__(self):
        self.mqtt_client = mqtt.Client(client_id="hermes_agent")
        if MQTT_USER and MQTT_PASS:
            self.mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
        
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        
        self.influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        self.model = None
        self.available_functions = {}
        self.setup_gemini()
        logger.info("Hermes Agent initialized")

    def setup_gemini(self):
        """Define all 12 tools for function calling"""
        
        # Tool 1: get_system_status
        def get_system_status() -> types.FunctionDeclaration:
            """Returns current system status including SOC, PV watts, and Energy Guard tier."""
            state = self.load_json_file(GUARD_STATE_FILE)
            return {
                "soc": state.get("current_soc"),
                "pv_watts": state.get("current_watts"),
                "guard_tier": state.get("current_tier"),
                "forecast_daily_kwh": state.get("forecast_daily_kwh", {}),
                "timestamp": state.get("timestamp"),
                "soc_lockout": state.get("config", {}).get("soc_lockout"),
                "soc_warning": state.get("config", {}).get("soc_warning")
            }
        
        # Tool 2: lock_appliance
        def lock_appliance(device_id: str) -> dict:
            """Locks a specific appliance via EVA. Device will be turned off during low battery conditions."""
            topic = f"solar/appliance/{device_id}/lock"
            self.mqtt_client.publish(topic, "LOCK", retain=True)
            self.save_to_history(f"Locked appliance: {device_id}")
            return {"status": "success", "action": "lock", "device_id": device_id}
        
        # Tool 3: unlock_appliance
        def unlock_appliance(device_id: str) -> dict:
            """Unlocks a specific appliance via EVA. Device will be allowed to run normally."""
            topic = f"solar/appliance/{device_id}/lock"
            self.mqtt_client.publish(topic, "UNLOCK", retain=True)
            self.save_to_history(f"Unlocked appliance: {device_id}")
            return {"status": "success", "action": "unlock", "device_id": device_id}
        
        # Tool 4: lock_all_appliances
        def lock_all_appliances() -> dict:
            """Locks all appliances in the EVA registry. Use during critical battery conditions."""
            registry = self.load_json_file(EVA_REGISTRY_FILE)
            locked = []
            for device_id in registry.keys():
                topic = f"solar/appliance/{device_id}/lock"
                self.mqtt_client.publish(topic, "LOCK", retain=True)
                locked.append(device_id)
            self.save_to_history(f"Locked all appliances: {locked}")
            return {"status": "success", "action": "lock_all", "devices": locked}
        
        # Tool 5: unlock_all_appliances
        def unlock_all_appliances() -> dict:
            """Unlocks all appliances in the EVA registry. Use when battery is restored."""
            registry = self.load_json_file(EVA_REGISTRY_FILE)
            unlocked = []
            for device_id in registry.keys():
                topic = f"solar/appliance/{device_id}/lock"
                self.mqtt_client.publish(topic, "UNLOCK", retain=True)
                unlocked.append(device_id)
            self.save_to_history(f"Unlocked all appliances: {unlocked}")
            return {"status": "success", "action": "unlock_all", "devices": unlocked}
        
        # Tool 6: get_forecast
        def get_forecast(days: int = 3) -> dict:
            """Returns solar production forecast in kWh. Default is 3 days."""
            state = self.load_json_file(GUARD_STATE_FILE)
            forecast = state.get("forecast_daily_kwh", {})
            sorted_keys = sorted(forecast.keys())[:days]
            return {k: forecast[k] for k in sorted_keys}
        
        # Tool 7: set_threshold
        def set_threshold(threshold_type: str, value: float) -> dict:
            """Sets a SOC threshold value. Types: soc_lockout, soc_warning, soc_advisory, soc_abundance."""
            valid_types = ["soc_lockout", "soc_warning", "soc_advisory", "soc_abundance"]
            if threshold_type not in valid_types:
                return {"status": "error", "message": f"Invalid threshold type. Use: {valid_types}"}
            
            topic = f"solar/guard/config/{threshold_type}"
            self.mqtt_client.publish(topic, str(value), retain=True)
            self.save_to_history(f"Set {threshold_type} to {value}")
            return {"status": "success", "threshold_type": threshold_type, "value": value}
        
        # Tool 8: send_notification
        def send_notification(message: str, priority: str = "default", tags: str = "") -> dict:
            """Sends a push notification via ntfy.sh."""
            import requests
            NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh/solar_sentinel_guard_alerts")
            try:
                requests.post(NTFY_URL, data=message, headers={
                    "Title": "Solar Sentinel - Hermes",
                    "Priority": priority,
                    "Tags": tags
                }, timeout=5)
                return {"status": "sent", "message": message}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        # Tool 9: get_energy_map
        def get_energy_map() -> dict:
            """Returns the current EVA Energy Map snapshot including all nodes and system status."""
            topic = "solar/eva/command"
            self.mqtt_client.publish(topic, "PUBLISH_MAP", retain=True)
            
            # Also read from state file
            state = self.load_json_file(GUARD_STATE_FILE)
            return {
                "timestamp": datetime.now().isoformat(),
                "system": {
                    "soc": state.get("current_soc"),
                    "tier": state.get("current_tier"),
                    "pv_watts": state.get("current_watts")
                },
                "nodes": state.get("eva", {}).get("nodes", {}),
                "phantom_cuts_total": state.get("eva", {}).get("phantom_cuts_performed", 0),
                "optimal_window": state.get("eva", {}).get("last_optimal_window")
            }
        
        # Tool 10: reschedule_device
        def reschedule_device(device_id: str, new_schedule: str) -> dict:
            """Schedules a device for a specific time window. new_schedule should be ISO format datetime."""
            topic = f"solar/eva/device/{device_id}/schedule"
            payload = json.dumps({"device_id": device_id, "scheduled_time": new_schedule})
            self.mqtt_client.publish(topic, payload, retain=True)
            self.save_to_history(f"Rescheduled {device_id} to {new_schedule}")
            return {"status": "success", "device_id": device_id, "scheduled_time": new_schedule}
        
        # Tool 11: classify_device
        def classify_device(device_id: str, priority: str) -> dict:
            """Classifies a device with EVA priority. Priority: CRITICAL, SHIFTABLE, LUXURY, PHANTOM."""
            valid_priorities = ["CRITICAL", "SHIFTABLE", "LUXURY", "PHANTOM"]
            if priority not in valid_priorities:
                return {"status": "error", "message": f"Invalid priority. Use: {valid_priorities}"}
            
            registry = self.load_json_file(EVA_REGISTRY_FILE)
            if device_id in registry:
                registry[device_id]["priority"] = priority
                self.save_json_file(EVA_REGISTRY_FILE, registry)
                self.mqtt_client.publish("solar/eva/command", "RELOAD_REGISTRY")
                self.save_to_history(f"Classified {device_id} as {priority}")
                return {"status": "success", "device_id": device_id, "priority": priority}
            return {"status": "error", "message": f"Device {device_id} not found in registry"}
        
        # Tool 12: get_waste_analysis
        def get_waste_analysis() -> dict:
            """Analyzes energy waste patterns from InfluxDB including phantom loads and inefficient usage."""
            query = f'''
            from(bucket: "{WASTE_BUCKET}")
              |> range(start: -24h)
              |> filter(fn: (r) => r["_measurement"] == "eva_patterns")
              |> filter(fn: (r) => r["_field"] == "avg_w")
              |> mean()
            '''
            try:
                tables = self.influx_client.query_api().query(query)
                phantom_loads = []
                total_waste_w = 0.0
                
                for table in tables:
                    for record in table.records:
                        avg_w = record.get_value()
                        node_id = record.values.get("node_id", "unknown")
                        
                        # Consider < 10W as potential phantom load
                        if avg_w < 10.0:
                            monthly_cost = round(avg_w * 24 * 30 / 1000 * ELECTRICITY_RATE_USD_PER_KWH, 2)
                            phantom_loads.append({
                                "node_id": node_id,
                                "avg_watts": round(avg_w, 2),
                                "daily_kwh": round(avg_w * 24 / 1000, 3),
                                "monthly_cost_estimate": monthly_cost
                            })
                            total_waste_w += avg_w
                
                return {
                    "phantom_loads": phantom_loads,
                    "total_phantom_watts": round(total_waste_w, 2),
                    "daily_waste_kwh": round(total_waste_w * 24 / 1000, 3),
                    "monthly_cost_estimate_usd": round(total_waste_w * 24 * 30 / 1000 * ELECTRICITY_RATE_USD_PER_KWH, 2),
                    "timestamp": datetime.now().isoformat()
                }
            except Exception as e:
                logger.error(f"Error querying waste analysis: {e}")
                return {"status": "error", "message": str(e)}

        # Map functions to tool specs for Gemini
        self.available_functions = {
            "get_system_status": get_system_status,
            "lock_appliance": lock_appliance,
            "unlock_appliance": unlock_appliance,
            "lock_all_appliances": lock_all_appliances,
            "unlock_all_appliances": unlock_all_appliances,
            "get_forecast": get_forecast,
            "set_threshold": set_threshold,
            "send_notification": send_notification,
            "get_energy_map": get_energy_map,
            "reschedule_device": reschedule_device,
            "classify_device": classify_device,
            "get_waste_analysis": get_waste_analysis
        }
        
        # Create tool specifications
        tools = {
            "function_declarations": [
                {
                    "name": "get_system_status",
                    "description": "Returns current system status including SOC, PV watts, and Energy Guard tier. Use this to check battery state and current power production.",
                    "parameters": {"type": "object", "properties": {}}
                },
                {
                    "name": "lock_appliance",
                    "description": "Locks a specific appliance via EVA. Device will be turned off during low battery conditions. Use when you need to manually lock a device.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "device_id": {"type": "string", "description": "The device identifier (e.g., 'washing_machine', 'ev_charger')"}
                        },
                        "required": ["device_id"]
                    }
                },
                {
                    "name": "unlock_appliance",
                    "description": "Unlocks a specific appliance via EVA. Device will be allowed to run normally. Use when battery is restored.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "device_id": {"type": "string", "description": "The device identifier"}
                        },
                        "required": ["device_id"]
                    }
                },
                {
                    "name": "lock_all_appliances",
                    "description": "Locks all appliances in the EVA registry. Use during critical battery conditions like LOCKOUT tier.",
                    "parameters": {"type": "object", "properties": {}}
                },
                {
                    "name": "unlock_all_appliances",
                    "description": "Unlocks all appliances in the EVA registry. Use when battery is restored to normal levels.",
                    "parameters": {"type": "object", "properties": {}}
                },
                {
                    "name": "get_forecast",
                    "description": "Returns solar production forecast in kWh for the upcoming days.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "days": {"type": "integer", "description": "Number of days to forecast (default: 3)", "default": 3}
                        }
                    }
                },
                {
                    "name": "set_threshold",
                    "description": "Sets a SOC threshold value for Energy Guard decisions.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "threshold_type": {"type": "string", "description": "Type: soc_lockout, soc_warning, soc_advisory, soc_abundance"},
                            "value": {"type": "number", "description": "Threshold percentage value"}
                        },
                        "required": ["threshold_type", "value"]
                    }
                },
                {
                    "name": "send_notification",
                    "description": "Sends a push notification via ntfy.sh to alert the user.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "description": "Notification message text"},
                            "priority": {"type": "string", "description": "Priority level: low, default, urgent", "default": "default"},
                            "tags": {"type": "string", "description": "ntfy tags (e.g., 'zap', 'warning')", "default": ""}
                        },
                        "required": ["message"]
                    }
                },
                {
                    "name": "get_energy_map",
                    "description": "Returns the current EVA Energy Map snapshot including all nodes, system status, and optimal windows.",
                    "parameters": {"type": "object", "properties": {}}
                },
                {
                    "name": "reschedule_device",
                    "description": "Schedules a device for a specific time window using the optimal energy window finder.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "device_id": {"type": "string", "description": "The device identifier"},
                            "new_schedule": {"type": "string", "description": "ISO format datetime for scheduled start (e.g., '2024-02-15T14:00:00')"}
                        },
                        "required": ["device_id", "new_schedule"]
                    }
                },
                {
                    "name": "classify_device",
                    "description": "Classifies a device with EVA priority. CRITICAL: always on, SHIFTABLE: can be moved, LUXURY: comfort item, PHANTOM: vampire load.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "device_id": {"type": "string", "description": "The device identifier"},
                            "priority": {"type": "string", "description": "Priority: CRITICAL, SHIFTABLE, LUXURY, PHANTOM"}
                        },
                        "required": ["device_id", "priority"]
                    }
                },
                {
                    "name": "get_waste_analysis",
                    "description": "Analyzes energy waste patterns from InfluxDB including phantom loads and inefficient usage. Returns potential savings.",
                    "parameters": {"type": "object", "properties": {}}
                }
            ]
        }
        
        # System instruction
        self.system_instruction = """You are Hermes, the AI agent for Solar-Sentinel-AIO v3.
You manage the energy system, EVA (Energy Value Analysis), and device control.
You have access to 12 tools for system management:
- get_system_status: Check battery SOC, PV power, guard tier
- lock_appliance/unlock_appliance: Control individual devices
- lock_all_appliances/unlock_all_appliances: Bulk device control
- get_forecast: Solar production forecasts
- set_threshold: Adjust guard thresholds
- send_notification: Push alerts to user
- get_energy_map: EVA energy map snapshot
- reschedule_device: Schedule for optimal window
- classify_device: Set EVA priority
- get_waste_analysis: Analyze phantom loads

Be concise, professional, and proactive. Suggest energy-saving actions when appropriate.
Always use tools to get real-time data before making recommendations."""

        # Initialize Gemini model with tools and system instruction
        self.model = genai.GenerativeModel(
            model_name='gemini-1.5-pro',
            tools=tools,
            system_instruction=self.system_instruction
        )
        # Persistent chat session for conversation memory (bounded history)
        self.chat = self.model.start_chat()

    def load_json_file(self, filepath):
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading {filepath}: {e}")
        return {}

    def save_json_file(self, filepath, data):
        try:
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving {filepath}: {e}")

    def save_to_history(self, action):
        """Save conversation/action to history for learning"""
        try:
            history = self.load_json_file(HERMES_HISTORY_FILE)
            if not isinstance(history, list):
                history = []
            history.append({
                "timestamp": datetime.now().isoformat(),
                "action": action
            })
            # Keep last 100 entries
            history = history[-100:]
            self.save_json_file(HERMES_HISTORY_FILE, history)
        except Exception as e:
            logger.error(f"Error saving to history: {e}")

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("Connected to MQTT Broker")
            client.subscribe(INBOX_TOPIC)
            client.publish(STATUS_TOPIC, "ONLINE", retain=True)
        else:
            logger.error(f"Failed to connect to MQTT, rc={rc}")

    def on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode()
            logger.info(f"Received message: {payload}")

            # Reuse the persistent chat session for conversation memory,
            # resetting once history grows too large
            chat = self.chat
            if len(chat.history) > 40:
                chat = self.model.start_chat()
                self.chat = chat

            # Send user message
            response = chat.send_message(payload)
            
            # Handle function calls
            while True:
                has_function_call = False
                if response.candidates and response.candidates[0].content.parts:
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, 'function_call') and part.function_call:
                            has_function_call = True
                            fn_name = part.function_call.name
                            fn_args = {k: v for k, v in part.function_call.args.items()} if part.function_call.args else {}
                            logger.info(f"Calling function: {fn_name} with args {fn_args}")
                            
                            if fn_name in self.available_functions:
                                try:
                                    result = self.available_functions[fn_name](**fn_args)
                                    logger.info(f"Function result: {result}")
                                    
                                    # Send function response back to Gemini
                                    response = chat.send_message(
                                        types.Content(
                                            parts=[types.Part(
                                                function_response=types.FunctionResponse(
                                                    name=fn_name,
                                                    response={"result": result}
                                                )
                                            )]
                                        )
                                    )
                                except TypeError:
                                    # Handle functions with no required args
                                    result = self.available_functions[fn_name]()
                                    response = chat.send_message(
                                        types.Content(
                                            parts=[types.Part(
                                                function_response=types.FunctionResponse(
                                                    name=fn_name,
                                                    response={"result": result}
                                                )
                                            )]
                                        )
                                    )
                            else:
                                logger.error(f"Function {fn_name} not found")
                                response = chat.send_message(
                                    types.Content(
                                        parts=[types.Part(
                                            function_response=types.FunctionResponse(
                                                name=fn_name,
                                                response={"error": f"Function {fn_name} not found"}
                                            )
                                        )]
                                    )
                                )
                            break
                if not has_function_call:
                    break
            
            # Get final response text
            try:
                final_text = response.text
            except Exception:
                final_text = "I have executed the requested command."

            out_msg = {
                "response": final_text,
                "timestamp": datetime.now().isoformat()
            }
            client.publish(OUTBOX_TOPIC, json.dumps(out_msg))
            self.save_to_history(f"User query: {payload[:100]}... Response: {final_text[:100]}...")
            logger.info(f"Sent response: {final_text[:200]}...")
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            client.publish(OUTBOX_TOPIC, json.dumps({
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }))

    def run(self):
        while True:
            try:
                self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
                self.mqtt_client.loop_forever()
            except Exception as e:
                logger.error(f"MQTT connection error: {e}. Retrying in 5s...")
                time.sleep(5)


if __name__ == "__main__":
    agent = HermesAgent()
    agent.run()
