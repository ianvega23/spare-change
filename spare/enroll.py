"""`spare enroll`: link your bank through Teller Connect on your own computer.

Starts a web page on 127.0.0.1 only, opens Teller Connect, and saves the access token Teller
returns to teller/token.txt (git-ignored, readable only by you). Nothing leaves your machine
except the Teller Connect flow itself.
"""
from __future__ import annotations

import html
import http.server
import json
import os
import secrets
import threading
import webbrowser
from pathlib import Path

CONNECT_JS = "https://cdn.teller.io/connect/connect.js"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Spare Change: link your bank</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:36rem;margin:4rem auto;padding:0 1rem}}
button{{font:inherit;padding:.6rem 1.2rem;cursor:pointer}}</style></head>
<body><h1>Link your bank</h1>
<p>This page runs on your computer. Teller Connect asks for your bank login; Spare Change only
receives a read-only access token, which is saved on this machine.</p>
<button id="go">Open Teller Connect</button><p id="status"></p>
<script src="{connect_js}"></script>
<script>
const status = document.getElementById("status");
const connect = TellerConnect.setup({{
  applicationId: {app_id},
  environment: {environment},
  products: ["transactions"],
  selectAccount: "single",
  {enrollment}
  onSuccess: async (e) => {{
    const token = e.accessToken || (e.enrollment && e.enrollment.accessToken);
    const r = await fetch("/token", {{method: "POST", headers: {{"Content-Type": "application/json",
      "X-Spare-Nonce": {nonce}}}, body: JSON.stringify({{accessToken: token,
      enrollmentId: e.enrollment && e.enrollment.id,
      institution: e.enrollment && e.enrollment.institution && e.enrollment.institution.name}})}});
    status.textContent = r.ok ? "Linked. You can close this tab and go back to the terminal."
                              : "Saving the token failed; check the terminal.";
  }},
  onExit: () => {{ status.textContent = "Closed without linking."; }},
}});
document.getElementById("go").onclick = () => connect.open();
</script></body></html>
"""


def render_page(app_id: str, environment: str, nonce: str, enrollment_id: str | None = None) -> str:
    js = json.dumps
    return PAGE.format(connect_js=CONNECT_JS, app_id=js(app_id), environment=js(environment),
                       nonce=js(nonce),
                       enrollment=f"enrollmentId: {js(enrollment_id)}," if enrollment_id else "")


def save_token(directory: Path, payload: dict) -> Path:
    token = payload.get("accessToken")
    if not isinstance(token, str) or not token or len(token) > 200 or any(c.isspace() for c in token):
        raise ValueError("that doesn't look like a Teller access token")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "token.txt"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    meta = {k: payload.get(k) for k in ("enrollmentId", "institution")}
    (directory / "enrollment.json").write_text(json.dumps(meta, indent=2) + "\n")
    return path


def make_handler(page: str, nonce: str, directory: Path, done: threading.Event, out=print):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep tokens and paths out of the terminal log
            pass

        def _send(self, status: int, body: bytes, ctype: str = "text/plain; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _for_us(self) -> bool:
            """Only requests addressed to this server. A web page that points its own domain at
            127.0.0.1 (DNS rebinding) still sends its own name as Host, so it gets nothing."""
            port = self.server.server_address[1]
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def do_GET(self):
            if not self._for_us():
                return self._send(403, b"forbidden")
            if self.path != "/":
                return self._send(404, b"not found")
            self._send(200, page.encode(), "text/html; charset=utf-8")

        def do_POST(self):
            if not self._for_us() or self.path != "/token" or self.headers.get("X-Spare-Nonce") != nonce:
                return self._send(403, b"forbidden")
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 10_000)
                path = save_token(directory, json.loads(self.rfile.read(length)))
            except (ValueError, json.JSONDecodeError) as e:
                out(f"Couldn't save the token: {html.escape(str(e))}")
                return self._send(400, b"bad token")
            out(f"Saved your Teller access token to {path} (only you can read it).")
            done.set()  # before replying, so whoever sees the 200 can rely on it
            self._send(200, b"ok")
    return Handler


def run(app_id: str, environment: str = "development", directory: str = "teller",
        enrollment_id: str | None = None, port: int = 0, open_browser: bool = True) -> int:
    nonce = secrets.token_urlsafe(24)
    done = threading.Event()
    handler = make_handler(render_page(app_id, environment, nonce, enrollment_id), nonce,
                           Path(directory), done)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Open {url} to link your bank (Ctrl+C to cancel).")
    if open_browser:
        webbrowser.open(url)
    try:
        done.wait()
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 1
    finally:
        server.shutdown()
    print("Next: add it as the TELLER_ACCESS_TOKEN secret in your repo's `money` environment, e.g.\n"
          f"  gh secret set TELLER_ACCESS_TOKEN --env money < {Path(directory) / 'token.txt'}")
    return 0
