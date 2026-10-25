"""What the speaker has been asked for and cannot do yet.

    ./wishes.sh              read them
    ./wishes.sh --clear      forget them, once they're built or dismissed
    ./wishes.sh --json       the raw list, for feeding to something else

A child asking for something the speaker cannot do is the most useful thing
that happens to this project — a feature request from the only person whose
opinion counts, in his own words, at the moment he wanted it. The speaker
writes each one down (see src/wishes.py) and this fetches the lot.

Repeats are folded together and counted, because a thing asked for five
times is five times as interesting as a thing asked for once.

The obvious next step is to hand a wish to Claude Code and let it write the
tool — `docs/tools.md` is close to a specification for exactly that, and it
managed src/scores.py from a one-paragraph description on the first try.
What stops that being automatic is that the television reaches the
speaker's transcript about fifty times an hour, so the path from "somebody
said something in this room" to "code ran" needs a person in it. This
command is that person's end of it.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

TARGET_FILE = HERE / ".deploy-target"   # written by deploy.py
REMOTE = "claude-speaker/state/wishes.jsonl"


def target(named: str | None = None) -> str:
    import speakers
    return speakers.pick(named)


def fetch(pi: str) -> str:
    """The wishes file off the Pi. Empty if there isn't one yet."""
    done = subprocess.run(["ssh", "-o", "ConnectTimeout=10", pi,
                           f"cat {REMOTE} 2>/dev/null || true"],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"Couldn't reach {pi}:\n{done.stderr.strip()}")
    return done.stdout


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clear", action="store_true",
                        help="forget them all, on the Pi")
    parser.add_argument("--json", action="store_true",
                        help="print the raw list instead")
    parser.add_argument("--local", action="store_true",
                        help="read this machine's own wishes, not the Pi's")
    import speakers
    speakers.add_argument(parser)
    args = parser.parse_args()

    import wishes as engine

    if args.local:
        where, raw = str(engine.WHERE), None
    else:
        pi = target(args.pi)
        where, raw = f"{pi}:{REMOTE}", fetch(pi)

    found = engine.read(raw)

    if args.clear:
        if not found:
            print("Nothing to forget.")
            return 0
        # Show them one last time — clearing something unread is a way to
        # lose the only record of what a child asked for.
        print(engine.show(found))
        if input(f"\nForget these {len(found)}? [y/N] ").strip().lower() != "y":
            print("Left alone.")
            return 0
        if args.local:
            engine.WHERE.unlink(missing_ok=True)
        else:
            subprocess.run(["ssh", pi, f"rm -f {REMOTE}"], check=True)
        print("Forgotten.")
        return 0

    if args.json:
        print(json.dumps([{k: v for k, v in w.items() if k != "words"}
                          for w in found], indent=2))
        return 0

    if not found:
        print(f"Nothing wished for yet.\n  Looked in {where}")
        return 0

    print(f"{len(found)} wish{'es' if len(found) != 1 else ''}, "
          f"most asked first, from {where}")
    print(engine.show(found))
    print("\nTo build one:  claude \"read docs/tools.md, then add a tool "
          "that <wish>\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
