"""Microphone — listens and records.

One microphone stream stays open the whole time the program is running.
Audio arrives in small chunks and gets dropped into a queue. Both the wake
word detector and the question recorder read from that same queue, so the
device only has to be opened once.

While the speaker is talking, incoming audio is thrown away — otherwise it
hears its own voice and wakes itself up.
"""

import collections
import queue
import time

import numpy as np
import sounddevice as sd

import sys

import config
import tts


# The microphone currently open, so the dashboard can say whether the
# speaker can still hear. There is only ever one — the array allows a
# single stream.
LATEST: "Microphone | None" = None


class Microphone:
    """An open microphone you can read chunks of audio from.

    Use it with `with`, so the device always gets closed again:

        with Microphone() as mic:
            chunk = mic.read()
    """

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._stream: sd.InputStream | None = None
        # When the device last handed us anything at all. The array stops
        # sending audio if its playback half goes quiet, and reports
        # nothing when it does — see docs/troubleshooting.md. A microphone
        # that returns nothing looks exactly like a quiet room, so the only
        # way to tell them apart is the clock.
        self._last_audio = time.monotonic()
        self._last_nudge = 0.0
        self.deaf_events = 0
        # How loud the room has been lately. Kept as a rolling window so the
        # cutoff between speech and silence can follow the room instead of
        # being fixed at whatever it happened to be when the program
        # started. About thirty seconds of history.
        self._recent: collections.deque = collections.deque(maxlen=375)

    def __enter__(self) -> "Microphone":
        global LATEST
        LATEST = self
        device = find_device(config.INPUT_DEVICE)
        name = sd.query_devices(device)["name"] if device is not None else "default"
        print(f"Microphone: {name}")

        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            blocksize=config.BLOCK_SIZE,
            channels=1,
            dtype="int16",
            device=device,
            callback=self._on_audio,
        )
        self._stream.start()
        self._last_audio = time.monotonic()
        return self

    def __exit__(self, *exc) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _on_audio(self, indata, frames, time_info, status) -> None:
        """Called by sounddevice every time a new chunk of audio arrives."""
        # This firing at all is the proof the array is still delivering, so
        # note it before anything else — including the line below, which
        # throws the audio away while we are talking. Otherwise every long
        # answer would look like a dead microphone.
        self._last_audio = time.monotonic()
        # Ignore everything that comes in while we're speaking, so the
        # speaker never hears itself.
        if tts.speaking.is_set():
            return
        chunk = indata[:, 0].copy()
        self._recent.append(loudness(chunk))
        self._queue.put(chunk)

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        """Get the next chunk of audio, or None if nothing arrived in time."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            self._check_still_hearing()
            return None

    def silent_for(self) -> float:
        """Seconds since the device last handed us anything at all.

        Not the same as a quiet room: a quiet room still delivers chunks of
        near-silence eighty milliseconds apart. This only grows when the
        hardware has stopped talking to us.
        """
        return time.monotonic() - self._last_audio

    def _check_still_hearing(self) -> None:
        """Say so if the array has stopped, and try to start it again.

        The speaker went deaf for eleven minutes once and nothing anywhere
        said a word — not ALSA, which went on reporting the stream RUNNING
        with its hardware pointer frozen, not PipeWire, not the log. The
        cure is in deploy.py and it is a WirePlumber rule. This is the net
        underneath it, because the failure is silent and the next cause
        might not be the one that has been fixed.

        Reopening the input does nothing; that was measured, and so was
        restarting the whole audio stack. Driving the playback half is the
        only thing that brings it back.
        """
        quiet = self.silent_for()
        if quiet < config.MIC_DEAF_SECONDS:
            return
        now = time.monotonic()
        if now - self._last_nudge < config.MIC_DEAF_SECONDS:
            return          # Already tried very recently; give it a chance.
        self._last_nudge = now
        self.deaf_events += 1
        print(f"[microphone] nothing from the array in {quiet:.0f}s — "
              f"waking it with a moment of silence (time {self.deaf_events})")
        try:
            tts.nudge()
        except Exception as error:
            print(f"[microphone] couldn't wake it "
                  f"({type(error).__name__}: {error})")

    def flush(self, keep_seconds: float = 0.0) -> list[np.ndarray]:
        """Throw away audio waiting in the queue.

        `keep_seconds` holds back the most recent audio instead of dropping
        it, and returns it oldest-first. That matters right after the wake
        word: someone who runs straight into their question — "hey claude
        what's the weather" — has already started talking by the time
        recording begins, and without this their first word is gone.
        """
        waiting: list[np.ndarray] = []
        while True:
            try:
                waiting.append(self._queue.get_nowait())
            except queue.Empty:
                break

        if keep_seconds <= 0:
            return []
        chunk_seconds = config.BLOCK_SIZE / config.SAMPLE_RATE
        keep = int(keep_seconds / chunk_seconds)
        return waiting[-keep:] if keep else []

    def noise_floor(self) -> float:
        """How loud "silence" is right now, from the last half minute.

        This has to keep up with the room. Measured once at startup, a
        speaker that was quiet at breakfast is still using the breakfast
        threshold when the television goes on — and then nothing is ever
        below it, so recording runs until MAX_RECORD_SECONDS and hoovers up
        whatever the television said after the person stopped talking. That
        arrives at Whisper as a question with a soap opera stuck on the end.

        The window is the audio just before someone speaks, which is the
        room and not them: the wake word has to be heard first, and while
        it's being waited for nobody is talking to the speaker.
        """
        if not self._recent:
            return 500.0
        # The median ignores the wake word itself and any door slam; the
        # floor stops a genuinely silent room making this impossibly touchy.
        quiet = float(np.median(self._recent))
        return max(quiet * 3.0, 250.0)

    def measure_noise_floor(self, seconds: float = 0.6) -> float:
        """Listen to the quiet room for a moment to learn how loud "silence" is.

        Returns the loudness to treat as the cutoff between silence and
        speech. Doing this at startup means the speaker works in a quiet
        bedroom and in a noisy kitchen without changing any settings.
        """
        levels = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            chunk = self.read(timeout=0.5)
            if chunk is not None:
                levels.append(loudness(chunk))

        if not levels:
            return 500.0  # Nothing heard — use a reasonable guess.

        quiet = float(np.median(levels))
        # Speech has to be clearly louder than the room, with a floor so a
        # silent room doesn't make the threshold impossibly sensitive.
        return max(quiet * 3.0, 250.0)

    def record_until_silence(self, silence_level: float | None = None) -> np.ndarray:
        """Record until the person stops talking, then return the audio.

        Recording always lasts at least MIN_RECORD_SECONDS, stops after
        SILENCE_SECONDS of quiet, and gives up at MAX_RECORD_SECONDS.
        """
        # No level given means work it out from the room as it is now.
        if silence_level is None:
            silence_level = self.noise_floor()

        # Keep the tail of what's already queued, so a question that starts
        # the instant the wake word ends doesn't lose its first word.
        chunks: list[np.ndarray] = self.flush(config.PRE_ROLL_SECONDS)
        chunk_seconds = config.BLOCK_SIZE / config.SAMPLE_RATE
        quiet_seconds = 0.0
        started = time.monotonic()

        while True:
            chunk = self.read(timeout=1.0)
            if chunk is None:
                # Nothing arriving (probably muted while speaking) — wait.
                if time.monotonic() - started > config.MAX_RECORD_SECONDS:
                    break
                continue

            chunks.append(chunk)
            elapsed = time.monotonic() - started

            if loudness(chunk) < silence_level:
                quiet_seconds += chunk_seconds
            else:
                quiet_seconds = 0.0

            long_enough = elapsed >= config.MIN_RECORD_SECONDS
            if long_enough and quiet_seconds >= config.SILENCE_SECONDS:
                break
            if elapsed >= config.MAX_RECORD_SECONDS:
                break

        if not chunks:
            return np.zeros(0, dtype=np.float32)

        audio = np.concatenate(chunks)
        # Whisper wants floats between -1 and 1; the mic gives us int16.
        return audio.astype(np.float32) / 32768.0


def find_device(wanted: str) -> int | None:
    """Turn the INPUT_DEVICE setting into a device number.

    Accepts a number ("1"), part of a name ("MacBook Air Microphone"), or
    an empty string for the system default.
    """
    wanted = wanted.strip()
    if not wanted:
        return None

    if wanted.isdigit():
        return int(wanted)

    for number, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0 and wanted.lower() in device["name"].lower():
            return number

    raise SystemExit(
        f"No microphone matching {wanted!r}.\n"
        "See the list with:  python src/audio_in.py --devices"
    )


def list_devices() -> None:
    """Print every microphone the laptop can see."""
    default_input = sd.default.device[0]
    print("Microphones this laptop can see:\n")
    for number, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] <= 0:
            continue
        marker = " <- default" if number == default_input else ""
        print(f"  {number}: {device['name']}{marker}")
    print("\nSet one in .env, for example:  INPUT_DEVICE=MacBook Air Microphone")


def loudness(chunk: np.ndarray) -> float:
    """How loud a chunk of audio is (RMS — the usual way to measure this)."""
    if chunk.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit


    if "--devices" in sys.argv:
        list_devices()
        raise SystemExit

    # Quick check: record three seconds and report what was heard.
    print("Listening to the room...")
    with Microphone() as mic:
        floor = mic.measure_noise_floor()
        print(f"Silence cutoff: {floor:.0f}")
        print("Say something!")
        audio = mic.record_until_silence(floor)
        seconds = len(audio) / config.SAMPLE_RATE
        print(f"Recorded {seconds:.1f} seconds ({len(audio)} samples).")
