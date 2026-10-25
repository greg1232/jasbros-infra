"""Keep the data, and tie each model to the version it was fitted on.

Not a thing you run. `train/relearn.py` calls this every time it retrains:
the dataset is committed first, the model is fitted, and the commit it came
from is written inside the model file. So `state/hey_claude_whisper.npz`
can always answer "what was I trained on", and the answer is a URL rather
than a date and a hope.

    dataset commit  ->  model, carrying that commit's sha
                    ->  uploaded as models/2026-08-23-a1b2c3d4.npz

Why Hugging Face rather than a folder
-------------------------------------
A dataset repo there is a git repository with large-file storage behind
it, so each retraining is a commit. That answers storage and versioning at
once: you can see what changed between two nights, and go back to exactly
the data a particular model was fitted on.

It also fixes something quietly wrong. The Pi keeps only the most recent
few hundred recordings — a rotating cap, so every new firing deletes an old
one — and the audio behind labels already given was being thrown away.
Archived, they are all kept; the Pi goes on holding only what it needs.

Private
-------
This is not the same as the recordings in `train/real/`. Those are four
people who sat down and said "hey Claude" into a microphone on purpose.
This is two second windows of a living room, caught whenever the detector
fired — dozens an hour with a television on — and it contains whatever was
being said in that room by whoever was in it. Nobody chose to record most
of it. The repository is created private and nothing here can make it
public.

Needs HF_TOKEN in .env, from https://huggingface.co/settings/tokens. A
fine-grained token with write access to this one repository is enough, and
is what I would use. Without it, retraining still works and simply says
the model isn't archived.
"""

import contextlib
import csv
import json
import os
import re
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

# Hugging Face's Xet backend hung a retrain here for good: 501 files reached
# finalize_ingestion, then nothing moved for twenty minutes — one socket left
# in CLOSE-WAIT, 1571 bytes sent of a hundred megabytes, and the whole
# learning loop sat behind the lock it was holding. The plain HTTP path is
# slower and finishes. Set HF_HUB_DISABLE_XET=0 in .env to try Xet again.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# How long any single upload may take before we stop waiting for it. Generous
# — a hundred megabytes over a home connection is minutes, not seconds — but
# finite, which is the whole point.
PATIENCE = int(os.environ.get("ARCHIVE_PATIENCE", "900"))

HERE = Path(__file__).resolve().parent
STATE = HERE.parent / "state"
REPO = "claude-speaker-room"

# What is worth keeping. The books and sound-effect caches are downloaded
# copies of other people's files and can always be fetched again; the wake
# log cannot.
KEEP = ("wakes", "enrolled", "metadata.csv", "README.md")

# The corpus the first model was ever fitted on, which lives in the project
# rather than in state/ and so was missing from the archive entirely: the
# dataset held everything the speaker had learned and nothing it had been
# taught. Uploaded once — they don't change — and skipped after that.
FOUNDING = (
    (HERE / "real" / "hey_claude", "recorded/wake_word",
     "people saying the wake word on purpose"),
    (HERE / "room", "recorded/room",
     "the room not saying it"),
)


def _config():
    sys.path.insert(0, str(HERE.parent / "src"))
    import config
    return config


def token() -> str:
    return _config().HF_TOKEN or os.environ.get("HF_TOKEN", "")


def where() -> str:
    """The folder this speaker's data goes in, and why there is one.

    Every speaker uploads to the same repository, and before this they all
    uploaded to the root of it. Two of them relearning at four in the
    morning meant whichever finished last replaced the other's wakes.jsonl,
    vectors.f16 and metadata.csv.

    Losing a night would have been the harmless version. vectors.f16 is
    row-aligned to wakes.jsonl, so a mixed pair puts every feature against
    somebody else's label — and the gate that decides whether to promote a
    model measures on that same pair, so it need not notice. The result is
    a model fitted on nonsense that scores well.
    """
    name = (_config().SPEAKER_NAME or "").strip().lower()
    return re.sub(r"[^a-z0-9_-]+", "-", name).strip("-") or "speaker"


def snapshot(say=print) -> str:
    """Commit the data as it stands. Returns the commit sha, or "".

    Called before fitting, so the sha names exactly what the model is
    about to be trained on rather than whatever the log looked like by the
    time the fitting finished.
    """
    if not token():
        say("  not archiving — no HF_TOKEN in .env")
        return ""
    if not (STATE / "wakes" / "wakes.jsonl").exists():
        return ""

    try:
        from huggingface_hub import HfApi

        counted = describe()
        readme(counted)
        api = HfApi(token=token())
        repo = f"{api.whoami()['name']}/{REPO}"
        api.create_repo(repo, repo_type="dataset", private=True,
                        exist_ok=True)
        say(f"  archiving {', '.join(f'{v} {k}' for k, v in counted.items())}")
        with patience(PATIENCE, "the dataset upload"):
            commit = api.upload_folder(
                folder_path=str(STATE), repo_id=repo, repo_type="dataset",
                path_in_repo=where(),
                allow_patterns=[f"{name}*" for name in KEEP]
                               + [f"{name}/**" for name in KEEP],
                commit_message=f"{where()}: data as of "
                               f"{datetime.now().astimezone():%Y-%m-%d %H:%M}",
            )
        card(api, repo, say)
        sha = getattr(commit, "oid", "") or ""
        say(f"  dataset committed as {sha[:8]} in {repo}/{where()}")
        keep_recordings(api, repo, say)
        return sha
    except Exception as error:
        say(f"  couldn't archive ({type(error).__name__}: {error})")
        return ""


@contextlib.contextmanager
def patience(seconds: int, what: str):
    """Put a wall clock on a network call.

    Everything below already runs inside `try: ... except Exception`, which
    is a defence against the upload *failing*. It is not a defence against
    the upload never returning, and that is what actually happened: no
    exception was ever raised, because nothing ever went wrong — it simply
    stopped. An alarm turns a hang into an exception the existing handler
    already knows what to do with.

    SIGALRM only lands on the main thread, so off it this does nothing but
    get out of the way.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def ring(signum, frame):
        raise TimeoutError(f"{what} got nowhere in {seconds}s")

    before = signal.signal(signal.SIGALRM, ring)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, before)


def keep_recordings(api, repo: str, say=print) -> None:
    """Upload the founding corpus, once.

    Seventy five megabytes of room, and eighty recordings of four people
    saying the phrase. They are what the shipped model was fitted on, and
    without them the archive can show what changed since but not what it
    started from.
    """
    there = set(api.list_repo_files(repo, repo_type="dataset"))
    for folder, where, what in FOUNDING:
        if not folder.is_dir():
            continue
        have = sum(1 for f in there if f.startswith(where + "/"))
        want = len(list(folder.glob("*.wav")))
        if have >= want or not want:
            continue
        say(f"  uploading {want - have} recordings of {what} (once)")
        try:
            with patience(PATIENCE, f"the {what} upload"):
                api.upload_folder(folder_path=str(folder), repo_id=repo,
                                  repo_type="dataset", path_in_repo=where,
                                  allow_patterns=["*.wav"],
                                  commit_message=f"The {what}")
        except Exception as error:
            say(f"    couldn't ({type(error).__name__}: {error})")


def keep_model(model: Path, sha: str, scores: dict, say=print) -> None:
    """Put the fitted model beside the data it came from."""
    if not token() or not sha:
        return
    try:
        from huggingface_hub import HfApi

        api = HfApi(token=token())
        repo = f"{api.whoami()['name']}/{REPO}"
        name = f"models/{datetime.now().astimezone():%Y-%m-%d}-{sha[:8]}.npz"
        with patience(PATIENCE, "the model upload"):
            api.upload_file(path_or_fileobj=str(model), path_in_repo=name,
                            repo_id=repo, repo_type="dataset",
                            commit_message=f"Model from {sha[:8]}: " + ", ".join(
                                f"{k} {v:.0%}" for k, v in scores.items()))
        say(f"  model kept as {name}")
    except Exception as error:
        say(f"  couldn't keep the model ({type(error).__name__})")


def card(api, repo: str, say=print) -> None:
    """The note at the root, explaining that there is more than one speaker.

    Each speaker's own README now lands inside its folder, so without this
    the front page of the dataset would be whichever one uploaded last,
    describing itself as though it were the whole thing.
    """
    text = f"""---
license: other
task_categories:
- audio-classification
tags:
- keyword-spotting
- wake-word
---

# Claude Speaker — what woke it

Every time a wake word fired, and every time it nearly did, as recorded by
reSpeaker XVF3800 arrays in one house. Kept so the detector can be
retrained on its own mistakes, and so every model can say which version of
this it was fitted on.

**One folder per speaker.** They share a repository and must not share a
path: `wakes/vectors.f16` is row-aligned to `wakes/wakes.jsonl`, so two
speakers writing to the same place would put one's features against the
other's labels — and the gate that decides whether to promote a model
measures on that same pair, so it need not notice.

    <speaker>/metadata.csv     one row per firing, lined up with its audio
    <speaker>/wakes/           the log, the feature vectors, and the audio
    <speaker>/enrolled/        somebody teaching it their voice

Shared, because they are not any one speaker's:

    recorded/wake_word/        eighty recordings of four people saying it
    recorded/room/             the room not saying it
    models/                    one per retraining, named for its dataset

Written by `train/archive.py` in
[greg1232/hey-claude](https://github.com/greg1232/hey-claude).
Last updated {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}.
"""
    handle, path = None, None
    try:
        import tempfile
        handle, path = tempfile.mkstemp(suffix=".md")
        Path(path).write_text(text)
        with patience(PATIENCE, "the dataset card"):
            api.upload_file(path_or_fileobj=path, path_in_repo="README.md",
                            repo_id=repo, repo_type="dataset",
                            commit_message="The front page")
    except Exception as error:
        say(f"  (couldn't update the dataset card: {type(error).__name__})")
    finally:
        if handle is not None:
            os.close(handle)
            if path:
                os.unlink(path)


def describe() -> dict:
    """Write a metadata table beside the recordings.

    The table is what makes this a dataset rather than a pile of files:
    Hugging Face's viewer reads metadata.csv and lines each row up with
    its audio, so the whole thing is browsable in a page.
    """
    index = STATE / "wakes" / "wakes.jsonl"
    rows: dict[int, dict] = {}
    for line in index.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if "n" in row:
            rows.setdefault(row["n"], {}).update(row)

    counted = {"firings": 0, "near misses": 0, "labelled": 0,
               "by a person": 0, "with audio": 0}
    table = []
    for number, row in sorted(rows.items()):
        clip = STATE / "wakes" / "audio" / f"{number:06d}.wav"
        counted["near misses" if row.get("near") else "firings"] += 1
        counted["labelled"] += "label" in row
        counted["by a person"] += row.get("by") == "person"
        counted["with audio"] += clip.exists()
        table.append({
            "file_name": f"wakes/audio/{number:06d}.wav" if clip.exists() else "",
            "n": number,
            "at": row.get("at", ""),
            "score": row.get("score", ""),
            "near_miss": int(bool(row.get("near"))),
            "repeated": int(bool(row.get("repeated"))),
            "label": row.get("label", ""),
            "labelled_by": row.get("by", "machine" if "label" in row else ""),
            "heard_next": row.get("heard", ""),
            "window_said": row.get("window", ""),
        })

    if table:
        with open(STATE / "metadata.csv", "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)
    return counted


def readme(counted: dict) -> None:
    (STATE / "README.md").write_text(f"""---
license: other
task_categories:
- audio-classification
tags:
- keyword-spotting
- wake-word
---

# Claude Speaker — what woke it

Every time the wake word fired, and every time it nearly did, as recorded
by a reSpeaker XVF3800 array in one living room. Kept so the detector can
be retrained on its own mistakes, and so that every model can say which
version of this it was fitted on.

Written by `train/archive.py` in
[greg1232/hey-claude](https://github.com/greg1232/hey-claude), from
`train/relearn.py`, each time it retrains.
Last updated {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}.

| | |
|---|---|
{chr(10).join(f'| {k} | {v} |' for k, v in counted.items())}

## What is here

- `metadata.csv` — one row per firing, lined up with its recording.
- `wakes/wakes.jsonl` — the log as the speaker writes it, append-only.
- `wakes/vectors.f16` — 768 float16 per firing, in row order. Whisper
  `tiny.en` encoder features, mean and max pooled over the first 100
  frames of a two second window, and what the model is actually fitted on.
  Free to keep: the encoder pass is what fired the wake word.
- `wakes/audio/` — the two seconds that fired, 16 kHz mono. The Pi keeps
  only the most recent few hundred; everything ever uploaded stays here.
- `enrolled/` — somebody teaching it their voice by repeating the phrase.
- `recorded/wake_word/` — eighty recordings of four people saying "hey
  Claude" on purpose, and `recorded/room/` — the room not saying it. This
  is the corpus the first model was fitted on, before the speaker had
  heard anything for itself.
- `models/` — one per retraining, named for the dataset commit it was
  fitted on. The same sha is inside the file, as `dataset`.

## Labels

`label` is 1 if that really was somebody saying "hey Claude".
`labelled_by` says whether a person listened to it or the machine worked it
out from what happened next. A person's answer overrides the machine's.
""")
