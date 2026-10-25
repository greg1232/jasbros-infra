"""Record yourself saying the wake word, through the microphone you use.

Two jobs. The first is diagnosis: a model that scores 0.99 in training can
score 0.001 through a real microphone in a real room, and the recording is
what tells you why. The second is retraining — a wake word built only from
synthetic voices has never heard the person it's meant to answer, and a
hundred real clips are worth more than a thousand generated ones.

    python train/record_wake.py --speaker greg,ojas,tejas,ana --times 20

Press Enter to start each clip, say the wake word, press Enter to stop.
It goes through the people you name one at a time. Clips land in
train/real/ as WAV files, 16 kHz mono, exactly what the speaker hears.

Record everyone who'll actually talk to it, and especially the children —
a child's voice differs from an adult's in pitch and pace far more than two
adults differ from each other, and a model that has only heard grown-ups
answers grown-ups.

Record on the machine the speaker lives on, through its own microphone. The
whole point is to capture what that path does to your voice — its gain, its
noise suppression, its room.
"""

import argparse
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DEFAULT_OUT = Path(__file__).resolve().parent / "real"

# A forgotten second Enter shouldn't fill the disk.
MAX_SECONDS = 30.0
# Below this it's a silent room, not somebody talking.
QUIET_RMS = 150.0


def record_until_enter(mic, prompt: str) -> np.ndarray:
    """Collect audio until the person presses Enter again."""
    chunks: list[np.ndarray] = []
    done = threading.Event()

    def collect() -> None:
        started = time.monotonic()
        while not done.is_set() and time.monotonic() - started < MAX_SECONDS:
            chunk = mic.read(timeout=0.2)
            if chunk is not None:
                chunks.append(chunk)

    mic.flush()
    listener = threading.Thread(target=collect, daemon=True)
    listener.start()
    try:
        input(prompt)
    finally:
        done.set()
        listener.join()

    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--times", type=int, default=20)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--label", default="hey_claude",
                        help="what you're saying; also the filename prefix")
    parser.add_argument("--speaker", default="",
                        help="who's talking — one name, or several separated "
                             "by commas to go round everyone in turn")
    args = parser.parse_args()

    import audio_in
    import config

    out = args.out / args.label
    out.mkdir(parents=True, exist_ok=True)
    phrase = args.label.replace("_", " ")
    people = [name.strip() for name in args.speaker.split(",") if name.strip()]
    if not people:
        people = [""]  # Nobody named: just record, unlabelled.

    print(f'\nSay "{phrase}" {args.times} times each: '
          f'{", ".join(p or "you" for p in people)}.')
    print("Enter to start, say it, Enter again to stop.")
    print("Vary them — closer, further, quieter, faster, a couple half\n"
          "mumbled from across the room. Twenty careful identical clips\n"
          "would teach it to recognise one performance.")
    print("\nType q at any prompt to skip to the next person.")

    tally = {}
    with audio_in.Microphone() as mic:
        for person in people:
            tally[person] = record_person(
                mic, person, phrase, out, args.label, args.times, config)

    print()
    total = 0
    for person, count in tally.items():
        print(f"  {person or 'unnamed'}: {count} clip"
              f"{'s' if count != 1 else ''}")
        total += count
    print(f"  {total} in total, in {out}")
    return 0 if total else 1


def record_person(mic, person, phrase, out, label, times, config) -> int:
    """Take `times` clips from one person. Returns how many were kept."""
    who = f"{person}-" if person else ""
    # Carry on from any earlier session rather than overwriting it.
    existing = len(list(out.glob(f"{who}{label}-*.wav")))

    print()
    print(f"  --- {person or 'your'} turn ---"
          + (f"  ({existing} already recorded)" if existing else ""))
    try:
        if input("      Enter when ready ").strip().lower() == "q":
            return 0
    except (EOFError, KeyboardInterrupt):
        return 0

    kept = 0
    while kept < times:
        try:
            start = input(f"  {kept + 1:2d}/{times}  Enter to start ")
        except (EOFError, KeyboardInterrupt):
            break
        if start.strip().lower() == "q":
            break

        audio = record_until_enter(
            mic, f'{" " * 12}say "{phrase}" — Enter to stop ')

        if audio.size == 0:
            print("            nothing recorded — try again")
            continue

        seconds = audio.size / config.SAMPLE_RATE
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
        peak = int(np.abs(audio).max())

        if rms < QUIET_RMS:
            print(f"            too quiet (rms {rms:.0f}) — try again")
            continue

        path = out / f"{who}{label}-{existing + kept + 1:03d}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(config.SAMPLE_RATE)
            w.writeframes(audio.astype(np.int16).tobytes())
        kept += 1
        print(f"            saved {path.name}  "
              f"{seconds:.1f}s  rms {rms:.0f}  peak {peak}")

    return kept


if __name__ == "__main__":
    raise SystemExit(main())
