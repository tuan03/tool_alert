import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from app.config import Bootstrap
from app.main import create_app

TOKEN="test-ui-token-that-is-long-enough"
@pytest.fixture
def client(tmp_path):
    config=Bootstrap(TOKEN,Fernet.generate_key().decode(),tmp_path)
    with TestClient(create_app(config,background=False)) as c:
        yield c

def login(c):
    response=c.post("/auth/login",json={"token":TOKEN})
    assert response.status_code == 200
    return {"X-CSRF-Token":response.json()["csrf"]}

def test_auth_everywhere_and_token_not_returned(client):
    for url in ["/api/settings","/api/status","/api/history"]:
        assert client.get(url).status_code == 401
    assert client.get("/",follow_redirects=False).status_code == 303
    assert client.post("/auth/login",json={"token":"wrong"}).status_code == 401
    headers=login(client)
    assert client.get("/api/settings").json()["interval_seconds"] == 120
    assert TOKEN not in client.get("/").text
    assert client.put("/api/settings",json={"interval_seconds":60}).status_code == 403
    assert client.put("/api/settings",json={"interval_seconds":60},headers=headers).status_code == 200
    client.post("/auth/logout",headers=headers)
    assert client.get("/api/status").status_code == 401

def test_settings_validation_and_secret_masking(client):
    headers=login(client)
    assert client.put("/api/settings",json={"password":"pass-hidden","bot_token":"123:abcdefghijk"},headers=headers).status_code == 200
    response=client.get("/api/settings")
    assert "pass-hidden" not in response.text and "abcdefghijk" not in response.text
    assert response.json()["password_configured"]
    assert client.put("/api/settings",json={"interval_seconds":86401},headers=headers).status_code == 422
    bad=client.put("/api/settings",json={"bot_token":"bad-secret-token"},headers=headers)
    assert bad.status_code == 422 and "bad-secret-token" not in bad.text
    assert client.post("/api/scan",headers=headers).status_code == 400

def test_mutations_require_auth_and_csrf(client):
    for path in ["/api/scan","/api/source/test","/api/telegram/test","/api/notifications/1/retry"]:
        assert client.post(path,json={}).status_code == 401
    login(client)
    for path in ["/api/scan","/api/source/test","/api/telegram/test","/api/notifications/1/retry"]:
        assert client.post(path,json={}).status_code == 403

def test_login_rate_limit(client):
    for _ in range(10):
        assert client.post("/auth/login",json={"token":"bad"}).status_code == 401
    assert client.post("/auth/login",json={"token":TOKEN}).status_code == 429

def test_foreign_origin_login_blocked(client):
    assert client.post("/auth/login",json={"token":TOKEN},headers={"Origin":"https://evil.example"}).status_code == 403
