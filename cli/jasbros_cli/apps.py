"""What every app on gmk's cluster has in common: build, push, deploy.

An app is a folder with a Dockerfile, a Helm chart, and a list of the files
that go into its image. Images are built here for gmk's amd64 and pushed to
Docker Hub (decision D15); charts are installed with the laptop's helm.
"""

import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import inventory
from .common import KUBECONFIG, fail, kube_env, need, run, say

REGISTRY = "docker.io/gdiamos"


@dataclass
class App:
    name: str                       # the image, the release, and the Service
    namespace: str
    source: Path                    # the docker build context
    chart: Path
    files: tuple[str, ...]          # what goes in the image, for the tag
    path: str                       # where the ingress serves it on gmk
    values: Callable[[], dict] = field(default=lambda: {})

    @property
    def image(self) -> str:
        return f"{REGISTRY}/{self.name}"

    def tag(self) -> str:
        """A hash of what goes into the image.

        The same code always gets the same tag, so deploying twice changes
        nothing — and a change nobody has committed yet still gets a new
        one, which a git commit hash would not.
        """
        digest = hashlib.sha256()
        for name in self.files:
            digest.update(name.encode() + b"\0"
                          + (self.source / name).read_bytes())
        return digest.hexdigest()[:12]

    def pushed(self) -> bool:
        return subprocess.run(
            ["docker", "manifest", "inspect", f"{self.image}:{self.tag()}"],
            capture_output=True).returncode == 0

    def build(self, force: bool = False) -> int:
        if not need("docker"):
            return 1
        image = f"{self.image}:{self.tag()}"
        if not force and self.pushed():
            say(f"{image} is already on Docker Hub — nothing to build")
            return 0
        if (self.source / "tests").is_dir():
            code = run(["python3", "-m", "unittest", "discover", "-s",
                        "tests"], cwd=self.source)
            if code:
                return fail("the tests failed, so nothing was built")
        return run(["docker", "buildx", "build", "--platform", "linux/amd64",
                    "--tag", image, "--push", str(self.source)])

    def deploy(self, force: bool = False, dry_run: bool = False) -> int:
        if not (need("helm") and KUBECONFIG.is_file()):
            return fail("needs helm and a kubeconfig — ./jasbros doctor")
        if self.build(force):
            return 1
        with tempfile.NamedTemporaryFile("w", suffix=".json") as rendered:
            json.dump(self.values(), rendered)
            rendered.flush()
            command = ["helm", "upgrade", "--install", self.name,
                       str(self.chart), "--namespace", self.namespace,
                       "--create-namespace", "--values", rendered.name,
                       "--set", f"image.tag={self.tag()}",
                       "--wait", "--timeout", "3m"]
            if dry_run:
                command += ["--dry-run=server"]
            code = run(command, env=kube_env())
        if code == 0 and not dry_run:
            say(f"up at {self.url()}")
        return code

    def url(self) -> str:
        _, gmk = inventory.pick("gmk", "servers")
        return f"http://{gmk['ansible_host']}{self.path}"


def speaker_url(name: str) -> str:
    """Where a speaker's own API is, from the inventory."""
    _, values = inventory.pick(name, "speakers")
    return f"http://{values['ansible_host']}:{values.get('dashboard_port', 8080)}"


def add_build_deploy(commands, app: App) -> None:
    """The `build` and `deploy` subcommands every app has."""
    p = commands.add_parser("build", help="test, build for amd64, push")
    p.add_argument("--force", action="store_true",
                   help="build even if this tag is already pushed")
    p.set_defaults(func=lambda args: app.build(args.force))
    p = commands.add_parser("deploy", help="build if needed, helm upgrade")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true",
                   help="ask the cluster what would change, change nothing")
    p.set_defaults(func=lambda args: app.deploy(args.force, args.dry_run))
