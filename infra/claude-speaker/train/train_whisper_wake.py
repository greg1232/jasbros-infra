"""Train a wake word on Whisper's encoder features.

openWakeWord's stack is a mel filterbank, a frozen 0.33M parameter CNN from
2020, and a small head you train. Measured on this Pi, that CNN is 91% of
the compute and the head is 1% — and the head is the only part anyone
tunes. On this data the whole thing tops out around 59% recall at 127 false
wakes an hour, and no amount of tuning the head moved it, because the
features it sits on can't separate the classes.

Whisper's encoder can. Same recordings, a plain logistic regression on
tiny.en's encoder output: about 90% recall at no measurable false wakes,
leave-one-person-out. It costs 131 ms per 2 second window on the Pi against
openWakeWord's 13 ms per 80 ms chunk — 0.26x realtime against 0.16x, which
is affordable.

    python train/train_whisper_wake.py

Writes models/hey_claude_whisper.npz: the standardiser and the weights, so
the speaker needs numpy and Whisper but not scikit-learn.
"""

import argparse
import glob
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

REAL = HERE / "real" / "hey_claude"
ROOM = HERE / "room"
CLIPS = HERE / "hey_claude"
RATE = 16_000

# The window the classifier sees. Two seconds comfortably holds the phrase
# and some room either side.
WINDOW_SECONDS = 2.0
# Whisper's encoder emits 50 frames a second; the rest of its 1500 are the
# 30 second pad it insists on and carry nothing.
KEEP_FRAMES = int(WINDOW_SECONDS * 50)


def load(path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def trim(audio: np.ndarray) -> np.ndarray:
    """Cut to the speech, so it can be placed deliberately in a window."""
    win = 160
    frames = audio[:len(audio) // win * win].reshape(-1, win)
    if not frames.size:
        return audio
    energy = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))
    loud = np.where(energy > max(energy.max() * 0.10, 60))[0]
    if not len(loud):
        return audio
    return audio[loud[0] * win:(loud[-1] + 1) * win]


class Encoder:
    """Whisper's encoder, as a fixed feature extractor."""

    def __init__(self, size: str = "tiny.en", threads: int = 4):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(size, device="cpu", compute_type="int8",
                                  cpu_threads=threads)
        self.features = self.model.feature_extractor

    def __call__(self, audio: np.ndarray) -> np.ndarray:
        """One vector for one window of int16 audio."""
        want = int(WINDOW_SECONDS * RATE)
        audio = np.pad(audio, (max(0, want - len(audio)), 0))[-want:]
        mel = self.features(audio.astype(np.float32) / 32768.0)
        mel = mel[..., :self.features.nb_max_frames]
        out = np.array(self.model.encode(mel))[0][:KEEP_FRAMES]
        # Mean and max over time. A phrase happens once inside the window,
        # so its peak matters as much as the average.
        return np.concatenate([out.mean(0), out.max(0)]).astype(np.float32)


def place(phrase: np.ndarray, background: np.ndarray, offset: float,
          rng) -> np.ndarray:
    """Drop a phrase into a window of room sound at a given offset.

    Training on windows where the phrase is always at the end would teach
    it that position. In use the window slides, so the phrase turns up
    anywhere in it — and if the model has only seen one alignment it misses
    the others.
    """
    want = int(WINDOW_SECONDS * RATE)
    window = background[:want].astype(np.float32).copy()
    if len(window) < want:
        window = np.pad(window, (0, want - len(window)))
    start = int(np.clip(offset, 0.0, 1.0) * max(1, want - len(phrase)))
    end = min(want, start + len(phrase))
    # Real speech is louder than the room it happens in, but not always by
    # the same amount.
    window[start:end] += phrase[:end - start] * rng.uniform(0.7, 1.3)
    return np.clip(window, -32768, 32767).astype(np.int16)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offsets", type=int, default=6,
                        help="window positions to place each phrase at")
    parser.add_argument("--model", default="tiny.en")
    parser.add_argument("--out", type=Path,
                        default=HERE.parent / "models" / "hey_claude_whisper.npz")
    parser.add_argument("--holdout", default="tejas",
                        help="a person to leave out entirely, to test on")
    args = parser.parse_args()

    positives = sorted(REAL.glob("*.wav"))
    if not positives:
        raise SystemExit(f"No recordings in {REAL}. Run train/record_wake.py.")
    room_clips = sorted(ROOM.glob("*.wav"))
    if not room_clips:
        raise SystemExit(f"No room audio in {ROOM}. Run train/record_room.py.")
    speech = [p for p in sorted((CLIPS / "negative_train").glob("*.wav"))
              if not p.name.startswith("room-")]

    rng = np.random.default_rng(0)
    encode = Encoder(args.model)
    room_audio = [load(p) for p in room_clips]

    def background():
        a, b = rng.integers(0, len(room_audio), 2)
        return np.concatenate([room_audio[a], room_audio[b]])

    print(f"  {len(positives)} recordings x {args.offsets} positions, "
          f"{len(room_clips)} room clips, {len(speech)} spoken negatives")

    X, y, who = [], [], []
    for path in positives:
        phrase = trim(load(path))
        person = path.name.split("-")[0]
        for i in range(args.offsets):
            X.append(encode(place(phrase, background(),
                                  i / max(1, args.offsets - 1), rng)))
            y.append(1)
            who.append(person)
    print(f"  positives embedded: {len(X)}")

    # The room, as it is — this is what it listens to all day. Windows are
    # cut from the continuous recording rather than from clips one at a
    # time, because that's what the detector sees: it slides a window over
    # unbroken audio, and scoring tidy isolated clips flatters it badly.
    # Measured: 0% false wakes clip by clip, 391 an hour streamed.
    continuous = np.concatenate([load(p) for p in room_clips])
    window = int(WINDOW_SECONDS * RATE)
    step = int(0.4 * RATE)
    room_windows = [continuous[i:i + window]
                    for i in range(0, len(continuous) - window, step)]
    rng.shuffle(room_windows)
    for w in room_windows[:900]:
        X.append(encode(w))
        y.append(0)
        who.append("room")

    # And speech that isn't the wake word, which is the negative that
    # actually matters: without it the model learns "somebody is talking"
    # and fires on every sentence in the house.
    rng.shuffle(speech)
    for path in speech[:900]:
        X.append(encode(place(trim(load(path)), background(),
                              rng.random(), rng)))
        y.append(0)
        who.append("speech")
    print(f"  negatives embedded: {y.count(0)}")

    X = np.array(X, dtype=np.float32)
    y = np.array(y)
    who = np.array(who)

    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    # Hard negative mining. Most room audio is obviously not the wake word
    # and teaches nothing; the few windows that score high are the entire
    # problem. Fit once, find those, and put them back in.
    for round_number in (1, 2):
        scaler = StandardScaler().fit(X)
        rough = LogisticRegression(max_iter=5000, C=0.1,
                                   class_weight="balanced")
        rough.fit(scaler.transform(X), y)
        scores = []
        for w in room_windows[900:2400]:
            v = encode(w)
            scores.append((float(rough.predict_proba(
                scaler.transform(v[None]))[0, 1]), v))
        scores.sort(key=lambda s: -s[0])
        hard = [v for score, v in scores[:250] if score > 0.05]
        if not hard:
            break
        print(f"  round {round_number}: adding {len(hard)} hard room windows "
              f"(worst scored {scores[0][0]:.3f})")
        X = np.vstack([X, np.array(hard, dtype=np.float32)])
        y = np.r_[y, np.zeros(len(hard))]
        who = np.r_[who, np.array(["room"] * len(hard))]

    # Keep the features, not just the model. They cost 159 ms each on a Pi
    # and about twenty minutes altogether, and they are what lets the
    # speaker relearn from its own mistakes in a second — see
    # train/relearn.py. float16 halves the file and loses nothing that
    # survives standardisation anyway.
    bank = args.out.with_suffix(".bank.npz")
    np.savez_compressed(bank, X=X.astype(np.float16), y=y, who=who)
    print(f"  bank of {len(X)} features -> {bank.name} "
          f"({bank.stat().st_size / 1e6:.1f} MB)")

    # Hold one person out completely, so the reported number is for a voice
    # the model has never met.
    test = who == args.holdout
    train = ~test
    scaler = StandardScaler().fit(X[train])
    clf = LogisticRegression(max_iter=5000, C=0.1, class_weight="balanced")
    clf.fit(scaler.transform(X[train]), y[train])

    def rate(mask, threshold=0.9):
        if not mask.any():
            return float("nan")
        p = clf.predict_proba(scaler.transform(X[mask]))[:, 1]
        return float((p >= threshold).mean())

    print(f"\n  held out '{args.holdout}' entirely:")
    print(f"    recall on {args.holdout}:      {rate(test & (y == 1)):.0%}")
    print(f"    room false wakes:      {rate(who == 'room'):.1%} of windows")
    print(f"    other speech:          {rate(who == 'speech'):.1%} of windows")

    # Ship a model trained on everyone, now that it's been measured.
    scaler = StandardScaler().fit(X)
    clf = LogisticRegression(max_iter=5000, C=0.1,
                             class_weight="balanced").fit(scaler.transform(X), y)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.out,
             mean=scaler.mean_.astype(np.float32),
             scale=scaler.scale_.astype(np.float32),
             coef=clf.coef_[0].astype(np.float32),
             intercept=np.float32(clf.intercept_[0]),
             whisper_model=args.model,
             window_seconds=np.float32(WINDOW_SECONDS),
             keep_frames=np.int32(KEEP_FRAMES))
    print(f"\n  wrote {args.out} ({args.out.stat().st_size / 1024:.0f} kB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
