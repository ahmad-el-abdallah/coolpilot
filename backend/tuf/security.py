"""The backend runs as root, so every /api request must:
  * arrive with an allowed Host header (blocks DNS-rebinding from web pages), and
  * carry the secret X-TUF-Token header (blocks cross-site requests: other sites
    can't read the token from our page, and custom headers force a CORS
    preflight we never approve).
"""
from __future__ import annotations

import hmac
import os
import secrets

from flask import abort, request

from .profiles import CONFIG_DIR

PORT = int(os.environ.get("TUF_PORT", "8787"))
TOKEN_FILE = os.path.join(CONFIG_DIR, "token")


def allowed_hosts() -> set[str]:
    hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
    extra = os.environ.get("TUF_EXTRA_HOSTS", "")
    hosts.update(h.strip() for h in extra.split(",") if h.strip())
    return hosts


def load_token() -> str:
    env = os.environ.get("TUF_TOKEN")
    if env:
        return env
    try:
        with open(TOKEN_FILE) as f:
            tok = f.read().strip()
            if tok:
                return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(32)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok)
    return tok


def install(app, token: str) -> None:
    hosts = allowed_hosts()

    @app.before_request
    def _guard():
        if request.host not in hosts:
            abort(403, "host not allowed")
        if request.path.startswith("/api/"):
            sent = request.headers.get("X-TUF-Token", "")
            if not hmac.compare_digest(sent, token):
                abort(403, "missing or bad token")

    @app.after_request
    def _headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store" if request.path.startswith("/api/") else resp.headers.get("Cache-Control", "no-cache")
        return resp
