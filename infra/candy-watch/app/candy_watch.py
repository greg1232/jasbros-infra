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
from pathlib import Path

# What the gangster in "Angels with Filthy Souls" — the film inside Home
# Alone — says, spoken by the speaker in its own voice. The film's audio
# itself is never part of this project (docs/candy-watch.md).
GANGSTER = ("I'm gonna give you to the count of ten to get your ugly, "
            "yella, no-good keister off my property, before I pump your "
            "guts full of lead!")

SAY_LIMIT = 200         # what a speaker's /api/do say accepts

# What candy watch says, in order. Fixed for now; Claude writes the
# call-outs in step 2 of docs/candy-watch.md.
def welcome(costume: str = "") -> str:
    """The first thing a visitor hears. The costume comes from the camera —
    Claude names it from the first frame (step 3); until then, from whoever
    runs the demo."""
    seen = f" I see you're {_a(costume)}!" if costume.strip() else ""
    return (f"Welcome to the JAS Brothers Halloween candy computer!{seen} "
            f"Please take two candies each.")


def _a(costume: str) -> str:
    costume = costume.strip().rstrip(".!")
    if costume.lower().startswith(("a ", "an ", "the ")):
        return costume
    return ("an " if costume[:1].lower() in "aeiou" else "a ") + costume

CALL_OUT = "Hey! I see you took more than two candies. Put them back."
THANKS = "Thank you. Enjoy your two."
LAST_CHANCE = "You didn't put them back. All right..."

# Your own copy of the scene, put in the cluster from the laptop by
# ./jasbros candy clip — never in the image, never in git.
CLIP = Path(os.environ.get("CLIP", "/clip/home-alone.flac"))


class Speaker:
    """One Claude speaker, through the API its own dashboard already has."""

    def __init__(self, name: str, url: str, post=None, get=None):
        self.name, self.url = name, url.rstrip("/")
        self.post = post or _post_json
        self.get = get or _get_json
        self.last: dict = {}

    def busy(self) -> bool:
        """Is it talking or playing something? (Not answering counts as no.)"""
        state = self.get(f"{self.url}/api/speaker", 5) or {}
        return bool(state.get("talking"))

    def wait_until_quiet(self, sleep=time.sleep, longest: float = 60) -> None:
        """A speaker answers "Saying it" at once and talks in the background,
        so the next line waits for this one to finish — or it talks over it."""
        sleep(1)                        # give it a moment to start
        waited = 1.0
        while waited < longest and self.busy():
            sleep(0.5)
            waited += 0.5

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

    def play(self, url: str) -> dict:
        """Ask the speaker to fetch a recording from here and play it."""
        reply = self.post(f"{self.url}/api/do", {"what": "play", "value": url},
                          15)
        said = (reply or {}).get("said", f"{self.name} didn't answer")
        ok = said.startswith("Playing")
        self.last = {"at": time.time(), "text": url, "ok": ok,
                     "speaker": self.name}
        print(f"[candy] {self.name}: {said} ({url})", flush=True)
        return {"ok": ok, "speaker": self.name, "said": said, "url": url}


def gangster(speaker: Speaker, here: str, clip: Path = CLIP) -> dict:
    """The line from the film if its clip is here, in the speaker's voice
    if it isn't — or if the speaker can't play it."""
    if here and clip.is_file():
        played = speaker.play(f"{here}/clip/{clip.name}")
        if played["ok"]:
            return played
    return speaker.say(GANGSTER)


def _get_json(url: str, timeout: float) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as reply:
            return json.loads(reply.read())
    except urllib.error.HTTPError as error:
        error.close()
        return None
    except Exception:
        return None


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
<div class=sub>Speaking through <b id=who></b>. <span id=clip></span>
No camera yet.</div>
<input id=text maxlength=200 placeholder="Something for the speaker to say">
<button onclick="say({text:document.getElementById('text').value})">Say it</button>
<button onclick="say({line:'gangster'})">The gangster line</button>
<input id=costume maxlength=60 placeholder="Their costume, e.g. scary witch">
<button onclick="say({wait:10, costume:document.getElementById('costume').value},
  'api/demo')">The whole thing</button>
<div id=out></div>
</main>
<script>
const base = location.pathname.endsWith('/') ? location.pathname
                                             : location.pathname + '/';
fetch(base + 'api/status').then(r => r.json()).then(d => {
  document.getElementById('who').textContent = d.speaker;
  document.getElementById('clip').textContent = d.clip
    ? 'The film clip is here.' : 'No film clip yet, so the speaker says the line.';
});
async function say(body, where = 'api/say') {
  const r = await fetch(base + where, {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const d = await r.json();
  document.getElementById('out').textContent =
    (d.ok ? '✓ ' : '✗ ') + d.speaker + ': ' + d.said;
}
</script>
"""


class Interaction:
    """One trip to the candy box that took too many, start to finish.

    `put_back` says whether the extras came back while it waited. With no
    camera yet it is always no, which is the demo: every line, then the film.
    """

    def __init__(self, speaker: Speaker, here: str, clip: Path = CLIP,
                 sleep=time.sleep):
        self.speaker, self.here, self.clip, self.sleep = (speaker, here, clip,
                                                          sleep)
        self.steps: list[str] = []
        self.running = False

    def _say(self, text: str) -> None:
        self.steps.append(text)
        self.speaker.say(text)
        self.speaker.wait_until_quiet(self.sleep)

    def run(self, wait: float = 60, put_back=lambda: False,
            costume: str = "") -> list[str]:
        self.running, self.steps = True, []
        try:
            self._say(welcome(costume))
            self._say(CALL_OUT)
            waited = 0.0
            while waited < wait:
                if put_back():
                    self._say(THANKS)
                    return self.steps
                self.sleep(1)
                waited += 1
            self._say(LAST_CHANCE)
            self.steps.append("(the gangster)")
            gangster(self.speaker, self.here, self.clip)
            self.speaker.wait_until_quiet(self.sleep)
            return self.steps
        finally:
            self.running = False


def serve(speaker: Speaker, port: int, here: str = "",
          clip: Path = CLIP) -> http.server.ThreadingHTTPServer:
    """`here` is this app as the speakers reach it: http://<gmk>/candy."""
    interaction = Interaction(speaker, here, clip)
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
                                   "clip": clip.is_file(),
                                   "last": speaker.last,
                                   "demo": {"running": interaction.running,
                                            "steps": interaction.steps}})
            if path == "/healthz":
                return self._send(200, b"ok\n", "text/plain")
            if path == f"/clip/{clip.name}" and clip.is_file():
                return self._send(200, clip.read_bytes(), "audio/flac")
            self._send(404, b"not here\n", "text/plain")

        def do_POST(self):                                # noqa: N802
            path = urllib.parse.urlparse(self.path).path
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json({"ok": False, "said": "Not JSON."}, 400)
            if path == "/api/say":
                if body.get("line") == "gangster":
                    return self._json(gangster(speaker, here, clip))
                return self._json(speaker.say(str(body.get("text", ""))))
            if path == "/api/demo":
                # The whole interaction, with nobody putting anything back.
                # On a thread: it takes a minute, and the page shouldn't wait.
                if interaction.running:
                    return self._json({"ok": False, "said": "Already running."})
                wait = min(max(float(body.get("wait", 10)), 0), 120)
                costume = str(body.get("costume", ""))[:60]
                threading.Thread(target=interaction.run, daemon=True,
                                 kwargs={"wait": wait,
                                         "costume": costume}).start()
                return self._json({"ok": True, "speaker": speaker.name,
                                   "said": f"Running the demo, waiting "
                                           f"{wait:.0f} s for the candy."})
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
    parser.add_argument("--here", default=os.environ.get("HERE", ""),
                        help="this app as the speakers reach it")
    args = parser.parse_args()
    name, _, url = args.speaker.partition("=")
    if not url:
        raise SystemExit("--speaker name=http://address:8080 (or $SPEAKER)")
    speaker = Speaker(name, url)
    print(f"[candy] speaking through {name} at {url}; serving on "
          f":{args.port}", flush=True)
    print(f"[candy] clip: {CLIP if CLIP.is_file() else 'none'}; "
          f"speakers fetch from {args.here or 'nowhere'}", flush=True)
    server = serve(speaker, args.port, args.here.rstrip("/"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
