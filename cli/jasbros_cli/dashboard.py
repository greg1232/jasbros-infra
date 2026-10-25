"""./jasbros dashboard — the speaker fleet dashboard on gmk.

See docs/speaker-fleet-dashboard.md. The image is built here on the laptop
for gmk's amd64 and pushed to Docker Hub (decision D15); the chart is
installed with the laptop's helm against gmk's cluster.
"""

import hashlib
import json
import subprocess
import tempfile
import webbrowser

from . import inventory
from .common import INFRA, KUBECONFIG, fail, kube_env, need, run, say

SPEAKER = INFRA / "claude-speaker"
SOURCE = SPEAKER / "fleet-dashboard"
CHART = SPEAKER / "charts" / "speaker-fleet-dashboard"
IMAGE = "docker.io/gdiamos/speaker-fleet-dashboard"
RELEASE = "speaker-fleet-dashboard"
NAMESPACE = "claude-speaker"
PATH = "/speakers"
FILES = ("Dockerfile", "fleet.py", "page.html")


def tag() -> str:
    """A hash of what goes into the image.

    The same code always gets the same tag, so deploying twice changes
    nothing — and a change nobody has committed yet still gets a new one,
    which a git commit hash would not.
    """
    digest = hashlib.sha256()
    for name in FILES:
        digest.update(name.encode() + b"\0" + (SOURCE / name).read_bytes())
    return digest.hexdigest()[:12]


def _pushed(image: str) -> bool:
    return subprocess.run(["docker", "manifest", "inspect", image],
                          capture_output=True).returncode == 0


def build(args) -> int:
    if not need("docker"):
        return 1
    image = f"{IMAGE}:{tag()}"
    if not args.force and _pushed(image):
        say(f"{image} is already on Docker Hub — nothing to build")
        return 0
    code = run(["python3", "-m", "unittest", "discover", "-s", "tests"],
               cwd=SOURCE)
    if code:
        return fail("the tests failed, so nothing was built")
    return run(["docker", "buildx", "build", "--platform", "linux/amd64",
                "--tag", image, "--push", str(SOURCE)])


def values() -> dict:
    """The speakers, from the inventory — one list, not two."""
    speakers = [{"name": name,
                 "url": f"http://{v['ansible_host']}:{v.get('dashboard_port', 8080)}"}
                for name, v in inventory.hosts("speakers").items()]
    return {"speakers": speakers,
            "rescan": {"subnet": str(inventory.lan_subnet())}}


def deploy(args) -> int:
    if not (need("helm") and KUBECONFIG.is_file()):
        return fail("needs helm and a kubeconfig — ./jasbros doctor")
    if build(args):
        return 1
    with tempfile.NamedTemporaryFile("w", suffix=".json") as rendered:
        json.dump(values(), rendered)
        rendered.flush()
        command = ["helm", "upgrade", "--install", RELEASE, str(CHART),
                   "--namespace", NAMESPACE, "--create-namespace",
                   "--values", rendered.name, "--set", f"image.tag={tag()}",
                   "--wait", "--timeout", "3m"]
        if args.dry_run:
            command += ["--dry-run=server"]
        code = run(command, env=kube_env())
    if code == 0 and not args.dry_run:
        say(f"up at {url()}")
    return code


def url() -> str:
    _, gmk = inventory.pick("gmk", "servers")
    return f"http://{gmk['ansible_host']}{PATH}"


def open_(args) -> int:
    webbrowser.open(url())
    say(url())
    return 0


def add_parser(sub) -> None:
    parser = sub.add_parser("dashboard", help="the speaker fleet dashboard")
    commands = parser.add_subparsers(dest="dashboard_command", required=True)
    p = commands.add_parser("build", help="test, build for amd64, push")
    p.add_argument("--force", action="store_true",
                   help="build even if this tag is already pushed")
    p.set_defaults(func=build)
    p = commands.add_parser("deploy", help="build if needed, helm upgrade")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true",
                   help="ask the cluster what would change, change nothing")
    p.set_defaults(func=deploy)
    p = commands.add_parser("open", help="open it in a browser")
    p.set_defaults(func=open_)
