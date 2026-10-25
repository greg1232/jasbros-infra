"""Candy watch: a camera on the candy box, and a speaker that notices.

See docs/candy-watch.md. This is step 1 of the plan: the pod, and making a
Claude speaker talk from it. The camera, the counting and Claude's
call-outs come next.

Standard library only, like the fleet dashboard.

    python candy_watch.py --speaker pi4=http://192.168.4.95:8080   # laptop
"""

import argparse
import http.server
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# What the gangster in "Angels with Filthy Souls" — the film inside Home
# Alone — says, spoken by the speaker in its own voice. The film's audio
# itself is never part of this project (docs/candy-watch.md).
GANGSTER = ("I'm gonna give you to the count of ten to get your ugly, "
            "yella, no-good keister off my property, before I pump your "
            "guts full of lead!")

SAY_LIMIT = 200         # what a speaker's /api/do say accepts


class Speaker:
    """One Claude speaker, through the API its own dashboard already has."""

    def __init__(self, name: str, url: str, post=None):
        self.name, self.url = name, url.rstrip("/")
        self.post = post or _post_json
        self.last: dict = {}

    def say(self, text: str) -> dict:
        text = " ".join(text.split())[:SAY_LIMIT]
        if not text:
            return {"ok": False, "said": "Nothing to say."}
        reply = self.post(f"{self.url}/api/do", {"what": "say", "value": text},
                          5)
        ok = reply is not None and "said" in reply
        self.last = {"at": time.time(), "text": text, "ok": ok,
                     "speaker": self.name}
        print(f"[candy] {self.name}: {'said' if ok else 'COULD NOT SAY'} "
              f"{text!r}", flush=True)
        return {"ok": ok, "speaker": self.name, "text": text,
                "said": (reply or {}).get("said",
                                          f"{self.name} didn't answer")}


def _post_json(url: str, body: dict, timeout: float) -> dict | None:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return json.loads(reply.read())
    except urllib.error.HTTPError as error:
        error.close()
        return None
    except Exception:
        return None


PAGE = """<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Candy watch</title>
<style>
:root{color-scheme:light dark;--edge:#8883;--dim:#8889}
body{font:15px/1.5 system-ui,sans-serif;margin:0;padding:1.5rem 1rem}
main{max-width:36rem;margin:0 auto}
h1{font-size:1.15rem;margin:0 0 .25rem}
.sub{color:var(--dim);font-size:.85rem;margin-bottom:1.25rem}
button{font:inherit;padding:.55rem .9rem;border-radius:9px;
  border:1px solid var(--edge);background:transparent;cursor:pointer}
input{font:inherit;padding:.5rem .6rem;border-radius:9px;width:100%;
  border:1px solid var(--edge);background:transparent;color:inherit;
  box-sizing:border-box;margin-bottom:.5rem}
#out{margin-top:1rem;color:var(--dim);font-size:.9rem}
</style>
<main>
<h1>Candy watch</h1>
<div class=sub>Speaking through <b id=who></b>. No camera yet — step 1.</div>
<input id=text maxlength=200 placeholder="Something for the speaker to say">
<button onclick="say({text:document.getElementById('text').value})">Say it</button>
<button onclick="say({line:'gangster'})">The gangster line</button>
<div id=out></div>
</main>
<script>
const base = location.pathname.endsWith('/') ? location.pathname
                                             : location.pathname + '/';
fetch(base + 'api/status').then(r => r.json()).then(d =>
  document.getElementById('who').textContent = d.speaker);
async function say(body) {
  const r = await fetch(base + 'api/say', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const d = await r.json();
  document.getElementById('out').textContent =
    (d.ok ? '✓ ' : '✗ ') + d.speaker + ': ' + d.said;
}
</script>
"""


def serve(speaker: Speaker, port: int) -> http.server.ThreadingHTTPServer:
    class Pages(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):                                 # noqa: N802
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._send(200, PAGE.encode(),
                                  "text/html; charset=utf-8")
            if path == "/api/status":
                return self._json({"speaker": speaker.name,
                                   "speaker_url": speaker.url,
                                   "last": speaker.last})
            if path == "/healthz":
                return self._send(200, b"ok\n", "text/plain")
            self._send(404, b"not here\n", "text/plain")

        def do_POST(self):                                # noqa: N802
            path = urllib.parse.urlparse(self.path).path
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json({"ok": False, "said": "Not JSON."}, 400)
            if path == "/api/say":
                text = GANGSTER if body.get("line") == "gangster" \
                    else str(body.get("text", ""))
                return self._json(speaker.say(text))
            self._send(404, b"not here\n", "text/plain")

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def _send(self, code: int, body: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("", port), Pages)
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--speaker", default=os.environ.get("SPEAKER", ""),
                        help="name=url of the speaker that talks")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    name, _, url = args.speaker.partition("=")
    if not url:
        raise SystemExit("--speaker name=http://address:8080 (or $SPEAKER)")
    speaker = Speaker(name, url)
    print(f"[candy] speaking through {name} at {url}; serving on "
          f":{args.port}", flush=True)
    server = serve(speaker, args.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
