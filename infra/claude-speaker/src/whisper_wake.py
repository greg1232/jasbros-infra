"""Wake word built on Whisper's encoder.

The alternative to openWakeWord, and on this data a much better one.

openWakeWord runs a mel filterbank into a frozen 0.33M parameter CNN from
2020, and you train a small head on its output. Measured on the Pi that CNN
is 91% of the compute, the head is 1%, and the head is the only part that
can be trained. On real recordings of real people in a real room it reached
59% recall at 127 false wakes an hour, and nothing done to the head moved
it — the features underneath simply don't separate the classes.

Whisper's tiny encoder does. Same recordings, a logistic regression on its
output: about 90% recall with no measurable false wakes, tested on people
it had never heard. It costs 131 ms per two second window on a Pi 4 against
openWakeWord's 13 ms per 80 ms chunk — 0.26x realtime against 0.16x, which
the Pi can afford.

Train one with train/train_whisper_wake.py, then set in .env:

    WAKE_MODEL=hey_claude_whisper.npz
"""

import time
from pathlib import Path

import numpy as np

import config
import wake_log




class WhisperWakeDetector:
    """Wake up when the wake word is heard, using Whisper's encoder."""

    def __init__(self, model_path: Path) -> None:
        self._path = model_path
        weights = self._read()
        self._keep = int(weights["keep_frames"])
        self._window = float(weights["window_seconds"])
        whisper_model = str(weights["whisper_model"])

        print(f"Loading the wake word from {model_path.name} "
              f"(whisper {whisper_model})...")
        from faster_whisper import WhisperModel

        # This is a second copy of the same model stt.py loads, and that is
        # deliberate. Sharing one would save 56 MB and 0.8 seconds of
        # startup, and cost far more than it saves, because the two uses
        # want opposite settings. Measured on a Pi 4:
        #
        #   two models, as now      0.33 cores idle, 1.72s a question
        #   shared at four threads  0.85 cores idle, 1.72s a question
        #   shared at one thread    0.33 cores idle, 4.38s a question
        #
        # One thread here: the wake word runs forever and should be frugal,
        # leaving the other cores for transcription and the voice, which
        # need them in bursts. Four threads would take a window from 131 ms
        # to 85 ms while spending 2.6 times the CPU to do it.
        self._whisper = WhisperModel(whisper_model, device="cpu",
                                     compute_type="int8", cpu_threads=1)
        self._features = self._whisper.feature_extractor
        self._samples = int(self._window * config.SAMPLE_RATE)
        self._buffer = np.zeros(self._samples, dtype=np.int16)
        self._next_look = 0.0
        # The last window scored, and the 768 numbers it was scored on.
        # Kept because whatever fires the wake word is exactly the example
        # worth learning from, and by the time it fires the expensive part
        # is already paid for — an encoder pass is 159 ms on a Pi, and this
        # is that pass's output. See wake_log.py.
        self.last_vector: np.ndarray | None = None
        self.last_audio: np.ndarray | None = None
        self.last_score = 0.0

        # How sure it has to be. The model carries the operating point the
        # sweep chose for it, because a threshold is not comparable between
        # two models — they sit on different score distributions, and the
        # same number means different things. A WAKE_THRESHOLD in .env
        # overrides it, for when you want to argue with the sweep.
        self.threshold = config.WAKE_THRESHOLD
        carried = float(weights["threshold"]) if "threshold" in weights.files \
            else 0.0
        if carried and not config.WAKE_THRESHOLD_SET:
            self.threshold = carried

        phrase = model_path.stem.split("_whisper")[0].split("-")[0]
        self.label = f"say '{phrase.replace('_', ' ')}'"

        # Models fitted by train/relearn.py carry the commit of the data
        # they were fitted on. Saying so at startup is the difference
        # between "the wake word got worse last week" being answerable and
        # not.
        came_from = weights.files
        if "dataset" in came_from and str(weights["dataset"]):
            print(f"  fitted {str(weights.get('fitted_at', ''))[:16]} on "
                  f"{int(weights.get('examples', 0))} logged examples, "
                  f"dataset {str(weights['dataset'])[:8]}")
        print(f"  wakes at {self.threshold:.3f}"
              + ("" if config.WAKE_THRESHOLD_SET else " (chosen by the sweep)"))

    def _read(self):
        weights = np.load(self._path)
        self._mean = weights["mean"]
        self._scale = weights["scale"]
        self._coef = weights["coef"]
        self._intercept = float(weights["intercept"])
        return weights

    def reload(self) -> None:
        """Pick up a newly trained head, without reloading Whisper.

        The encoder is 56 MB and several seconds to load, and retraining
        never touches it — only the four small arrays in front of it change.
        So learning a voice can take effect while the person is still
        standing there, instead of at the next restart.
        """
        self._read()
        print(f"  reloaded {self._path.name}")

    def score(self, audio: np.ndarray) -> float:
        """How much the last two seconds sound like the wake word."""
        mel = self._features(audio.astype(np.float32) / 32768.0)
        mel = mel[..., :self._features.nb_max_frames]
        encoded = np.array(self._whisper.encode(mel))[0][:self._keep]
        vector = np.concatenate([encoded.mean(0), encoded.max(0)])
        standardised = (vector - self._mean) / self._scale
        self.last_vector = vector
        # A copy, not the buffer itself. reset() zeroes the rolling
        # buffer in place, and last_audio pointed straight at it — so
        # every firing's recording was overwritten with silence a moment
        # after it was taken. A hundred and twenty four of four hundred
        # saved clips were digital silence, and they were the firings,
        # which are the ones worth listening to.
        self.last_audio = np.asarray(audio).copy()
        self.last_score = float(1.0 / (1.0 + np.exp(
            -(standardised @ self._coef + self._intercept))))
        return self.last_score

    def push(self, chunk: np.ndarray) -> None:
        """Add audio to the rolling window, oldest falling off the front."""
        self._buffer = np.concatenate([self._buffer, chunk])[-self._samples:]

    def observe(self, chunk: np.ndarray) -> float | None:
        """Feed 80 ms of audio. Returns a score, or None if it isn't due.

        The two wake words disagree about how often they can answer —
        openWakeWord scores every chunk, this one every stride, because a
        Whisper encoder pass is 131 ms and a chunk is 80 ms. Both answer the
        same question, so the tools that measure them don't have to care.
        """
        self.push(chunk)
        now = time.monotonic()
        if now < self._next_look:
            return None
        self._next_look = now + config.WAKE_STRIDE_SECONDS
        return self.score(self._buffer)

    def reset(self) -> None:
        self._buffer[:] = 0
        self._next_look = time.monotonic() + config.WAKE_STRIDE_SECONDS

    def wait_for_wake(self, mic) -> bool:
        """Block until the wake word is heard. Returns False on Ctrl-C."""
        self.reset()
        mic.flush()

        while True:
            chunk = mic.read(timeout=1.0)
            if chunk is None:
                continue
            score = self.observe(chunk)
            if score is not None and score < self.threshold:
                # Close, but not close enough. Might have been somebody
                # saying it — see wake_log.consider().
                wake_log.consider(self, score)
            if score is not None and score >= self.threshold:
                # Hold on to what fired before reset() wipes the buffer.
                fired_on = (self.last_vector, self.last_audio, score)
                self.reset()
                self.last_vector, self.last_audio, self.last_score = fired_on
                return True


def find_model(name: str) -> Path | None:
    """Find a Whisper wake word file, or None if this isn't one."""
    if not name.endswith(".npz"):
        return None
    # state/ first. That is where relearn.py puts what this machine has
    # learned from its own room, and deploy mirrors the project directory —
    # so a model left in models/ would be overwritten by the shipped one on
    # the next deploy, silently throwing away everything it had learned.
    for path in (config.PROJECT_ROOT / "state" / Path(name).name,
                 Path(name), config.PROJECT_ROOT / name,
                 config.PROJECT_ROOT / "models" / name):
        if path.is_file():
            return path.resolve()
    raise SystemExit(
        f"Can't find the wake word model {name!r}.\n"
        "Train one with:  python train/train_whisper_wake.py")


if __name__ == "__main__":
    import sys

    print(__doc__)
    if not {"-h", "--help"} & set(sys.argv):
        print("To hear how it scores your voice:\n"
              "    python train/test_wake.py models/hey_claude_whisper.npz")
