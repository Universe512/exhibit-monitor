import psutil
import platform
import time
import os
import json
import threading
import ctypes
import sys
import subprocess
import tkinter as tk
import base64
from datetime import datetime
from io import BytesIO
from tkinter import filedialog, messagebox, ttk
from flask import Flask, jsonify, request
from flask_cors import CORS

try:
    import mss
    from PIL import Image, ImageDraw, ImageTk

    HAS_SCREEN_CAPTURE = True
except ImportError:
    HAS_SCREEN_CAPTURE = False

try:
    import pystray

    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# Forces the CREATE_NO_WINDOW flag onto every subprocess call made by the app or libraries.
if platform.system() == "Windows":
    CREATE_NO_WINDOW = 0x08000000
    _original_popen = subprocess.Popen

    class PatchedPopen(_original_popen):
        def __init__(self, *args, **kwargs):
            creationflags = kwargs.get("creationflags", 0)
            kwargs["creationflags"] = creationflags | CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = PatchedPopen


def hide_console():
    if platform.system() == "Windows":
        whnd = ctypes.windll.kernel32.GetConsoleWindow()
        if whnd != 0:
            ctypes.windll.user32.ShowWindow(whnd, 0)


hide_console()

try:
    import pygame.midi

    pygame.midi.init()
    HAS_MIDI = True
except Exception:
    HAS_MIDI = False

try:
    from pythonosc import udp_client

    HAS_OSC = True
except ImportError:
    HAS_OSC = False

try:
    import serial

    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

try:
    import GPUtil

    HAS_GPUTIL = True
except ImportError:
    HAS_GPUTIL = False

CONFIG_FILE = "monitor_config.json"


def load_config():
    defaults = {
        "watched_apps": [],
        "display_name": platform.node(),
        "location": "Gallery Main",
        "midi_device_index": -1,
        "serial_port": "COM1",
        "serial_baud": 9600,
        "presets": [],
        "autolaunch_time": "09:00",
        "shutdown_time": "22:00",
        "automation_enabled": True,
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                for key, val in defaults.items():
                    if key not in config:
                        config[key] = val
                migrated_apps = []
                for app in config["watched_apps"]:
                    if isinstance(app, str):
                        migrated_apps.append({"path": app, "autolaunch": True})
                    else:
                        migrated_apps.append(app)
                config["watched_apps"] = migrated_apps
                return config
        except:
            pass
    return defaults


def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)


config_data = load_config()


def check_requires_admin(exe_path):
    if (
        not exe_path
        or not os.path.exists(exe_path)
        or not exe_path.lower().endswith(".exe")
    ):
        return False
    try:
        admin_patterns = [
            b'level="requireAdministrator"',
            b'level="highestAvailable"',
            b"requireAdministrator",
        ]
        with open(exe_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(0)
            head = f.read(min(size, 1024 * 1024))
            tail = b""
            if size > 1024 * 1024:
                f.seek(size - 1024 * 1024)
                tail = f.read()
            combined_content = head + tail
            for pattern in admin_patterns:
                if pattern in combined_content:
                    return True
    except:
        pass
    return False


# --- SCHEDULER LOGIC ---
last_trigger_min = ""


def scheduler_loop():
    global last_trigger_min
    while True:
        if not config_data.get("automation_enabled", True):
            time.sleep(30)
            continue
        now = datetime.now().strftime("%H:%M")
        if now != last_trigger_min:
            st = config_data.get("shutdown_time")
            if st and st == now:
                if platform.system() == "Windows":
                    subprocess.Popen("shutdown /s /t 60")
                else:
                    os.system("sudo shutdown -h now")
            at = config_data.get("autolaunch_time")
            if at and at == now:
                running_names = [
                    p.info["name"].lower() for p in psutil.process_iter(["name"])
                ]
                for app_entry in config_data.get("watched_apps", []):
                    full_path = app_entry.get("path")
                    if not app_entry.get("autolaunch", True) or not full_path:
                        continue
                    app_name = os.path.basename(full_path).lower()
                    if app_name not in running_names and os.path.exists(full_path):
                        try:
                            if check_requires_admin(full_path):
                                ctypes.windll.shell32.ShellExecuteW(
                                    None, "runas", full_path, None, None, 1
                                )
                            else:
                                subprocess.Popen(full_path)
                        except:
                            pass
            last_trigger_min = now
        time.sleep(20)


app = Flask(__name__)
CORS(app)


# --- MIDI MANAGER CLASS ---
class MidiManager:
    def __init__(self):
        self.output = None
        self.current_idx = -2
        self.lock = threading.Lock()

    def get_output(self):
        idx = config_data.get("midi_device_index", -1)
        if idx == -1:
            idx = pygame.midi.get_default_output_id()

        # Only re-open if the device index actually changed or it's crashed/None
        if self.output is None or idx != self.current_idx:
            with self.lock:
                try:
                    if self.output:
                        try:
                            self.output.close()
                        except:
                            pass

                    pygame.midi.quit()
                    pygame.midi.init()

                    if idx >= 0:
                        self.output = pygame.midi.Output(idx)
                        self.current_idx = idx
                        print(f"MIDI connected to ID: {idx}")
                    else:
                        self.output = None
                except Exception as e:
                    print(f"Failed to open MIDI: {e}")
                    self.output = None
        return self.output


midi_mgr = MidiManager()


def get_gpu_data():
    if not HAS_GPUTIL:
        return {"load": 0, "temp": 0}
    try:
        gpus = GPUtil.getGPUs()
        if gpus:
            return {"load": round(gpus[0].load * 100, 1), "temp": gpus[0].temperature}
    except:
        pass
    return {"load": 0, "temp": 0}


def get_system_temp():
    try:
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if temps:
                for entries in temps.values():
                    if entries:
                        return entries[0].current
        return 45.0
    except:
        return 45.0


@app.route("/health", methods=["GET"])
def health():
    app_status = []
    watched_names = [os.path.basename(e["path"]) for e in config_data["watched_apps"]]
    running_procs = {}
    for p in psutil.process_iter(["name", "create_time"]):
        try:
            if p.info["name"] in watched_names:
                running_procs[p.info["name"]] = p.info
        except:
            continue
    for app_entry in config_data["watched_apps"]:
        full_path = app_entry["path"]
        app_name = os.path.basename(full_path)
        is_running = app_name in running_procs
        uptime = "0m"
        if is_running:
            try:
                diff = time.time() - running_procs[app_name]["create_time"]
                uptime = f"{int(diff // 3600)}h {int((diff % 3600) // 60)}m"
            except:
                uptime = "Unknown"
        app_status.append(
            {
                "name": app_name,
                "path": full_path,
                "status": "running" if is_running else "stopped",
                "uptime": uptime,
                "autolaunch": app_entry.get("autolaunch", True),
            }
        )
    gpu_info = get_gpu_data()
    return jsonify(
        {
            "id": platform.node(),
            "name": config_data.get("display_name"),
            "location": config_data.get("location"),
            "status": "online",
            "vitals": {
                "cpu": psutil.cpu_percent(interval=0.1),
                "ram": psutil.virtual_memory().percent,
                "gpu": gpu_info["load"],
                "temp": get_system_temp(),
            },
            "apps": app_status,
            "presets": config_data.get("presets", []),
            "automation": {
                "autolaunch": config_data.get("autolaunch_time"),
                "shutdown": config_data.get("shutdown_time"),
                "enabled": config_data.get("automation_enabled"),
            },
        }
    )


@app.route("/action/schedule", methods=["POST"])
def update_schedule():
    data = request.json
    if "autolaunch" in data:
        config_data["autolaunch_time"] = data["autolaunch"]
    if "shutdown" in data:
        config_data["shutdown_time"] = data["shutdown"]
    if "enabled" in data:
        config_data["automation_enabled"] = data["enabled"]
    save_config(config_data)
    return jsonify({"message": "Schedule updated"})


@app.route("/action/screenshot", methods=["GET"])
def get_screenshot():
    if not HAS_SCREEN_CAPTURE:
        return jsonify({"error": "Missing libs"}), 500
    try:
        with mss.mss() as sct:
            img = Image.frombytes(
                "RGB",
                sct.grab(sct.monitors[1]).size,
                sct.grab(sct.monitors[1]).bgra,
                "raw",
                "BGRX",
            )
            img = img.resize(
                (1600, int(img.size[1] * (1600 / img.size[0]))),
                Image.Resampling.LANCZOS,
            )
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            return jsonify({"image": base64.b64encode(buffer.getvalue()).decode()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def execute_midi(data):
    if not HAS_MIDI:
        return jsonify({"error": "No MIDI support"}), 500

    out = midi_mgr.get_output()
    if not out:
        return jsonify({"error": "MIDI Device not ready or not found"}), 500

    try:
        note = int(data.get("note", 60))
        vel = int(data.get("velocity", 100))
        chan = int(data.get("channel", 1)) - 1

        # Direct call protected by the manager lock
        with midi_mgr.lock:
            out.note_on(note, vel, chan)

        # Background thread for Note Off using a localized pointer reference
        def note_off_delayed(target_out, n, v, c):
            time.sleep(0.2)
            try:
                with midi_mgr.lock:
                    target_out.note_off(n, v, c)
            except:
                pass  # Handle might be invalidated or closed, ignore safely

        threading.Thread(
            target=note_off_delayed, args=(out, note, vel, chan), daemon=True
        ).start()

        return jsonify({"status": "sent"})

    except Exception as e:
        print(f"MIDI Execution Error: {e}")
        # Reset the manager state so it re-initializes on the next attempt
        midi_mgr.output = None
        return jsonify({"error": str(e)}), 500


def execute_osc(data):
    if not HAS_OSC:
        return jsonify({"error": "No OSC"}), 500
    try:
        client = udp_client.SimpleUDPClient("127.0.0.1", int(data["port"]))
        client.send_message(data["path"], float(data["value"]))
        return jsonify({"message": "OSC sent"})
    except:
        return jsonify({"error": "OSC Failed"}), 500


def execute_serial(data):
    if not HAS_SERIAL:
        return jsonify({"error": "No Serial"}), 500
    try:
        ser = serial.Serial(
            data.get("port") or config_data.get("serial_port", "COM1"),
            config_data.get("serial_baud", 9600),
            timeout=1,
        )
        ser.write(data["message"].encode())
        ser.close()
        return jsonify({"message": "Serial sent"})
    except:
        return jsonify({"error": "Serial Failed"}), 500


@app.route("/action/control/midi", methods=["POST"])
def manual_midi():
    return execute_midi(request.json)


@app.route("/action/control/osc", methods=["POST"])
def manual_osc():
    return execute_osc(request.json)


@app.route("/action/control/serial", methods=["POST"])
def manual_serial():
    return execute_serial(request.json)


@app.route("/action/control/preset", methods=["POST"])
def trigger_preset():
    preset = next(
        (p for p in config_data["presets"] if p["name"] == request.json.get("name")),
        None,
    )
    if not preset:
        return jsonify({"error": "Not found"}), 404
    t = preset.get("type")
    if t == "midi":
        return execute_midi(preset)
    elif t == "osc":
        return execute_osc(preset)
    elif t == "serial":
        return execute_serial(preset)
    return jsonify({"error": "Unknown type"}), 400


@app.route("/action/reboot", methods=["POST"])
def reboot():
    if platform.system() == "Windows":
        subprocess.Popen("shutdown /r /t 1")
    else:
        os.system("sudo reboot")
    return jsonify({"message": "Rebooting"})


@app.route("/action/restart-app", methods=["POST"])
def restart_app():
    name = request.json.get("name")
    path = next(
        (
            p["path"]
            for p in config_data["watched_apps"]
            if os.path.basename(p["path"]) == name
        ),
        None,
    )
    for p in psutil.process_iter(["name"]):
        try:
            if p.info["name"] == name:
                p.kill()
        except:
            continue
    if path and os.path.exists(path):
        try:
            if check_requires_admin(path):
                ctypes.windll.shell32.ShellExecuteW(None, "runas", path, None, None, 1)
            else:
                subprocess.Popen(path)
            return jsonify({"message": "Restarted"})
        except:
            pass
    return jsonify({"error": "Failed"}), 500


class MonitorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Exhibit Monitor Agent")
        self.root.geometry("900x700")
        self.root.configure(bg="#0f172a")

        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)
        self.icon = None
        if HAS_TRAY:
            self.app_icon_img = self.create_icon_image(32, 32)
            self.tk_icon = ImageTk.PhotoImage(self.app_icon_img)
            self.root.iconphoto(False, self.tk_icon)
            self.create_tray_icon()

        self.last_midi_list = []
        self.current_page = None
        self.pages = {}

        # Styles
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure(
            "TCombobox",
            fieldbackground="#1e293b",
            background="#334155",
            foreground="white",
            arrowcolor="white",
        )
        self.style.configure(
            "TSpinbox",
            fieldbackground="#0f172a",
            background="#1e293b",
            foreground="white",
            arrowcolor="white",
        )

        # Sidebar
        self.sidebar = tk.Frame(root, bg="#1e293b", width=200)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)

        brand_frame = tk.Frame(self.sidebar, bg="#1e293b", pady=40)
        brand_frame.pack(fill=tk.X)
        tk.Label(
            brand_frame,
            text="EXHIBIT",
            bg="#1e293b",
            fg="#3b82f6",
            font=("Segoe UI", 16, "bold"),
        ).pack()
        tk.Label(
            brand_frame,
            text="MONITOR",
            bg="#1e293b",
            fg="#94a3b8",
            font=("Segoe UI", 10, "bold"),
        ).pack()

        self.nav_buttons = {}
        nav_items = [
            ("Dashboard", self.show_dashboard),
            ("Identity", self.show_identity),
            ("Hardware", self.show_hardware),
            ("Automation", self.show_automation),
            ("App Watcher", self.show_apps),
            ("Presets", self.show_presets),
        ]

        for text, command in nav_items:
            btn = tk.Button(
                self.sidebar,
                text=f"  {text}",
                command=command,
                bg="#1e293b",
                fg="#94a3b8",
                activebackground="#334155",
                activeforeground="white",
                bd=0,
                font=("Segoe UI", 10, "bold"),
                anchor="w",
                padx=20,
                pady=15,
            )
            btn.pack(fill=tk.X)
            self.nav_buttons[text] = btn

        # Content Container
        self.content_container = tk.Frame(root, bg="#0f172a")
        self.content_container.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.header = tk.Frame(self.content_container, bg="#0f172a", height=80)
        self.header.pack(fill=tk.X, padx=40, pady=(30, 20))

        self.page_title = tk.Label(
            self.header,
            text="Dashboard",
            bg="#0f172a",
            fg="white",
            font=("Segoe UI", 24, "bold"),
        )
        self.page_title.pack(side=tk.LEFT)

        self.clock_label = tk.Label(
            self.header,
            text="00:00:00",
            bg="#0f172a",
            fg="#4ade80",
            font=("Consolas", 16, "bold"),
        )
        self.clock_label.pack(side=tk.RIGHT)

        self.main_area = tk.Frame(self.content_container, bg="#0f172a")
        self.main_area.pack(fill=tk.BOTH, expand=True, padx=40, pady=0)

        self.init_pages()
        self.show_dashboard()

        self.tick()
        self.update_stats()
        self.update_midi_devices()

    def init_pages(self):
        for name in [
            "Dashboard",
            "Identity",
            "Hardware",
            "Automation",
            "App Watcher",
            "Presets",
        ]:
            self.pages[name] = tk.Frame(self.main_area, bg="#0f172a")

        self.setup_dashboard_page()
        self.setup_identity_page()
        self.setup_hardware_page()
        self.setup_automation_page()
        self.setup_apps_page()
        self.setup_presets_page()

    def show_page(self, name):
        if self.current_page:
            self.current_page.pack_forget()
        self.current_page = self.pages[name]
        self.current_page.pack(fill=tk.BOTH, expand=True)
        self.page_title.config(text=name)
        for text, btn in self.nav_buttons.items():
            if text == name:
                btn.config(bg="#334155", fg="white")
            else:
                btn.config(bg="#1e293b", fg="#94a3b8")

    def show_dashboard(self):
        self.show_page("Dashboard")

    def show_identity(self):
        self.show_page("Identity")

    def show_hardware(self):
        self.show_page("Hardware")

    def show_automation(self):
        self.show_page("Automation")

    def show_apps(self):
        self.refresh_app_list()
        self.show_page("App Watcher")

    def show_presets(self):
        self.refresh_presets()
        self.show_page("Presets")

    def setup_dashboard_page(self):
        page = self.pages["Dashboard"]
        cards_frame = tk.Frame(page, bg="#0f172a")
        cards_frame.pack(fill=tk.X, pady=10)

        self.stat_widgets = {}
        stats = [
            ("CPU USAGE", "cpu"),
            ("RAM USAGE", "ram"),
            ("GPU LOAD", "gpu"),
            ("CORE TEMP", "temp"),
        ]

        for i, (label, key) in enumerate(stats):
            card = tk.Frame(
                cards_frame, bg="#1e293b", padx=20, pady=20, width=180, height=120
            )
            card.grid(row=0, column=i, padx=5, sticky="nsew")
            card.pack_propagate(False)
            tk.Label(
                card,
                text=label,
                bg="#1e293b",
                fg="#64748b",
                font=("Segoe UI", 8, "bold"),
            ).pack(anchor="w")
            val_label = tk.Label(
                card,
                text="--",
                bg="#1e293b",
                fg="#3b82f6",
                font=("Segoe UI", 20, "bold"),
            )
            val_label.pack(anchor="w", pady=5)
            self.stat_widgets[key] = val_label
        cards_frame.columnconfigure((0, 1, 2, 3), weight=1)

        status_card = tk.Frame(page, bg="#1e293b", padx=25, pady=25)
        status_card.pack(fill=tk.X, pady=25)
        tk.Label(
            status_card,
            text="AGENT STATUS",
            bg="#1e293b",
            fg="white",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 15))

        grid = tk.Frame(status_card, bg="#1e293b")
        grid.pack(fill=tk.X)
        self.create_info_row(grid, "Node Name:", platform.node(), 0)
        self.create_info_row(grid, "Local IP:", "0.0.0.0 (Port 5001)", 1)
        self.create_info_row(
            grid, "Service Status:", "ACTIVE / LISTENING", 2, fg="#10b981"
        )

        actions = tk.Frame(page, bg="#0f172a")
        actions.pack(fill=tk.X, pady=10)
        tk.Button(
            actions,
            text="REBOOT SYSTEM",
            command=reboot,
            bg="#ef4444",
            fg="white",
            bd=0,
            font=("Segoe UI", 9, "bold"),
            padx=20,
            pady=10,
        ).pack(side=tk.LEFT)

    def create_info_row(self, parent, label, value, row, fg="#94a3b8"):
        tk.Label(
            parent, text=label, bg="#1e293b", fg="#64748b", font=("Segoe UI", 10)
        ).grid(row=row, column=0, sticky="w", pady=4)
        tk.Label(
            parent, text=value, bg="#1e293b", fg=fg, font=("Consolas", 10, "bold")
        ).grid(row=row, column=1, sticky="w", padx=20, pady=4)

    def setup_identity_page(self):
        page = self.pages["Identity"]
        f = tk.Frame(page, bg="#1e293b", padx=30, pady=30)
        f.pack(fill=tk.X, pady=20)
        self.name_entry = self.create_styled_input(
            f, "Agent Display Name", config_data["display_name"], 0
        )
        self.loc_entry = self.create_styled_input(
            f, "Physical Location", config_data["location"], 1
        )
        tk.Button(
            page,
            text="SAVE CHANGES",
            command=self.save_all_config,
            bg="#3b82f6",
            fg="white",
            bd=0,
            font=("Segoe UI", 10, "bold"),
            pady=12,
        ).pack(fill=tk.X, pady=20)

    def setup_hardware_page(self):
        page = self.pages["Hardware"]
        f = tk.Frame(page, bg="#1e293b", padx=30, pady=30)
        f.pack(fill=tk.X, pady=20)
        tk.Label(
            f,
            text="Default MIDI Output",
            bg="#1e293b",
            fg="#64748b",
            font=("Segoe UI", 10),
        ).grid(row=0, column=0, sticky="w")
        self.midi_cb = ttk.Combobox(f, state="readonly", width=40)
        self.midi_cb.grid(row=0, column=1, sticky="ew", padx=20, pady=15)
        self.serial_entry = self.create_styled_input(
            f, "Serial Port", config_data["serial_port"], 1
        )
        self.baud_entry = self.create_styled_input(
            f, "Baud Rate", str(config_data["serial_baud"]), 2
        )
        tk.Button(
            page,
            text="SAVE HARDWARE CONFIG",
            command=self.save_all_config,
            bg="#3b82f6",
            fg="white",
            bd=0,
            font=("Segoe UI", 10, "bold"),
            pady=12,
        ).pack(fill=tk.X, pady=20)

    def setup_automation_page(self):
        page = self.pages["Automation"]
        f = tk.Frame(page, bg="#1e293b", padx=30, pady=30)
        f.pack(fill=tk.X, pady=20)
        self.launch_h, self.launch_m = self.create_styled_time_picker(
            f, "Daily Startup Time", config_data.get("autolaunch_time", "09:00"), 0
        )
        self.shutdown_h, self.shutdown_m = self.create_styled_time_picker(
            f, "Daily Shutdown Time", config_data.get("shutdown_time", "22:00"), 1
        )
        self.auto_enabled_var = tk.BooleanVar(
            value=config_data.get("automation_enabled", True)
        )
        tk.Checkbutton(
            f,
            text="Enable Automatic Scheduling",
            variable=self.auto_enabled_var,
            bg="#1e293b",
            fg="white",
            selectcolor="#0f172a",
            activebackground="#1e293b",
            pady=20,
        ).grid(row=2, column=0, columnspan=2, sticky="w")
        tk.Button(
            page,
            text="SAVE AUTOMATION",
            command=self.save_all_config,
            bg="#3b82f6",
            fg="white",
            bd=0,
            font=("Segoe UI", 10, "bold"),
            pady=12,
        ).pack(fill=tk.X, pady=20)

    def setup_apps_page(self):
        page = self.pages["App Watcher"]
        container = tk.Frame(page, bg="#1e293b", pady=10)
        container.pack(fill=tk.BOTH, expand=True)
        self.app_canvas = tk.Canvas(container, bg="#1e293b", highlightthickness=0)
        self.app_scrollbar = ttk.Scrollbar(
            container, orient="vertical", command=self.app_canvas.yview
        )
        self.app_scroll_frame = tk.Frame(self.app_canvas, bg="#1e293b")
        self.app_scroll_frame.bind(
            "<Configure>",
            lambda e: self.app_canvas.configure(
                scrollregion=self.app_canvas.bbox("all")
            ),
        )
        self.app_canvas.create_window(
            (0, 0), window=self.app_scroll_frame, anchor="nw", width=600
        )
        self.app_canvas.configure(yscrollcommand=self.app_scrollbar.set)
        self.app_canvas.pack(side="left", fill="both", expand=True, padx=20)
        self.app_scrollbar.pack(side="right", fill="y")
        btns = tk.Frame(page, bg="#0f172a", pady=20)
        btns.pack(fill=tk.X)
        tk.Button(
            btns,
            text="+ ADD APP",
            command=self.add_app,
            bg="#10b981",
            fg="white",
            bd=0,
            font=("Segoe UI", 9, "bold"),
            padx=20,
            pady=10,
        ).pack(side=tk.LEFT)
        tk.Button(
            btns,
            text="REMOVE UNCHECKED",
            command=self.remove_apps,
            bg="#ef4444",
            fg="white",
            bd=0,
            font=("Segoe UI", 9, "bold"),
            padx=20,
            pady=10,
        ).pack(side=tk.RIGHT)

    def setup_presets_page(self):
        page = self.pages["Presets"]
        f = tk.Frame(page, bg="#1e293b")
        f.pack(fill=tk.BOTH, expand=True, pady=10)
        # Fix: Standard Listbox does not take 'padx' or 'pady' in constructor.
        self.preset_list = tk.Listbox(
            f,
            bg="#1e293b",
            fg="white",
            bd=0,
            highlightthickness=0,
            font=("Segoe UI", 10),
            selectbackground="#334155",
        )
        self.preset_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(f, orient="vertical", command=self.preset_list.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.preset_list.config(yscrollcommand=sb.set)
        btns = tk.Frame(page, bg="#0f172a", pady=10)
        btns.pack(fill=tk.X)
        cfg = {"bd": 0, "font": ("Segoe UI", 8, "bold"), "padx": 12, "pady": 8}
        tk.Button(
            btns,
            text="+ MIDI",
            command=lambda: self.add_preset_dialog("midi"),
            bg="#8b5cf6",
            fg="white",
            **cfg,
        ).pack(side=tk.LEFT, padx=2)
        tk.Button(
            btns,
            text="+ OSC",
            command=lambda: self.add_preset_dialog("osc"),
            bg="#a78bfa",
            fg="white",
            **cfg,
        ).pack(side=tk.LEFT, padx=2)
        tk.Button(
            btns,
            text="+ SERIAL",
            command=lambda: self.add_preset_dialog("serial"),
            bg="#10b981",
            fg="white",
            **cfg,
        ).pack(side=tk.LEFT, padx=2)
        tk.Button(
            btns,
            text="DELETE",
            command=self.delete_preset,
            bg="#ef4444",
            fg="white",
            **cfg,
        ).pack(side=tk.RIGHT, padx=2)

    def create_styled_input(self, parent, label, value, row):
        tk.Label(
            parent, text=label, bg="#1e293b", fg="#64748b", font=("Segoe UI", 10)
        ).grid(row=row, column=0, sticky="w", pady=5)
        # Fix: Standard Entry does not take 'padx' in constructor. Styling with highlightthickness for 'border' effect.
        e = tk.Entry(
            parent,
            bg="#0f172a",
            fg="white",
            bd=0,
            highlightthickness=1,
            highlightbackground="#334155",
            highlightcolor="#3b82f6",
            insertbackground="white",
            font=("Segoe UI", 11),
        )
        e.insert(0, str(value))
        e.grid(row=row, column=1, sticky="ew", padx=20, pady=10)
        parent.columnconfigure(1, weight=1)
        return e

    def create_styled_time_picker(self, parent, label, current_time, row):
        tk.Label(
            parent, text=label, bg="#1e293b", fg="#64748b", font=("Segoe UI", 10)
        ).grid(row=row, column=0, sticky="w", pady=5)
        f = tk.Frame(parent, bg="#1e293b")
        f.grid(row=row, column=1, sticky="w", padx=20, pady=10)
        try:
            h, m = current_time.split(":")
        except:
            h, m = "00", "00"
        h_s = ttk.Spinbox(
            f, from_=0, to=23, format="%02.0f", width=5, font=("Segoe UI", 11)
        )
        h_s.set(h)
        h_s.pack(side=tk.LEFT)
        tk.Label(
            f, text=":", bg="#1e293b", fg="white", font=("Segoe UI", 12, "bold")
        ).pack(side=tk.LEFT, padx=5)
        m_s = ttk.Spinbox(
            f, from_=0, to=59, format="%02.0f", width=5, font=("Segoe UI", 11)
        )
        m_s.set(m)
        m_s.pack(side=tk.LEFT)
        return h_s, m_s

    def tick(self):
        self.clock_label.config(text=datetime.now().strftime("%H:%M:%S"))
        self.root.after(1000, self.tick)

    def update_stats(self):
        try:
            self.stat_widgets["cpu"].config(text=f"{psutil.cpu_percent()}%")
            self.stat_widgets["ram"].config(text=f"{psutil.virtual_memory().percent}%")
            gpu = get_gpu_data()
            self.stat_widgets["gpu"].config(text=f"{gpu['load']}%")
            self.stat_widgets["temp"].config(text=f"{get_system_temp()}°C")
        except:
            pass
        self.root.after(2000, self.update_stats)

    def create_icon_image(self, width, height):
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        dc = ImageDraw.Draw(image)
        s = width / 64
        dc.ellipse(
            (4 * s, 4 * s, 60 * s, 60 * s),
            fill=(30, 41, 59),
            outline=(59, 130, 246),
            width=max(1, int(4 * s)),
        )
        dc.ellipse((20 * s, 20 * s, 44 * s, 44 * s), fill=(59, 130, 246))
        dc.line(
            [
                (16 * s, 32 * s),
                (24 * s, 32 * s),
                (28 * s, 16 * s),
                (36 * s, 48 * s),
                (40 * s, 32 * s),
                (48 * s, 32 * s),
            ],
            fill=(255, 255, 255),
            width=max(1, int(3 * s)),
        )
        return image

    def create_tray_icon(self):
        image = self.create_icon_image(64, 64)
        menu = pystray.Menu(
            pystray.MenuItem("Restore", self.show_window),
            pystray.MenuItem("Exit", self.quit_all),
        )
        self.icon = pystray.Icon("exhibit_monitor", image, "Monitor Agent", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def minimize_to_tray(self):
        self.root.withdraw()
        if not HAS_TRAY:
            self.quit_all()

    def show_window(self):
        self.root.after(0, self.root.deiconify)

    def quit_all(self):
        if self.icon:
            self.icon.stop()
        self.root.destroy()
        os._exit(0)

    def update_midi_devices(self):
        if not HAS_MIDI:
            return
        try:
            # We use the manager's lock if we're going to touch MIDI internals
            with midi_mgr.lock:
                pygame.midi.quit()
                pygame.midi.init()
                midi_devices = ["None / Default"]
                for i in range(pygame.midi.get_count()):
                    info = pygame.midi.get_device_info(i)
                    if info[3] == 1:
                        name = info[1].decode("utf-8", errors="ignore")
                        midi_devices.append(f"{i}: {name}")
                if midi_devices != self.last_midi_list:
                    cur = self.midi_cb.get()
                    self.midi_cb["values"] = midi_devices
                    self.midi_cb.set(cur if cur in midi_devices else midi_devices[0])
                    self.last_midi_list = midi_devices
        except:
            pass
        self.root.after(5000, self.update_midi_devices)

    def save_all_config(self):
        config_data["display_name"] = self.name_entry.get()
        config_data["location"] = self.loc_entry.get()
        m_sel = self.midi_cb.get()
        config_data["midi_device_index"] = (
            int(m_sel.split(":")[0]) if ":" in m_sel else -1
        )
        config_data["serial_port"] = self.serial_entry.get()
        try:
            config_data["serial_baud"] = int(self.baud_entry.get())
        except:
            pass
        config_data["autolaunch_time"] = (
            f"{int(self.launch_h.get()):02}:{int(self.launch_m.get()):02}"
        )
        config_data["shutdown_time"] = (
            f"{int(self.shutdown_h.get()):02}:{int(self.shutdown_m.get()):02}"
        )
        config_data["automation_enabled"] = self.auto_enabled_var.get()
        for app_entry in config_data["watched_apps"]:
            if hasattr(app_entry, "_var"):
                app_entry["autolaunch"] = app_entry["_var"].get()
        save_config(config_data)
        messagebox.showinfo("Success", "Settings saved.")

    def refresh_app_list(self):
        for w in self.app_scroll_frame.winfo_children():
            w.destroy()
        for a in config_data["watched_apps"]:
            f = tk.Frame(self.app_scroll_frame, bg="#1e293b", pady=10)
            f.pack(fill=tk.X, pady=2)
            v = tk.BooleanVar(value=a.get("autolaunch", True))
            a["_var"] = v
            tk.Checkbutton(
                f,
                variable=v,
                bg="#1e293b",
                activebackground="#1e293b",
                selectcolor="#0f172a",
            ).pack(side=tk.LEFT, padx=10)
            lbls = tk.Frame(f, bg="#1e293b")
            lbls.pack(side=tk.LEFT, fill=tk.X)
            tk.Label(
                lbls,
                text=os.path.basename(a["path"]),
                bg="#1e293b",
                fg="white",
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w")
            tk.Label(
                lbls, text=a["path"], bg="#1e293b", fg="#64748b", font=("Segoe UI", 8)
            ).pack(anchor="w")

    def add_app(self):
        p = filedialog.askopenfilename(
            filetypes=[("Executable", "*.exe"), ("All Files", "*.*")]
        )
        if p:
            if not any(x["path"] == p for x in config_data["watched_apps"]):
                config_data["watched_apps"].append({"path": p, "autolaunch": True})
                save_config(config_data)
                self.refresh_app_list()

    def remove_apps(self):
        config_data["watched_apps"] = [
            a for a in config_data["watched_apps"] if a["_var"].get()
        ]
        save_config(config_data)
        self.refresh_app_list()

    def refresh_presets(self):
        self.preset_list.delete(0, tk.END)
        for p in config_data["presets"]:
            icon = "🎹" if p["type"] == "midi" else "🌐" if p["type"] == "osc" else "🔌"
            self.preset_list.insert(
                tk.END, f" {icon}  {p['name']} ({p['type'].upper()})"
            )

    def delete_preset(self):
        s = self.preset_list.curselection()
        if s:
            config_data["presets"].pop(s[0])
            save_config(config_data)
            self.refresh_presets()

    def add_preset_dialog(self, p_type):
        d = tk.Toplevel(self.root)
        d.title(f"New {p_type.upper()}")
        d.geometry("350x450")
        d.configure(bg="#1e293b")
        d.transient(self.root)
        d.grab_set()
        tk.Label(
            d,
            text=f"NEW {p_type.upper()} PRESET",
            bg="#1e293b",
            fg="#3b82f6",
            font=("Segoe UI", 12, "bold"),
        ).pack(pady=20)
        tk.Label(d, text="Name:", bg="#1e293b", fg="#94a3b8").pack()
        n_e = tk.Entry(d, bg="#0f172a", fg="white", bd=0, insertbackground="white")
        n_e.pack(pady=5, padx=20, fill=tk.X)
        if p_type == "midi":
            tk.Label(d, text="Note / Vel:", bg="#1e293b", fg="#94a3b8").pack()
            f = tk.Frame(d, bg="#1e293b")
            f.pack()
            nt_e = tk.Entry(f, width=10, bg="#0f172a", fg="white", bd=0)
            nt_e.insert(0, "60")
            nt_e.pack(side=tk.LEFT, padx=5)
            vl_e = tk.Entry(f, width=10, bg="#0f172a", fg="white", bd=0)
            vl_e.insert(0, "100")
            vl_e.pack(side=tk.LEFT, padx=5)
        elif p_type == "osc":
            tk.Label(d, text="Path:", bg="#1e293b", fg="#94a3b8").pack()
            pt_e = tk.Entry(d, bg="#0f172a", fg="white", bd=0)
            pt_e.insert(0, "/trigger")
            pt_e.pack(pady=5, padx=20, fill=tk.X)
            tk.Label(d, text="Port / Val:", bg="#1e293b", fg="#94a3b8").pack()
            f = tk.Frame(d, bg="#1e293b")
            f.pack()
            pr_e = tk.Entry(f, width=10, bg="#0f172a", fg="white", bd=0)
            pr_e.insert(0, "8000")
            pr_e.pack(side=tk.LEFT, padx=5)
            va_e = tk.Entry(f, width=10, bg="#0f172a", fg="white", bd=0)
            va_e.insert(0, "1.0")
            va_e.pack(side=tk.LEFT, padx=5)
        else:
            tk.Label(d, text="Message:", bg="#1e293b", fg="#94a3b8").pack()
            ms_e = tk.Entry(d, bg="#0f172a", fg="white", bd=0)
            ms_e.pack(pady=5, padx=20, fill=tk.X)

        def save():
            new_p = {"name": n_e.get(), "type": p_type}
            if p_type == "midi":
                new_p.update({"note": nt_e.get(), "velocity": vl_e.get(), "channel": 1})
            elif p_type == "osc":
                new_p.update(
                    {"path": pt_e.get(), "port": pr_e.get(), "value": va_e.get()}
                )
            else:
                new_p.update({"message": ms_e.get()})
            config_data["presets"].append(new_p)
            save_config(config_data)
            self.refresh_presets()
            d.destroy()

        tk.Button(
            d,
            text="SAVE",
            command=save,
            bg="#10b981",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            bd=0,
            pady=10,
        ).pack(pady=30, padx=20, fill=tk.X)


def run_flask():
    app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    root = tk.Tk()
    gui = MonitorGUI(root)
    root.mainloop()