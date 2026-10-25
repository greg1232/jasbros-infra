# When something goes wrong

## It can't hear me

**Recording is silent, or it never wakes up.** Almost always the wrong
microphone:

```bash
python src/audio_in.py --devices
```

Set `INPUT_DEVICE` in `.env` to the right one. On macOS, a virtual device
(BlackHole, Loopback, Zoom) records what the computer is *playing* rather
than what you are saying, and is often the default.

**On the Pi, check the hardware before the code:**

```bash
ssh you@your-pi arecord -l
```

No capture devices means nothing is reaching the Pi at all — a USB mic
should appear in `lsusb`. Then try recording from it directly:

```bash
ssh you@your-pi 'arecord -D plughw:3,0 -f S16_LE -r 16000 -c 2 -d 3 /tmp/c.wav; ls -l /tmp/c.wav'
```

Three seconds should be about 192 KB. **44 bytes and `read error:
Input/output error` means the device enumerates but won't stream** — which
on a Pi 4 is usually the socket. Move the array from a black USB 2 port to a
blue USB 3 one. Try this first, because every software symptom looks the
same as this hardware one.

**It hears me but ignores me.** Lower `WAKE_THRESHOLD` to 0.3, or set
`WAKE_MODE=key` and press Enter while you sort it out. If it misses one
person in particular, the real fix is "hey Claude, learn my voice" — see
[wake-word.md](wake-word.md).

## It goes deaf after a while, and playing something wakes it up

**This is the array, not the wake word.** The reSpeaker XVF3800 only sends
microphone audio while its playback endpoint is being driven. Leave the
sink idle and capture stops dead — and nothing notices: ALSA still reports
the stream `RUNNING`, PipeWire still shows the source `running`, no xrun,
no error, nothing in any log. `mic.read()` simply returns nothing for ever
and the wake word waits on a microphone that will never speak again.

Playing anything revives it for exactly as long as the sound lasts, which
is why pressing play appears to fix it.

Check whether audio is actually arriving — the hardware pointer has to move
at about the sample rate:

```bash
ssh you@your-pi 'a=$(awk -F: "/hw_ptr/{print \$2}" /proc/asound/card3/pcm0c/sub0/status); \
  sleep 5; b=$(awk -F: "/hw_ptr/{print \$2}" /proc/asound/card3/pcm0c/sub0/status); \
  echo $(( (b-a)/5 )) frames a second'
```

**16000 is healthy. 0 means it has gone deaf.** Restarting the speaker will
not fix it, and neither will restarting PipeWire — only driving the
playback half does.

The cure is a WirePlumber rule that keeps the sink running with no client
attached, which `./deploy.sh` installs at
`~/.config/wireplumber/wireplumber.conf.d/50-respeaker-no-suspend.conf`. If
the array has gone deaf, check that file is there and run `./deploy.sh`
again if it isn't.

**It survives a reboot**, which is worth saying because the rule is a file
in a home directory read by a user service, and that is exactly the sort of
thing that quietly does not come back. Measured on a cold boot: the machine
came up at 12:06:03, the speaker was ready at 12:06:58, the playback PCM was
`RUNNING` with nothing attached to it, and capture held 16002 to 16013
frames a second through five minutes of silence with the watchdog below
never once having to step in. Under the old behaviour the array had stopped
after 69.8 seconds.

Measured on this Pi: with a 30 second tone playing, capture ran at 16040
frames a second for exactly as long as the tone lasted and stopped within
four seconds of it ending.

**The speaker now notices on its own.** A working microphone hands over a
chunk every 80 ms even in a silent room, so if `MIC_DEAF_SECONDS` (10) go
by with nothing at all, `audio_in` says so in the log and plays a quarter
second of silence to bring the array back:

```
[microphone] nothing from the array in 10s — waking it with a moment of
silence (time 1)
```

One of those is worth a look. A stream of them means the WirePlumber rule
is missing or not working, and the speaker is limping along on the net
rather than being fixed — tested by deleting the rule, it recovers within
ten seconds each time but only hears about half the time in between.

The dashboard carries the same thing under `microphone`, which is the only
way to tell a deaf speaker from one nobody is talking to without going and
saying the wake word:

```json
"microphone": { "silent_for": 0.0, "hearing": true, "gone_deaf": 0 }
```

## It wakes up when nobody said anything

Measure it before turning dials:

```bash
python train/test_silence.py models/hey_claude_whisper.npz --seconds 180
```

If that **fails**, the model never learned what a quiet room is and needs
retraining with more negative data. Raising `WAKE_THRESHOLD` only hides it.

If it **passes**, something in the room is setting it off. Label thirty
clips with `./label.sh` and let the nightly retraining use them; that is
what the whole learning loop is for. Lowering `WAKE_FALSE_BUDGET` moves the
line the next time it fits.

## It cuts me off mid-sentence

`SILENCE_SECONDS=1.5` gives you more silence before it stops listening.
`MIN_RECORD_SECONDS` guards against a slow start; `PRE_ROLL_SECONDS` (0.5)
is how much audio from before the recording starts is kept, so running
straight from the wake word into the question doesn't lose the first word.

## Sound problems

**Too quiet, even with everything turned up.** Check what the device is
actually set to:

```bash
ssh you@your-pi 'amixer -c 3 contents | grep -A3 "Playback Volume"'
```

USB audio often ships attenuated, and on the Pi there are *two* gain stages
— the array's own and PipeWire's. `OUTPUT_VOLUME=100` sets both at every
startup; if you have set it blank, this is why it is quiet. See
[raspberry-pi.md](raspberry-pi.md).

**Silent, or talking out of the wrong socket.**

```bash
python src/tts.py --devices
```

Then set `OUTPUT_DEVICE`. ALSA puts the HDMI outputs first, so the default
is often a monitor with no speakers.

**It talks too fast, or the voice grates.** `SPEECH_RATE=150` to slow down.
On a Mac `say -v '?'` lists every voice, then set `VOICE`. On the Pi set
`PIPER_VOICE` from
[rhasspy.github.io/piper-samples](https://rhasspy.github.io/piper-samples/)
— and keep to a *medium* voice, because a *high* one synthesises slower than
it speaks.

**Long pauses between sentences, or it falls behind itself.** You are on a
`high` Piper voice. Switch to `medium`.

## Setup problems

**"No API key found."** You skipped step 2 — `cp .env.example .env` and
paste your key in.

**A setting seems to be ignored.** Print what it will actually use:

```bash
python src/config.py
```

`.env` overrides the defaults and a typo in a name is silent. Note that two
training settings — `WAKE_FIT_C` and `WAKE_FALSE_BUDGET` — are read by
`train/relearn.py` and will not appear here.

**A capability is missing.** One feature module that fails to import doesn't
take the others down; it prints `[tools] <name> unavailable:` at startup.
Check with `python src/tools.py`, which lists what actually registered.

**Music doesn't play.** Spotify Premium is required — librespot cannot play
on a free account at all. Check the service with
`systemctl --user status librespot`.

**The dashboard isn't there.** It is on port 8080 unless `DASHBOARD_PORT`
says otherwise, and `DASHBOARD=off` disables it. If it failed to start the
speaker carries on without it by design, so look in
`journalctl --user-unit=claude-speaker`.
