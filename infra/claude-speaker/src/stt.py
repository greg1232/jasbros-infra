"""Speech to text — turns a recording into words.

Uses faster-whisper, which runs locally on the laptop. Nothing is sent over
the internet, it costs nothing, and on Apple Silicon a short question is
transcribed in well under a second.

The first run downloads the model (about 150 MB for base.en) and caches it,
so it only happens once.
"""

import numpy as np

import sys

import config

_model = None


def _get_model():
    """Load the Whisper model the first time it's needed, then reuse it."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        print(f"Loading the {config.WHISPER_MODEL} speech model...")
        # int8 keeps it small and fast enough for a laptop CPU.
        _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def warm_up() -> None:
    """Load the model now, so the first real question isn't slow."""
    _get_model()


def transcribe(audio: np.ndarray) -> str:
    """Turn recorded audio into text. Returns "" if nothing was said."""
    if audio.size == 0:
        return ""

    segments, _info = _get_model().transcribe(
        audio,
        language="en",
        beam_size=1,  # Fast. Bump to 5 if accuracy matters more than speed.
        vad_filter=True,  # Skip silent parts.
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    # Quick check: record a sentence and print what it heard.
    import audio_in

    warm_up()
    with audio_in.Microphone() as mic:
        floor = mic.measure_noise_floor()
        print("Say something!")
        audio = mic.record_until_silence(floor)

    print(f"Heard: {transcribe(audio)!r}")
