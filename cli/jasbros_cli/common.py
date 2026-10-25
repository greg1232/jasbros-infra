"""What every command needs: where things are, and how to run them."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENV = ROOT / ".venv"
INFRA = ROOT / "infra"
INVENTORY = INFRA / "inventory.yaml"
GMK = INFRA / "gmk"
KUBECONFIG = Path.home() / ".kube" / "jasbros-gmk.yaml"


def say(message: str) -> None:
    print(f"[jasbros] {message}", file=sys.stderr, flush=True)


def fail(message: str) -> int:
    say(message)
    return 1


def run(command: list[str], **kwargs) -> int:
    """Run a command where the person can see it, and return its exit code."""
    say("$ " + " ".join(command))
    return subprocess.run(command, **kwargs).returncode


def venv_tool(name: str) -> str:
    """A tool installed in the CLI's own virtualenv, like ansible-playbook."""
    return str(VENV / "bin" / name)


def need(tool: str) -> str | None:
    """The path to a tool on the laptop, or None with a note on how to get it."""
    found = shutil.which(tool)
    if not found:
        say(f"{tool} is not installed — brew install {tool}")
    return found


def ansible_env() -> dict:
    env = dict(os.environ)
    env["ANSIBLE_CONFIG"] = str(GMK / "ansible.cfg")
    env["ANSIBLE_COLLECTIONS_PATH"] = str(VENV / "collections")
    return env


def kube_env() -> dict:
    env = dict(os.environ)
    env["KUBECONFIG"] = str(KUBECONFIG)
    return env
