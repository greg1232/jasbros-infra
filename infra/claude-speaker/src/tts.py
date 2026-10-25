"""Text to speech — makes the speaker talk.

Which voice you get depends on the machine, and it's picked automatically:

  macOS          the built-in `say` command. Nothing to install.
  Linux (Pi)     Piper, a small neural voice that runs on the Raspberry Pi
                 itself, with espeak-ng as a last resort.

Piper was chosen for the Pi because it's the only good-sounding text to
speech that comfortably runs faster than real time on four Arm cores: a
medium voice takes about a third of a second of compute per second of
speech. The bigger, more expressive models — Chatterbox and its kind — need
over three gigabytes of weights before Python has even started, which is
more memory than the Pi has, and would take minutes per sentence here.

Hear the macOS voices with:  say -v '?'
Piper's voices:              https://rhasspy.github.io/piper-samples/
"""

import queue
import re
import shutil
import subprocess
import sys
import threading

import config
import sounds

# True while the speaker is talking out loud. The microphone watches this
# so the speaker doesn't hear itself and wake itself up in a loop.
speaking = threading.Event()

# The sound hardware takes one stream at a time — on the microphone array,
# playing and listening are literally the same device. There are two threads
# that talk now, the one answering questions and the one ringing timers, so
# they queue here rather than racing for it and losing with "Device
# unavailable".
_device = threading.Lock()

# Set to cut an answer off in the middle. The speaker checks it between
# small blocks of audio rather than between sentences, because a sentence
# is one to three seconds and being interrupted three seconds later is not
# being interrupted.
_hushed = threading.Event()
_talking: "subprocess.Popen | None" = None

MACOS = sys.platform == "darwin"

# A short built-in macOS sound, used as the "I'm listening" beep.
BEEP_SOUND = "/System/Library/Sounds/Tink.aiff"

# Where Piper voices are kept. They're about 63 MB each and downloaded on
# first use, so they live outside the repository.
VOICE_DIR = config.PROJECT_ROOT / "voices"

_voice = None  # The loaded Piper voice, kept in memory between answers.


def warm_up() -> None:
    """Get everything ready before the first question, not during it.

    Loading a Piper voice takes about five seconds on a Raspberry Pi.
    Without this it happens partway through the first thing the speaker
    ever says, which is the worst possible moment for it.
    """
    if MACOS:
        return
    turn_up()
    _piper_voice()


def turn_up() -> None:
    """Set the speaker's own volume to OUTPUT_VOLUME.

    USB audio devices often arrive attenuated. The reSpeaker array came set
    to 37 of 60, on a scale where 60 is 0 dB and 0 is -60 dB — about 20 dB
    down, which sounds like a fault rather than a setting. Nothing can be
    gained in software to make up for it: Piper already peaks at full scale,
    so the only headroom is here.

    This runs at every startup rather than once when the speaker is
    installed, because mixer levels don't reliably survive a reboot — and a
    speaker that goes quiet every time the power blinks is worse than one
    that was never loud.
    """
    if MACOS or not config.OUTPUT_VOLUME.strip():
        return

    level = config.OUTPUT_VOLUME.strip().rstrip("%") + "%"

    # PipeWire is a gain stage too, and a quiet one by default: its sink
    # sat at 0.40, which is 8 dB, on top of the card's own 23. Turning the
    # hardware up while a software mixer holds it down achieves nothing.
    _turn_pipewire_up(level)

    card = _output_card()
    if card is None:
        return
    try:
        contents = subprocess.run(
            ["amixer", "-c", card, "contents"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return  # No amixer, or no such card. Not worth failing over.

    for block in contents.split("numid=")[1:]:
        if "Playback Volume" not in block:
            continue
        subprocess.run(
            ["amixer", "-c", card, "cset", "numid=" + block.split(",")[0], level],
            capture_output=True, check=False,
        )


def _turn_pipewire_up(level: str) -> None:
    """Take PipeWire's own volume out of the way."""
    try:
        subprocess.run(
            ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@",
             f"{int(level.rstrip('%')) / 100:.2f}"],
            capture_output=True, check=False)
    except OSError:
        pass  # No wpctl. Not worth failing over.


def volume() -> int | None:
    """How loud the speaker is now, 0 to 100, or None if it cannot tell.

    PipeWire's sink, not the card. turn_up() sets the card once at startup
    to undo the array arriving 23 dB down, and that is a calibration
    rather than a setting — leaving it alone means there is exactly one
    gain stage anybody turns, which is the one here.
    """
    try:
        said = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                              capture_output=True, text=True, timeout=5)
    except OSError:
        return None
    for word in said.stdout.split():
        try:
            return int(round(float(word) * 100))
        except ValueError:
            continue
    return None


def set_volume(percent) -> int | None:
    """Set it, and report back what it actually became."""
    try:
        want = max(0, min(100, int(float(percent))))
    except (TypeError, ValueError):
        return None
    _turn_pipewire_up(f"{want}%")
    return volume()


def _output_card() -> str | None:
    """The ALSA card number to turn up, for amixer to talk to.

    PortAudio's device numbers and ALSA's card numbers are different
    things, so this reads `aplay -l` rather than reusing
    find_output_device().

    OUTPUT_DEVICE names what to play through, which since PipeWire went in
    is "pipewire" — a name that appears in no `aplay -l` line, so this
    quietly returned None and the array's own mixer stopped being turned
    up at all. It had reverted to 37 of 60, which is 23 dB down, and with
    PipeWire's sink at 0.40 on top of it the speaker was 31 dB quieter than
    it had been. So when the setting names a software device, ask PipeWire
    which card its output actually lands on.
    """
    import re

    try:
        listing = subprocess.run(
            ["aplay", "-l"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None

    wanted = config.OUTPUT_DEVICE.strip().lower()
    if wanted in ("pipewire", "pulse", "default", "sysdefault"):
        wanted = (_card_behind_pipewire() or "").lower()

    for line in listing.splitlines():
        match = re.match(r"card (\d+):", line)
        if match and (not wanted or wanted in line.lower()):
            return match.group(1)
    return None


def _card_behind_pipewire() -> str | None:
    """The name of the sound card PipeWire's default sink plays through."""
    try:
        told = subprocess.run(
            ["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"],
            capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    found = re.search(r'alsa\.card_name\s*=\s*"([^"]+)"', told)
    return found.group(1) if found else None


def hush() -> bool:
    """Stop talking, now. True if it was saying something."""
    was = speaking.is_set()
    _hushed.set()
    if _talking is not None:
        try:
            _talking.terminate()
        except Exception:
            pass
    return was


def speak(text: str) -> None:
    """Say `text` out loud and wait until it finishes."""
    text = text.strip()
    if not text:
        return
    _hushed.clear()

    with _device, sounds.paused():
        speaking.set()
        try:
            if MACOS:
                _say_macos(text)
            else:
                _say_linux(text)
        finally:
            speaking.clear()


def beep() -> None:
    """Play the "I'm listening" sound, and start recording straight away.

    This deliberately does not wait for the sound to finish, and does not
    set `speaking`. Both were costing the first words of every question:
    the player takes 1.2-1.7 seconds to start up and play a 0.56 second
    sound, and with the microphone muted for all of it, anyone who answered
    the beep promptly had their opening words thrown away. You had to pause
    for a beat before speaking, which is exactly the wrong instinct to
    teach someone.

    The cost is that the beep itself lands at the top of the recording.
    That's fine — it's short, and Whisper ignores it. Muting matters for
    `speak()`, where the speaker really would hear itself and wake up in a
    loop; a tink is not that.
    """
    if MACOS:
        # Not under the lock: this one deliberately doesn't wait.
        subprocess.Popen(
            ["afplay", BEEP_SOUND],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    # There's no stock sound file to rely on, so make the tone ourselves.
    import numpy as np

    device = find_output_device(config.OUTPUT_DEVICE)
    # Make the tone at whatever rate this speaker wants, rather than making
    # it at one rate and converting — a sine wave costs nothing to generate.
    rate = playable_rate(device, 22_050)
    t = np.arange(int(rate * 0.12)) / rate
    tone = 0.25 * np.sin(2 * np.pi * 880 * t)
    # Fade both ends, or it clicks.
    fade = np.minimum(np.minimum(t, t[-1] - t) * 60, 1.0)
    with _device, sounds.paused():
        _play(device, rate, (tone * fade * 32767).astype(np.int16))


def ring_once() -> None:
    """One burst of the timer alarm: two short beeps, about half a second.

    Deliberately short. A timer rings for half a minute (see timers.py) and
    the microphone is deaf for as long as the speaker is playing, so the
    ringing is broken into bursts with long gaps to listen in — otherwise
    "hey Claude, stop" couldn't be heard until it had finished ringing on
    its own. The gaps are why this beeps twice and stops rather than
    holding a note.

    Two notes a fifth apart, which carries across a room better than one.
    """
    import numpy as np

    device = find_output_device(config.OUTPUT_DEVICE)
    rate = playable_rate(device, 22_050)

    parts = []
    for hertz in (1047, 1568):  # C6 and G6.
        t = np.arange(int(rate * 0.18)) / rate
        note = 0.4 * np.sin(2 * np.pi * hertz * t)
        # Fade both ends, or it clicks — and a click is what a broken
        # speaker sounds like.
        fade = np.minimum(np.minimum(t, t[-1] - t) * 80, 1.0)
        parts.append(note * fade)
        parts.append(np.zeros(int(rate * 0.09)))

    audio = (np.concatenate(parts) * 32767).astype(np.int16)
    with _device, sounds.paused():
        speaking.set()
        try:
            _play(device, rate, audio)
        finally:
            speaking.clear()


def _play(device, rate: int, audio) -> None:
    """Play one lump of audio, and give the speaker back afterwards.

    Opening and closing the device around every sound is deliberate. When
    the speaker is a microphone array, playing and listening are the same
    piece of hardware and it allows exactly one stream at a time — anything
    left open in the background makes the next thing to talk fail with
    "Device unavailable". `sounddevice.play()` does exactly that: it keeps
    its stream open after the sound has finished, so the beep would silence
    every answer that followed it.
    """
    import numpy as np
    import sounddevice as sd

    with sd.OutputStream(samplerate=rate, channels=1, dtype="int16",
                         device=device) as stream:
        stream.write(np.ascontiguousarray(audio))
        _drain(stream)


def spoken_pieces(text: str):
    """Piper reading `text`, handed back a piece at a time as (rate, audio).

    For books.py, which needs the audio rather than the speaker: it plays
    the pieces itself so that it can stop between them, keep its place, and
    give the device back when somebody asks a question.
    """
    voice = _piper_voice()
    if voice is None:
        return
    from piper import SynthesisConfig

    settings = SynthesisConfig(length_scale=175 / config.SPEECH_RATE)
    native = voice.config.sample_rate
    for chunk in _synthesise(voice, text, settings):
        yield native, _trimmed(chunk.audio_int16_array, native)


def nudge() -> None:
    """Drive the playback half for a quarter of a second, silently.

    The array only sends microphone audio while its playback endpoint is
    being driven. When it stops, nothing anywhere reports it — see
    docs/troubleshooting.md. The cure is the WirePlumber rule deploy.py
    installs; this is what audio_in falls back on if it ever goes deaf
    regardless, because reopening the input does not help and neither does
    restarting the audio stack. Playing something does.

    It takes the device lock like everything else here, because the array
    is one piece of hardware and allows one stream. It does not set
    `speaking`: there is nothing to hear, and the microphone needs to be
    listening for exactly the audio this is trying to bring back.
    """
    import numpy as np

    import sounds

    device = find_output_device(config.OUTPUT_DEVICE)
    rate = playable_rate(device, config.SAMPLE_RATE)
    with _device, sounds.paused():
        _play(device, rate, np.zeros(int(rate * 0.25), dtype=np.int16))


def play_clip(audio, rate: int) -> None:
    """Play one lump of audio somebody found, at whatever rate it came at.

    Used by effects.py. Goes through the same arbitration as speech: the
    array is one device, so this takes the lock and steps the background
    sounds aside, and sets `speaking` so the wake word can't hear a
    bullfrog and wake up for it.
    """
    import numpy as np

    import sounds

    device = find_output_device(config.OUTPUT_DEVICE)
    playable = playable_rate(device, rate)
    samples = (np.asarray(audio, dtype=np.float32) * 32767).astype(np.int16)

    with _device, sounds.paused():
        speaking.set()
        try:
            _play(device, playable, _at_rate(samples, rate, playable))
        finally:
            speaking.clear()


def _drain(stream) -> None:
    """Wait for the sound the card is still holding.

    Closing a stream does not empty it. Measured on the Pi, _play returned
    0.13 seconds before a tone had finished coming out of the speaker, at
    one and at three seconds alike — so it is the buffer, not a fraction.

    That 0.13 seconds is when `speaking` gets cleared, which is when the
    microphone starts listening again, which means the speaker heard the
    tail of every single thing it said. Enough to catch the end of a word
    and put it at the front of the next question.
    """
    import time

    time.sleep(max(0.0, float(getattr(stream, "latency", 0.0) or 0.0)) + 0.05)


# --- macOS -----------------------------------------------------------------


def _say_macos(text: str) -> None:
    global _talking
    _talking = subprocess.Popen(
        ["say", "-v", config.VOICE, "-r", str(config.SPEECH_RATE), text])
    try:
        _talking.wait()
    finally:
        _talking = None


# --- Linux -----------------------------------------------------------------


def _say_linux(text: str) -> None:
    """Speak with Piper, falling back to espeak-ng if it isn't available."""
    voice = _piper_voice()
    if voice is None:
        _say_espeak(text)
        return

    import numpy as np
    import sounddevice as sd
    from piper import SynthesisConfig

    # Piper's speed dial is a stretch factor, not words per minute: below 1
    # is faster, above 1 is slower. A Piper voice at 1.0 reads at roughly
    # 175 words a minute, so this keeps SPEECH_RATE meaning the same thing
    # on both machines.
    settings = SynthesisConfig(length_scale=175 / config.SPEECH_RATE)

    device = find_output_device(config.OUTPUT_DEVICE)
    native = voice.config.sample_rate
    rate = playable_rate(device, native)

    # Piper hands back one chunk per sentence, and making each one takes
    # about six tenths of a second on a Pi — half as long as the sentence
    # lasts. Written straight to the stream that time is dead air, because
    # stream.write() blocks until the audio has played out, so nothing is
    # being synthesised while anything is being said. That was the long
    # pause after every full stop: not the voice taking a breath, the Pi
    # thinking.
    #
    # So one thread makes the sound and another plays it. The next sentence
    # is ready long before the current one finishes, and the only gap left
    # is the one deliberately put there.
    made: queue.Queue = queue.Queue(maxsize=4)

    def synthesise() -> None:
        try:
            for chunk in _synthesise(voice, text, settings):
                # Resampling happens here too, on this thread, for the same
                # reason: it is work, and work belongs off the path that is
                # holding the speaker open.
                made.put(_at_rate(_trimmed(chunk.audio_int16_array, native),
                                  native, rate))
        except Exception as error:
            print(f"[voice] {type(error).__name__}: {error}")
        finally:
            made.put(None)

    worker = threading.Thread(target=synthesise, daemon=True)
    worker.start()

    gap = np.zeros(int(rate * config.SENTENCE_PAUSE), dtype=np.int16)
    with sd.OutputStream(samplerate=rate, channels=1, dtype="int16",
                         device=device) as stream:
        first = True
        while not _hushed.is_set():
            piece = made.get()
            if piece is None:
                break
            # The pause goes before each sentence rather than after, so an
            # answer ends the moment the last word does. Trailing silence
            # here is silence with the microphone still muted.
            if not first and gap.size:
                stream.write(gap)
            first = False
            # In blocks, not in one go, so hush() takes effect in a tenth
            # of a second instead of at the end of the sentence.
            step = max(1024, rate // 10)
            for at in range(0, len(piece), step):
                if _hushed.is_set():
                    break
                stream.write(np.ascontiguousarray(piece[at:at + step]))
        if not _hushed.is_set():
            _drain(stream)
    worker.join(timeout=1.0)


# How many words to let the first piece run to before breaking it, and the
# ones after. The first is short because nothing is being said until it is
# finished; the rest are longer because by then the speaker is talking and
# there is time in hand, and because breaking a sentence costs prosody.
FIRST_WORDS = 10
LATER_WORDS = 28

# Where a long sentence can be broken without it sounding wrong: after a
# comma or semicolon, or before the word that starts a new clause.
CLAUSE = re.compile(
    r"(?<=[,;:])\s+|\s+(?=(?:and|but|or|so|because|which|while|then|"
    r"although|though|since|after|before|when)\s)")


def _synthesise(voice, text: str, settings):
    """Make the audio, in pieces small enough to start on quickly.

    Piper hands back one chunk per sentence and nothing is heard until the
    first one is finished, so the length of the first sentence is the
    length of the silence. Measured on a Pi: a sixty word sentence full of
    ands took 5.88 seconds to produce its first sound. The same answer
    beginning with a short sentence took 1.08.

    Claude is asked to open briefly (see brain.py) but can't be relied on
    to, so long sentences are broken at clause boundaries here. The break
    costs a little prosody — each piece gets its own falling intonation —
    which is why only the opening is cut short, and why the pieces after
    it are allowed to run nearly three times as long.
    """
    for piece in _pieces(text):
        yield from voice.synthesize(piece, settings)


def _pieces(text: str) -> list[str]:
    """Break an answer into what to synthesise, in order."""
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        if sentence.strip():
            out.extend(_break_up(sentence, FIRST_WORDS if not out
                                 else LATER_WORDS))
    return out or [text]


def _break_up(sentence: str, limit: int) -> list[str]:
    """Cut one over-long sentence into pieces of about `limit` words."""
    if len(sentence.split()) <= limit:
        return [sentence]

    pieces: list[str] = []
    current = ""
    for clause in CLAUSE.split(sentence):
        candidate = f"{current} {clause}".strip() if current else clause
        if current and len(candidate.split()) > limit:
            pieces.append(current)
            current = clause
            limit = LATER_WORDS  # Only the opening has to be short.
        else:
            current = candidate
    if current:
        pieces.append(current)

    # A three word fragment on its own sounds like a stumble. Put any runt
    # back with the piece before it.
    tidy: list[str] = []
    for piece in pieces:
        if tidy and len(piece.split()) < 4:
            tidy[-1] = f"{tidy[-1]} {piece}"
        else:
            tidy.append(piece)
    return tidy


def _trimmed(audio, rate: int):
    """Cut the silence off both ends of a sentence.

    Piper leaves about a twentieth of a second at the front and up to a
    seventh at the back, and between two sentences that adds up to a pause
    nobody chose. Trimming both and putting back a known gap makes the
    spacing a setting instead of an accident.

    The threshold is a fraction of the sentence's own peak, so it follows a
    loud sentence and a quiet one alike, and a little of the quiet is kept
    at each end — cutting hard against a soft "s" or "th" is audible, and
    sounds like a dropout rather than a shorter pause.
    """
    import numpy as np

    if audio.size == 0:
        return audio

    loud = np.abs(audio) >= max(np.abs(audio).max() * 0.02, 32)
    speaking_at = np.flatnonzero(loud)
    if speaking_at.size == 0:
        return audio  # All quiet — not ours to judge.

    keep = int(rate * 0.02)
    start = max(0, int(speaking_at[0]) - keep)
    end = min(audio.size, int(speaking_at[-1]) + keep + 1)
    return audio[start:end]


def playable_rate(device, native: int) -> int:
    """The rate to play at: the voice's own, or the speaker's if it insists.

    Most speakers take whatever you give them. A microphone array does not
    — the reSpeaker XVF3800 runs everything at 16 kHz, and Piper's voices
    come out at 22.05 kHz, so handing it the voice's own rate fails outright
    with "Invalid sample rate" and nothing is ever said.
    """
    import sounddevice as sd

    try:
        sd.check_output_settings(device=device, samplerate=native,
                                 channels=1, dtype="int16")
        return native
    except Exception:
        return int(sd.query_devices(device)["default_samplerate"])


def _at_rate(audio, native: int, rate: int):
    """Resample a chunk, if the speaker won't take the voice's own rate.

    This runs per sentence rather than over the whole answer, which costs a
    little quality at the joins — each chunk is filtered on its own. Piper
    breaks at sentence ends, where there's near silence anyway, and doing it
    this way keeps the speaker talking while the rest is still being made.
    """
    import numpy as np

    if rate == native:
        return np.ascontiguousarray(audio)

    from scipy.signal import resample_poly

    resampled = resample_poly(audio.astype(np.float32), rate, native)
    return np.ascontiguousarray(
        np.clip(resampled, -32768, 32767).astype(np.int16))


def _piper_voice():
    """Load the Piper voice once and keep it, or None if it can't be had.

    Loading takes a second or two on a Pi, which would be noticeable on
    every single answer, so it stays in memory for the life of the program.
    """
    global _voice
    if _voice is not None:
        return _voice

    try:
        from piper import PiperVoice
    except ImportError:
        return None

    path = VOICE_DIR / f"{config.PIPER_VOICE}.onnx"
    if not path.exists():
        try:
            from piper.download_voices import download_voice

            print(f"Downloading the {config.PIPER_VOICE} voice (once)...")
            VOICE_DIR.mkdir(parents=True, exist_ok=True)
            download_voice(config.PIPER_VOICE, VOICE_DIR)
        except Exception as error:
            print(f"Couldn't download the voice ({error}) — using espeak-ng.")
            return None

    _voice = PiperVoice.load(path)
    return _voice


def _say_espeak(text: str) -> None:
    """The plain robotic fallback. Always available, never nice."""
    if shutil.which("espeak-ng") is None:
        print(f"[no voice installed, so this went unsaid] {text}")
        return
    subprocess.run(
        ["espeak-ng", "-v", "en-us", "-s", str(config.SPEECH_RATE), text],
        check=False,
    )


def find_output_device(wanted: str) -> int | None:
    """Turn the OUTPUT_DEVICE setting into a device number.

    Accepts a number, part of a name, or an empty string for the system
    default. Worth setting on a Raspberry Pi: ALSA lists the HDMI outputs
    first, so the default is often a monitor rather than your speakers.
    """
    wanted = wanted.strip()
    if not wanted:
        return None
    if wanted.isdigit():
        return int(wanted)

    import sounddevice as sd

    for number, device in enumerate(sd.query_devices()):
        if device["max_output_channels"] > 0 and wanted.lower() in device["name"].lower():
            return number

    raise SystemExit(
        f"No speaker matching {wanted!r}.\n"
        "See the list with:  python src/tts.py --devices"
    )


def list_devices() -> None:
    """Print every speaker this machine can see."""
    import sounddevice as sd

    default_output = sd.default.device[1]
    print("Speakers this machine can see:\n")
    for number, device in enumerate(sd.query_devices()):
        if device["max_output_channels"] <= 0:
            continue
        marker = " <- default" if number == default_output else ""
        print(f"  {number}: {device['name']}{marker}")
    print("\nSet one in .env, for example:  OUTPUT_DEVICE=Headphones")


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    if "--devices" in sys.argv:
        list_devices()
        raise SystemExit

    # Step 1 of the build order: make the speaker say hello.
    beep()
    speak("Hello, I am Claude. I am your speaker.")
