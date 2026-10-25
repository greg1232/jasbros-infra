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

from . import apps, inventory
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
    files=("Dockerfile", "requirements.txt", "candy_watch.py", "vision.py",
           "watch.py"), path="/candy",
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


def demo(args) -> int:
    """The whole interaction on the speaker, with nobody putting it back."""
    request = urllib.request.Request(
        APP.url() + "/api/demo",
        data=json.dumps({"wait": args.wait,
                         "costume": " ".join(args.costume)}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            answer = json.loads(reply.read())
    except Exception as error:
        return fail(f"couldn't reach candy watch at {APP.url()}: {error}")
    say(f"{answer.get('speaker', '')}: {answer['said']}")
    return 0 if answer["ok"] else 1


LOGIN = Path.home() / ".config" / "jasbros" / "reolink.env"


def _login() -> dict:
    if not LOGIN.is_file():
        raise SystemExit(f"[jasbros] no camera login — put REOLINK_USER= and "
                         f"REOLINK_PASSWORD= in {LOGIN} (chmod 600)")
    if LOGIN.stat().st_mode & 0o077:
        raise SystemExit(f"[jasbros] {LOGIN} is readable by others — "
                         f"chmod 600 {LOGIN}")
    return dict(line.strip().split("=", 1) for line in LOGIN.read_text()
                .splitlines() if "=" in line)


def snapshot(address: str, login: dict) -> bytes:
    """One still from a Reolink, over its HTTP API (which must be on)."""
    def call(cmd: str, params: dict, token: str = "") -> list:
        body = json.dumps([{"cmd": cmd, "action": 0, "param": params}]).encode()
        url = f"http://{address}/api.cgi?cmd={cmd}" + (
            f"&token={token}" if token else "")
        with urllib.request.urlopen(urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}),
                timeout=10) as reply:
            return json.loads(reply.read())

    answer = call("Login", {"User": {"Version": "0",
                                     "userName": login["REOLINK_USER"],
                                     "password": login["REOLINK_PASSWORD"]}})
    if answer[0].get("code") != 0:
        # Reolinks lock the login after five failures, so this never retries.
        raise SystemExit(f"[jasbros] the camera refused the login "
                         f"({answer[0].get('error', {}).get('detail')}) — "
                         f"check {LOGIN}. Not retrying: it locks after five.")
    token = answer[0]["value"]["Token"]["name"]
    try:
        with urllib.request.urlopen(
                f"http://{address}/cgi-bin/api.cgi?cmd=Snap&channel=0"
                f"&rs=jasbros&token={token}", timeout=15) as reply:
            return reply.read()
    finally:
        call("Logout", {}, token)


def camera(args) -> int:
    """Check the camera, save a snapshot, and give candy watch the login."""
    name, values = inventory.pick(args.camera, "cameras")
    login = _login()
    picture = snapshot(values["ansible_host"], login)
    out = HERE / "clip" / f"{name}.jpg"           # gitignored, like the clip
    out.write_bytes(picture)
    say(f"{name} ({values.get('model', 'camera')}) at "
        f"{values['ansible_host']}: snapshot {len(picture) // 1024} KB → "
        f"{out.relative_to(INFRA.parent)}")
    if args.no_secret:
        return 0
    # Built here and piped in, so the password is never on a command line.
    secret = {"apiVersion": "v1", "kind": "Secret",
              "metadata": {"name": f"{APP.name}-camera",
                           "namespace": APP.namespace},
              "type": "Opaque",
              "stringData": {"REOLINK_HOST": values["ansible_host"],
                             "REOLINK_USER": login["REOLINK_USER"],
                             "REOLINK_PASSWORD": login["REOLINK_PASSWORD"]}}
    code = subprocess.run(
        ["kubectl", "apply", "--server-side", "--field-manager=jasbros",
         "-f", "-"], input=json.dumps(secret), env=kube_env(), text=True,
        capture_output=True).returncode
    if code:
        return fail("couldn't put the camera login in the cluster")
    say(f"camera login in the cluster as secret {APP.name}-camera")
    if shutil.which("open") and not args.no_open:
        run(["open", str(out)])
    return 0


def switch(on: bool) -> int:
    """Switch the watcher on (greet, count, call out) or off (just watch)."""
    request = urllib.request.Request(
        APP.url() + "/api/watch", data=json.dumps({"on": on}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            answer = json.loads(reply.read())
    except Exception as error:
        return fail(f"couldn't reach candy watch at {APP.url()}: {error}")
    say(answer["said"])
    return 0 if answer["ok"] else 1


def key(args) -> int:
    """Give candy watch the speakers' Claude API key, as a Secret."""
    env = INFRA / "claude-speaker" / ".env"
    found = [line.split("=", 1)[1].strip() for line in env.read_text()
             .splitlines() if line.startswith("ANTHROPIC_API_KEY=")] \
        if env.is_file() else []
    if not found or not found[0]:
        return fail(f"no ANTHROPIC_API_KEY in {env}")
    # Built here and piped in, so the key is never on a command line.
    secret = {"apiVersion": "v1", "kind": "Secret",
              "metadata": {"name": f"{APP.name}-claude",
                           "namespace": APP.namespace},
              "type": "Opaque", "stringData": {"ANTHROPIC_API_KEY": found[0]}}
    code = subprocess.run(
        ["kubectl", "apply", "--server-side", "--field-manager=jasbros",
         "-f", "-"], input=json.dumps(secret), env=kube_env(), text=True,
        capture_output=True).returncode
    if code:
        return fail("couldn't put the key in the cluster")
    say(f"Claude API key in the cluster as secret {APP.name}-claude; "
        f"restart candy watch to use it: ./jasbros k8s -n {APP.namespace} "
        f"rollout restart deploy/{APP.name}")
    return 0


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

    p = commands.add_parser("demo", help=f"the whole interaction on {SPEAKER}")
    p.add_argument("costume", nargs="*",
                   help="what they're dressed as, e.g. scary witch")
    p.add_argument("--wait", type=float, default=10,
                   help="seconds to wait for the candy to come back")
    p.set_defaults(func=demo)

    p = commands.add_parser("camera", help="check the camera, snapshot it, "
                            "and give candy watch its login")
    p.add_argument("camera", nargs="?", default="front-yard")
    p.add_argument("--no-secret", action="store_true",
                   help="just the snapshot")
    p.add_argument("--no-open", action="store_true",
                   help="don't open the snapshot")
    p.set_defaults(func=camera)

    commands.add_parser("on", help="start greeting trick-or-treaters"
                        ).set_defaults(func=lambda args: switch(True))
    commands.add_parser("off", help="stop greeting; just watch"
                        ).set_defaults(func=lambda args: switch(False))
    commands.add_parser("key", help="give candy watch the Claude API key "
                        "(from the speakers' .env)").set_defaults(func=key)

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
