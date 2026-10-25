# On a Raspberry Pi

A laptop is a fine place to build this, but a speaker belongs on a shelf.

## A new speaker

    ./card.sh bedroom --flash     erase the card, write 64-bit Pi OS, set it up
    ./card.sh bedroom             just set up a card that already has it
    ./card.sh --find              what address it came up on

Then `./deploy.sh normal@<that address>`.

**It has to be the 64-bit image.** `ctranslate2`, the engine under
faster-whisper, has no 32-bit ARM build — pip answers "from versions:
none" — so a 32-bit card gives you no speech recognition and no wake word,
and a deploy onto one gets as far as an empty virtualenv and stops.
`card.sh` refuses a 32-bit card rather than letting you find that out after
a deploy.

Do not judge this by what the Pi reports. Raspberry Pi OS 32-bit boots a
*64-bit kernel* on a Pi 4, so `uname -m` says `aarch64` and Python's
`sysconfig` says `linux-aarch64`, while pip correctly resolves `armv7l`
wheels. On the Pi, only `dpkg --print-architecture` is honest. From the
card, the tell is which kernels are present:

    32-bit   kernel.img  kernel7.img  kernel8.img
    64-bit                            kernel8.img  kernel_2712.img

### Why not Raspberry Pi Imager's settings

They are not part of the image. Imager writes `custom.toml` **and** adds
`init=/usr/lib/raspberrypi-sys-mods/firstboot` to `cmdline.txt`; the hook
is what reads the file. Flash the same image any other way and the hook is
absent and `custom.toml` is decoration.

Worse, that path does not exist on these images, so adding the hook by
hand leaves the kernel with no init and the Pi never boots — silently, if
it has no monitor. `card.sh` refuses to proceed if it finds one.

What does work: `userconf.txt` for the user, an empty `ssh` file, and
`systemd.run=` for a first-boot script — which runs as a unit rather than
replacing init, so a broken script costs you the script and not the
machine. That script runs *before NetworkManager starts*, so it writes a
`.nmconnection` file rather than calling `nmcli`, and chmods it 600 or
NetworkManager ignores it.

## Deploying

```bash
./deploy.sh normal@192.168.4.95    # the first time — it remembers the address
./deploy.sh                        # every time after that
./deploy.sh --run                  # deploy, then start it and watch
./deploy.sh --service              # deploy, and start it on every boot
./deploy.sh --no-apt               # skip system packages (no sudo at all)
```

It copies the code, installs what the Pi needs, sends your API key over the
SSH connection into a file only you can read, and downloads the voice. Run
it as often as you like — it only redoes what changed. The first run takes
a few minutes, mostly downloading Python packages; later ones take seconds.

The address is remembered in `.deploy-target`, which holds one host. Every
other laptop command (`./label.sh`, `./relearn.sh`, `./wishes.sh`) reads the
same file.

Two settings are rewritten on the way over, because the Pi is not a Mac:

| | on the laptop | on the Pi |
|---|---|---|
| Voice | macOS `say` | Piper, `en_GB-alan-medium` |
| Speech recognition | `base.en` | `tiny.en` — a Pi 4 is slower |

Everything else — your key, your town, the wake word — carries over as is.
To write the Pi's settings by hand instead, make a `.env.pi` and that is
sent untouched.

System packages installed once: `python3-venv`, `libportaudio2`,
`libsndfile1`, `espeak-ng`, `alsa-utils`, and the ALSA-to-PipeWire bridge —
without which the array really does allow one program at a time.

## Running it

```bash
cd ~/claude-speaker
./start.sh                                    # start it, or restart it
./start.sh --status                           # is it running
./start.sh --stop                             # stop it
./start.sh --foreground                       # watch it here, Ctrl-C to stop
./start.sh --text                             # type questions instead
journalctl --user-unit=claude-speaker -f      # what it hears and says
```

**Everything goes through systemd.** There is no second way to run the
speaker, because two of them cannot share a microphone array: playing and
listening are the same piece of hardware and it allows one stream, so the
second one fails or quietly steals it from the first.

`start.py` runs on the system Python and only the standard library, because
its first job is to build the virtualenv everything else needs.

### The services

Three, all **user** services, so none of this needs a password.
`systemctl --user` does not need root, and `loginctl enable-linger` is what
makes them start at boot with nobody logged in. The speaker has no reason to
be root — it needs the audio group, which you are already in.

| | |
|---|---|
| `claude-speaker.service` | the speaker itself; restarts on failure |
| `librespot.service` | Spotify Connect, if music is set up |
| `claude-relearn.timer` | retrains the wake word at 04:00 |

The timer has `RandomizedDelaySec=15m` and `Persistent=true`, so it jitters
and a Pi that was switched off catches up. See
[docs/wake-word.md](wake-word.md).

librespot is installed as this user's own service rather than the system one
that ships with it. No Spotify password goes anywhere near the Pi —
librespot advertises itself and the phone hands it a token.

`./deploy.sh` restarts a running speaker for you, so deployed code takes
effect.

## The microphone array

A **reSpeaker XVF3800 4-Mic Array**. It captures at 16 kHz, which is exactly
what the wake word and Whisper both want, so nothing is resampled on the way
in, and it does beamforming and echo cancellation on its own chip.

It is a speaker as well as a microphone. If your speakers are wired into the
array rather than the Pi's headphone socket, use it for both:

```
INPUT_DEVICE=
OUTPUT_DEVICE=Array
```

Worth doing: the array cancels its own output in hardware, so it genuinely
does not hear Claude talking.

The catch is that it only accepts 16 kHz, and Piper's voices come out at
22.05 kHz, so `tts.py` resamples on the way out. And because it is one piece
of hardware, only one stream can be open at a time — which is why `tts.py`
closes the device after every sound rather than leaving a player running.

### It goes deaf if the speaker half goes quiet

The array only sends microphone audio while its playback endpoint is being
driven. Left idle, capture stops — with ALSA still reporting the stream
`RUNNING` and nothing logged anywhere. `./deploy.sh` installs a WirePlumber
rule (`node.always-process`) that keeps the sink running with no client
attached, which keeps the device clocked and the microphone arriving. See
[troubleshooting.md](troubleshooting.md) for how to check it.

Plug it into a **blue USB 3.0 port**. On a Pi 4 the black USB 2 sockets can
enumerate the array fine and then refuse to stream from it.

### Two volume controls

The array arrives attenuated — `PCM Playback Volume` at 37 of 60, on a scale
where 60 is 0 dB — and PipeWire puts a second gain stage in front of it,
which sits at 0.40 by default. Together that is about 31 dB down, roughly
thirty-five times quieter, and no amount of software gain fixes it because
Piper already peaks at full scale.

So `turn_up()` sets both at every startup, asking PipeWire which card the
output actually lands on. It happens at startup rather than at install
because mixer levels do not reliably survive a reboot, and a speaker that
goes quiet when the power blinks is worse than one that was never loud.

```
OUTPUT_VOLUME=100     # turn it down for the evening, or blank to not touch it
```

## The voice

macOS has `say` built in; Linux has nothing, so the Pi speaks with
[Piper](https://rhasspy.github.io/piper-samples/), a small neural voice that
runs locally like everything else here.

Measured on this Pi 4, per second of speech synthesised:

| voice | cost |
|---|---|
| `en_GB-alan-medium` | 0.31× — three times faster than real time |
| `en_US-ryan-medium` | 0.32× |
| `en_US-ryan-high` | 2.0× — slower than talking, so don't |

**Stick to a medium voice.** Loading one takes about five seconds, so it is
loaded at startup and kept, not reloaded per answer.

The bigger expressive models cannot go here at all: over 3 GB of weights
against the Pi's 3.8 GB of memory, before Python has started, and minutes
per sentence on four Arm cores.

Piper makes one chunk per sentence, and on a Pi each takes about six tenths
of a second — half as long as the sentence lasts. One thread makes the sound
and another plays it, so the next sentence is ready before the current one
ends: 4.13 s against 4.95 s for a three-sentence answer. The silence Piper
leaves at both ends of a chunk is trimmed and replaced with `SENTENCE_PAUSE`
(0.12 s), so the spacing is a choice.

```bash
python src/tts.py --devices     # lists the speakers it can see
```

Worth checking on a Pi: ALSA lists the HDMI outputs first, so the default is
often a monitor with no speakers.

## What it uses

Measured over nearly five hours on a Pi 4:

| | |
|---|---|
| the speaker, peak | 679 MB |
| whole system, with a desktop running | 897 MB |
| a retraining run, peak | 232 MB |
| the wake word | about a quarter of one core |

It fits in 2 GB with room to spare. 4 GB is worth having for deploy headroom
and for anything you fine-tune later, not for the steady state.
