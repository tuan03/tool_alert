import subprocess
import sys
from pathlib import Path
from cryptography.fernet import Fernet
from dotenv import dotenv_values


def test_fresh_server_env_generation_needs_only_python_stdlib(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "init_env.py"
    script.write_bytes(Path("scripts/init_env.py").read_bytes())
    result = subprocess.run([sys.executable, "-S", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    env = dotenv_values(tmp_path / ".env")
    assert len(env["ADMIN_ACCESS_TOKEN"]) >= 24
    Fernet(env["SETTINGS_ENCRYPTION_KEY"].encode())
    original = (tmp_path / ".env").read_bytes()
    assert subprocess.run([sys.executable, "-S", str(script)], capture_output=True).returncode == 0
    assert (tmp_path / ".env").read_bytes() == original
    assert env["ADMIN_ACCESS_TOKEN"] not in result.stdout
