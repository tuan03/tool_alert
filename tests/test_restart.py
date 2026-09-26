from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from app.config import Bootstrap
from app.main import create_app
from app.db import Repository

def test_token_rotation_invalidates_existing_cookie(tmp_path):
    key=Fernet.generate_key().decode()
    with TestClient(create_app(Bootstrap("old-admin-token-long-enough",key,tmp_path),background=False)) as c:
        c.post("/auth/login",json={"token":"old-admin-token-long-enough"})
        cookie=c.cookies.get("session")
        assert c.get("/api/status").status_code==200
    with TestClient(create_app(Bootstrap("new-admin-token-long-enough",key,tmp_path),background=False)) as c:
        c.cookies.set("session",cookie)
        assert c.get("/api/status").status_code==401

def test_session_expiry(tmp_path):
    config=Bootstrap("access-token-long-enough-for-test",Fernet.generate_key().decode(),tmp_path)
    app=create_app(config,background=False)
    with TestClient(app) as c:
        c.post("/auth/login",json={"token":config.admin_token})
        with app.state.repo.connect() as db:
            db.execute("UPDATE sessions SET expires=0")
        assert c.get("/api/status").status_code==401
