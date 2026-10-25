"""Settings, all in one place.

Everything here can be overridden in the .env file. See .env.example.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (the folder above src/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _get(name: str, default: str) -> str:
    """Read a setting from .env, falling back to a default."""
    value = os.environ.get(name, "").strip()
    return value if value else default


# --- Claude ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
# Sonnet, not Opus. Measured on this laptop, answering the kind of question
# a kid asks out loud: Opus 2.85s, Sonnet 1.52s, Haiku 0.87s — and all three
# gave the same answers. Claude is about two thirds of the silence between
# a question and a reply, so this is the cheapest speed there is.
# Set CLAUDE_MODEL in .env to change it.
CLAUDE_MODEL = _get("CLAUDE_MODEL", "claude-sonnet-5")

# How many past turns to remember. One turn is a question plus an answer.
HISTORY_TURNS = int(_get("HISTORY_TURNS", "10"))

# Let Claude search the web when a question turns on something recent.
# The search runs on Anthropic's side, not on the Pi, so it costs no local
# compute — but it does cost money per search and adds a few seconds to the
# answer. Set WEB_SEARCH=off in .env to turn it off.
WEB_SEARCH = _get("WEB_SEARCH", "on").lower() not in ("off", "0", "false", "no")

# Searches allowed per question. A speaker should answer, not research;
# this is the ceiling on both the wait and the bill.
WEB_SEARCH_MAX = int(_get("WEB_SEARCH_MAX", "3"))

# Roughly where the speaker is, in whatever form you'd say out loud —
# "Palo Alto, California". The time zone already narrows this down, but a
# town makes questions like "how long until it gets dark" answerable.
# Leave it empty if you'd rather not tell it.
LOCATION = _get("LOCATION", "")

# What to call the people it talks to, e.g. "Tejas and his family". Only
# used to make it sound less like a stranger. Empty is fine.
HOUSEHOLD = _get("HOUSEHOLD", "")

# --- Voice out ---
# The macOS voice, used by the `say` command. See them all with: say -v '?'
VOICE = _get("VOICE", "Samantha")

# The Piper voice, used on Linux — that's what the Raspberry Pi speaks with.
# Listen to them all at https://rhasspy.github.io/piper-samples/
#
# Stick to a "medium" voice on a Pi 4. Measured on this one, synthesising a
# two sentence answer: medium takes 0.32 seconds per second of speech, so it
# talks three times faster than it can be listened to; "high" takes 2.0
# seconds per second, so the speaker falls behind its own sentences.
PIPER_VOICE = _get("PIPER_VOICE", "en_GB-alan-medium")

# How fast to talk, in words per minute. Means the same thing on both
# machines — tts.py converts it for Piper.
SPEECH_RATE = int(_get("SPEECH_RATE", "180"))

# The gap between sentences, in seconds. Piper's own is about a fifth of a
# second once the silence it leaves at both ends is added up, which sounds
# slow read aloud. Trimmed and replaced with this, so the spacing is a
# choice. Linux only — macOS `say` does its own pacing.
SENTENCE_PAUSE = float(_get("SENTENCE_PAUSE", "0.12"))

# How loud, as a percentage. USB speakers often arrive attenuated — the
# reSpeaker array came set about 20 dB down, quiet enough that you reach for
# the volume knob and find it already at maximum — so the speaker turns
# itself up at startup. Lower it if it's too much at night; set it to
# nothing at all to leave the system mixer alone.
OUTPUT_VOLUME = _get("OUTPUT_VOLUME", "100")

# Which speaker to play out of. Leave empty for the system default, or set
# part of a device name or a device number.
# List them with:  python src/tts.py --devices
#
# Worth setting on a Raspberry Pi: ALSA lists the HDMI outputs first, so the
# default is often a monitor with no speakers rather than the real ones.
OUTPUT_DEVICE = _get("OUTPUT_DEVICE", "")

# --- Microphone ---
# Which microphone to use. Leave empty for the system default, or set it to
# part of a device name ("MacBook Air Microphone") or a device number.
# List the choices with:  python src/audio_in.py --devices
#
# Worth checking: if you have BlackHole, Loopback, or a virtual meeting
# device installed, macOS may pick that as the default input — and those
# record what the computer is playing, not what you're saying.
INPUT_DEVICE = _get("INPUT_DEVICE", "")

# 16 kHz mono is what Whisper and the wake word detector both want.
SAMPLE_RATE = 16_000
# 80 ms of audio per chunk. openWakeWord likes chunks of 1280 samples.
BLOCK_SIZE = 1280

# Stop recording after this much silence (seconds).
SILENCE_SECONDS = float(_get("SILENCE_SECONDS", "1.0"))

# How much audio from just before recording starts to keep, so running
# straight from the wake word into the question doesn't lose the first word.
# Raise it if the start of questions still gets clipped.
PRE_ROLL_SECONDS = float(_get("PRE_ROLL_SECONDS", "0.5"))
# Always record at least this long, so a slow start doesn't cut you off.
MIN_RECORD_SECONDS = float(_get("MIN_RECORD_SECONDS", "0.7"))
# Give up after this long even if you're still talking.
MAX_RECORD_SECONDS = float(_get("MAX_RECORD_SECONDS", "20.0"))

# How long the array may deliver nothing before the speaker decides it has
# gone deaf and does something about it. A working microphone hands over a
# chunk every 80 ms even in a silent room, so anything approaching a second
# is already wrong; ten is unambiguous and leaves room for a slow moment.
MIC_DEAF_SECONDS = float(_get("MIC_DEAF_SECONDS", "10"))

# --- Speech to text ---
WHISPER_MODEL = _get("WHISPER_MODEL", "base.en")

# --- Wake word ---
WAKE_MODE = _get("WAKE_MODE", "auto")  # auto | openwakeword | key
WAKE_MODEL = _get("WAKE_MODEL", "hey_jarvis")
# How confident the detector has to be (0 to 1). Raise it if the speaker
# keeps waking up on its own; lower it if it ignores you.
WAKE_THRESHOLD = float(_get("WAKE_THRESHOLD", "0.5"))
# Whether that was actually chosen, or is just the default here. A model
# fitted by train/relearn.py carries the operating point its sweep picked,
# and should be allowed to use it unless somebody has said otherwise.
WAKE_THRESHOLD_SET = bool(os.environ.get("WAKE_THRESHOLD", "").strip())

# How often a Whisper-based wake word looks, in seconds. It reads a two
# second window, and the phrase is under one, so the phrase falls inside
# several consecutive windows and doesn't have to be caught first time.
# Shorter costs proportionally more compute for slightly less delay.
# Ignored by openWakeWord models, which score every 80 ms chunk.
WAKE_STRIDE_SECONDS = float(_get("WAKE_STRIDE_SECONDS", "0.4"))

# Write down every time the wake word fires, and how the turn went, so the
# mistakes can be learned from. About 1.5 kB per firing, plus the audio.
WAKE_LOG = _get("WAKE_LOG", "on").lower() not in ("off", "0", "false", "no")

# How many recordings of firings to keep. The 768 numbers are kept forever
# — they're what retraining needs — but the audio is 64 kB a time on an SD
# card, so only the most recent are held on to.
WAKE_LOG_CLIPS = int(_get("WAKE_LOG_CLIPS", "1500"))

# Also write down windows that came close but didn't fire — the times
# somebody said it and nothing happened. Logging only what fires teaches
# the speaker nothing about the half of the problem it is worse at.
WAKE_NEAR = float(_get("WAKE_NEAR", "0.5"))

# How far back to keep near misses in memory when a wake word fires.
WAKE_NEAR_SECONDS = float(_get("WAKE_NEAR_SECONDS", "15"))

# And how recent the one before a firing has to be to count as somebody
# repeating themselves. Short: a person who says a thing twice says it
# again within a couple of seconds, and anything longer is the room.
WAKE_REPEAT_SECONDS = float(_get("WAKE_REPEAT_SECONDS", "4"))

# And a slow trickle of near misses that were never followed by success,
# because whoever it was gave up. Seconds between samples.
WAKE_NEAR_EVERY = float(_get("WAKE_NEAR_EVERY", "180"))


# --- Timers ---
# How long a finished timer keeps ringing if nobody says anything. It beeps
# in bursts with gaps to listen in, so you can stop it by saying the wake
# word; this is the backstop for when nobody is in the room.
RING_SECONDS = float(_get("RING_SECONDS", "30"))


# --- The LED ring on the microphone array ---
# Shows what the speaker is doing: blue for listening, green for talking,
# red when a timer goes off. Set LEDS=off if you'd rather it stayed dark.
LEDS = _get("LEDS", "on").lower() not in ("off", "0", "false", "no")

# Out of 255. The ring is genuinely bright — this is a lamp on a shelf in
# the evening, not a status light in a server room.
LED_BRIGHTNESS = int(_get("LED_BRIGHTNESS", "40"))


# --- Background sounds ---
# How loud rain, ocean and the rest play, from 0 to 1. Quiet on purpose:
# this is something to fall asleep to, and the microphone has to hear you
# over it.
SOUND_VOLUME = float(_get("SOUND_VOLUME", "0.30"))

# How long they play if nobody says. Hours.
SOUND_HOURS = float(_get("SOUND_HOURS", "8"))

# A key for Freesound, which is by far the better place to look up a
# recording of a bullfrog. Free, from https://freesound.org/apiv2/apply/.
# Without one it falls back to Wikimedia Commons, which needs no account
# and is noticeably worse at this.
FREESOUND_KEY = _get("FREESOUND_API_KEY", "")


# --- Learning from the other speakers ---
# Each speaker fits on the shipped corpus plus its own firings, so a new
# one starts knowing nothing about this house. With this on, the nightly
# retraining also trains on every other speaker's labelled firings from
# the shared archive — their data, never their operating point: the
# threshold is still chosen on this room.
POOL = _get("POOL", "on").lower() not in ("off", "0", "false", "no")

# --- Keeping what it learns ---
# A Hugging Face token, so each retraining can commit the data it used and
# stamp the model with that commit. Without one, retraining still works and
# nothing is archived. https://huggingface.co/settings/tokens — a
# fine-grained token with write access to the one dataset repository is
# enough.
HF_TOKEN = _get("HF_TOKEN", "")


# --- Which speaker this is ---
# One name per unit, and the only thing that has to differ between them.
# It is the folder this speaker's data goes under in the shared dataset,
# what it calls itself on Spotify, and the network it raises when it is
# lost. Defaults to the hostname, which is already unique on a network.
SPEAKER_NAME = _get("SPEAKER_NAME", "") or __import__("socket").gethostname()

# --- When it can't get onto a network ---
# After this long unable to join anything, the speaker raises an access
# point of its own and says so, so you can fix the Wi-Fi from a phone. See
# src/rescue.py. Minutes, not seconds: a router reboot must pass without
# the speaker deserting the network over it.
RESCUE = _get("RESCUE", "on").lower() not in ("off", "0", "false", "no")
RESCUE_AFTER = float(_get("RESCUE_AFTER", "5"))
# And if nobody joins it in this long, tear it down and go back to looking.
# An unattended speaker has to end up back on the house network by itself
# once the house network returns.
RESCUE_GIVE_UP = float(_get("RESCUE_GIVE_UP", "15"))
RESCUE_EVERY = float(_get("RESCUE_EVERY", "20"))

# What that access point is called, and the password for it. It cannot be
# open: an open one puts a page with no password, which can change your
# Wi-Fi and shows what was said in the room, in front of anybody walking
# past. The speaker reads the password out when it announces itself, so the
# default is the one string everybody in the house already knows rather
# than something random nobody could type. WPA2 needs eight characters.
AP_NAME = _get("AP_NAME", f"Claude Speaker {SPEAKER_NAME}")
# One word and all lower case on purpose: it is read out loud and then
# typed into a phone, where a space or a capital is a failed attempt with
# no explanation. rescue.spoken_password() describes whatever is set here.
AP_PASSWORD = _get("AP_PASSWORD", "heyclaude")

# --- The dashboard ---
# A page served from the Pi, for a browser on the same network: what the
# speaker is doing, how the machine is, and which Wi-Fi it is on.
#
# It has no password. Anybody who can reach the Pi can see what was asked
# and change the network, so this belongs on a home network and nowhere
# else. Set DASHBOARD=off to turn it off.
DASHBOARD = _get("DASHBOARD", "on").lower() not in ("off", "0", "false", "no")
DASHBOARD_PORT = _get("DASHBOARD_PORT", "8080")


# --- Spotify ---
# Playing music needs a Spotify Premium account: librespot, which does the
# streaming on the Pi, cannot play at all on a free one.
#
# The id and secret come from a free app at
# https://developer.spotify.com/dashboard, and the refresh token from
# running train/spotify_login.py once on a machine with a browser. The
# speaker never sees your password.
SPOTIFY_CLIENT_ID = _get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = _get("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REFRESH_TOKEN = _get("SPOTIFY_REFRESH_TOKEN", "")

# librespot can run perfectly while being no use at all — see src/music.py.
# So the speaker asks Spotify every so often whether it can still be played
# to, and restarts librespot if it cannot. Minutes, because this costs an
# API call and the failure it catches lasted five days before anybody
# noticed.
MUSIC_WATCH = _get("MUSIC_WATCH", "on").lower() not in ("off", "0", "false", "no")
MUSIC_WATCH_EVERY = float(_get("MUSIC_WATCH_EVERY", "300"))
MUSIC_WATCH_PATIENCE = float(_get("MUSIC_WATCH_PATIENCE", "10"))

# If a guest's phone has claimed this speaker on Spotify, take it back the
# next time somebody in the room asks for a song. Off means say so instead.
MUSIC_TAKE_BACK = _get("MUSIC_TAKE_BACK", "on").lower() not in ("off", "0", "false", "no")

# What the Pi calls itself in the Spotify app's device list.
# Derived, so two speakers cannot both be "Claude Speaker". music.py finds
# this Pi in the account's device list by name and takes the first match,
# so with two of them a song asked for in one room could start in the
# other — and nothing would look broken.
SPOTIFY_DEVICE = _get("SPOTIFY_DEVICE", f"Claude Speaker {SPEAKER_NAME}")


if __name__ == "__main__":
    # Print what the speaker will actually use, which is not always what you
    # think you set — .env overrides these, and a typo in a name is silent.
    import sys

    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    print(f"Settings, as read from {PROJECT_ROOT / '.env'}:\n")
    for name, value in sorted(globals().items()):
        if name.isupper() and isinstance(value, (str, int, float)):
            # Never print the key, only whether there is one.
            if "API_KEY" in name:
                value = f"<{len(str(value))} characters>" if value else "<not set>"
            print(f"  {name:22s} {value!r}")
