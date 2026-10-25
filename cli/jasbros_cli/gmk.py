"""./jasbros gmk — provisioning gmk, and getting at its cluster."""

import os
import re
import subprocess
import threading
from pathlib import Path

import yaml

from . import inventory, scan
from .common import (GMK, INVENTORY, KUBECONFIG, ansible_env, fail, kube_env,
                     need, run, say, venv_tool)

# Where the sudo password can live so a run needn't ask. Never in the repo.
DEFAULT_PASS_FILE = Path.home() / ".config" / "jasbros" / "gmk-become-pass"
CONTEXT = "jasbros-gmk"


def gmk() -> tuple[str, dict]:
    return inventory.pick("gmk", "servers")


def _become_args() -> list[str]:
    named = os.environ.get("JASBROS_BECOME_PASS_FILE")
    path = Path(named).expanduser() if named else DEFAULT_PASS_FILE
    if path.is_file():
        if path.stat().st_mode & 0o077:
            raise SystemExit(f"[jasbros] {path} is readable by others — "
                             f"chmod 600 {path}")
        return ["--become-password-file", str(path)]
    return ["--ask-become-pass"]


def _reachable_or_say() -> bool:
    name, values = gmk()
    if scan.reachable(values["ansible_host"]):
        return True
    say(f"{name} doesn't answer at {values['ansible_host']} — it may have "
        f"moved. Find it with:  ./jasbros gmk find --update")
    return False


def follow_apt(target: str) -> subprocess.Popen:
    """Print each package gmk installs, while it installs it.

    Ansible says nothing during a step, and installing a desktop is one
    step that takes ten minutes. dpkg's own log says what it is doing; it
    is only written to while packages are installed, so the rest of a run
    stays quiet.
    """
    follower = subprocess.Popen(
        ["ssh", "-n", "-o", "BatchMode=yes", target,
         "tail -n0 -F /var/log/dpkg.log 2>/dev/null"],
        # No stdin: ssh would make the terminal's non-blocking, and the
        # ansible-playbook started after it refuses to run on that.
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True)

    # A big install unpacks everything first, then sets everything up —
    # and the second half is as long as the first, so both are shown.
    doing = {"install": "unpack", "upgrade": "upgrade",
             "configure": "set up", "remove": "remove"}

    def show() -> None:
        for line in follower.stdout:
            parts = line.split()
            # 2026-10-25 19:14:56 install libfoo:amd64 <none> 1.3-3build2
            # 2026-10-25 19:16:19 configure libfoo:amd64 1.3-3build2 <none>
            if len(parts) >= 5 and parts[2] in doing:
                package = parts[3].split(":")[0]
                print(f"[gmk apt] {parts[1]}  {doing[parts[2]]:7} {package}",
                      flush=True)

    threading.Thread(target=show, daemon=True).start()
    return follower


def _recap_changed(output: str) -> int:
    return sum(int(n) for n in re.findall(r"\bchanged=(\d+)", output))


def provision(args) -> int:
    if not _reachable_or_say():
        return 1
    command = [venv_tool("ansible-playbook"), "-i", str(INVENTORY),
               str(GMK / "site.yml"), *_become_args()]
    if args.check:
        command += ["--check", "--diff"]
    if args.tags:
        command += ["--tags", args.tags]
    for extra in args.extra or []:
        command += ["-e", extra]
    if args.verbose:
        command += ["-" + "v" * args.verbose]

    follower = None
    if not args.check and not args.quiet:
        name, values = gmk()
        follower = follow_apt(inventory.target(name, values))
    try:
        code = _run_rounds(command, args)
    finally:
        if follower:
            follower.terminate()
    if code or args.check:
        return code
    if not args.tags or "k3s" in args.tags:
        return kubeconfig(args)
    return 0


def _run_rounds(command: list[str], args) -> int:
    rounds = 2 if args.twice else 1
    for round_ in range(1, rounds + 1):
        if args.twice:
            say(f"round {round_} of 2 — the second should change nothing")
        say("$ " + " ".join(command))
        process = subprocess.Popen(command, env=ansible_env(), cwd=GMK,
                                   stdout=subprocess.PIPE, text=True)
        lines = []
        for line in process.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        if process.wait() != 0:
            return process.returncode
        if args.twice and round_ == 2 and _recap_changed("".join(lines)):
            return fail("the second run changed something — a task is not "
                        "idempotent. Look for 'changed:' above.")
    return 0


def kubeconfig(args) -> int:
    """Copy k3s's admin kubeconfig to the laptop, pointed at gmk's address.

    k3s writes it group-readable by `normal` (write-kubeconfig-group), so
    this needs no sudo. It is written to its own file and never merged
    into ~/.kube/config.
    """
    name, values = gmk()
    address = values["ansible_host"]
    got = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", inventory.target(name, values),
         "cat /etc/rancher/k3s/k3s.yaml"], capture_output=True, text=True)
    if got.returncode != 0:
        return fail(f"couldn't read the kubeconfig on {name}: "
                    f"{got.stderr.strip()} — has k3s been provisioned?")
    config = yaml.safe_load(got.stdout)
    for cluster in config["clusters"]:
        cluster["name"] = CONTEXT
        cluster["cluster"]["server"] = f"https://{address}:6443"
    for user in config["users"]:
        user["name"] = CONTEXT
    config["contexts"] = [{"name": CONTEXT, "context": {
        "cluster": CONTEXT, "user": CONTEXT}}]
    config["current-context"] = CONTEXT
    KUBECONFIG.parent.mkdir(parents=True, exist_ok=True)
    KUBECONFIG.touch(mode=0o600)
    KUBECONFIG.write_text(yaml.safe_dump(config, sort_keys=False))
    KUBECONFIG.chmod(0o600)
    say(f"kubeconfig for {address} written to {KUBECONFIG}")
    return 0


def status(args) -> int:
    if not need("kubectl"):
        return 1
    if not KUBECONFIG.is_file():
        return fail("no kubeconfig yet — ./jasbros gmk kubeconfig")
    env = kube_env()
    code = run(["kubectl", "get", "nodes", "-o", "wide"], env=env)
    run(["kubectl", "top", "node"], env=env)
    run(["kubectl", "get", "pods", "-A"], env=env)
    return code


def ssh(args) -> int:
    name, values = gmk()
    os.execvp("ssh", ["ssh", inventory.target(name, values), *args.command])


def find(args) -> int:
    return scan.update("servers", args.update)


def add_parser(sub) -> None:
    parser = sub.add_parser("gmk", help="provision gmk and reach its cluster")
    commands = parser.add_subparsers(dest="gmk_command", required=True)

    p = commands.add_parser("provision", help="run infra/gmk/site.yml")
    p.add_argument("--check", action="store_true",
                   help="dry run: show what would change, change nothing")
    p.add_argument("--tags", help="only these roles, e.g. base or k3s")
    p.add_argument("-e", "--extra", action="append",
                   help="an Ansible extra var, key=value")
    p.add_argument("--twice", action="store_true",
                   help="run again and fail if the second run changes anything")
    p.add_argument("--quiet", action="store_true",
                   help="don't show each package as gmk installs it")
    p.add_argument("-v", "--verbose", action="count", default=0)
    p.set_defaults(func=provision)

    p = commands.add_parser("kubeconfig", help=f"(re)fetch {KUBECONFIG}")
    p.set_defaults(func=kubeconfig)
    p = commands.add_parser("status", help="nodes, usage, pods")
    p.set_defaults(func=status)
    p = commands.add_parser("ssh", help="ssh to gmk")
    p.add_argument("command", nargs="*")
    p.set_defaults(func=ssh)
    p = commands.add_parser("find", help="scan the LAN for gmk")
    p.add_argument("--update", action="store_true",
                   help="write the address it finds into the inventory")
    p.set_defaults(func=find)
