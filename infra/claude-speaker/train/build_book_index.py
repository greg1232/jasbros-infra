"""Build the local index of every Gutenberg book, so the speaker can search.

Project Gutenberg itself is not usable from a program any more: gutenberg.org
answers automated requests with 503, and its own catalogue file times out at
504. Hugging Face carries the whole corpus and serves it off a CDN that
actually works — 48,284 English books in `sedthh/gutenberg_english` — but
its search and filter endpoints both return 500 on a dataset that size.

So: keep the titles here, and fetch only the book somebody asks for.

That is cheap because parquet is columnar. Each of the 37 files is about
340 MB, almost all of it the text of the books, and reading just the
METADATA column out of one over HTTP takes under two seconds — the reader
range-requests the bytes for that column and skips the rest. A minute for
the lot, and about 4 MB to keep.

Run this once. The result is committed, so nobody else has to.

    python train/build_book_index.py
"""

import gzip
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "models" / "gutenberg.jsonl.gz"
DATASET = "sedthh/gutenberg_english"
LISTING = "https://datasets-server.huggingface.co/parquet?dataset="


def _split(field, keep: int) -> list[str]:
    """Gutenberg's list-ish fields, which arrive as one semicolon string."""
    if not field:
        return []
    return [part.strip() for part in str(field).split(";") if part.strip()][:keep]


def parquet_files() -> list[dict]:
    url = LISTING + urllib.parse.quote(DATASET)
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)["parquet_files"]


def main() -> int:
    if {"-h", "--help"} & set(sys.argv):
        print(__doc__)
        return 0

    import fsspec
    import pyarrow.parquet as pq

    files = parquet_files()
    print(f"{len(files)} parquet files, "
          f"{sum(f['size'] for f in files) / 1e9:.1f} GB of books")

    handle = fsspec.filesystem("http")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    offset = 0   # Row number across the whole split, which is what /rows wants.
    written = 0

    with gzip.open(OUT, "wt", encoding="utf-8") as out:
        for number, entry in enumerate(files):
            with handle.open(entry["url"], "rb") as stream:
                table = pq.ParquetFile(stream).read(columns=["METADATA"])
            for raw in table.column("METADATA").to_pylist():
                try:
                    meta = json.loads(raw)
                except ValueError:
                    offset += 1
                    continue
                out.write(json.dumps({
                    # Where to ask for this book's text later.
                    "at": offset,
                    "id": meta.get("text_id"),
                    "title": (meta.get("title") or "").replace("\r\n", " ")[:200],
                    "by": (meta.get("authors") or "")[:120],
                    # Every one of these is a semicolon-separated string,
                    # not a list. Slicing one as a list gives you its first
                    # four characters, and a "bookshelf" called "i".
                    # Bookshelves are Gutenberg's own curation, and are how
                    # "read me a children's story" finds one.
                    "shelves": _split(meta.get("bookshelves"), 4),
                    "about": _split(meta.get("subjects"), 6),
                }) + "\n")
                offset += 1
                written += 1
            print(f"  {number + 1}/{len(files)}  {written:,} books  "
                  f"{time.monotonic() - began:.0f}s", flush=True)

    print(f"\nwrote {OUT} — {written:,} books, "
          f"{OUT.stat().st_size / 1e6:.1f} MB, "
          f"{time.monotonic() - began:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
