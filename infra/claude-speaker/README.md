# Claude Speaker

A speaker you talk to, and it talks back.

Say the wake word, ask a question out loud, and it answers out loud. It runs
on a laptop, and properly on a Raspberry Pi with a microphone array.

```
 you ──"hey claude"──►  wake word  ──►  record  ──►  Whisper  ──►  Claude
                                                                     │
 you  ◄────── speakers ◄────── Piper ◄─────────── tools ◄────────────┘
                                                  timers, rain, books,
                                                  music, the weather…
```

The wake word, the speech recognition and the voice all run on the machine
itself. Nothing leaves the house until there is a question to ask.

## Setup

**1. Install the pieces** (once):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**2. Add your API key:**

```bash
cp .env.example .env
```

Open `.env` and paste in your key from
[console.anthropic.com](https://console.anthropic.com/settings/keys). `.env`
is in `.gitignore`, so it never gets committed.

**3. Pick the right microphone.** macOS sometimes defaults to a virtual
audio device (BlackHole, Loopback, Zoom) that records what the computer is
*playing* instead of what you are *saying*:

```bash
python src/audio_in.py --devices
```

If the one marked `<- default` isn't your real microphone, put
`INPUT_DEVICE=MacBook Air Microphone` in `.env`.

**4. Let the terminal use the microphone.** macOS asks the first time. If it
doesn't, turn it on under System Settings → Privacy & Security → Microphone.

## Running it

```bash
python src/main.py           # the real thing — say the wake word
python src/main.py --text    # type questions instead of speaking them
```

Ctrl-C stops it. On the Pi it runs as a systemd service — see
[docs/raspberry-pi.md](docs/raspberry-pi.md).

## Living with it

Six commands, all from this folder on the laptop:

```bash
./card.sh bedroom        # prepare an SD card for a new speaker
./deploy.sh              # put the current code on the Pi
./start.sh --status      # (on the Pi) is it running
./wishes.sh              # what it was asked for and couldn't do
./label.sh               # listen to what woke it, and say if it was right
./relearn.sh             # teach it from today — it also runs itself at 4am
./evaluate.sh            # how good the wake word is on firings it hasn't seen
./evaluate.sh --compare  # which training recipe is better
```

With more than one speaker, every one of those takes `--pi`:

```bash
./card.sh bedroom --flash    # a new one, from a blank card
./deploy.sh --pi bedroom     # or ./deploy.sh normal@192.168.4.22 the first time
./label.sh --pi bedroom
```

`.deploy-target` keeps the list, newest first, and the first line is what
you get when you don't say. Any unambiguous piece of a name will do, so
`--pi bed` is enough.

`./label.sh` is the one worth a few minutes a week. It plays back the clips
that woke the speaker and you answer yes or no; those answers are the only
ground truth the project has, and the nightly retraining is judged against
them. See [docs/wake-word.md](docs/wake-word.md).

The dashboard is at `http://<the pi>:8080`.

## Trying one piece at a time

Every file runs on its own, which is how you find what's broken. All of them
take `--help`, including the ones in `train/`.

```bash
python src/tts.py            # 1. make it talk
python src/brain.py          # 2. type a question, hear Claude answer
python src/audio_in.py       # 3. record you until you stop talking
python src/stt.py            # 4. record you and print what it heard
python src/wake.py           # 5. wake up three times, then stop
python src/config.py         #    print every setting it will actually use
python src/tools.py          #    list every tool Claude is offered
python src/sounds.py rain    #    a sound made from filtered noise
python src/effects.py owl    #    look a real recording up and play it
python src/books.py --shelf  #    the children's shelf, from 48,284 books
python src/music.py          #    what Spotify is playing, and where
python src/wake_log.py       #    what has woken it, and how those went
```

## The files

| File | What it does |
|------|--------------|
| `src/main.py` | The loop that ties everything together |
| `src/wake.py` | Waits for the wake word (or the Enter key) |
| `src/whisper_wake.py` | The wake word itself, on Whisper's encoder |
| `src/wake_log.py` | Writes down every firing, to learn from |
| `src/audio_in.py` | Microphone — records until you stop talking |
| `src/stt.py` | Turns the recording into words (Whisper, locally) |
| `src/brain.py` | Asks Claude, and runs the tools it asks for |
| `src/tts.py` | Says the answer out loud (macOS `say`, or Piper) |
| `src/lights.py` | The LED ring on the array |
| `src/dashboard.py` | A page served from the Pi — state, metrics, Wi-Fi |
| `src/config.py` | Every setting, in one place |
| **Tools** | |
| `src/tools.py` | The framework: collect, describe, run |
| `src/timers.py` | Timers and alarms, and the ringing |
| `src/weather.py` | The forecast |
| `src/sounds.py` | Rain, ocean, a fire — made as they play |
| `src/effects.py` | Real recordings — what a bullfrog sounds like |
| `src/books.py` | Reads books aloud, and remembers the place |
| `src/music.py` | Spotify — search, play, skip, volume |
| `src/search.py` | Web search, which runs on Anthropic's side |
| `src/enroll.py` | Learns a voice from somebody repeating the wake word |
| `src/wishes.py` | Writes down what it was asked for and can't do |
| `src/rescue.py` | Becomes an access point when it can't find a network |
| `src/eggs.py` | The things it does that nobody told you about |
| **Hardware** | |
| `case/speaker-case.scad` | A printed case for the Pi and the array — see [case/README.md](case/README.md) |
| **Training and setup** | |
| `train/record_wake.py` | Records people saying the wake word |
| `train/record_room.py` | Records the room not saying it |
| `train/train_whisper_wake.py` | Trains the wake word |
| `train/label_wakes.py` | Decides which logged firings were real |
| `train/relearn.py` | Retrains on them, in about a second |
| `train/evaluate.py` | Measures it on firings it has never seen |
| `train/archive.py` | Commits the data, and stamps the model with it |
| `train/test_wake.py` | Checks it hears you |
| `train/test_silence.py` | Checks it doesn't fire in a quiet room |
| `train/build_book_index.py` | Builds the local index of 48,284 books |
| `train/spotify_login.py` | Signs in to Spotify once, for a token |
| **On the laptop** | |
| `card.py` | Prepares an SD card for a new speaker (`./card.sh`) |
| `speakers.py` | Which Pi a command is talking to, when there's more than one |
| `deploy.py` | Puts the whole thing on a Pi (`./deploy.sh`) |
| `start.py` | Starts and stops the service (`./start.sh`) |
| `wishes.py` | Reads the wishes off the Pi (`./wishes.sh`) |
| `label.py` | Listen to what woke it and say if it was right (`./label.sh`) |
| `relearn.py` | Make it learn from today, now (`./relearn.sh`) |

Only `brain.py`, `weather.py`, `effects.py`, `books.py` and `music.py` use
the internet. The microphone, the wake word, the speech recognition and the
voice all run on the machine itself.

## What it can do

```
"hey claude, why is the sky blue"
"hey claude, set a timer for ten minutes"
"hey claude, wake me up at seven"
"hey claude, what's the weather on Thursday"
"hey claude, who won the game last night"      <- searches the web
"hey claude, play rain until the morning"
"hey claude, what does a bullfrog say"
"hey claude, read me Treasure Island"
"hey claude, play Baby Shark"
"hey claude, learn my voice"
"hey claude, can you keep score for our game"  <- writes down a wish
```

Twenty-eight tools in all, plus ten things it does that aren't written down
here. Those are meant to be found by accident; if you need to know, they are
in `src/eggs.py`. See **[docs/capabilities.md](docs/capabilities.md)** for
every tool and the settings it needs.

## The documentation

| | |
|---|---|
| **[docs/capabilities.md](docs/capabilities.md)** | Everything it can do, and the settings for each |
| **[docs/tools.md](docs/tools.md)** | How tools work, and how to add one |
| **[docs/wake-word.md](docs/wake-word.md)** | How it hears its name, and how it learns |
| **[docs/raspberry-pi.md](docs/raspberry-pi.md)** | Deploying, the services, the array, the voice |
| **[docs/dashboard.md](docs/dashboard.md)** | The page served from the Pi |
| **[docs/troubleshooting.md](docs/troubleshooting.md)** | When something goes wrong |
| **[docs/wifi-and-display.md](docs/wifi-and-display.md)** | *Planned* — a screen you can plug in |

## Not built yet

- **A follow-up window.** Keep listening for a few seconds after answering,
  so a conversation doesn't need the wake word every turn.
- **Barge-in.** It can be stopped mid-sentence by saying "I have spoken",
  but with the microphone left open its own voice scores 0.991 against a
  0.95 threshold, so it interrupts itself on about one sentence in four.
- **Memory between runs.** It forgets everything when it restarts,
  including who it is talking to.
- **Children's speech.** `tiny.en` struggles most with exactly the voices
  this is for. Fine-tuning is well evidenced — around a third off the error
  rate — and the recordings are already in this repo.
- **More than one speaker.** `.deploy-target` holds a single host, and
  `train/archive.py` uploads every device's state to the same repository
  root, so a second Pi would overwrite the first one's data.
- **A screen you can plug in.** See
  [docs/wifi-and-display.md](docs/wifi-and-display.md).
