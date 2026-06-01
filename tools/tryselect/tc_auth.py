# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from mach.util import get_state_dir

TC_ROOT_URL = "https://firefox-ci-tc.services.mozilla.com"
TC_CREDENTIALS_FILE = (
    Path(get_state_dir(specific_to_topsrcdir=False)) / "tc_credentials.json"
)
_CREDENTIAL_LIFETIME_S = 24 * 60 * 60  # 1 day


def get_taskcluster_credentials(scope: str) -> dict:
    """Return a dict with 'clientId' and 'accessToken' for Taskcluster.

    Checks environment variables first, then a cached credentials file, and
    finally falls back to the browser-redirect auth flow.

    `scope` is passed to the TC UI so the created client has the right
    permissions (e.g. 'hooks:trigger-hook:git-push/mozilla/firefox-try/*').
    """
    if os.environ.get("TASKCLUSTER_CLIENT_ID") and os.environ.get(
        "TASKCLUSTER_ACCESS_TOKEN"
    ):
        return {
            "clientId": os.environ["TASKCLUSTER_CLIENT_ID"],
            "accessToken": os.environ["TASKCLUSTER_ACCESS_TOKEN"],
        }

    cached = _load_cached_credentials()
    if cached:
        return cached

    return _browser_auth_flow(scope)


def _load_cached_credentials() -> dict | None:
    if not TC_CREDENTIALS_FILE.exists():
        return None
    try:
        data = json.loads(TC_CREDENTIALS_FILE.read_text())
        if data.get("expires", 0) > time.time():
            return {"clientId": data["clientId"], "accessToken": data["accessToken"]}
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _save_credentials(clientId: str, accessToken: str) -> None:
    TC_CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TC_CREDENTIALS_FILE.write_text(
        json.dumps(
            {
                "clientId": clientId,
                "accessToken": accessToken,
                "expires": time.time() + _CREDENTIAL_LIFETIME_S,
            }
        )
    )


def _browser_auth_flow(scope: str) -> dict:
    """Open the TC client-creation UI and wait for the callback."""
    credentials = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            credentials["clientId"] = qs.get("clientId", [""])[0]
            credentials["accessToken"] = qs.get("accessToken", [""])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Signed in to Taskcluster</h1>"
                b"<p>You may close this window.</p></body></html>"
            )

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    callback_url = f"http://localhost:{port}"

    params = urlencode(
        {
            "scope": scope,
            "name": "mach-try",
            "expires": "1d",
            "callback_url": callback_url,
            "description": "Temporary client for mach try",
        }
    )
    login_url = f"{TC_ROOT_URL}/auth/clients/create?{params}"

    print(f"Opening browser for Taskcluster sign-in: {login_url}")
    webbrowser.open(login_url)

    server.handle_request()
    server.server_close()

    if not credentials.get("clientId") or not credentials.get("accessToken"):
        raise RuntimeError("Taskcluster sign-in did not return credentials.")

    _save_credentials(credentials["clientId"], credentials["accessToken"])
    return credentials
