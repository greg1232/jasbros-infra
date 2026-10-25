# The wake word

How the speaker hears its name, and how it gets better at it.

    two seconds of audio
        -> Whisper tiny.en encoder      768 numbers
        -> logistic regression          one score, 0 to 1
        -> over the line?               wake up

## What it is

A logistic regression on the output of Whisper's `tiny.en` encoder, mean and
max pooled over the first 100 frames of a two-second window. The encoder is
frozen and does the hearing; the regression is the only part that is
trained, and it is 768 weights and a standardiser in an `.npz`.

That means the speaker needs numpy and Whisper to run the wake word, and
scikit-learn only to fit one.

The window is scored every `WAKE_STRIDE_SECONDS` (0.4). The phrase is under
a second, so it falls inside several consecutive windows and does not have
to be caught the first time.

### What it costs

131 ms per two-second window on a Pi 4 — 0.26× realtime, about a quarter of
one core, leaving three for Whisper and the voice.

### Why not openWakeWord

`wake.py` still supports it, and `WAKE_MODE=openwakeword` selects it. It is
cheaper — 13 ms per 80 ms chunk, 0.16× realtime — but on this data it tops
out around **59% recall at 127 false wakes an hour**, against about 90%
recall at no measurable false wakes for the encoder.

The reason is where the compute sits. openWakeWord is a mel filterbank into
a frozen 0.33M-parameter CNN from 2020 with a small head on top. On this Pi
the CNN is 91% of the cost, the head is 1% — and the head is the only part
anyone can train. Everything tunable is 1% of the compute, so when the
frozen features don't separate the classes, nothing done above them helps.

`WAKE_MODE=key` is the third option: press Enter instead. Always works, no
setup, useful while sorting something else out.

## What gets written down

Every firing, and every near miss, goes in `state/wakes/`. This is what
makes the speaker able to learn from its own mistakes without anybody
recording anything.

| | |
|---|---|
| the 768 numbers | the vector that was scored. **Free** — the encoder pass that produced it is what fired the wake word. 1.5 kB, kept forever |
| the two seconds | the audio, so a person can listen and so features can be recomputed if the encoder changes. 64 kB, so only the most recent `WAKE_LOG_CLIPS` (1500) are kept |
| what happened next | what Whisper heard, and whether Claude thought it was being spoken to. Written after the turn, because that is when it is known |

The last of those is the valuable one. A firing followed by silence is
almost certainly a mistake; one followed by a question that got answered is
almost certainly real. The speaker works both out anyway in the course of
answering.

### Near misses

Logging only what fires teaches the speaker nothing about recall, which is
the weaker half — held out entirely, it catches about half of one child's
attempts. The times somebody said "hey Claude" and nothing happened are
exactly what would fix that, and they never fire, so they would never be
logged.

So windows scoring above `WAKE_NEAR` (0.5) are held in memory for
`WAKE_NEAR_SECONDS` (15), and when a firing does happen the ones just before
it are written down too. A near miss followed within `WAKE_REPEAT_SECONDS`
(4) by a real firing is somebody saying it again because the first go was
missed — a labelled recall failure that nobody had to label.

A slower trickle, one every `WAKE_NEAR_EVERY` (180 s), catches the attempts
that were never followed by success because whoever it was gave up.

Nothing in the logging may slow a turn down or break one. Every call is
wrapped: a full disk costs you the log, not the speaker.

## Labelling

`train/label_wakes.py` turns the pile into labelled data, using three things
in order of what they cost:

**1. What happened next.** Free, and the most reliable thing here. Nothing
said → almost certainly a mistake. A question that got answered → almost
certainly real. For a near miss, the equivalent is repetition.

**2. What Whisper makes of the two seconds — in one direction only.**
Against 80 real recordings, `tiny.en` transcribes the wake word 5% of the
time and `base.en` 16%. It hears "It's hot", "Take that", "Great class" —
the right rhythm and roughly the right vowels, overruled by a language model
for which "Claude" is rare and "take that" is common.

So a window that *does* say Claude is strong evidence it was real, and one
that doesn't is no evidence at all. What the transcript is genuinely good
for is the opposite case: Whisper handles ordinary English perfectly well,
so a window that comes out as a fluent sentence of television is evidence
the speaker was not being spoken to.

**3. Claude**, for the ones still in doubt, told plainly how unreliable the
window transcript is so it doesn't make the same mistake.

Nothing is thrown away and nothing is overwritten. Labels are appended to
the same log, so a bad run can be relabelled and the audio is still there to
listen to.

### A person listening

`./label.sh` fetches the clips to the laptop, opens a page, and plays them
one at a time: **y** if that really was somebody saying the wake word, **n**
if it wasn't. Answers go back to the Pi.

This matters because the automatic labels are good at the easy half and
guess at the hard half, and the guesses are worth `BY_PERSON` = 5× less than
a person's in the fit. Thirty clips takes five minutes.

Nothing runs on the Pi and there is nothing to install; the clips are copied
down and served from the laptop.

## Retraining

```bash
./relearn.sh              # label today's firings, refit, keep if better
./relearn.sh --dry        # say what would change, change nothing
./relearn.sh --force      # take the new model even if it isn't better
./relearn.sh --log        # what the timer did on its own, last time
```

A systemd timer runs the same thing at 04:00 nightly, with 15 minutes of
jitter and `Persistent=true` so a Pi that was off catches up.

It never touches audio. Turning audio into features is the expensive part —
159 ms per window, twenty minutes for the whole set — so instead it joins
two piles of features that already exist: **the bank** that
`train_whisper_wake.py` embedded, saved beside the model, and **the log**,
whose vectors cost nothing because the encoder pass that made them is what
fired the wake word.

Fitting on those is a second's work. That is the whole reason this can run
on the Pi itself, overnight, on data the Pi collected, with no laptop
involved.

### The gate

A model that measures worse than the one it would replace is not kept. A
fifth of the labelled firings are held back, the candidate is fitted without
them, and both models are asked about those. `SLACK` (0.05) allows a
candidate to be slightly worse and still be taken, because these are small
samples and being the same within noise is not a reason to refuse.

Below `LEAST_TO_JUDGE` (25) labelled firings there is nothing to hold back
and the new model is taken on trust — it can only be the bank plus a
handful. A recall figure needs at least `ENOUGH_POSITIVES` (8) positives to
mean anything; with one, recall is 0% or 100% and nothing between.

The model being replaced is always kept as `.npz.previous`, whether the gate
passed or `--force` overrode it.

### Regularisation

`WAKE_FIT_C` (0.001) is how hard the fit is held back — smaller means more
regularisation. 768 features against a few thousand rows is a great deal of
room to overfit. Measured by five-fold cross-validation, at the strictest
threshold that still fires on no more than a tenth of known mistakes:

| C | catches | of the ones said to it |
|---|---|---|
| 1.0 | 75% | 62% |
| 0.1 | 85% | 79% |
| 0.01 | 95% | 90% |
| **0.001** | **99%** | **97%** |

Weighting the human labels 1×, 5× or 20× moved nothing by comparison, and a
small neural net in place of the regression matched `C=0.001` without
beating it.

### Where the line goes

The fit sweeps thresholds from 0.30 to 0.999 and keeps the one it chose
inside the model file, so a fitted model carries its own operating point.
Setting `WAKE_THRESHOLD` in `.env` overrides that; leaving it unset lets the
model use what its sweep picked.

`WAKE_FALSE_BUDGET` (0.05) caps how much firing on the room the chosen
threshold may cost, as a fraction of the mistakes a person has labelled.
**This is the one number in the retraining that is a judgement rather than a
measurement** — how much television is worth how much of a child being heard
— so it is a setting, and the sweep prints the whole curve beside its choice
so the judgement can be checked.

Measured leak-free on 443 hand labels:

| budget | line | catches | fires on |
|---|---|---|---|
| 15% | 0.400 | 97% | 14.8% |
| **5%** | **0.630** | **91%** | **4.4%** |
| 2% | 0.780 | 80% | 1.8% |

Five per cent keeps nine tenths of the recall for a third of the mistakes.
A false wake is nearly silent — nothing is said, nothing is answered, it
costs a flash of the ring — but a budget is a rate times an exposure, and
the exposure is large: sixteen firings an hour in this room with the
television on, fourteen of them with nobody talking.

## Teaching it a voice

"Hey Claude, learn my voice", then say the wake word about ten times with a
pause between each while the ring is purple. It refits and reloads in
seconds.

This is the cure for the speaker missing a particular person, and it is the
only one — the fix for poor recall on a child is more recordings of that
child. Nobody labels anything: the person was asked to say the wake word
over and over, so every segment is the wake word by construction.

Two guards. It needs at least three segments or the recording is thrown away
rather than half-learned. And each segment is turned into the same 768
numbers the detector scores, with any that doesn't resemble the others
dropped — a cough, a door, a sibling shouting are all outliers among ten
repetitions of one phrase.

Recordings go in `state/`, not `train/`, because deploy mirrors the project
directory and would delete them.

## Measuring it honestly

```bash
./evaluate.sh              # on firings the model has never seen
./evaluate.sh --compare    # which recipe is actually better
```

`relearn.py`'s job is to decide whether to promote a model, not to say how
good one is — it compares a candidate against the installed model, which was
fitted on everything available at the time. Asking that model about its own
training data is how you get 100% recall and 0% false wakes, which is memory
rather than skill.

`train/evaluate.py` answers the other question, two ways, both leak-free:

- **before learning** — the shipped model, fitted only on the recorded
  corpus in `train/`, scored on everything from this room. It has genuinely
  never seen any of it. The baseline: what you get with no learning at all.
- **after learning** — five-fold cross-validation. The log is cut into five;
  a model is fitted on the bank, the machine labels and four fifths of what
  a person vouched for, then scored on the fifth it did not see. Five
  models, five disjoint test sets, pooled into one curve.

Ground truth is what a person can vouch for: clips somebody listened to and
labelled, plus enrolment recordings. Augmented copies of enrolment
recordings are allowed in training and never in a test set.

The two kinds of positive are reported apart, because enrolment is
deliberate and a logged firing is not.

### What it measures at

At the shipped threshold of 0.5:

| | |
|---|---|
| recall, known voices | 100% |
| recall, a voice it has never heard | 66% |
| false wakes per hour, room noise | 0.0 |

## Settings

| | |
|---|---|
| `WAKE_MODE` | `auto`, `openwakeword`, or `key` |
| `WAKE_MODEL` | `hey_claude_whisper.npz` on the Pi |
| `WAKE_THRESHOLD` | overrides what the model's own sweep chose |
| `WAKE_STRIDE_SECONDS` | how often it looks (0.4) |
| `WAKE_LOG` | write down firings at all (on) |
| `WAKE_LOG_CLIPS` | how many recordings to keep (1500) |
| `WAKE_NEAR` | score above which a near miss is remembered (0.5) |
| `WAKE_NEAR_SECONDS` | how long to hold them (15) |
| `WAKE_REPEAT_SECONDS` | within this, a repeat labels the miss (4) |
| `WAKE_NEAR_EVERY` | slow sample of give-ups, seconds (180) |
| `WAKE_FIT_C` | regularisation, read by `relearn.py` (0.001) |
| `WAKE_FALSE_BUDGET` | false wakes the sweep may buy, read by `relearn.py` (0.05) |

## Training one from scratch

```bash
python train/record_wake.py          # people saying it on purpose
python train/record_room.py          # the room not saying it
python train/train_whisper_wake.py   # writes models/hey_claude_whisper.npz
python train/test_wake.py            # does it hear you
python train/test_silence.py         # does it stay quiet in an empty room
```
