# The dashboard

    http://192.168.4.95:8080

Three things a speaker with no screen cannot tell you: what it is doing
right now, whether the machine under it is healthy, and which network it is
on — the last mattering most, because getting the Wi-Fi wrong is the one
mistake that makes every other page unreachable.

`DASHBOARD=off` turns it off. `DASHBOARD_PORT` moves it.

## What is on it

**What it is doing.** The last twenty turns — what was asked, what was
answered, and what the wake word scored — plus timers running, what sound is
playing, and what book is being read and where it has got to.

The turns are tagged with how they ended: `answered`, `not for me` when
Claude decided the television was talking, and `woke, nobody spoke` when the
wake word fired and there was silence after it.

**How the machine is.** CPU, temperature, memory, disk, uptime, load, and
whether the Pi is throttling — charted over recent history as well as shown
as numbers. Plus whether each of the three services is up.

**Which network it is on**, and the ability to change it. This is the reason
the page can be worth its risk: a speaker on the wrong network is a speaker
you cannot reach any other way.

## What it can make the speaker do

A short list, deliberately:

| | |
|---|---|
| `stop` | stop everything — talking, reading, music, sounds, a ringing timer |
| `say` | speak a line of text, up to 200 characters |
| `sound` | start or stop a background sound |
| `volume` | set the music volume |
| `timer` | set a timer, in minutes |
| `restart` | restart the service — it answers first, then goes away |

## Wishes

Everything any speaker has been asked for and cannot do, pooled across the
house. A thing two children ask for in two rooms is a better idea than a
thing asked for twice in one, so each wish shows how many times it came
up, in whose words, and which speakers heard it.

They live in their own dataset — `claude-speaker-wishes`, one folder per
speaker — and deliberately not in the one with the recordings. That one is
two second windows of a living room caught whenever the detector fired,
and is private for good reason. This is a list of things a child asked
for, which is a different kind of thing and the only feature request in
this project written by the person it is for.

A wish uploads as soon as it is written, from a thread, so nobody waits on
a network round trip to be told their idea was noted. The page caches the
pooled list for ten minutes, because it redraws every few seconds and that
would otherwise be a download each time; if the archive cannot be read it
shows this speaker's own wishes rather than an error.

`./wishes.sh --pi bedroom` still reads one speaker's list in the terminal.

## Wi-Fi

It shows the network it is on, what is in range, and **what it has saved**
— which is not the same list. Saving a network does not need it to be in
range: you type in the one at the grandparents' house before you drive
there, and it joins when it gets there.

| | |
|---|---|
| **Join now** | connect to something in range, and remember it |
| **Save for later** | remember it without connecting; works anywhere |
| **Hidden network** | for one that does not broadcast its name |
| **Forget** | delete a saved network |

Two things it will not do. It never sends a saved password back to the
page — `nmcli` will hand over stored secrets and nothing here asks, so
somebody who has forgotten one retypes it. And it refuses to forget the
network it is currently on unless another saved network is in range, because
deleting the connection carrying the request is a way to make the speaker
unreachable from the page that did it.

Note that a connection's name is not the network's name. netplan calls one
`netplan-wlan0-solus` and the network it joins is `solus`, so the SSID is
read per connection rather than assumed.

## When it cannot get onto anything

A speaker with the wrong Wi-Fi is a brick: the dashboard is where you fix
the network, and the dashboard is on the network. So after `RESCUE_AFTER`
minutes unable to join anything, it **becomes** a network — see
`src/rescue.py`.

It says so out loud, because it can:

> I can't get onto the network. I've made one of my own. On your phone,
> join Claude Speaker. The password is heyclaude, all one word and all
> lower case.

and lights the ring amber, which is used for nothing else.

The password is read aloud and then typed into a phone, where a space or a
capital is a failed attempt with no explanation — so it is one lower-case
word by default and the announcement describes its shape. `AP_PASSWORD`
changes it. It cannot be open: an open access point would put a page with
no password, which can change your Wi-Fi and shows what was said in the
room, in front of anybody walking past.

**There is one radio, so this is a repair mode.** While the access point is
up the speaker has no internet and cannot answer questions. Every rule
below exists to keep it short:

| | |
|---|---|
| `RESCUE_AFTER` (5 min) | how long adrift before it gives up on the house |
| `RESCUE_GIVE_UP` (15 min) | if nobody joins, tear it down and go back to looking |
| Ethernet | never, while a cable is working |
| Credentials arrive | drop the access point at once and go and use them |

The failure being designed against is not the access point failing to come
up. It is a router rebooting for ninety seconds while the speaker quietly
deserts it and stops looking. A speaker that abandons a working network is
harder to live with than one that occasionally needs setting up.

### The captive portal needs the Pi's password once

Joining the access point should make a phone pop up a sign-in sheet
straight onto this page. That needs two pieces of system config, installed
by `./deploy.sh` **run from a terminal where it can ask for the Pi's
password**:

- `net.ipv4.ip_unprivileged_port_start=80`, so the dashboard can answer on
  port 80 — the port every operating system probes to decide whether a
  network needs signing into.
- a dnsmasq line pointing every name at `10.42.0.1`, so the probe arrives.

Without them the access point still works and the page is still on port
8080; the phone just will not offer to open it, and may warn that the
network has no internet. The log says which you have:

    [dashboard] no captive portal on port 80 (PermissionError) — the page
    is still on 8080

## How it runs

Inside the speaker, on a daemon thread — not beside it. That is the whole
reason it can show anything interesting: what is playing, what timers are
set, what was asked a minute ago and what the wake word scored are all in
that process and nowhere else. A separate service would have to guess at
them from files.

The cost is a rule: **nothing here may ever raise into the speaker.** Every
handler is wrapped, the server is threaded so one slow request cannot block
another, and if the whole thing fails to start the speaker carries on
without it. The dashboard's own request log is silenced — the speaker's log
is for the speaker.

```bash
python src/dashboard.py     # serve it on its own, for development
```

## It has no password

Anybody who can reach the Pi can see what was asked in that room and change
which network it is on. **This belongs on a home network and nowhere else.**
Do not forward a port to it.
