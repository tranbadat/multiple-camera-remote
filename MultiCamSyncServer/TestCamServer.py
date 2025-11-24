import socket
import struct
import time
import base64
import threading
import sys
import json

# --- CẤU HÌNH KẾT NỐI ---
SERVER_IP = '127.0.0.1'  # Test trên cùng máy PC (localhost)
CONTROL_PORT = 5000
CAM1_PORT = 5001
CAM2_PORT = 5002

# --- CẤU HÌNH AUTH (Mới) ---
AUTH_TOKEN = "123456"       # Phải khớp với cấu hình trong pc_server_auth.py
DEVICE_ID = "test_tool_pc"  # Định danh giả lập cho tool này

# Một ảnh JPEG 1x1 pixel màu trắng (Base64) để test
DUMMY_IMG_B64 = "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX3N3e4+Tl5ufo6erx8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iiigD//2Q=="

def print_color(msg, color="WHITE"):
    colors = {
        "GREEN": "\033[92m",
        "RED": "\033[91m",
        "YELLOW": "\033[93m",
        "RESET": "\033[0m"
    }
    c = colors.get(color, colors["RESET"])
    print(f"{c}{msg}{colors['RESET']}")

def send_udp_json(sock, msg_type):
    """Hàm trợ giúp đóng gói và gửi JSON qua UDP"""
    payload = {
        "type": msg_type,
        "deviceId": DEVICE_ID,
        "token": AUTH_TOKEN
    }
    # Chuyển Dictionary thành chuỗi JSON bytes
    json_data = json.dumps(payload).encode('utf-8')
    
    print(f" -> Đang gửi JSON: {payload} tới Port {CONTROL_PORT}")
    sock.sendto(json_data, (SERVER_IP, CONTROL_PORT))

def test_udp_control():
    """Kiểm tra luồng điều khiển UDP (Đồng bộ với Auth JSON)"""
    print_color("\n--- [TEST 1] UDP CONTROL SYNC (JSON AUTH) ---", "YELLOW")
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3.0) # Chờ tối đa 3 giây
    
    try:
        # --- Bước 1: Gửi lệnh REGISTER ---
        print_color("Bước 1: Giả lập đăng ký thiết bị...", "WHITE")
        send_udp_json(sock, "REGISTER")
        
        # Chờ ACK
        try:
            data, addr = sock.recvfrom(1024)
            resp = data.decode('utf-8')
            if resp == "ACK_REGISTER":
                print_color(f"✓ Đã nhận phản hồi: {resp}", "GREEN")
            else:
                print_color(f"Warning: Nhận phản hồi lạ: {resp}", "YELLOW")
        except socket.timeout:
             print_color("Warning: Không thấy ACK (Server có thể không gửi lại cho lệnh REGISTER hoặc bị lag)", "YELLOW")

        # --- Bước 2: Gửi lệnh START ---
        print_color("\nBước 2: Gửi lệnh START...", "WHITE")
        send_udp_json(sock, "START")
        
        # --- Bước 3: Chờ phản hồi SYNC ---
        print("Đang chờ tín hiệu đồng bộ từ Server...")
        start_time = time.time()
        
        while (time.time() - start_time) < 3.0:
            try:
                data, addr = sock.recvfrom(1024)
                response = data.decode('utf-8')
                
                # Bỏ qua nếu là ACK cũ còn sót lại
                if response == "ACK_REGISTER":
                    continue

                if response == "SYNC_START":
                    print_color(f"✓ THÀNH CÔNG: Nhận được tín hiệu kích hoạt '{response}' từ Server!", "GREEN")
                    return
                else:
                    print_color(f"✗ THẤT BẠI: Phản hồi không đúng: '{response}'", "RED")
                    return
            except socket.timeout:
                break
        
        print_color("✗ THẤT BẠI: Timeout - Không nhận được SYNC_START. (Kiểm tra Token hoặc xem Server đã chạy chưa)", "RED")
            
    except Exception as e:
        print_color(f"✗ LỖI: {e}", "RED")
    finally:
        sock.close()

def send_tcp_frame(port, cam_name):
    """Gửi 1 frame giả lập tới cổng TCP (Không đổi vì TCP ko cần Auth ở mức socket)"""
    print(f"Đang kết nối tới {cam_name} ({SERVER_IP}:{port})...")
    
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect((SERVER_IP, port))
        
        # Chuẩn bị dữ liệu
        img_bytes = DUMMY_IMG_B64.encode('utf-8')
        frame_length = len(img_bytes)
        header = struct.pack('>I', frame_length)
        
        print(f"Đang gửi frame dài {frame_length} bytes...")
        client.sendall(header)
        client.sendall(img_bytes)
        
        print_color(f"✓ Đã gửi dữ liệu tới {cam_name}", "GREEN")
        client.close()
        return True
    except ConnectionRefusedError:
        print_color(f"✗ LỖI: Không thể kết nối tới {cam_name} (Cổng {port} chưa mở?)", "RED")
        return False
    except Exception as e:
        print_color(f"✗ LỖI: {e}", "RED")
        return False

def test_tcp_data():
    """Kiểm tra luồng dữ liệu TCP (Gửi ảnh)"""
    print_color("\n--- [TEST 2] TCP DATA TRANSMISSION ---", "YELLOW")
    
    # Gửi thử cho Cam 1
    t1 = threading.Thread(target=send_tcp_frame, args=(CAM1_PORT, "CAM 1"))
    # Gửi thử cho Cam 2
    t2 = threading.Thread(target=send_tcp_frame, args=(CAM2_PORT, "CAM 2"))
    
    t1.start()
    t2.start()
    
    t1.join()
    t2.join()
    
    print_color("\nKiểm tra thư mục 'data/cam1' và 'data/cam2' trên Server để xem ảnh có được tạo không.", "YELLOW")

def main():
    print("=========================================")
    print("   TOOL TEST CLIENT GIẢ LẬP (AUTH JSON)")
    print(f"   Token: {AUTH_TOKEN} | DeviceID: {DEVICE_ID}")
    print("=========================================")
    while True:
        print("\nChọn chức năng test:")
        print("1. Test UDP (Register -> Start -> Sync Check)")
        print("2. Test TCP (Gửi ảnh giả lập tới Cam1 & Cam2)")
        print("3. Chạy cả hai")
        print("0. Thoát")
        
        choice = input("Nhập lựa chọn: ")
        
        if choice == '1':
            test_udp_control()
        elif choice == '2':
            test_tcp_data()
        elif choice == '3':
            test_udp_control()
            time.sleep(1)
            test_tcp_data()
        elif choice == '0':
            break
        else:
            print("Lựa chọn không hợp lệ.")

if __name__ == "__main__":
    main()