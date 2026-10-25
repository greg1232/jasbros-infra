"""./jasbros doctor — does this laptop have what the other commands need."""

import json
import shutil
import subprocess

from . import inventory, scan
from .common import KUBECONFIG, VENV, say

# The cluster's Kubernetes minor version, from infra/gmk/group_vars.
# kubectl supports one minor version either side of the server.
SERVER_MINOR = 36


def doctor(args) -> int:
    problems = 0

    def check(ok: bool, good: str, bad: str) -> None:
        nonlocal problems
        say(("  ok    " if ok else "  FIX   ") + (good if ok else bad))
        problems += not ok

    for tool in ("ssh", "kubectl", "helm"):
        check(bool(shutil.which(tool)), tool, f"{tool}: brew install {tool}")

    if shutil.which("kubectl"):
        out = subprocess.run(["kubectl", "version", "--client", "-o", "json"],
                             capture_output=True, text=True).stdout
        minor = int(json.loads(out)["clientVersion"]["minor"].rstrip("+"))
        check(abs(minor - SERVER_MINOR) <= 1, f"kubectl 1.{minor}",
              f"kubectl 1.{minor} is too far from the cluster's 1.{SERVER_MINOR}"
              f" — brew upgrade kubectl")

    check((VENV / "bin" / "ansible-playbook").exists(), "ansible (in .venv)",
          "ansible missing from .venv — delete .venv and run ./jasbros again")

    for name, values in inventory.hosts("servers").items():
        address = values["ansible_host"]
        check(scan.reachable(address), f"{name} answers at {address}",
              f"{name} doesn't answer at {address} — ./jasbros gmk find")
    check(KUBECONFIG.is_file(), f"kubeconfig {KUBECONFIG}",
          "no kubeconfig yet — ./jasbros gmk provision, or gmk kubeconfig")
    return 1 if problems else 0


def add_parser(sub) -> None:
    sub.add_parser("doctor", help="check this laptop has what it needs"
                   ).set_defaults(func=doctor)
