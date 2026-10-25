# A screen you can plug in

**This is a plan, not a description.** Nothing below is built yet. When it
is, it belongs in [dashboard.md](dashboard.md) and this file should be
deleted.

The other two parts of this plan are built and have moved there: networks
it can remember, and the access point it raises when it cannot find one.

## What changed about this one

In the first draft this was the headline — the way out of the lock-out
loop, because the dashboard is where you fix the Wi-Fi and the dashboard is
on the Wi-Fi. The access point took that job. A phone is a better answer
than a monitor, a micro-HDMI cable and a keyboard, and everybody has one.

So this is now a **diagnostic**: the thing you reach for when the access
point itself did not come up, or when you want to watch what the speaker is
doing without another machine. Worth building, worth building last.

## What it should do

Plug a monitor into either micro-HDMI and the dashboard is on it, full
screen, with no desktop, no browser chrome and no cursor.

    chromium --kiosk --ozone-platform=wayland \
             --app=http://localhost:8080 \
             --no-first-run --disable-session-crashed-bubble --disable-infobars

The flags after `--app` are not decoration: without them the first thing a
plugged-in screen shows is a first-run dialog or a "Chromium didn't shut
down properly" bar.

## Only when something is plugged in

The obvious build starts it with the session and leaves it running, which
costs a few hundred megabytes for ever on ten speakers of which perhaps one
will have a screen — and the wake word already takes 87% of a core.

The kernel already says:

    $ cat /sys/class/drm/card1-HDMI-A-1/status
    disconnected

A small service polling that, starting chromium on `connected` and stopping
it on `disconnected`, is a dozen lines and is trivially debuggable. udev
emits DRM change events and would avoid the poll, but routing a udev event
into a user service is more machinery than reading a file, and this file is
the truth either way.

## Where it stands on the Pi

| | |
|---|---|
| Compositor | labwc, under lightdm, already running with nothing attached |
| What that costs | 216 MB, today, with both ports empty |
| Browser | `/usr/bin/chromium` |
| Displays | two micro-HDMI, both currently `disconnected` |
| Memory free | 2911 MB of 3795 |

A screen therefore costs a browser, not a graphical stack.

## What needs checking rather than assuming

- **Blanking.** Nothing obviously runs an idle daemon on this image, so the
  screen may never blank — or lightdm may have its own opinion. Measure
  before writing code to prevent something that does not happen.
- **The cursor.** With no mouse attached, a pointer parked mid-screen looks
  broken. That is a compositor setting, not a chromium flag.
- **Which output labwc picks** when both ports are populated.
- **That the page still reads.** It sets `width=device-width`, caps at
  `52rem` and lays tiles out with `auto-fit`, so it should survive a small
  screen unchanged. Should is not measured.

## One thing it would add that the access point cannot

The access point tells you its password out loud. A screen can *show* it —
along with the name, and whatever else the speaker is confused about. If
the kiosk is built, the rescue state is the first thing it should display,
because that is the moment somebody is standing in front of it wondering
what is wrong.

## Deliberately not in this

- **No purpose-built screen layout.** The plugged-in display shows the
  dashboard as it already is. A calmer face for a shelf — the time, a
  timer, what is playing — is a good idea and a separate one.
- **No touchscreen assumption.** One would be nicer on a shelf and is not
  required.
