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

# Disable annoying Flask request logging spam from the React background scanner
werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.setLevel(logging.ERROR)

def load_fleet():
    """Loads IPs and the polling rate. Handles upgrading old list-based configs."""
    if not os.path.exists(CONFIG_FILE):
        return [], 5.0
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
            # Upgrading legacy config file format (if it was just a list of IPs)
            if isinstance(data, list):
                return data, 5.0
            # Modern config format
            return data.get("ips", []), data.get("refresh_rate", 5.0)
    except (FileNotFoundError, json.JSONDecodeError):
        return [], 5.0

def save_fleet(ips, refresh_rate):
    """Saves both the IPs and the dynamic polling rate to disk"""
    with open(CONFIG_FILE, "w") as f:
        json.dump({"ips": ips, "refresh_rate": refresh_rate}, f)

# Initialize globals
monitored_ips, master_refresh_interval = load_fleet()
failure_tracker = {ip: 0 for ip in monitored_ips}
fleet_state = {}  
AGENT_PORT = 5001

app = Flask(__name__)
CORS(app) 

@app.route('/sync', methods=['GET', 'POST'])
def sync_fleet():
    global monitored_ips, failure_tracker, fleet_state, master_refresh_interval
    
    if request.method == 'GET':
        return jsonify({
            "status": "online", 
            "message": "Master Service reachable. Send a POST request to sync fleet.",
            "current_fleet_count": len(monitored_ips),
            "current_refresh_rate": master_refresh_interval
        })

    data = request.json
    new_ips = data.get("ips", monitored_ips)
    
    # Grab the new refresh rate from the React Dashboard (defaults to current if not sent)
    new_interval = data.get("refreshRate", master_refresh_interval)
    
    logging.info(f"SYNC RECEIVED: Updating fleet to {len(new_ips)} stations. Polling interval set to {new_interval}s.")
    save_fleet(new_ips, new_interval)
    
    monitored_ips = new_ips
    master_refresh_interval = float(new_interval)
    
    for ip in monitored_ips:
        if ip not in failure_tracker:
            failure_tracker[ip] = 0
        if ip not in fleet_state:
            fleet_state[ip] = {"ip": ip, "status": "unknown"}
            
    keys_to_remove = [ip for ip in fleet_state if ip not in monitored_ips]
    for ip in keys_to_remove:
        del fleet_state[ip]
            
    return jsonify({"status": "success", "count": len(monitored_ips), "refresh_rate": master_refresh_interval})

@app.route('/state', methods=['GET'])
def get_fleet_state():
    """Returns the fully cached state of all exhibits for the UI"""
    return jsonify({
        "status": "online",
        "fleet_count": len(monitored_ips),
        "state": fleet_state
    })

@app.route('/proxy/<path:subpath>', methods=['GET', 'POST'])
def proxy_to_agent(subpath):
    """Proxies commands from the React UI through the Master to the individual exhibit agents"""
    if request.method == 'POST':
        data = request.json or {}
        target_ip = data.get("target_ip")
    else:
        target_ip = request.args.get("target_ip")
        
    if not target_ip:
        return jsonify({"error": "target_ip is required in payload or query args"}), 400
        
    url = f"http://{target_ip}:{AGENT_PORT}/{subpath}"
    
    try:
        # FAST FAIL FOR SCANNERS: Health checks give up in 1.0 second so we don't choke the server.
        # Direct commands (reboot, screenshot) still get 5.0 seconds.
        timeout_val = 1.0 if 'health' in subpath else 5.0
        
        if request.method == 'POST':
            payload = {k: v for k, v in data.items() if k != 'target_ip'}
            resp = requests.post(url, json=payload, timeout=timeout_val)
        else:
            resp = requests.get(url, timeout=timeout_val)
            
        if resp.ok:
            try:
                return jsonify(resp.json())
            except:
                return jsonify({"message": resp.text})
        else:
            return jsonify({"error": "Agent returned an error", "status": resp.status_code}), resp.status_code
    except Exception as e:
        return jsonify({"error": f"Failed to reach agent at {target_ip}: {str(e)}"}), 500

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
    global monitored_ips, fleet_state, master_refresh_interval
    logging.info("Background Monitoring Thread Started.")
    
    # Pre-populate state for initial boot
    for ip in monitored_ips:
        if ip not in fleet_state:
            fleet_state[ip] = {"ip": ip, "status": "unknown"}

    while True:
        loop_start_time = time.time()
        current_list = list(monitored_ips)
        
        if not current_list:
            time.sleep(5)
            continue

        for ip in current_list:
            online, data = check_station(ip)
            if online:
                # Save real-time metrics to memory cache
                data['ip'] = ip
                data['status'] = 'online'
                data['last_seen'] = datetime.now().isoformat()
                fleet_state[ip] = data
                
                if failure_tracker.get(ip, 0) >= 3:
                    logging.info(f"RECOVERY: Station {ip} is back online.")
                failure_tracker[ip] = 0
            else:
                # Preserve last known name/location but mark offline
                existing = fleet_state.get(ip, {})
                fleet_state[ip] = {
                    "ip": ip, 
                    "status": "offline", 
                    "name": existing.get("name", ip),
                    "location": existing.get("location", "Unknown"),
                    "last_seen": existing.get("last_seen", None)
                }
                
                failure_tracker[ip] = failure_tracker.get(ip, 0) + 1
                if failure_tracker[ip] == 3:
                    logging.error(f"ALERT: Station {ip} MARKED OFFLINE.")
        
        # Determine how long the polling took, and sleep the remainder based on UI config
        elapsed = time.time() - loop_start_time
        sleep_needed = max(1.0, master_refresh_interval) - elapsed
        
        if sleep_needed > 0:
            # We break the sleep into 1-second chunks so the thread stays highly responsive
            # if a user suddenly Adopts a new station in the middle of a long sleep.
            chunks = int(sleep_needed)
            remainder = sleep_needed - chunks
            for _ in range(chunks):
                time.sleep(1)
            time.sleep(remainder)
        else:
            time.sleep(1) # Safety yield

if __name__ == "__main__":
    monitor_thread = threading.Thread(target=master_loop, daemon=True)
    monitor_thread.start()
    app.run(host='0.0.0.0', port=5002)