"""Record what your room sounds like when nobody is saying the wake word.

The negative half of record_wake.py, and the more important one. A wake
word only ever hears two things: someone saying it, and everything else.
"Everything else" is almost entirely this — the room, sitting there.

It has to come from the real microphone. Measured here, a quiet room
through a MacBook is 1-50 RMS; the same silence through a reSpeaker
XVF3800 averages 547 RMS and peaks near 18,000, because the array's
automatic gain hauls the noise floor up with everything else. A model
trained on the first has no idea what the second is, and fires on it:
0.53 false wakes an hour on borrowed validation audio, about 180 an hour
on the actual device.

    python train/record_room.py --minutes 10

Leave it running and go about your business — or leave the room. Both are
useful. What you must not do is say the wake word.

Clips land in train/room/ as WAV files the length the trainer wants, ready
to be added to the negatives.
"""

import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

DEFAULT_OUT = HERE / "room"
# Long enough to hold the wake word, which is what the model compares
# everything against.
CLIP_SECONDS = 1.5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--label", default="room",
                        help="filename prefix; try 'kitchen', 'evening', 'tv'")
    args = parser.parse_args()

    import audio_in
    import config

    args.out.mkdir(parents=True, exist_ok=True)
    existing = len(list(args.out.glob(f"{args.label}-*.wav")))
    per_clip = int(CLIP_SECONDS * config.SAMPLE_RATE)
    total = args.minutes * 60

    print(f"\nRecording {args.minutes:g} minutes of room sound.")
    print("Do not say the wake word. Anything else is fine and useful —")
    print("talking, cooking, music, the room being empty.\n")

    saved, buffer, levels = 0, [], []
    started = time.monotonic()
    with audio_in.Microphone() as mic:
        held = np.zeros(0, dtype=np.int16)
        while time.monotonic() - started < total:
            chunk = mic.read(timeout=1.0)
            if chunk is None:
                continue
            levels.append(audio_in.loudness(chunk))
            held = np.concatenate([held, chunk])

            while len(held) >= per_clip:
                clip, held = held[:per_clip], held[per_clip:]
                path = args.out / f"{args.label}-{existing + saved + 1:05d}.wav"
                with wave.open(str(path), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(config.SAMPLE_RATE)
                    w.writeframes(clip.tobytes())
                saved += 1

            elapsed = time.monotonic() - started
            if saved and saved % 20 == 0 and len(held) < len(chunk) * 2:
                print(f"  {elapsed:5.0f}s / {total:.0f}s   {saved} clips   "
                      f"level {np.mean(levels[-200:]):.0f} RMS", end="\r",
                      flush=True)

    room = np.array(levels) if levels else np.zeros(1)
    print(f"\n\n  saved {saved} clips to {args.out}")
    print(f"  room level: {room.mean():.0f} RMS average, {room.max():.0f} peak")
    print("\n  Copy them to the machine you train on, and retrain:")
    print("      python train/train_whisper_wake.py")
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
