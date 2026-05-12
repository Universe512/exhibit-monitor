import requests
import time
import json
import logging
import threading
import os
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

CONFIG_FILE = "fleet_config.json"
LOG_FILE = "fleet_uptime.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)

def load_fleet():
    if not os.path.exists(CONFIG_FILE):
        return []
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_fleet(ips):
    with open(CONFIG_FILE, "w") as f:
        json.dump(ips, f)

monitored_ips = load_fleet()
failure_tracker = {ip: 0 for ip in monitored_ips}
AGENT_PORT = 5001

app = Flask(__name__)
CORS(app) 

@app.route('/sync', methods=['GET', 'POST']) # Added GET for easier testing
def sync_fleet():
    global monitored_ips, failure_tracker
    
    if request.method == 'GET':
        return jsonify({
            "status": "online", 
            "message": "Master Service reachable. Send a POST request to sync fleet.",
            "current_fleet_count": len(monitored_ips)
        })

    data = request.json
    new_ips = data.get("ips", [])
    
    logging.info(f"SYNC RECEIVED: Updating fleet to {len(new_ips)} stations.")
    save_fleet(new_ips)
    
    monitored_ips = new_ips
    for ip in monitored_ips:
        if ip not in failure_tracker:
            failure_tracker[ip] = 0
            
    return jsonify({"status": "success", "count": len(monitored_ips)})

def check_station(ip):
    url = f"http://{ip}:{AGENT_PORT}/health"
    try:
        response = requests.get(url, timeout=2)
        if response.ok:
            return True, response.json()
    except Exception:
        pass
    return False, None

def master_loop():
    global monitored_ips
    logging.info("Background Monitoring Thread Started.")
    while True:
        current_list = list(monitored_ips)
        if not current_list:
            time.sleep(10)
            continue

        for ip in current_list:
            online, data = check_station(ip)
            if online:
                if failure_tracker.get(ip, 0) >= 3:
                    logging.info(f"RECOVERY: Station {ip} is back online.")
                failure_tracker[ip] = 0
            else:
                failure_tracker[ip] = failure_tracker.get(ip, 0) + 1
                if failure_tracker[ip] == 3:
                    logging.error(f"ALERT: Station {ip} MARKED OFFLINE.")
        
        time.sleep(30)

if __name__ == "__main__":
    monitor_thread = threading.Thread(target=master_loop, daemon=True)
    monitor_thread.start()
    app.run(host='0.0.0.0', port=5002)
