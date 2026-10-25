"""When it can't get onto a network, it becomes one.

A speaker with the wrong Wi-Fi is a brick: the dashboard is where you fix
the network, and the dashboard is on the network. So when it has been
unable to join anything for a while, it raises an access point of its own,
says so out loud, and serves the same dashboard to whoever joins.

    can't join anything for RESCUE_AFTER minutes
        -> raise an access point
        -> say the name and password out loud, and light the ring amber
        -> somebody joins it with a phone and types in the real network
        -> drop the access point and go and use it

There is one radio
------------------
This is the whole reason the rules below exist. An access point is not a
second network alongside the house — it is instead of it. While it is up
the speaker has no internet: Claude cannot answer, the weather is stale,
music will not play. It is a repair mode and it has to be a short one.

The failure to design against is not "the access point didn't come up". It
is this:

    router reboots
        -> speaker loses the network for ninety seconds
        -> speaker raises an access point
        -> router comes back
        -> speaker is not on it, and is not looking

A speaker that deserts a working network is harder to live with than one
that occasionally needs setting up. Hence: wait minutes rather than
seconds, never do it while Ethernet is carrying traffic, give up if nobody
turns up, and stand down the moment there are credentials to try.

The password
------------
It cannot be open. An open access point puts a page with no password on it,
which can change your Wi-Fi and shows what was said in the room, in front
of anybody walking past. So WPA2 — and the speaker reads the password out
when it announces itself, which is a thing a speaker can do and a headless
box cannot. Nothing is printed on the bottom of anything.

It has to survive being heard once. "hey claude" spoken aloud is three
questions rather than a password — one word or two, capitals or not, was
that a space — and a Wi-Fi password is case-sensitive and gets one attempt
before the phone says it is wrong. So the default is `heyclaude`: one word,
all lower case, and the announcement says so. Set AP_PASSWORD in .env to
change it; whatever you set, spoken_password() describes its shape.
"""

import subprocess
import threading
import time

import config

# NetworkManager names the profile it makes for a hotspot, and we delete it
# again on the way out. A leftover profile with autoconnect on would race
# the real networks for the radio at the next boot.
PROFILE = "claude-speaker-rescue"

_up_since = 0.0
_adrift_since = 0.0
_said_it = False
_lock = threading.Lock()


def _run(*args, timeout=30) -> tuple[bool, str]:
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout)
        said = (done.stderr or done.stdout).strip().splitlines()
        return done.returncode == 0, (said[-1] if said else "")
    except Exception as error:
        return False, f"{type(error).__name__}"


def _devices() -> dict:
    """Every interface and what it is doing, from NetworkManager."""
    out = {}
    try:
        listed = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return out
    for line in listed.splitlines():
        parts = line.split(":")
        if len(parts) >= 3:
            out[parts[0]] = {"type": parts[1], "state": parts[2]}
    return out


def on_a_network() -> bool:
    """Is it usefully connected to something that is not itself?

    The access point makes wlan0 read as `connected`, which would otherwise
    look exactly like success and leave it up for ever.
    """
    if active():
        return False
    for name, what in _devices().items():
        if what["type"] in ("wifi", "ethernet") and what["state"] == "connected":
            return True
    return False


def ethernet_up() -> bool:
    """A cable is plugged in and working, so there is nothing to rescue."""
    return any(w["type"] == "ethernet" and w["state"] == "connected"
               for w in _devices().values())


def active() -> bool:
    """Is our own access point up right now?"""
    try:
        listed = subprocess.run(
            ["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return False
    return PROFILE in listed.split()


def state() -> dict:
    """What the dashboard shows, and what the page says while it is up."""
    return {"active": active(),
            "name": config.AP_NAME,
            "password": config.AP_PASSWORD,
            "since": int(time.time() - _up_since) if _up_since else 0,
            "adrift": int(time.time() - _adrift_since) if _adrift_since else 0}


def _wifi_device() -> str:
    return next((n for n, w in _devices().items() if w["type"] == "wifi"),
                "wlan0")


def _hold_the_radio(device: str, hold: bool) -> None:
    """Stop NetworkManager taking the radio back while we are an AP.

    Raising a hotspot succeeds and then quietly loses: NetworkManager sees
    a saved network it could be on, decides it would rather be on that, and
    reclaims the device seconds later. Its own log says so plainly —
    "Device 'wlan0' successfully activated", and then "Connected to
    wireless network solus" — while nmcli has already returned 0 and gone.

    Turning autoconnect off for the whole device is one call and covers
    every saved profile, including ones added after this was written.
    """
    _run("nmcli", "device", "set", device, "autoconnect",
         "no" if hold else "yes")


def up() -> bool:
    """Raise the access point. Returns whether it is actually up."""
    global _up_since, _said_it
    with _lock:
        if active():
            return True
        device = _wifi_device()
        _hold_the_radio(device, True)
        ok, said = _run("nmcli", "device", "wifi", "hotspot",
                        "ifname", device, "con-name", PROFILE,
                        "ssid", config.AP_NAME,
                        "password", config.AP_PASSWORD)
        if not ok:
            print(f"[rescue] couldn't raise the access point — {said}")
            _hold_the_radio(device, False)
            return False
        # Never let it come back on its own. This profile exists to be torn
        # down, and a hotspot that autoconnects at boot would take the radio
        # away from the networks that actually work.
        _run("nmcli", "connection", "modify", PROFILE,
             "connection.autoconnect", "no")

        # Check, rather than believe the return code. The first version of
        # this trusted nmcli, said "access point up", announced itself out
        # loud, and was wrong — the radio was back on the house network
        # before the sentence finished. Telling somebody to join a network
        # that does not exist is worse than saying nothing.
        for _ in range(10):
            time.sleep(1)
            if active():
                break
        else:
            print("[rescue] the access point came up and was taken away again")
            _run("nmcli", "connection", "delete", PROFILE)
            _hold_the_radio(device, False)
            return False

        _up_since = time.time()
        _said_it = False
        print(f"[rescue] access point up — {config.AP_NAME}")
        _announce()
        return True


def down(why: str = "") -> None:
    """Lower it, and leave no profile behind."""
    global _up_since
    with _lock:
        if not active() and not _profile_exists():
            return
        _run("nmcli", "connection", "down", PROFILE)
        _run("nmcli", "connection", "delete", PROFILE)
        _hold_the_radio(_wifi_device(), False)
        _up_since = 0.0
        print(f"[rescue] access point down{' — ' + why if why else ''}")
    try:
        import lights
        lights.show("idle")
    except Exception:
        pass


def _profile_exists() -> bool:
    try:
        listed = subprocess.run(["nmcli", "-t", "-f", "NAME", "connection",
                                 "show"], capture_output=True, text=True,
                                timeout=8).stdout
    except Exception:
        return False
    return any(line.strip() == PROFILE for line in listed.splitlines())


def spoken_password(word: str) -> str:
    """Say a password in a way somebody can actually type.

    Heard once, out loud, "hey claude" is three questions rather than a
    password: one word or two, capitals or not, and was that a space. A
    Wi-Fi password is case-sensitive and gets one attempt before the phone
    says it is wrong, so the announcement describes the shape as well as
    the letters.
    """
    if word.isdigit():
        return ", ".join(word)          # digit by digit; nothing to mishear
    shape = []
    if " " not in word:
        shape.append("all one word")
    if word == word.lower():
        shape.append("all lower case")
    return word + (", " + " and ".join(shape) if shape else "")


def _announce() -> None:
    """Say it out loud, and light the ring in a colour used for nothing else.

    This is the part a headless box cannot do. Somebody standing in the
    room needs to learn three things — that it is stuck, what network to
    join, and the password — and it can simply tell them.
    """
    global _said_it
    if _said_it:
        return
    _said_it = True
    try:
        import lights
        lights.show("rescue")
    except Exception:
        pass
    try:
        import tts
        said = spoken_password(config.AP_PASSWORD)
        # The name is picked from a list and the password is typed, so only
        # the password has to survive being heard. It is also said twice,
        # because you get one go at writing down something spoken.
        tts.speak(
            f"I can't get onto the network. I've made one of my own. "
            f"On your phone, join {config.AP_NAME}. "
            f"The password is {said}. Again, the password is {said}. "
            f"Then I'll show you how to fix me.")
    except Exception as error:
        print(f"[rescue] couldn't say so ({type(error).__name__})")


def _somebody_is_here() -> bool:
    """Has anyone actually turned up since the access point came up?

    Asking the page rather than the radio. A phone that associates and then
    wanders off is not somebody fixing the Wi-Fi; a request to the
    dashboard is. It is also the signal that survives having no way to ask
    the driver, which `iw` — not installed here — would otherwise provide.
    """
    try:
        import dashboard
        return dashboard.last_request_at > _up_since
    except Exception:
        return False


def _adopt() -> None:
    """Take ownership of an access point that was already up."""
    global _up_since
    _up_since = time.time()
    print("[rescue] adopting an access point that was already up")


def _watch() -> None:
    global _adrift_since
    while True:
        time.sleep(config.RESCUE_EVERY)
        try:
            if active():
                if not _up_since:
                    # Up, but not by us — the service restarted while it was
                    # running, or somebody raised it by hand. Adopt it and
                    # start the clock now. Without this the elapsed time is
                    # measured from the epoch, which is comfortably longer
                    # than any give-up window, and the access point is torn
                    # down a few seconds after the speaker comes back.
                    _adopt()
                waited = time.time() - _up_since
                if _somebody_is_here():
                    continue            # Being used. Leave it alone.
                if waited > config.RESCUE_GIVE_UP * 60:
                    # Nobody came. Go back to looking for the real networks
                    # — the house may simply have come back while we sat
                    # here being an access point nobody wanted.
                    down("nobody joined")
                    _adrift_since = 0.0
                continue

            if on_a_network() or ethernet_up():
                _adrift_since = 0.0
                continue

            if not _adrift_since:
                _adrift_since = time.time()
                print("[rescue] not on a network — waiting to see if it comes back")
                continue
            if time.time() - _adrift_since > config.RESCUE_AFTER * 60:
                up()
        except Exception as error:
            print(f"[rescue] {type(error).__name__}: {error}")


def start() -> None:
    """Watch the network, from a thread, for as long as the speaker runs."""
    if not config.RESCUE:
        return
    # Anything we left behind last time, before anything else races it —
    # including the radio being held, which would otherwise leave the
    # speaker unable to join anything at all after a crash mid-rescue.
    if _profile_exists():
        down("left over from last time")
    _hold_the_radio(_wifi_device(), False)
    threading.Thread(target=_watch, daemon=True).start()


if __name__ == "__main__":
    import sys

    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit
    if "--up" in sys.argv:
        up()
    elif "--down" in sys.argv:
        down("asked to")
    else:
        print(f"  on a network : {on_a_network()}")
        print(f"  ethernet     : {ethernet_up()}")
        print(f"  rescue ap up : {active()}")
        print(f"  would use    : {config.AP_NAME} / {config.AP_PASSWORD}")
        print(f"  raises after : {config.RESCUE_AFTER} min adrift")
        print(f"  gives up after: {config.RESCUE_GIVE_UP} min unvisited")
