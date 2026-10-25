"""Make the speaker learn from what it heard today — now, not at four.

    ./relearn.sh              label today's firings, refit, keep if better
    ./relearn.sh --dry        say what would change, change nothing
    ./relearn.sh --force      take the new model even if it isn't better
    ./relearn.sh --log        what the timer did on its own, last time

The work happens on the Pi, because that is where the data is: the wake
word writes down every firing along with the 768 numbers it scored, which
cost nothing because the encoder pass had already happened. Fitting on
those is a second's work even on a Pi.

A systemd timer runs the same thing at four in the morning. This is the
same command, said out loud.

Why it might decide not to
--------------------------
Because it has been wrong before. The first night's retraining reported
that it caught 100% of the real firings and none of the mistakes, and the
room went on waking the speaker every twenty seconds — a model can memorise
thirty-four clips of one evening's television and learn nothing whatever
about television.

So a fifth of the labelled firings are held back, the model is fitted
without them, and both the old and the new are asked about those. If the
new one isn't better, it isn't kept. `--force` overrides that; the model it
replaces is always kept as .npz.previous either way.
"""

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET_FILE = HERE / ".deploy-target"      # written by deploy.py
REMOTE_DIR = "claude-speaker"


def target(named: str | None = None) -> str:
    import speakers
    return speakers.pick(named)


def run(pi: str, command: str) -> int:
    """Run something on the Pi, showing its output as it arrives."""
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", pi,
         f"cd {REMOTE_DIR} && {command}"]).returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry", action="store_true",
                        help="report, change nothing")
    parser.add_argument("--force", action="store_true",
                        help="take the new model even if it isn't better")
    parser.add_argument("--log", action="store_true",
                        help="what the nightly timer did, last time")
    parser.add_argument("--when", action="store_true",
                        help="when the timer next runs")
    import speakers
    speakers.add_argument(parser)
    args = parser.parse_args()
    pi = target(args.pi)

    if args.log:
        return run(pi, "true") or subprocess.run(
            ["ssh", pi, "journalctl --user-unit=claude-relearn "
             "--no-pager -n 60 -o cat"]).returncode
    if args.when:
        return subprocess.run(
            ["ssh", pi, "systemctl --user list-timers claude-relearn "
             "--no-pager"]).returncode

    print(f"Asking {pi} to learn from today...\n")
    if args.dry:
        return run(pi, ".venv/bin/python train/relearn.py --dry")
    if args.force:
        code = run(pi, ".venv/bin/python train/relearn.py")
        if code == 0:
            subprocess.run(["ssh", pi,
                            "systemctl --user restart claude-speaker"])
        return code
    return run(pi, ".venv/bin/python train/relearn.py --nightly")


if __name__ == "__main__":
    raise SystemExit(main())
