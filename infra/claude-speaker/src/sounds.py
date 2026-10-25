"""Background sounds — rain, ocean, a fire — that play until you say stop.

Nothing here is a recording. Every sound is made as it plays, from filtered
noise, which is the right answer for a Raspberry Pi for three reasons: no
files to download or license, no memory to hold them in, and no seam. A
looped recording ticks every time it comes round, and a child lying awake
listening for the tick will find it. Noise made a block at a time never
repeats, so it can run all night.

    rain, ocean, fireplace, fan, white noise, pink noise, brown noise

Giving the speaker back
-----------------------
The microphone array plays and listens through one piece of hardware, and
allows exactly one stream at a time — so a sound playing for eight hours
would otherwise mean eight hours of the speaker being unable to answer.
Everything that needs to make a noise wraps itself in `paused()`, which
closes this stream and reopens it afterwards, carrying on mid-sound. The
count is kept, so a beep inside an answer inside a question all nest
safely.

Hearing you over it
-------------------
This is the interesting one. Audio arriving while the speaker is talking is
thrown away, or it wakes itself up — and that rule can't apply here, or the
speaker would be deaf for the whole night. So the ambience deliberately
does not set `tts.speaking`, and the wake word runs on a microphone that
can hear the rain.

That works because the array cancels its own output in hardware, and the
rain is its own output. It is the one trick this speaker has that a
homemade one usually doesn't. Measured: see README.

    python src/sounds.py rain 0.05      play rain for three minutes
    python src/sounds.py --list
"""

import sys
import threading
import time

import config
import tools

# Each sound is filtered noise with a slow swell over it, and sometimes a
# little something on top. The numbers are the whole recipe:
#
#   colour   white is flat, pink drops 3 dB per octave, brown 6 — pink and
#            brown are the deep ones that sound like weather rather than
#            like a broken television.
#   swell    (how often it breathes in Hz, how deep, 0 to 1)
#   sparkle  a little treble noise on top — rain on a roof
#   crackle  random pops per second — a fire
#   drops    (hertz, per second, loudness) per layer: impulses rung
#            through a resonator, which is what a raindrop landing is
#   wander   depth of a slow random walk in loudness, instead of a sine
#   stereo   two decorrelated channels, so it surrounds rather than
#            points. Costs a second noise stream and nothing else.
RECIPES = {
    # Rain, after reading how it is actually done rather than guessing.
    #
    # Farnell's model in Designing Sound builds it from a *shaped* bed
    # plus a few audible drops a second — not from thousands of drops.
    # The first version here used three thousand a second and measured
    # flatter than the hiss it replaced, which is the whole lesson: a
    # thousand overlapping transients is the definition of noise. That is
    # why rain sounds like noise in the first place.
    #
    # So the texture comes from `gate`, a threshold on the noise bed that
    # keeps only its peaks — Farnell subtracts a constant and clips, which
    # turns smooth noise into sparse spatter for almost no cost. The drops
    # on top are sparse and loud enough to hear one at a time, which is
    # what the ear uses to say water rather than static.
    "rain":       dict(colour="pink", wander=0.10, sparkle=0.10,
                       stereo=True, gate=0.55,
                       drops=((2800, 7, 0.40),      # near, bright, countable
                              (1500, 26, 0.22),     # middle distance
                              (850, 70, 0.10))),    # far, blurring into bed
    # The old recipe, kept so the two can be compared by ear and in the
    # wake log — this one has to be listened through all night.
    "rain-plain": dict(colour="pink",  swell=(0.09, 0.10), sparkle=0.30),
    "ocean":      dict(colour="brown", swell=(0.07, 0.55), stereo=True),
    "fireplace":  dict(colour="brown", swell=(0.30, 0.18), crackle=7.0),
    "fan":        dict(colour="brown", swell=(0.02, 0.04)),
    "white":      dict(colour="white"),
    "pink":       dict(colour="pink"),
    "brown":      dict(colour="brown"),
}

# What people call them out loud, mapped to the recipe.
ALIASES = {
    "rain": "rain", "rainfall": "rain", "raining": "rain", "storm": "rain",
    "ocean": "ocean", "sea": "ocean", "waves": "ocean", "surf": "ocean",
    "beach": "ocean",
    "fireplace": "fireplace", "fire": "fireplace", "campfire": "fireplace",
    "fan": "fan", "air conditioner": "fan", "aircon": "fan",
    "white": "white", "white noise": "white",
    "pink": "pink", "pink noise": "pink",
    "brown": "brown", "brown noise": "brown", "brownian noise": "brown",
    "static": "white", "noise": "white",
}

# Nobody means "forever" literally, and a stream left open for a week is a
# thing to explain rather than a feature.
MAX_HOURS = 24.0

_lock = threading.RLock()
# Other things that hold the speaker and have to step aside for it to talk.
# books.py registers itself here, so every existing `with paused():` — in
# tts.speak, in beep, in the main loop — quiets a story too, without any of
# them having to know stories exist.
_others: list = []
_stream = None
_maker = None
_name: str | None = None
_until = 0.0
_paused_depth = 0


class _Maker:
    """Makes the next block of a sound, forever, without repeating.

    Filter state carries from one block to the next, which is what keeps the
    joins inaudible — and is also why pausing can close the stream and
    reopen it without the sound jumping.
    """

    def __init__(self, recipe: dict, rate: int) -> None:
        import numpy as np
        from scipy.signal import lfilter_zi

        self._np = np
        self._rate = rate
        self._recipe = recipe
        self._rng = np.random.default_rng()
        self._phase = 0.0
        self._embers = np.zeros(0, dtype=np.float32)
        self._wander = 1.0                 # slow random walk, around 1.0
        self._drop_state: dict = {}        # resonator memory, per layer
        self._tails: dict = {}             # rings that outlive their block
        self._right = None                 # the second channel's filter

        # A three-pole fit to pink noise, and a leaky integrator for brown.
        # Both need their state kept between blocks; lfilter_zi gives the
        # steady state to start from, so the first block isn't a thump.
        colour = recipe.get("colour", "white")
        if colour == "pink":
            self._b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
            self._a = [1.0, -2.494956002, 2.017265875, -0.522189400]
            self._gain = 3.0
        elif colour == "brown":
            self._b = [0.03]
            self._a = [1.0, -0.97]
            self._gain = 3.5
        else:
            self._b = self._a = None
            self._gain = 0.28

        self._state = (lfilter_zi(self._b, self._a) * 0.0
                       if self._b is not None else None)

    def block(self, frames: int):
        """The next slice of sound.

        Built in two parts, which is what makes stereo cheap: a *bed* of
        filtered noise that each channel gets its own copy of, and the
        *extras* — drops, sparkle, crackle — which are the same in both.
        Shared transients with a decorrelated bed is what surrounds you;
        two identical channels are a point source however loud.
        """
        np = self._np

        # How loud this block is, shared by both channels so they swell
        # together. A sine for the old recipes, a random walk for rain.
        level = 1.0
        swell = self._recipe.get("swell")
        if swell:
            hertz, depth = swell
            step = 2 * np.pi * hertz / self._rate
            angles = self._phase + step * np.arange(frames, dtype=np.float32)
            self._phase = float((self._phase + step * frames) % (2 * np.pi))
            level = (1.0 - depth) + depth * (0.5 + 0.5 * np.sin(angles))

        wander = self._recipe.get("wander")
        if wander:
            # Rain gusts; it does not oscillate. One very slow pole, so
            # the loudness takes tens of seconds to get anywhere, clamped
            # so it can drift but never fade away or shout.
            target = 1.0 + wander * float(self._rng.standard_normal()) * 0.5
            self._wander += (target - self._wander) * min(
                frames / (self._rate * 8.0), 1.0)
            self._wander = float(np.clip(self._wander, 1.0 - wander,
                                         1.0 + wander))
            level = level * self._wander

        # Everything added rather than multiplied, and identical in both
        # channels: a drop lands in one place, not in each ear separately.
        extras = np.zeros(frames, dtype=np.float32)
        sparkle = self._recipe.get("sparkle")
        if sparkle:
            # Difference of white noise is a cheap high pass — the hiss of
            # rain hitting things, as opposed to the rumble of a downpour.
            fine = self._rng.standard_normal(frames + 1).astype(np.float32)
            extras += sparkle * 0.3 * np.diff(fine)
        crackle = self._recipe.get("crackle")
        if crackle:
            extras += self._crackle(frames, crackle)
        for layer in self._recipe.get("drops", ()):
            extras += self._drops(frames, *layer)

        left = self._shape(self._bed(frames, "left")) * level + extras
        left = np.clip(left * config.SOUND_VOLUME, -1.0, 1.0)
        if not self._recipe.get("stereo"):
            return left

        right = self._shape(self._bed(frames, "right")) * level + extras
        right = np.clip(right * config.SOUND_VOLUME, -1.0, 1.0)
        return np.stack([left, right], axis=1)

    def _shape(self, bed):
        """Keep the peaks of the noise and throw the middle away.

        Farnell's trick, and the cheapest realism in the file: subtract a
        threshold and clip at zero, and smooth noise becomes sparse
        spatter. Multiplied back up so the loudness does not drop with it.
        """
        np = self._np
        gate = self._recipe.get("gate")
        if not gate:
            return bed
        rms = float(np.sqrt((bed * bed).mean())) or 1.0
        keep = np.maximum(np.abs(bed) - gate * rms, 0.0)
        return (np.sign(bed) * keep * (1.0 + gate)).astype(np.float32)

    def _bed(self, frames: int, side: str):
        """A channel's own noise, coloured, with its filter state kept."""
        np = self._np
        noise = self._rng.standard_normal(frames).astype(np.float32)
        if self._b is None:
            return noise * self._gain
        from scipy.signal import lfilter
        if side == "right" and self._right is None:
            self._right = self._state * 0.0
        state = self._state if side == "left" else self._right
        noise, state = lfilter(self._b, self._a, noise, zi=state)
        if side == "left":
            self._state = state
        else:
            self._right = state
        return noise.astype(np.float32) * self._gain

    def _drops(self, frames: int, hertz: float, per_second: float,
               loudness: float):
        """One layer of raindrops: impulses, each rung through a resonator.

        Not a loop over drops — at two thousand a second that would cost
        more than the wake word. A sparse impulse train through a two-pole
        resonator gives every impulse its own decaying ring in one pass,
        and the whole layer is a handful of microseconds.

        The ring outlives its block, so the tail is carried into the next
        one; cutting it at the edge would be a click, and a click every
        46 ms is exactly the tick this file exists to avoid.
        """
        np = self._np
        from scipy.signal import lfilter

        tail = self._rate // 20
        train = np.zeros(frames + tail, dtype=np.float32)
        held = self._tails.get(hertz)
        if held is not None:
            train[:held.size] += held[:train.size]

        # Poisson, so the gaps between drops are irregular. Evenly spaced
        # impulses buzz at their own rate rather than sounding like rain.
        count = self._rng.poisson(per_second * frames / self._rate)
        if count:
            where = self._rng.integers(0, frames, size=count)
            # Near drops are louder and rarer; the spread is what stops a
            # layer sounding like one drop repeated.
            train[where] += self._rng.uniform(0.3, 1.0,
                                              size=count).astype(np.float32)

        # A two-pole resonator at `hertz`, decaying in a few milliseconds.
        decay = float(np.exp(-1.0 / (self._rate * 0.010)))
        w = 2 * np.pi * hertz / self._rate
        a = [1.0, -2.0 * decay * float(np.cos(w)), decay * decay]
        state = self._drop_state.get(hertz)
        rung, state = lfilter([1.0 - decay], a, train, zi=state) \
            if state is not None else lfilter([1.0 - decay], a, train,
                                              zi=np.zeros(2))
        self._drop_state[hertz] = state
        rung = rung.astype(np.float32)

        self._tails[hertz] = train[frames:].copy()
        return rung[:frames] * loudness

    def _crackle(self, frames: int, per_second: float):
        """Random pops that decay away — a fire, more or less.

        Each pop outlives its block, so the tail is carried forward rather
        than cut off at the block edge, which would itself be a click.
        """
        np = self._np
        pops = np.zeros(frames + self._rate // 10, dtype=np.float32)
        pops[:self._embers.size] = self._embers[:pops.size]

        expected = per_second * frames / self._rate
        for _ in range(self._rng.poisson(expected)):
            at = int(self._rng.integers(0, frames))
            length = int(self._rate * self._rng.uniform(0.004, 0.03))
            decay = np.exp(-np.arange(length, dtype=np.float32) * 6.0 / length)
            loudness = float(self._rng.uniform(0.15, 0.8))
            spark = self._rng.standard_normal(length).astype(np.float32)
            pops[at:at + length] += spark * decay * loudness

        self._embers = pops[frames:]
        return pops[:frames]


def known(name: str) -> str | None:
    """Turn what somebody said into a recipe name, or None."""
    cleaned = name.strip().lower().removesuffix(" sounds").removesuffix(" sound")
    cleaned = cleaned.removesuffix(" noise") if cleaned not in ALIASES else cleaned
    return ALIASES.get(cleaned) or (cleaned if cleaned in RECIPES else None)


def play(name: str, hours: float) -> str:
    """Start a sound. Returns what to tell the person."""
    recipe = known(name)
    if recipe is None:
        return (f"I don't have a {name} sound. I can do "
                + ", ".join(sorted(RECIPES)) + ".")

    hours = max(0.01, min(float(hours), MAX_HOURS))
    global _name, _until, _maker
    with _lock:
        _shut()
        _name = recipe
        _until = time.monotonic() + hours * 3600
        _maker = None
        _open()

    return (f"Playing {recipe} for {_say_hours(hours)}. "
            "Say the wake word and ask me to stop it.")


def stop() -> str:
    """Stop whatever is playing. Returns what to tell the person."""
    global _name
    with _lock:
        if _name is None:
            return "Nothing is playing."
        was = _name
        _name = None
        _shut()
    return f"Stopped the {was}."


def playing() -> str | None:
    """The sound that's on, or None."""
    with _lock:
        if _name is not None and time.monotonic() >= _until:
            stop()
        return _name


def left() -> str:
    """How much longer it has to run, said out loud."""
    with _lock:
        if _name is None:
            return "Nothing is playing."
        return f"{_name}, {_say_hours((_until - time.monotonic()) / 3600)} left"


# --- what Claude can ask for --------------------------------------------------


@tools.tool(
    "Play a continuous background sound — rain, ocean, fireplace, fan, or "
    "white, pink or brown noise. Use this for 'play rain', 'put the ocean "
    "on', 'I want white noise'. It keeps going until someone asks you to "
    "stop it, so this is the right tool for going to sleep to.",
    properties={
        "name": {
            "type": "string",
            "description": "rain, ocean, fireplace, fan, white, pink, or "
                           "brown.",
        },
        "hours": {
            "type": "number",
            "description": "How long to play for, up to 24. Leave it out "
                           "unless they said — the default is a night's "
                           "worth.",
        },
    },
    required=["name"],
    says="play background sounds like rain or white noise",
)
def play_sound(name: str, hours: float = 0) -> str:
    return play(name, hours or config.SOUND_HOURS)


@tools.tool("Stop the background sound. Use this for 'stop', 'turn it off', "
            "'that's enough' when something is playing.")
def stop_sound() -> str:
    return stop()


@tools.tool("Say what background sound is playing and how much longer it "
            "has to run.")
def what_is_playing() -> str:
    return left()


def also_pause(pause, resume) -> None:
    """Register something else that has to go quiet when the speaker talks."""
    _others.append((pause, resume))


class paused:
    """Give the speaker back for a moment, then carry on where it was.

    Used by everything in tts.py that makes a noise. Counted, so a beep
    inside a turn inside an answer doesn't restart the rain in the middle.
    """

    def __enter__(self) -> None:
        global _paused_depth
        with _lock:
            _paused_depth += 1
            if _paused_depth == 1:
                _shut()
                for pause, _resume in _others:
                    pause()

    def __exit__(self, *exc) -> None:
        global _paused_depth
        with _lock:
            _paused_depth = max(0, _paused_depth - 1)
            if _paused_depth:
                return
            for _pause, resume in _others:
                resume()
            if _name is not None:
                if time.monotonic() >= _until:
                    stop()
                else:
                    _open()


# --- the stream ------------------------------------------------------------


def _open() -> None:
    """Start playing. Call with _lock held."""
    global _stream, _maker
    if _stream is not None or _name is None or _paused_depth:
        return

    import sounddevice as sd
    import tts

    device = tts.find_output_device(config.OUTPUT_DEVICE)
    rate = tts.playable_rate(device, 22_050)
    if _maker is None:
        _maker = _Maker(RECIPES[_name], rate)

    # Stereo only for the sounds that ask for it. A mono stream is half
    # the data over USB and half the work, and a fan does not need to
    # arrive from two directions.
    wide = bool(RECIPES[_name].get("stereo"))

    def feed(outdata, frames, time_info, status):
        import numpy as np
        block = _maker.block(frames)
        if block.ndim == 1:
            block = block[:, None]
        # However many channels the device took, fill them all: a stereo
        # block into a mono device would otherwise play only the left.
        for channel in range(outdata.shape[1]):
            source = block[:, min(channel, block.shape[1] - 1)]
            outdata[:, channel] = (source * 32767).astype(np.int16)

    try:
        _stream = sd.OutputStream(
            samplerate=rate, channels=2 if wide else 1,
            dtype="int16", device=device,
            # Bigger than the default. The wake word is already using most
            # of a core, and an underrun here is an audible tear in what is
            # supposed to be the calmest thing in the room.
            blocksize=2048, callback=feed)
        _stream.start()
    except Exception as error:
        print(f"[sounds] couldn't play: {error}")
        _stream = None


def _shut() -> None:
    """Close the stream but keep the sound's place. Call with _lock held."""
    global _stream
    if _stream is None:
        return
    try:
        _stream.stop()
        _stream.close()
    except Exception:
        pass
    _stream = None


def _watch() -> None:
    """Stop when the time is up, whatever else is going on."""
    while True:
        time.sleep(5)
        playing()  # Checks the deadline, and stops if it has passed.


def start() -> None:
    """Begin watching the clock, once, at startup."""
    threading.Thread(target=_watch, daemon=True).start()


def _say_hours(hours: float) -> str:
    if hours < 1:
        minutes = max(1, round(hours * 60))
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    if hours == int(hours):
        return f"{int(hours)} hour{'s' if hours != 1 else ''}"
    return f"{hours:.1f} hours"


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    if "--list" in sys.argv:
        print("Sounds:", ", ".join(sorted(RECIPES)))
        raise SystemExit

    name = sys.argv[1] if len(sys.argv) > 1 else "rain"
    hours = float(sys.argv[2]) if len(sys.argv) > 2 else 0.02
    print(play(name, hours))
    try:
        while playing():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(stop())
