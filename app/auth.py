import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from fastapi import HTTPException, Request


class Auth:
    def __init__(self, repo, token):
        self.repo = repo
        self.token = token
        self.generation = hashlib.sha256(token.encode()).hexdigest()
        self.attempts = defaultdict(deque)

    @staticmethod
    def check_origin(request: Request):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403,"Nguồn yêu cầu không hợp lệ.")

    def login(self, request: Request, token: str):
        self.check_origin(request)
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        # Bound memory used by unauthenticated addresses.
        for key in list(self.attempts):
            if not self.attempts[key] or self.attempts[key][-1] < now - 300:
                self.attempts.pop(key,None)
        bucket = self.attempts[ip]
        while bucket and bucket[0] < now - 300:
            bucket.popleft()
        if len(bucket) >= 10 or len(self.attempts) > 10000:
            raise HTTPException(429,"Quá nhiều lần thử. Chờ 5 phút.")
        if not hmac.compare_digest(token.encode(), self.token.encode()):
            bucket.append(now)
            raise HTTPException(401,"Token truy cập không đúng.")
        bucket.clear()
        session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        digest = hashlib.sha256(session.encode()).hexdigest()
        with self.repo.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires<? OR generation!=?",(now,self.generation))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)",(digest,csrf,now+43200,self.generation))
        return session, csrf

    def require(self, request: Request):
        digest = hashlib.sha256(request.cookies.get("session","").encode()).hexdigest()
        with self.repo.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE digest=?",(digest,)).fetchone()
        if not row or row["expires"] <= time.time() or row["generation"] != self.generation:
            raise HTTPException(401,"Vui lòng đăng nhập.")
        if request.method not in ("GET","HEAD","OPTIONS"):
            self.check_origin(request)
            if not hmac.compare_digest(request.headers.get("x-csrf-token",""),row["csrf"]):
                raise HTTPException(403,"CSRF token không hợp lệ. Tải lại trang.")
        return dict(row)

    def logout(self, request):
        digest = hashlib.sha256(request.cookies.get("session","").encode()).hexdigest()
        with self.repo.connect() as db:
            db.execute("DELETE FROM sessions WHERE digest=?",(digest,))
