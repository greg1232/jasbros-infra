"""Every time the wake word fires, write down what fired it.

The wake word is wrong about forty times an hour with a television on, and
each of those mistakes is a labelled training example that nobody had to
record. This keeps them.

What gets kept, per firing:

  the 768 numbers  the vector the detector scored. Free — the encoder pass
                   that produced it is the expensive part and has already
                   happened by the time we know it fired. 1.5 kB.
  the two seconds  the audio itself, so a person can listen, and so the
                   features can be recomputed if the encoder ever changes.
                   64 kB, so only the most recent are kept.
  what happened    what Whisper heard afterwards, and whether Claude
                   thought it was being spoken to. Written after the turn,
                   because that's when we know.

The last of those is what makes this worth doing. A firing followed by
silence is almost certainly a mistake; one followed by "what's the weather"
is almost certainly real. The speaker already works both of those out in
the course of answering, and they cost nothing to write down.

Near misses
-----------
Logging only what fires would teach the speaker nothing about recall, and
recall is the weaker half — held out entirely, it catches 52% of one
child's attempts. The times somebody said "hey Claude" and nothing
happened are exactly the examples that would fix that, and they never fire,
so they would never be logged.

So windows that score close to the line are kept in memory for a few
seconds, and when a firing does happen the ones just before it are written
down too. That is the "I had to say it three times" case, and the person
repeating themselves is what labels it: a near miss followed within seconds
by a real wake word was almost certainly the same person saying the same
thing, missed. A slower sample is kept as well, for the attempts that were
never followed by success because whoever it was gave up.

Nothing here is allowed to slow a turn down or break one. Every call is
wrapped, and a full disk costs you the log, not the speaker.

    python src/wake_log.py            what's been logged
    python src/wake_log.py --clear    throw it away
"""

import collections
import json
import sys
import threading
import time
from datetime import datetime

import numpy as np

import config

WHERE = config.PROJECT_ROOT / "state" / "wakes"
INDEX = WHERE / "wakes.jsonl"
VECTORS = WHERE / "vectors.f16"          # 768 float16 per firing, in order.
AUDIO = WHERE / "audio"

WIDTH = 768
_lock = threading.Lock()
_rows = 0

# Windows that scored close to the line but didn't cross it, with when they
# happened. Small: a handful of seconds of candidates, not a history.
_near: "collections.deque" = None
_last_sample = 0.0


def consider(waker, score: float) -> None:
    """Offer a window that didn't fire, in case it should have.

    Called for every window the detector scores below the threshold, so it
    has to be cheap: almost all of them are the room being the room, and
    are dropped on the first line.
    """
    global _near, _last_sample
    if not config.WAKE_LOG or score < config.WAKE_NEAR:
        return
    try:
        vector = getattr(waker, "last_vector", None)
        audio = getattr(waker, "last_audio", None)
        if vector is None or audio is None:
            return

        now = time.monotonic()
        if _near is None:
            _near = collections.deque(maxlen=8)
        # Copy: the detector's buffer is about to be written over.
        _near.append((now, float(score), vector.copy(), audio.copy()))

        # Most near misses are never followed by a firing, because the
        # person gave up or was never talking to us. Keep a slow trickle of
        # those too, or the log only ever sees the ones that ended well.
        if now - _last_sample >= config.WAKE_NEAR_EVERY:
            _last_sample = now
            # The vector, but not the audio. This fires every few minutes
            # all day, which is four hundred and eighty clips into a four
            # hundred clip budget — it was evicting every recording of the
            # wake word actually firing, which are the ones worth
            # listening to and the only ones that can ever be labelled
            # "yes" by a person. The vector is what training needs and
            # costs 1.5 kB; nobody was ever going to listen to these.
            _write(vector, None, score, near=True)
    except Exception as error:
        print(f"[wake log] {type(error).__name__}: {error}")


def flush_near() -> None:
    """Write down the one near miss just before a firing, and forget the rest.

    Somebody said it, nothing happened, they said it again and it worked —
    so the first attempt was the wake word and the detector missed it.
    Nobody had to label that; the repetition did.

    One, though. This used to keep every near miss from the previous
    fifteen seconds, up to eight of them, and call them all repetitions.
    In a room with a television on, that is one real wake word
    manufacturing eight positives out of the room being a room — and it
    produced 1,527 of the 2,436 labels in the log, sixty-three per cent of
    the training data from the single least trustworthy rule. The false
    wake rate doubled every time the model was fitted on it.

    Somebody saying a thing twice says it again within a couple of
    seconds, so that is all that is taken.
    """
    global _near
    if not config.WAKE_LOG or not _near:
        return
    try:
        now = time.monotonic()
        recent = [item for item in _near
                  if now - item[0] <= config.WAKE_REPEAT_SECONDS]
        _near.clear()
        if recent:
            _when, score, vector, audio = recent[-1]
            _write(vector, audio, score, near=True, repeated=True)
    except Exception as error:
        print(f"[wake log] {type(error).__name__}: {error}")


def note(waker, score: float) -> int:
    """Write down a firing. Returns its number, for outcome() later."""
    if not config.WAKE_LOG:
        return -1
    try:
        return _note(waker, score)
    except Exception as error:
        print(f"[wake log] {type(error).__name__}: {error}")
        return -1


def _note(waker, score: float) -> int:
    return _write(getattr(waker, "last_vector", None),
                  getattr(waker, "last_audio", None), score)


def _write(vector, audio, score: float, near: bool = False,
           repeated: bool = False) -> int:
    """Put one window in the log. Returns its number."""
    global _rows
    with _lock:
        AUDIO.mkdir(parents=True, exist_ok=True)
        _count()
        number = _rows

        # The vector file is a plain wall of float16, one row per firing, so
        # a row's number is its line in the index. No format, no library, and
        # loading the lot for retraining is one reshape.
        if vector is not None and vector.size == WIDTH:
            with open(VECTORS, "ab") as handle:
                handle.write(vector.astype(np.float16).tobytes())
        else:
            with open(VECTORS, "ab") as handle:
                handle.write(np.zeros(WIDTH, dtype=np.float16).tobytes())

        if audio is not None:
            _write_wav(AUDIO / f"{number:06d}.wav", audio)

        with open(INDEX, "a") as handle:
            row = {
                "n": number,
                "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "score": round(float(score), 4),
                "has_vector": vector is not None,
            }
            if near:
                row["near"] = True
                # Followed by a real firing within seconds, so somebody was
                # very likely repeating themselves.
                row["repeated"] = repeated
            handle.write(json.dumps(row) + "\n")
        _rows += 1

    _tidy()
    return number


def outcome(number: int, heard: str, answered: bool) -> None:
    """Say how the turn that firing started actually went.

    `heard` is what Whisper made of the question, empty if it heard nothing.
    `answered` is false when Claude decided nobody was talking to it.
    """
    if number < 0 or not config.WAKE_LOG:
        return
    try:
        with _lock, open(INDEX, "a") as handle:
            handle.write(json.dumps({
                "n": number, "heard": heard, "answered": answered}) + "\n")
    except Exception as error:
        print(f"[wake log] {type(error).__name__}: {error}")


def teach(vector, audio, label: int, why: str, source: str = "") -> None:
    """Add an example that was never a firing — somebody taught it directly.

    Enrolment goes through here rather than straight into the trainer, so
    it lands in the same pile as everything else and relearn.py needs to
    know nothing about where an example came from.
    """
    if not config.WAKE_LOG:
        return
    try:
        number = _write(vector, audio, 1.0 if label else 0.0)
        with _lock, open(INDEX, "a") as handle:
            row = {"n": number, "label": int(label), "why": why,
                   "taught": True,
                   "labelled_at": datetime.now().astimezone().isoformat(
                       timespec="seconds")}
            if source:
                # Which recording this came from, so that somebody
                # listening to that recording later can confirm it and
                # have the answer land on this row.
                row["source"] = source
            handle.write(json.dumps(row) + "\n")
    except Exception as error:
        print(f"[wake log] {type(error).__name__}: {error}")


def read() -> list[dict]:
    """Every firing, with its outcome folded in and its row number."""
    if not INDEX.exists():
        return []
    firings: dict[int, dict] = {}
    for line in INDEX.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # A half-written line from a power cut. Skip it.
        number = row.get("n")
        if number is None:
            continue
        firings.setdefault(number, {}).update(row)
    return [firings[n] for n in sorted(firings)]


def vectors() -> np.ndarray:
    """Every logged vector, one row each, lining up with read()."""
    if not VECTORS.exists():
        return np.zeros((0, WIDTH), dtype=np.float32)
    raw = np.fromfile(VECTORS, dtype=np.float16)
    return raw[:raw.size // WIDTH * WIDTH].reshape(-1, WIDTH).astype(np.float32)


# --- housekeeping -----------------------------------------------------------


def _count() -> None:
    """How many firings are already logged."""
    global _rows
    if _rows:
        return
    if VECTORS.exists():
        _rows = VECTORS.stat().st_size // (WIDTH * 2)


def _tidy() -> None:
    """Keep the audio from filling the card.

    Forty firings an hour at 64 kB each is 60 MB a day, and this runs on an
    SD card. The vectors are 1.5 kB and are what retraining actually needs,
    so those are kept; the audio is for listening to and for the day the
    encoder changes, and only the most recent is worth that.
    """
    try:
        clips = sorted(AUDIO.glob("*.wav"))
        for old in clips[:-config.WAKE_LOG_CLIPS]:
            old.unlink()
    except Exception:
        pass


def _write_wav(path, audio: np.ndarray) -> None:
    import wave

    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(config.SAMPLE_RATE)
        out.writeframes(np.asarray(audio, dtype=np.int16).tobytes())


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    if "--clear" in sys.argv:
        import shutil
        shutil.rmtree(WHERE, ignore_errors=True)
        print("Cleared.")
        raise SystemExit

    firings = read()
    if not firings:
        raise SystemExit(f"Nothing logged yet. Looked in {WHERE}")

    near = [f for f in firings if f.get("near")]
    firings = [f for f in firings if not f.get("near")]
    answered = sum(1 for f in firings if f.get("answered"))
    silent = sum(1 for f in firings if f.get("heard") == "")
    print(f"{len(firings)} firings logged in {WHERE}")
    print(f"  {answered} led to an answer")
    print(f"  {silent} were followed by nobody saying anything")
    print(f"  {len(firings) - answered - silent} something was said, "
          "but not to the speaker")
    print(f"  {len(list(AUDIO.glob('*.wav')))} still have their audio")
    repeated = sum(1 for f in near if f.get("repeated"))
    print(f"{len(near)} near misses, {repeated} of them just before a real "
          "wake word\n")
    for firing in firings[-15:]:
        mark = "OK " if firing.get("answered") else "no "
        print(f"  {mark} {firing['at'][11:19]} {firing['score']:.3f} "
              f"{firing.get('heard', '')[:60]!r}")
