"""The LED ring on the microphone array — showing what the speaker is doing.

A speaker with no screen has one honest way to say "I heard you": the ring
of twelve LEDs around the microphone array. Sound can't do this job on its
own. The beep tells you it woke up, but it can't keep telling you it's
still listening, and it can't say anything at all while you're talking.

    dark        idle, waiting for the wake word
    blue        listening to your question
    blue, slow  thinking about it
    green       talking back
    red, fast   a timer is going off
    purple      learning your voice — keep saying it until this goes out

Everything here is best-effort. If the array isn't plugged in, or pyusb
isn't installed, or the udev rule was never added, the lights quietly stay
off and the speaker works exactly as before. A voice assistant should not
stop working because a light didn't.

How it talks to the array
-------------------------
The XVF3800 takes vendor USB control transfers: request 0, wValue is the
command, wIndex is the resource. The whole LED protocol is five commands,
so this speaks it directly rather than shipping Seeed's 1.8 MB xvf_host
binary or vendoring their 400-line script.

    https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY

Permission
----------
The array's USB node is owned by root, so this needs a udev rule to work
without sudo. `./deploy.sh` installs one. Without it you get "Access
denied" once, and then the lights are off for the rest of the run.

    python src/lights.py            walk through every colour
    python src/lights.py --off      turn them off
"""

import struct
import sys
import threading
import time

import config

VENDOR, PRODUCT = 0x2886, 0x001A

# The array groups its settings into numbered resources; the LEDs are 20.
LED_RESOURCE = 20
EFFECT, BRIGHTNESS, SPEED, COLOUR = 12, 13, 15, 16

# Effects the firmware knows. It runs the animation itself, so a breathing
# ring costs one USB message rather than a thread here redrawing it.
OFF, BREATHE, RAINBOW, SOLID, DIRECTION = 0, 1, 2, 3, 4

# What each state looks like: effect, colour, and how fast it breathes.
STATES = {
    "idle":      (OFF, 0x000000, 0),
    "listening": (SOLID, 0x0066FF, 0),    # blue — the same blue as the beep
    "thinking":  (BREATHE, 0x0066FF, 3),  # same colour, so it reads as one
    "speaking":  (SOLID, 0x00CC44, 0),    # green — my turn
    "ringing":   (BREATHE, 0xFF0000, 9),  # red, fast, unmissable
    # Learning a voice. A colour used for nothing else, because while this
    # is on it is the only instruction the person has: keep saying it until
    # the light goes out.
    "learning":  (SOLID, 0xAA00FF, 0),
    # Amber, slow, and used for nothing else: it is an access point and it
    # is waiting for somebody. Visible from the doorway, which is the
    # point — see src/rescue.py.
    "rescue":    (BREATHE, 0xFF8800, 2),
}

_lock = threading.Lock()
_device = None
_broken = False
_failures = 0

# A USB control transfer to a device that is also streaming audio fails now
# and then — "Input/Output Error", and it works again a second later. Giving
# up for the rest of the run on the first of those turned a flicker into a
# dark ring for the evening, so it takes this many in a row.
GIVE_UP_AFTER = 8


def _connect():
    """Find the array. Returns None, once and quietly, if anything is wrong.

    _broken is checked first and on its own. Checking "already found it or
    already gave up" in one condition looks equivalent and isn't: the array
    is found perfectly well and then refuses the write, so _device is set
    and every later call sailed past the latch and complained again. Four
    lines in the log per state change, a dozen per question.
    """
    global _device, _broken
    if _broken:
        return None
    if _device is not None:
        return _device

    try:
        import usb.core
        _device = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)
        if _device is None:
            raise LookupError("no reSpeaker array on the USB bus")
    except Exception as error:
        # Said once, then never again. This is a nicety, not a fault, and a
        # line per state change would bury the log.
        print(f"[lights] off ({error})")
        _broken = True
        return None
    return _device


def show(state: str) -> None:
    """Put the ring into one of the states above."""
    global _failures
    if not config.LEDS or _broken:
        return

    effect, colour, speed = STATES.get(state, STATES["idle"])
    before = _failures
    with _lock:
        _send(EFFECT, bytes([effect]))
        if effect != OFF:
            _send(COLOUR, struct.pack("<I", colour))
            _send(BRIGHTNESS, bytes([config.LED_BRIGHTNESS]))
        if speed:
            _send(SPEED, bytes([speed]))
    if _failures == before:
        _failures = 0   # A whole state change went through. Forget the past.


def _send(command: int, payload: bytes) -> None:
    """One control message to the array. Never raises."""
    global _broken
    device = _connect()
    if device is None:
        return

    try:
        import usb.util

        out = (usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR
               | usb.util.CTRL_RECIPIENT_DEVICE)
        try:
            device.ctrl_transfer(out, 0, command, LED_RESOURCE, payload, 2000)
        except Exception:
            # Once more. The array is busy playing something more often
            # than not, and a retry a moment later almost always works.
            time.sleep(0.05)
            device.ctrl_transfer(out, 0, command, LED_RESOURCE, payload, 2000)
    except Exception as error:
        global _failures
        _failures += 1
        if "denied" in str(error).lower():
            print(f"[lights] off for the rest of this run ({error})")
            print("[lights] run ./deploy.sh once to add the udev rule "
                  "that allows this")
            _broken = True
            return
        if _failures >= GIVE_UP_AFTER:
            print(f"[lights] off for the rest of this run — {_failures} "
                  f"failures in a row, last was {error}")
            _broken = True


if __name__ == "__main__":
    import time

    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    if "--off" in sys.argv:
        show("idle")
        raise SystemExit

    for name in ("listening", "thinking", "speaking", "ringing",
                 "learning", "idle"):
        print(f"  {name}")
        show(name)
        time.sleep(2.5)
