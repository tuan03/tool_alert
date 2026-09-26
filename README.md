# BKTeam Alerts

Server **Python** theo dõi đơn Shopify trên BKTeam và gửi thông báo về Telegram khi phát hiện đơn mới. Chu kỳ mặc định **120 giây**, thay đổi được trong UI; đóng trình duyệt không dừng server.

- UI có token truy cập riêng từ `.env`.
- Cấu hình tài khoản BKTeam, bot token, chat ID/topic, store và lịch quét ngay trên UI.
- Thông báo gồm **store, mã đơn và Grand Total**.
- Gộp các hàng cùng đơn; lưu baseline và hàng đợi để tránh gửi lại khi restart.
- Bí mật lưu mã hóa trong SQLite; triển khai Docker có volume bền vững.
- Có lịch sử quét, thông báo, retry và trạng thái gửi chưa xác định.

## Setup VPS Linux

### 1. Chuẩn bị

VPS cần **Git, Python 3, Docker Engine và Docker Compose plugin**. Python trên host chỉ dùng để tạo `.env`; ứng dụng và Chromium chạy bên trong container, không cần desktop hay cài pip trên host.

Cài Docker theo [hướng dẫn chính thức](https://docs.docker.com/engine/install/) cho bản Linux của bạn; với Ubuntu dùng [hướng dẫn Docker Ubuntu](https://docs.docker.com/engine/install/ubuntu/).

Kiểm tra:

```bash
git --version
python3 --version
docker --version
docker compose version
```

Chạy Docker bằng tài khoản có quyền sử dụng Docker; thêm `sudo` vào lệnh Docker nếu môi trường của bạn yêu cầu.

### 2. Clone và tạo cấu hình riêng

```bash
git clone https://github.com/tuan03/tool_alert.git
cd tool_alert
python3 scripts/init_env.py
chmod 600 .env
nano .env
```

Script tạo token và khóa mã hóa ngẫu nhiên; **không ghi đè `.env` đã có**. Repo không chứa tài khoản BKTeam, bot token hoặc database của người triển khai trước.

| Biến | Ý nghĩa |
| --- | --- |
| `ADMIN_ACCESS_TOKEN` | Token dùng để đăng nhập UI, khác token bot Telegram. |
| `SETTINGS_ENCRYPTION_KEY` | Khóa mã hóa bí mật trong database; giữ nguyên qua restart/cập nhật và lưu cùng backup. |
| `DATA_DIR` | Thư mục dữ liệu khi chạy trực tiếp; Compose tự đặt thành `/app/data`. |
| `HOST`, `PORT` | Địa chỉ lắng nghe khi chạy trực tiếp; Compose đặt trong container. |
| `DOMAIN` | Domain thật cho cách HTTPS bên dưới, ví dụ `alerts.example.com`, không có `https://` hoặc đường dẫn. |

Chọn **một** trong hai cách triển khai sau; không chạy đồng thời cả hai vì chúng dùng database/volume khác nhau.

### 3A. Có domain — HTTPS với Caddy

Trỏ DNS của domain về VPS. TCP 80/443 phải truy cập được và chưa bị dịch vụ khác chiếm. Nếu VPS đã có reverse proxy, xem [hướng dẫn Linux chi tiết](deploy/LINUX.md) để lựa chọn cách phù hợp.

Thêm domain thật vào `.env`:

```dotenv
DOMAIN=alerts.example.com
```

Khởi động:

```bash
docker compose -f compose.vps.yaml config --quiet
docker compose -f compose.vps.yaml up -d --build
docker compose -f compose.vps.yaml ps
docker compose -f compose.vps.yaml logs --tail=60 alerts caddy
```

Mở **`https://alerts.example.com`**, đăng nhập bằng `ADMIN_ACCESS_TOKEN` trong `.env`.

Caddy xử lý HTTPS; backend 8000 không mở công khai. Database và chứng chỉ nằm trên volume, dịch vụ có chính sách tự khởi động lại. Cấu hình chỉ tin proxy Caddy; nếu subnet Docker `172.30.87.0/24` bị trùng, xem [cách thay đổi đồng bộ](deploy/LINUX.md).

### 3B. Chưa có domain — SSH tunnel

Trên VPS:

```bash
docker compose up -d --build
docker compose ps
```

Trên máy cá nhân, mở terminal và giữ kết nối:

```bash
ssh -N -L 18000:127.0.0.1:8000 user@VPS_IP
```

Thay `user@VPS_IP` bằng tài khoản SSH và IP của bạn. Mở **http://127.0.0.1:18000** trên máy cá nhân. Backend chỉ lắng nghe localhost trên VPS; không cần mở cổng 8000 ra Internet.

### 4. Cấu hình trong UI

1. Nhập tài khoản và mật khẩu BKTeam → **Kiểm tra đăng nhập**.
2. Nhập **bot token, chat ID**; thêm topic ID nếu gửi vào topic Telegram → **Gửi tin thử**.
3. Chọn chu kỳ, mặc định **120 giây**; phạm vi ngày, mặc định **30 ngày**; store để trống nghĩa là tất cả.
4. **Lưu cấu hình** → **Quét ngay** để tạo baseline.
5. **Bật tự động** và kiểm tra lịch sử ở lượt tiếp theo.

Bot phải có quyền gửi vào chat đích. Với chat cá nhân, mở hội thoại và bấm Start trước; với nhóm, thêm bot vào nhóm. Có thể nhập chat ID số hoặc `@channelusername`; không đưa bot token vào mã nguồn.

Mật khẩu và bot token hiện trống sau khi lưu: **để trống để giữ nguyên**, chọn checkbox Xóa nếu muốn xóa. Thay đổi cấu hình áp dụng ở lượt kế tiếp; quét đang chạy không bị cắt ngang. Tạm dừng chỉ dừng quét mới, sender vẫn xử lý queue cũ.

### 5. Cập nhật và vận hành

Các lệnh sau dành cho **3A**. Với **3B**, bỏ `-f compose.vps.yaml`.

```bash
# Trạng thái và log
docker compose -f compose.vps.yaml ps
docker compose -f compose.vps.yaml logs --tail=100 alerts

# Cập nhật từ GitHub rồi build lại, giữ nguyên volume
git pull --ff-only
docker compose -f compose.vps.yaml up -d --build

# Sau khi đổi token hoặc biến môi trường trong .env
docker compose -f compose.vps.yaml up -d --force-recreate alerts

# Dừng dịch vụ và giữ dữ liệu
docker compose -f compose.vps.yaml down
```

**Không dùng `down -v` nếu muốn giữ database/chứng chỉ.** Không tạo lại `SETTINGS_ENCRYPTION_KEY` khi database vẫn chứa cấu hình cũ.

Đổi `ADMIN_ACCESS_TOKEN` rồi recreate server sẽ vô hiệu phiên UI cũ. Endpoint `/health` chỉ trả tình trạng tối thiểu; API quản trị yêu cầu đăng nhập và CSRF cho request thay đổi.

## Backup, khôi phục và chuyển server

Làm theo [hướng dẫn backup/restore Linux](deploy/LINUX.md#backup-database-đang-chạy). Dùng SQLite backup API để tạo bản nhất quán, giữ khóa mã hóa cùng bản backup và giới hạn quyền truy cập.

- Nếu chuyển từ Windows/VPS cũ: chuyển database backup **và khóa mã hóa tương ứng** để giữ config, baseline và lịch sử.
- Nếu cài mới không chuyển database: nhập lại cấu hình trong UI; lần quét đầu tạo baseline mới.
- Dừng server cũ trước khi bật server mới. Hai database độc lập không chia sẻ trạng thái chống trùng.

## Cách phát hiện đơn mới

Lần quét đầu thành công chỉ ghi nhận đơn hiện có, **không gửi hàng loạt đơn cũ**. Các lượt sau tạo sự kiện cho đơn chưa từng thấy theo tài khoản nguồn + mã store + Shopify order ID. Restart hoặc thay đổi trạng thái/giá không tạo lại thông báo đơn mới.

Giá trị lấy từ **Grand Total**. Các hàng sản phẩm của cùng đơn lặp tổng tiền, vì vậy chỉ lấy một lần; không cộng lại. Chưa xác định được tiền tệ từ nguồn thì không tự thêm USD.

Cửa sổ ngày dựa theo ngày mua hàng: đơn import lùi ngày ngoài phạm vi sẽ không được nhìn thấy. Chọn toàn bộ lịch sử nếu cần bao phủ trường hợp đó. Mở rộng phạm vi sẽ tạo baseline cho phần mới thêm; không báo hàng loạt đơn cũ.

Ứng dụng chỉ đọc trang nguồn, không gọi thao tác cập nhật/hủy/sync/import đơn. Database không lưu địa chỉ, email hoặc điện thoại khách.

## Trạng thái Telegram và retry

| Trạng thái | Ý nghĩa |
| --- | --- |
| `pending` | Đang chờ gửi hoặc chờ cấu hình chat. |
| `sending` | Đang thực hiện yêu cầu gửi. |
| `sent` | Telegram xác nhận thành công, đã lưu message ID. |
| `retrying` | Lỗi tạm thời, sẽ thử lại có backoff. |
| `failed` | Lỗi cần xử lý hoặc hết số lần retry; sửa cấu hình rồi thử lại từ UI. |
| `uncertain` | Có thể tin đã tới nhưng mất phản hồi; kiểm tra Telegram trước khi gửi lại. |

Rate limit tạm dừng toàn queue theo thời gian Telegram yêu cầu. Đích chat/topic đã gắn cho một sự kiện không đổi khi sửa cấu hình. Nếu chưa có chat lúc tạo sự kiện, đích trống được gắn một lần khi có cấu hình.

Orders và outbox ghi cùng transaction. Nếu crawl lỗi/thiếu trang/schema thay đổi, snapshot không được xác nhận. Không hứa gửi exactly-once qua mạng: timeout hay restart giữa lúc gửi có thể để lại kết quả chưa xác định.

## Xử lý lỗi thường gặp

| Hiện tượng | Kiểm tra |
| --- | --- |
| Không có trang HTTPS | DNS, TCP 80/443, port bị chiếm, log `caddy`; chứng chỉ cần domain thực trỏ đúng VPS. |
| Token UI không đúng | Dùng `ADMIN_ACCESS_TOKEN`, không dùng bot token; đổi .env xong phải recreate container. |
| Lưu cấu hình báo CSRF | Tải lại trang/đăng nhập lại; nếu dùng proxy riêng, kiểm tra Host và forwarded HTTPS. |
| Không gửi được Telegram | Gửi thử từ UI, kiểm tra token, chat ID, quyền gửi của bot và topic ID. |
| Quét thành công nhưng không báo đơn cũ | Đây là baseline; chỉ đơn chưa từng thấy sau đó mới tạo thông báo. |
| Không giải mã được cấu hình | Khôi phục đúng `SETTINGS_ENCRYPTION_KEY` của database; không tự tạo khóa mới. |
| Sau deploy thấy UI trắng/lỗi server | Xem log `alerts`, tình trạng health và dung lượng đĩa của VPS. |

## Chạy trực tiếp để phát triển

Python **3.13** đã được kiểm thử (project yêu cầu Python 3.12+). Trên Windows:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.lock
.venv/Scripts/python -m playwright install chromium
.venv/Scripts/python scripts/init_env.py
.venv/Scripts/python run.py
```

Mở http://127.0.0.1:8000. Chỉ chạy một instance/worker cho cùng DATA_DIR; ứng dụng có khóa tiến trình để ngăn chạy chồng.

## Kiểm thử

```powershell
.venv/Scripts/python -m pytest -q
```

Suite dùng dữ liệu nguồn/Telegram giả và Chromium cục bộ; không tạo/sửa đơn thật hoặc gửi Telegram thật. Crawler đã được kiểm tra đọc trên nguồn thật; HTTPS domain và Telegram của người triển khai cần kiểm tra sau khi cấu hình trên VPS.
