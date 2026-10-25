"""./jasbros candy — candy watch, the camera on the candy box.

See docs/candy-watch.md.
"""

import json
import shutil
import subprocess
import tempfile
import urllib.request
import webbrowser
from pathlib import Path

from . import apps
from .common import INFRA, VENV, fail, need, run, say

HERE = INFRA / "candy-watch"
SPEAKER = "pi4"

# The scene in Home Alone where Kevin plays the gangster film. Your own
# copy of its audio is downloaded on request, kept in clip/ (gitignored),
# and never committed: the repo is public and the film is not.
CLIP_URL = "https://www.youtube.com/watch?v=hj_XtWtOOzA"
CLIP_START, CLIP_END = "1:21", "1:56"
CLIP = HERE / "clip" / "home-alone.wav"

APP = apps.App(
    name="candy-watch", namespace="candy-watch",
    source=HERE / "app", chart=HERE / "charts" / "candy-watch",
    files=("Dockerfile", "candy_watch.py"), path="/candy",
    values=lambda: {"speaker": {"name": SPEAKER,
                                "url": apps.speaker_url(SPEAKER)}})


def say_(args) -> int:
    """Make the speaker talk, through the pod — the whole path, end to end."""
    body = {"line": "gangster"} if args.gangster else {"text": " ".join(args.text)}
    if not (args.gangster or args.text):
        return fail("say what? — ./jasbros candy say hello, or --gangster")
    request = urllib.request.Request(
        APP.url() + "/api/say", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            answer = json.loads(reply.read())
    except Exception as error:
        return fail(f"couldn't reach candy watch at {APP.url()}: {error}")
    say(f"{answer['speaker']}: {answer['said']}  ({answer.get('text', '')!r})")
    return 0 if answer["ok"] else 1


def clip(args) -> int:
    """Download the clip's audio, cut it, and make it a WAV a speaker plays."""
    if not need("ffmpeg"):
        return 1
    ytdlp = VENV / "bin" / "yt-dlp"
    if not ytdlp.exists():
        return fail("yt-dlp is missing from .venv — delete .venv and run "
                    "./jasbros again")
    CLIP.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.%(ext)s"
        code = run([str(ytdlp), "--quiet", "--progress", "--no-playlist",
                    "--format", "bestaudio", "--extract-audio",
                    "--download-sections", f"*{args.start}-{args.end}",
                    "--force-keyframes-at-cuts",
                    "--ffmpeg-location", shutil.which("ffmpeg"),
                    "--output", str(raw), args.url])
        if code:
            return fail("the download failed — see above")
        got = next(Path(tmp).glob("raw.*"))
        # 16 kHz mono: what the speakers' microphone array plays, so the Pi
        # has nothing to convert.
        code = run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(got),
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                    str(CLIP)])
        if code:
            return fail("couldn't convert the audio")
    length = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(CLIP)], capture_output=True, text=True).stdout
    say(f"{CLIP} — {float(length or 0):.1f} s, gitignored")
    if args.play and shutil.which("afplay"):
        run(["afplay", str(CLIP)])
    return 0


def open_(args) -> int:
    webbrowser.open(APP.url())
    say(APP.url())
    return 0


def add_parser(sub) -> None:
    parser = sub.add_parser("candy", help="candy watch: the candy box camera")
    commands = parser.add_subparsers(dest="candy_command", required=True)
    apps.add_build_deploy(commands, APP)

    p = commands.add_parser("say", help=f"make {SPEAKER} talk, through the pod")
    p.add_argument("text", nargs="*")
    p.add_argument("--gangster", action="store_true",
                   help="the Home Alone line, in the speaker's voice")
    p.set_defaults(func=say_)

    p = commands.add_parser("clip", help="download the Home Alone clip "
                            f"(to {CLIP.relative_to(INFRA.parent)})")
    p.add_argument("url", nargs="?", default=CLIP_URL)
    p.add_argument("--start", default=CLIP_START)
    p.add_argument("--end", default=CLIP_END)
    p.add_argument("--play", action="store_true",
                   help="play it here afterwards, to check it")
    p.set_defaults(func=clip)

    commands.add_parser("open", help="open its page").set_defaults(func=open_)
