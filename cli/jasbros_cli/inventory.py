"""infra/inventory.yaml: every machine, by name.

Reading goes through PyYAML. Writing does not, because PyYAML drops the
comments that explain the file — so an address is changed by rewriting the
one `ansible_host:` line under that host, and everything else is left as
the person wrote it.
"""

import ipaddress
import re

import yaml

from .common import INVENTORY


def load() -> dict:
    return yaml.safe_load(INVENTORY.read_text())


def lan_subnet() -> ipaddress.IPv4Network:
    return ipaddress.ip_network(load()["all"]["vars"]["lan_subnet"])


def hosts(group: str) -> dict[str, dict]:
    """Every host in a group, with the group's vars filled in under it."""
    top = load()["all"]
    found = top.get("children", {}).get(group) or {}
    shared = {**top.get("vars", {}), **(found.get("vars") or {})}
    return {name: {**shared, **(values or {})}
            for name, values in (found.get("hosts") or {}).items()}


def every_host() -> dict[str, dict]:
    out = {}
    for group in load()["all"].get("children", {}):
        out.update(hosts(group))
    return out


def pick(named: str, group: str | None = None) -> tuple[str, dict]:
    """A host by name, or by any unambiguous piece of one.

    Ambiguity is an error rather than a guess, because the commands on the
    other end of this provision machines and restart services.
    """
    known = hosts(group) if group else every_host()
    if named in known:
        return named, known[named]
    matches = [name for name in known if named in name]
    if len(matches) == 1:
        return matches[0], known[matches[0]]
    if not matches:
        raise SystemExit(f"[jasbros] nothing called {named!r} in "
                         f"{INVENTORY.name} — known: {', '.join(known)}")
    raise SystemExit(f"[jasbros] {named!r} could be any of "
                     f"{', '.join(matches)} — say which")


def target(name: str, values: dict) -> str:
    return f"{values.get('ansible_user', 'normal')}@{values['ansible_host']}"


def set_address(name: str, address: str) -> bool:
    """Rewrite one host's ansible_host. True if the file changed."""
    text = INVENTORY.read_text()
    pattern = re.compile(
        rf"(^(\s+){re.escape(name)}:\s*\n\2\s+ansible_host:\s*)(\S+)",
        re.MULTILINE)
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"[jasbros] couldn't find {name}'s ansible_host "
                         f"line in {INVENTORY.name} to rewrite")
    if match.group(3) == address:
        return False
    INVENTORY.write_text(text[:match.start(3)] + address + text[match.end(3):])
    return True
