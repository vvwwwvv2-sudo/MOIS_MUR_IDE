# server.py (исправленная версия)
import socket
import threading
import json
import base64
import time
import cv2
import pymurapi as mur
import sys

# Global state
auv = None
running = True
mode = "manual"                # "manual" or "auto"
auto_thread = None
auto_running = False

# Auto mode state machine
auto_state = 0
auto_start_time = 0

def set_motors(powers):
    """Apply motor powers to all 5 motors."""
    if len(powers) != 5:
        return
    for i, p in enumerate(powers):
        auv.set_motor_power(i, p)

def send_telemetry(client_sock):
    """Send telemetry data to client. Return True on success, False on error."""
    try:
        depth = auv.get_depth()
        yaw = auv.get_yaw()
        pitch = auv.get_pitch()
        roll = auv.get_roll()
        data = {
            "type": "telemetry",
            "depth": depth,
            "yaw": yaw,
            "pitch": pitch,
            "roll": roll
        }
        msg = json.dumps(data) + "\n"
        client_sock.send(msg.encode())
        return True
    except (BrokenPipeError, ConnectionResetError, OSError) as e:
        print(f"Telemetry send error – connection lost: {e}")
        return False
    except Exception as e:
        print(f"Error sending telemetry: {e}")
        return False

def send_frame(client_sock, camera):
    """Capture and send a frame. Return True on success, False on error."""
    try:
        if camera == "front":
            img = auv.get_image_front()
        elif camera == "bottom":
            img = auv.get_image_bottom()
        else:
            return True
        if img is None:
            return True
        height, width = img.shape[:2]
        if width > 640:
            new_width = 640
            new_height = int(height * (640 / width))
            img = cv2.resize(img, (new_width, new_height))
        _, jpeg = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        b64 = base64.b64encode(jpeg).decode()
        data = {
            "type": "frame",
            "camera": camera,
            "data": b64
        }
        msg = json.dumps(data) + "\n"
        client_sock.send(msg.encode())
        return True
    except (BrokenPipeError, ConnectionResetError, OSError) as e:
        print(f"Frame send error for {camera} – connection lost: {e}")
        return False
    except Exception as e:
        print(f"Error sending frame: {e}")
        return True   # Other errors (e.g. image encoding) don't break connection

def auto_control_loop():
    """Auto mode control loop (runs in separate thread)."""
    global auto_running, auto_state, auto_start_time, mode
    while auto_running and mode == "auto":
        now = time.time()
        if auto_state == 0:
            set_motors([30, 30, 0, 0, 0])
            auto_state = 1
            auto_start_time = now
        elif auto_state == 1:
            if now - auto_start_time >= 3:
                set_motors([0, 0, 0, 0, 0])
                auto_state = 2
                auto_start_time = now
        elif auto_state == 2:
            if now - auto_start_time >= 1:
                set_motors([0, 0, 0, 0, 50])
                auto_state = 3
                auto_start_time = now
        elif auto_state == 3:
            if now - auto_start_time >= 2:
                set_motors([0, 0, 0, 0, 0])
                auto_state = 4
                auto_start_time = now
        elif auto_state == 4:
            if now - auto_start_time >= 1:
                auto_state = 0
        time.sleep(0.1)

def handle_command(data, client_sock):
    """Process a command received from client."""
    global mode, auto_thread, auto_running
    if data["type"] == "command":
        powers = data.get("motor_powers")
        if powers and len(powers) == 5:
            if all(p == 0 for p in powers):
                set_motors(powers)
                if mode == "auto":
                    mode = "manual"
                    auto_running = False
                    if auto_thread and auto_thread.is_alive():
                        auto_thread.join(timeout=0.1)
                print("Emergency stop triggered")
            elif mode == "manual":
                set_motors(powers)
            else:
                print("Ignoring manual command in auto mode")
    elif data["type"] == "mode":
        new_mode = data.get("mode")
        if new_mode in ["manual", "auto"]:
            if new_mode == "auto" and mode == "manual":
                mode = "auto"
                auto_running = True
                auto_thread = threading.Thread(target=auto_control_loop)
                auto_thread.start()
                print("Switched to AUTO mode")
            elif new_mode == "manual" and mode == "auto":
                mode = "manual"
                auto_running = False
                if auto_thread and auto_thread.is_alive():
                    auto_thread.join(timeout=0.1)
                set_motors([0, 0, 0, 0, 0])
                print("Switched to MANUAL mode")

def receive_commands(client_sock):
    """Thread: receive and process commands from client."""
    global running
    while running:
        try:
            data = client_sock.recv(4096).decode()
            if not data:
                break
            lines = data.split('\n')
            for line in lines:
                if line.strip():
                    cmd = json.loads(line)
                    handle_command(cmd, client_sock)
        except (ConnectionResetError, BrokenPipeError, OSError):
            print("Command receive – connection lost")
            break
        except Exception as e:
            print(f"Error receiving command: {e}")
            break

def handle_client(client_sock):
    """Handle a single client connection."""
    global mode, auto_running, auto_thread
    # Reset state for new client
    mode = "manual"
    auto_running = False
    if auto_thread and auto_thread.is_alive():
        auto_thread.join(timeout=0.1)
    set_motors([0, 0, 0, 0, 0])

    # Start command receiver thread
    recv_thread = threading.Thread(target=receive_commands, args=(client_sock,))
    recv_thread.daemon = True
    recv_thread.start()

    # Main loop: send telemetry and video frames
    try:
        while running:
            if not send_telemetry(client_sock):
                break
            if not send_frame(client_sock, "front"):
                break
            if not send_frame(client_sock, "bottom"):
                break
            time.sleep(0.1)      # 10 Hz
    except Exception as e:
        print(f"Client handler error: {e}")
    finally:
        client_sock.close()
        mode = "manual"
        auto_running = False
        if auto_thread and auto_thread.is_alive():
            auto_thread.join(timeout=0.1)
        print("Client disconnected")

def accept_clients(server_sock):
    """Accept clients one at a time."""
    server_sock.listen(1)
    print("Waiting for client connection...")
    while running:
        try:
            client_sock, addr = server_sock.accept()
            print(f"Client connected: {addr}")
            handle_client(client_sock)
        except Exception as e:
            print(f"Accept error: {e}")
            break

def main():
    global running, auv
    try:
        auv = mur.mur_init()
        print("MUR initialized")
    except Exception as e:
        print(f"Failed to initialize MUR: {e}")
        sys.exit(1)

    host = '0.0.0.0'
    port = 5000
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    print(f"Server listening on {host}:{port}")

    try:
        accept_clients(server_sock)
    except KeyboardInterrupt:
        print("Server shutting down...")
    finally:
        running = False
        server_sock.close()

if __name__ == "__main__":
    main()
