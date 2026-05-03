import socket
import threading
import json
import base64
import queue
from io import BytesIO
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, messagebox

class AUVClient:
    def __init__(self):
        self.socket = None
        self.running = False
        self.recv_thread = None
        self.recv_queue = queue.Queue()

    def connect(self, ip, port):
        """Establish connection to the server."""
        if self.socket:
            self.disconnect()
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((ip, port))
            self.running = True
            self.recv_thread = threading.Thread(target=self._receive_data, daemon=True)
            self.recv_thread.start()
            return True
        except Exception as e:
            self.socket = None
            messagebox.showerror("Connection error", str(e))
            return False

    def disconnect(self):
        """Close the connection and stop receiver thread."""
        self.running = False
        if self.socket:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except:
                pass
            self.socket.close()
            self.socket = None
        if self.recv_thread and self.recv_thread.is_alive():
            self.recv_thread.join(timeout=0.5)
        # Clear queue
        while not self.recv_queue.empty():
            try:
                self.recv_queue.get_nowait()
            except queue.Empty:
                break

    def send_command(self, cmd_dict):
        """Send a JSON command to the server."""
        if self.socket is None:
            return False
        try:
            msg = json.dumps(cmd_dict) + "\n"
            self.socket.send(msg.encode())
            return True
        except Exception as e:
            print(f"Error sending command: {e}")
            self.disconnect()
            return False

    def _receive_data(self):
        """Background thread: receive data and put into queue."""
        buffer = ""
        while self.running and self.socket:
            try:
                data = self.socket.recv(4096).decode()
                if not data:
                    break
                buffer += data
                lines = buffer.split('\n')
                buffer = lines[-1]
                for line in lines[:-1]:
                    if line.strip():
                        try:
                            msg = json.loads(line)
                            self.recv_queue.put(msg)
                        except json.JSONDecodeError:
                            print("Invalid JSON received")
            except (ConnectionResetError, BrokenPipeError, OSError):
                print("Connection lost")
                break
            except Exception as e:
                print(f"Receive error: {e}")
                break
        self.running = False
        if self.socket:
            self.socket.close()
            self.socket = None

    def get_message(self, block=False, timeout=None):
        """Get a message from the queue (for GUI polling)."""
        try:
            return self.recv_queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

class AUVControlGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("AUV Remote Control")
        self.root.geometry("1200x800")
        self.client = AUVClient()
        self.connected = False

        # Keyboard control state
        self.keyboard_enabled = False
        self.active_keys = set()
        # Mapping from key symbol to motor powers (list of 5 ints)
        self.key_motor_map = {
            'w': [50, 50, 0, 0, 0],    # forward
            'a': [0, 0, 0, 0, 50],     # left
            'z': [0, 0, -50, -50, 0],  # down
            'd': [0, 0, 0, 0, -50],    # right
            'q': [50, -50, 0, 0, 0],   # rotate left
            'e': [-50, 50, 0, 0, 0],    # rotate right
            'x': [0, 0, 50, 50, 0],  # up
            's': [-50, -50, 0, 0, 0]    # forward
        }

        # Bind keyboard events
        self.root.bind('<KeyPress>', self.on_key_press)
        self.root.bind('<KeyRelease>', self.on_key_release)

        # Connection frame
        conn_frame = ttk.LabelFrame(root, text="Connection")
        conn_frame.pack(padx=10, pady=5, fill="x")

        ttk.Label(conn_frame, text="Server IP:").grid(row=0, column=0, padx=5, pady=5)
        self.ip_entry = ttk.Entry(conn_frame, width=15)
        self.ip_entry.grid(row=0, column=1, padx=5, pady=5)
        self.ip_entry.insert(0, "127.0.0.1")

        ttk.Label(conn_frame, text="Port:").grid(row=0, column=2, padx=5, pady=5)
        self.port_entry = ttk.Entry(conn_frame, width=6)
        self.port_entry.grid(row=0, column=3, padx=5, pady=5)
        self.port_entry.insert(0, "5000")

        self.connect_btn = ttk.Button(conn_frame, text="Connect", command=self.do_connect)
        self.connect_btn.grid(row=0, column=4, padx=5, pady=5)

        self.disconnect_btn = ttk.Button(conn_frame, text="Disconnect", command=self.do_disconnect, state="disabled")
        self.disconnect_btn.grid(row=0, column=5, padx=5, pady=5)

        # Video frames
        video_frame = ttk.Frame(root)
        video_frame.pack(padx=10, pady=5, fill="both", expand=True)

        front_frame = ttk.LabelFrame(video_frame, text="Front Camera")
        front_frame.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        self.front_label = ttk.Label(front_frame)
        self.front_label.pack()

        bottom_frame = ttk.LabelFrame(video_frame, text="Bottom Camera")
        bottom_frame.grid(row=0, column=1, padx=5, pady=5, sticky="nsew")
        self.bottom_label = ttk.Label(bottom_frame)
        self.bottom_label.pack()

        video_frame.columnconfigure(0, weight=1)
        video_frame.columnconfigure(1, weight=1)
        video_frame.rowconfigure(0, weight=1)

        # Telemetry frame
        telemetry_frame = ttk.LabelFrame(root, text="Telemetry")
        telemetry_frame.pack(padx=10, pady=5, fill="x")

        self.depth_var = tk.StringVar(value="Depth: -- m")
        self.yaw_var = tk.StringVar(value="Yaw: -- deg")
        self.pitch_var = tk.StringVar(value="Pitch: -- deg")
        self.roll_var = tk.StringVar(value="Roll: -- deg")

        ttk.Label(telemetry_frame, textvariable=self.depth_var).grid(row=0, column=0, padx=5, pady=2, sticky="w")
        ttk.Label(telemetry_frame, textvariable=self.yaw_var).grid(row=0, column=1, padx=5, pady=2, sticky="w")
        ttk.Label(telemetry_frame, textvariable=self.pitch_var).grid(row=1, column=0, padx=5, pady=2, sticky="w")
        ttk.Label(telemetry_frame, textvariable=self.roll_var).grid(row=1, column=1, padx=5, pady=2, sticky="w")

        # Control frame
        control_frame = ttk.LabelFrame(root, text="Manual Control")
        control_frame.pack(padx=10, pady=5, fill="x")

        # Movement mapping (motor powers)
        self.move_powers = {
            "forward":   [50, 50, 0, 0, 0],
            "backward":  [-50, -50, 0, 0, 0],
            "up":        [0, 0, 50, 50, 0],
            "down":      [0, 0, -50, -50, 0],
            "left":      [0, 0, 0, 0, 50],
            "right":     [0, 0, 0, 0, -50],
            "rotate_left":  [50, -50, 0, 0, 0],
            "rotate_right": [-50, 50, 0, 0, 0]
        }

        # Create buttons with press/release events
        self.buttons = {}
        for move in self.move_powers:
            btn = ttk.Button(control_frame, text=move.capitalize())
            btn.bind("<ButtonPress-1>", lambda e, m=move: self.start_move(m))
            btn.bind("<ButtonRelease-1>", lambda e, m=move: self.stop_move())
            self.buttons[move] = btn

        # Place buttons in a grid
        positions = {
            "forward": (0, 1), "backward": (0, 2),
            "up": (1, 1), "down": (1, 2),
            "left": (2, 0), "right": (2, 2),
            "rotate_left": (3, 0), "rotate_right": (3, 2)
        }
        for move, (row, col) in positions.items():
            self.buttons[move].grid(row=row, column=col, padx=5, pady=2)

        # Emergency stop button
        self.stop_btn = ttk.Button(control_frame, text="EMERGENCY STOP", command=self.emergency_stop)
        self.stop_btn.grid(row=4, column=1, padx=5, pady=10)

        # Mode selection
        mode_frame = ttk.LabelFrame(root, text="Mode")
        mode_frame.pack(padx=10, pady=5, fill="x")

        self.mode_var = tk.StringVar(value="manual")
        ttk.Radiobutton(mode_frame, text="Manual", variable=self.mode_var, value="manual", command=self.change_mode).grid(row=0, column=0, padx=5)
        ttk.Radiobutton(mode_frame, text="Auto",   variable=self.mode_var, value="auto",   command=self.change_mode).grid(row=0, column=1, padx=5)

        # Initially disable controls
        self.set_controls_enabled(False)

        # Start periodic GUI update
        self.update_gui()

    def set_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for btn in self.buttons.values():
            btn.config(state=state)
        self.stop_btn.config(state=state)
        for widget in self.root.winfo_children():
            if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "Mode":
                for rb in widget.winfo_children():
                    if isinstance(rb, ttk.Radiobutton):
                        rb.config(state=state)

    def start_move(self, move):
        if self.connected:
            powers = self.move_powers.get(move)
            if powers:
                self.client.send_command({"type": "command", "motor_powers": powers})

    def stop_move(self):
        if self.connected:
            self.client.send_command({"type": "command", "motor_powers": [0,0,0,0,0]})

    def emergency_stop(self):
        if self.connected:
            self.client.send_command({"type": "command", "motor_powers": [0,0,0,0,0]})
            self.client.send_command({"type": "mode", "mode": "manual"})
            # Also reset UI mode selector to manual
            self.mode_var.set("manual")
            self.change_mode()

    def change_mode(self):
        if self.connected:
            mode = self.mode_var.get()
            self.client.send_command({"type": "mode", "mode": mode})
            if mode == "manual":
                self.enable_keyboard()
            else:
                self.disable_keyboard()

    def enable_keyboard(self):
        self.keyboard_enabled = True
        if self.active_keys:
            self.active_keys.clear()
            self.update_motors_from_keys()

    def disable_keyboard(self):
        self.keyboard_enabled = False
        if self.active_keys:
            self.active_keys.clear()
            self.update_motors_from_keys()

    def update_motors_from_keys(self):
        if not self.connected:
            return
        combined = [0, 0, 0, 0, 0]
        for key in self.active_keys:
            contrib = self.key_motor_map.get(key)
            if contrib:
                for i in range(5):
                    combined[i] += contrib[i]
        for i in range(5):
            combined[i] = max(-100, min(100, combined[i]))
        if any(v != 0 for v in combined):
            self.client.send_command({"type": "command", "motor_powers": combined})
        else:
            # If no keys, stop
            self.client.send_command({"type": "command", "motor_powers": [0,0,0,0,0]})

    def on_key_press(self, event):
        if not (self.keyboard_enabled and self.connected):
            return
        focused = self.root.focus_get()
        if focused and isinstance(focused, (tk.Entry, ttk.Entry)):
            return
        key = event.keysym.lower()
        if key in self.key_motor_map and key not in self.active_keys:
            self.active_keys.add(key)
            self.update_motors_from_keys()
            return "break"

    def on_key_release(self, event):
        if not (self.keyboard_enabled and self.connected):
            return
        focused = self.root.focus_get()
        if focused and isinstance(focused, (tk.Entry, ttk.Entry)):
            return
        key = event.keysym.lower()
        if key in self.key_motor_map and key in self.active_keys:
            self.active_keys.discard(key)
            self.update_motors_from_keys()
            return "break"

    def do_connect(self):
        ip = self.ip_entry.get().strip()
        try:
            port = int(self.port_entry.get().strip())
        except ValueError:
            messagebox.showerror("Invalid port", "Port must be a number")
            return
        if self.client.connect(ip, port):
            self.connected = True
            self.connect_btn.config(state="disabled")
            self.disconnect_btn.config(state="normal")
            self.set_controls_enabled(True)
            if self.mode_var.get() == "manual":
                self.enable_keyboard()
        else:
            self.connected = False

    def do_disconnect(self):
        self.client.disconnect()
        self.connected = False
        self.connect_btn.config(state="normal")
        self.disconnect_btn.config(state="disabled")
        self.set_controls_enabled(False)
        self.disable_keyboard()
        self.front_label.config(image='')
        self.bottom_label.config(image='')
        # Reset telemetry display
        self.depth_var.set("Depth: -- m")
        self.yaw_var.set("Yaw: -- deg")
        self.pitch_var.set("Pitch: -- deg")
        self.roll_var.set("Roll: -- deg")

    def update_gui(self):
        # Process all pending messages
        while True:
            msg = self.client.get_message()
            if msg is None:
                break
            if msg["type"] == "telemetry":
                depth = msg.get("depth", 0)
                yaw = msg.get("yaw", 0)
                pitch = msg.get("pitch", 0)
                roll = msg.get("roll", 0)
                self.depth_var.set(f"Depth: {depth:.1f} m")
                self.yaw_var.set(f"Yaw: {yaw:.1f} deg")
                self.pitch_var.set(f"Pitch: {pitch:.1f} deg")
                self.roll_var.set(f"Roll: {roll:.1f} deg")
            elif msg["type"] == "frame":
                camera = msg.get("camera")
                b64_data = msg.get("data")
                if b64_data:
                    img_data = base64.b64decode(b64_data)
                    img = Image.open(BytesIO(img_data))
                    img.thumbnail((640, 480))
                    imgtk = ImageTk.PhotoImage(img)
                    if camera == "front":
                        self.front_label.config(image=imgtk)
                        self.front_label.image = imgtk
                    elif camera == "bottom":
                        self.bottom_label.config(image=imgtk)
                        self.bottom_label.image = imgtk
        self.root.after(50, self.update_gui)

def main():
    root = tk.Tk()
    app = AUVControlGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
