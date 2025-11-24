# **📸 Hệ thống Quay Video Đồng Bộ Đa Camera (Multi-Cam Sync System)**

Dự án này cung cấp giải pháp quay video đồng bộ từ nhiều điện thoại Android và truyền dữ liệu về máy tính (PC) theo thời gian thực qua mạng Wi-Fi cục bộ (LAN). Hệ thống sử dụng cơ chế Client-Server với giao thức TCP (truyền ảnh) và UDP (điều khiển đồng bộ).

## **🌟 Chức năng chính**

1. **Quay hình đồng bộ:** Kích hoạt quay video trên tất cả các điện thoại kết nối cùng lúc chỉ với một nút bấm từ bất kỳ điện thoại nào.
2. **Truyền tải thời gian thực:** Các frame hình ảnh (JPEG) được gửi liên tục từ điện thoại về PC với tốc độ 5 FPS (Frames Per Second).
3. **Phân loại dữ liệu:** Server tự động phân chia dữ liệu từ các Camera khác nhau vào các thư mục riêng biệt data/Camera_**{deviceId}** (vi du: data/Camera_android_19327).
4. **Cơ chế Xác thực:** Sử dụng Token và Device ID để đảm bảo chỉ các thiết bị được cấp quyền mới có thể kết nối và gửi lệnh.
5. **Giao thức tin cậy:**
   * **TCP:** Đảm bảo toàn vẹn dữ liệu hình ảnh (không mất frame khi đã gửi).
   * **UDP:** Đảm bảo tín hiệu điều khiển (START/STOP) có độ trễ thấp nhất để đồng bộ thời gian.

## **🛠 Kiến trúc & Công nghệ**

| Thành phần | Công nghệ | Vai trò |
| :---- | :---- | :---- |
| **Server (PC)** | Python | Lắng nghe kết nối, điều phối lệnh đồng bộ, lưu trữ hình ảnh. |
| **Client (Mobile)** | Android (Kotlin) | Quay phim, nén ảnh, gửi dữ liệu và nhận lệnh điều khiển. |
| **Giao thức Ảnh** | TCP Socket | Truyền Header (4 bytes độ dài) \+ Payload (Base64 Image). |
| **Giao thức Lệnh** | UDP Socket | Gửi JSON Command (REGISTER, START, STOP). |

### **Sơ đồ cổng mạng (Ports)**

* **Port 5000 (UDP):** Cổng điều khiển trung tâm (Nhận lệnh Start/Stop, Gửi tín hiệu Sync).
* **Port 6001 (TCP):** Cổng nhận dữ liệu cho **Camera 1**.
* **Port 600x (TCP):** Cổng nhận dữ liệu cho **Camera x**.

## **📋 Hướng dẫn Cài đặt & Cấu hình**

### **1\. Chuẩn bị môi trường**

* **Mạng:** Máy tính và các điện thoại Android **BẮT BUỘC** phải kết nối chung một mạng Wi-Fi (hoặc cùng lớp mạng LAN).
* **Máy tính:** Đã cài Python 3.x.
* **Điện thoại:** Android 7.0 trở lên.

### **2\. Cấu hình Server (PC)**

1. Mở file CamServer.py.
2. Kiểm tra biến AUTH\_TOKEN. Mặc định là "123456".
3. Mở Terminal/CMD tại thư mục chứa file và chạy:
   python CamServer.py

4. **Quan trọng:** Nếu Windows hỏi quyền truy cập mạng (Firewall), hãy chọn **Allow Access** (cho cả Private và Public networks).
5. Ghi lại địa chỉ IP LAN hiện trên màn hình console (Ví dụ: 192.168.1.15).

### **3\. Cài đặt Client (Android)**

1. Mở dự án Android trong Android Studio.
2. Thay sdk path tương ứng ở máy của bạn trong file local.properties sdk.dir
3. Build file APK và cài đặt lên 2 điện thoại.
4. Cấp quyền Camera khi mở ứng dụng lần đầu.

## **🚀 Hướng dẫn Sử dụng**

### **Bước 1: Kết nối (Handshake)**

1. Trên cả 2 điện thoại:
   * Nhập **IP Server** (IP của máy tính, vd: 192.168.1.15).
   * Nhập **Token** (Mặc định: 123456).
   * Nhập **Camera Id** tuỳ chọn/không cần nhập (Mặc định: camera_+ deviceId).
2. **Điện thoại A:**  Nhấn **KẾT NỐI**.
   * *Log:* TCP Connected... và UDP \-\> REGISTER.
3. **Điện thoại B:**  Nhấn **KẾT NỐI**.

### **Bước 2: Bắt đầu ghi hình (Sync Start)**

1. Trên **bất kỳ điện thoại nào** (A hoặc B), nhấn nút **START**.
2. Điện thoại đó sẽ gửi lệnh UDP lên Server.
3. Server phát lệnh SYNC\_START xuống tất cả các máy.
4. Cả 2 máy sẽ cùng hiện dòng \>\>\> START RECORDING \<\<\< và bắt đầu gửi ảnh.
5. Trên PC, kiểm tra thư mục data/Camera_**{deviceId}** để thấy ảnh được lưu.

### **Bước 3: Dừng ghi hình**

1. Nhấn nút **STOP** trên bất kỳ điện thoại nào.
2. Hệ thống ngừng gửi dữ liệu.

## **🔧 Xử lý sự cố (Troubleshooting)**

### **1\. Lỗi "Connection Refused" hoặc không kết nối được TCP**

* **Nguyên nhân:** Sai IP Server hoặc Tường lửa (Firewall) chặn.
* **Khắc phục:**
  * Tắt tạm thời Windows Firewall để test.
  * Đảm bảo nhập đúng IP LAN (bắt đầu bằng 192.168...), không nhập IP loopback (127.0.0.1) hay IP ảo Docker (172...) trên điện thoại thật.

### **2\. Lỗi "unpack requires a buffer of 4 bytes" trên Server**

* **Nguyên nhân:** App Android bị tắt đột ngột hoặc mất mạng khi đang gửi dữ liệu.
* **Khắc phục:** Server đã có cơ chế tự xử lý, chỉ cần khởi động lại App trên điện thoại và kết nối lại.

### **3\. Lỗi UDP \[WinError 10054\]**

* **Nguyên nhân:** Một trong các Client đã ngắt kết nối nhưng Server vẫn cố gửi lệnh UDP phản hồi.
* **Khắc phục:** Không ảnh hưởng đến hệ thống. Server sẽ tự động xóa Client đó khỏi danh sách trong lần gửi tiếp theo.

## **📦 Cấu trúc Gói tin (Dành cho Developer)**

### **Gói tin TCP (Dữ liệu ảnh)**

Để tránh dính gói tin (TCP Stream), cấu trúc gửi đi như sau:

1. **Header (4 bytes):** Số nguyên (Big-Endian) biểu thị độ dài của chuỗi Base64 ảnh.
2. **Payload (N bytes):** Chuỗi Base64 của ảnh JPEG.

### **Gói tin UDP (Lệnh JSON)**

{
  "type": "START",          // Loại lệnh: REGISTER | START | STOP
  "deviceId": "android\_x",  // ID định danh thiết bị
  "token": "123456"         // Mã bảo mật
}
