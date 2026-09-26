# Triển khai VPS Linux

Ứng dụng và Chromium chạy trong Docker Linux; không cần giao diện desktop trên VPS.
Bộ triển khai có hai cách: domain + HTTPS với compose.vps.yaml, hoặc SSH tunnel với compose.yaml khi chưa có domain.
Không chạy cả Windows và VPS tự động gửi cùng lúc: hai database độc lập không chia sẻ chống trùng.

## 1. Chuẩn bị
Cài Docker Engine + Compose plugin theo tài liệu chính thức cho hệ điều hành của VPS:
https://docs.docker.com/engine/install/
Ubuntu: https://docs.docker.com/engine/install/ubuntu/

Upload source vào /opt/bkteam-alert, gồm app/, scripts/, deploy/, Dockerfile, compose*.yaml, requirements.lock, pyproject.toml và run.py.
Không upload .venv hoặc cache Windows. Cách bên dưới là cài mới; nếu cần giữ lịch sử/baseline cũ, xem mục chuyển dữ liệu.
Chạy các lệnh từ /opt/bkteam-alert.

## 2. Tạo cấu hình
Chỉ cần Python 3 chuẩn để tạo .env, không cần pip trên host:
```sh
cd /opt/bkteam-alert
python3 scripts/init_env.py
chmod 600 .env
nano .env
```
File .env có ADMIN_ACCESS_TOKEN và SETTINGS_ENCRYPTION_KEY ngẫu nhiên. Giữ khóa mã hóa cùng backup.
Thêm dòng DOMAIN=alerts.example.com (thay bằng domain thật, không kèm https:// hoặc đường dẫn).

## 3A. Có domain: HTTPS tự động
Trỏ bản ghi DNS A của domain về IP VPS. Nếu có AAAA thì IPv6 cũng phải trỏ đúng.
Cho phép TCP 80/443 tới VPS; giữ cổng SSH đang dùng. Caddy cần các cổng này và DNS đúng để cấp chứng chỉ.
Không dùng cách này nếu 80/443 đã bị reverse proxy khác chiếm: tích hợp ứng dụng vào proxy hiện tại hoặc dùng cách 3B.

```sh
docker compose -f compose.vps.yaml config --quiet
docker compose -f compose.vps.yaml up -d --build
docker compose -f compose.vps.yaml ps
docker compose -f compose.vps.yaml logs --tail=60 alerts caddy
```
Mở https://alerts.example.com, đăng nhập bằng ADMIN_ACCESS_TOKEN trong .env.
Vào UI cấu hình BKTeam, bot token/chat ID, gửi thử rồi Bật tự động.
Cổng backend 8000 không được publish. Uvicorn chỉ tin forwarded headers từ IP container Caddy 172.30.87.2.
Nếu subnet 172.30.87.0/24 đã được dùng trên VPS, đổi đồng bộ subnet, ipv4_address và FORWARDED_ALLOW_IPS trong compose.vps.yaml.
Caddy tự quản lý HTTPS và giữ chứng chỉ trong volume caddy-data. Tham khảo:
https://caddyserver.com/docs/automatic-https
https://caddyserver.com/docs/caddyfile/directives/reverse_proxy

## 3B. Chưa có domain: truy cập qua SSH tunnel
```sh
docker compose up -d --build
```
Trên máy cá nhân, mở terminal và giữ kết nối:
```sh
ssh -N -L 18000:127.0.0.1:8000 user@VPS_IP
```
Mở http://127.0.0.1:18000 trên máy cá nhân. Kết nối từ máy tới VPS được mã hóa bởi SSH.
Compose này chỉ bind 127.0.0.1:8000 trên VPS; không cần mở cổng 8000 công khai.
Không trộn lệnh giữa hai file Compose: chúng có project/volume khác nhau.

## Vận hành (cách 3A)
```sh
# Xem tình trạng và log
docker compose -f compose.vps.yaml ps
docker compose -f compose.vps.yaml logs --tail=100 alerts

# Cập nhật source, build lại và giữ nguyên dữ liệu
docker compose -f compose.vps.yaml up -d --build

# Sau khi đổi token/biến môi trường trong .env, recreate để nạp giá trị mới
docker compose -f compose.vps.yaml up -d --force-recreate alerts

# Dừng dịch vụ, giữ volume
docker compose -f compose.vps.yaml down
```
Không dùng down -v khi muốn giữ database/chứng chỉ.
Với cách 3B, bỏ -f compose.vps.yaml trong các lệnh tương ứng.

## Backup database đang chạy
SQLite backup API tạo bản nhất quán; không copy riêng file database đang chạy.
```sh
docker compose -f compose.vps.yaml exec -T alerts python -c "import sqlite3; s=sqlite3.connect('/app/data/alerts.sqlite3'); d=sqlite3.connect('/tmp/alerts-backup.sqlite3'); s.backup(d); d.close(); s.close()"
mkdir -p backups
chmod 700 backups
docker compose -f compose.vps.yaml cp alerts:/tmp/alerts-backup.sqlite3 "backups/alerts-$(date +%Y%m%d-%H%M%S).sqlite3"
chmod 600 backups/*.sqlite3
```
Sao lưu .env/SETTINGS_ENCRYPTION_KEY riêng với quyền truy cập hạn chế. Mất khóa mã hóa sẽ không đọc được mật khẩu/bot token đã lưu.

## Chuyển dữ liệu từ Windows hoặc khôi phục
1. Tạm dừng quét ở UI cũ, chờ lượt quét/gửi hiện tại hoàn tất; dừng server cũ trước khi bật VPS.
2. Tạo backup bằng scripts/backup.py và chuyển sang VPS qua SCP, ví dụ restore.sqlite3.
3. Đặt SETTINGS_ENCRYPTION_KEY trên VPS giống bản cũ. Có thể giữ hoặc đổi ADMIN_ACCESS_TOKEN.
4. Nếu VPS đã có dữ liệu, backup trước. Dừng container alerts:
```sh
docker compose -f compose.vps.yaml stop alerts
```
5. Chạy khôi phục sau đây chỉ khi đã chọn đúng file và chấp nhận thay database đích. Lệnh chạy khi alerts đã dừng, xóa WAL/SHM cũ để không trộn journal:
```sh
docker compose -f compose.vps.yaml run --rm --no-deps -v "$PWD/restore.sqlite3:/tmp/restore.sqlite3:ro" alerts python -c "from pathlib import Path; import sqlite3; p=Path('/app/data/alerts.sqlite3'); [Path(str(p)+s).unlink(missing_ok=True) for s in ('-wal','-shm')]; src=sqlite3.connect('file:/tmp/restore.sqlite3?mode=ro',uri=True); dst=sqlite3.connect(p); src.backup(dst); dst.close(); src.close()"
docker compose -f compose.vps.yaml up -d --force-recreate alerts
```
File backup trên host phải đọc được bởi user container trong lúc restore; cấp quyền đọc tạm thích hợp rồi thu hồi sau đó.
Đăng nhập UI, kiểm tra lịch sử/config rồi mới Bật tự động. Nếu không chuyển database, VPS tạo baseline mới và không báo lại các đơn đang có.

## Kiểm tra sau deploy
- Container alerts healthy; UI HTTPS truy cập được.
- Token sai bị từ chối, token đúng vào được; lưu config không lỗi CSRF.
- Kiểm tra đăng nhập BKTeam thành công.
- Gửi thử Telegram đến đúng chat.
- Quét ngay lần đầu tạo baseline; Bật tự động và quan sát lần quét tiếp theo.
- Recreate alerts vẫn giữ cấu hình và lịch sử.
Chứng chỉ domain thật và kết nối Telegram thật chỉ xác minh được sau khi cấu hình trên VPS.
