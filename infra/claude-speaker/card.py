"""Make an SD card that boots into a speaker you can reach.

    ./card.sh bedroom            prepare the card that's plugged in
    ./card.sh bedroom --flash    ...erasing it and writing 64-bit Pi OS first
    ./card.sh bedroom --password hunter2
    ./card.sh --find             what new Pi turned up on the network

It refuses a 32-bit card. That is not fussiness: ctranslate2, the engine
under faster-whisper, has no 32-bit ARM build, so a speaker on one has no
speech recognition and no wake word. --flash will put the right image on.

Flash Raspberry Pi OS onto a card however you like — Imager, dd, anything —
then run this with the card in the laptop. It writes the three files the Pi
needs to come up with a name, a user, a key and a network, and tells you
what the password is. Boot it, and `--find` will tell you where it landed.

Why not just use Raspberry Pi Imager's settings
-----------------------------------------------
Because they are not part of the image. Imager writes `custom.toml` *and*
adds `init=/usr/lib/raspberrypi-sys-mods/firstboot` to cmdline.txt, and the
hook is what reads the file. Flash the same image any other way and the
hook is absent, so `custom.toml` sits there being ignored for ever.

That was worth four trips to the card to learn, so it is written down here:

  custom.toml      ignored unless something invokes it. Not used.
  init=            do not. On this image
                   /usr/lib/raspberrypi-sys-mods/firstboot does not exist,
                   so the kernel comes up with no init and the Pi never
                   boots at all — silently, if it has no monitor.
  userconf.txt     works. A systemd service reads it and makes the user.
  ssh              works. Consumed on boot, so write it every time.
  systemd.run=     works, and is safe: it runs a script as a unit rather
                   than replacing init, so a broken script costs you the
                   script and not the machine.

And the one that is easy to get wrong: at `kernel-command-line.target`,
*NetworkManager is not running yet*. A firstrun script that calls nmcli
gets "NetworkManager is not running" for every line. So this one writes
configuration files instead, which need no daemon, and lets the reboot
pick them up.
"""

import argparse
import getpass
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET_FILE = HERE / ".deploy-target"

WORDS = ("amber harbour lantern meadow pebble thicket willow cinder tandem "
         "quartz bramble kettle marlin saffron drift copper").split()


def say(message: str) -> None:
    print(message)


def boot_partition() -> Path:
    """The one mounted Raspberry Pi boot partition, or an explanation."""
    seen = [p for p in Path("/Volumes").iterdir()
            if (p / "cmdline.txt").is_file() and (p / "config.txt").is_file()]
    if not seen:
        raise SystemExit(
            "No Raspberry Pi card is mounted.\n\n"
            "Flash Raspberry Pi OS onto one, put it in this laptop, and wait\n"
            "for a volume called bootfs to appear.")
    if len(seen) > 1:
        raise SystemExit("More than one card is mounted:\n  " +
                         "\n  ".join(str(p) for p in seen) +
                         "\n\nEject all but the one you mean.")
    return seen[0]


# The speaker needs ctranslate2, the engine under faster-whisper, and
# there is no 32-bit ARM build of it — pip says "from versions: none".
# No speech recognition, no wake word, nothing. So the architecture is not
# a preference here, it is the difference between a speaker and a brick.
#
# Telling the two apart from the boot partition alone, which is the only
# half macOS can read: the 32-bit image ships the 32-bit kernels beside
# the 64-bit one, and the 64-bit image does not ship them at all.
#
#   32-bit   kernel.img  kernel7.img  kernel8.img
#   64-bit                            kernel8.img  kernel_2712.img
#
# Do not try to judge this by the kernel the Pi is running. Raspberry Pi
# OS 32-bit boots a 64-bit kernel on a Pi 4, so `uname -m` says aarch64
# and Python's sysconfig says linux-aarch64 while pip is correctly
# resolving armv7l wheels. Three answers, two of them misleading. On the
# Pi itself only `dpkg --print-architecture` is honest.
THIRTY_TWO_BIT = ("kernel.img", "kernel7.img", "kernel7l.img")

IMAGES = {
    "desktop": "https://downloads.raspberrypi.com/raspios_arm64_latest",
    "lite": "https://downloads.raspberrypi.com/raspios_lite_arm64_latest",
}


def architecture(boot: Path) -> str:
    """"arm64" or "armhf", from the boot partition alone."""
    if any((boot / k).is_file() for k in THIRTY_TWO_BIT):
        return "armhf"
    return "arm64" if (boot / "kernel8.img").is_file() else "unknown"


def card_disk() -> str:
    """The external, physical, removable disk the card is on.

    Deliberately fussy. This hands a device node to `dd`, and the failure
    mode of getting it wrong is the laptop.
    """
    plist = subprocess.run(["diskutil", "list", "-plist", "external",
                            "physical"], capture_output=True, text=True)
    import plistlib
    disks = plistlib.loads(plist.stdout.encode()).get("WholeDisks", [])
    if not disks:
        raise SystemExit(
            "No external disk is plugged in. Put the card in and try again.")
    if len(disks) > 1:
        raise SystemExit(
            "More than one external disk is plugged in:\n  " +
            "\n  ".join("/dev/" + d for d in disks) +
            "\n\nUnplug the others. This writes an image with dd and will "
            "not guess.")
    name = disks[0]
    info = subprocess.run(["diskutil", "info", "-plist", name],
                          capture_output=True, text=True)
    d = plistlib.loads(info.stdout.encode())
    if d.get("Internal") or not d.get("Removable", d.get("RemovableMedia")):
        raise SystemExit(f"/dev/{name} does not look removable. Not touching it.")
    size = d.get("TotalSize", 0)
    if size > 128 * 10**9:
        raise SystemExit(
            f"/dev/{name} is {size / 10**9:.0f} GB, which is bigger than any "
            "card\nthis is meant for. Refusing in case it is a backup drive.")
    say(f"  disk      /dev/{name}  —  {d.get('MediaName', '?')}, "
        f"{size / 10**9:.1f} GB")
    return name


def flash(which: str, yes: bool) -> None:
    """Put a 64-bit Raspberry Pi OS on the card. Destroys what is there."""
    if not sys.stdin.isatty() and not yes:
        raise SystemExit(
            "Writing an image needs sudo, and sudo needs a terminal.\n"
            "Run this one from a terminal:  ./card.sh <name> --flash")
    disk = card_disk()
    cache = Path.home() / ".cache" / "claude-speaker-images"
    cache.mkdir(parents=True, exist_ok=True)
    image = cache / f"raspios-{which}-arm64.img.xz"

    if not image.is_file() or image.stat().st_size < 100 * 10**6:
        say(f"  downloading the 64-bit {which} image (a gigabyte or two)")
        got = subprocess.run(["curl", "-fL", "--retry", "3", "-C", "-",
                              "-o", str(image), IMAGES[which]])
        if got.returncode != 0:
            raise SystemExit("Download failed.")
    else:
        say(f"  using the image already in {cache}")

    if not yes:
        say("")
        say(f"  This ERASES /dev/{disk} completely.")
        # Accept it with or without the /dev/, because the line above says
        # /dev/disk4 and typing back what you were just shown is the
        # obvious thing to do. The first version demanded the bare name
        # and threw away a two-minute download over a prefix.
        typed = input(f'  Type "{disk}" to go ahead: ').strip()
        if typed.removeprefix("/dev/") != disk:
            raise SystemExit("  Nothing written.")

    subprocess.run(["diskutil", "unmountDisk", f"/dev/{disk}"], check=True)
    say("  writing — several minutes, and it will look like nothing is "
        "happening")
    piped = subprocess.Popen(["xz", "-dc", str(image)],
                             stdout=subprocess.PIPE)
    wrote = subprocess.run(["sudo", "dd", f"of=/dev/r{disk}", "bs=4m"],
                           stdin=piped.stdout)
    piped.wait()
    if wrote.returncode != 0:
        raise SystemExit("dd failed.")
    subprocess.run(["sync"])
    say("  written. Waiting for it to mount again...")
    import time
    for _ in range(30):
        time.sleep(2)
        try:
            return boot_partition() and None
        except SystemExit:
            continue
    raise SystemExit("The card did not come back. Re-seat it and run again "
                     "without --flash.")


def a_password() -> str:
    return "-".join(secrets.choice(WORDS) for _ in range(3)) + \
        f"-{secrets.randbelow(90) + 10}"


def hashed(password: str) -> str:
    done = subprocess.run(["openssl", "passwd", "-6", password],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit("openssl couldn't hash the password:\n" + done.stderr)
    return done.stdout.strip()


def my_key() -> str:
    """This laptop's public key, so the new Pi trusts it from first boot.

    Imager would do this through custom.toml, which does not work here, so
    the firstrun script writes authorized_keys itself. Without it the first
    thing you do with a new speaker is type a password at ssh-copy-id.
    """
    for name in ("id_ed25519.pub", "id_rsa.pub"):
        key = Path.home() / ".ssh" / name
        if key.is_file():
            return key.read_text().strip()
    raise SystemExit(
        "No SSH key at ~/.ssh/id_ed25519.pub.\n"
        "Make one with:  ssh-keygen -t ed25519")


def wifi_from_a_working_pi() -> tuple[str, str] | None:
    """Borrow the network from a speaker that is already on it.

    NetworkManager keeps the derived 64-character key rather than the
    passphrase, which is all this needs: a .nmconnection takes either.
    """
    if not TARGET_FILE.is_file():
        return None
    target = TARGET_FILE.read_text().strip()
    # `connection show --active` only knows about the connection's own
    # columns, so the wireless settings need a second call naming it — and
    # -s, or nmcli hands back an empty psk without saying why.
    ask = (
        'n=$(nmcli -t -f NAME,TYPE connection show --active '
        '| awk -F: \'$2 ~ /wireless/ {print $1; exit}\'); '
        '[ -n "$n" ] || exit 1; '
        'nmcli -t -g 802-11-wireless.ssid connection show "$n"; '
        'nmcli -s -t -g 802-11-wireless-security.psk connection show "$n"')
    try:
        done = subprocess.run(["ssh", "-o", "ConnectTimeout=8", "-o",
                               "BatchMode=yes", target, ask],
                              capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    if len(lines) < 2 or not lines[1]:
        return None
    return lines[0], lines[1]


FIRSTRUN = """#!/bin/bash
# Written by card.py. Runs once, early, as a systemd unit.
#
# Prefer imager_custom, which is Raspberry Pi's own script for exactly this
# and is sitting at /usr/lib/raspberrypi-sys-mods/imager_custom on the
# card. Hand-rolling this is what went wrong repeatedly: the user does not
# exist yet at this point in the boot and NetworkManager is not running, so
# writing authorized_keys or calling nmcli both fail. Their script knows
# that; mine kept learning it.
#
# Everything below still falls back to doing it by hand, because the one
# thing worse than the vendor tool missing a case is finding out silently.
exec > /boot/firmware/debug.txt 2>&1
set -x
date -u
IC=/usr/lib/raspberrypi-sys-mods/imager_custom
ls -l $IC

if [ -x $IC ]; then
  # The real interface, read off a Pi rather than assumed. There is no
  # set_user: the user comes from userconf.txt, which a systemd service
  # reads on the normal boot, and that part has always worked.
  #
  #   set_hostname HOSTNAME
  #   enable_ssh [-k|--key-only]|[-p|--pass-auth] [-d] [KEY_LINE...]
  #   set_wlan [-h|--hidden] [-p|--plain] SSID [PASS [COUNTRY]]
  #   set_keymap KEYMAP
  #   set_timezone TIMEZONE
  $IC set_hostname {name}              || echo "FELL BACK: hostname"
  $IC enable_ssh -k '{key}'            || echo "FELL BACK: ssh key"
  $IC set_wlan '{ssid}' '{psk}' '{country}' || echo "FELL BACK: wlan"
  $IC set_keymap 'us'                  || true
  $IC set_timezone '{timezone}'        || true
fi

# --- and by hand, for anything the above did not manage -----------------
if [ ! -f /etc/NetworkManager/system-connections/{ssid}.nmconnection ]; then
  mkdir -p /etc/NetworkManager/system-connections
  cat > /etc/NetworkManager/system-connections/{ssid}.nmconnection <<'CONN'
[connection]
id={ssid}
type=wifi
interface-name=wlan0
autoconnect=true
autoconnect-priority=10

[wifi]
mode=infrastructure
ssid={ssid}

[wifi-security]
key-mgmt=wpa-psk
psk={psk}

[ipv4]
method=auto

[ipv6]
method=auto
CONN
  chown root:root /etc/NetworkManager/system-connections/{ssid}.nmconnection
  chmod 600 /etc/NetworkManager/system-connections/{ssid}.nmconnection
fi

[ -s /etc/hostname ] || echo {name} > /etc/hostname
grep -q {name} /etc/hostname || echo {name} > /etc/hostname
sed -i "s/127.0.1.1.*/127.0.1.1\t{name}/" /etc/hosts || true

# The key goes to /etc/skel as well: at this point in the boot the user may
# not exist, and skel is copied into a home directory when one is made.
install -d -m 700 /etc/skel/.ssh
cat > /etc/skel/.ssh/authorized_keys <<'KEY'
{key}
KEY
chmod 600 /etc/skel/.ssh/authorized_keys
if id {user} >/dev/null 2>&1; then
  install -d -m 700 -o {user} -g {user} /home/{user}/.ssh
  install -m 600 -o {user} -g {user} /etc/skel/.ssh/authorized_keys \
          /home/{user}/.ssh/authorized_keys
fi

# Grow the filesystem to the card, here rather than by leaving `resize` on
# the kernel command line. That keyword is consumed by the first boot, so
# juggling it between the hooked line and the restored one is fiddly and I
# got it wrong: a card came up with a 29.2 GB partition and a 5.6 GB
# filesystem on it, which is not enough room for the speaker. ext4 grows
# while mounted, so this just works and needs no keyword at all.
ROOTDEV=$(findmnt -no SOURCE /)
resize2fs "$ROOTDEV" || true
df -h /

rfkill unblock wifi || true
raspi-config nonint do_wifi_country {country} || true
systemctl enable NetworkManager ssh || true

# Everything above runs before NetworkManager, so none of it can say
# whether NetworkManager liked any of it. This unit runs after it does.
cat > /etc/systemd/system/claude-netdebug.service <<'UNIT'
[Unit]
Description=Write down what the network actually did
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStartPre=/bin/sleep 25
ExecStart=/bin/bash -c '{{ date; id {user}; echo "--- devices ---"; nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device; echo "--- connections ---"; nmcli -t -f NAME,TYPE,AUTOCONNECT,ACTIVE connection show; echo "--- seen ---"; nmcli -t -f SSID,SIGNAL,CHAN device wifi list; echo "--- radio ---"; rfkill list; iw reg get 2>&1 | head -4; echo "--- address ---"; ip -4 addr show wlan0; echo "--- NM log ---"; journalctl -u NetworkManager --no-pager -n 60; }} > /boot/firmware/net-debug.txt 2>&1; sync'

[Install]
WantedBy=multi-user.target
UNIT
systemctl enable claude-netdebug.service

ls -l /etc/NetworkManager/system-connections/
id {user} || echo "user does not exist yet, skel will supply the key"

cp /boot/firmware/cmdline.txt.backup /boot/firmware/cmdline.txt
sync
echo DONE
"""


RUN_HOOK = ("systemd.run=/boot/firmware/firstrun.sh "
            "systemd.run_success_action=reboot "
            "systemd.unit=kernel-command-line.target")


def prepare(boot: Path, name: str, user: str, password: str,
            ssid: str, psk: str, country: str) -> None:
    # cmdline.txt has to stay a single line, and the script puts this copy
    # back at the end so it only ever runs once.
    import re
    cmdline = boot / "cmdline.txt"
    backup = boot / "cmdline.txt.backup"

    # Always rebuild from what is on the card now, with any hook of ours
    # stripped — never from a backup written on a previous run. Reusing a
    # stale backup put the pristine pre-first-boot line back, and that
    # line still had `resize` in it.
    line = cmdline.read_text().strip()
    line = re.sub(r"\s*systemd\.(run|unit)\S*", "", line).strip()
    if "init=" in line:
        raise SystemExit(
            f"{cmdline} has an init= hook in it. That is the thing that\n"
            "stops this image booting — take it out before going on.")

    # `resize` grows the filesystem on first boot and is consumed doing it.
    # It must not share a command line with the hook: asking for a minimal
    # target and a resize at once leaves firstrun.sh never starting at all,
    # with nothing written down to say why. So the hook runs without it,
    # and the line restored afterwards keeps it, which does the resize on
    # the reboot instead.
    # Keep resize out of the hooked line — a resize and a boot to a
    # minimal target do not need to happen at once — but firstrun.sh grows
    # the filesystem itself now, so nothing depends on this either way.
    hooked = re.sub(r"\s+resize\b", "", line)
    backup.write_text(line + "\n")
    cmdline.write_text(hooked + " " + RUN_HOOK + "\n")

    (boot / "userconf.txt").write_text(f"{user}:{hashed(password)}\n")
    os.chmod(boot / "userconf.txt", 0o600)
    (boot / "ssh").touch()          # consumed on boot, so written every time

    script = boot / "firstrun.sh"
    script.write_text(FIRSTRUN.format(
        name=name, user=user, ssid=ssid, psk=psk, key=my_key(),
        country=country,
        timezone=subprocess.run(["readlink", "/etc/localtime"],
                                capture_output=True, text=True
                                ).stdout.strip().split("zoneinfo/")[-1]
        or "America/Los_Angeles"))
    os.chmod(script, 0o755)

    # custom.toml would be read by nothing here, and leaving one around
    # invites the next person to believe it did something.
    stale = boot / "custom.toml"
    if stale.is_file():
        stale.unlink()


def subnets() -> list[str]:
    """The /24s worth scanning, from this laptop's own address and mask.

    Hardcoding 192.168.4 and .5 missed a Pi once: the network here is a
    /22, so DHCP can hand out anything up to 192.168.7.254, and a scan
    that stops at .5 reports a working speaker as absent.
    """
    import ipaddress
    out = []
    listed = subprocess.run(["ifconfig"], capture_output=True, text=True).stdout
    for line in listed.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] != "inet" or parts[1].startswith("127."):
            continue
        try:
            mask = int(parts[3], 16) if parts[3].startswith("0x") else None
            if mask is None:
                continue
            net = ipaddress.ip_network(f"{parts[1]}/{bin(mask).count('1')}",
                                       strict=False)
        except Exception:
            continue
        if net.num_addresses > 4096:       # a /20 or wider is not worth it
            continue
        out += sorted({".".join(str(ip).split(".")[:3])
                       for ip in (net.network_address,
                                  net.broadcast_address)})
        first = int(str(net.network_address).split(".")[2])
        last = int(str(net.broadcast_address).split(".")[2])
        head = ".".join(str(net.network_address).split(".")[:2])
        out = [f"{head}.{n}" for n in range(first, last + 1)]
    return out or ["192.168.4"]


def find(known: set[str]) -> None:
    """Which Pi just turned up.

    By scanning, not by asking for <name>.local: mDNS does not resolve on
    every network and did not on the one this was written for, which cost
    an hour of believing a working Pi was a dead one.
    """
    import concurrent.futures
    import socket

    def open22(ip: str) -> str | None:
        try:
            with socket.create_connection((ip, 22), timeout=1):
                return ip
        except OSError:
            return None

    here = [f"{base}.{host}" for base in subnets() for host in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=256) as pool:
        up = [ip for ip in pool.map(open22, here) if ip]
    say(f"  scanned {', '.join(s + '.x' for s in subnets())}")
    for ip in up:
        who = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
             "-o", "StrictHostKeyChecking=accept-new", f"normal@{ip}",
             "hostname"], capture_output=True, text=True).stdout.strip()
        mark = "" if ip in known else "   <- not one I knew about"
        say(f"    {ip:16} {who or '(no key for it yet)'}{mark}")
    if not up:
        say("    nothing with ssh open yet — give it two or three minutes")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare an SD card for a new speaker.")
    parser.add_argument("name", nargs="?",
                        help="what to call it — bedroom, kitchen")
    parser.add_argument("--user", default="normal")
    parser.add_argument("--password", default="")
    parser.add_argument("--ssid", default="")
    parser.add_argument("--psk", default="")
    parser.add_argument("--country", default="US")
    parser.add_argument("--flash", action="store_true",
                        help="erase the card and write 64-bit Raspberry Pi OS")
    parser.add_argument("--lite", action="store_true",
                        help="with --flash, the Lite image rather than desktop")
    parser.add_argument("--yes", action="store_true",
                        help="with --flash, do not ask before erasing")
    parser.add_argument("--find", action="store_true",
                        help="scan for a Pi that has just booted")
    args = parser.parse_args()

    if args.find:
        known = set()
        if TARGET_FILE.is_file():
            known.add(TARGET_FILE.read_text().strip().split("@")[-1])
        find(known)
        return 0

    if not args.name:
        parser.error("what should it be called? e.g. ./card.sh bedroom")

    if args.flash:
        flash("lite" if args.lite else "desktop", args.yes)

    boot = boot_partition()
    say(f"  card      {boot}")
    arch = architecture(boot)
    if arch != "arm64":
        raise SystemExit(
            f"\nThis card has the {arch} image on it, and the speaker cannot "
            "run on it.\n\n"
            "faster-whisper needs ctranslate2, which has no 32-bit ARM build "
            "at all —\nno speech recognition and no wake word. A deploy onto "
            "it gets as far as\nmaking an empty virtualenv and stops.\n\n"
            "Flash the 64-bit image. This can do it for you, from a "
            "terminal:\n\n"
            f"    ./card.sh {args.name} --flash\n")
    say(f"  image     64-bit, which is the one that works")

    ssid, psk = args.ssid, args.psk
    if not ssid:
        borrowed = wifi_from_a_working_pi()
        if borrowed:
            ssid, psk = borrowed
            say(f"  network   {ssid}, borrowed from the speaker you already "
                "have")
        else:
            ssid = input("  Wi-Fi name: ").strip()
            psk = getpass.getpass("  Wi-Fi password: ").strip()
    if not ssid or not psk:
        raise SystemExit("Need a network to put it on.")

    password = args.password or a_password()
    prepare(boot, args.name, args.user, password, ssid, psk, args.country)

    say(f"  name      {args.name}")
    say(f"  user      {args.user}")
    say(f"  password  {password}")
    say(f"  key       ~/.ssh added, so ssh works without one")
    say("")
    say("  Written. Eject the card, put it in the Pi and power it up.")
    say("  It writes its config, reboots once, and joins the network —")
    say("  about two minutes. Then:")
    say("")
    say("      ./card.sh --find")
    say(f"      ./deploy.sh {args.user}@<the address it found>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
