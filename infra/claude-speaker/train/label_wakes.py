"""Decide which of the logged wake-word firings were real, and which weren't.

The speaker writes down every firing (src/wake_log.py). This turns that pile
into labelled training data, using three things in order of how much they
cost:

  1. What happened next.
     Nobody said anything -> almost certainly a mistake. A question that
     got answered -> almost certainly real. The speaker works both of
     these out anyway in the course of answering, so this is free and it
     is the most reliable thing here.

     For a near miss, the equivalent is repetition: one that was followed
     within seconds by a real firing is somebody saying it again because
     the first go was missed. That is a labelled recall failure and nobody
     had to label it.

  2. What Whisper makes of the two seconds that fired — but only in one
     direction. Measured against 80 real recordings, tiny.en transcribes
     the wake word 5% of the time and base.en 16%. It hears "It's hot",
     "Take that", "Great class" — the right rhythm and roughly the right
     vowels, overruled by a language model for which "Claude" is rare and
     "take that" is common.

     So a window that does say Claude is strong evidence it was real, and
     a window that doesn't is no evidence at all. What the transcript is
     good for is the opposite case: Whisper handles ordinary English
     perfectly well, so a window that comes out as a fluent sentence of
     television is evidence the speaker was not being spoken to.

  3. Claude, for the ones still in doubt, told plainly how unreliable the
     window transcript is so it doesn't make the same mistake.

Nothing is thrown away and nothing is overwritten: labels are appended to
the same log, so a bad run can be relabelled and the audio is still there
to listen to.

    python train/label_wakes.py                  label everything new
    python train/label_wakes.py --no-claude      steps 1 and 2 only
    python train/label_wakes.py --show           what's labelled so far
"""

import argparse
import datetime
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import wake_log  # noqa: E402


def _now() -> str:
    """When a label was decided, for relearn.py's freshness test."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

# What the wake window sounds like when it really was the wake word. Whisper
# spells it a dozen ways — "hey Claude", "Hey, Cloud", "a clod" — so this
# looks for the shape of it rather than the spelling.
SOUNDS_RIGHT = re.compile(
    r"\b(clau?de?|cloud|clod|claud|klaud)\b", re.IGNORECASE)
SOUNDS_LIKE_HEY = re.compile(r"\b(hey|hi|hay|a|ok|okay)\b", re.IGNORECASE)

BATCH = 20


def transcribe_windows(firings: list[dict], size: str, most: int) -> None:
    """Add `window` — what Whisper heard in the two seconds that fired.

    Deliberately a bigger model than the speaker runs. The Pi listens with
    tiny.en because it has to keep up in real time on a quarter of a core;
    this runs later, on a laptop, with nothing waiting on it, so it can
    afford small.en or medium.en. That difference is the whole point of
    labelling offline — the judge should be better than the thing it is
    judging, or it only teaches the model its own mistakes.

    Claude can't listen to the audio itself: the API takes text, images and
    PDFs, and rejects audio outright. Tested, not assumed. So a transcript
    is the way in, and it is worth making it a good one.
    """
    from faster_whisper import WhisperModel

    todo = [f for f in firings
            if "window" not in f and (wake_log.AUDIO / f"{f['n']:06d}.wav").exists()]
    if most == 0:
        print("Not transcribing the wake windows (--windows 0).")
        return
    if len(todo) > most:
        # Oldest first: the recent ones will come round again tomorrow.
        todo = todo[:most]
    if not todo:
        return
    print(f"Transcribing {len(todo)} wake windows with {size}...", flush=True)
    model = WhisperModel(size, device="cpu", compute_type="int8")

    import wave

    import numpy as np

    began = time.monotonic()
    for done, firing in enumerate(todo, 1):
        if done % 10 == 0 or done == len(todo):
            each = (time.monotonic() - began) / done
            print(f"  {done}/{len(todo)}  {each:.1f}s each, "
                  f"about {each * (len(todo) - done) / 60:.0f} min left",
                  flush=True)
        path = wake_log.AUDIO / f"{firing['n']:06d}.wav"
        with wave.open(str(path), "rb") as handle:
            raw = handle.readframes(handle.getnframes())
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        # beam_size 5, not the 1 the speaker uses: accuracy matters here
        # and nobody is waiting. No VAD filter — a two second window is
        # mostly silence by design, and filtering it can leave nothing.
        segments, _ = model.transcribe(audio, language="en", beam_size=5)
        firing["window"] = " ".join(s.text.strip() for s in segments).strip()
        _append({"n": firing["n"], "window": firing["window"]})


def label_locally(firing: dict) -> tuple[int | None, str]:
    """Label from evidence already on the machine. None means ask Claude.

    Ordered by how much the evidence is worth, which is not the order it
    was in the first time: the window transcript looked like the strongest
    signal and is in fact the weakest, because Whisper only writes down
    "Claude" for one real wake word in six.
    """
    window = (firing.get("window") or "").strip()
    heard = (firing.get("heard") or "").strip()

    # A near miss is a window that didn't fire. There is no question after
    # it to go on, so the evidence is different.
    if firing.get("near"):
        if firing.get("repeated"):
            # Followed within seconds by a real wake word. Somebody said it,
            # nothing happened, and they said it again — so this was the
            # wake word and the detector missed it. The person repeating
            # themselves is the label.
            # The wording matters: relearn.py throws away labels carrying
            # the old text, from when this rule kept eight near misses per
            # firing instead of one. See wake_log.flush_near().
            return 1, "repeated within seconds, so this one was missed"
        if window and SOUNDS_RIGHT.search(window):
            return 1, "the window says Claude"
        return None, ""

    # Somebody spoke a real question and Claude answered it. This is the
    # strongest thing in the log and it costs nothing — the speaker worked
    # it out while answering.
    if firing.get("answered") and len(heard.split()) >= 3:
        return 1, "a real question followed"

    # Rare, because Whisper mostly can't spell it, but precise when it hits.
    if window and SOUNDS_RIGHT.search(window):
        return 1, "the window says Claude"

    # Nobody said anything afterwards. This is what almost every television
    # mistake looks like, and the window transcript is not why: a window
    # that doesn't say Claude tells us nothing, so this rests entirely on
    # the silence that followed.
    if not heard:
        return 0, "nobody said anything after it fired"

    return None, ""


def ask_claude(unsure: list[dict]) -> None:
    """Label the leftovers, twenty at a time."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    import anthropic

    import config

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    print(f"Asking Claude about {len(unsure)} uncertain firings...")

    for start in range(0, len(unsure), BATCH):
        batch = unsure[start:start + BATCH]
        listing = "\n".join(
            f"{f['n']}. at {f.get('at', '?')[11:16]}, score {f.get('score')}, "
            + ("this one did NOT fire (it scored below the line), so there "
               "is no question after it; "
               if f.get("near") else "")
            + f"the two seconds sounded like {(f.get('window') or '')!r}"
            + ("" if f.get("near")
               else f", and what came next was {(f.get('heard') or '')!r}")
            for f in batch)

        message = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=2000,
            system=(
                "You are labelling training data for the wake word of a "
                "voice assistant that listens for the phrase \"hey Claude\". "
                "It sits in a family living room with a television on.\n\n"
                "For each numbered firing, decide whether somebody really "
                "said the wake word to the speaker, or whether it fired by "
                "mistake on the television or on ordinary conversation.\n\n"
                "Read the two pieces of evidence very differently.\n\n"
                "What came NEXT is reliable. A real question, especially "
                "one addressed to an assistant, means somebody was talking "
                "to the speaker. Nothing at all usually means it fired on "
                "the television.\n\n"
                "The transcript of the two seconds that FIRED is close to "
                "worthless as evidence against. It comes from a tiny speech "
                "model that, measured on eighty real recordings, writes "
                "down the wake word only about one time in six: it hears "
                "\"It's hot\", \"Take that\", \"Great class\", \"Thank God\" "
                "— right rhythm, right vowels, wrong word, because "
                "\"Claude\" is rare and those phrases are common. So if it "
                "does say something like Claude, that is strong evidence "
                "the firing was real. If it does not, that is almost no "
                "evidence either way, and you must not treat it as a "
                "reason to say no.\n\n"
                "Where the window transcript IS useful is when it reads as "
                "a fluent, complete sentence of broadcast or overheard "
                "speech. That model handles ordinary English well, so that "
                "really is what was in the room.\n\n"
                "Answer with one JSON array and nothing else: "
                '[{"n": 12, "real": true, "why": "short reason"}, ...]. '
                "Include every number. If you genuinely cannot tell, leave "
                "that number out rather than guessing."),
            messages=[{"role": "user", "content": listing}],
        )

        text = "".join(b.text for b in message.content if b.type == "text")
        for verdict in _json_array(text):
            number = verdict.get("n")
            if number is None or "real" not in verdict:
                continue
            _append({"n": number, "labelled_at": _now(),
                     "label": int(bool(verdict["real"])),
                     "why": f"claude: {verdict.get('why', '')}"[:120]})
        print(f"  labelled {min(start + BATCH, len(unsure))}"
              f"/{len(unsure)}")


def _json_array(text: str) -> list[dict]:
    """Pull the array out of Claude's reply, however it wrapped it."""
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return []
    try:
        return json.loads(match.group(0))
    except ValueError:
        return []


def _append(row: dict) -> None:
    with open(wake_log.INDEX, "a") as handle:
        handle.write(json.dumps(row) + "\n")


def show() -> int:
    firings = wake_log.read()
    labelled = [f for f in firings if "label" in f]
    real = sum(f["label"] for f in labelled)
    print(f"{len(firings)} firings, {len(labelled)} labelled: "
          f"{real} real, {len(labelled) - real} mistakes")
    for firing in labelled[-25:]:
        mark = "REAL" if firing["label"] else "  no"
        print(f"  {mark} {firing.get('at', '')[11:19]} "
              f"{firing.get('score', 0):.3f} "
              f"window={(firing.get('window') or '')[:34]!r} "
              f"next={(firing.get('heard') or '')[:28]!r}")
        if firing.get("why"):
            print(f"       {firing['why']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--windows", type=int, default=200,
                        help="how many wake windows to transcribe. On a Pi "
                             "this is seconds each, so a backlog of "
                             "hundreds is a quarter of an hour with the "
                             "processor pinned. 0 to skip it")
    parser.add_argument("--whisper", default="small.en",
                        help="which Whisper to judge the wake window with. "
                             "Bigger than the Pi's tiny.en on purpose "
                             "(default: small.en)")
    parser.add_argument("--no-claude", action="store_true",
                        help="use only the free signals, don't call the API")
    parser.add_argument("--show", action="store_true",
                        help="print what has been labelled and stop")
    args = parser.parse_args()

    if args.show:
        return show()

    firings = wake_log.read()
    if not firings:
        raise SystemExit(f"Nothing logged yet. Looked in {wake_log.WHERE}")

    todo = [f for f in firings if "label" not in f]
    print(f"{len(firings)} firings logged, {len(todo)} unlabelled")
    if not todo:
        return show()

    transcribe_windows(todo, args.whisper, args.windows)

    unsure = []
    free = 0
    for firing in todo:
        label, why = label_locally(firing)
        if label is None:
            unsure.append(firing)
        else:
            _append({"n": firing["n"], "label": label, "why": why,
                     "labelled_at": _now()})
            free += 1
    print(f"  {free} settled without asking anyone, {len(unsure)} in doubt")

    if unsure and not args.no_claude:
        ask_claude(unsure)

    return show()


if __name__ == "__main__":
    raise SystemExit(main())
