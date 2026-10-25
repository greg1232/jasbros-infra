"""Sign in to Spotify once, and keep the token so the speaker never has to.

Spotify's API needs you, personally, to approve this in a browser — there
is no way to do it from a Raspberry Pi with no screen. So it happens here,
on a machine with a browser, once. What comes out is a refresh token, which
is what the speaker actually uses: it never sees your password, and you can
revoke it at https://www.spotify.com/account/apps/ whenever you like.

Before running this, make an app at
https://developer.spotify.com/dashboard (free, a minute):

  1. Create app. Any name — "Claude Speaker" will do.
  2. Redirect URI: exactly   http://127.0.0.1:8888/callback
     Spotify insists on 127.0.0.1 rather than localhost for loopback.
  3. Which API: tick "Web API".
  4. Copy the Client ID and Client Secret into .env:

        SPOTIFY_CLIENT_ID=...
        SPOTIFY_CLIENT_SECRET=...

Then run this, approve in the browser, and paste the last line into .env.

    python train/spotify_login.py
"""

import base64
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config  # noqa: E402

PORT = 8888
REDIRECT = f"http://127.0.0.1:{PORT}/callback"

# Only what the speaker actually needs: see what's playing, and control it.
# Not your library, not your playlists, not your email.
SCOPES = "user-read-playback-state user-modify-playback-state"

_code = {}


class Catcher(http.server.BaseHTTPRequestHandler):
    """Catches the one redirect Spotify sends back, then goes away."""

    def do_GET(self):  # noqa: N802 — http.server's spelling.
        query = urllib.parse.urlparse(self.path).query
        _code.update(urllib.parse.parse_qs(query))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        good = "code" in _code
        self.wfile.write(
            ("<h2>" + ("Signed in. You can close this tab."
                       if good else "Something went wrong — see the terminal.")
             + "</h2>").encode())

    def log_message(self, *args):
        pass  # Don't print a request log over the instructions.


def main() -> int:
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        return 0

    if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
        print(__doc__)
        print("\nSPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET aren't in .env "
              "yet — see above.")
        return 1

    state = secrets.token_urlsafe(16)
    ask = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": config.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT,
        "scope": SCOPES,
        "state": state,
    })

    server = http.server.HTTPServer(("127.0.0.1", PORT), Catcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("Opening Spotify in your browser. Approve it there.\n")
    print(f"If nothing opens, go to:\n  {ask}\n")
    webbrowser.open(ask)

    while "code" not in _code and "error" not in _code:
        pass
    server.shutdown()

    if "error" in _code:
        print(f"Spotify said: {_code['error'][0]}")
        return 1
    if _code.get("state", [None])[0] != state:
        print("The reply didn't match the request. Try again.")
        return 1

    secret = base64.b64encode(
        f"{config.SPOTIFY_CLIENT_ID}:{config.SPOTIFY_CLIENT_SECRET}".encode()
    ).decode()
    request = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": _code["code"][0],
            "redirect_uri": REDIRECT,
        }).encode(),
        headers={"Authorization": f"Basic {secret}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=30) as response:
        token = json.load(response)

    if "refresh_token" not in token:
        print(f"No refresh token came back: {token}")
        return 1

    print("\nSigned in. Add this line to .env, then run ./deploy.sh:\n")
    print(f"SPOTIFY_REFRESH_TOKEN={token['refresh_token']}\n")
    print("It doesn't expire. Revoke it any time at "
          "https://www.spotify.com/account/apps/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
