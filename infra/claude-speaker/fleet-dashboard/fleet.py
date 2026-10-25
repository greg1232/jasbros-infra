"""The speaker fleet dashboard: every Claude speaker in the house, on one page.

It runs in a pod on gmk (docs/speaker-fleet-dashboard.md), polls each
speaker's own JSON API, keeps the latest answer and a day of history, and
serves one page. Standard library only, like the speaker's own dashboard.

Three rules, from the design:

  - What was said in a room stays on that room's speaker. Transcripts are
    dropped the moment a poll returns, before anything is kept or served.
  - Read-only. Nothing here can change a speaker.
  - A speaker is known by its name, not its address. The router moves
    addresses about and nothing has a reservation, so a speaker that stops
    answering is looked for, not just reported.

    python fleet.py --config fleet.json --db /tmp/fleet.db    # on a laptop
"""

import argparse
import concurrent.futures
import http.server
import ipaddress
import json
import os
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent

DEFAULTS = {
    "pollSeconds": 15,          # how often a speaker that answers is asked
    "slowPollSeconds": 60,      # ...and one that has stopped answering
    "timeoutSeconds": 3,
    "wifiEveryMinutes": 5,      # /api/wifi scans the air: ~4 s on a Pi 4
    "downAfterMinutes": 5,      # a reboot is a minute, a router blink ninety s
    "historyDays": 7,
    "rescan": {"enabled": True, "subnet": "192.168.4.0/22", "port": 8080,
               "everyMinutes": 5},
    "speakers": [],             # [{"name": "bedroom", "url": "http://…:8080"}]
}

HOT = 75            # °C — a Pi 4 throttles at 80
FULL = 90           # disk, percent


# --- asking a speaker -------------------------------------------------------

def get_json(url: str, timeout: float) -> tuple[int | None, dict | None]:
    """(HTTP status, body) — (None, None) if nothing answered at all."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as error:
        error.close()           # or every 404 from an old speaker leaks a socket
        return error.code, None
    except Exception:
        return None, None


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def host_of(base: str) -> str:
    return urllib.parse.urlparse(base).hostname or base


# --- keeping only what this page is for -------------------------------------

def _describe(thing) -> str | None:
    """A short line for whatever a module reports as playing, or None."""
    if not thing:
        return None
    if isinstance(thing, str):
        return thing[:80]
    if isinstance(thing, dict):
        parts = [str(thing[key]) for key in
                 ("title", "name", "track", "artist", "sound", "kind")
                 if thing.get(key)]
        return " — ".join(parts)[:80] or "playing"
    return str(thing)[:80]


def summarize_speaker(data: dict) -> dict:
    """/api/speaker, without the transcripts.

    `recent` holds the last twenty things said in the room and what was
    answered. Only how each one ended is kept — never the words.
    """
    recent = data.get("recent") or []
    timers = data.get("timers") or []
    wake = data.get("wake") or {}
    mic = data.get("microphone") or {}
    return {
        "talking": bool(data.get("talking")),
        "ringing": bool(data.get("ringing")),
        "timers": len(timers),
        "next_timer": min((t.get("seconds_left", 0) for t in timers),
                          default=None),
        "sound": _describe(data.get("sound")),
        "book": _describe(data.get("book")),
        "music": _describe(data.get("music")),
        "volume": data.get("volume"),
        "mic": {"hearing": mic.get("hearing"),
                "gone_deaf": mic.get("gone_deaf", 0)},
        "wake": {"fired_today": wake.get("fired_today"),
                 "answered_today": wake.get("answered_today"),
                 "threshold": wake.get("threshold"),
                 "model": wake.get("model")},
        "endings": dict(Counter(turn.get("kind", "?") for turn in recent)),
    }


def summarize_system(data: dict) -> dict:
    throttling = data.get("throttling") or {}
    return {
        "cpu": data.get("cpu"),
        "temperature": data.get("temperature"),
        "memory": (data.get("memory") or {}).get("used_percent"),
        "disk": (data.get("disk") or {}).get("used_percent"),
        "uptime_hours": data.get("uptime_hours"),
        "load": (data.get("load") or [None])[0],
        "cores": data.get("cores"),
        "throttled_now": bool(throttling.get("throttled now")
                              or throttling.get("under voltage now")),
        "throttling": {k: v for k, v in throttling.items() if v},
        "services": data.get("services") or {},
    }


def summarize_wifi(data: dict) -> dict:
    """Which network and how strong — not what else is in range, and never
    the rescue access point's password."""
    return {"network": data.get("connected"), "signal": data.get("signal")}


# --- what is wrong, across the house ----------------------------------------

def problems(speakers: list[dict]) -> list[str]:
    out = []
    for s in speakers:
        name = s["name"]
        if s["status"] == "down":
            out.append(f"{name} is down — {_since(s['last_seen'], s['now'])}")
            continue
        if s["status"] != "up":
            continue
        system, speaker = s.get("system") or {}, s.get("speaker") or {}
        if system.get("throttled_now"):
            out.append(f"{name} is throttling — "
                       f"{', '.join(system['throttling']) or 'now'}")
        temperature = system.get("temperature")
        if temperature is not None and temperature >= HOT:
            out.append(f"{name} is hot — {temperature:.0f} °C")
        disk = system.get("disk")
        if disk is not None and disk >= FULL:
            out.append(f"{name}'s disk is {disk:.0f}% full")
        for unit, state in (system.get("services") or {}).items():
            if state != "active":
                out.append(f"{name}: {unit} is {state}")
        if (speaker.get("mic") or {}).get("hearing") is False:
            out.append(f"{name} has gone deaf — the microphone sends nothing")
    out += _version_problems([s for s in speakers if s["status"] == "up"])
    return out


def _version_problems(up: list[dict]) -> list[str]:
    out, commits = [], Counter()
    for s in up:
        version = s.get("version") or {}
        if version.get("changed"):
            out.append(f"{s['name']} runs uncommitted changes on "
                       f"{version.get('commit')}")
        if version.get("commit", "unknown") != "unknown":
            commits[version["commit"]] += 1
    if len(commits) > 1:
        usual = commits.most_common(1)[0][0]
        for s in up:
            commit = (s.get("version") or {}).get("commit")
            if commit not in (None, "unknown", usual):
                out.append(f"{s['name']} runs {commit}, the others {usual}")
    return out


def _since(then: float, now: float) -> str:
    if not then:
        return "not seen since this page started"
    minutes = int((now - then) // 60)
    if minutes < 60:
        return f"not seen for {minutes} min"
    return f"not seen for {minutes // 60} h {minutes % 60} min"


# --- what is kept -----------------------------------------------------------

class Store:
    """SQLite on the pod's volume: a sample a minute, and where each was.

    Losing it costs the sparklines and the remembered addresses, nothing
    more — which is why it is a file and not a database server.
    """

    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        self.sampled: dict[str, int] = {}
        with self.lock, self.db:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                    name TEXT, at INTEGER, up INTEGER,
                    temperature REAL, cpu REAL, throttled INTEGER);
                CREATE INDEX IF NOT EXISTS samples_by_name
                    ON samples (name, at);
                CREATE TABLE IF NOT EXISTS addresses (
                    name TEXT PRIMARY KEY, base TEXT, moved_from TEXT,
                    at REAL);
            """)

    def sample(self, name: str, at: float, up: bool, system: dict) -> None:
        minute = int(at // 60)
        if self.sampled.get(name) == minute:
            return
        self.sampled[name] = minute
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO samples VALUES (?, ?, ?, ?, ?, ?)",
                (name, int(at), int(up), system.get("temperature"),
                 system.get("cpu"), int(bool(system.get("throttled_now")))))

    def history(self, name: str, since: float, bucket: int = 600) -> list:
        """Ten-minute buckets: how much of each it was up, and how warm."""
        with self.lock:
            rows = self.db.execute(
                "SELECT at / ? * ?, AVG(up), AVG(temperature) FROM samples "
                "WHERE name = ? AND at >= ? GROUP BY at / ? ORDER BY 1",
                (bucket, bucket, name, int(since), bucket)).fetchall()
        return [{"at": at, "up": round(up, 2),
                 "temperature": None if t is None else round(t, 1)}
                for at, up, t in rows]

    def remember(self, name: str, base: str, moved_from: str | None,
                 at: float) -> None:
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO addresses VALUES (?, ?, ?, ?)",
                (name, base, moved_from, at))

    def addresses(self) -> dict[str, tuple[str, str | None]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT name, base, moved_from FROM addresses").fetchall()
        return {name: (base, moved) for name, base, moved in rows}

    def prune(self, before: float) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM samples WHERE at < ?", (int(before),))


# --- the fleet --------------------------------------------------------------

@dataclass
class Speaker:
    name: str
    base: str                   # where it is now, e.g. http://192.168.4.22:8080
    hint: str                   # where the inventory says it was
    last_seen: float = 0
    last_try: float = 0
    failures: int = 0
    error: str = ""
    moved_from: str | None = None
    version: dict | None = None
    speaker: dict | None = None
    system: dict | None = None
    wifi: dict | None = None
    wifi_at: float = 0


class Fleet:
    def __init__(self, config: dict, store: Store, clock=time.time,
                 get=get_json, probe=port_open):
        self.config = {**DEFAULTS, **config,
                       "rescan": {**DEFAULTS["rescan"],
                                  **config.get("rescan", {})}}
        self.store, self.clock, self.get, self.probe = store, clock, get, probe
        self.started = clock()
        self.last_round = 0.0
        self.last_rescan = float("-inf")
        self.rounds = 0
        self.unknown: dict[str, str] = {}
        remembered = store.addresses()
        self.speakers: dict[str, Speaker] = {}
        for entry in self.config["speakers"]:
            base, moved = remembered.get(entry["name"], (entry["url"], None))
            self.speakers[entry["name"]] = Speaker(
                entry["name"], base.rstrip("/"), entry["url"].rstrip("/"),
                moved_from=moved)
        self.lock = threading.Lock()

    # -- polling

    def due(self, s: Speaker, now: float) -> bool:
        wait = (self.config["pollSeconds"] if s.failures < 3
                else self.config["slowPollSeconds"])
        return now - s.last_try >= wait

    def poll_round(self) -> None:
        now = self.clock()
        due = [s for s in self.speakers.values() if self.due(s, now)]
        if due:
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                list(pool.map(self.poll, due))
        if self.config["rescan"]["enabled"]:
            self.maybe_rescan()
        if self.rounds % 3600 == 0:
            self.store.prune(now - self.config["historyDays"] * 86400)
        self.rounds += 1
        self.last_round = self.clock()

    def poll(self, s: Speaker) -> None:
        now = self.clock()
        s.last_try = now
        timeout = self.config["timeoutSeconds"]
        status, version = self.get(f"{s.base}/api/version", timeout)
        if status is None:
            return self._missing(s, now, "not answering")
        if status == 200 and version.get("name") not in (None, s.name):
            # The router gave this address to another speaker.
            return self._missing(s, now, f"{version['name']} answers at "
                                         f"{host_of(s.base)}")
        _, speaker = self.get(f"{s.base}/api/speaker", timeout)
        _, system = self.get(f"{s.base}/api/system", timeout)
        if speaker is None or system is None:
            return self._missing(s, now, "answers, but not like a speaker")

        wifi = s.wifi
        if now - s.wifi_at >= self.config["wifiEveryMinutes"] * 60:
            _, found = self.get(f"{s.base}/api/wifi", max(timeout, 8))
            if found is not None:
                wifi, s.wifi_at = summarize_wifi(found), now
        with self.lock:
            # Old speakers answer 404 here, and are taken at their word.
            s.version = version if status == 200 else {"commit": "unknown"}
            s.speaker = summarize_speaker(speaker)
            s.system = summarize_system(system)
            s.wifi = wifi
            s.last_seen, s.failures, s.error = now, 0, ""
        self.store.sample(s.name, now, True, s.system)

    def _missing(self, s: Speaker, now: float, why: str) -> None:
        with self.lock:
            s.failures += 1
            s.error = why
        self.store.sample(s.name, now, False, {})

    def status(self, s: Speaker, now: float) -> str:
        if s.failures == 0 and s.last_seen:
            return "up"
        since = s.last_seen or self.started
        if now - since >= self.config["downAfterMinutes"] * 60:
            return "down"
        return "looking"

    # -- finding the ones that moved

    def maybe_rescan(self) -> None:
        missing = [s for s in self.speakers.values() if s.failures]
        every = self.config["rescan"]["everyMinutes"] * 60
        if missing and self.clock() - self.last_rescan >= every:
            self.rescan()

    def rescan(self) -> dict[str, str]:
        """Ask everything on the subnet with the port open who it is.

        One scan finds every missing speaker at once. It is limited to the
        speakers' port and to once every few minutes, and only happens while
        a speaker is missing, so it does not lean on the Wi-Fi.
        """
        self.last_rescan = self.clock()
        settings = self.config["rescan"]
        port = settings["port"]
        everything = [str(h) for h in
                      ipaddress.ip_network(settings["subnet"]).hosts()]
        with concurrent.futures.ThreadPoolExecutor(64) as pool:
            open_ = [h for h, ok in zip(everything, pool.map(
                lambda h: self.probe(h, port), everything)) if ok]

        def ask(host: str) -> tuple[str, str | None]:
            base = f"http://{host}:{port}"
            status, version = self.get(f"{base}/api/version", 2)
            return base, (version or {}).get("name") if status == 200 else None

        with concurrent.futures.ThreadPoolExecutor(16) as pool:
            found = {name: base for base, name in pool.map(ask, open_) if name}

        now = self.clock()
        with self.lock:
            for name, base in found.items():
                s = self.speakers.get(name)
                if s is None:
                    self.unknown[name] = host_of(base)
                elif s.base != base:
                    s.moved_from, s.base = host_of(s.base), base
                    s.last_try = 0              # ask it straight away
                    self.store.remember(name, base, s.moved_from, now)
        return found

    # -- what the page sees

    def snapshot(self) -> dict:
        now = self.clock()
        since = now - 86400
        out = []
        with self.lock:
            for s in self.speakers.values():
                out.append({
                    "name": s.name, "now": now,
                    "status": self.status(s, now),
                    "address": host_of(s.base), "page": s.base + "/",
                    "moved_from": s.moved_from,
                    "last_seen": s.last_seen, "error": s.error,
                    "version": s.version, "speaker": s.speaker,
                    "system": s.system, "wifi": s.wifi,
                })
        for entry in out:
            entry["history"] = self.store.history(entry["name"], since)
        return {"now": now, "speakers": out, "problems": problems(out),
                "unknown": [{"name": n, "address": a}
                            for n, a in sorted(self.unknown.items())],
                "last_rescan": (None if self.last_rescan == float("-inf")
                                else self.last_rescan)}

    def healthy(self) -> bool:
        return self.clock() - self.last_round < 10 * self.config["pollSeconds"]

    def ready(self) -> bool:
        return self.rounds > 0


# --- the page ---------------------------------------------------------------

def serve(fleet: Fleet, port: int) -> http.server.ThreadingHTTPServer:
    page = (HERE / "page.html").read_bytes()

    class Pages(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):                                 # noqa: N802
            path = urllib.parse.urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, page, "text/html; charset=utf-8")
                if path == "/api/fleet":
                    return self._send(200, json.dumps(fleet.snapshot()).encode(),
                                      "application/json")
                if path == "/healthz":
                    ok = fleet.healthy()
                    return self._send(200 if ok else 503,
                                      b"ok\n" if ok else b"stuck\n",
                                      "text/plain")
                if path == "/readyz":
                    ok = fleet.ready()
                    return self._send(200 if ok else 503,
                                      b"ok\n" if ok else b"first round\n",
                                      "text/plain")
            except Exception as error:
                return self._send(500, f"{type(error).__name__}: {error}\n"
                                  .encode(), "text/plain")
            self._send(404, b"not here\n", "text/plain")

        def _send(self, code: int, body: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass                    # the poller's log is the useful one

    server = http.server.ThreadingHTTPServer(("", port), Pages)
    server.daemon_threads = True
    return server


def keep_polling(fleet: Fleet) -> None:
    while True:
        try:
            fleet.poll_round()
        except Exception as error:          # one bad round is not the end
            print(f"[fleet] round failed: {type(error).__name__}: {error}",
                  flush=True)
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", default=os.environ.get(
        "FLEET_CONFIG", "/config/fleet.json"))
    parser.add_argument("--db", default=os.environ.get(
        "FLEET_DB", "/data/fleet.db"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text())
    fleet = Fleet(config, Store(args.db))
    names = ", ".join(fleet.speakers) or "none"
    print(f"[fleet] watching {names}; serving on :{args.port}", flush=True)
    threading.Thread(target=keep_polling, args=(fleet,), daemon=True).start()
    serve(fleet, args.port).serve_forever()


if __name__ == "__main__":
    main()
