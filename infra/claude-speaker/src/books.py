"""Reading books aloud — and remembering where it got to.

    "hey claude, read me Treasure Island"
    "hey claude, next chapter"
    "hey claude, stop"
    ...the next evening...
    "hey claude, read me Treasure Island"   -> carries on from chapter four

A book is the first thing this speaker does that is long-running,
resumable and seekable, and that is the whole of the interesting part. The
fetching is easy; knowing where you were is not.

Where the books come from
-------------------------
LibriVox first: twenty thousand public-domain books read aloud by human
volunteers, free, no key. For a bedtime story that beats Piper outright — a
real voice for two hours instead of a very good two-sentence voice stretched
over a chapter — and it costs the Pi no synthesis at all, which matters when
the wake word already has a quarter of a core. Chapters arrive pre-split
with titles and durations, so "next chapter" is an index.

Project Gutenberg second, for the books nobody has recorded. Not from
gutenberg.org, which answers programs with 503 and whose own catalogue
times out; from the copy on Hugging Face, with a 2.4 MB index of all 48,284
titles kept locally so searching needs no network at all. See
train/build_book_index.py.

Giving the speaker back
-----------------------
The array plays and listens through one device. A story registers itself
with sounds.also_pause(), so every `with sounds.paused():` already in the
code — in tts.speak, in the beep, around each turn — stops the story and
starts it again afterwards, at the same word.

    python src/books.py treasure island      read a bit of it
    python src/books.py --shelf              what's on the children's shelf
"""

import gzip
import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

import config
import sounds
import tools

INDEX = config.PROJECT_ROOT / "models" / "gutenberg.jsonl.gz"
STATE = config.PROJECT_ROOT / "state" / "books"
AGENT = ("ClaudeSpeaker/1.0 (a family voice assistant; "
         "https://github.com/greg1232/hey-claude)")

LIBRIVOX = "https://librivox.org/api/feed/audiobooks/"
ROWS = "https://datasets-server.huggingface.co/rows"
DATASET = "sedthh/gutenberg_english"

# Written to disk this often, so a power cut costs seconds, not an evening.
SAVE_EVERY = 10.0
# How much audio to hand the speaker at a time. Small enough that stopping
# feels immediate and a question doesn't have to wait; large enough not to
# spend the Pi's afternoon on Python.
BLOCK = 16384

_lock = threading.RLock()
_reader = None          # The book being read, or None.
_thread = None
_go = threading.Event()  # Clear means "hold still, somebody is talking".
_stop = threading.Event()


# --- finding a book ---------------------------------------------------------


def find(title: str):
    """The single best book for a title. Used by the command line and tests."""
    found = search(title)
    return _build(found[0]) if found else None


def search(title: str, limit: int = 12) -> list[dict]:
    """Everything that might be the book they meant, best guess first.

    Deliberately does not choose. Libraries are full of things that match
    a title and are not the book: three different recordings of Peter Pan
    of very different lengths, an abridgement, a sequel, a critical essay
    about it, a different author with the same title. Claude picks, with
    the author and the length and the subjects in front of it, and asks
    when it genuinely can't tell — see the tools at the bottom.
    """
    found = _from_librivox(title, limit)
    found += _from_gutenberg(title, limit)
    # A long list on purpose. Choosing between twenty titles by author and
    # length is exactly what Claude is good at, and narrowing it here is
    # how the wrong Peter Pan gets read.
    return found[:limit * 2]


def _from_librivox(title: str, limit: int = 5) -> list[dict]:
    """Human-read recordings that match the title."""
    found = {}
    # LibriVox matches titles closely, and its own are usually filed
    # without the article — "Tale of Two Cities", "Velveteen Rabbit". Asked
    # for "a tale of two cities" it finds nothing and the speaker falls
    # back to reading the text itself, which is a much worse evening.
    for wanted in _title_guesses(title):
        query = urllib.parse.urlencode({
            "format": "json", "extended": "1", "limit": str(limit),
            "title": wanted,
        })
        try:
            found = json.loads(_get(f"{LIBRIVOX}?{query}"))
        except Exception as error:
            # The API answers 404 when nothing matches, which is not a fault.
            if "404" not in str(error):
                print(f"[books] librivox: {type(error).__name__}: {error}")
            continue
        if found.get("books"):
            break

    out = []
    for book in found.get("books", [])[:limit]:
        chapters = [
            {"title": s.get("title") or f"Chapter {s.get('section_number')}",
             "url": s["listen_url"],
             "seconds": float(s.get("playtime") or 0)}
            for s in (book.get("sections") or []) if s.get("listen_url")]
        if not chapters:
            continue
        author = (book.get("authors") or [{}])[0]
        out.append({
            "kind": "spoken",
            "title": book.get("title", title),
            "by": ", ".join(x for x in (author.get("last_name"),
                                        author.get("first_name")) if x),
            "chapters": chapters,
            "parts": len(chapters),
            "hours": sum(c["seconds"] for c in chapters) / 3600,
            "language": book.get("language", ""),
            "about": (book.get("description") or "")[:200],
        })
    return out


def _title_guesses(title: str) -> list[str]:
    """The title as asked for, then without a leading article."""
    guesses = [title]
    bare = re.sub(r"^(the|a|an)\s+", "", title.strip(), flags=re.IGNORECASE)
    if bare.lower() != title.strip().lower():
        guesses.append(bare)
    return guesses


def _from_gutenberg(title: str, limit: int = 5) -> list[dict]:
    """Texts that match, to be read by Piper. No network needed."""
    return [{
        "kind": "printed",
        "title": entry["title"],
        "by": entry.get("by", ""),
        "entry": entry,
        "parts": 0,          # Not known without fetching the book.
        "hours": 0.0,
        "language": "en",
        "about": "; ".join(entry.get("about") or [])[:200],
        "shelves": entry.get("shelves") or [],
    } for entry in search_index(title, limit)]


def _build(found: dict):
    """Turn one candidate into something that can be read."""
    if found["kind"] == "spoken":
        return Spoken(found["title"], found["by"], found["chapters"])
    return Printed(found["entry"])


def search_index(wanted: str, limit: int = 5) -> list[dict]:
    """Look a title up in the local index. No network, instant."""
    if not INDEX.exists():
        return []
    words = [w for w in re.findall(r"[a-z0-9]+", wanted.lower()) if len(w) > 1]
    if not words:
        return []

    scored = []
    with gzip.open(INDEX, "rt", encoding="utf-8") as handle:
        for line in handle:
            book = json.loads(line)
            low = book["title"].lower()
            hits = sum(1 for w in words if w in low)
            if not hits:
                continue
            # Prefer a title that is mostly the words asked for, so "Peter
            # Pan" beats "Peter Pan in Kensington Gardens, Illustrated".
            scored.append((-hits, len(low), book))
    scored.sort(key=lambda row: (row[0], row[1]))
    return [book for _h, _l, book in scored[:limit]]


def _get(url: str, binary: bool = False, timeout: int = 60):
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return raw if binary else raw.decode("utf-8", "replace")


# --- the two kinds of book --------------------------------------------------


class Spoken:
    """A LibriVox recording: a list of mp3 chapters, read by a person."""

    kind = "spoken"

    def __init__(self, title, author, chapters):
        self.title = title
        self.author = author
        self.chapters = chapters
        self.at = 0          # Which chapter.
        self.frame = 0       # How far into it.

    def how_long(self) -> str:
        total = sum(c["seconds"] for c in self.chapters) / 3600
        return f"{len(self.chapters)} chapters, about {total:.0f} hours"

    def chapter_name(self) -> str:
        return self.chapters[self.at]["title"]

    def blocks(self):
        """Audio for the current chapter, from wherever we left off."""
        import soundfile as sf

        path = self._file(self.at)
        with sf.SoundFile(path) as sound:
            rate = sound.samplerate
            if self.frame:
                sound.seek(min(self.frame, len(sound) - 1))
            while True:
                piece = sound.read(BLOCK, dtype="int16", always_2d=True)
                if not len(piece):
                    return
                self.frame = sound.tell()
                yield rate, piece.mean(axis=1).astype(np.int16)

    def _file(self, index: int) -> Path:
        """The chapter on disk, downloaded if it isn't there yet."""
        into = STATE / "audio"
        into.mkdir(parents=True, exist_ok=True)
        path = into / f"{_slug(self.title)}-{index:03d}.mp3"
        if not path.exists():
            print(f"  fetching chapter {index + 1}...")
            path.write_bytes(_get(self.chapters[index]["url"], binary=True,
                                  timeout=180))
        return path

    def get_ready(self, index: int) -> None:
        """Fetch a chapter ahead of time, so the join is silent."""
        if 0 <= index < len(self.chapters):
            try:
                self._file(index)
            except Exception as error:
                print(f"[books] couldn't fetch ahead: {type(error).__name__}")


class Printed:
    """A Gutenberg text, read by Piper. Chapters found in the text itself."""

    kind = "printed"

    # Gutenberg is not consistent, but it is not chaotic either.
    HEADING = re.compile(
        r"^\s*(chapter|part|book|act|letter|scene)\s+"
        r"([0-9]+|[ivxlcdm]+|[a-z]+)\b.*$", re.IGNORECASE | re.MULTILINE)

    def __init__(self, entry: dict):
        self.title = entry["title"]
        self.author = entry.get("by", "")
        self.entry = entry
        self.at = 0
        self.frame = 0       # Which piece of text, not which audio frame.
        self._chapters: list[str] | None = None

    def how_long(self) -> str:
        return f"{len(self.text_chapters())} chapters"

    def chapter_name(self) -> str:
        first = self.text_chapters()[self.at].strip().split("\n")[0]
        return first[:60] or f"Chapter {self.at + 1}"

    @property
    def chapters(self):
        return self.text_chapters()

    def text_chapters(self) -> list[str]:
        if self._chapters is not None:
            return self._chapters

        query = urllib.parse.urlencode({
            "dataset": DATASET, "config": "default", "split": "train",
            "offset": str(self.entry["at"]), "length": "1"})
        row = json.loads(_get(f"{ROWS}?{query}", timeout=120))
        text = row["rows"][0]["row"]["TEXT"]
        text = _without_boilerplate(text)

        marks = [m.start() for m in self.HEADING.finditer(text)]
        if len(marks) < 3:
            # No headings worth trusting. Cut it into readable lengths so
            # "next chapter" still means something.
            step = 20_000
            parts = [text[i:i + step] for i in range(0, len(text), step)]
        else:
            edges = [0] + marks + [len(text)]
            parts = [text[a:b] for a, b in zip(edges, edges[1:]) if b > a]
        self._chapters = [p for p in parts if len(p.strip()) > 200]
        return self._chapters

    def blocks(self):
        """Piper reading the current chapter, a piece at a time."""
        import tts

        pieces = _sentences(self.text_chapters()[self.at])
        for number, piece in enumerate(pieces):
            if number < self.frame:
                continue
            for rate, audio in tts.spoken_pieces(piece):
                yield rate, audio
            self.frame = number + 1

    def get_ready(self, index: int) -> None:
        self.text_chapters()


def _without_boilerplate(text: str) -> str:
    """Drop Gutenberg's licence headers, which nobody wants read aloud."""
    start = re.search(r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*",
                      text, re.IGNORECASE | re.DOTALL)
    if start:
        text = text[start.end():]
    end = re.search(r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG",
                    text, re.IGNORECASE)
    if end:
        text = text[:end.start()]
    return text.strip()


def _sentences(chapter: str) -> list[str]:
    """A chapter as things to synthesise, a paragraph or so at a time."""
    clean = re.sub(r"\s*\n\s*", " ", chapter).strip()
    clean = re.sub(r"\s{2,}", " ", clean)
    out, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", clean):
        if len(current) + len(sentence) > 400 and current:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


def _saved_place(title: str) -> dict | None:
    """Find the remembered place for a title, however it was filed."""
    wanted = {_slug(g) for g in _title_guesses(title)}
    for slug in wanted:
        path = STATE / f"{slug}.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                return None

    # Nothing filed under that name. Look at what is saved and see if any
    # of it is plainly the same book.
    words = {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2}
    if not words:
        return None
    for path in STATE.glob("*.json"):
        try:
            saved = json.loads(path.read_text())
        except Exception:
            continue
        theirs = {w for w in re.findall(r"[a-z0-9]+", saved.get("title", "").lower())
                  if len(w) > 2}
        if theirs and (words <= theirs or theirs <= words):
            return saved
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


# --- reading it out loud ----------------------------------------------------


def _play() -> None:
    """Read, block by block, until stopped — pausing when spoken over.

    The device is opened and closed around each run rather than held for a
    whole chapter, because the microphone array allows one stream at a time
    and a story that held it for twenty seven minutes would be a speaker
    that could not answer for twenty seven minutes.
    """
    import sounddevice as sd

    import tts

    saved = 0.0
    while not _stop.is_set():
        _go.wait()
        if _stop.is_set():
            break

        book = _reader
        if book is None:
            break

        try:
            stream = None
            rate_open = None
            for rate, audio in book.blocks():
                if _stop.is_set() or not _go.is_set():
                    break
                if stream is None or rate_open != rate:
                    if stream is not None:
                        stream.stop()
                        stream.close()
                    device = tts.find_output_device(config.OUTPUT_DEVICE)
                    playable = tts.playable_rate(device, rate)
                    stream = sd.OutputStream(samplerate=playable, channels=1,
                                             dtype="int16", device=device)
                    stream.start()
                    rate_open = rate
                    resample_to = playable
                stream.write(np.ascontiguousarray(
                    tts._at_rate(audio, rate, resample_to)))
                if time.monotonic() - saved > SAVE_EVERY:
                    remember()
                    saved = time.monotonic()
            else:
                # The chapter finished on its own — move to the next one.
                if not _turn_page():
                    say_later("That's the end of the book.")
                    break
        except Exception as error:
            print(f"[books] {type(error).__name__}: {error}")
            say_later("I lost my place in that book, sorry.")
            break
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
        remember()


def _turn_page() -> bool:
    """Move to the next chapter. False if there isn't one."""
    book = _reader
    if book is None or book.at + 1 >= len(book.chapters):
        return False
    book.at += 1
    book.frame = 0
    remember()
    print(f"  chapter {book.at + 1}: {book.chapter_name()}")
    threading.Thread(target=book.get_ready, args=(book.at + 1,),
                     daemon=True).start()
    return True


def say_later(words: str) -> None:
    """Say something from the reading thread, without holding anything up."""
    def talk():
        import tts
        tts.speak(words)
    threading.Thread(target=talk, daemon=True).start()


def _start() -> None:
    """Get the reading thread going, after any previous one has let go.

    Waiting for the old thread is not optional. It closes the sound device
    on its way out, and starting a new one first means two threads opening
    the same device — which on the microphone array is "File descriptor in
    bad state" and no book at all. That is what happened when a book was
    stopped and immediately restarted, which is exactly what resuming is.
    """
    global _thread
    old = _thread
    if old is not None and old.is_alive():
        _stop.set()
        _go.set()
        old.join(timeout=5.0)

    _stop.clear()
    _go.set()
    _thread = threading.Thread(target=_play, daemon=True)
    _thread.start()


def hold() -> None:
    """Stop reading for a moment — somebody is talking."""
    _go.clear()


def carry_on() -> None:
    """Start reading again where it stopped."""
    if _reader is not None and not _stop.is_set():
        _go.set()
        _start()


sounds.also_pause(hold, carry_on)


# --- remembering the place --------------------------------------------------


def remember() -> None:
    """Write down where we are. Cheap, and done often."""
    book = _reader
    if book is None:
        return
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / f"{_slug(book.title)}.json").write_text(json.dumps({
            "title": book.title, "author": book.author, "kind": book.kind,
            "at": book.at, "frame": book.frame,
            "chapters": (book.chapters if book.kind == "spoken" else None),
            "entry": getattr(book, "entry", None),
        }))
    except Exception as error:
        print(f"[books] couldn't save the place: {error}")


def recall(title: str):
    """The book and place from last time, if we've read this one before.

    Matched loosely on purpose. The place is filed under the title the
    library gave it, and somebody asks for the title they know — LibriVox
    files A Tale of Two Cities as "Tale of Two Cities", so an exact lookup
    finds nothing and the book starts again from chapter one, which is the
    single most annoying thing this could get wrong.
    """
    saved = _saved_place(title)
    if saved is None:
        return None

    if saved["kind"] == "spoken" and saved.get("chapters"):
        book = Spoken(saved["title"], saved.get("author", ""),
                      saved["chapters"])
    elif saved.get("entry"):
        book = Printed(saved["entry"])
    else:
        return None
    book.at = saved.get("at", 0)
    book.frame = saved.get("frame", 0)
    return book


# --- what Claude can ask for ------------------------------------------------


_offered: list[dict] = []


@tools.tool(
    "Look for a book to read aloud. This does NOT start reading — it hands "
    "back what the libraries have, and you choose. Always call this first.\n\n"
    "Look at what comes back before picking. A title match is not the book: "
    "there are usually several recordings of a well known story, of very "
    "different lengths, and among them an abridgement, a sequel, a parody, "
    "or a different author's book of the same name. Judge by the author, "
    "the length and the subjects. Prefer one read by a person over one read "
    "by the computer, and prefer a full-length recording over a short one "
    "unless they asked for something short.\n\n"
    "If one is clearly right, call play_book straight away in the same "
    "breath. Only ask them which they want when you genuinely cannot tell "
    "the difference from what you can see.",
    properties={
        "title": {
            "type": "string",
            "description": "The book to look for. If they just asked for a "
                           "story, choose something from a children's shelf "
                           "and search for that.",
        },
    },
    required=["title"],
    says="read books aloud and remember where you got to",
)
def find_book(title: str) -> str:
    global _offered
    title = title.strip()
    if not title:
        return "Which book?"

    _offered = search(title)
    if not _offered:
        return f"Nothing in either library matches {title}."

    lines = []
    for number, found in enumerate(_offered, 1):
        where = _saved_place(found["title"])
        how = ("read by a person" if found["kind"] == "spoken"
               else "no recording, I would read it")
        length = (f"{found['parts']} chapters, {found['hours']:.1f} hours"
                  if found["parts"] else "length unknown")
        line = (f"{number}. {found['title']} by {found['by'] or 'unknown'} "
                f"— {how}, {length}")
        if found.get("about"):
            line += f". About: {found['about'][:120]}"
        if where:
            line += (f". You have this one open at chapter "
                     f"{where.get('at', 0) + 1}")
        lines.append(line)
    return ("Choose one and call play_book with its number:\n"
            + "\n".join(lines))


@tools.tool(
    "Start reading one of the books find_book offered, by its number. If "
    "they have had this book before it carries on where they stopped, so "
    "say so rather than starting again. Reading begins as soon as you "
    "finish speaking, so keep the reply to one short sentence.",
    properties={
        "number": {
            "type": "integer",
            "description": "Which of the books from find_book, counting "
                           "from 1.",
        },
    },
    required=["number"],
)
def play_book(number: int) -> str:
    global _reader
    if not _offered:
        return "Look for a book first with find_book."
    if not 1 <= int(number) <= len(_offered):
        return f"There were only {len(_offered)} to choose from."

    chosen = _offered[int(number) - 1]
    remembered = recall(chosen["title"])
    try:
        book = remembered or _build(chosen)
    except Exception as error:
        print(f"[books] {type(error).__name__}: {error}")
        return "I couldn't open that one."

    with _lock:
        _reader = book
    _start()

    where = ""
    if remembered and (book.at or book.frame):
        where = f", carrying on from {book.chapter_name()}"
    voice = "read out loud" if book.kind == "spoken" else "read by me"
    return f"Starting {book.title}{where} — {book.how_long()}, {voice}."


@tools.tool("Stop reading the book. Use this for 'stop', 'that's enough', "
            "'stop reading' while a book is being read.")
def stop_reading() -> str:
    global _reader
    with _lock:
        if _reader is None:
            return "I'm not reading anything."
        remember()
        title, chapter = _reader.title, _reader.chapter_name()
        _stop.set()
        _go.set()   # Let the thread wake up and notice it should stop.
        reading_thread = _thread
        _reader = None
    if reading_thread is not None:
        # Wait for the device to be given back, so whatever happens next
        # isn't racing it.
        reading_thread.join(timeout=5.0)
    return f"Stopped {title}, in {chapter}. I'll remember the place."


@tools.tool(
    "Move about in the book being read: the next chapter, the one before, "
    "or a particular number.",
    properties={
        "where": {
            "type": "string",
            "description": "'next', 'back', or a chapter number like '4'.",
        },
    },
    required=["where"],
)
def change_chapter(where: str) -> str:
    book = _reader
    if book is None:
        return "I'm not reading anything."

    where = where.strip().lower()
    total = len(book.chapters)
    if where.startswith("next"):
        wanted = book.at + 1
    elif where.startswith(("back", "prev", "last")):
        wanted = book.at - 1
    elif where.strip("chapter ").strip().isdigit():
        wanted = int(where.strip("chapter ").strip()) - 1
    else:
        return f"I didn't understand {where!r}."

    if not 0 <= wanted < total:
        return f"There are {total} chapters, so I can't go there."

    # Stop where it is, move, and let the reading thread pick it up.
    hold()
    time.sleep(0.2)
    book.at, book.frame = wanted, 0
    remember()
    carry_on()
    threading.Thread(target=book.get_ready, args=(wanted + 1,),
                     daemon=True).start()
    return f"Chapter {wanted + 1}, {book.chapter_name()}."


@tools.tool("Say which book is being read and where it has got to.")
def what_book() -> str:
    book = _reader
    if book is None:
        return "I'm not reading anything just now."
    return (f"{book.title}, chapter {book.at + 1} of "
            f"{len(book.chapters)}: {book.chapter_name()}.")


def reading() -> bool:
    return _reader is not None


if __name__ == "__main__":
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        raise SystemExit

    if "--shelf" in sys.argv:
        found = [json.loads(l) for l in gzip.open(INDEX, "rt")]
        kids = [b for b in found if any("Children" in s for s in b["shelves"])]
        print(f"{len(kids)} books on a children's shelf. Some of them:\n")
        for book in kids[:30]:
            print(f"  {book['title'][:52]:54} {book['by'][:30]}")
        raise SystemExit

    print(find_book(" ".join(sys.argv[1:]) or "treasure island"))
    print(play_book(1))
    try:
        time.sleep(float(sys.argv[-1]) if sys.argv[-1].replace(".", "").isdigit()
                   else 30)
    except KeyboardInterrupt:
        pass
    print(stop_reading())
