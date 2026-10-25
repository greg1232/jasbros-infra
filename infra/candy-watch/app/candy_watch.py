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


class Camera:
    """A Reolink, over its HTTP API: one login, then snapshots on its token.

    Reolinks lock the login after five failures, and a pod restarting in a
    loop would spend those in seconds — so after a refused login this does
    not ask again for ten minutes, whatever happens.

    Frames are kept in memory, newest only, and never written anywhere
    (docs/candy-watch.md, privacy).
    """

    SIZE = "&width=640&height=360"      # its sub stream: 40 KB, 0.6 s
    WAIT_AFTER_REFUSAL = 600

    def __init__(self, host: str, user: str, password: str,
                 call=None, fetch=None, clock=time.time):
        self.host, self.user, self.password = host, user, password
        self.call = call or self._call
        self.fetch = fetch or self._fetch
        self.clock = clock
        self.token = ""
        self.refused_at = 0.0
        self.error = ""
        self.frame: bytes = b""
        self.frame_at = 0.0
        self.lock = threading.Lock()

    def _call(self, cmd: str, params: dict) -> list:
        body = json.dumps([{"cmd": cmd, "action": 0, "param": params}]).encode()
        url = f"http://{self.host}/api.cgi?cmd={cmd}" + (
            f"&token={self.token}" if self.token else "")
        with urllib.request.urlopen(urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}),
                timeout=10) as reply:
            return json.loads(reply.read())

    def _fetch(self, url: str) -> tuple[str, bytes]:
        with urllib.request.urlopen(url, timeout=10) as reply:
            return reply.headers.get("Content-Type", ""), reply.read()

    def login(self) -> bool:
        if self.clock() - self.refused_at < self.WAIT_AFTER_REFUSAL:
            return False
        answer = self.call("Login", {"User": {
            "Version": "0", "userName": self.user, "password": self.password}})
        if answer[0].get("code") != 0:
            self.refused_at = self.clock()
            self.error = ("the camera refused the login — not asking again "
                          "for ten minutes (it locks after five)")
            print(f"[candy] {self.error}", flush=True)
            return False
        self.token = answer[0]["value"]["Token"]["name"]
        return True

    def snap(self) -> bytes | None:
        """The newest frame, or None. Logs in when it has to, and once more
        if the token has run out — never in a loop."""
        with self.lock:
            for attempt in (1, 2):
                if not self.token and not self.login():
                    return None
                try:
                    kind, data = self.fetch(
                        f"http://{self.host}/cgi-bin/api.cgi?cmd=Snap"
                        f"&channel=0&rs=candy{int(self.clock())}{self.SIZE}"
                        f"&token={self.token}")
                except Exception as error:
                    self.error = f"can't reach the camera — {type(error).__name__}"
                    return None
                if kind.startswith("image/"):
                    self.frame, self.frame_at, self.error = (data,
                                                             self.clock(), "")
                    return data
                self.token = ""         # expired: one fresh login, then stop
            self.error = "the camera answered, but not with a picture"
            return None

    def status(self) -> dict:
        return {"host": self.host, "error": self.error,
                "frame_at": self.frame_at, "ok": bool(self.frame)
                and not self.error}


def keep_looking(camera: "Camera", watched, every: float = 2) -> None:
    """A frame every couple of seconds while someone is looking at the page
    (gmk's screen always is) — and none at all while nobody is."""
    while True:
        if watched():
            camera.snap()
        time.sleep(every)


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
<title>Candy computer</title>
<style>
:root{color-scheme:dark;--bg:#0f0d0b;--card:#1a1612;--edge:#3a2f25;
  --ink:#f3ebe1;--dim:#a8998a;--pumpkin:#ff8a1f;--good:#5bc46b;--bad:#ef5b4c}
*{box-sizing:border-box}
body{font:16px/1.5 system-ui,sans-serif;margin:0;padding:1.25rem;
  background:var(--bg);color:var(--ink)}
main{max-width:120rem;margin:0 auto}
h1{font-size:1.6rem;margin:0;letter-spacing:.01em}
h1 span{color:var(--pumpkin)}
.sub{color:var(--dim);font-size:.9rem;margin:.2rem 0 1rem}
.grid{display:grid;grid-template-columns:minmax(0,2.2fr) minmax(0,1fr);gap:1rem}
@media (max-width:52rem){.grid{grid-template-columns:minmax(0,1fr)}}
.card{background:var(--card);border:1px solid var(--edge);border-radius:14px;
  padding:1rem 1.1rem;margin-bottom:1rem}
.card h2{font-size:.78rem;text-transform:uppercase;letter-spacing:.08em;
  color:var(--dim);margin:0 0 .6rem;font-weight:600}
.view{position:relative;aspect-ratio:16/9;background:#000;border-radius:10px;
  overflow:hidden}
.view img{width:100%;height:100%;object-fit:cover;display:block}
.view .note{position:absolute;inset:0;display:grid;place-items:center;
  color:var(--dim);text-align:center;padding:1rem}
.live{display:flex;align-items:center;gap:.45rem;font-size:.85rem;
  color:var(--dim);margin-top:.5rem}
.dot{width:.6rem;height:.6rem;border-radius:50%;background:var(--bad)}
.dot.on{background:var(--good)}
.rule{font-size:2.4rem;font-weight:700;line-height:1.15;margin:.2rem 0}
.rule small{display:block;font-size:.95rem;font-weight:400;color:var(--dim)}
ol{margin:0;padding-left:1.2rem}
ol li{margin:.35rem 0;color:var(--dim)}
ol li:last-child{color:var(--ink);font-weight:600}
.idle{color:var(--dim)}
details summary{cursor:pointer;color:var(--dim);font-size:.85rem}
input{font:inherit;padding:.5rem .6rem;border-radius:9px;width:100%;
  border:1px solid var(--edge);background:transparent;color:inherit;
  margin:.5rem 0}
button{font:inherit;padding:.5rem .85rem;border-radius:9px;margin:0 .3rem .3rem 0;
  border:1px solid var(--edge);background:transparent;color:inherit;cursor:pointer}
button:hover{border-color:var(--pumpkin)}
#out{color:var(--dim);font-size:.85rem;margin-top:.4rem}
</style>
<main>
<h1>JAS Brothers <span>Halloween</span> candy computer</h1>
<div class=sub id=sub>&nbsp;</div>
<div class=grid>
  <div>
    <div class=card>
      <div class=view><img id=frame alt="The candy box, live">
        <div class=note id=note>Looking for the camera…</div></div>
      <div class=live><span class=dot id=dot></span><span id=live>&nbsp;</span></div>
    </div>
  </div>
  <div>
    <div class=card>
      <div class=rule>Two candies each.<small>Take more, and the computer
        will notice.</small></div>
    </div>
    <div class=card>
      <h2>What's happening</h2>
      <div id=now class=idle>Watching the candy box.</div>
    </div>
    <div class=card>
      <details><summary>Test it</summary>
        <input id=costume maxlength=60 placeholder="Their costume, e.g. scary witch">
        <button onclick="act('api/demo', {wait: 10,
          costume: document.getElementById('costume').value})">The whole thing</button>
        <button onclick="act('api/say', {line: 'gangster'})">The gangster</button>
        <input id=text maxlength=200 placeholder="Something to say">
        <button onclick="act('api/say', {text:
          document.getElementById('text').value})">Say it</button>
        <div id=out></div>
      </details>
    </div>
  </div>
</div>
</main>
<script>
// Served under /candy through the ingress and at / when run directly.
const base = location.pathname.endsWith('/') ? location.pathname
                                             : location.pathname + '/';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let shown = null;

async function frame() {
  try {
    const r = await fetch(base + 'api/frame.jpg', {cache: 'no-store'});
    if (!r.ok) throw new Error(await r.text());
    const url = URL.createObjectURL(await r.blob());
    $('frame').src = url;
    if (shown) URL.revokeObjectURL(shown);
    shown = url;
    $('note').style.display = 'none';
  } catch (e) {
    $('note').style.display = 'grid';
    $('note').textContent = String(e.message || e).trim() || 'No picture yet.';
  }
}

async function status() {
  let d;
  try { d = await (await fetch(base + 'api/status', {cache: 'no-store'})).json(); }
  catch (e) { $('sub').textContent = 'Can’t reach the candy computer — retrying.'; return; }
  const cam = d.camera || {};
  $('sub').textContent = `Speaking through ${d.speaker} · ` +
    (d.clip ? 'film clip ready' : 'no film clip, so the speaker says the line') +
    (cam.host ? ` · camera ${cam.host}` : ' · no camera');
  const age = cam.frame_at ? Math.round(d.now - cam.frame_at) : null;
  const ok = cam.ok && age !== null && age < 15;
  $('dot').className = 'dot' + (ok ? ' on' : '');
  $('live').textContent = cam.error ? cam.error
    : age === null ? 'waiting for the first picture'
    : ok ? 'live' : `last picture ${age} s ago`;
  const demo = d.demo || {};
  $('now').innerHTML = demo.steps && demo.steps.length
    ? `<ol>${demo.steps.map(s => `<li>${esc(s)}</li>`).join('')}</ol>` +
      (demo.running ? '' : '<div class=idle style="margin-top:.5rem">Done. Watching again.</div>')
    : 'Watching the candy box.';
  $('now').className = demo.steps && demo.steps.length ? '' : 'idle';
}

async function act(where, body) {
  const r = await fetch(base + where, {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const d = await r.json();
  $('out').textContent = (d.ok ? '✓ ' : '✗ ') + (d.speaker || '') + ': ' + d.said;
  status();
}

frame(); status();
setInterval(frame, 2000);
setInterval(status, 2000);
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


class Watched:
    """When the page last asked for anything — frames are only fetched
    while somebody is looking."""

    def __init__(self, clock=time.time):
        self.clock, self.at = clock, 0.0

    def touch(self) -> None:
        self.at = self.clock()

    def __call__(self) -> bool:
        return self.clock() - self.at < 30


def serve(speaker: Speaker, port: int, here: str = "", clip: Path = CLIP,
          camera: "Camera | None" = None,
          watched: "Watched | None" = None) -> http.server.ThreadingHTTPServer:
    """`here` is this app as the speakers reach it: http://<gmk>/candy."""
    interaction = Interaction(speaker, here, clip)
    watched = watched or Watched()
    class Pages(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):                                 # noqa: N802
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._send(200, PAGE.encode(),
                                  "text/html; charset=utf-8")
            if path == "/api/frame.jpg":
                watched.touch()
                if camera is None:
                    return self._send(503, b"No camera is set up.",
                                      "text/plain")
                if not camera.frame:
                    return self._send(503, (camera.error or "Waiting for the "
                                            "first picture.").encode(),
                                      "text/plain")
                return self._send(200, camera.frame, "image/jpeg")
            if path == "/api/status":
                watched.touch()
                return self._json({"now": time.time(),
                                   "camera": camera.status() if camera else {},
                                   "speaker": speaker.name,
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
    camera = None
    if os.environ.get("REOLINK_HOST"):
        camera = Camera(os.environ["REOLINK_HOST"],
                        os.environ.get("REOLINK_USER", ""),
                        os.environ.get("REOLINK_PASSWORD", ""))
        print(f"[candy] camera at {camera.host}", flush=True)
    watched = Watched()
    server = serve(speaker, args.port, args.here.rstrip("/"), CLIP, camera,
                   watched)
    if camera:
        threading.Thread(target=keep_looking, args=(camera, watched),
                         daemon=True).start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
