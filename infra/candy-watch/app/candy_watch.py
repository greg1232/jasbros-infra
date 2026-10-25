"""Candy watch: a camera on the candy box, and a speaker that notices.

See docs/candy-watch.md. This is step 1 of the plan: the pod, and making a
Claude speaker talk from it. The camera, the counting and Claude's
call-outs come next.

Standard library only, like the fleet dashboard.

    python candy_watch.py --speaker pi4=http://192.168.4.95:8080   # laptop
"""

import argparse
import hashlib
import http.server
import json
import os
import signal
import sys
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
    # Claude writes the phrase to follow "I see you're", article and all
    # ("a scary witch", "Darth Vader"); only a bare noun gets one added.
    seen = f" I see you're {_a(costume)}!" if costume.strip() else ""
    # "jas" in lower case so the speaker says it like "jazz", not J-A-S.
    return (f"Welcome to the jas brothers Halloween candy computer!{seen} "
            f"Please take two candies each.")


def _a(costume: str) -> str:
    costume = costume.strip().rstrip(".!")
    if costume.lower().startswith(("a ", "an ", "the ", "two ", "three ",
                                   "some ")) or costume[:1].isupper():
        return costume
    return ("an " if costume[:1].lower() in "aeiou" else "a ") + costume

CALL_OUT = "I see you took more than two candies. Put them back."


def call_out(costume: str = "") -> str:
    who = costume.strip().rstrip(".!")
    return f"Hey{', ' + who if who else ''}! {CALL_OUT}"
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

    def snap(self, size: str | None = None) -> bytes | None:
        """The newest small frame (or, with size="", a full 4K still), or
        None. Logs in when it has to, and once more if the token has run
        out — never in a loop."""
        size = self.SIZE if size is None else size
        with self.lock:
            for attempt in (1, 2):
                if not self.token and not self.login():
                    return None
                try:
                    kind, data = self.fetch(
                        f"http://{self.host}/cgi-bin/api.cgi?cmd=Snap"
                        f"&channel=0&rs=candy{int(self.clock())}{size}"
                        f"&token={self.token}")
                except Exception as error:
                    self.error = f"can't reach the camera — {type(error).__name__}"
                    return None
                if kind.startswith("image/"):
                    self.error = ""
                    if size == self.SIZE:       # the live view is the small one
                        self.frame, self.frame_at = data, self.clock()
                    return data
                self.token = ""         # expired: one fresh login, then stop
            self.error = "the camera answered, but not with a picture"
            return None

    def still(self) -> bytes | None:
        """One full-resolution still, for Claude — 4K, about a second."""
        return self.snap(size="")

    def status(self) -> dict:
        return {"host": self.host, "error": self.error,
                "frame_at": self.frame_at, "ok": bool(self.frame)
                and not self.error}


def keep_watching(camera: "Camera", watcher, every: float = 1) -> None:
    """A small frame a second, through the detector — always, whether or not
    anyone has the page open, because that's the job. ~25 ms of one CPU."""
    while True:
        started = time.time()
        frame = camera.snap()
        if frame and watcher is not None:
            try:
                watcher.step(frame)
            except Exception as error:         # one bad frame isn't the end
                print(f"[candy] watch step failed: {type(error).__name__}: "
                      f"{error}", flush=True)
        time.sleep(max(0.0, every - (time.time() - started)))


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
.top{display:flex;justify-content:space-between;align-items:center;gap:1rem;
  flex-wrap:wrap}
.switch{font:inherit;font-weight:700;font-size:1rem;padding:.6rem 1.2rem;
  border-radius:999px;cursor:pointer;border:2px solid var(--edge);
  background:transparent;color:var(--dim);margin:0}
.switch.on{background:var(--good);border-color:var(--good);color:#000}
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
.box{position:absolute;border:3px solid var(--pumpkin);border-radius:6px}
.box span{position:absolute;top:-1.5rem;left:-3px;background:var(--pumpkin);
  color:#000;font-size:.75rem;font-weight:700;padding:.05rem .4rem;
  border-radius:4px 4px 4px 0;white-space:nowrap}
.nums{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.5rem;
  margin:.4rem 0 .8rem}
.num{border:1px solid var(--edge);border-radius:10px;padding:.4rem .5rem}
.num b{display:block;font-size:1.4rem;line-height:1.2;
  font-variant-numeric:tabular-nums}
.num span{color:var(--dim);font-size:.72rem}
.num.bad b{color:var(--bad)}
.group{padding:.3rem 0;border-bottom:1px solid var(--edge)}
.group:last-child{border:0}
.log{font:12px/1.5 ui-monospace,monospace;color:var(--dim);margin-top:.6rem;
  max-height:9rem;overflow:auto}
.small{color:var(--dim);font-size:.8rem}
</style>
<main>
<div class=top>
  <h1>JAS Brothers <span>Halloween</span> candy computer</h1>
  <button class=switch id=switch onclick="toggle()">…</button>
</div>
<div class=sub id=sub>&nbsp;</div>
<div class=grid>
  <div>
    <div class=card>
      <div class=view><img id=frame alt="The candy box, live">
        <div id=boxes></div>
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
      <h2>Watching</h2>
      <div id=groups class=idle>Nobody at the box.</div>
      <div class=nums>
        <div class=num><b id=n-base>–</b><span>to start</span></div>
        <div class=num><b id=n-now>–</b><span>now</span></div>
        <div class=num id=c-taken><b id=n-taken>–</b><span>taken</span></div>
        <div class=num><b id=n-allowed>–</b><span>allowed</span></div>
      </div>
      <div class=small id=engine>&nbsp;</div>
      <div class=log id=log></div>
    </div>
    <div class=card>
      <h2>What the speaker said</h2>
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
  if (d.version && window.pageVersion && d.version !== window.pageVersion) {
    location.reload();                 // a new page was deployed
    return;
  }
  window.pageVersion = window.pageVersion || d.version;
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
  const w = d.watch;
  const colours = ['#5bc46b', '#ff8a1f', '#5bc4f0', '#c77dff', '#ffd23f'];
  const colour = g => colours[((g || 1) - 1) % colours.length];
  $('boxes').innerHTML = !w ? '' : w.people.map(p => {
    const [x1, y1, x2, y2] = p.box;
    return `<div class=box style="left:${x1*100}%;top:${y1*100}%;` +
      `width:${(x2-x1)*100}%;height:${(y2-y1)*100}%;` +
      `border-color:${colour(p.group)}"><span style="background:` +
      `${colour(p.group)}">#${p.id}${p.group ? ' · group ' + p.group : ''}` +
      `</span></div>`;
  }).join('');
  if (w) {
    $('switch').className = 'switch' + (w.on ? ' on' : '');
    $('switch').textContent = w.on ? 'Watching — on' : 'Switched off';
    $('switch').title = w.on ? 'Click to stop greeting people and asking Claude'
                             : 'Click to start greeting trick-or-treaters';
    $('groups').className = w.groups.length ? '' : 'idle';
    $('groups').innerHTML = w.groups.length ? w.groups.map(g =>
      `<div class=group><b style="color:${colour(g.id)}">Group ${g.id}</b> · ` +
      `${g.kids} ${g.kids === 1 ? 'person' : 'people'} · ` +
      `${esc(g.costume) || '<span class=idle>looking…</span>'}</div>`).join('')
      : !w.on ? (w.people.length ? 'Someone is there — switched off, so ' +
                 'nobody is greeted.' : 'Switched off: watching, but not ' +
                 'greeting anyone or asking Claude.')
      : (w.people.length ? 'Someone is walking up…' : 'Nobody at the box.');
    const show = v => v === null || v === undefined ? '–' : v;
    $('n-base').textContent = show(w.baseline);
    $('n-now').textContent = show(w.count);
    $('n-taken').textContent = show(w.taken);
    $('n-allowed').textContent = w.groups.length ? w.allowed : '–';
    $('c-taken').className = 'num' + (w.taken > w.allowed && w.groups.length
                                       ? ' bad' : '');
    $('engine').textContent = `Detector ${w.detector_ms} ms a frame on gmk · ` +
      (w.claude ? `Claude asked ${w.claude_looks}×` +
                  (w.claude_error ? ` · ${w.claude_error}` : '')
                : 'no Claude key yet — people only');
    $('log').innerHTML = w.events.slice().reverse().map(esc).join('<br>');
  } else {
    $('engine').textContent = 'Not watching: no camera or no detector.';
  }
  const demo = d.demo || {};
  $('now').innerHTML = demo.steps && demo.steps.length
    ? `<ol>${demo.steps.map(s => `<li>${esc(s)}</li>`).join('')}</ol>` +
      (demo.running ? '' : '<div class=idle style="margin-top:.5rem">Done. Watching again.</div>')
    : 'Watching the candy box.';
  $('now').className = demo.steps && demo.steps.length ? '' : 'idle';
}

async function toggle() {
  const r = await fetch(base + 'api/status', {cache: 'no-store'});
  const on = !((await r.json()).watch || {}).on;
  await act('api/watch', {on});
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
        # One speaker, one thing at a time: a new group's welcome waits for
        # a call-out in progress to finish rather than talking over it.
        self.talking = threading.Lock()

    def _say(self, text: str) -> None:
        self.steps.append(text)
        self.speaker.say(text)
        self.speaker.wait_until_quiet(self.sleep)

    def welcome(self, costume: str = "") -> None:
        """Someone has arrived: hello, and what they're dressed as."""
        with self.talking:
            self.steps = []
            self._say(welcome(costume))

    def call_out(self, costume: str, put_back, wait: float = 60) -> list[str]:
        """Too many taken: ask for them back, then the film if they don't.
        `put_back()` is asked about once a few seconds while it waits."""
        self.running = True
        try:
            self.talking.acquire()
            self._say(call_out(costume))
            waited = 0.0
            while waited < wait:
                if put_back():
                    self._say(THANKS)
                    return self.steps
                self.sleep(5)
                waited += 5
            self._say(LAST_CHANCE)
            self.steps.append("(the gangster)")
            gangster(self.speaker, self.here, self.clip)
            self.speaker.wait_until_quiet(self.sleep)
            return self.steps
        finally:
            self.talking.release()
            self.running = False

    def run(self, wait: float = 60, put_back=lambda: False,
            costume: str = "") -> list[str]:
        """The whole thing, as the demo button plays it."""
        self.running = True
        self.welcome(costume)
        return self.call_out(costume, put_back, wait)


# Which page this is. A kiosk never reloads by itself, so the page checks
# this every few seconds and reloads when a deploy has changed it.
VERSION = hashlib.sha256(PAGE.encode()).hexdigest()[:12]


def serve(speaker: Speaker, port: int, here: str = "", clip: Path = CLIP,
          camera: "Camera | None" = None, watcher=None,
          interaction: "Interaction | None" = None
          ) -> http.server.ThreadingHTTPServer:
    """`here` is this app as the speakers reach it: http://<gmk>/candy."""
    interaction = interaction or Interaction(speaker, here, clip)
    class Pages(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):                                 # noqa: N802
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._send(200, PAGE.encode(),
                                  "text/html; charset=utf-8")
            if path == "/api/frame.jpg":
                if camera is None:
                    return self._send(503, b"No camera is set up.",
                                      "text/plain")
                if not camera.frame:
                    return self._send(503, (camera.error or "Waiting for the "
                                            "first picture.").encode(),
                                      "text/plain")
                return self._send(200, camera.frame, "image/jpeg")
            if path == "/api/status":
                return self._json({"now": time.time(), "version": VERSION,
                                   "watch": watcher.status() if watcher
                                   else None,
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
            if path == "/api/watch":
                if watcher is None:
                    return self._json({"ok": False,
                                       "said": "Not watching: no camera."})
                watcher.switch(bool(body.get("on")))
                return self._json({"ok": True, "on": watcher.on,
                                   "speaker": speaker.name,
                                   "said": "Watching." if watcher.on
                                   else "Switched off."})
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


def make_watcher(camera: "Camera", interaction: "Interaction"):
    """The detector always, if its model is here; Claude only with a key."""
    import vision
    import watch

    if not vision.MODEL.is_file():
        print(f"[candy] no detector model at {vision.MODEL} — live view only",
              flush=True)
        return None
    detector = vision.Detector()
    eyes = watch.Eyes() if os.environ.get("ANTHROPIC_API_KEY") else None
    print(f"[candy] watching: detector on 1 thread; "
          f"{'Claude ' + watch.MODEL if eyes else 'no Claude key — people only'}",
          flush=True)
    return watch.Watcher(camera, detector, eyes, interaction)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--speaker", default=os.environ.get("SPEAKER", ""),
                        help="name=url of the speaker that talks")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--here", default=os.environ.get("HERE", ""),
                        help="this app as the speakers reach it")
    args = parser.parse_args()
    # As a container's first process Python ignores SIGTERM, so every
    # restart would wait out Kubernetes' 30 s grace period. Go when asked.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
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
    interaction = Interaction(speaker, args.here.rstrip("/"), CLIP)
    watcher = None
    if camera:
        watcher = make_watcher(camera, interaction)
        threading.Thread(target=keep_watching, args=(camera, watcher),
                         daemon=True).start()
    server = serve(speaker, args.port, args.here.rstrip("/"), CLIP, camera,
                   watcher, interaction)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
