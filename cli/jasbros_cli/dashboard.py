"""./jasbros dashboard — the speaker fleet dashboard on gmk.

See docs/speaker-fleet-dashboard.md.
"""

import webbrowser

from . import apps, inventory
from .common import INFRA, say

SPEAKER = INFRA / "claude-speaker"


def values() -> dict:
    """The speakers, from the inventory — one list, not two."""
    return {"speakers": [{"name": name, "url": apps.speaker_url(name)}
                         for name in inventory.hosts("speakers")],
            "rescan": {"subnet": str(inventory.lan_subnet())}}


APP = apps.App(
    name="speaker-fleet-dashboard", namespace="claude-speaker",
    source=SPEAKER / "fleet-dashboard",
    chart=SPEAKER / "charts" / "speaker-fleet-dashboard",
    files=("Dockerfile", "fleet.py", "page.html"),
    path="/speakers", values=values)


def open_(args) -> int:
    webbrowser.open(APP.url())
    say(APP.url())
    return 0


def add_parser(sub) -> None:
    parser = sub.add_parser("dashboard", help="the speaker fleet dashboard")
    commands = parser.add_subparsers(dest="dashboard_command", required=True)
    apps.add_build_deploy(commands, APP)
    commands.add_parser("open", help="open it in a browser").set_defaults(
        func=open_)
