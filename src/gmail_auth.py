"""One-time Gmail sign-in on your laptop: gets a READ-ONLY refresh token and saves it to .env.

    python -m src.gmail_auth

Needs GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env (a "Desktop app" OAuth client, see README).
Opens Google's consent page in your browser, receives the answer on http://127.0.0.1 (never leaves your
machine), and writes GMAIL_REFRESH_TOKEN into .env. Nothing is printed. The only scope requested is
gmail.readonly: the token can read mail and can never delete, move, label or send anything.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import os
import re
import secrets
import stat
import sys
import threading
import webbrowser
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from .config import ROOT
from .envsecrets import load_dotenv, secret
from .gmail_sync import SCOPE, TOKEN_URL

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
ENV_FILE = ROOT / ".env"


def _set_env(key: str, value: str) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    out, done = [], False
    for line in lines:
        if re.match(rf"\s*{key}\s*=", line):
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(ENV_FILE, stat.S_IRUSR | stat.S_IWUSR)


def main() -> int:
    load_dotenv()
    cid, csec = secret("GMAIL_CLIENT_ID"), secret("GMAIL_CLIENT_SECRET")
    if not (cid and csec):
        print("Put GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env first (README, 'Gmail status sync').")
        return 1
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            q = parse_qs(urlsplit(self.path).query)
            if q.get("state", [""])[0] == state:
                result.update({k: v[0] for k, v in q.items()})
            ok = "code" in result
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<h3>Done. You can close this tab and go back to the terminal.</h3>" if ok else
                              "<h3>Sign-in was not completed. Go back to the terminal.</h3>").encode())

        def log_message(self, *args: object) -> None:  # keep the code out of the terminal
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = AUTH_URL + "?" + urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print("Opening Google sign-in in your browser. If it doesn't open, copy this link into the browser:\n")
    print(url + "\n")
    webbrowser.open(url)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=300)
    server.server_close()
    if "code" not in result:
        print(f"No sign-in received ({result.get('error', 'timed out after 5 minutes')}).")
        return 1
    resp = requests.post(TOKEN_URL, timeout=20, data={
        "client_id": cid, "client_secret": csec, "code": result["code"], "code_verifier": verifier,
        "grant_type": "authorization_code", "redirect_uri": redirect})
    if resp.status_code != 200:
        print(f"Token exchange failed: HTTP {resp.status_code} {resp.json().get('error', '')}")
        return 1
    data = resp.json()
    if set(data.get("scope", "").split()) != {SCOPE}:
        print(f"Refusing: Google granted more than read-only access ({data.get('scope')}).")
        return 1
    if not data.get("refresh_token"):
        print("Google returned no refresh token. Remove the app at myaccount.google.com/permissions and run again.")
        return 1
    _set_env("GMAIL_REFRESH_TOKEN", data["refresh_token"])
    print("Saved GMAIL_REFRESH_TOKEN to .env (read-only Gmail access). Next: python -m src.gmail_sync --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
