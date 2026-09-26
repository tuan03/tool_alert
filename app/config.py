import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv


@dataclass(frozen=True)
class Bootstrap:
    admin_token: str
    encryption_key: str
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self):
        if len(self.admin_token) < 24:
            raise RuntimeError("ADMIN_ACCESS_TOKEN phải có ít nhất 24 ký tự.")
        from cryptography.fernet import Fernet
        try:
            Fernet(self.encryption_key.encode())
        except (ValueError, TypeError):
            raise RuntimeError("SETTINGS_ENCRYPTION_KEY không hợp lệ.") from None

    @classmethod
    def from_env(cls):
        load_dotenv()
        return cls(os.getenv("ADMIN_ACCESS_TOKEN",""), os.getenv("SETTINGS_ENCRYPTION_KEY",""),
                   Path(os.getenv("DATA_DIR","data")), os.getenv("HOST","127.0.0.1"), int(os.getenv("PORT","8000")))
