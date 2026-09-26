from pathlib import Path
import secrets
import base64
import os

target = Path(__file__).resolve().parents[1] / ".env"
if target.exists():
    print(".env already exists; preserved. No secrets changed.")
else:
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("ADMIN_ACCESS_TOKEN="+secrets.token_urlsafe(32)+"\n")
        f.write("SETTINGS_ENCRYPTION_KEY="+base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()+"\n")
        f.write("DATA_DIR=data\nHOST=127.0.0.1\nPORT=8000\n")
    print("Created .env. Open it locally to get your UI access token. Keep the encryption key with backups.")
