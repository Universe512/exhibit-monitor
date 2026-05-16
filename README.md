<div align="center">
  <h2 style="color: #ff4d4f;">⚠️ Current State: Broken</h2>
  <p><strong>Please read fixes needed before install.</strong></p>
</div>

---

<div style="padding: 15px; border-left: 5px solid #ff4d4f; background-color: #ffe6e6; color: #cc0000; margin-bottom: 20px; border-radius: 4px;">
  <strong>⚠️ CAUTION:</strong> The current build is unstable. Attempting to install may result in runtime errors or undesired behavior. A patch is currently in the works.
</div>

### 🛠️ Required Fixes
*When I add presets in the pc_app they do not show up until we go in and out of the tab

we Should be able to multiselect presets like shift click or control click

The app watcher section checkbox for autostart is not labeled and has a styling issue

The fleet config json should be saving all the information of the exhibits like names and settings 

The local ip is showing 0.0.0.0 on the pc_app

The core temp always just shows up as 45c*

# Museum Monitor: Operator & Deployment Guide

This guide provides technical instructions for server operators to deploy, manage, and persist the **Museum Monitor** fleet infrastructure using a central Linux server and Windows exhibit PCs.

---

## 1. System Architecture

* **Fleet Master (`fleet_master.py`)**: A central Linux-based service (Port 5002) that tracks the global list of exhibits, logs uptime, and provides a synchronization point for the UI.
* **Fleet Command UI (React)**: The web dashboard (Port 3000) used by operators to visualize and manage the fleet.
* **Monitor Agent (`monitor.py`)**: A service running on each individual Windows exhibit PC (Port 5001). It reports hardware vitals, app status, and enables remote control.

---

## 2. Server Requirements (Central Linux Server)

The central server (hosting both the Master and the UI) requires Python 3.8+, Node.js, and PM2.

### Initial Linux Setup

```bash
# Update and install system dependencies
sudo apt update
sudo apt install python3-pip nodejs npm -y

# Install PM2 and the static server globally
sudo npm install pm2 serve -g

```

---

## 3. Deploying the Fleet Master & UI (Linux)

### Installation

1. **Copy the entire project structure** to your server (e.g., `/opt/museum-monitor/`).
2. Navigate to the project root: `cd /opt/museum-monitor/`
3. Install Python requirements for the Master:
```bash
pip3 install flask flask-cors requests

```


4. Install Node requirements and build the UI:
```bash
npm install
npm run build

```



### Running with PM2

We will run both the Python Master Service and the production-built React UI using PM2 from the root project folder.

```bash
# 1. Start the Master Service
pm2 start fleet_master.py --name "fleet-master" --interpreter python3

# 2. Start the React UI Service
# This serves the 'build' folder created in the previous step
pm2 start serve --name "fleet-ui" -- -s build -l 3000

# Verify both are running
pm2 status

```

### Boot Persistence

```bash
# Generate startup script (follow the terminal instructions)
pm2 startup

# Save the current process list to load on boot
pm2 save

```

---

## 4. Building & Deploying the Monitor Agent (Windows)

The exhibit PC app should be packaged as a standalone executable for Windows.

### Step 1: Build the Executable

On a Windows machine with Python installed:

```powershell
# Install dependencies
pip install flask flask-cors psutil mss pillow pystray pygame python-osc pyserial gputil pyinstaller

# Build the standalone binary
pyinstaller --noconsole --onefile pc_app/monitor.py

```

### Step 2: Deployment on Exhibit PC

1. Copy `dist/monitor.exe` to the exhibit PC.
2. Place `monitor_config.json` in the same folder as the `.exe`.
3. Add a shortcut of `monitor.exe` to the Windows **Startup folder** (`shell:startup`).

---

## 5. Port Configuration Summary

| Service | Port | Description |
| --- | --- | --- |
| **Agent** | 5001 | API on Windows Exhibit PCs. |
| **Master** | 5002 | Sync service on Central Linux Server. |
| **UI** | 3000 | Fleet Command Dashboard (Web Interface). |

---

## 6. Dependency Checklist

### Master Service (Linux)

* `flask`: Web framework
* `flask-cors`: Cross-origin resource sharing
* `requests`: HTTP requests for polling agents

### Agent Service (Windows)

* `psutil`: System metrics
* `flask` & `flask-cors`: API server
* `mss` & `pillow`: Screen capture
* `pystray`: System tray icon
* `pygame`: MIDI support
* `python-osc`: OSC messaging
* `pyserial`: Serial communication
* `gputil`: GPU metrics

---

## 7. Operational Features

* **Logging**: Use `pm2 logs` to see real-time heartbeats and alerts.
* **Polling**: The Master pings agents every 30 seconds independently.
* **Alerting**: A station is marked `OFFLINE` after 3 failed pings.

---

## 8. Troubleshooting

Ensure **Port 5002** (Master) and **Port 3000** (UI) are open on the Linux firewall, and **Port 5001** is open on the Windows firewall.

```bash
# Example Linux Firewall Rules
sudo ufw allow 5002/tcp
sudo ufw allow 3000/tcp
