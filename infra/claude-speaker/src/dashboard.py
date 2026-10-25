"""A dashboard, served from the Pi, for a browser on the same network.

    http://192.168.4.95:8080

Three things a speaker with no screen cannot tell you: what it is doing
right now, whether the machine under it is healthy, and which network it is
on — the last of which matters most, because getting the Wi-Fi wrong is the
one mistake that makes every other page unreachable.

It runs inside the speaker rather than beside it, on a daemon thread. That
is the whole reason it can show anything interesting: what is playing, what
timers are set, what was asked a minute ago and what the wake word scored
are all in this process and nowhere else. A separate service would have to
guess at them from files.

The cost of that is a rule: nothing here may ever raise into the speaker.
Every handler is wrapped, the server is threaded so one slow request cannot
block another, and if the whole thing fails to start the speaker carries on
without it.

    python src/dashboard.py        serve it on its own, for development
"""

import http.server
import json
import os
import socket
import subprocess
import threading
import time
import urllib.parse
from collections import deque
from pathlib import Path

import config

PORT = int(config.DASHBOARD_PORT) if config.DASHBOARD_PORT else 8080

# The last few turns, so the page can show what has been going on. Small on
# purpose: this is a window, not a record — wake_log.py keeps the record.
recent: deque = deque(maxlen=40)
# CPU and temperature, sampled slowly, for the graphs.
history: deque = deque(maxlen=180)     # about half an hour at ten seconds

_started = False
_cpu_was: tuple | None = None

# When somebody last asked this page for anything. src/rescue.py reads it to
# decide whether anyone actually turned up after it raised an access point:
# a phone that associates and wanders off is not somebody fixing the Wi-Fi,
# and a request to this page is.
last_request_at = 0.0


def note(kind: str, said: str = "", answer: str = "", score: float = 0.0):
    """Tell the dashboard something happened. Never raises."""
    try:
        recent.appendleft({
            "at": time.time(), "kind": kind, "said": said,
            "answer": answer, "score": round(float(score), 3),
        })
    except Exception:
        pass


def start() -> None:
    """Serve the dashboard, if it can. A failure here costs the dashboard."""
    global _started
    if _started or not config.DASHBOARD:
        return
    _started = True
    threading.Thread(target=_watch_machine, daemon=True).start()
    threading.Thread(target=_serve, daemon=True).start()
    threading.Thread(target=_serve_portal, daemon=True).start()


def _serve_portal() -> None:
    """Also answer on port 80, for the captive portal.

    When the speaker is its own access point, a phone joining it asks for
    one of the addresses its operating system probes — and dnsmasq points
    every name at us, so the probe arrives here. Anything other than the
    exact success reply it expects is what makes the phone put up a "sign
    in to this network" sheet instead of quietly leaving for cellular. So
    this serves the dashboard, and the sheet opens on it.

    Needs deploy.py to have lowered ip_unprivileged_port_start; without
    that this fails, the portal does not pop up, and everything else still
    works on 8080.
    """
    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", 80), Pages)
    except Exception as error:
        print(f"[dashboard] no captive portal on port 80 "
              f"({type(error).__name__}) — the page is still on {PORT}")
        return
    server.serve_forever()


def _serve() -> None:
    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Pages)
    except Exception as error:
        print(f"[dashboard] not serving ({type(error).__name__}: {error})")
        return
    print(f"Dashboard: http://{_my_address()}:{PORT}")
    server.serve_forever()


def _my_address() -> str:
    """This machine's address on the network, as a browser would need it."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 1))     # goes nowhere; just picks a route
        found = probe.getsockname()[0]
        probe.close()
        return found
    except Exception:
        return socket.gethostname()


# --- what the machine is doing ----------------------------------------------


def _watch_machine() -> None:
    while True:
        try:
            history.append({"at": time.time(), "cpu": _cpu_busy(),
                            "temp": _temperature(),
                            "memory": _memory()["used_percent"]})
        except Exception:
            pass
        time.sleep(10)


def _cpu_busy() -> float:
    """How busy the processor has been since this was last asked."""
    global _cpu_was
    try:
        fields = Path("/proc/stat").read_text().split("\n")[0].split()[1:]
        numbers = [int(x) for x in fields]
    except Exception:
        return 0.0
    idle, total = numbers[3] + numbers[4], sum(numbers)
    was, _cpu_was = _cpu_was, (idle, total)
    if was is None or total == was[1]:
        return 0.0
    return round(100 * (1 - (idle - was[0]) / (total - was[1])), 1)


def _memory() -> dict:
    got = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, _, rest = line.partition(":")
        got[name] = int(rest.split()[0]) * 1024
    total = got.get("MemTotal", 1)
    free = got.get("MemAvailable", 0)
    return {"total": total, "used": total - free,
            "used_percent": round(100 * (total - free) / total, 1)}


def _temperature() -> float:
    try:
        return round(int(Path("/sys/class/thermal/thermal_zone0/temp")
                         .read_text()) / 1000, 1)
    except Exception:
        return 0.0


def _throttling() -> dict:
    """What the Pi has complained about since it was switched on.

    Under-voltage is the commonest fault on a Raspberry Pi and it shows up
    as everything being mysteriously slow rather than as an error, so it is
    worth a line of its own on a page somebody actually looks at.
    """
    try:
        said = subprocess.run(["vcgencmd", "get_throttled"],
                              capture_output=True, text=True, timeout=5).stdout
        bits = int(said.strip().split("=")[1], 16)
    except Exception:
        return {}
    return {
        "under voltage now": bool(bits & 1),
        "throttled now": bool(bits & 4),
        "under voltage since boot": bool(bits & (1 << 16)),
        "throttled since boot": bool(bits & (1 << 18)),
    }


def _run(*args, timeout=8) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:
        return ""


# --- the three things the page asks about -----------------------------------


def speaker_state() -> dict:
    """What the speaker is doing, from the modules themselves."""
    out = {"recent": list(recent)[:20]}

    try:
        import timers
        out["timers"] = [
            {"name": t["label"] or t["spoken"], "kind": t["kind"],
             "seconds_left": max(0, int(t["at"] - time.time()))}
            for t in sorted(timers._pending, key=lambda t: t["at"])]
        out["ringing"] = timers.ringing.is_set()
    except Exception:
        out["timers"] = []

    try:
        import sounds
        out["sound"] = sounds.playing()
    except Exception:
        out["sound"] = None

    # Whether it can still hear. A speaker that has gone deaf behaves
    # exactly like one nobody is talking to, so this is the only place the
    # difference shows without saying the wake word and waiting.
    try:
        import audio_in
        import config
        mic = audio_in.LATEST
        quiet = mic.silent_for() if mic else None
        out["microphone"] = None if mic is None else {
            "silent_for": round(quiet, 1),
            "hearing": quiet < config.MIC_DEAF_SECONDS,
            "gone_deaf": mic.deaf_events,
        }
    except Exception:
        out["microphone"] = None

    try:
        import tts
        out["volume"] = tts.volume()
    except Exception:
        out["volume"] = None

    # Whether it is still a Spotify speaker. librespot can be running and
    # useless, and nothing else here would show the difference.
    try:
        import music
        out["spotify"] = {"visible": music._visible,
                          "said": music._health} if music.ready() else None
    except Exception:
        out["spotify"] = None

    try:
        import books
        book = books._reader
        out["book"] = ({"title": book.title, "chapter": book.at + 1,
                        "chapters": len(book.chapters)} if book else None)
    except Exception:
        out["book"] = None

    try:
        import music
        out["music"] = music.now_playing() or None
    except Exception:
        out["music"] = None

    try:
        import tts
        out["talking"] = tts.speaking.is_set()
    except Exception:
        out["talking"] = False

    out["wake"] = _wake_summary()
    return out


def _wake_summary() -> dict:
    """The wake word: which model, and how it has been doing today."""
    out = {}
    try:
        import numpy as np

        import whisper_wake
        found = whisper_wake.find_model(config.WAKE_MODEL)
        weights = np.load(found)
        out["model"] = found.name
        out["threshold"] = round(float(weights["threshold"]), 3) \
            if "threshold" in weights.files else config.WAKE_THRESHOLD
        out["fitted"] = str(weights["fitted_at"])[:16] \
            if "fitted_at" in weights.files else ""
        out["dataset"] = str(weights["dataset"])[:8] \
            if "dataset" in weights.files else ""
    except Exception:
        pass

    try:
        import wake_log
        rows = wake_log.read()
        day = time.time() - 24 * 3600
        stamp = time.strftime("%Y-%m-%dT%H:%M", time.localtime(day))
        today = [r for r in rows if not r.get("near")
                 and not r.get("taught") and r.get("at", "") >= stamp]
        out["fired_today"] = len(today)
        out["answered_today"] = sum(1 for r in today if r.get("answered"))
        out["labelled"] = sum(1 for r in rows if r.get("by") == "person")
        # When they happened, by hour, for the bars.
        hours = [0] * 24
        for row in today:
            try:
                hours[int(row["at"][11:13])] += 1
            except Exception:
                pass
        out["by_hour"] = hours
        # Both sides of the line. Firings alone are every window that
        # already crossed the threshold, so a histogram of them says "200
        # of 200 woke it", which is true and tells you nothing. The near
        # misses are the other hump, and the point of the picture is how
        # well the line separates them.
        windows = [r for r in rows if not r.get("taught")
                   and r.get("at", "") >= stamp]
        out["scores"] = [r.get("score", 0) for r in windows][-600:]
    except Exception:
        pass
    return out


def system_state() -> dict:
    services = {}
    for name in ("claude-speaker", "librespot", "claude-relearn.timer"):
        services[name] = _run("systemctl", "--user", "is-active", name) \
            or "unknown"
    try:
        space = os.statvfs("/")
        disk = {"total": space.f_blocks * space.f_frsize,
                "free": space.f_bavail * space.f_frsize}
        disk["used_percent"] = round(
            100 * (1 - disk["free"] / disk["total"]), 1)
    except Exception:
        disk = {}
    try:
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        load = Path("/proc/loadavg").read_text().split()[:3]
    except Exception:
        uptime, load = 0.0, ["0", "0", "0"]

    return {
        "cpu": _cpu_busy(), "temperature": _temperature(),
        "memory": _memory(), "disk": disk,
        "uptime_hours": round(uptime / 3600, 1),
        "load": [float(x) for x in load],
        "throttling": _throttling(),
        "services": services,
        "history": list(history),
        "address": _my_address(),
        "cores": os.cpu_count(),
    }


def wishes_state() -> dict:
    """What every speaker has been asked for and cannot do.

    Pooled across the house, because a thing two children ask for in two
    rooms is a better idea than a thing asked for once — and because the
    whole point of writing them down is that somebody reads them.
    """
    try:
        import wishes
        found = wishes.everyones()
        return {"wishes": [
            {"wish": w.get("wish", ""),
             "times": w.get("times", 1),
             "last": w.get("last", ""),
             "askeds": w.get("askeds", [])[:3],
             "whos": [x for x in w.get("whos", []) if x],
             "speakers": [x for x in w.get("speakers", []) if x]}
            for w in found[:60]], "total": len(found)}
    except Exception as error:
        return {"wishes": [], "total": 0,
                "said": f"{type(error).__name__}: {error}"}


def wifi_state(include_saved: bool = True) -> dict:
    """Which network, how strong, what else is within reach, and what it
    has credentials for.

    `include_saved` is off when forget_wifi calls this, because that would
    recurse: saved_networks needs to know what is connected, and this needs
    to know what is saved.
    """
    out = {"connected": "", "signal": 0, "address": _my_address(),
           "networks": []}
    active = _run("nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL",
                  "device", "wifi", "list")
    seen = {}
    for line in active.splitlines():
        parts = line.split(":")
        if len(parts) < 3 or not parts[1]:
            continue
        name, signal = parts[1], int(parts[2] or 0)
        if parts[0] == "yes":
            out["connected"], out["signal"] = name, signal
        # The strongest sighting of each name; a house has repeaters.
        if signal > seen.get(name, -1):
            seen[name] = signal
    out["networks"] = [{"name": n, "signal": s}
                       for n, s in sorted(seen.items(),
                                          key=lambda kv: -kv[1])][:20]
    out["can_change"] = _can_change_wifi()
    if include_saved:
        out["saved"] = saved_networks(out["connected"])
    try:
        import rescue
        out["rescue"] = rescue.state()
    except Exception:
        out["rescue"] = None
    return out


def _wifi_device() -> str:
    """The name of the wireless interface, rather than assuming wlan0."""
    for line in _run("nmcli", "-t", "-f", "DEVICE,TYPE", "device").splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1] == "wifi":
            return parts[0]
    return "wlan0"


def saved_networks(connected: str = "") -> list[dict]:
    """Every network this machine has credentials for.

    Not the same list as the one it can see. The point of saving a network
    is to do it somewhere else — you type in the one at the grandparents'
    house before you drive there, and it is never in range while you do.

    Passwords are deliberately not fetched. nmcli will hand them over and
    nothing here asks; the page shows names.
    """
    rows = []
    listed = _run("nmcli", "-t", "-f",
                  "NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY",
                  "connection", "show")
    for line in listed.splitlines():
        parts = line.split(":")
        if len(parts) < 4 or "wireless" not in parts[1]:
            continue
        name = parts[0]
        # The connection's name is not the network's. netplan called this
        # one netplan-wlan0-solus and the network it joins is solus, so
        # matching the two by name alone quietly fails.
        ssid = _run("nmcli", "-t", "-g", "802-11-wireless.ssid",
                    "connection", "show", name).strip() or name
        rows.append({"name": name, "ssid": ssid,
                     "autoconnect": parts[2] == "yes",
                     "priority": int(parts[3] or 0),
                     "current": bool(connected) and ssid == connected})
    return sorted(rows, key=lambda r: (not r["current"], r["ssid"].lower()))


def save_wifi(name: str, password: str, hidden: bool = False) -> dict:
    """Remember a network without having to be standing in it.

    `nmcli device wifi connect` — what join_wifi uses — needs the network
    to be in range. This does not, which is the whole reason it exists.
    """
    name = name.strip()
    if not name:
        return {"ok": False, "said": "No network name."}
    if not _can_change_wifi():
        return {"ok": False, "said": CANNOT_CHANGE}

    # Replace rather than pile up. Saving the same network twice is what
    # somebody does when they got the password wrong the first time.
    for row in saved_networks():
        if row["ssid"] == name or row["name"] == name:
            _run("nmcli", "connection", "delete", row["name"])

    args = ["nmcli", "connection", "add", "type", "wifi",
            "con-name", name, "ifname", _wifi_device(), "ssid", name]
    if password:
        args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
    if hidden:
        args += ["802-11-wireless.hidden", "yes"]
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=20)
    except Exception as error:
        return {"ok": False, "said": f"{type(error).__name__}"}
    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip().splitlines()
        return {"ok": False, "said": said[-1] if said else "Didn't save."}
    return {"ok": True, "said": f"Saved {name}. It will join when in range.",
            **_stand_down_if_rescuing(name)}


def _stand_down_if_rescuing(name: str) -> dict:
    """Credentials have arrived, so stop being an access point and go.

    Answering first and acting afterwards is not politeness, it is the only
    order that works: whoever asked is joined to the access point this is
    about to take down, so the reply has to be on the wire before the radio
    changes what it is doing.
    """
    try:
        import rescue
        if not rescue.active():
            return {}
    except Exception:
        return {}

    def go():
        try:
            import rescue
            rescue.down(f"{name} was saved")
            ok, said = rescue._run("nmcli", "connection", "up", name)
            print(f"[rescue] joining {name}: {'ok' if ok else said}")
        except Exception as error:
            print(f"[rescue] {type(error).__name__}: {error}")

    threading.Timer(2.0, go).start()
    return {"leaving": True,
            "said_extra": "Dropping my own network and joining that one. "
                          "Rejoin your usual Wi-Fi to see this page again."}


def forget_wifi(name: str) -> dict:
    """Delete a saved network, unless that would strand the speaker.

    Deleting the network currently carrying this request is a way to make
    the speaker unreachable from the page that did it, so it is only
    allowed when there is somewhere else to land.
    """
    name = name.strip()
    if not name:
        return {"ok": False, "said": "Which one?"}
    if not _can_change_wifi():
        return {"ok": False, "said": CANNOT_CHANGE}

    state = wifi_state(include_saved=False)
    connected = state.get("connected", "")
    in_range = {n["name"] for n in state.get("networks", [])}
    rows = saved_networks(connected)
    going = [r for r in rows if r["name"] == name or r["ssid"] == name]
    if not going:
        return {"ok": False, "said": f"Nothing saved called {name}."}

    if going[0]["current"]:
        elsewhere = [r for r in rows
                     if not r["current"] and r["ssid"] in in_range]
        if not elsewhere:
            return {"ok": False, "said":
                    "That's the network it's on, and there's nothing else "
                    "saved within reach. Save another one first."}

    _run("nmcli", "connection", "delete", going[0]["name"])
    return {"ok": True, "said": f"Forgotten {going[0]['ssid']}."}


CANNOT_CHANGE = ("This machine won't let me change the network. "
                 "Run ./deploy.sh once to allow it.")


def _can_change_wifi() -> bool:
    """Whether this user may change the network without a password."""
    said = _run("pkcheck", "--action-id",
                "org.freedesktop.NetworkManager.settings.modify.system",
                "--process", str(os.getpid()))
    return "requires authentication" not in said.lower()


def join_wifi(name: str, password: str) -> dict:
    """Join a network. The one thing here that can lock you out.

    If this succeeds the page you asked from is at a different address, and
    if it fails you want to still be on the old one — so it reports what
    happened rather than assuming, and NetworkManager brings the previous
    connection back on its own if the new one doesn't come up.
    """
    name = name.strip()
    if not name:
        return {"ok": False, "said": "No network name."}
    if not _can_change_wifi():
        return {"ok": False, "said": CANNOT_CHANGE}
    args = ["nmcli", "device", "wifi", "connect", name]
    if password:
        args += ["password", password]
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=45)
    except Exception as error:
        return {"ok": False, "said": f"{type(error).__name__}"}
    said = (done.stdout + done.stderr).strip().splitlines()
    return {"ok": done.returncode == 0,
            "said": said[-1] if said else "",
            "address": _my_address()}


# --- controls ---------------------------------------------------------------


def do(what: str, value: str) -> dict:
    """One of the few things the page can ask the speaker to do."""
    try:
        if what == "stop":
            import eggs
            return {"said": eggs.stop_everything()}
        if what == "say":
            import tts
            threading.Thread(target=tts.speak, args=(value[:200],),
                             daemon=True).start()
            return {"said": "Saying it."}
        if what == "sound":
            import sounds
            return {"said": sounds.play(value, config.SOUND_HOURS)
                    if value else sounds.stop()}
        if what == "volume":
            import tts
            now = tts.set_volume(value)
            return {"said": "" if now is None else f"Volume {now}%.",
                    "volume": now}
        if what == "music":
            # Spotify's own level, which is separate: turning the speaker
            # down should not change what the music is set to when it
            # comes back up.
            import music
            return {"said": music.music_volume(value)}
        if what == "timer":
            import timers
            return {"said": timers.add_timer(int(float(value) * 60), "")}
        if what == "restart":
            # Answer first; the process is about to go away.
            threading.Timer(0.5, lambda: subprocess.run(
                ["systemctl", "--user", "restart", "claude-speaker"])).start()
            return {"said": "Restarting. This page will come back in half a "
                            "minute."}
    except Exception as error:
        return {"said": f"That didn't work — {type(error).__name__}."}
    return {"said": f"I don't know how to {what}."}


class Pages(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):                                     # noqa: N802
        global last_request_at
        last_request_at = time.time()
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                return self._send(PAGE.encode(), "text/html; charset=utf-8")
            if path == "/api/speaker":
                return self._json(speaker_state())
            if path == "/api/system":
                return self._json(system_state())
            if path == "/api/wishes":
                return self._json(wishes_state())
            if path == "/api/wifi":
                return self._json(wifi_state())
        except Exception as error:
            return self._json({"error": f"{type(error).__name__}: {error}"})
        self.send_error(404)

    def do_POST(self):                                    # noqa: N802
        global last_request_at
        last_request_at = time.time()
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self.send_error(400)
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/wifi":
                # `join` is the default so the page that shipped before
                # saved networks existed goes on working unchanged.
                doing = body.get("action", "join")
                name = body.get("name", "")
                if doing == "save":
                    return self._json(save_wifi(name,
                                                body.get("password", ""),
                                                bool(body.get("hidden"))))
                if doing == "forget":
                    return self._json(forget_wifi(name))
                return self._json(join_wifi(name, body.get("password", "")))
            if path == "/api/do":
                return self._json(do(body.get("what", ""),
                                     str(body.get("value", ""))))
        except Exception as error:
            return self._json({"said": f"{type(error).__name__}: {error}"})
        self.send_error(404)

    def _json(self, data) -> None:
        self._send(json.dumps(data).encode(), "application/json")

    def _send(self, body: bytes, kind: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass          # The speaker's log is for the speaker.


PAGE = r"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Claude Speaker</title>
<style>
:root{color-scheme:light dark;--edge:#8883;--dim:#8889;--good:#3a3;--bad:#c44;
  --series:#2a78d6;      /* one series, one colour */
  --wash:#2a78d612;      /* the region where it fires */
  --axis:#8884}
@media (prefers-color-scheme:dark){
  :root{--series:#3987e5;--wash:#3987e51f}
}
svg text{fill:var(--dim);font-size:9px;font-family:system-ui,sans-serif}
svg text.value{fill:currentColor;font-weight:600}
*{box-sizing:border-box}
body{font:15px/1.5 system-ui,sans-serif;margin:0;padding:1.5rem 1rem 3rem}
main{max-width:52rem;margin:0 auto}
h1{font-size:1.15rem;margin:0 0 .25rem}
.sub{color:var(--dim);font-size:.85rem;margin-bottom:1.25rem}
nav{display:flex;gap:.5rem;margin-bottom:1.25rem;flex-wrap:wrap}
nav button{font:inherit;padding:.4rem .9rem;border-radius:999px;
  border:1px solid var(--edge);background:transparent;cursor:pointer}
nav button[aria-selected=true]{background:#8882;font-weight:600}
.card{border:1px solid var(--edge);border-radius:12px;padding:1rem 1.1rem;
  margin-bottom:1rem}
.card h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.06em;
  color:var(--dim);margin:0 0 .75rem;font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(7rem,1fr));
  gap:.75rem}
.tile{border:1px solid var(--edge);border-radius:10px;padding:.7rem .8rem}
.tile b{display:block;font-size:1.5rem;font-weight:600;line-height:1.2}
.tile span{color:var(--dim);font-size:.78rem}
.row{display:flex;justify-content:space-between;gap:1rem;padding:.35rem 0;
  border-bottom:1px solid var(--edge)}
.row:last-child{border:0}
.row span{color:var(--dim)}
.pill{border:1px solid var(--edge);border-radius:999px;padding:.05rem .55rem;
  font-size:.8rem}
.on{border-color:var(--good);color:var(--good)}
.off{border-color:var(--bad);color:var(--bad)}
button.act{font:inherit;padding:.55rem .9rem;border-radius:9px;
  border:1px solid var(--edge);background:transparent;cursor:pointer}
button.act:hover{background:#8881}
.acts{display:flex;gap:.5rem;flex-wrap:wrap}
input{font:inherit;padding:.5rem .6rem;border-radius:9px;width:100%;
  border:1px solid var(--edge);background:transparent;color:inherit}
label{display:block;font-size:.8rem;color:var(--dim);margin:.6rem 0 .2rem}
input[type=range]{width:100%;accent-color:var(--ink);height:1.6rem;
  background:none;cursor:pointer}
.vol{display:flex;align-items:center;gap:.8rem;margin-top:.3rem}
.vol b{min-width:3.2rem;text-align:right;font-variant-numeric:tabular-nums}
.said{margin-top:.75rem;font-size:.88rem}
.net{display:flex;justify-content:space-between;align-items:center;
  padding:.4rem 0;border-bottom:1px solid var(--edge);cursor:pointer}
.net:hover{background:#8881}
.warn{border-color:var(--bad)}
svg{display:block;width:100%;height:auto}
.turn{padding:.45rem 0;border-bottom:1px solid var(--edge);font-size:.9rem}
.turn:last-child{border:0}
.turn i{color:var(--dim);font-style:normal;font-size:.78rem}
</style>
<main>
<h1>Claude Speaker</h1>
<div class=sub id=where></div>
<nav>
  <button id=t-speaker aria-selected=true onclick="go('speaker')">Speaker</button>
  <button id=t-system aria-selected=false onclick="go('system')">System</button>
  <button id=t-wishes aria-selected=false onclick="go('wishes')">Wishes</button>
  <button id=t-wifi aria-selected=false onclick="go('wifi')">Wi-Fi</button>
</nav>
<div id=body></div>
</main>
<script>
let tab='speaker', timer=null;
const $=(s)=>document.querySelector(s);
const esc=(s)=>String(s??'').replace(/[<>&]/g,m=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[m]));
const pct=(n)=>(n==null?'—':n.toFixed(0)+'%');

function go(which){
  tab=which;
  for(const t of ['speaker','system','wishes','wifi'])
    $('#t-'+t).setAttribute('aria-selected', String(t===which));
  draw();
}
async function get(p){ const r=await fetch(p); return r.json() }
async function post(p,b){
  const r=await fetch(p,{method:'POST',body:JSON.stringify(b)});
  return r.json();
}
async function act(what,value){
  const out=await post('/api/do',{what,value:value??''});
  const s=$('#said'); if(s) s.textContent=out.said||'';
  setTimeout(draw,600);
}

// Charts are drawn in a fixed coordinate space and scaled uniformly. The
// first version stretched a 100x34 box to whatever height the column
// happened to be, which turned every bar into a tower with a screenful of
// dead air above it and no numbers anywhere.
const W=320, H=132, L=34, R=8, T=14, B=22;   // box, and room for the axes
const plotW=W-L-R, plotH=H-T-B, base=T+plotH;

function axes(ticks, maxY){
  // A baseline, one hairline at the top of the scale, and the numbers that
  // go with them. Recessive: hairline, solid, one step off the surface.
  return `<line x1="${L}" y1="${base}" x2="${W-R}" y2="${base}" `+
         `stroke="var(--axis)" stroke-width="1"/>`+
         `<line x1="${L}" y1="${T}" x2="${W-R}" y2="${T}" `+
         `stroke="var(--axis)" stroke-width="1" opacity=".5"/>`+
         `<text x="${L-5}" y="${base+3}" text-anchor="end">0</text>`+
         (maxY===null?'':
           `<text x="${L-5}" y="${T+3}" text-anchor="end">${maxY}</text>`)+
         ticks.map(([x,label])=>`<text x="${x}" y="${H-7}" `+
           `text-anchor="middle">${label}</text>`).join('');
}
function column(x, w, h, fill){
  // Rounded at the data end, square at the baseline.
  const r=Math.min(3, w/2, h);
  if(h<=0.5) return `<rect x="${x}" y="${base-0.6}" width="${w}" height="0.6" `+
                    `fill="${fill}" opacity=".45"/>`;
  return `<path d="M${x},${base} V${base-h+r} q0,-${r} ${r},-${r} `+
         `h${w-2*r} q${r},0 ${r},${r} V${base} Z" fill="${fill}"/>`;
}

function hourly(values){
  if(!values.length||!values.some(v=>v)) return '<div class=sub>nothing yet</div>';
  const top=Math.max(...values), band=plotW/values.length;
  const w=Math.min(24, band-2.5), biggest=values.indexOf(top);
  return `<svg viewBox="0 0 ${W} ${H}" role=img `+
    `aria-label="how many times it woke, by hour of the day">`+
    // No number on the top of the scale: the direct label above the
    // tallest bar is the same figure, and printing it twice is clutter.
    axes([0,6,12,18,23].map(h=>[L+band*(h+0.5), String(h).padStart(2,'0')]), null)+
    values.map((v,i)=>{
      const x=L+band*i+(band-w)/2, h=v/top*plotH;
      return column(x,w,h,'var(--series)')+
        `<rect x="${L+band*i}" y="${T}" width="${band}" height="${plotH}" `+
        `fill="transparent"><title>${String(i).padStart(2,'0')}:00 — `+
        `${v} time${v===1?'':'s'}</title></rect>`;
    }).join('')+
    // One direct label, on the hour that stands out. Never on every bar,
    // and never repeating a number the axis already carries.
    (top>0?`<text class=value x="${L+band*(biggest+0.5)}" `+
      `y="${base-plotH-4}" text-anchor="middle">${top}</text>`:'')+
    `</svg>`;
}

function histogram(scores, threshold){
  if(!scores.length) return '<div class=sub>nothing yet</div>';
  // Only windows that scored above WAKE_NEAR are ever written down, so the
  // bottom of the range is empty by construction rather than by accident.
  // Showing it as empty space reads as missing data, so the axis starts
  // where the data does.
  const lo=Math.max(0, Math.min(threshold, Math.min(...scores))-0.02), hi=1;
  const span=hi-lo, bins=new Array(20).fill(0);
  for(const s of scores) bins[Math.max(0,Math.min(19,
    Math.floor((s-lo)/span*20)))]++;
  const top=Math.max(...bins), band=plotW/20, w=Math.min(24, band-2.5);
  const atX=(v)=>L+((v-lo)/span)*plotW;
  const fires=scores.filter(s=>s>=threshold).length;
  return `<svg viewBox="0 0 ${W} ${H}" role=img `+
    `aria-label="what the wake word scored, and where it fires">`+
    // The region that wakes the speaker, as a wash rather than a second
    // colour — it is the same series, not another one.
    `<rect x="${atX(threshold)}" y="${T}" width="${W-R-atX(threshold)}" `+
      `height="${plotH}" fill="var(--wash)"/>`+
    axes([lo, lo+span/2, hi].map(v=>[atX(v), v.toFixed(2)]), top)+
    bins.map((v,i)=>{
      const x=L+band*i+(band-w)/2, h=v/top*plotH;
      const from=(lo+span*i/20).toFixed(2), to=(lo+span*(i+1)/20).toFixed(2);
      return column(x,w,h,'var(--series)')+
        `<rect x="${L+band*i}" y="${T}" width="${band}" height="${plotH}" `+
        `fill="transparent"><title>${from}–${to}: ${v}</title></rect>`;
    }).join('')+
    `<line x1="${atX(threshold)}" y1="${T}" x2="${atX(threshold)}" `+
      `y2="${base}" stroke="var(--bad)" stroke-width="1"/>`+
    // One annotation, above the plot, where nothing can collide with it.
    `<text class=value x="${L}" y="${T-5}">${fires} of ${scores.length} `+
      `woke it — the line is ${threshold}</text>`+
    `</svg>`;
}

function overTime(points, unit, floor, ceil){
  if(points.length<2) return '<div class=sub>collecting…</div>';
  const lo=floor??Math.min(...points), hi=ceil??Math.max(...points,1);
  const h=104, top=10, plot=h-top-20, baseY=top+plot;
  const at=(v)=>baseY-((v-lo)/((hi-lo)||1))*plot;
  const x=(i)=>L+i*(plotW/(points.length-1));
  const now=points[points.length-1];
  return `<svg viewBox="0 0 ${W} ${h}" role=img aria-label="the last half hour">`+
    `<line x1="${L}" y1="${baseY}" x2="${W-R}" y2="${baseY}" `+
      `stroke="var(--axis)" stroke-width="1"/>`+
    `<text x="${L-5}" y="${baseY+3}" text-anchor="end">${lo}</text>`+
    `<text x="${L-5}" y="${top+3}" text-anchor="end">${hi}</text>`+
    `<text x="${L}" y="${h-6}">30 min ago</text>`+
    `<text x="${W-R}" y="${h-6}" text-anchor="end">now</text>`+
    `<polyline points="${points.map((v,i)=>x(i)+','+at(v).toFixed(1)).join(' ')}" `+
      `fill=none stroke="var(--series)" stroke-width="2" `+
      `stroke-linejoin=round stroke-linecap=round/>`+
    `<circle cx="${x(points.length-1)}" cy="${at(now)}" r="4" `+
      `fill="var(--series)"/>`+
    `<text class=value x="${W-R}" y="${at(now)-8}" text-anchor="end">`+
      `${now}${unit}</text>`+
    `</svg>`;
}

// When the volume slider was last touched. The page redraws every five
// seconds and rebuilds its own HTML, which would otherwise snatch the
// slider back to wherever the Pi last said it was, mid-drag.
let adjusting = 0;

async function draw(){
  if(tab==='speaker') return drawSpeaker();
  if(tab==='system') return drawSystem();
  if(tab==='wishes') return drawWishes();
  return drawWifi();
}

async function drawSpeaker(){
  const d=await get('/api/speaker'), w=d.wake||{};
  const doing=[d.talking&&'talking', d.ringing&&'a timer is ringing',
    d.sound&&('playing '+d.sound), d.music&&('music: '+d.music),
    d.book&&(`reading ${d.book.title}, chapter ${d.book.chapter} of ${d.book.chapters}`)
  ].filter(Boolean);
  $('#where').textContent='what it is doing, and how it is';
  $('#body').innerHTML=
   `<div class=card><h2>Right now</h2>`+
    (doing.length?doing.map(x=>`<div class=row><span>${esc(x)}</span></div>`).join('')
      :'<div class=sub>listening, and otherwise idle</div>')+
    `<div class=acts style="margin-top:.9rem">
      <button class=act onclick="act('stop')">Stop everything</button>
      <button class=act onclick="act('sound','rain')">Rain</button>
      <button class=act onclick="act('sound','')">Silence</button>
      <button class=act onclick="act('timer','5')">5 min timer</button>
      <button class=act onclick="act('say','Hello from the dashboard')">Say hello</button>
     </div>
     <label>How loud it is</label>
     <div class=vol>
       <input id=vol type=range min=0 max=100 step=5
              value="${d.volume==null?70:d.volume}"
              oninput="adjusting=Date.now();document.getElementById('volshow').textContent=this.value+'%'"
              onchange="adjusting=Date.now();act('volume',this.value)">
       <b id=volshow>${d.volume==null?'—':d.volume+'%'}</b>
     </div><div class=said id=said></div></div>`+

   (d.timers.length?`<div class=card><h2>Timers and alarms</h2>`+
     d.timers.map(t=>`<div class=row><span>${esc(t.name)}</span>`+
       `<b>${Math.floor(t.seconds_left/60)}m ${t.seconds_left%60}s</b></div>`).join('')+
     `</div>`:'')+

   `<div class=card><h2>The wake word</h2><div class=tiles>
      <div class=tile><b>${w.fired_today??'—'}</b><span>woke today</span></div>
      <div class=tile><b>${w.answered_today??'—'}</b><span>were real</span></div>
      <div class=tile><b>${w.threshold??'—'}</b><span>threshold</span></div>
      <div class=tile><b>${w.labelled??'—'}</b><span>labelled by hand</span></div>
     </div>
     <div style="margin-top:1.1rem"><div class=sub>how many times it woke,
       by hour of the day</div>${hourly(w.by_hour||[])}</div>
     <div style="margin-top:1.1rem"><div class=sub>what it scored, and where
       it wakes</div>${histogram(w.scores||[], w.threshold||0.5)}
       <div class=sub>Only windows scoring above 0.50 are written down, so
        this is the top of the distribution and not all of it.</div></div>
     <div class=row style="margin-top:.75rem"><span>model</span>
       <span>${esc(w.model||'')} ${w.fitted?'· fitted '+esc(w.fitted):''}
       ${w.dataset?'· '+esc(w.dataset):''}</span></div></div>`+

   `<div class=card><h2>Lately</h2>`+
    (d.recent.length?d.recent.map(t=>`<div class=turn>`+
      (t.said?`${esc(t.said)}<br>`:'')+
      `<i>${new Date(t.at*1000).toLocaleTimeString()} · ${esc(t.kind)}`+
      (t.score?` · scored ${t.score}`:'')+`</i>`+
      (t.answer?`<br><i>${esc(t.answer)}</i>`:'')+`</div>`).join('')
      :'<div class=sub>nothing yet</div>')+`</div>`;
}

async function drawSystem(){
  const d=await get('/api/system');
  const gb=(n)=>(n/1e9).toFixed(1)+' GB';
  const bad=Object.entries(d.throttling||{}).filter(([,v])=>v);
  $('#where').textContent=`${d.address} · ${d.cores} cores · up ${d.uptime_hours}h`;
  $('#body').innerHTML=
   `<div class=card><h2>Now</h2><div class=tiles>
     <div class=tile><b>${pct(d.cpu)}</b><span>processor</span></div>
     <div class=tile><b>${d.temperature}°</b><span>temperature</span></div>
     <div class=tile><b>${pct(d.memory.used_percent)}</b><span>memory of ${gb(d.memory.total)}</span></div>
     <div class=tile><b>${pct(d.disk.used_percent)}</b><span>disk, ${gb(d.disk.free)} free</span></div>
    </div></div>`+
   `<div class=card><h2>Last half hour</h2>
     <div class=sub>processor, percent</div>
     ${overTime((d.history||[]).map(h=>h.cpu),'%',0,100)}
     <div class=sub style="margin-top:1rem">temperature, °C</div>
     ${overTime((d.history||[]).map(h=>h.temp),'°',30,85)}
     <div class=sub style="margin-top:1rem">memory, percent</div>
     ${overTime((d.history||[]).map(h=>h.memory),'%',0,100)}</div>`+
   (bad.length?`<div class="card warn"><h2>The power supply</h2>`+
     bad.map(([k])=>`<div class=row><span>${esc(k)}</span><b>yes</b></div>`).join('')+
     `<div class=sub style="margin-top:.6rem">Under-voltage makes a Pi slow
      rather than broken, so it is easy to miss. A better supply or a
      shorter cable usually fixes it.</div></div>`:'')+
   `<div class=card><h2>Services</h2>`+
    Object.entries(d.services).map(([n,s])=>`<div class=row><span>${esc(n)}</span>`+
      `<span class="pill ${s==='active'?'on':'off'}">${esc(s)}</span></div>`).join('')+
    `<div class=row><span>load</span><span>${d.load.join('  ')}</span></div>
     <div class=acts style="margin-top:.9rem">
       <button class=act onclick="act('restart')">Restart the speaker</button>
     </div><div class=said id=said></div></div>`;
}

async function drawWishes(){
  const d=await get('/api/wishes');
  $('#where').textContent=`${d.total} asked for, across every speaker`;
  if(!d.wishes.length){
    $('#body').innerHTML=`<div class=card><h2>Nothing yet</h2><div class=sub>
      When somebody asks for something this can't do, it writes it down
      here instead of just saying no.</div></div>`+
      (d.said?`<div class=card><div class=sub>${esc(d.said)}</div></div>`:'');
    return;
  }
  $('#body').innerHTML = d.wishes.map(w=>`<div class=card>
     <div class=row><b style="font-weight:600">${esc(w.wish)}</b>
       <span>${w.times>1?w.times+'&times;':''}</span></div>
     ${w.askeds.map(a=>`<div class=sub style="margin:.3rem 0 0">
        &ldquo;${esc(a)}&rdquo;</div>`).join('')}
     <div class=sub style="margin-top:.5rem;opacity:.7">
       ${w.speakers.length?esc(w.speakers.join(', '))+' &middot; ':''}
       ${w.whos.length?esc(w.whos.join(', '))+' &middot; ':''}
       ${esc((w.last||'').slice(0,10))}</div></div>`).join('');
}

async function drawWifi(){
  const d=await get('/api/wifi');
  $('#where').textContent=`${d.address} · ${d.connected||'not connected'}`;
  $('#body').innerHTML=
   ((d.rescue&&d.rescue.active)?`<div class=card
      style="border-color:#c87b2a"><h2>This is my own network</h2>
      <div class=sub>I couldn't get onto any Wi-Fi, so I made this one
      (<b>${esc(d.rescue.name)}</b>) so you could reach me. There's no
      internet on it, so I can't answer questions until I'm back on a real
      network. Type yours in below and I'll go and join it.</div></div>`:'')+
   `<div class=card><h2>Connected to</h2>
     <div class=row><span>${esc(d.connected)||'nothing'}</span>
       <b>${d.signal?d.signal+'%':''}</b></div>
     <div class=row><span>address</span><b>${esc(d.address)}</b></div>`+
     (d.can_change?'':`<div class=sub style="margin-top:.6rem">This machine
       won't let me change the network. Run <b>./deploy.sh</b> once from the
       laptop to allow it.</div>`)+`</div>`+
   `<div class=card><h2>Add a network</h2>
     <label>Name</label><input id=ssid value="${esc(d.connected)}">
     <label>Password</label><input id=pw type=password placeholder="leave empty if open">
     <label style="margin-top:.6rem"><input id=hidden type=checkbox
       style="width:auto;margin-right:.4rem">Hidden network</label>
     <div class=acts style="margin-top:.9rem">
       <button class=act onclick="wifi('join')">Join now</button>
       <button class=act onclick="wifi('save')">Save for later</button></div>
     <div class=said id=said></div>
     <div class=sub style="margin-top:.6rem"><b>Join now</b> needs the network
      to be in range, and if it works this page moves to a new address.
      <b>Save for later</b> does not — type in the network somewhere else and
      it joins when it gets there.</div></div>`+
   `<div class=card><h2>Saved</h2>`+
    ((d.saved||[]).length?'':`<div class=sub>Nothing saved yet.</div>`)+
    (d.saved||[]).map(s=>`<div class=net>`+
      `<span>${esc(s.ssid)}${s.current?' <b>· on it now</b>':''}</span>`+
      `<span><button class=act style="padding:.15rem .5rem;font-size:.75rem"
        onclick="forget('${esc(s.name)}')">Forget</button></span></div>`).join('')+
    `</div>`+
   `<div class=card><h2>In range</h2>`+
    d.networks.map(n=>`<div class=net onclick="document.getElementById('ssid').value='${esc(n.name)}'">`+
      `<span>${esc(n.name)}</span><span class=sub>${n.signal}%</span></div>`).join('')+
    `</div>`;
}
async function wifi(action){
  const name=$('#ssid').value, password=$('#pw').value;
  const hidden=$('#hidden')&&$('#hidden').checked;
  $('#said').textContent=action==='save'?'Saving…':'Joining…';
  const out=await post('/api/wifi',{action,name,password,hidden});
  $('#said').textContent=(out.said||'')+(out.said_extra?' '+out.said_extra:'')+
    (out.ok&&out.address?` Now at ${out.address}.`:'');
  if(out.ok&&action==='save'&&!out.leaving) drawWifi();
}
async function forget(name){
  $('#said').textContent='…';
  const out=await post('/api/wifi',{action:'forget',name});
  $('#said').textContent=out.said||'';
  if(out.ok) drawWifi();
}

draw();
setInterval(()=>{
  if(tab!=='wifi' && Date.now()-adjusting > 4000) draw();
}, 5000);
</script>"""


if __name__ == "__main__":
    import sys

    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit
    _started = False
    threading.Thread(target=_watch_machine, daemon=True).start()
    print(f"http://{_my_address()}:{PORT}")
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Pages).serve_forever()
