"""Spotify — playing actual music, on the Pi.

    "hey claude, play Baby Shark"
    "hey claude, skip this"
    "hey claude, turn it down"
    "hey claude, stop the music"

Two halves, and they meet in the middle.

librespot runs on the Pi as a service and makes it a Spotify Connect
speaker — the same thing as a Sonos or a smart TV, as far as Spotify is
concerned. No password goes anywhere near the Pi: you pick it once in the
phone app, which is how it gets its credentials, and they are cached after
that.

This file is the other half: the Web API, which searches and says what to
play where. It never touches audio. That matters — the speaker does not
decode or resample a note of it, so an hour of music costs the Pi about as
much as an hour of silence.

Sharing the speaker
-------------------
Music and speech go through PipeWire, which mixes them, so nothing has to
be closed and reopened the way sounds.py and books.py do. But mixing is
not what you want when somebody asks a question over the top of a song —
you want the song to get quieter. So the volume is pulled down while the
speaker talks and put back afterwards, which Spotify does on its own side
in one API call.

    python src/music.py                  what's playing, and where
    python src/music.py rain on me       find it, and play it
"""

import base64
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import config
import sounds
import tools

ACCOUNTS = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"

# The most Spotify will return per search. Their documentation says 50;
# an app in development mode gets 400 "Invalid limit" above ten, which is
# not what the message suggests. Measured: 10 works, 11 does not.
MOST = 10

# How far to duck the music while the speaker is talking, as a fraction of
# whatever it was. Quiet enough to be talked over, loud enough that it
# plainly hasn't stopped.
DUCK_TO = 0.2


def _quietly(what: str, error) -> None:
    """Say a Spotify problem once, not once a minute.

    A speaker that answers questions all evening does this on every turn,
    and a line per turn is how a log stops being read.
    """
    global _no_ducking
    if "403" in str(error):
        # The device won't be told what volume to be. It never will be, so
        # stop asking.
        _no_ducking = True
        print(f"[music] {what}: this device won't allow it — not trying again")
        return
    print(f"[music] {what}: {type(error).__name__}")


def _nothing_playing(error) -> bool:
    """Spotify answers 404 when there is no active device.

    That is the ordinary case for "stop the music" said in a quiet room,
    and printing it as an error every time is noise in the log.
    """
    return "404" in str(error)

_lock = threading.RLock()
_token = ""
_token_until = 0.0
_offered: list[dict] = []
_ducked_from: int | None = None
_no_ducking = False


def ready() -> bool:
    return bool(config.SPOTIFY_REFRESH_TOKEN and config.SPOTIFY_CLIENT_ID)


# --- talking to Spotify -----------------------------------------------------


def _access() -> str:
    """A usable access token, refreshed when it goes stale.

    They last an hour, which is shorter than an evening, so this is not an
    optimisation — a speaker that stopped working after an hour would be a
    puzzling thing to debug.
    """
    global _token, _token_until
    with _lock:
        if _token and time.monotonic() < _token_until:
            return _token

        secret = base64.b64encode(
            f"{config.SPOTIFY_CLIENT_ID}:{config.SPOTIFY_CLIENT_SECRET}"
            .encode()).decode()
        request = urllib.request.Request(
            ACCOUNTS,
            data=urllib.parse.urlencode({
                "grant_type": "refresh_token",
                "refresh_token": config.SPOTIFY_REFRESH_TOKEN}).encode(),
            headers={"Authorization": f"Basic {secret}",
                     "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request, timeout=20) as response:
            got = json.load(response)
        _token = got["access_token"]
        # A minute early, so a call never starts on a token that expires
        # while it's in flight.
        _token_until = time.monotonic() + int(got.get("expires_in", 3600)) - 60
        return _token


def _call(method: str, path: str, body: dict | None = None):
    """One Web API call. Returns the reply, or {} when there's no body."""
    request = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {_access()}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        # Not everything answers with JSON. Skipping a track comes back 200
        # with a bare tracking id in the body, which is not an error and
        # must not be treated as one.
        return {}


def _ours(join: str = "?") -> str:
    """`device_id=` for this speaker, so commands land here and not in a car."""
    mine = _speaker_id()
    return f"{join}device_id={mine}" if mine else ""


def _speaker_id() -> str | None:
    """The Pi's own Spotify device, by the name librespot advertises."""
    wanted = config.SPOTIFY_DEVICE.strip().lower()
    for device in _call("GET", "/me/player/devices").get("devices", []):
        if device["name"].strip().lower() == wanted:
            return device["id"]
    return None


# --- taking the speaker back when somebody has borrowed it -----------------
#
# Spotify Connect is one account per device, so a guest picking this
# speaker in their own app does not play alongside us — it signs librespot
# in as them, and the account the speaker searches with can no longer see
# the device at all.
#
# The fix is lazy on purpose. Nothing patrols for this and nothing snatches
# the speaker back on a timer: a guest keeps it for as long as nobody in
# the room asks for anything. The moment somebody says "play ...", we take
# it back, because their music was already coming out of this speaker in
# this room — whoever is standing in front of it has the better claim.
#
# Measured on this Pi: restarting librespot and waiting for the device to
# be playable again takes 1.4 to 1.6 seconds, which is less than the
# speaker spends transcribing the question that asked for it.

OWNER = Path(__file__).resolve().parent.parent / "state" / "spotify-owner.json"


def _owner_account() -> str:
    """Whose speaker this is: the account the refresh token belongs to.

    Nothing to configure. This is already the account that has to own
    librespot for any of it to work, so it can define itself.
    """
    try:
        return _call("GET", "/me").get("id", "")
    except Exception:
        return ""


def _remember_how_to_come_back() -> None:
    """Keep a copy of librespot's credentials while they are still ours.

    Kept in state/ rather than beside the code, because deploy mirrors the
    project directory and would delete it — the same reason enrolment
    recordings live there.
    """
    try:
        if logged_in_as() != _owner_account():
            return
        OWNER.parent.mkdir(parents=True, exist_ok=True)
        if CREDENTIALS.exists() and (
                not OWNER.exists()
                or OWNER.read_bytes() != CREDENTIALS.read_bytes()):
            OWNER.write_bytes(CREDENTIALS.read_bytes())
            OWNER.chmod(0o600)
    except Exception:
        pass            # Nice to have. Never worth failing a song over.


def claim() -> str | None:
    """This speaker's Spotify id, taking it back first if it has wandered.

    Returns None only when it genuinely cannot be had — no saved
    credentials to go back to, or librespot will not come up.
    """
    mine = _speaker_id()
    if mine:
        _remember_how_to_come_back()
        return mine
    if not config.MUSIC_TAKE_BACK or not OWNER.exists():
        return None

    borrowed = logged_in_as()
    print(f"[music] the speaker is signed in as {borrowed or 'nobody'} — "
          f"taking it back")
    try:
        CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
        CREDENTIALS.write_bytes(OWNER.read_bytes())
        CREDENTIALS.chmod(0o600)
    except Exception as error:
        print(f"[music] couldn't put our credentials back ({error})")
        return None
    if not _restart_librespot():
        return None

    # It was 1.6 s at its slowest here; wait a good deal longer than that
    # before giving up, because the alternative is telling a child no.
    for _ in range(40):
        time.sleep(0.5)
        mine = _speaker_id()
        if mine:
            print("[music] got it back")
            return mine
    print("[music] it didn't come back")
    return None


# --- noticing when it has quietly stopped being a speaker -------------------
#
# librespot can be running and useless, and systemd cannot tell. It sat for
# five days reporting active with its last word being "Connection to server
# closed", no sockets open and no processor used, while `Restart=always`
# never fired — because that only fires when a process *exits*, and this one
# did not. From the outside the only way to know is to ask Spotify whether
# the speaker exists.
#
# There is a second way to lose it that a restart does not fix. librespot
# caches whoever last connected to it, so when somebody else's phone picks
# this speaker, it is logged into their account from then on — and the
# account the speaker searches with can no longer see it. That happened
# here on the 9th of September and was invisible until somebody asked for a
# song. The two faults look identical from `_speaker_id()`, so the check
# below tells them apart before acting, because restarting librespot cures
# the first and does nothing at all for the second.

CREDENTIALS = Path.home() / ".cache" / "librespot" / "credentials.json"

_missing_since = 0.0
_last_restart = 0.0
# The last thing the watcher found, for anybody who wants to show it. Held
# rather than asked for: the dashboard redraws every five seconds and this
# answer costs a call to Spotify.
_health = "not checked yet"
_visible = None


def logged_in_as() -> str:
    """The account librespot cached, which is not necessarily ours."""
    try:
        return json.loads(CREDENTIALS.read_text()).get("username", "")
    except Exception:
        return ""


def health() -> dict:
    """Whether this speaker can actually be played to, and why not.

    `visible` is the only question that matters — everything else is here
    to explain a no.
    """
    out = {"configured": ready(), "visible": False, "said": _health,
           "account": "", "librespot_account": logged_in_as()}
    if not ready():
        out["said"] = "no Spotify set up"
        return out
    try:
        out["account"] = _call("GET", "/me").get("id", "")
        out["visible"] = _speaker_id() is not None
    except Exception as error:
        out["said"] = f"couldn't ask Spotify ({type(error).__name__})"
        return out

    if out["visible"]:
        out["said"] = "on Spotify"
    elif out["librespot_account"] and out["account"] \
            and out["librespot_account"] != out["account"]:
        out["said"] = (
            "somebody else's Spotify has the speaker — it will take itself "
            "back next time anyone asks for a song"
            if config.MUSIC_TAKE_BACK and OWNER.exists() else
            "librespot is signed in to a different Spotify account — open "
            f"Spotify and pick {config.SPOTIFY_DEVICE} once")
    else:
        out["said"] = "not showing up on Spotify"
    return out


def _restart_librespot() -> bool:
    import subprocess
    try:
        done = subprocess.run(
            ["systemctl", "--user", "restart", "librespot"],
            capture_output=True, text=True, timeout=30)
        return done.returncode == 0
    except Exception:
        return False


def _watch() -> None:
    global _missing_since, _last_restart, _health, _visible
    # Look shortly after starting rather than waiting out a whole interval,
    # so the dashboard is not saying "not checked yet" for the first five
    # minutes of every boot. The sleep stays at the top of the loop because
    # every `continue` below relies on it.
    wait = 20.0
    while True:
        time.sleep(wait)
        wait = config.MUSIC_WATCH_EVERY
        try:
            state = health()
            _health = state["said"]
            _visible = state["visible"]
            if state["visible"] or not state["configured"]:
                _missing_since = 0.0
                continue

            if state["librespot_account"] and state["account"] \
                    and state["librespot_account"] != state["account"]:
                # Restarting would just sign it back in as the wrong person.
                # Say so once in a while instead, and leave it alone.
                if time.time() - _last_restart > 3600:
                    _last_restart = time.time()
                    print(f"[music] {state['said']}")
                continue

            if not _missing_since:
                _missing_since = time.time()
                continue
            waited = time.time() - _missing_since
            if waited < config.MUSIC_WATCH_PATIENCE * 60:
                continue
            if time.time() - _last_restart < config.MUSIC_WATCH_PATIENCE * 60:
                continue
            _last_restart = time.time()
            print(f"[music] not on Spotify for {waited / 60:.0f} min — "
                  f"restarting librespot")
            _restart_librespot()
            _missing_since = 0.0
        except Exception as error:
            print(f"[music] {type(error).__name__}: {error}")


def start() -> None:
    """Keep an eye on whether this is still a Spotify speaker."""
    if not config.MUSIC_WATCH or not ready():
        return
    threading.Thread(target=_watch, daemon=True).start()


# --- getting out of the way -------------------------------------------------


def duck() -> None:
    """Pull the music down while the speaker talks — our music, at least.

    Only ever this speaker's own playback. An account is signed in to
    several things, and ducking whatever it happens to be playing
    elsewhere is both wrong and impossible: this spent an evening trying
    to turn down a Tesla, once every time it answered a question, and
    being told 403 Cannot control device volume by a car.
    """
    global _ducked_from, _no_ducking
    if not ready() or _no_ducking:
        return
    try:
        with _lock:
            if _ducked_from is not None:
                return  # Already down; a beep inside an answer must not
                        # take the ducked volume for the real one.
            now = _call("GET", "/me/player")
            if not now or not now.get("is_playing"):
                return
            device = now.get("device") or {}
            if device.get("id") != _speaker_id():
                return          # Somebody else's speaker. Not ours to touch.
            if not device.get("supports_volume", True):
                return
            was = device.get("volume_percent")
            if was is None:
                return
            _ducked_from = was
        _call("PUT", f"/me/player/volume?volume_percent={int(was * DUCK_TO)}")
    except Exception as error:
        _ducked_from = None
        _quietly("couldn't duck", error)


def unduck() -> None:
    """Put it back."""
    global _ducked_from
    if not ready():
        return
    with _lock:
        was, _ducked_from = _ducked_from, None
    if was is None:
        return
    try:
        _call("PUT", f"/me/player/volume?volume_percent={int(was)}")
    except Exception as error:
        _quietly("couldn't put the volume back", error)


sounds.also_pause(duck, unduck)


# --- what Claude can ask for ------------------------------------------------


@tools.tool(
    "Search Spotify for music. This does NOT play anything — it hands back "
    "what Spotify has, and you choose.\n\n"
    "Read the results before picking. A song title match is very often the "
    "wrong recording: a children's song has dozens of versions by different "
    "people, and there are karaoke tracks, covers, remixes and lullaby "
    "renditions of almost everything. Prefer the well known recording — "
    "usually the most popular — unless they asked for something else. When "
    "somebody asks for an artist or a kind of music rather than a song, "
    "search for that and pick an album or playlist.\n\n"
    "When one is clearly right, call play_music with its number straight "
    "away. Ask only when you genuinely cannot tell which they meant.",
    properties={
        "search": {
            "type": "string",
            "description": "What to search Spotify for: a song, an artist, "
                           "an album, or a kind of music.",
        },
        "kind": {
            "type": "string",
            "description": "track, album, or playlist. Use track for a "
                           "named song, playlist for a mood or an activity.",
        },
    },
    required=["search"],
    says="play music from Spotify",
)
def find_music(search: str, kind: str = "track") -> str:
    global _offered
    if not ready():
        return "Spotify isn't set up on this speaker."
    kind = kind if kind in ("track", "album", "playlist") else "track"

    try:
        found = _call("GET", "/search?" + urllib.parse.urlencode(
            {"q": search, "type": kind, "limit": str(MOST)}))
    except Exception as error:
        print(f"[music] search failed: {type(error).__name__}: {error}")
        return "I couldn't reach Spotify just now."

    items = [i for i in found.get(kind + "s", {}).get("items", []) if i]
    _offered = items
    if not items:
        return f"Spotify has nothing for {search}."

    lines = []
    for number, item in enumerate(items, 1):
        who = ", ".join(a["name"] for a in item.get("artists", []))
        if kind == "playlist":
            who = (item.get("owner") or {}).get("display_name", "")
            extra = f"{(item.get('tracks') or {}).get('total', 0)} tracks"
        elif kind == "album":
            extra = (f"{item.get('total_tracks', 0)} tracks, "
                     f"{(item.get('release_date') or '')[:4]}")
        else:
            seconds = item.get("duration_ms", 0) // 1000
            extra = (f"{seconds // 60}:{seconds % 60:02d}, "
                     f"popularity {item.get('popularity', 0)}")
        lines.append(f"{number}. {item.get('name', '?')}"
                     + (f" — {who}" if who else "") + f" ({extra})")
    return ("Choose one and call play_music with its number:\n"
            + "\n".join(lines))


@tools.tool(
    "Play one of the things find_music offered, by its number, on this "
    "speaker.",
    properties={
        "number": {
            "type": "integer",
            "description": "Which one, counting from 1.",
        },
    },
    required=["number"],
)
def play_music(number: int) -> str:
    if not _offered:
        return "Search for something first with find_music."
    if not 1 <= int(number) <= len(_offered):
        return f"There were only {len(_offered)} to choose from."

    chosen = _offered[int(number) - 1]
    try:
        device = claim()
        if device is None:
            return ("This speaker isn't showing up in Spotify. Open Spotify "
                    "on a phone and pick "
                    f"{config.SPOTIFY_DEVICE} once, and it will stay.")

        where = f"/me/player/play?device_id={device}"
        if chosen["type"] == "track":
            _call("PUT", where, {"uris": [chosen["uri"]]})
        else:
            _call("PUT", where, {"context_uri": chosen["uri"]})
    except Exception as error:
        print(f"[music] {type(error).__name__}: {error}")
        return "Spotify wouldn't start that. Is the Premium account free?"

    who = ", ".join(a["name"] for a in chosen.get("artists", []))
    return f"Playing {chosen['name']}" + (f" by {who}" if who else "") + "."


@tools.tool("Stop, pause, or start the music again. Use for 'stop the "
            "music', 'pause', 'carry on'.",
            properties={"what": {
                "type": "string",
                "description": "'pause' or 'play'.",
            }},
            required=["what"])
def pause_music(what: str) -> str:
    try:
        if what.strip().lower().startswith(("play", "resume", "carry", "un")):
            _call("PUT", "/me/player/play" + _ours())
            return "Music going again."
        _call("PUT", "/me/player/pause" + _ours())
        return "Music paused."
    except Exception as error:
        if not _nothing_playing(error):
            print(f"[music] {type(error).__name__}: {error}")
        return "Nothing is playing."


@tools.tool("Skip to the next song, or back to the one before.",
            properties={"which": {
                "type": "string",
                "description": "'next' or 'back'.",
            }},
            required=["which"])
def skip_music(which: str) -> str:
    try:
        if which.strip().lower().startswith(("back", "prev", "last")):
            _call("POST", "/me/player/previous" + _ours())
            return "Went back one."
        _call("POST", "/me/player/next" + _ours())
        time.sleep(0.6)   # Give Spotify a moment to load the next one.
        return f"Skipped. Now playing {now_playing()}."
    except Exception as error:
        if not _nothing_playing(error):
            print(f"[music] {type(error).__name__}: {error}")
        return "Nothing is playing."


# How much "a bit louder" moves it.
STEP = 15


@tools.tool(
    "Change how loud the music is. Only the music — it does not change how "
    "loud you are. Say 'up' or 'down' for a step, or give a number from 0 "
    "to 100 if they asked for one.",
    properties={"level": {
        "type": "string",
        "description": "'up', 'down', 'mute', or a number from 0 to 100.",
    }},
    required=["level"])
def music_volume(level: str) -> str:
    """Louder, quieter, or a number.

    It used to take a percentage and nothing else, which meant that asked
    to "turn it down a bit" Claude had no number to give and said it had
    turned the music down without turning anything down. A tool that can
    only be called with something the caller cannot know is a tool that
    invites being talked around.
    """
    global _ducked_from
    said = str(level).strip().lower()
    try:
        with _lock:
            # If it's ducked right now, the real volume is the one being
            # held for afterwards, not the quiet one Spotify reports.
            current = _ducked_from
        if current is None:
            now = _call("GET", "/me/player")
            current = (now.get("device") or {}).get("volume_percent", 50)

        if said.startswith(("up", "loud", "high", "more")):
            wanted = current + STEP
        elif said.startswith(("down", "quiet", "low", "soft", "less")):
            wanted = current - STEP
        elif said.startswith(("mute", "silen", "off")):
            wanted = 0
        else:
            digits = "".join(c for c in said if c.isdigit())
            if not digits:
                return f"I didn't understand {level!r}."
            wanted = int(digits)
        wanted = max(0, min(100, wanted))

        with _lock:
            if _ducked_from is not None:
                # Change what it comes back to, or the answer being spoken
                # right now would undo what was just asked for.
                _ducked_from = wanted
                return f"Music at {wanted} percent."
        _call("PUT", f"/me/player/volume?volume_percent={wanted}"
              + _ours("&"))
    except Exception as error:
        print(f"[music] {type(error).__name__}: {error}")
        return "I couldn't change the volume."
    return f"Music at {wanted} percent."


@tools.tool("Say what music is playing.")
def what_music() -> str:
    playing = now_playing()
    return f"Playing {playing}." if playing else "No music playing."


def now_playing() -> str:
    try:
        now = _call("GET", "/me/player")
    except Exception:
        return ""
    if not now or not now.get("item"):
        return ""
    item = now["item"]
    who = ", ".join(a["name"] for a in item.get("artists", []))
    return f"{item.get('name','?')}" + (f" by {who}" if who else "")


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    if not ready():
        raise SystemExit("Spotify isn't set up — see train/spotify_login.py")

    if len(sys.argv) > 1:
        print(find_music(" ".join(sys.argv[1:])))
        print(play_music(1))
    else:
        for device in _call("GET", "/me/player/devices").get("devices", []):
            mark = " <- this Pi" if device["name"] == config.SPOTIFY_DEVICE else ""
            print(f"  {device['name'][:28]:30} {device['type']:12} "
                  f"active={device['is_active']}{mark}")
        print(f"  now: {now_playing() or 'nothing'}")
