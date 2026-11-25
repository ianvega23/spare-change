"""`spare enroll --bank plaid`: link your bank through Plaid Link on your own computer.

1. Asks Plaid for a one-time link token (with your client id and secret).
2. Serves a page on 127.0.0.1 only that opens Plaid Link; you sign in to your bank there.
3. Plaid Link hands the page a short-lived public token, which this program exchanges for the
   long-lived access token and saves to plaid/token.txt (git-ignored, readable only by you).

Uses Plaid's Production environment, which is where the free Trial plan's real connections live.
Only the transactions product is requested: Spare Change never asks for payment access.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path

from .core.bank_plaid import BASE_URLS
from .core.http import NetworkError, Transport, make_transport
from .enroll import serve, write_private

LINK_JS = "https://cdn.plaid.com/link/v2/stable/link-initialize.js"
CLIENT_USER_ID = "spare-change-owner"  # one person per copy; Plaid only needs it to be stable

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Spare Change: link your bank</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:36rem;margin:4rem auto;padding:0 1rem}}
button{{font:inherit;padding:.6rem 1.2rem;cursor:pointer}}</style></head>
<body><h1>Link your bank</h1>
<p>This page runs on your computer. Plaid asks for your bank login; Spare Change only receives a
read-only access token for transactions, which is saved on this machine.</p>
<button id="go">Open Plaid</button><p id="status"></p>
<script src="{link_js}"></script>
<script>
const status = document.getElementById("status");
const handler = Plaid.create({{
  token: {link_token},
  onSuccess: async (publicToken, metadata) => {{
    status.textContent = "Saving...";
    const r = await fetch("/token", {{method: "POST", headers: {{"Content-Type": "application/json",
      "X-Spare-Nonce": {nonce}}}, body: JSON.stringify({{publicToken: publicToken,
      institution: metadata.institution && metadata.institution.name,
      accounts: (metadata.accounts || []).map(a => ({{id: a.id, name: a.name, mask: a.mask, subtype: a.subtype}}))}})}});
    status.textContent = r.ok ? "Linked. You can close this tab and go back to the terminal."
                              : "Saving the token failed; check the terminal.";
  }},
  onExit: (err) => {{ status.textContent = err ? "Plaid closed with an error: " + (err.display_message || err.error_code)
                                              : "Closed without linking."; }},
}});
document.getElementById("go").onclick = () => handler.open();
</script></body></html>
"""


class PlaidError(Exception):
    pass


def _post(transport: Transport, environment: str, path: str, body: dict) -> dict:
    try:
        resp = transport("POST", BASE_URLS[environment] + path,
                         {"Content-Type": "application/json", "User-Agent": "spare-change"},
                         json.dumps(body).encode())
    except NetworkError as e:
        raise PlaidError(f"can't reach Plaid ({e})") from None
    data = json.loads(resp.body or b"{}")
    if resp.status != 200:
        # Plaid's error text never contains the secret; show its code and message only.
        raise PlaidError(f"{data.get('error_code', 'HTTP ' + str(resp.status))}: "
                         f"{str(data.get('error_message') or '')[:200]}")
    return data


def link_token(client_id: str, secret: str, *, environment: str = "production",
               access_token: str | None = None, transport: Transport | None = None) -> str:
    """A one-time token that opens Link. With access_token, Link opens in update mode to re-link."""
    body = {"client_id": client_id, "secret": secret, "client_name": "Spare Change",
            "user": {"client_user_id": CLIENT_USER_ID}, "country_codes": ["US"], "language": "en"}
    if access_token:
        body["access_token"] = access_token
    else:
        body["products"] = ["transactions"]
        body["transactions"] = {"days_requested": 30}
    return _post(transport or make_transport(), environment, "/link/token/create", body)["link_token"]


def render_page(link_token_value: str, nonce: str) -> str:
    js = json.dumps
    return PAGE.format(link_js=LINK_JS, link_token=js(link_token_value), nonce=js(nonce))


def make_saver(client_id: str, secret: str, environment: str, transport: Transport | None = None,
               existing_token: str | None = None):
    def save(directory: Path, payload: dict) -> Path:
        public = payload.get("publicToken")
        if not isinstance(public, str) or not public.startswith("public-") or len(public) > 200:
            raise ValueError("that doesn't look like a Plaid public token")
        if existing_token:  # update mode: the access token stays the same
            path = write_private(directory, existing_token)
            item_id = None
        else:
            try:
                data = _post(transport or make_transport(), environment, "/item/public_token/exchange",
                             {"client_id": client_id, "secret": secret, "public_token": public})
            except PlaidError as e:
                raise ValueError(f"Plaid didn't exchange the token ({e})") from None
            path = write_private(directory, data["access_token"])
            item_id = data.get("item_id")
        accounts = [{k: a.get(k) for k in ("id", "name", "mask", "subtype")}
                    for a in payload.get("accounts") or [] if isinstance(a, dict)]
        meta = {"item_id": item_id, "institution": payload.get("institution"), "accounts": accounts}
        (directory / "item.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return path
    return save


def run(client_id: str, secret: str, *, environment: str = "production", directory: str = "plaid",
        relink: bool = False, port: int = 0, open_browser: bool = True,
        transport: Transport | None = None, out=print) -> int:
    folder = Path(directory)
    existing = None
    if relink:
        try:
            existing = (folder / "token.txt").read_text(encoding="utf-8").strip()
        except OSError:
            out(f"There's no {folder / 'token.txt'} to re-link. Run without --relink first.")
            return 1
    try:
        token = link_token(client_id, secret, environment=environment, access_token=existing,
                           transport=transport)
    except PlaidError as e:
        out(f"Plaid refused to start linking: {e}\nCheck your client id and that the secret is the one "
            f"for {environment.capitalize()} (the Trial plan uses Production).")
        return 1
    nonce = secrets.token_urlsafe(24)
    code = serve(render_page(token, nonce), nonce, folder, label="Plaid",
                 save=make_saver(client_id, secret, environment, transport, existing),
                 port=port, open_browser=open_browser)
    if code:
        return code
    out("Next: run `python -m spare bank-check` to see your purchases, then `python -m spare setup`, "
        "which offers this token for the PLAID_ACCESS_TOKEN secret.")
    return 0
