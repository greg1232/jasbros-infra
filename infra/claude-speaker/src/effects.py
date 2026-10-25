"""Sound effects — real recordings, fetched when somebody asks for one.

    "hey claude, what does a bullfrog say"
    "hey claude, play the sound of a train whistle"

Unlike the background sounds in sounds.py, these can't be made from filtered
noise. A bullfrog is a bullfrog. So they are looked up, downloaded, cached
on disk, and played — and the second bullfrog is instant.

Where they come from
--------------------
Freesound, if you have a key. It is the good one: six hundred thousand
Creative Commons recordings, searchable, with ratings and durations to sort
by. A key is free from https://freesound.org/apiv2/apply/ and goes in .env
as FREESOUND_API_KEY.

Wikimedia Commons otherwise, which needs no key and no account. It works,
but its search is not built for this and the ranking shows it: "cow moo"
returns a 1922 jazz record called Atlanta Rag by Cow Cow Davenport, and
half the hits for any animal are dictionary pronunciations of the word.
Hence FALSE_FRIENDS below, which is not elegant and is entirely necessary.

Playing it
----------
Through the same arbitration as everything else: the microphone array is
one device, so this takes the speaker lock and steps the background sounds
aside, and sets `speaking` so the wake word can't hear the bullfrog and
wake up for it.

    python src/effects.py bullfrog       look one up and play it
    python src/effects.py --list         what's already cached
"""

import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

import config
import tools

CACHE = config.PROJECT_ROOT / "state" / "effects"

# Wikimedia asks for a real one, and returns 403 without it.
AGENT = ("ClaudeSpeaker/1.0 (a family voice assistant; "
         "https://github.com/greg1232/hey-claude)")

COMMONS = "https://commons.wikimedia.org/w/api.php"
FREESOUND = "https://freesound.org/apiv2/search/text/"

# How many recordings to put in front of Claude. Generous on purpose:
# picking the real fire engine out of thirty names is exactly what it is
# good at, and thirty names cost a fraction of a second of tokens. Being
# clever about which eight to show it was how the fire engine got lost.
ENOUGH = 30
# How many to ask each library for, per search.
DEEP = 40

# Longer than this and it isn't a sound effect, it's a recording of
# something. Kept short deliberately: a child asked a question.
LONGEST = 12.0

# Titles that match the search but aren't the thing. Commons is full of
# spoken-dictionary files — "En-us-elephant.ogg" is a person saying the
# word "elephant" — and of music whose title happens to contain an animal.
# Words that shouldn't count towards matching a title, because almost any
# recording of an animal could be described with them.
VAGUE = {"sound", "sounds", "noise", "noises", "call", "calling", "a", "an",
         "the", "of", "effect"}

FALSE_FRIENDS = re.compile(
    r"^(?:[a-z]{2}(?:-[a-z]{2})?)-|^LL-Q|\(eng\)|pronunciation|"
    r"\bsong\b|\brag\b|\bsymphony\b|\bsonata\b|\bopus\b|\bband\b|"
    r"\bmusic\b|\binterview\b|\bspeech\b|\bpodcast\b|\baudiobook\b",
    re.IGNORECASE)


_offered: list[tuple] = []
_asked = ""


@tools.tool(
    "Look for a real recording of a sound: an animal, a vehicle, an "
    "instrument, a natural noise, or anything else that makes a noise. Use "
    "this whenever somebody asks what something sounds like, or asks you "
    "to play the sound of something. This does NOT play anything — it "
    "hands back what the libraries have, and you choose.\n\n"
    "Always look before saying you can't find something. You are a poor "
    "judge of what a sound library holds, and looking costs a second. "
    "Asked what a wet sock sounds like, guessing there would be no "
    "recording was wrong: the library has Wet Slap, Squelching SFX and "
    "Mud Squelching. Look first, then say.\n\n"
    "Search for the sound, not the object. Libraries are filed by what a "
    "thing sounds like, so a wet sock is 'wet slap' or 'squelch', a "
    "haunted house is 'creaking door', a monster is 'growl'. If the "
    "obvious words find nothing, try the noise it makes instead.\n\n"
    "Read the names before picking, because a search match is very often "
    "the wrong thing with the right word in it. A search for a cow returned "
    "a 1922 jazz record called Atlanta Rag by Cow Cow Davenport; a search "
    "for a fire engine returned a bus engine starting. Recordings of "
    "somebody pronouncing the word are common too. If none of them looks "
    "like the actual sound, say so and describe it in words instead — that "
    "is a better answer than playing the wrong thing.\n\n"
    "When one is clearly right, call play_effect with its number straight "
    "away, in the same breath.",
    properties={
        "search": {
            "type": "string",
            "description": "What to look for, as you'd search for a "
                           "recording of it: 'bullfrog croaking', 'steam "
                           "train whistle'. Two or three words.",
        },
    },
    required=["search"],
    says="play real recordings of sounds like animals when asked what "
         "something sounds like",
)
def find_effect(search: str) -> str:
    global _offered, _asked
    search = " ".join(search.strip().lower().split())[:60]
    if not search:
        return "I need to know what to look for."
    _asked = search

    saved = CACHE / f"{_slug(search)}.wav"
    if saved.exists():
        _offered = [(saved.stem.replace("-", " "), str(saved))]
        return ("1. " + _offered[0][0] + " (already saved here). "
                "Call play_effect with 1.")

    try:
        _offered = candidates(search)
    except Exception as error:
        print(f"[effects] {type(error).__name__}: {error}")
        _offered = []
        return "I couldn't reach the sound library just now."

    if not _offered:
        return (f"Nothing in the library matches {search}. "
                "Describe the sound in words instead.")
    return ("Choose one and call play_effect with its number:\n"
            + "\n".join(f"{n}. {title}"
                         for n, (title, _url) in enumerate(_offered, 1)))


@tools.tool(
    "Play one of the recordings find_effect offered, by its number.",
    properties={
        "number": {
            "type": "integer",
            "description": "Which recording, counting from 1.",
        },
    },
    required=["number"],
)
def play_effect(number: int) -> str:
    if not _offered:
        return "Look for a sound first with find_effect."
    if not 1 <= int(number) <= len(_offered):
        return f"There were only {len(_offered)} to choose from."

    title, url = _offered[int(number) - 1]
    try:
        audio, rate = load(url, _asked)
    except Exception as error:
        print(f"[effects] {type(error).__name__}: {error}")
        return f"I couldn't play {title}. Try another one."
    if audio is None:
        return f"{title} wouldn't play. Try another one."

    import tts
    tts.play_clip(audio, rate)
    return f"Played {title!r}."


def load(url: str, search: str):
    """Fetch and tidy up one recording, and keep it for next time.

    Cached under the words that were asked for rather than the file that
    was found, so the second bullfrog is instant and offline.
    """
    import soundfile as sf

    if not url.startswith("http"):
        audio, rate = sf.read(url, dtype="float32")
        return audio, rate

    raw = _get(url, binary=True)
    audio, rate = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)  # Down to mono; the array is one channel.
    if len(audio) / rate > LONGEST:
        audio = audio[:int(LONGEST * rate)]

    peak = float(np.abs(audio).max())
    if peak < 0.01:
        return None, 0  # Silence, or near enough.
    # Recordings arrive at wildly different levels — one measured 0.28
    # peak — and a sound effect that can't be heard is no use.
    audio = audio * (0.9 / peak)

    CACHE.mkdir(parents=True, exist_ok=True)
    sf.write(CACHE / f"{_slug(search)}.wav", audio, rate)
    return audio, rate


def candidates(search: str) -> list[tuple]:
    """Everything that might be the sound, from every angle worth trying.

    Two things about these libraries make one search insufficient.

    Commany ANDs the words, so an exact phrase has high precision and
    almost no recall: "fire engine siren" returns nothing at all, and
    "siren" returns Siren.ogg and an American police siren.

    So the words have to be tried separately — and which word matters
    cannot be guessed. This used to try them longest first, on the theory
    that the longer word is the more particular one. For "fire engine
    siren" that tries "engine" before "siren", matches a bus starting,
    and stops: the right recording was one word away and never reached.
    Nothing about a word's spelling says whether it is the important one.

    So it gathers from every angle and hands the lot to Claude, which can
    tell a police siren from a Gillig bus by reading the names — which is
    what the find_effect tool is for.
    """
    by_term = []
    for term in _attempts(search):
        found = []
        if config.FREESOUND_KEY:
            found = from_freesound(term)
        if not found:
            found = from_commons(term)
        if found:
            by_term.append(found)
        if sum(len(f) for f in by_term) >= ENOUGH * 3:
            break

    # Take turns between the words rather than letting one fill the list.
    # "fire engine siren" finds one fire, six engines and two sirens, and
    # in order the sirens come last and are the first thing a shorter list
    # would cut — the only two that are actually a fire engine.
    seen, out = set(), []
    for rank in range(ENOUGH):
        for found in by_term:
            if rank >= len(found):
                continue
            title, url = found[rank]
            if url in seen:
                continue
            seen.add(url)
            out.append((title, url))
            if len(out) >= ENOUGH:
                return out
    return out


def _attempts(search: str) -> list[str]:
    """The whole phrase first, then each word of it on its own."""
    words = [w for w in search.split() if w not in VAGUE]
    singles = list(dict.fromkeys(words))  # In the order they were said.
    return [search] + [w for w in singles if w != search]


def from_freesound(search: str):
    """The good source, if there's a key for it."""
    query = urllib.parse.urlencode({
        "query": search,
        "filter": f"duration:[0.3 TO {LONGEST}]",
        # Relevance, not rating. Sorting by rating asks for the best
        # sounds that happen to match, which is not the same question: a
        # search for "bowling" came back with highly rated coins and
        # popcorn while three real bowling recordings sat further down.
        # Claude gets the rating in the name and can weigh it itself.
        "sort": "score",
        "fields": "name,previews,avg_rating,num_ratings,duration",
        "page_size": str(DEEP),
        "token": config.FREESOUND_KEY,
    })
    try:
        found = json.loads(_get(f"{FREESOUND}?{query}"))
    except Exception as error:
        print(f"[effects] freesound: {type(error).__name__}: {error}")
        return []

    out = []
    for hit in found.get("results", []):
        preview = (hit.get("previews") or {}).get("preview-hq-mp3")
        if preview:
            rating = hit.get("avg_rating") or 0
            seconds = hit.get("duration") or 0
            out.append((f"{hit.get('name', search)} "
                        f"(freesound, {seconds:.0f}s, rated {rating:.1f})", preview))
    return out


def from_commons(search: str):
    """The one that needs no key, with the junk filtered out."""
    query = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:audio {search}", "gsrnamespace": "6",
        "gsrlimit": str(DEEP), "prop": "imageinfo", "iiprop": "url|mime|size",
    })
    pages = json.loads(_get(f"{COMMONS}?{query}")).get(
        "query", {}).get("pages", {})

    scored = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        title = page.get("title", "")[5:]          # Drop the "File:".
        if not info.get("url") or FALSE_FRIENDS.search(title):
            continue
        # A short file is much more likely to be the sound itself than a
        # recording of an event that contains it.
        size = info.get("size", 0)
        if size > 3_000_000:
            continue
        # How much of what was asked for appears in the name — used to
        # rank, and deliberately not to exclude. This was a filter once,
        # and it threw away "WWS Fireenginesiren.ogg", an actual fire
        # engine siren, because the filename has no spaces in it and so
        # matched none of "fire", "truck" or "siren" as whole words.
        # Substrings catch that; Claude throws out the rest by reading.
        if not title.lower().endswith(PLAYABLE):
            continue
        squashed = re.sub(r"[^a-z]", "", title.lower())
        words = set(re.findall(r"[a-z]+", title.lower()))
        wanted = set(search.split()) - VAGUE
        overlap = sum(1 for w in wanted if w in words or w in squashed)
        scored.append((-overlap, size, title, info["url"]))

    scored.sort()
    # The size is a rough stand-in for length, which the search doesn't
    # give us, and length is often what tells a recording of a thing from
    # a recording of an event containing it.
    return [(f"{t} (wikimedia, {s // 1000} kB)", u)
            for _o, s, t, u in scored[:DEEP]]


# What soundfile can actually decode. Commons files everything under
# "filetype:audio" including MIDI, which is a score rather than a sound and
# fails to open — six of the first ten anvil candidates were guitar tabs.
PLAYABLE = (".ogg", ".oga", ".opus", ".mp3", ".wav", ".flac", ".m4a", ".aac")


def _get(url: str, binary: bool = False):
    """Fetch, waiting and trying once more if the library asks us to.

    Wikimedia rate-limits, and it does so exactly when this is most likely
    to be useful — several searches and a download in quick succession.
    Measured: ten downloads back to back gave nine failures, and the same
    ten with a breath between them gave none.
    """
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
            return raw if binary else raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            if error.code not in (429, 503) or attempt:
                raise
            time.sleep(1.5)
    raise RuntimeError("unreachable")


def _slug(search: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", search.lower()).strip("-")[:50]


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    if "--list" in sys.argv:
        for path in sorted(CACHE.glob("*.wav")):
            print(f"  {path.stem.replace('-', ' ')}  "
                  f"({path.stat().st_size / 1000:.0f} kB)")
        raise SystemExit

    print(find_effect(" ".join(sys.argv[1:]) or "bullfrog"))
    print(play_effect(1))
