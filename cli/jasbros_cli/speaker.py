"""./jasbros speaker — the Claude speakers.

For now this knows where they are. The commands that drive them (card,
deploy, start, label, …) arrive with the hey-claude migration, as thin
wrappers over its scripts — see docs/claude-speaker-migration.md.
"""

from . import inventory, scan
from .common import say


def list_(args) -> int:
    for name, values in inventory.hosts("speakers").items():
        up = scan.reachable(values["ansible_host"])
        say(f"  {name:12} {inventory.target(name, values):24} "
            f"{'up' if up else 'not answering'}")
    return 0


def scan_(args) -> int:
    return scan.update("speakers", args.update)


def add_parser(sub) -> None:
    parser = sub.add_parser("speaker", help="the Claude speakers")
    commands = parser.add_subparsers(dest="speaker_command", required=True)
    p = commands.add_parser("list", help="every speaker, and whether it's up")
    p.set_defaults(func=list_)
    p = commands.add_parser("scan", help="find the speakers on the LAN")
    p.add_argument("--update", action="store_true",
                   help="write the addresses it finds into the inventory")
    p.set_defaults(func=scan_)
