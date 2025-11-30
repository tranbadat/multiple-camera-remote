import socket
import threading
import os
import datetime
import base64
import struct
import json
import time

# Load .env file (optional) into environment if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --- Cấu hình Server (từ Environment Variables) ---
HOST_IP = os.getenv('HOST_IP', '0.0.0.0')
CONTROL_PORT = int(os.getenv('CONTROL_PORT', '5000'))     # UDP control port
AUTH_TOKEN = os.getenv('AUTH_TOKEN', '123456')

DATA_DIR = os.getenv('DATA_DIR', 'data')

# Số frame tối đa (0 = không giới hạn)
MAX_FRAMES = int(os.getenv('MAX_FRAMES', '50'))

# --- Dynamic devices (nhiều mobile/cam) ---
BASE_CAM_PORT = int(os.getenv('BASE_CAM_PORT', '6001'))   # cổng TCP bắt đầu cho device mới
DEVICES_FILE = os.path.join(DATA_DIR, 'devices.json')

# { deviceId: { "deviceId": ..., "name": ..., "port": ..., "subdir": ... } }
devices = {}
devices_lock = threading.Lock()
next_dynamic_port = BASE_CAM_PORT

# Lưu danh sách client UDP: { 'deviceId': ('ip', port) }
control_clients = {}

# Socket UDP điều khiển toàn cục
control_udp_socket = None

# Bộ đếm frame theo camera
frame_counters = {
}
frame_counters_lock = threading.Lock()

# Sự kiện để dừng từng server camera
stop_events = {
}


def log(tag, message):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}][{tag}] {message}")


def handle_tcp_client(conn, addr, cam_dir):
    """
    Xử lý kết nối dữ liệu hình ảnh qua TCP.
    Giao thức: 4 bytes (length) + N bytes (Base64 image).
    """
    cam_name = os.path.basename(cam_dir).lower()
    if cam_name not in frame_counters:
        # Nếu là dynamic subdir thì thêm vào
        with frame_counters_lock:
            frame_counters.setdefault(cam_name, 0)

    log("TCP", f"Kết nối MỚI từ {addr} -> Lưu vào: {cam_dir} (camera: {cam_name})")

    try:
        while True:
            # Nếu server cho camera này đã được dừng theo giới hạn frame
            event = stop_events.get(cam_name)
            if event is not None and event.is_set():
                log("TCP", f"Giới hạn frame đã đạt cho {cam_name}. Đóng kết nối {addr}.")
                break

            length_data = conn.recv(4)
            if not length_data:
                log("TCP", f"Client {addr} đã đóng kết nối (Không nhận được header).")
                break

            frame_length = struct.unpack('>I', length_data)[0]
            log("TCP", f"Chuẩn bị nhận frame kích thước: {frame_length} bytes")

            data = b""
            while len(data) < frame_length:
                packet = conn.recv(frame_length - len(data))
                if not packet:
                    break
                data += packet

            if len(data) != frame_length:
                log("TCP", f"LỖI: Dữ liệu không đủ. Cần {frame_length}, nhận được {len(data)}. Bỏ qua frame này.")
                continue

            try:
                image_data = data

                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                filepath = os.path.join(cam_dir, f"frame_{timestamp}.jpg")
                with open(filepath, 'wb') as f:
                    f.write(image_data)

                with frame_counters_lock:
                    frame_counters.setdefault(cam_name, 0)
                    frame_counters[cam_name] += 1
                    current_count = frame_counters[cam_name]

                log("TCP", f"{cam_name} count={current_count}")

                if MAX_FRAMES > 0 and current_count >= MAX_FRAMES:
                    log("TCP", f"ĐẠT GIỚI HẠN: {current_count} >= {MAX_FRAMES} cho {cam_name}. Gửi SYNC_STOP và dừng camera.")
                    if cam_name not in stop_events:
                        stop_events[cam_name] = threading.Event()
                    stop_events[cam_name].set()
                    try:
                        broadcast_command(control_udp_socket, b"SYNC_STOP")
                        log("UDP", "Đã phát SYNC_STOP do đạt giới hạn frame.")
                    except Exception as e:
                        log("UDP", f"Lỗi khi gửi SYNC_STOP: {e}")
                    break
            except Exception as e:
                log("TCP", f"Lỗi khi giải mã/lưu ảnh: {e}")

    except ConnectionResetError:
        log("TCP", f"Client {addr} ngắt kết nối đột ngột (ConnectionReset).")
    except Exception as e:
        log("TCP", f"Lỗi không xác định với {addr}: {e}")
    finally:
        conn.close()
        log("TCP", f"Đã đóng socket với {addr}")


def broadcast_command(sock, message_bytes):
    """Gửi lệnh tới TẤT CẢ client đã đăng ký"""
    internal_sock = None
    if sock is None:
        internal_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock_to_use = internal_sock
    else:
        sock_to_use = sock

    for d_id in list(control_clients.keys()):
        d_addr = control_clients[d_id]
        log("UDP", f"Gửi lệnh tới {d_id} tại {d_addr}")
        try:
            sock_to_use.sendto(message_bytes, d_addr)
        except OSError as e:
            if getattr(e, 'winerror', 0) == 10054:
                log("UDP", f"Phát hiện client CHẾT: {d_id}. Đang xóa khỏi danh sách.")
                del control_clients[d_id]
            else:
                log("UDP", f"Lỗi gửi tới {d_id}: {e}")

    if internal_sock is not None:
        internal_sock.close()


# ---------- Dynamic device quản lý ----------

    """Load devices từ file, đồng thời khởi động TCP server cho mỗi device."""
def load_devices():
    global devices, next_dynamic_port

    if not os.path.exists(DEVICES_FILE):
        return

    try:
        with open(DEVICES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            with devices_lock:
                for item in data:
                    did = item.get('deviceId')
                    if not did:
                        continue
                    devices[did] = item

                if devices:
                    max_port = max(d.get('port', BASE_CAM_PORT - 1) for d in devices.values())
                    next_dynamic_port = max(max_port + 1, BASE_CAM_PORT)

            # Start TCP server cho từng device đã tồn tại
            for item in data:
                did = item.get('deviceId')
                port = item.get('port')
                subdir = item.get('subdir')
                if not did or not port or not subdir:
                    continue
                cam_dir = os.path.join(DATA_DIR, subdir)
                if not os.path.exists(cam_dir):
                    os.makedirs(cam_dir, exist_ok=True)

                cam_name = subdir.lower()
                with frame_counters_lock:
                    frame_counters.setdefault(cam_name, 0)
                if cam_name not in stop_events:
                    stop_events[cam_name] = threading.Event()

                threading.Thread(target=start_tcp_server, args=(port, cam_dir), daemon=True).start()

            log("STATE", f"Đã load {len(devices)} devices từ {DEVICES_FILE}")
    except Exception as e:
        log("STATE", f"Không đọc được devices.json: {e}")


def save_devices():
    """Ghi danh sách devices ra file JSON."""
    try:
        with devices_lock:
            data = list(devices.values())
        with open(DEVICES_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log("STATE", f"Đã lưu {len(data)} devices vào {DEVICES_FILE}")
    except Exception as e:
        log("STATE", f"Lỗi khi lưu devices.json: {e}")


def sanitize_subdir(name: str) -> str:
    s = "".join(c for c in name if c.isalnum() or c in ('_', '-')).strip()
    return s or "cam"


def register_device(device_id: str, name: str, address) -> dict:
    """
    Tạo mới (hoặc lấy lại) device:
      - Cấp port TCP mới
      - Tạo folder subdir trong DATA_DIR
      - Start TCP server nhận ảnh
      - Lưu vào devices + devices.json
    """
    global next_dynamic_port

    with devices_lock:
        if device_id in devices:
            info = devices[device_id]
            log("STATE", f"Device đã tồn tại: {info}")
            return info

        port = next_dynamic_port
        next_dynamic_port += 1

        subdir = sanitize_subdir(name or device_id)
        cam_dir = os.path.join(DATA_DIR, subdir)
        if not os.path.exists(cam_dir):
            os.makedirs(cam_dir, exist_ok=True)

        info = {
            "deviceId": device_id,
            "name": name,
            "port": port,
            "subdir": subdir,
            "address": address
        }
        devices[device_id] = info

        cam_name = subdir.lower()
        with frame_counters_lock:
            frame_counters.setdefault(cam_name, 0)
        if cam_name not in stop_events:
            stop_events[cam_name] = threading.Event()

    # Start TCP server ngoài lock
    threading.Thread(target=start_tcp_server, args=(port, cam_dir), daemon=True).start()
    save_devices()
    log("STATE", f"Tạo device mới: {info}")
    return info


# ---------- UDP Điều khiển ----------

def listen_for_control():
    """
    Lắng nghe các gói tin UDP (JSON) để điều khiển START/STOP/CONNECT/REGISTER.
    """
    global control_clients, control_udp_socket
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    control_udp_socket = udp_socket
    udp_socket.bind((HOST_IP, CONTROL_PORT))
    log("UDP", f"Server điều khiển đang lắng nghe tại cổng {CONTROL_PORT}")

    while True:
        try:
            raw_data, addr = udp_socket.recvfrom(8192)

            try:
                decoded_data = raw_data.decode('utf-8')
                message = json.loads(decoded_data)
            except Exception:
                continue  # bỏ qua gói tin rác

            msg_type = message.get('type', '').upper()
            device_id = message.get('deviceId')
            token = message.get('token')

            if token != AUTH_TOKEN:
                log("UDP", f"CẢNH BÁO: Sai Token '{token}' từ {addr} (Device: {device_id})")
                continue

            if not device_id:
                log("UDP", f"CẢNH BÁO: Thiếu deviceId từ {addr}")
                continue

            # Cập nhật danh sách client UDP (địa chỉ để broadcast SYNC_START/STOP)
            if device_id not in control_clients or control_clients[device_id] != addr:
                log("UDP", f"Cập nhật vị trí client: {device_id} -> {addr}")
            control_clients[device_id] = addr

            if msg_type == "REGISTER":
                log("UDP", f"Client {device_id} yêu cầu ĐĂNG KÝ.")
                udp_socket.sendto("ACK_REGISTER".encode('utf-8'), addr)

            elif msg_type == "START":
                log("UDP", f"Nhận lệnh START từ {device_id}. Đang kích hoạt toàn bộ...")
                response = "SYNC_START".encode('utf-8')
                broadcast_command(udp_socket, response)
                log("UDP", "Đã gửi lệnh SYNC_START tới tất cả client đã đăng ký.")

            elif msg_type == "STOP":
                log("UDP", f"Nhận lệnh STOP từ {device_id}. Đang dừng toàn bộ...")
                response = "SYNC_STOP".encode('utf-8')
                broadcast_command(udp_socket, response)
                log("UDP", "Đã gửi lệnh SYNC_STOP tới tất cả client đã đăng ký.")

            elif msg_type == "CONNECT":
                # NEW: Tạo device mới + trả về danh sách devices
                name = message.get('name') or device_id
                info = register_device(device_id, name, addr)
                with devices_lock:
                    all_devices = list(devices.values())
                resp_bytes = json.dumps(all_devices, ensure_ascii=False).encode('utf-8')
                udp_socket.sendto(resp_bytes, addr)
                log("UDP", f"CONNECT từ {device_id} -> tạo/giữ {info}, gửi lại {len(all_devices)} device(s).")

            else:
                log("UDP", f"Nhận lệnh lạ '{msg_type}' từ {device_id}")

        except Exception as e:
            if getattr(e, 'winerror', 0) == 10054:
                continue
            log("UDP", f"Lỗi luồng chính: {e}")


def start_tcp_server(port, cam_dir):
    """Khởi tạo socket TCP lắng nghe dữ liệu ảnh."""
    cam_name = os.path.basename(cam_dir).lower()
    tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_socket.bind((HOST_IP, port))
    tcp_socket.listen(5)
    tcp_socket.settimeout(1.0)
    log("INIT", f"Đã mở cổng TCP {port} để nhận ảnh vào: {cam_dir} (camera: {cam_name})")

    try:
        while not stop_events.get(cam_name, threading.Event()).is_set():
            try:
                conn, addr = tcp_socket.accept()
            except socket.timeout:
                continue
            t = threading.Thread(target=handle_tcp_client, args=(conn, addr, cam_dir))
            t.daemon = True
            t.start()
    except Exception as e:
        log("INIT", f"Lỗi server TCP {cam_name}: {e}")
    finally:
        try:
            tcp_socket.close()
        except Exception:
            pass
        log("INIT", f"Server TCP {cam_name} trên cổng {port} đã dừng.")


# ---------- UI Tkinter hiển thị devices ----------

def start_ui():
    """UI đơn giản trên PC để xem danh sách device đang config."""
    try:
        import tkinter as tk
        from tkinter import ttk
        import sys
    except Exception as e:
        log("UI", f"Không thể khởi tạo UI Tkinter: {e}")
        # fallback: giữ process sống bằng vòng lặp
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log("MAIN", "Ctrl+C — thoát.")
        return

    root = tk.Tk()
    root.title("Camera PC Server - Connected Devices")

    cols = ("deviceId", "name", "port", "subdir")
    tree = ttk.Treeview(root, columns=cols, show="headings")
    for c in cols:
        tree.heading(c, text=c)
        tree.column(c, width=150, anchor="w")
    tree.pack(fill="both", expand=True)

    # --- Thanh trạng thái ---
    status_label = tk.Label(root, text=f"UDP control port: {CONTROL_PORT}", anchor="w")
    status_label.pack(fill="x", padx=5)

    # --- Hàng nút điều khiển START/STOP ---
    button_frame = tk.Frame(root)
    button_frame.pack(fill="x", padx=5, pady=5)

    def on_start_all():
        """Gửi SYNC_START tới tất cả thiết bị."""
        global control_udp_socket
        if control_udp_socket is None:
            log("UI", "Không có UDP control socket – chưa có client nào gửi CONNECT/REGISTER?")
            return
        try:
            broadcast_command(control_udp_socket, b"SYNC_START")
            log("UI", "Đã gửi SYNC_START tới tất cả thiết bị.")
        except Exception as e:
            log("UI", f"Lỗi khi gửi SYNC_START: {e}")

    def on_stop_all():
        """Gửi SYNC_STOP tới tất cả thiết bị."""
        global control_udp_socket
        if control_udp_socket is None:
            log("UI", "Không có UDP control socket – chưa có client nào gửi CONNECT/REGISTER?")
            return
        try:
            broadcast_command(control_udp_socket, b"SYNC_STOP")
            log("UI", "Đã gửi SYNC_STOP tới tất cả thiết bị.")
        except Exception as e:
            log("UI", f"Lỗi khi gửi SYNC_STOP: {e}")

    start_btn = tk.Button(
        button_frame,
        text="START ALL",
        command=on_start_all,
        width=12,
        bg="#2e7d32",
        fg="white"
    )
    start_btn.pack(side="left", padx=(0, 5))

    stop_btn = tk.Button(
        button_frame,
        text="STOP ALL",
        command=on_stop_all,
        width=12,
        bg="#c62828",
        fg="white"
    )
    stop_btn.pack(side="left")

    def refresh():
        with devices_lock:
            data = list(devices.values())

        # Clear
        for item in tree.get_children():
            tree.delete(item)

        for d in data:
            tree.insert(
                "",
                "end",
                values=(
                    d.get("deviceId", ""),
                    d.get("name", ""),
                    d.get("port", ""),
                    os.path.join(DATA_DIR, d.get("subdir", "")),
                ),
            )
        root.after(1000, refresh)  # refresh mỗi 1s

    def on_close():
        # 1. Gửi tín hiệu dừng cho các camera
        for ev in stop_events.values():
            ev.set()

        # 2. Lưu lại devices nếu cần
        save_devices()

        # 3. Destroy UI rồi thoát hẳn
        root.destroy()
        sys.exit(0)

    root.protocol("WM_DELETE_WINDOW", on_close)

    refresh()
    root.mainloop()


def main():
    # Tạo thư mục nếu chưa có
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    print("============================================")
    print("   SERVER CAMERA PC")
    print(f"   IP Hiện tại: {socket.gethostbyname(socket.gethostname())}")
    print(f"   Token: {AUTH_TOKEN}")
    print(f"   UDP Control Port: {CONTROL_PORT}")
    print("============================================")

    # Load devices đã lưu (nếu có) & start TCP cho chúng
    load_devices()

    # Chạy luồng UDP (Điều khiển)
    threading.Thread(target=listen_for_control, daemon=True).start()

    # UI sẽ giữ chương trình chạy
    start_ui()


if __name__ == "__main__":
    main()
