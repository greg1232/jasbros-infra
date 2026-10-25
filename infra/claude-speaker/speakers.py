"""Which speaker a laptop command is talking to, when there is more than one.

`.deploy-target` held exactly one line, so every command here addressed
exactly one Pi and pointing them at a second meant overwriting the first.
That was fine while there was one speaker and is the first thing in the way
of there being ten.

It is now a list, newest first, one per line:

    bedroom normal@192.168.4.22
    pi4 normal@192.168.4.95

The first line is what you get when you do not say. `--pi bedroom` picks by
name, and so does any unambiguous piece of one — `--pi bed` is enough.

A file written by the old version has one line and no name. That still
works: a line with no name takes the host as its name, so an existing
`.deploy-target` keeps pointing where it did.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET_FILE = HERE / ".deploy-target"

NOBODY = ("I don't know which Pi to ask. Set one up, or name one:\n\n"
          "    ./card.sh bedroom --flash\n"
          "    ./deploy.sh normal@192.168.4.95")


def entries() -> list[tuple[str, str]]:
    """Every speaker known, as (name, user@host), in order."""
    if not TARGET_FILE.is_file():
        return []
    out = []
    for line in TARGET_FILE.read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) == 1:
            # The old one-line format. Name it after the host so that a
            # file written before any of this still resolves.
            out.append((parts[0].split("@")[-1], parts[0]))
        else:
            out.append((parts[0], parts[1]))
    return out


def pick(named: str | None = None) -> str:
    """The user@host to talk to.

    `named` may be a name, a user@host, or enough of either to be
    unambiguous. Ambiguity is an error rather than a guess, because the
    commands on the other end of this relabel training data and restart
    services.
    """
    known = entries()
    if named and "@" in named:
        return named
    if not named:
        if not known:
            raise SystemExit(NOBODY)
        return known[0][1]

    exact = [t for n, t in known if n == named or t == named]
    if len(exact) == 1:
        return exact[0]
    near = [(n, t) for n, t in known if named in n or named in t]
    if len(near) == 1:
        return near[0][1]
    if not near:
        raise SystemExit(f"No speaker called {named!r}.\n\n" + describe())
    raise SystemExit(f"{named!r} matches more than one:\n\n" + describe())


def remember(target: str, name: str = "") -> None:
    """Put this one at the top of the list, keeping the rest."""
    name = name or target.split("@")[-1]
    rest = [(n, t) for n, t in entries() if t != target and n != name]
    lines = [f"{name} {target}"] + [f"{n} {t}" for n, t in rest]
    TARGET_FILE.write_text("\n".join(lines) + "\n")


def describe() -> str:
    known = entries()
    if not known:
        return NOBODY
    out = ["Speakers I know about:"]
    for i, (name, target) in enumerate(known):
        out.append(f"    {name:12} {target}" + ("   (the default)" if not i
                                                else ""))
    out.append("\nPick one with --pi <name>.")
    return "\n".join(out)


def add_argument(parser) -> None:
    """The flag, worded the same way everywhere it appears."""
    parser.add_argument("--pi", default=None, metavar="NAME",
                        help="which speaker, when there is more than one "
                             "(default: the first in .deploy-target)")
