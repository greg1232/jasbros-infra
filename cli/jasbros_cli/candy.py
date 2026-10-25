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
from .common import INFRA, KUBECONFIG, VENV, fail, kube_env, need, run, say

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
    what = answer.get("url") or repr(answer.get("text", ""))
    say(f"{answer['speaker']}: {answer['said']}  ({what})")
    return 0 if answer["ok"] else 1


def clip(args) -> int:
    """Get the clip, and put it in the cluster for candy watch to serve."""
    if not need("ffmpeg"):
        return 1
    if args.fresh or not CLIP.is_file():
        code = download(args)
        if code:
            return code
    else:
        say(f"using {CLIP.relative_to(INFRA.parent)} (--fresh to download "
            f"it again)")
    if args.play and shutil.which("afplay"):
        run(["afplay", str(CLIP)])
    return 0 if args.no_upload else upload()


def upload() -> int:
    """The clip, as FLAC, into a ConfigMap the pod mounts at /clip.

    Made here from the file on this laptop rather than by the chart, so the
    clip never passes through git. FLAC because a ConfigMap holds 1 MiB and
    the WAV is just over; FLAC is lossless and about half the size.
    """
    if not KUBECONFIG.is_file():
        return fail("no kubeconfig — ./jasbros gmk kubeconfig")
    with tempfile.TemporaryDirectory() as tmp:
        flac = Path(tmp) / "home-alone.flac"
        code = run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(CLIP),
                    "-compression_level", "12", str(flac)])
        if code:
            return fail("couldn't make the FLAC")
        size = flac.stat().st_size
        if size > 1000 * 1024:
            return fail(f"the FLAC is {size // 1024} KB — a ConfigMap holds "
                        f"1 MiB. Cut a shorter stretch with --start/--end.")
        made = subprocess.run(
            ["kubectl", "create", "configmap", f"{APP.name}-clip",
             "--namespace", APP.namespace, f"--from-file={flac}",
             "--dry-run=client", "--output=yaml"],
            env=kube_env(), capture_output=True, text=True)
        if made.returncode:
            return fail(made.stderr.strip())
        # Server-side: a client-side apply copies the whole object into an
        # annotation, and annotations stop at 256 KB.
        code = subprocess.run(
            ["kubectl", "apply", "--server-side", "--field-manager=jasbros",
             "-f", "-"], input=made.stdout, env=kube_env(),
            text=True).returncode
    if code:
        return fail("couldn't put the clip in the cluster")
    say(f"clip in the cluster ({size // 1024} KB); restarting candy watch so "
        f"it sees it now")
    return run(["kubectl", "rollout", "restart", f"deployment/{APP.name}",
                "--namespace", APP.namespace], env=kube_env()) or run(
        ["kubectl", "rollout", "status", f"deployment/{APP.name}",
         "--namespace", APP.namespace, "--timeout=120s"], env=kube_env())


def download(args) -> int:
    """Download the clip's audio, cut it, and make it a WAV a speaker plays."""
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

    p = commands.add_parser("clip", help="get the Home Alone clip and put "
                            "it in the cluster")
    p.add_argument("url", nargs="?", default=CLIP_URL)
    p.add_argument("--start", default=CLIP_START)
    p.add_argument("--end", default=CLIP_END)
    p.add_argument("--play", action="store_true",
                   help="play it here too, to check it")
    p.add_argument("--fresh", action="store_true",
                   help="download it again even if it's here")
    p.add_argument("--no-upload", action="store_true",
                   help="just the file here, not the cluster")
    p.set_defaults(func=clip)

    commands.add_parser("open", help="open its page").set_defaults(func=open_)
