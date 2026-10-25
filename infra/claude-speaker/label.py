"""Listen to what woke the speaker, and say whether it was right.

    ./label.sh

Fetches the clips off the Pi, opens a page in your browser, and plays them
one at a time. One key each: **y** if that really was somebody saying the
wake word, **n** if it wasn't. Your answers go back to the Pi and are what
the nightly retraining learns from.

Why this exists
---------------
The speaker labels its own firings, and it is good at the easy half: a
firing followed by silence was the television, one followed by a question
that got answered was real. The hard half it guesses at, and the guesses
matter — the first retrained model, measured on firings held back from the
fitting, caught 85% of the real ones and fired on 43% of the mistakes,
which is very much worse than the model it would have replaced.

A person listening to thirty clips fixes that in five minutes. Labels from
here are appended after the automatic ones, so they win.

There is nothing to install and nothing runs on the Pi. It copies the
clips down, serves them from this machine, and appends your answers when
you're finished.
"""

import argparse
import datetime
import http.server
import io
import json
import random
import shutil
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET_FILE = HERE / ".deploy-target"
REMOTE = "claude-speaker/state/wakes"
CACHE = HERE / "state" / "labelling"
KNOWN = CACHE / "known"          # clips that really are the wake word
# Where known-good wake words come from, best first. Enrolled clips are two
# second windows recorded in this room through this array, so they sound
# like the rest; the training recordings are trimmed phrases and stand out
# slightly, which is why they are the fallback.
KNOWN_FROM = ("claude-speaker/state/enrolled", None)
# Roughly one in six, which is enough to notice if somebody has stopped
# listening without wasting much of their time.
KNOWN_SHARE = 0.16
# Where to serve the page. If something else already has this port — and
# on a developer's laptop something usually does — take whatever is free.
PORT = 8899
# As many as somebody will sit through in one go. --all for the lot.
MOST = 60

_answers: dict[int, int] = {}
_clips: list[dict] = []


def target(named: str | None = None) -> str:
    import speakers
    return speakers.pick(named)


def fetch(pi: str) -> None:
    """Copy the clips and the index down from the Pi."""
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"Fetching clips from {pi}...")
    done = subprocess.run(
        ["rsync", "-az", "--delete", "--exclude", "known/",
         f"{pi}:{REMOTE}/", f"{CACHE}/"],
        capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"Couldn't fetch:\n{done.stderr.strip()}")

    KNOWN.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-az", f"{pi}:{KNOWN_FROM[0]}/", f"{KNOWN}/"],
                   capture_output=True, text=True)
    if not any(KNOWN.glob("*.wav")):
        for clip in sorted((HERE / "train" / "real" / "hey_claude")
                           .glob("*.wav")):
            shutil.copy(clip, KNOWN / clip.name)


def load(everything: bool) -> list[dict]:
    """The firings that still have their audio, newest first."""
    index = CACHE / "wakes.jsonl"
    if not index.exists():
        return []

    rows: dict[int, dict] = {}
    for line in index.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if "n" in row:
            rows.setdefault(row["n"], {}).update(row)

    out = []
    for number, row in sorted(rows.items(), reverse=True):
        clip = CACHE / "audio" / f"{number:06d}.wav"
        if not clip.exists() or row.get("taught"):
            continue
        # Nothing to listen to. Firings logged before the buffer-aliasing
        # bug was fixed are all like this — see src/whisper_wake.py.
        if peak_of(clip) == 0:
            continue
        if not everything and row.get("by") == "person":
            continue
        out.append({
            "rank": _usefulness(row),
            "n": number,
            "score": row.get("score", 0),
            "at": (row.get("at") or "")[:16].replace("T", " "),
            "near": bool(row.get("near")),
            "repeated": bool(row.get("repeated")),
            "heard": row.get("heard") or "",
            "window": row.get("window") or "",
            "guess": row.get("label"),
            "why": row.get("why", ""),
        })

    # Most useful first, and only as many as a person will actually sit
    # through. Four hundred clips newest-first means half an hour of the
    # room being quiet before you reach anything that decides the model.
    out.sort(key=lambda c: c["rank"])
    if not everything:
        out = out[:MOST]

    # Then shuffled. Sorted by kind, you get a run of twenty television
    # clips and start answering "no" without listening, which is worse
    # than not labelling at all.
    out += _known(max(2, int(len(out) * KNOWN_SHARE / (1 - KNOWN_SHARE))))
    random.shuffle(out)
    return out


def _known(how_many: int) -> list[dict]:
    """Clips that really are the wake word, mixed in unannounced.

    Two jobs. They tell somebody who has never done this what a real one
    sounds like through this microphone, which is not obvious — the array
    compresses hard and a real wake word can be quieter than a television.
    And they say afterwards whether the answers can be trusted, because
    somebody twenty minutes in and clicking quickly will start missing
    them, and there is no other way to know that happened.
    """
    clips = sorted(KNOWN.glob("*.wav"))
    if not clips:
        return []
    random.shuffle(clips)
    return [{
        "n": -(i + 1),               # negative, so it is never sent back
        "known": clip.name,
        "rank": 0,
        "score": 0.0,
        "at": "",
        "near": False,
        "repeated": False,
        "heard": "",
        "window": "",
        "guess": None,
        "why": "",
    } for i, clip in enumerate(clips[:how_many])]


# How much a person's answer is worth on each kind of clip, lowest first.
def _usefulness(row: dict) -> int:
    """What to put in front of somebody first.

    A firing nobody could label automatically is worth most: it is the
    case the machine could not decide and the one the model will be fitted
    on either way. A near miss that somebody repeated seconds later is
    next, because those are the recall failures and there is no other way
    to find them. Ordinary near misses are the room being a room.
    """
    if not row.get("near"):
        # A firing the machine believes was real comes first. Of two
        # hundred and three answers a person has given, two hundred and
        # two were "no" — which measures false wakes and says nothing at
        # all about recall. Confirming a likely yes is the most valuable
        # thing anybody can do here, and there are only a handful.
        if row.get("label") == 1:
            return 0
        return 1 if row.get("label") is None else 2
    return 3 if row.get("repeated") else 4


def marked() -> tuple[int, int]:
    """How the known wake words were answered: (right, asked)."""
    known = {n: label for n, label in _answers.items() if n < 0}
    return sum(known.values()), len(known)


def already() -> tuple[int, int]:
    """How many yes and no answers a person has given before today."""
    index = CACHE / "wakes.jsonl"
    if not index.exists():
        return 0, 0
    rows: dict[int, dict] = {}
    for line in index.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if "n" in row:
            rows.setdefault(row["n"], {}).update(row)
    mine = [r for r in rows.values() if r.get("by") == "person"]
    return (sum(1 for r in mine if r.get("label") == 1),
            sum(1 for r in mine if r.get("label") == 0))


def push(pi: str) -> int:
    """Send the answers back, as lines appended to the Pi's log.

    The known wake words have negative numbers and are dropped here. They
    were never rows in the log and must not become rows in it.
    """
    real = {n: label for n, label in _answers.items() if n >= 0}
    if not real:
        return 0
    # When the answer was given, which is not when the speaker fired.
    # A person labelling a fortnight of old clips is the most valuable
    # thing that happens to this model, and relearn.py can only see it as
    # new data if the label says when it arrived. Same format and same
    # time zone as the Pi writes, so the two can be compared as strings.
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    lines = "\n".join(json.dumps({
        "n": number, "label": label, "by": "person",
        "why": "listened to by a person", "labelled_at": now,
    }) for number, label in sorted(real.items()))
    done = subprocess.run(
        ["ssh", pi, f"cat >> {REMOTE}/wakes.jsonl"],
        input=lines + "\n", text=True, capture_output=True)
    if done.returncode != 0:
        print(f"Couldn't save to the Pi:\n{done.stderr.strip()}")
        return 0
    return len(real)


def peak_of(clip: Path) -> int:
    """How loud the loudest sample in a clip is, 0 to 32767."""
    import wave
    try:
        with wave.open(str(clip), "rb") as handle:
            raw = handle.readframes(handle.getnframes())
    except Exception:
        return 0
    if not raw:
        return 0
    return max(abs(int.from_bytes(raw[i:i + 2], "little", signed=True))
               for i in range(0, len(raw), 2))


def loud(clip: Path) -> bytes:
    """The clip, turned up so a person can actually hear it.

    These are recordings of a room made through an array that cancels and
    compresses hard. They come off the Pi at about sixteen decibels below
    full scale, with a lot of them much quieter than that, which through
    laptop speakers is indistinguishable from nothing playing at all.
    """
    import wave

    with wave.open(str(clip), "rb") as handle:
        rate = handle.getframerate()
        width = handle.getsampwidth()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())

    peak = peak_of(clip)
    if peak and width == 2:
        gain = min(28000 / peak, 40.0)   # capped, or silence becomes hiss
        out = bytearray(len(raw))
        for i in range(0, len(raw) - 1, 2):
            sample = int(int.from_bytes(raw[i:i + 2], "little", signed=True)
                         * gain)
            sample = max(-32768, min(32767, sample))
            out[i:i + 2] = sample.to_bytes(2, "little", signed=True)
        raw = bytes(out)

    made = io.BytesIO()
    with wave.open(made, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(raw)
    return made.getvalue()


class Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            return self._send(PAGE.encode(), "text/html; charset=utf-8")
        if path == "/clips":
            return self._send(json.dumps(_clips).encode(), "application/json")
        if path.startswith("/audio/"):
            clip = CACHE / "audio" / Path(path).name
            if clip.is_file():
                return self._send(loud(clip), "audio/wav")
        if path.startswith("/known/"):
            clip = KNOWN / Path(path).name
            if clip.is_file():
                return self._send(loud(clip), "audio/wav")
        self.send_error(404)

    def do_POST(self):                                   # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        try:
            said = json.loads(self.rfile.read(length) or b"{}")
            _answers[int(said["n"])] = int(said["label"])
        except Exception:
            return self.send_error(400)
        self._send(b'{"ok":true}', "application/json")

    def _send(self, body: bytes, kind: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


PAGE = r"""<!doctype html><meta charset=utf-8>
<title>What woke the speaker</title>
<style>
:root{color-scheme:light dark;--edge:#8884}
body{font:16px/1.5 system-ui,sans-serif;margin:0;display:grid;
     place-items:center;min-height:100vh}
main{width:min(46rem,92vw);padding:2rem 0}
h1{font-size:1.1rem;font-weight:600;margin:0 0 .25rem}
.sub{opacity:.65;font-size:.9rem;margin-bottom:1.5rem}
.card{border:1px solid var(--edge);border-radius:14px;padding:1.5rem}
.meta{display:flex;gap:1rem;flex-wrap:wrap;font-size:.85rem;opacity:.7;
      margin-bottom:1rem}
.tag{border:1px solid var(--edge);border-radius:999px;padding:.1rem .6rem}
.said{font-size:1.05rem;margin:.75rem 0;min-height:1.6rem}
.said b{font-weight:600}
audio{width:100%;margin:.5rem 0 1rem}
.row{display:flex;gap:.75rem}
button{flex:1;font:inherit;padding:.9rem;border-radius:10px;cursor:pointer;
       border:1px solid var(--edge);background:transparent}
button.yes{border-color:#3a3;color:#3a3}
button.no{border-color:#c44;color:#c44}
button:hover{background:#8881}
.skip{flex:0 0 auto;opacity:.6}
.bar{height:4px;background:#8883;border-radius:2px;margin-top:1.5rem}
.bar div{height:100%;background:#8888;border-radius:2px;width:0}
.done{text-align:center;padding:3rem 1rem}
.nudge{margin-bottom:1rem}
.nudge button{width:100%}
kbd{border:1px solid var(--edge);border-radius:4px;padding:0 .35rem;
    font-size:.8em}
</style>
<main>
<h1>What woke the speaker</h1>
<div class=sub>Was that somebody saying <b>hey Claude</b>?
  <kbd>y</kbd> yes &middot; <kbd>n</kbd> no &middot;
  <kbd>space</kbd> play again &middot; <kbd>s</kbd> skip</div>
<div id=box></div>
<div class=bar><div id=bar></div></div>
</main>
<script>
let clips=[], at=0, done=0;
const box=document.getElementById('box'), bar=document.getElementById('bar');

fetch('/clips').then(r=>r.json()).then(c=>{clips=c;show()});

function show(){
  if(at>=clips.length){
    box.innerHTML='<div class="card done"><h1>That\'s all of them.</h1>'+
      '<div class=sub>'+done+' labelled. Close this and the answers go '+
      'back to the Pi.</div></div>';
    return;
  }
  const c=clips[at];
  const tags = c.known ? [] :
    [c.near?'nearly fired':'fired', 'score '+c.score.toFixed(3), c.at]
    .concat(c.repeated?['said again seconds later']:[])
    .concat(c.guess!=null?['it guessed '+(c.guess?'yes':'no')]:[]);
  box.innerHTML=
    '<div class=card><div class=meta>'+
      tags.map(t=>'<span class=tag>'+t+'</span>').join('')+
    '</div>'+
    '<audio id=a controls src="'+
      (c.known ? '/known/'+c.known : '/audio/'+String(c.n).padStart(6,'0')+'.wav')+
      '"></audio>'+
    '<div id=nudge class=nudge><button onclick="start()">'+
      'Click to start listening</button></div>'+
    '<div class=said>'+
      (c.window?'the two seconds sounded like <b>'+esc(c.window)+'</b><br>':'')+
      (c.heard?'what came next: <b>'+esc(c.heard)+'</b>':
        (c.near?'':'<i>nothing was said afterwards</i>'))+
    '</div>'+
    '<div class=row>'+
      '<button class=yes onclick="say(1)">Yes &mdash; hey Claude</button>'+
      '<button class=no onclick="say(0)">No &mdash; something else</button>'+
      '<button class=skip onclick="skip()">Skip</button>'+
    '</div></div>';
  bar.style.width=(100*at/clips.length)+'%';
  if(unlocked) play();
}
function esc(s){return s.replace(/[<>&]/g,m=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[m]))}
// Browsers refuse to play sound until you have interacted with the page,
// and refuse silently — which looks exactly like a broken audio player.
// So the first one waits for a click, and every one after it plays itself.
let unlocked=false;
function play(){
  const a=document.getElementById('a'); if(!a) return;
  a.currentTime=0;
  a.play().then(()=>{unlocked=true}).catch(()=>{
    const n=document.getElementById('nudge'); if(n) n.hidden=false;
  });
}
function start(){document.getElementById('nudge').hidden=true; play()}
function say(label){
  fetch('/',{method:'POST',body:JSON.stringify({n:clips[at].n,label})});
  done++; at++; show();
}
function skip(){at++; show()}
addEventListener('keydown',e=>{
  if(e.key==='y'||e.key==='1') say(1);
  else if(e.key==='n'||e.key==='0') say(0);
  else if(e.key==='s') skip();
  else if(e.key===' '){e.preventDefault();
    const a=document.getElementById('a'); if(a){a.currentTime=0;a.play()}}
});
</script>"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true",
                        help="include ones a person has already answered")
    parser.add_argument("--keep", action="store_true",
                        help="don't fetch again, use the clips already here")
    import speakers
    speakers.add_argument(parser)
    args = parser.parse_args()

    if not shutil.which("rsync"):
        raise SystemExit("This needs rsync, same as deploy does.")

    pi = target(args.pi)
    if not args.keep:
        fetch(pi)

    global _clips
    _clips = load(args.all)
    if not _clips:
        raise SystemExit(
            "No clips to listen to. The speaker keeps the most recent "
            f"{'400' if True else ''} — give it a day with the wake word on.")

    try:
        server = http.server.HTTPServer(("127.0.0.1", PORT), Pages)
    except OSError:
        server = http.server.HTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    where = f"http://127.0.0.1:{server.server_port}/"
    print(f"{len(_clips)} clips to listen to.\n  {where}\n"
          "  Ctrl-C when you've had enough — your answers are saved then.")
    webbrowser.open(where)

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print()
    server.shutdown()

    right, asked = marked()
    if asked:
        print(f"Of {asked} clips that really were the wake word, you said "
              f"yes to {right}. Those are\nmixed in to check the answers "
              "and are not saved — they were already known.")
        if right < asked:
            print("  The ones you missed are worth hearing again — either "
                  "they are hard,\n  or it was time to stop.")

    was_yes, was_no = already()
    mine = [label for number, label in _answers.items() if number >= 0]
    yes, no = mine.count(1), mine.count(0)

    sent = push(pi)
    if not sent:
        print("Nothing to save.")
        return 0

    print(f"Saved {sent} answer{'s' if sent != 1 else ''} to the Pi: "
          f"{yes} yes, {no} no.")
    # The yes answers are the scarce half and the interesting number. The
    # first two hundred labels here were 202 no and 1 yes, which measures
    # false wakes perfectly and says nothing at all about whether the
    # speaker hears anybody.
    print(f"  {was_yes + yes} yes and {was_no + no} no altogether.")
    if yes:
        print(f"  {yes} more example{'s' if yes != 1 else ''} of somebody "
              "actually being heard — those are the ones\n  the retraining "
              "measures recall against.")
    else:
        print("  No yes answers this time. Recall can only be measured "
              "against those,\n  so it's worth using the speaker a few "
              "times and labelling again.")
    print("\nNow teach it:  ./relearn.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
