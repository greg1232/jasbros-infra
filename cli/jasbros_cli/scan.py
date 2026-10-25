"""Find machines on the LAN by asking who answers.

By scanning, not by name: neither the router's DNS nor mDNS resolves on
this network (docs/open-questions.md Q5), and nothing has a DHCP
reservation, so an address in the inventory is only where a machine was
last seen. This is the same approach as hey-claude's `card.py --find`.
"""

import concurrent.futures
import socket
import subprocess

from . import inventory
from .common import say


def _open(address: str, port: int) -> str | None:
    try:
        with socket.create_connection((address, port), timeout=1):
            return address
    except OSError:
        return None


def _hostname(user: str, address: str) -> str:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
         "-o", "StrictHostKeyChecking=accept-new", f"{user}@{address}",
         "hostname"], capture_output=True, text=True).stdout.strip()


def find_hosts(user: str = "normal") -> dict[str, str]:
    """{hostname: address} for everything on the LAN that lets us SSH in."""
    subnet = inventory.lan_subnet()
    say(f"scanning {subnet} for ssh — about ten seconds")
    every = [str(a) for a in subnet.hosts()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=256) as pool:
        up = [a for a in pool.map(lambda a: _open(a, 22), every) if a]
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        names = list(pool.map(lambda a: _hostname(user, a), up))
    return {name: address for name, address in zip(names, up) if name}


def reachable(address: str, port: int = 22) -> bool:
    return _open(address, port) is not None


def update(group: str, apply: bool) -> int:
    """Compare a group's inventory addresses to what the scan found."""
    known = inventory.hosts(group)
    users = {values.get("ansible_user", "normal") for values in known.values()}
    found = {}
    for user in users:
        found.update(find_hosts(user))
    missing = 0
    for name, values in known.items():
        was, now = values["ansible_host"], found.get(name)
        if now is None:
            missing += 1
            say(f"  {name:12} not found (inventory says {was})")
        elif now == was:
            say(f"  {name:12} {now}")
        elif apply:
            inventory.set_address(name, now)
            say(f"  {name:12} {now}   moved from {was} — inventory updated")
        else:
            say(f"  {name:12} {now}   moved from {was} — --update to write it")
    strangers = sorted(set(found) - set(inventory.every_host()))
    for name in strangers:
        say(f"  {name:12} {found[name]}   <- not in the inventory")
    return 1 if missing else 0
