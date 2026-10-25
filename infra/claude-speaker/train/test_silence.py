"""Check the wake word against a quiet room. Run this before trusting one.

This is the test that matters most for a speaker on a shelf, and the
easiest to forget. A model can score beautifully on every phrase you throw
at it and still wake hundreds of times an hour on an empty room — because
"nobody is talking" is a kind of audio, and if it wasn't in the training
data the model's behaviour there is undefined.

That happened here twice. An early model scored 0.99 on ordinary room noise
and fired about 4,000 times an hour. A later one measured 0.53 false wakes
an hour on borrowed validation audio and produced about 180 on the real
microphone — wrong by a factor of 340, because that audio came from other
rooms and other microphones.

    python train/test_silence.py                    # whatever .env is using
    python train/test_silence.py --seconds 600      # a number you can trust

Stay quiet while it runs, or don't — running it with the television on is
the more useful test. Just don't say the wake word.

Three minutes can only resolve about 20 false wakes an hour. If you want to
know whether it's 5 an hour or 30, you have to listen for longer.
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument("--threshold", type=float, default=None,
                        help="defaults to WAKE_THRESHOLD from .env")
    args = parser.parse_args()

    import audio_in
    import config
    import wake

    threshold = args.threshold if args.threshold is not None else config.WAKE_THRESHOLD
    waker = wake.make_waker()
    if not hasattr(waker, "observe"):
        raise SystemExit("WAKE_MODE=key has nothing to measure.")

    print(f"\nListening for {args.seconds:.0f}s — {waker.label}, "
          f"firing at {threshold}.")
    print("Don't say the wake word. Anything else is fair game.\n")

    scores, levels = [], []
    with audio_in.Microphone() as mic:
        waker.reset()
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            chunk = mic.read(timeout=1.0)
            if chunk is None:
                continue
            levels.append(audio_in.loudness(chunk))
            score = waker.observe(chunk)
            if score is not None:
                scores.append(score)

    if not scores:
        print("Nothing was heard at all. Check the microphone.")
        return 1

    scores = np.array(scores)
    room = np.array(levels)
    wakes = int((scores >= threshold).sum())
    per_hour = wakes / (args.seconds / 3600)
    # One wake is the smallest thing this run could have seen, so it's also
    # the finest rate it can distinguish from zero.
    resolution = 1 / (args.seconds / 3600)

    print(f"  room level   : {room.mean():.0f} RMS average, {room.max():.0f} peak")
    print(f"  score        : {scores.mean():.4f} average, {scores.max():.4f} worst")
    print(f"  over {threshold:<8} : {wakes} of {len(scores)} looks")
    print(f"\n  {per_hour:.0f} false wakes an hour "
          f"(this run can't tell apart anything under {resolution:.0f})")

    if wakes:
        print("\nFAIL. Record more of this room and retrain:")
        print("    python train/record_room.py --minutes 30")
        print("    python train/train_whisper_wake.py")
        print("Raising WAKE_THRESHOLD hides this rather than fixing it.")
        return 1
    print("\nPASS — though see the resolution above before trusting it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
