"""Teaching the speaker a voice, by saying the wake word at it.

    "hey Claude, learn my voice"
    "Say hey Claude over and over, with a pause between each."
    "hey Claude ... hey Claude ... hey Claude ..."
    "I got nine. Give me a moment."     <- refits, reloads, done in seconds

This is the answer to recall. Held out entirely, the wake word catches
about half of one child's attempts, and the only cure for that is more
recordings of that child — which used to mean sitting down with
train/record_wake.py and a laptop. Now the speaker collects them itself, in
the room it lives in, through the microphone it actually listens with.

Nobody labels anything. The person was asked to say the wake word over and
over, so every segment is the wake word by construction. That is the whole
trick.

Two things guard against it learning rubbish:

  it must hear enough      three segments at least, or the recording is
                           thrown away rather than half-learned.
  they must resemble       each segment is turned into the same 768 numbers
  each other               the detector scores, and any that doesn't look
                           like the others is dropped. A cough, a door, a
                           sibling shouting — all outliers among ten
                           repetitions of one phrase.

The recordings are kept in state/, not in train/, because deploy mirrors
the project directory and would delete them.

    python src/enroll.py        record a round now, without the speaker
"""

import sys
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

import config
import tools
import wake_log

KEPT = config.PROJECT_ROOT / "state" / "enrolled"

# How long to listen for repetitions once they've started, and how much
# quiet means they've finished.
LISTEN_SECONDS = 25.0
DONE_AFTER_QUIET = 3.0
# And however quiet it goes, don't call it finished before this much has
# been recorded. Somebody pausing to think between the second and third
# repetition is not somebody who has stopped.
LEAST_RECORDED = 5.0
# And how long to wait for them to begin at all. Somebody who has just been
# told what to do takes a moment to draw breath, and the whole thing used
# to give up during that breath.
WAIT_TO_START = 12.0

# A "hey Claude" is about three-quarters of a second. Anything much shorter
# is a knock, anything much longer is a sentence.
SHORTEST = 0.35
LONGEST = 1.6
# Gaps below this are inside one phrase, not between two.
JOIN_GAP = 0.18

_armed = False
_who = ""


@tools.tool(
    "Start teaching the speaker to recognise someone's voice saying the "
    "wake word. Use this when somebody asks you to learn their voice, or "
    "says you keep missing them, or asks to be trained on. After you call "
    "this, tell them in one short sentence to say the wake word over and "
    "over while the light is on, with a small pause between each, about ten "
    "times, and to stop when they have finished. Mention the light: it "
    "turns purple while it is listening and goes out when it has enough, "
    "which is the only way they can tell. The recording starts as soon as "
    "you finish speaking.",
    properties={
        "who": {
            "type": "string",
            "description": "Whose voice it is, if you know — 'tejas'. "
                           "Lower case, one word.",
        },
    },
    says="learn a new voice when you ask it to",
)
def learn_wake_word(who: str = "") -> str:
    """Arm the recording. main.py runs it once the answer has been spoken."""
    global _armed, _who
    _armed = True
    _who = "".join(c for c in who.strip().lower() if c.isalnum()) or "someone"
    return ("Ready. Tell them to say the wake word about ten times while "
            "the light is purple, with a pause between each, and that the "
            "light goes out when I have enough.")


def armed() -> bool:
    return _armed


def run(mic, waker, say) -> None:
    """Record repetitions, learn from them, and start using them. Now."""
    global _armed
    _armed = False
    try:
        _run(mic, waker, say)
    except Exception as error:
        print(f"[enroll] {type(error).__name__}: {error}")
        say("Something went wrong while I was learning that. Sorry.")


def _run(mic, waker, say) -> None:
    import lights
    import tts

    # Light first, then the beep. Opening the sound device on a Pi means
    # enumerating ALSA, which takes a moment, and the light going on is the
    # only signal the person has that anything is happening.
    lights.show("learning")
    tts.beep()
    print(f"Listening for repetitions from {_who}...")
    audio = listen(mic)
    lights.show("thinking")
    seconds = len(audio) / config.SAMPLE_RATE
    print(f"  recorded {seconds:.1f}s")

    if seconds < 1.0:
        say("I didn't hear anything at all. Ask me again when you're ready.")
        return

    segments = cut_up(audio)
    print(f"  found {len(segments)} candidate repetitions")
    if len(segments) < 3:
        say("I only caught a couple of those. Try again, a bit louder, "
            "with a clear pause between each one.")
        return

    vectors, kept = embed(waker, segments)
    # Dropping the odd one out is the point. Dropping most of them means
    # the room was noisy or somebody else was talking, and learning from
    # what survived would be learning from the wrong thing.
    if len(kept) < 3 or len(kept) * 2 < len(segments):
        say("Those didn't sound consistent enough to learn from. "
            "Try again in a quieter moment.")
        return
    print(f"  kept {len(kept)} that resemble each other")

    names = save(kept)
    for vector, window, name in zip(vectors, kept, names):
        wake_log.teach(vector, window, 1, f"enrolled:{_who}", source=name)

    say(f"Got {len(kept)}. Give me a moment to learn them.")
    extra = augment(waker, kept, quiet_parts(audio))
    print(f"  and {len(extra)} more by moving them about in the room noise")
    for vector in extra:
        wake_log.teach(vector, None, 1, f"enrolled:{_who}:moved")
    line = relearn()
    reload_into(waker)
    say(line)


def listen(mic) -> np.ndarray:
    """Record until they stop repeating themselves, or time runs out.

    Two things here were wrong the first time and are worth keeping written
    down, because both looked like the microphone being broken.

    The queue has to be thrown away first. Audio is only discarded while
    the speaker is talking, and beeping on a Pi means enumerating ALSA and
    opening the output device, which takes seconds — all of it recorded.
    So the first thing this saw was several seconds of stale room, which
    is silence, which it took for somebody who had finished.

    And the silence rule can only apply after they have started. It used to
    stop as soon as it held four seconds of anything and two and a half of
    quiet, so it gave up during the breath somebody takes after being told
    what to do. Now it waits WAIT_TO_START for a first word and only then
    starts looking for the end.
    """
    from audio_in import loudness

    mic.flush()
    floor = mic.noise_floor()
    chunk_seconds = config.BLOCK_SIZE / config.SAMPLE_RATE
    chunks: list[np.ndarray] = []
    quiet = 0.0
    started = False
    began = time.monotonic()

    while True:
        waited = time.monotonic() - began
        if started and waited > LISTEN_SECONDS + WAIT_TO_START:
            break
        if not started and waited > WAIT_TO_START:
            print("  nobody started")
            break

        chunk = mic.read(timeout=1.0)
        if chunk is None:
            continue

        loud = loudness(chunk) >= floor
        if loud:
            started = True
        if not started:
            continue  # Still waiting for a first word; don't keep the room.

        chunks.append(chunk)
        quiet = 0.0 if loud else quiet + chunk_seconds
        if quiet >= DONE_AFTER_QUIET \
                and len(chunks) * chunk_seconds >= LEAST_RECORDED:
            break

    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)


def cut_up(audio: np.ndarray) -> list[np.ndarray]:
    """Find each repetition, and return two seconds centred on it.

    Two seconds because that is the window the detector scores, and a
    phrase learned without the room around it isn't the thing it will be
    asked to recognise.

    The cutoff between speech and room comes from this recording and
    nothing else. It used to come from the microphone's rolling
    thirty-second noise floor, which is right for deciding when somebody
    has stopped talking and wrong here: that window had just been filled
    with somebody talking, so its median rose, and three times the median
    landed above the very speech it was meant to find. It reported zero
    repetitions in a recording that plainly had several.
    """
    from audio_in import loudness

    step = config.BLOCK_SIZE
    louds = np.array([loudness(audio[i:i + step])
                      for i in range(0, len(audio) - step, step)])
    if not louds.size:
        return []

    # Most of a recording of somebody repeating a short phrase is the gaps
    # between, so the quiet third of it is the room.
    floor = max(250.0, float(np.percentile(louds, 33)) * 3.0)
    speaking = louds >= floor
    runs, start = [], None
    for i, on in enumerate(speaking):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(speaking)))

    # Join runs split by the tiny gap inside "hey ... Claude".
    joined = []
    for run in runs:
        gap = (run[0] - joined[-1][1]) * step if joined else 0
        if joined and gap < JOIN_GAP * config.SAMPLE_RATE:
            joined[-1] = (joined[-1][0], run[1])
        else:
            joined.append(list(run))
    joined = [tuple(r) for r in joined]
    print(f"  cutoff {floor:.0f} (quiet {np.percentile(louds, 33):.0f}, "
          f"loudest {louds.max():.0f}); bursts of "
          + ", ".join(f"{(b - a) * step / config.SAMPLE_RATE:.2f}s"
                      for a, b in joined[:12]))

    window = int(2.0 * config.SAMPLE_RATE)
    out = []
    for first, last in joined:
        length = (last - first) * step / config.SAMPLE_RATE
        if not SHORTEST <= length <= LONGEST:
            continue
        middle = (first + last) * step // 2
        begin = max(0, middle - window // 2)
        piece = audio[begin:begin + window]
        if piece.size < window:
            piece = np.pad(piece, (0, window - piece.size))
        out.append(piece.astype(np.int16))
    return out


# How alike two repetitions have to look. Calibrated on eight real
# recordings with a burst of noise and an unrelated spoken sentence dropped
# in: the real ones scored 0.62 to 0.81 and the intruders 0.09 and 0.23.
ALIKE_ENOUGH = 0.55


def embed(waker, segments: list[np.ndarray]):
    """Turn each repetition into the 768 numbers, and drop the odd ones out.

    Ten goes at one phrase by one person cluster together. Whatever doesn't
    belong — a cough, a chair, a sibling shouting something else — sits
    away from the middle.

    The comparison is made on standardised vectors, and that matters: raw
    Whisper features share a large common component, so everything looks
    alike. A burst of noise scored 0.905 against the real ones' 0.965,
    which is not a gap you can cut on. Subtract the model's own mean and
    divide by its scale first — the same transform the classifier uses —
    and the same burst falls to 0.09 against 0.62 to 0.81.

    What this cannot do is use the wake score itself, tempting as that is.
    The repetitions worth learning from are exactly the ones the model
    currently misses; two of the seven in that test scored 0.01 and 0.42.
    Filtering on the score would keep only what already works.
    """
    if not hasattr(waker, "score"):
        return [], []

    vectors = []
    for segment in segments:
        waker.score(segment)
        vectors.append(np.array(waker.last_vector, dtype=np.float32))

    standardised = (np.array(vectors) - waker._mean) / waker._scale
    unit = standardised / (
        np.linalg.norm(standardised, axis=1, keepdims=True) + 1e-9)
    # The median direction, not the mean, so one bad segment can't drag the
    # centre towards itself and make everything else look like the outlier.
    middle = np.median(unit, axis=0)
    middle /= np.linalg.norm(middle) + 1e-9
    likeness = unit @ middle

    keep = likeness >= max(ALIKE_ENOUGH, float(np.median(likeness)) - 0.20)
    return ([v for v, k in zip(vectors, keep) if k],
            [s for s, k in zip(segments, keep) if k])


# How many places in the window to put each repetition. The trainer uses
# six, and matching it is the point — measured on a voice the model had
# never met, eight recordings added as they were took recall at 0.8 from
# 55% to 59%, and the same eight placed at six offsets took it to 64%.
OFFSETS = 6


def quiet_parts(audio: np.ndarray) -> np.ndarray:
    """The room in between the repetitions.

    The recording brings its own backgrounds, which is the neat part: the
    pauses somebody leaves between saying it are this room, this
    microphone, this evening. No need for a library of room noise on a
    machine that may not have one.
    """
    from audio_in import loudness

    step = config.BLOCK_SIZE
    louds = [loudness(audio[i:i + step])
             for i in range(0, len(audio) - step, step)]
    if not louds:
        return audio
    floor = max(250.0, float(np.percentile(louds, 33)) * 3.0)
    quiet = [audio[i * step:(i + 1) * step]
             for i, level in enumerate(louds) if level < floor]
    if len(quiet) * step < 2 * config.SAMPLE_RATE:
        return audio  # Barely any gaps — use the lot rather than nothing.
    return np.concatenate(quiet)


def augment(waker, segments: list[np.ndarray],
            background: np.ndarray) -> list[np.ndarray]:
    """Make more examples by sliding each repetition around the window.

    The detector sees a window that slides, so where in it the phrase
    happens to fall is arbitrary — and a model taught the phrase in the
    middle every time learns that too. This is what the trainer does with
    the recorded corpus, and doing the same here is worth about five points
    of recall over adding the clips as they came.
    """
    sys.path.insert(0, str(config.PROJECT_ROOT / "train"))
    try:
        from train_whisper_wake import place, trim
    except Exception as error:
        print(f"[enroll] not augmenting: {type(error).__name__}: {error}")
        return []

    window = int(2.0 * config.SAMPLE_RATE)
    rng = np.random.default_rng()
    out = []
    for segment in segments:
        phrase = trim(segment)
        if phrase.size < 0.2 * config.SAMPLE_RATE:
            continue
        for i in range(OFFSETS):
            spot = rng.integers(0, max(1, len(background) - window * 2))
            room = background[spot:spot + window * 2]
            if room.size < window * 2:
                room = np.pad(room, (0, window * 2 - room.size))
            waker.score(place(phrase, room, i / max(1, OFFSETS - 1), rng))
            out.append(np.array(waker.last_vector, dtype=np.float32))
    return out


def save(segments: list[np.ndarray]) -> list[str]:
    """Keep the audio, so a full retrain can use it later too."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    KEPT.mkdir(parents=True, exist_ok=True)
    names = []
    for i, segment in enumerate(segments):
        path = KEPT / f"{_who}-{stamp}-{i:02d}.wav"
        names.append(path.name)
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(config.SAMPLE_RATE)
            out.writeframes(segment.tobytes())
    print(f"  kept the audio in {KEPT}")
    return names


def relearn() -> str:
    """Refit the wake word on everything, including what just happened."""
    sys.path.insert(0, str(config.PROJECT_ROOT / "train"))
    try:
        import relearn as retrainer
    except Exception as error:
        return f"I saved those, but I couldn't retrain: {type(error).__name__}."
    return retrainer.refit(say=lambda line: print(f"  {line}"))


def reload_into(waker) -> None:
    """Pick the new weights up without reloading Whisper."""
    if hasattr(waker, "reload"):
        waker.reload()


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    import audio_in
    import tts
    import wake

    _who = sys.argv[1] if len(sys.argv) > 1 else "someone"
    waker = wake.make_waker()
    with audio_in.Microphone() as mic:
        mic.measure_noise_floor()
        print(f"Say the wake word about ten times, with pauses. ({_who})")
        _armed = True
        run(mic, waker, lambda line: (print(f"  -> {line}"), tts.speak(line)))
