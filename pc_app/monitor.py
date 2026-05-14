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
            creationflags = kwargs.get('creationflags', 0)
            kwargs['creationflags'] = creationflags | CREATE_NO_WINDOW
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
        "presets": []
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                for key, val in defaults.items():
                    if key not in config:
                        config[key] = val
                return config
        except: pass
    return defaults

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

config_data = load_config()

def check_requires_admin(exe_path):
    if not exe_path or not os.path.exists(exe_path) or not exe_path.lower().endswith('.exe'):
        return False
    try:
        admin_patterns = [b'level="requireAdministrator"', b'level="highestAvailable"', b'requireAdministrator']
        with open(exe_path, 'rb') as f:
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

app = Flask(__name__)
CORS(app)

midi_output_obj = None
midi_current_idx = -2
midi_lock = threading.Lock()

def get_gpu_data():
    if not HAS_GPUTIL: return {"load": 0, "temp": 0}
    try:
        gpus = GPUtil.getGPUs()
        if gpus: return {"load": round(gpus[0].load * 100, 1), "temp": gpus[0].temperature}
        return {"load": 0, "temp": 0}
    except: return {"load": 0, "temp": 0}

def get_system_temp():
    try:
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    if entries: return entries[0].current
        return 45.0
    except: return 45.0

@app.route('/health', methods=['GET'])
def health():
    app_status = []
    watched_names = [os.path.basename(path) for path in config_data["watched_apps"]]
    running_procs = {}
    for p in psutil.process_iter(['name', 'create_time']):
        try:
            name = p.info['name']
            if name in watched_names: running_procs[name] = p.info
        except: continue
    
    for full_path in config_data["watched_apps"]:
        app_name = os.path.basename(full_path)
        is_running = app_name in running_procs
        uptime = "0m"
        if is_running:
            try:
                diff = time.time() - running_procs[app_name]['create_time']
                uptime = f"{int(diff // 3600)}h {int((diff % 3600) // 60)}m"
            except: uptime = "Unknown"
        
        app_status.append({
            "name": app_name, 
            "path": full_path, 
            "status": "running" if is_running else "stopped", 
            "uptime": uptime,
            "requires_admin": check_requires_admin(full_path)
        })

    gpu_info = get_gpu_data()
    return jsonify({
        "id": platform.node(),
        "name": config_data.get("display_name"),
        "location": config_data.get("location"),
        "status": "online",
        "vitals": {
            "cpu": psutil.cpu_percent(interval=0.1),
            "ram": psutil.virtual_memory().percent,
            "gpu": gpu_info['load'],
            "temp": get_system_temp()
        },
        "apps": app_status,
        "presets": config_data.get("presets", [])
    })

@app.route('/action/screenshot', methods=['GET'])
def get_screenshot():
    if not HAS_SCREEN_CAPTURE:
        return jsonify({"error": "mss or Pillow libraries not installed"}), 500
    try:
        with mss.mss() as sct:
            # Capture the primary monitor
            monitor = sct.monitors[1]
            sct_img = sct.grab(monitor)
            
            # Convert to PIL Image
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            
            # --- RESOLUTION FIX ---
            # Set target width (e.g., 1920 for 1080p). 
            # Commenting out thumbnail() entirely will send the native resolution.
            target_width = 1600 
            w_percent = (target_width / float(img.size[0]))
            h_size = int((float(img.size[1]) * float(w_percent)))
            img = img.resize((target_width, h_size), Image.Resampling.LANCZOS)
            
            # Save to buffer as JPEG with 85% quality (much cleaner than 60)
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=85, optimize=True)
            img_str = base64.b64encode(buffer.getvalue()).decode()
            
            return jsonify({"image": img_str})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def execute_midi(data):
    global midi_output_obj, midi_current_idx
    if not HAS_MIDI: return jsonify({"error": "MIDI not available"}), 500
    with midi_lock:
        idx = config_data.get('midi_device_index', -1)
        if idx == -1: idx = pygame.midi.get_default_output_id()
        if idx == -1: return jsonify({"error": "No MIDI output device detected"}), 404
        try:
            if midi_output_obj is None or idx != midi_current_idx:
                if midi_output_obj:
                    try: midi_output_obj.close()
                    except: pass
                midi_output_obj = pygame.midi.Output(idx)
                midi_current_idx = idx
            midi_output_obj.note_on(int(data['note']), int(data['velocity']), int(data['channel']) - 1)
            time.sleep(0.1)
            midi_output_obj.note_off(int(data['note']), int(data['velocity']), int(data['channel']) - 1)
            return jsonify({"message": f"MIDI {data['note']} sent successfully"})
        except Exception as e: 
            midi_output_obj = None
            midi_current_idx = -2
            return jsonify({"error": str(e)}), 500

def execute_osc(data):
    if not HAS_OSC: return jsonify({"error": "OSC not available"}), 500
    try:
        client = udp_client.SimpleUDPClient("127.0.0.1", int(data['port']))
        client.send_message(data['path'], float(data['value']))
        return jsonify({"message": "OSC sent"})
    except Exception as e: return jsonify({"error": str(e)}), 500

def execute_serial(data):
    if not HAS_SERIAL: return jsonify({"error": "Serial not available"}), 500
    try:
        port = data.get('port') or config_data.get('serial_port', 'COM1')
        baud = config_data.get('serial_baud', 9600)
        ser = serial.Serial(port, baud, timeout=1)
        ser.write(data['message'].encode())
        ser.close()
        return jsonify({"message": "Serial sent"})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/action/control/midi', methods=['POST'])
def manual_midi(): return execute_midi(request.json)

@app.route('/action/control/osc', methods=['POST'])
def manual_osc(): return execute_osc(request.json)

@app.route('/action/control/serial', methods=['POST'])
def manual_serial(): return execute_serial(request.json)

@app.route('/action/control/preset', methods=['POST'])
def trigger_preset():
    preset_name = request.json.get('name')
    preset = next((p for p in config_data["presets"] if p['name'] == preset_name), None)
    if not preset: return jsonify({"error": "Preset not found"}), 404
    p_type = preset.get('type')
    if p_type == 'midi': return execute_midi(preset)
    elif p_type == 'osc': return execute_osc(preset)
    elif p_type == 'serial': return execute_serial(preset)
    return jsonify({"error": "Unknown type"}), 400

@app.route('/action/reboot', methods=['POST'])
def reboot():
    if platform.system() == "Windows":
        subprocess.Popen("shutdown /r /t 1")
    else:
        os.system("sudo reboot")
    return jsonify({"message": "Reboot command sent"})

@app.route('/action/restart-app', methods=['POST'])
def restart_app():
    data = request.json
    app_name = data.get('name')
    full_path = next((p for p in config_data["watched_apps"] if os.path.basename(p) == app_name), None)
    for proc in psutil.process_iter(['name']):
        try:
            if proc.info['name'] == app_name: proc.kill()
        except: continue
    if full_path and os.path.exists(full_path):
        try:
            if check_requires_admin(full_path):
                ctypes.windll.shell32.ShellExecuteW(None, "runas", full_path, None, None, 1)
            else:
                subprocess.Popen(full_path)
            return jsonify({"message": f"Restarting {app_name}"})
        except Exception as e:
            return jsonify({"error": f"Failed to start: {str(e)}"}), 500
    return jsonify({"error": "Path not found"}), 404

class MonitorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Exhibit Monitor Agent")
        self.root.geometry("600x850")
        self.root.configure(bg="#0f172a")
        
        self.root.protocol('WM_DELETE_WINDOW', self.minimize_to_tray)
        self.icon = None
        if HAS_TRAY:
            self.app_icon_img = self.create_icon_image(32, 32)
            self.tk_icon = ImageTk.PhotoImage(self.app_icon_img)
            self.root.iconphoto(False, self.tk_icon)
            self.create_tray_icon()

        self.last_midi_list = []
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.style.configure("TCombobox", fieldbackground="#1e293b", background="#334155", foreground="white", arrowcolor="white")
        
        header = tk.Frame(root, bg="#1e293b", height=80)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        title_label = tk.Label(header, text="AGENT SETTINGS", bg="#1e293b", fg="#3b82f6", font=("Segoe UI", 16, "bold"))
        title_label.pack(side=tk.LEFT, padx=30)
        self.node_label = tk.Label(header, text=platform.node(), bg="#1e293b", fg="#94a3b8", font=("Consolas", 10))
        self.node_label.pack(side=tk.RIGHT, padx=30)

        main_scroll = tk.Frame(root, bg="#0f172a")
        main_scroll.pack(fill=tk.BOTH, expand=True, padx=30, pady=20)

        self.create_section(main_scroll, "SYSTEM IDENTITY")
        sys_inner = tk.Frame(main_scroll, bg="#1e293b", padx=15, pady=15)
        sys_inner.pack(fill=tk.X, pady=(0, 20))
        self.name_entry = self.create_input(sys_inner, "Display Name:", config_data["display_name"], 0)
        self.loc_entry = self.create_input(sys_inner, "Location:", config_data["location"], 1)

        self.create_section(main_scroll, "HARDWARE ROUTING")
        hw_inner = tk.Frame(main_scroll, bg="#1e293b", padx=15, pady=15)
        hw_inner.pack(fill=tk.X, pady=(0, 20))

        tk.Label(hw_inner, text="MIDI Output:", bg="#1e293b", fg="#94a3b8", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        self.midi_cb = ttk.Combobox(hw_inner, state="readonly", width=35)
        self.midi_cb.grid(row=0, column=1, sticky="ew", padx=10, pady=5)
        
        self.update_midi_devices()
        self.serial_entry = self.create_input(hw_inner, "Serial Port:", config_data["serial_port"], 1)
        
        tk.Button(main_scroll, text="SAVE SYSTEM SETTINGS", command=self.save_all_config, 
                  bg="#3b82f6", fg="white", font=("Segoe UI", 10, "bold"), borderwidth=0, pady=8).pack(fill=tk.X, pady=(0, 30))

        cols = tk.Frame(main_scroll, bg="#0f172a")
        cols.pack(fill=tk.BOTH, expand=True)

        left_col = tk.Frame(cols, bg="#0f172a")
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        self.create_section(left_col, "APP WATCHER")
        self.app_list = tk.Listbox(left_col, bg="#1e293b", fg="white", borderwidth=0, highlightthickness=0, font=("Segoe UI", 9))
        self.app_list.pack(fill=tk.BOTH, expand=True)
        app_btns = tk.Frame(left_col, bg="#0f172a")
        app_btns.pack(fill=tk.X, pady=10)
        tk.Button(app_btns, text="+ Add", command=self.add_app, bg="#1e293b", fg="#60a5fa", borderwidth=0, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        tk.Button(app_btns, text="- Remove", command=self.remove_app, bg="#1e293b", fg="#f87171", borderwidth=0, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        right_col = tk.Frame(cols, bg="#0f172a")
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))
        self.create_section(right_col, "REMOTE PRESETS")
        self.preset_list = tk.Listbox(right_col, bg="#1e293b", fg="white", borderwidth=0, highlightthickness=0, font=("Segoe UI", 9))
        self.preset_list.pack(fill=tk.BOTH, expand=True)
        pre_btns = tk.Frame(right_col, bg="#0f172a")
        pre_btns.pack(fill=tk.X, pady=10)
        tk.Button(pre_btns, text="+ MIDI", command=lambda: self.add_preset_dialog('midi'), bg="#1e293b", fg="#8b5cf6", borderwidth=0, font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=1)
        tk.Button(pre_btns, text="+ OSC", command=lambda: self.add_preset_dialog('osc'), bg="#1e293b", fg="#a78bfa", borderwidth=0, font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=1)
        tk.Button(pre_btns, text="+ Ser", command=lambda: self.add_preset_dialog('serial'), bg="#1e293b", fg="#10b981", borderwidth=0, font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=1)
        tk.Button(pre_btns, text="Del", command=self.delete_preset, bg="#1e293b", fg="#94a3b8", borderwidth=0, font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=1)

        footer = tk.Frame(root, bg="#0f172a")
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        hw_status = f"MIDI: {'●' if HAS_MIDI else '○'}  OSC: {'●' if HAS_OSC else '○'}  SER: {'●' if HAS_SERIAL else '○'}"
        self.status_label = tk.Label(footer, text=f"API ACTIVE: PORT 5001  |  {hw_status}", bg="#0f172a", fg="#4ade80", font=("Consolas", 8), pady=10)
        self.status_label.pack()

        self.refresh_list()
        self.refresh_presets()

    def create_icon_image(self, width, height):
        image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        dc = ImageDraw.Draw(image)
        s = width / 64
        dc.ellipse((4*s, 4*s, 60*s, 60*s), fill=(30, 41, 59), outline=(59, 130, 246), width=max(1, int(4*s)))
        dc.ellipse((20*s, 20*s, 44*s, 44*s), fill=(59, 130, 246))
        dc.line([(16*s, 32*s), (24*s, 32*s), (28*s, 16*s), (36*s, 48*s), (40*s, 32*s), (48*s, 32*s)], fill=(255, 255, 255), width=max(1, int(3*s)))
        return image

    def create_tray_icon(self):
        image = self.create_icon_image(64, 64)
        menu = pystray.Menu(
            pystray.MenuItem("Restore Window", self.show_window),
            pystray.MenuItem("Exit Agent", self.quit_all)
        )
        self.icon = pystray.Icon("exhibit_monitor", image, "Exhibit Monitor Agent", menu)
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
        if not HAS_MIDI: return
        try:
            pygame.midi.quit()
            pygame.midi.init()
            midi_devices = ["None / Default"]
            for i in range(pygame.midi.get_count()):
                info = pygame.midi.get_device_info(i)
                if info[3] == 1:
                    name = info[1].decode('utf-8', errors='ignore')
                    midi_devices.append(f"{i}: {name}")
            if midi_devices != self.last_midi_list:
                cur = self.midi_cb.get()
                self.midi_cb['values'] = midi_devices
                self.midi_cb.set(cur if cur in midi_devices else midi_devices[0])
                self.last_midi_list = midi_devices
        except Exception: pass
        self.root.after(3000, self.update_midi_devices)

    def create_section(self, parent, title):
        tk.Label(parent, text=title, bg="#0f172a", fg="#475569", font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(5, 5))

    def create_input(self, parent, label, value, row):
        tk.Label(parent, text=label, bg="#1e293b", fg="#94a3b8", font=("Segoe UI", 9, "bold")).grid(row=row, column=0, sticky="w", pady=5)
        e = tk.Entry(parent, bg="#0f172a", fg="white", borderwidth=0, highlightthickness=1, highlightbackground="#334155", highlightcolor="#3b82f6", insertbackground="white", font=("Segoe UI", 10))
        e.insert(0, value)
        e.grid(row=row, column=1, sticky="ew", padx=10, pady=5)
        parent.columnconfigure(1, weight=1)
        return e

    def save_all_config(self):
        config_data["display_name"] = self.name_entry.get()
        config_data["location"] = self.loc_entry.get()
        config_data["serial_port"] = self.serial_entry.get()
        midi_val = self.midi_cb.get()
        config_data["midi_device_index"] = int(midi_val.split(":")[0]) if ":" in midi_val else -1
        save_config(config_data)
        self.refresh_list()
        messagebox.showinfo("Config Saved", "Settings updated successfully.")

    def refresh_list(self):
        self.app_list.delete(0, tk.END)
        for p in config_data["watched_apps"]:
            is_admin = check_requires_admin(p)
            tag = " [ADMIN]" if is_admin else ""
            self.app_list.insert(tk.END, f" {os.path.basename(p)}{tag}")

    def refresh_presets(self):
        self.preset_list.delete(0, tk.END)
        for p in config_data["presets"]: self.preset_list.insert(tk.END, f" [{p['type'][:3].upper()}] {p['name']}")

    def add_app(self):
        f = filedialog.askopenfilename(title="Select Application Executable", filetypes=[("Executables", "*.exe")])
        if f and f not in config_data["watched_apps"]:
            config_data["watched_apps"].append(f); save_config(config_data); self.refresh_list()

    def remove_app(self):
        s = self.app_list.curselection()
        if s: config_data["watched_apps"].pop(s[0]); save_config(config_data); self.refresh_list()

    def delete_preset(self):
        s = self.preset_list.curselection()
        if s: config_data["presets"].pop(s[0]); save_config(config_data); self.refresh_presets()

    def add_preset_dialog(self, p_type):
        dialog = tk.Toplevel(self.root); dialog.title(f"New {p_type.upper()} Preset"); dialog.geometry("350x400"); dialog.configure(bg="#1e293b")
        tk.Label(dialog, text=f"CREATE {p_type.upper()} PRESET", bg="#1e293b", fg="#3b82f6", font=("Segoe UI", 10, "bold")).pack(pady=20)
        tk.Label(dialog, text="Preset Name:", bg="#1e293b", fg="#94a3b8").pack()
        name_e = tk.Entry(dialog, bg="#0f172a", fg="white", borderwidth=0); name_e.pack(pady=5, padx=20, fill=tk.X)

        if p_type == 'midi':
            tk.Label(dialog, text="Note (0-127):", bg="#1e293b", fg="#94a3b8").pack()
            note_e = tk.Entry(dialog, bg="#0f172a", fg="white", borderwidth=0); note_e.insert(0, "60"); note_e.pack(pady=2)
            tk.Label(dialog, text="Velocity:", bg="#1e293b", fg="#94a3b8").pack()
            vel_e = tk.Entry(dialog, bg="#0f172a", fg="white", borderwidth=0); vel_e.insert(0, "127"); vel_e.pack(pady=2)
        elif p_type == 'osc':
            tk.Label(dialog, text="OSC Path:", bg="#1e293b", fg="#94a3b8").pack()
            path_e = tk.Entry(dialog, bg="#0f172a", fg="white", borderwidth=0); path_e.insert(0, "/trigger"); path_e.pack(pady=5, padx=20, fill=tk.X)
            tk.Label(dialog, text="Port / Value:", bg="#1e293b", fg="#94a3b8").pack()
            f = tk.Frame(dialog, bg="#1e293b"); f.pack()
            port_e = tk.Entry(f, width=10, bg="#0f172a", fg="white", borderwidth=0); port_e.insert(0, "8000"); port_e.pack(side=tk.LEFT, padx=2)
            val_e = tk.Entry(f, width=10, bg="#0f172a", fg="white", borderwidth=0); val_e.insert(0, "1.0"); val_e.pack(side=tk.LEFT, padx=2)
        else:
            tk.Label(dialog, text="Message String:", bg="#1e293b", fg="#94a3b8").pack()
            msg_e = tk.Entry(dialog, bg="#0f172a", fg="white", borderwidth=0); msg_e.pack(pady=5, padx=20, fill=tk.X)

        def save():
            new_p = {"name": name_e.get(), "type": p_type}
            if p_type == 'midi': new_p.update({"note": note_e.get(), "velocity": vel_e.get(), "channel": 1})
            elif p_type == 'osc': new_p.update({"path": path_e.get(), "port": port_e.get(), "value": val_e.get()})
            else: new_p.update({"message": msg_e.get()})
            config_data["presets"].append(new_p); save_config(config_data); self.refresh_presets(); dialog.destroy()

        tk.Button(dialog, text="SAVE PRESET", command=save, bg="#10b981", fg="white", font=("Segoe UI", 9, "bold"), borderwidth=0, pady=8).pack(pady=20, padx=20, fill=tk.X)

def run_flask(): 
    app.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    root = tk.Tk()
    gui = MonitorGUI(root)
    root.mainloop()