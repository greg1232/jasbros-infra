"""Put the Claude Speaker on a Raspberry Pi.

    ./deploy.sh normal@192.168.4.95   name the Pi (remembered afterwards)
    ./deploy.sh                       deploy again to the same Pi
    ./deploy.sh --run                 deploy, then start it and watch
    ./deploy.sh --service             deploy, and start it on every boot

Safe to run as many times as you like: it only re-does the parts that
changed. The first run takes several minutes, mostly downloading Python
packages onto the Pi; later runs take seconds.

Your API key travels over the SSH connection and lands in a file only you
can read. It is never printed, and never committed — .env is in .gitignore
on both machines.
"""

import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REMOTE_DIR = "claude-speaker"        # relative to the Pi user's home
TARGET_FILE = HERE / ".deploy-target"  # so you only name the Pi once

# Everything the speaker needs and nothing it doesn't: no git history, no
# virtualenv built for a different CPU, no training data, and no .env —
# that one goes separately, with tighter permissions. Logs and the pidfile
# are excluded because --delete removes whatever isn't here, and wiping the
# log on every deploy throws away the record of what you were investigating.
EXCLUDE = (".git/", ".venv/", ".env", ".deploy-target", "__pycache__/",
           "*.pyc", "voices/", "state/", "train/data/", "train/voices/",
           "train/hey_claude/", "train/room/", "*.png", "*.log*", "*.pid",
           ".DS_Store")

APT_PACKAGES = ("python3-venv", "libportaudio2", "libsndfile1", "espeak-ng",
                "alsa-utils",
                # Lets ALSA programs — which is everything here — go through
                # PipeWire instead of seizing the sound card. Without it the
                # microphone array really does allow one thing at a time, so
                # music and speech cannot both exist.
                "pipewire-alsa")

# The Pi is a different machine, so a few settings can't come across as they
# are: the microphone name is the laptop's, and the voice is a macOS one.
# Everything else — your key, your town, the wake word — carries over.
PI_ONLY = ("INPUT_DEVICE", "OUTPUT_DEVICE", "OUTPUT_VOLUME", "VOICE",
           "PIPER_VOICE", "WHISPER_MODEL", "WAKE_MODEL", "WAKE_THRESHOLD",
           "WAKE_STRIDE_SECONDS")

PI_SETTINGS = """
# ---- Added by deploy.sh, because this is the Pi and not the laptop ----

# The Pi speaks with Piper, not the macOS `say` command.
# Listen to the choices at https://rhasspy.github.io/piper-samples/
# Keep to a "medium" voice: measured on a Pi 4, medium needs 0.32 seconds
# of compute per second of speech, but "high" needs 2.0 — slower than
# talking, so the speaker would fall behind itself.
PIPER_VOICE=en_GB-alan-medium

# Which microphone and which speaker.
#   python src/audio_in.py --devices     lists microphones
#   python src/tts.py --devices          lists speakers
#
# OUTPUT_DEVICE is set rather than left empty on purpose. A Pi's ALSA
# default is the first HDMI port, and opening it with no monitor plugged in
# fails outright ("audio open error: Unknown error 524") — so the speaker
# would start up and then not be able to say a word. Playing through the
# microphone array is better still when the speakers are wired that way:
# the XVF3800 cancels its own output in hardware.
INPUT_DEVICE=
# Through PipeWire, not straight at the microphone array.
#
# The array allows one program at a time. That was survivable while the
# speaker was the only thing making a noise, and stopped being survivable
# the day librespot started playing music: Spotify took the card, and the
# next thing the speaker tried to say died with "Device unavailable" and
# took the whole program with it.
#
# pipewire-alsa (installed by this script) puts PipeWire in between, so
# music, speech, a story and a timer can all exist at once. It also hands
# back the voice's own 22 kHz instead of the array's 16, so answers stop
# being resampled on the way out.
OUTPUT_DEVICE=pipewire

# This array arrives about 20 dB down, which sounds like a broken speaker
# rather than a quiet one. Turn it down here if it's too loud at night;
# leave it blank to not touch the system mixer at all.
OUTPUT_VOLUME=100

# The wake word, and how sure it has to be.
#
# This was 0.99, and 0.99 was costing about half the recall for nothing.
# Measured on the training bank, holding one child out of training entirely:
#
#     threshold   recall, unseen voice   recall, known voices
#       0.99              38%                    58%
#       0.90              52%                    94%
#       0.80              55%                   100%
#       0.50              66%                   100%
#
# and no measurable change in false wakes anywhere across that range. That
# last part turned out to be worthless: those negatives were mined against
# this same model, and the real room disagreed loudly. At 0.80 it woke
# roughly every twenty seconds with a television on — 108 an hour — so it
# was nearly always mid-turn and could not hear anybody. Measured in the
# room, the false wakes score HIGHER than the real ones (median 0.991
# against 0.933), so there is no clean cut anywhere; the threshold only
# buys rate, not separation.
#
# What makes a low threshold affordable is that a false wake is now silent:
# nothing said means nothing spoken, and television speech gets (nothing)
# back from Claude. It costs a flash of the LED ring and some CPU.
#
# Measure yours on the Pi itself, and read what it actually logged:
#     python train/test_wake.py --times 6
#     python src/wake_log.py
WAKE_MODEL=hey_claude_whisper.npz
# Left unset on purpose. A model fitted by train/relearn.py carries the
# operating point its own sweep chose, and two models are not comparable at
# a single number — they sit on different score distributions, so the same
# threshold means different things to each. Set this only to argue with the
# sweep.
# WAKE_THRESHOLD=0.95

# How often the Whisper wake word looks, in seconds. About 42% of one core
# at 0.4 on a Pi 4. Raise it to spend less, and be noticed a little later.
WAKE_STRIDE_SECONDS=0.4

# A Pi 4 is much slower than a laptop at speech recognition, so use the
# small model. Change it to base.en if it mishears too often and you don't
# mind waiting longer.
WHISPER_MODEL=tiny.en
"""

# A user service, not a system one, so deploying never needs a password.
# systemctl --user needs no sudo, and `loginctl enable-linger` — which the
# user can also run for themselves — is what makes it start at boot without
# anyone logging in. The speaker has no reason to be root: it needs the
# audio group, which the user is already in, and PipeWire is running in
# their session anyway.
#
# Restart=always rather than on-failure, because at boot the network may
# not be up yet and the first question would fail. Retrying costs nothing.
SERVICE = """[Unit]
Description=Claude Speaker

[Service]
Type=simple
WorkingDirectory=%h/{dir}
ExecStart=%h/{dir}/.venv/bin/python src/main.py
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""

UNIT_PATH = ".config/systemd/user/claude-speaker.service"


def step(message: str) -> None:
    print(f"\n==> {message}")


def indent(text: str) -> None:
    for line in text.rstrip().splitlines():
        print(f"    {line}")


def can_ask_for_a_password() -> bool:
    """Whether sudo has anywhere to ask.

    A deploy run from a terminal can hand the Pi's password prompt to the
    person sitting in front of it. A deploy run from a script has no
    terminal at all, and `ssh -t` then fails outright rather than
    prompting — so the handful of steps that need root once stand aside
    and say what's missing, instead of taking the whole deploy down with
    them. Before this, a deploy with no terminal died on the polkit rule
    and never got as far as copying the code.
    """
    return sys.stdin.isatty()


class Pi:
    """The Raspberry Pi at the other end of an SSH connection."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.user = target.split("@")[0]

    def run(self, command: str, tty: bool = False, check: bool = True,
            quiet: bool = False) -> subprocess.CompletedProcess:
        """Run a command on the Pi. `tty` lets sudo ask for a password."""
        argv = ["ssh"] + (["-t"] if tty else []) + [self.target, command]
        if quiet:
            return subprocess.run(argv, check=check, capture_output=True,
                                  text=True)
        return subprocess.run(argv, check=check)

    def output(self, command: str) -> str:
        return self.run(command, check=False, quiet=True).stdout.strip()

    def reachable(self) -> bool:
        return subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
             self.target, "true"], capture_output=True).returncode == 0

    def send(self, local: Path, remote: str) -> None:
        subprocess.run(["scp", "-q", str(local), f"{self.target}:{remote}"],
                       check=True)

    def python(self, script: str) -> str:
        """Run a Python snippet inside the speaker's virtualenv.

        Shows what went wrong if it fails. Swallowing the error here once
        cost an afternoon: a stray apostrophe made the remote script a
        syntax error, and the step just printed nothing at all.
        """
        done = self.run(
            f"cd ~/{REMOTE_DIR} && .venv/bin/python - <<'PYTHON'\n"
            f"import sys; sys.path.insert(0, 'src')\n{script}\nPYTHON",
            check=False, quiet=True)
        if done.returncode != 0:
            noise = ("ALSA lib", "jack server", "Cannot connect", "JackShm",
                     "Expression", "Unknown PCM")
            real = [line for line in done.stderr.splitlines()
                    if line.strip() and not any(n in line for n in noise)]
            return (done.stdout.strip() + "\n"
                    + "\n".join(real[-6:])).strip()
        return done.stdout.strip()


def local_env() -> Path:
    """The .env to send, checked before we go near the network."""
    env = HERE / ".env"
    if not env.is_file():
        raise SystemExit(
            "There's no .env file here, so there's no API key to send.\n"
            "Make one first:  cp .env.example .env")
    # Read it without printing it, so the key never shows up on screen.
    if not re.search(r"^\s*ANTHROPIC_API_KEY=.+", env.read_text(), re.M):
        raise SystemExit(
            "ANTHROPIC_API_KEY is empty in .env — the Pi would have "
            "nothing to use.")
    return env


def settings_for_pi() -> str:
    """The laptop's settings, with the machine-specific ones replaced.

    Write a .env.pi by hand and that gets sent instead, untouched.
    """
    by_hand = HERE / ".env.pi"
    if by_hand.is_file():
        print("    using .env.pi")
        return by_hand.read_text()
    keep = [line for line in local_env().read_text().splitlines()
            if not any(re.match(rf"\s*{name}=", line) for name in PI_ONLY)]
    return "\n".join(keep) + "\n" + PI_SETTINGS


def resolve_target(named: str | None) -> str:
    """Which Pi, remembering it without forgetting the others.

    This used to overwrite .deploy-target, so deploying to a second
    speaker silently pointed every other command at it too.
    """
    import speakers
    target = speakers.pick(named)
    speakers.remember(target)
    return target


def install_packages(pi: Pi) -> None:
    """sounddevice needs PortAudio; espeak-ng is the fallback voice."""
    missing = pi.output(
        "for p in " + " ".join(APT_PACKAGES) + "; do "
        "dpkg -s $p >/dev/null 2>&1 || printf '%s ' $p; done")
    if not missing:
        indent("already installed")
        return
    indent(f"installing: {missing}")
    if not can_ask_for_a_password():
        raise SystemExit(
            "    ...but there's no terminal here to type the Pi's password\n"
            "    into. Run ./deploy.sh yourself, or ./deploy.sh --no-apt to\n"
            "    deploy without these.")
    pi.run(f"sudo apt-get update -qq && sudo apt-get install -y -qq {missing}",
           tty=True)


# The LED ring is driven over raw USB, and the array's USB node belongs to
# root. This hands it to the plugdev group, which the Pi's own user is
# already in — so the speaker can light its own lights without being root,
# the same bargain as the systemd user service.
UDEV_RULE = ('SUBSYSTEM=="usb", ATTR{idVendor}=="2886", '
             'ATTR{idProduct}=="001a", MODE="0660", GROUP="plugdev"')
UDEV_PATH = "/etc/udev/rules.d/60-respeaker.rules"


def leds_allowed(pi: Pi) -> bool:
    return pi.output(f"cat {UDEV_PATH} 2>/dev/null").strip() == UDEV_RULE


def allow_led_access(pi: Pi, ask: bool = True) -> None:
    """Let the speaker talk to the LED ring without sudo.

    This is the one thing here that genuinely needs root once, so a deploy
    that was asked not to touch system packages says what's needed and
    leaves it rather than springing a password prompt.
    """
    if leds_allowed(pi):
        indent("already allowed")
        return

    if not ask or not can_ask_for_a_password():
        indent("not set up yet — the LED ring will stay dark")
        indent("this needs the Pi's password once:  ./deploy.sh --leds")
        return

    indent("adding a udev rule for the microphone array's LEDs")
    # Re-triggering beats asking anyone to unplug the array; without it the
    # rule only takes effect at the next boot.
    pi.run(f"echo '{UDEV_RULE}' | sudo tee {UDEV_PATH} >/dev/null"
           " && sudo udevadm control --reload-rules"
           " && sudo udevadm trigger --subsystem-match=usb"
           " --attr-match=idVendor=2886", tty=True)


# Spotify's own client for small machines. Its Debian package carries the
# librespot binary; the service that comes with it runs as its own system
# user, which cannot reach this user's PipeWire, so it is turned off and
# replaced with a user service like the speaker's.
RASPOTIFY = ("https://github.com/dtcooper/raspotify/releases/download/"
             "0.48.2/raspotify_0.48.2.librespot.v0.8.0-9c7d756_arm64.deb")

LIBRESPOT_UNIT = """[Unit]
Description=Spotify Connect (librespot)
After=pipewire.service

[Service]
ExecStart=/usr/bin/librespot --name "{name}" --backend pulseaudio \\
    --bitrate 160 --cache %h/.cache/librespot --device-type speaker
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def install_spotify(pi: Pi) -> None:
    """Put librespot on the Pi, as this user's own service.

    No Spotify password goes anywhere near the Pi. librespot advertises
    itself on the network and you pick it once in the phone app, which is
    how it gets its credentials; after that they are cached and the Web
    API can see it as a speaker to play to.
    """
    if pi.output("which librespot").strip():
        indent("librespot already installed")
    elif not can_ask_for_a_password():
        indent("not installed — needs the Pi's password once")
        indent("run ./deploy.sh from a terminal and it will do it")
        return
    else:
        indent("downloading and installing librespot")
        pi.run(f"curl -fsSL -o /tmp/raspotify.deb {RASPOTIFY} && "
               "sudo dpkg -i /tmp/raspotify.deb; "
               # Its own service runs as the raspotify user, which has no
               # PipeWire of its own and so can never make a sound here.
               "sudo systemctl disable --now raspotify 2>/dev/null; true",
               tty=True)

    # The same name src/config.py will compute, derived the same way. If
    # these two disagree the speaker advertises under one name and looks
    # itself up under another, finds nothing, and says it isn't showing up
    # in Spotify — which is true, and baffling.
    unit = LIBRESPOT_UNIT.format(name=spotify_name(pi))
    handle, path = tempfile.mkstemp()
    try:
        Path(path).write_text(unit)
        pi.run("mkdir -p ~/.config/systemd/user", quiet=True)
        pi.send(Path(path), "~/.config/systemd/user/librespot.service")
    finally:
        os.close(handle)
        os.unlink(path)

    pi.run("systemctl --user daemon-reload && "
           "systemctl --user enable --now librespot", quiet=True)
    indent("librespot is "
           + (pi.output("systemctl --user is-active librespot").strip()
              or "not running"))


def speaker_name(pi: Pi) -> str:
    """What this unit calls itself — SPEAKER_NAME, or its hostname.

    Mirrors src/config.py. Deploy cannot just import that: on a first run
    the code is not on the Pi yet when the Spotify service is written.
    """
    return setting("SPEAKER_NAME", "") or pi.output("hostname").strip() \
        or "speaker"


def spotify_name(pi: Pi) -> str:
    return setting("SPOTIFY_DEVICE", "") or f"Claude Speaker {speaker_name(pi)}"


def setting(name: str, fallback: str) -> str:
    """Read one value out of the local .env, without importing anything."""
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip() or fallback
    return fallback


# Learning from the day, at four in the morning, when nobody is talking to
# it and nothing else wants the processor. A user timer, so no root: the
# same bargain as the speaker's own service.
RELEARN_UNIT = """[Unit]
Description=Teach the wake word from today's mistakes

[Service]
Type=oneshot
WorkingDirectory=%h/{where}
ExecStart=%h/{where}/.venv/bin/python train/relearn.py --nightly
"""

RELEARN_TIMER = """[Unit]
Description=Teach the wake word from today's mistakes, nightly

[Timer]
OnCalendar=*-*-* 04:00:00
# If the Pi was off at four, do it when it next comes up rather than
# waiting a day. A week away from home shouldn't cost a week of learning.
Persistent=true
RandomizedDelaySec=15m

[Install]
WantedBy=timers.target
"""


# The array only sends microphone audio while its playback endpoint is
# actually being driven. Leave the sink idle and capture stops dead — no
# error, no xrun, ALSA still reporting the stream RUNNING while hw_ptr sits
# frozen. The wake word simply goes deaf, and nothing anywhere notices,
# because a microphone that returns nothing is indistinguishable from a
# quiet room.
#
# Measured on this Pi: with a 30 second tone playing, capture ran at 16040
# frames a second for exactly as long as the tone lasted and stopped within
# four seconds of it ending. That is also why pressing play on the speaker
# appears to fix it — it does, for as long as the sound lasts.
#
# node.always-process keeps the sink running with no client attached, so the
# device stays clocked and the microphone keeps arriving. Restarting the
# speaker does not fix this, and neither does restarting PipeWire; only
# driving the playback half does.
NO_SUSPEND = """# Written by deploy.py. See docs/troubleshooting.md.
#
# The reSpeaker XVF3800 only sends microphone audio while its playback
# endpoint is being driven. Left idle, capture stops and the wake word goes
# deaf with nothing logged anywhere. Keep both halves awake.
monitor.alsa.rules = [
  {
    matches = [
      { node.name = "~alsa_output.usb-Seeed_Studio_reSpeaker.*" }
      { node.name = "~alsa_input.usb-Seeed_Studio_reSpeaker.*" }
    ]
    actions = {
      update-props = {
        session.suspend-timeout-seconds = 0
        node.always-process = true
        node.pause-on-idle = false
      }
    }
  }
]
"""

WP_CONF = ".config/wireplumber/wireplumber.conf.d/50-respeaker-no-suspend.conf"


def keep_the_array_awake(pi: Pi) -> None:
    """Stop the microphone going deaf when the room is quiet."""
    pi.run(f"mkdir -p $(dirname ~/{WP_CONF})", quiet=True)
    handle, path = tempfile.mkstemp()
    try:
        Path(path).write_text(NO_SUSPEND)
        pi.send(Path(path), f"~/{WP_CONF}")
    finally:
        os.close(handle)
        os.unlink(path)

    pi.run("systemctl --user restart wireplumber", quiet=True)
    # Prove it, rather than trusting it: the hardware pointer on the capture
    # device has to be moving, and at about the sample rate.
    moving = pi.output(
        "a=$(awk -F: '/hw_ptr/{print $2}' "
        "/proc/asound/card*/pcm0c/sub0/status 2>/dev/null | head -1); "
        "sleep 4; "
        "b=$(awk -F: '/hw_ptr/{print $2}' "
        "/proc/asound/card*/pcm0c/sub0/status 2>/dev/null | head -1); "
        "echo $(( (${b:-0} - ${a:-0}) / 4 ))").strip()
    try:
        rate = int(moving)
    except ValueError:
        rate = 0
    indent(f"microphone delivering {rate} frames a second"
           if rate > 8000 else
           f"microphone NOT delivering ({rate} frames a second) — see "
           "docs/troubleshooting.md")


# Two small pieces of system config the rescue access point needs, both
# installed once. See src/rescue.py and docs/wifi-and-display.md.
#
# The dashboard runs as an ordinary user on 8080, and a captive portal has
# to answer on 80 — the port every operating system probes to decide
# whether a network needs signing into. Lowering the unprivileged port
# floor to 80 lets it bind that without the speaker becoming root. It does
# mean any process on the Pi may bind 80 and up; on a machine that does one
# job that is a fair trade, and it is a trade rather than a detail.
PORT_FLOOR = """# Installed by deploy.py — see src/rescue.py.
# So the dashboard can answer the captive portal probe on port 80 without
# running as root. Ports below 80 are still privileged.
net.ipv4.ip_unprivileged_port_start = 80
"""
PORT_FLOOR_PATH = "/etc/sysctl.d/60-claude-speaker-portal.conf"

# And every name a phone looks up on the rescue network has to resolve to
# the speaker, or the probe never arrives. NetworkManager runs dnsmasq for
# a shared connection and reads this directory.
CAPTIVE_DNS = """# Installed by deploy.py — see src/rescue.py.
# On the rescue access point, every name answers as the speaker, so a
# phone's captive-portal probe lands on the dashboard.
address=/#/10.42.0.1
"""
CAPTIVE_DNS_PATH = "/etc/NetworkManager/dnsmasq-shared.d/claude-speaker.conf"


def allow_captive_portal(pi: Pi, ask: bool = True) -> None:
    """Let the speaker answer on port 80, and point every name at itself."""
    have_port = pi.output(f"cat {PORT_FLOOR_PATH} 2>/dev/null").strip()
    have_dns = pi.output(f"cat {CAPTIVE_DNS_PATH} 2>/dev/null").strip()
    if have_port == PORT_FLOOR.strip() and have_dns == CAPTIVE_DNS.strip():
        indent("already allowed")
        return
    if not ask or not can_ask_for_a_password():
        indent("not set up — it can still raise the access point, but a")
        indent("phone won't offer to sign in. Needs the Pi's password once.")
        return

    indent("allowing the dashboard to answer on port 80")
    pi.run(f"printf '%s' {shlex.quote(PORT_FLOOR)} | "
           f"sudo tee {PORT_FLOOR_PATH} >/dev/null && "
           f"sudo sysctl -q -p {PORT_FLOOR_PATH}", tty=True)
    indent("pointing every name on the rescue network at the speaker")
    pi.run(f"sudo mkdir -p $(dirname {CAPTIVE_DNS_PATH}) && "
           f"printf '%s' {shlex.quote(CAPTIVE_DNS)} | "
           f"sudo tee {CAPTIVE_DNS_PATH} >/dev/null", tty=True)


def install_nightly(pi: Pi) -> None:
    """The timer that makes the loop a loop rather than a pipeline."""
    for name, text in (("claude-relearn.service",
                        RELEARN_UNIT.format(where=REMOTE_DIR)),
                       ("claude-relearn.timer", RELEARN_TIMER)):
        handle, path = tempfile.mkstemp()
        try:
            Path(path).write_text(text)
            pi.send(Path(path), f"~/.config/systemd/user/{name}")
        finally:
            os.close(handle)
            os.unlink(path)

    pi.run("systemctl --user daemon-reload && "
           "systemctl --user enable --now claude-relearn.timer", quiet=True)
    when = pi.output("systemctl --user list-timers claude-relearn --no-pager "
                     "| sed -n 2p").strip()
    indent(when or "enabled")


# Changing the Wi-Fi means asking NetworkManager to change a system
# connection, which polkit guards behind a password — and a password prompt
# is not something a web page in a kitchen can answer. This hands that one
# family of actions to the netdev group, which the Pi's own user is already
# in: the same bargain as the LED rule, give the thing to a group rather
# than becoming root.
POLKIT_RULE = """// Installed by deploy.sh — see src/dashboard.py
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 &&
        subject.isInGroup("netdev")) {
        return polkit.Result.YES;
    }
});
"""
POLKIT_PATH = "/etc/polkit-1/rules.d/50-claude-speaker-wifi.rules"


def allow_wifi_changes(pi: Pi, ask: bool = True) -> None:
    """Let the dashboard change the network without a password."""
    if pi.output(f"cat {POLKIT_PATH} 2>/dev/null").strip() == \
            POLKIT_RULE.strip():
        indent("already allowed")
        return
    if not ask or not can_ask_for_a_password():
        indent("not set up — the dashboard can show the Wi-Fi, not change it")
        indent("this needs the Pi's password once:  ./deploy.sh --wifi")
        return

    indent("adding a polkit rule so the dashboard can change the network")
    pi.run(f"printf '%s' {shlex.quote(POLKIT_RULE)} | sudo tee {POLKIT_PATH} "
           ">/dev/null && sudo systemctl restart polkit", tty=True)


def here_timezone() -> str | None:
    """The zone this computer is in, as an IANA name.

    /etc/localtime is a symlink into the zoneinfo tree on both macOS and
    Linux, and the part after "zoneinfo" is the name we want:
    "America/Los_Angeles" rather than "PDT". The Pi needs the name, not the
    abbreviation, so that it knows about next spring on its own.
    """
    try:
        parts = Path("/etc/localtime").resolve().parts
    except OSError:
        return None
    if "zoneinfo" in parts:
        return "/".join(parts[parts.index("zoneinfo") + 1:])
    return None


def match_the_timezone(pi: Pi, ask: bool = True) -> None:
    """Put the Pi in the same time zone as the computer deploying to it.

    A Pi keeps whatever zone it was imaged with, and nothing in the room
    ever tells it otherwise. Ours sat in America/New_York, so it answered
    "what time is it" three hours ahead, and a timer set for eight o'clock
    was set for the wrong eight o'clock. The clock was right the whole
    time — only the name it went by was wrong, which is the harder kind of
    wrong to notice.
    """
    here = here_timezone()
    if here is None:
        indent("can't tell what zone this computer is in — leaving the Pi's "
               "alone")
        return
    there = pi.output("timedatectl show -p Timezone --value")
    if there == here:
        indent(f"already {here}")
        return
    if not ask or not can_ask_for_a_password():
        indent(f"the Pi says {there or 'an unknown zone'}, this computer says "
               f"{here},")
        indent("so it will tell the time wrong — one run of ./deploy.sh from "
               "a terminal fixes it")
        return
    indent(f"moving the Pi from {there or 'an unknown zone'} to {here}")
    pi.run(f"sudo timedatectl set-timezone {shlex.quote(here)}", tty=True)


def copy_code(pi: Pi) -> None:
    pi.run(f"mkdir -p ~/{REMOTE_DIR}", quiet=True)
    # Plain flags only: macOS still ships rsync 2.6.9, which doesn't know
    # --info= and friends.
    subprocess.run(
        ["rsync", "-az", "--delete"]
        + [arg for pattern in EXCLUDE for arg in ("--exclude", pattern)]
        + [f"{HERE}/", f"{pi.target}:{REMOTE_DIR}/"], check=True)
    indent("code, models/ and start.sh copied")


def send_settings(pi: Pi) -> None:
    handle, path = tempfile.mkstemp()
    try:
        os.close(handle)
        temp = Path(path)
        temp.chmod(0o600)
        temp.write_text(settings_for_pi())
        pi.send(temp, f"{REMOTE_DIR}/.env")
    finally:
        Path(path).unlink(missing_ok=True)
    pi.run(f"chmod 600 ~/{REMOTE_DIR}/.env", quiet=True)
    indent(f"sent (readable only by {pi.user})")


def prepare_audio(pi: Pi) -> None:
    """Fetch the voice and set the volume, so the first run isn't a wait."""
    indent(pi.python(
        "import config, tts\n"
        "tts.turn_up()\n"
        "print('volume: %s%% on card %s' % (config.OUTPUT_VOLUME or "
        "'left alone', tts._output_card()))\n"
        "print('voice:  %s' % (config.PIPER_VOICE if tts._piper_voice() "
        "else 'unavailable — will use espeak-ng'))"))


def check_sound(pi: Pi) -> None:
    """The two things that go wrong in practice: no microphone, and sound
    going out of an HDMI port nobody is listening to.

    A speaker that's already running holds the microphone open, and a
    microphone array allows one stream — so it disappears from the list and
    this looks like a hardware fault. Say so rather than sending someone
    after a cable.
    """
    busy = pi.run('pgrep -f "[s]rc/main.py" >/dev/null',
                  check=False, quiet=True).returncode == 0
    indent(pi.python("""
try:
    import sounddevice as sd
except OSError as error:
    print('Sound library missing:', error)
    print('Run ./deploy.sh without --no-apt to install it.')
    raise SystemExit
import config
devices = list(sd.query_devices())
mics = [d['name'] for d in devices if d['max_input_channels'] > 0]
speakers = [d['name'] for d in devices if d['max_output_channels'] > 0]
print('microphones:', ', '.join(mics) if mics else 'NONE')
print('speakers:   ', ', '.join(speakers) if speakers else 'NONE')
if not mics and not BUSY:
    print()
    print('!! No microphone is reaching the Pi, so it can only be used with')
    print('   WAKE_MODE=key and --text. A USB mic should show up in lsusb.')
    print('   If one is listed by arecord -l but nothing records, try a')
    print('   different USB socket — a blue USB 3 port fixed it here.')
elif not mics:
    print("   (the running speaker is holding the microphone, so it")
    print("    is not listed — that is expected, not a fault)")
if config.OUTPUT_DEVICE and not any(
        config.OUTPUT_DEVICE.lower() in name.lower() for name in speakers):
    print()
    print('!! OUTPUT_DEVICE is %r, which is not in that list.'
          % config.OUTPUT_DEVICE)
""".replace("BUSY", str(busy))))


def install_service(pi: Pi) -> bool:
    """Set it to start on boot, without ever asking for a password.

    Returns whether it's now installed.
    """
    # An older version of this installed a system service. Two managers is
    # worse than either, so say so rather than quietly running both.
    if pi.output("test -f /etc/systemd/system/claude-speaker.service "
                 "&& echo yes"):
        indent("There's an old system-wide service here from a previous\n"
               "version. Remove it, once, and this will never need sudo "
               "again:\n"
               f"    ssh -t {pi.target} 'sudo systemctl disable --now "
               "claude-speaker && sudo rm "
               "/etc/systemd/system/claude-speaker.service'")
        return False

    pi.run(f"mkdir -p ~/{Path(UNIT_PATH).parent} && "
           f"cat > ~/{UNIT_PATH} <<'UNIT'\n"
           f"{SERVICE.format(dir=REMOTE_DIR)}UNIT", quiet=True)
    # Lingering is what lets a user service run with nobody logged in.
    pi.run("loginctl enable-linger && systemctl --user daemon-reload && "
           "systemctl --user enable --now claude-speaker", quiet=True)
    indent(pi.output("systemctl --user is-active claude-speaker"))
    indent("starts on every boot, no password needed")
    return True


def service_scope(pi: Pi) -> str | None:
    """'--user', '' for a system unit, or None if there isn't one.

    Both, because this project used to install a system-wide unit. A Pi
    part way between the two has the old one and not the new, and treating
    that as unmanaged starts a second speaker beside it.
    """
    for scope in ("--user", ""):
        if pi.run(f"systemctl {scope} is-enabled --quiet claude-speaker",
                  check=False, quiet=True).returncode == 0:
            return scope
    return None


def restart_if_running(pi: Pi) -> None:
    """Copying files onto the Pi doesn't change what's already running.

    Asks whether systemd is *enabled* rather than active. A service that's
    enabled but stopped still owns the speaker — starting a loose one
    beside it means two processes competing for a microphone that allows
    one, and the loose one disappears at the next reboot.
    """
    scope = service_scope(pi)
    if scope is not None:
        step("Restarting the service so it picks this up")
        sudo = "" if scope == "--user" else "sudo "
        if pi.run(f"{sudo}systemctl {scope} restart claude-speaker",
                  tty=bool(sudo), check=False, quiet=not sudo).returncode != 0:
            indent("couldn't restart it — the code is on the Pi but the "
                   "running speaker is\nstill the old one. Finish with:\n"
                   f"    ssh -t {pi.target} '{sudo}systemctl {scope} restart "
                   "claude-speaker'")
            return
        indent(pi.output(f"systemctl {scope} is-active claude-speaker"))
    elif pi.run('pgrep -f "[s]rc/main.py" >/dev/null',
                check=False, quiet=True).returncode == 0:
        step("Restarting the speaker so it picks this up")
        indent(pi.output(f"cd ~/{REMOTE_DIR} && ./start.sh --stop && ./start.sh"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # --pi, so this reads the same as the other laptop commands. The
    # positional still works and is how a new Pi gets named the first time.
    import speakers
    speakers.add_argument(parser)
    parser.add_argument("target", nargs="?",
                        help="user@address of the Pi; remembered afterwards")
    parser.add_argument("--run", action="store_true",
                        help="start it afterwards and watch")
    parser.add_argument("--service", action="store_true",
                        help="start it on every boot")
    parser.add_argument("--wifi", action="store_true",
                        help="only add the polkit rule that lets the "
                             "dashboard change the network (asks for the "
                             "Pi's password, once)")
    parser.add_argument("--leds", action="store_true",
                        help="only add the udev rule that lets the speaker "
                             "drive the array's LED ring (asks for the Pi's "
                             "password, once)")
    parser.add_argument("--no-apt", action="store_true",
                        help="skip system packages")
    args = parser.parse_args()

    local_env()  # Fail before touching the network if the key is missing.
    pi = Pi(resolve_target(args.target or args.pi))
    print(f"Deploying to {pi.target}:~/{REMOTE_DIR}")

    step("Checking the Pi is reachable")
    if not pi.reachable():
        raise SystemExit(
            f"Can't log in to {pi.target} without a password.\n"
            f"Set up a key once with:  ssh-copy-id {pi.target}")
    indent(pi.output(
        'echo "$(. /etc/os-release; echo "$PRETTY_NAME") on $(uname -m), '
        'Python $(python3 -V | cut -d" " -f2)"'))

    if args.wifi:
        step("Letting the dashboard change the Wi-Fi")
        allow_wifi_changes(pi)
        return 0

    if args.leds:
        step("Letting the speaker drive the array's LED ring")
        allow_led_access(pi)
        step("Restarting the service so it picks this up")
        restart_if_running(pi)
        return

    if args.no_apt:
        step("Skipping system packages (--no-apt)")
        step("Checking the speaker can drive the array's LED ring")
        allow_led_access(pi, ask=False)
        step("Checking the dashboard can change the Wi-Fi")
        allow_wifi_changes(pi, ask=False)
        step("Checking the Pi's clock is in this time zone")
        match_the_timezone(pi, ask=False)
    else:
        step("Installing system packages (sudo may ask for the Pi's password)")
        install_packages(pi)

        step("Letting the speaker drive the array's LED ring")
        allow_led_access(pi)

        step("Installing Spotify Connect")
        install_spotify(pi)

        step("Letting the dashboard change the Wi-Fi")
        allow_wifi_changes(pi)

        step("Putting the Pi's clock in this time zone")
        match_the_timezone(pi)

    step("Copying the code")
    copy_code(pi)

    step("Sending settings and API key")
    send_settings(pi)

    step("Installing Python packages on the Pi (slow the first time)")
    # start.sh already knows how to do this, and knowing it in two places is
    # how the two drift apart.
    indent(pi.output(f"cd ~/{REMOTE_DIR} && ./start.sh --install-only"))

    step("Downloading the voice and setting the volume")
    prepare_audio(pi)

    step("Checking the sound hardware")
    check_sound(pi)

    step("Keeping the microphone awake when the room is quiet")
    keep_the_array_awake(pi)

    step("Letting it be an access point when it can't find a network")
    allow_captive_portal(pi)

    step("Setting the nightly retraining going")
    pi.run("mkdir -p ~/.config/systemd/user", quiet=True)
    install_nightly(pi)

    running_as_service = service_scope(pi) is not None
    if args.service:
        step("Setting it to start on boot")
        running_as_service = install_service(pi) or running_as_service
    else:
        restart_if_running(pi)

    print("\nDeployed.\n")
    if running_as_service:
        print("systemd is looking after it, and starts it on every boot.\n")
        print(f"  Watch it:     ssh {pi.target} journalctl --user-unit=claude-speaker -f")
        print(f"  Restart it:   ssh {pi.target} systemctl --user restart claude-speaker")
        print(f"  Stop it:      ssh {pi.target} systemctl --user stop claude-speaker")
        print(f"  Type instead: ssh -t {pi.target} 'cd {REMOTE_DIR} && "
              "systemctl --user stop claude-speaker && ./start.sh --text'")
    else:
        print(f"  Start it:     ssh {pi.target} 'cd {REMOTE_DIR} && ./start.sh'")
        print(f"  Watch it:     ssh {pi.target} 'tail -f {REMOTE_DIR}/speaker.log'")
        print(f"  Stop it:      ssh {pi.target} 'cd {REMOTE_DIR} && ./start.sh --stop'")
        print(f"  Type instead: ssh -t {pi.target} 'cd {REMOTE_DIR} && ./start.sh --text'")
        print("  On every boot: ./deploy.sh --service")

    if args.run:
        step("Starting it (Ctrl-C to stop)")
        os.execvp("ssh", ["ssh", "-t", pi.target,
                          f"cd {REMOTE_DIR} && ./start.sh --foreground"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
