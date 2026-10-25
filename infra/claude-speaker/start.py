"""Start the Claude Speaker.

    ./start.sh                 start it, or restart it if it's already going
    ./start.sh --stop          stop it
    ./start.sh --status        say whether it's running, and where its log is
    ./start.sh --foreground    run it in this terminal instead, to watch it
    ./start.sh --text          type questions instead of speaking them
    ./start.sh --install-only  build the environment and stop (deploy.py uses this)

Everything here goes through systemd. There is no second way to run the
speaker, because two of them do not share a microphone array — playing and
listening are the same piece of hardware and it allows one stream, so the
second one fails or quietly steals it from the first.

The first run takes a couple of minutes: it builds a private Python folder
and downloads the speech models. After that it starts in seconds.

Runs on the system Python and only the standard library, because its first
job is to build the virtualenv that everything else needs.
"""

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
PYTHON = VENV / "bin" / "python"
STAMP = VENV / ".installed"
KEY_URL = "https://console.anthropic.com/settings/keys"
NAME = "claude-speaker"
# Under the home directory, not the project directory. systemd looks in
# ~/.config/systemd/user, and looking in the wrong place made the script
# announce there was no service while one was running perfectly well.
UNIT = Path.home() / ".config" / "systemd" / "user" / f"{NAME}.service"


def unit_installed() -> bool:
    return UNIT.is_file()


def install_unit() -> None:
    """Write the service, if this machine hasn't got it yet.

    The text comes from deploy.py so there is one definition of what the
    service is, rather than two that drift.
    """
    import deploy

    UNIT.parent.mkdir(parents=True, exist_ok=True)
    UNIT.write_text(deploy.SERVICE.format(dir=HERE.name))
    systemctl("daemon-reload")
    systemctl("enable", NAME)
    print(f"Installed {UNIT}")


def systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args],
                          capture_output=True, text=True)


def is_active() -> str:
    return systemctl("is-active", NAME).stdout.strip() or "unknown"


def speaker_pid() -> int | None:
    """The running speaker's pid, according to systemd."""
    said = systemctl("show", NAME, "-p", "MainPID", "--value").stdout.strip()
    return int(said) if said.isdigit() and said != "0" else None


def stop() -> int:
    if not unit_installed():
        print("Not running (no service installed).")
        return 0
    if is_active() != "active":
        print("Not running.")
        return 0
    systemctl("stop", NAME)
    print("Stopped." if is_active() != "active" else "Wouldn't stop.")
    return 0


def status() -> int:
    if not unit_installed():
        print("No service installed. Run ./start.sh to install and start it.")
        return 1
    pid = speaker_pid()
    print(f"{is_active()}" + (f", pid {pid}" if pid else ""))
    print(f"  log:     journalctl --user-unit={NAME} -f")
    print("  stop:    ./start.sh --stop")
    print("  restart: ./start.sh")
    return 0


def build_environment() -> None:
    """Make the virtualenv and install what's needed, if anything changed."""
    if not PYTHON.is_file():
        print("Setting up Python for the first time (this takes a minute)...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        subprocess.run([str(VENV / "bin" / "pip"), "install", "--quiet",
                        "--upgrade", "pip"], check=True)

    requirements = HERE / "requirements.txt"
    if STAMP.is_file() and STAMP.stat().st_mtime >= requirements.stat().st_mtime:
        return

    print("Installing the packages it needs...")
    pip = str(VENV / "bin" / "pip")
    subprocess.run([pip, "install", "--quiet", "-r", str(requirements)],
                   check=True)

    # The wake word, on Linux. It goes in by hand and without its
    # dependencies, because openWakeWord insists on tflite-runtime there and
    # there's no build of that for recent Pythons on Arm. We don't use it —
    # wake.py loads the ONNX build — and the packages it really needs are
    # in requirements.txt. Without --no-deps, pip refuses to install
    # anything at all on a Raspberry Pi.
    if sys.platform.startswith("linux"):
        subprocess.run([pip, "install", "--quiet", "--no-deps",
                        "openwakeword>=0.6.0"], check=True)

    STAMP.touch()


def check_settings() -> None:
    env = HERE / ".env"
    if not env.is_file():
        raise SystemExit(
            "\nThere's no .env file yet, so it doesn't know your API key.\n"
            "Make one and put your key in it:\n\n"
            "    cp .env.example .env\n\n"
            f"Get a key at {KEY_URL}")
    # Read it without printing it, so the key never shows up on screen.
    if not re.search(r"^\s*ANTHROPIC_API_KEY=.+", env.read_text(), re.M):
        raise SystemExit(
            "\nANTHROPIC_API_KEY is empty in .env.\n"
            "Open .env and paste your key after the = sign.\n"
            f"Get one at {KEY_URL}")


def start(extra: list[str]) -> int:
    """Start the speaker, which means starting the service.

    There was once a second way — this script would run src/main.py itself
    under nohup, and then had to detect the service and warn you off,
    because two speakers do not share a microphone array: playing and
    listening are the same piece of hardware and it allows one stream, so
    the second one fails or quietly steals it from the first.

    That whole apparatus existed to manage a situation nothing needs. The
    service restarts on failure, starts at boot, and keeps its log in the
    journal, which the loose process never did.
    """
    if extra:
        # --text and --foreground want a terminal, so they cannot be the
        # service. Stop it first rather than fight it for the microphone.
        if unit_installed() and is_active() == "active":
            print("Stopping the service first — it holds the microphone.")
            systemctl("stop", NAME)
        print()
        # main.py knows --text; --foreground only means "not the service".
        pass_on = [a for a in extra if a != "--foreground"]
        return subprocess.run([str(PYTHON), "src/main.py", *pass_on],
                              cwd=HERE).returncode

    if not unit_installed():
        install_unit()

    systemctl("restart", NAME)
    if is_active() != "active":
        print("It wouldn't start. The last of the log:\n")
        print(subprocess.run(
            ["journalctl", "--user-unit", NAME, "-n", "20", "--no-pager",
             "-o", "cat"], capture_output=True, text=True).stdout)
        return 1

    pid = speaker_pid()
    print(f"Started{f' as pid {pid}' if pid else ''}. "
          "It takes about half a minute to be ready.\n")
    print(f"  watch it:  journalctl --user-unit={NAME} -f")
    print("  stop it:   ./start.sh --stop")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-f", "--foreground", action="store_true")
    parser.add_argument("--text", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--install-only", action="store_true")
    args = parser.parse_args()

    if args.stop:
        return stop()
    if args.status:
        return status()

    build_environment()
    check_settings()

    if args.install_only:
        print("Ready.")
        return 0

    # Typing questions is a conversation, so it has to stay in front of you.
    if args.text:
        return start(["--text"])
    if args.foreground:
        return start(["--foreground"])
    return start([])


if __name__ == "__main__":
    os.chdir(HERE)
    sys.exit(main())
