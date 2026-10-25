"""Watching the candy: the detector decides when to look, Claude looks.

Every second the small camera frame goes through the person detector on
gmk (vision.py) — cheap, local, nothing leaves the house. A tracker gives
each person an ID across frames, and IDs that turn up together are a
**group**. Claude is only asked at the moments that matter:

  - a new group has formed:       what are *they* dressed as (a crop of just
                                  them), and how many candies are there?
  - every few seconds while anyone's there:  how many candies now?
  - the box has sat alone a while:           how many, for a fresh baseline?

So the costume question is asked once per group, even when the next group
walks up before the last has left.

"Taken" is counted from when the earliest group still there arrived; the
allowance is two each for everyone there. A count has to be over twice
running before anyone is called out — a count made through somebody's arm
is not believed. Stills go to Anthropic's API and are kept nowhere
(docs/candy-watch.md).
"""

import base64
import io
import itertools
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

# Whether the watcher is switched on, kept on the pod's volume so a restart
# on Halloween night doesn't quietly switch it off.
STATE = Path(os.environ.get("STATE", "/data/state.json"))

MODEL = "claude-opus-5-5"
EACH = 2                        # candies each — the whole point

SYSTEM = (
    "You are the eyes of a Halloween candy station. A camera looks at a "
    "candy box or bowl that trick-or-treaters help themselves from, and the "
    "house's speaker talks to them based on what you report. Report only "
    "what the pictures show. Costumes are said out loud to the children, so "
    "describe them the way an excited neighbour would — short and kind "
    "(\"scary witch\", \"tiny astronaut\", \"a skeleton and a pirate\").")

COUNT = (
    "- candies: how many individual pieces of candy are in the candy box or "
    "bowl right now. Count carefully. If you can't see the box or can't "
    "count it, use -1.\n"
    "- blocked: true if a hand, arm, bag or body is in or over the box, so "
    "the count may be wrong.")

ARRIVAL = (
    "Two pictures from the candy camera. The first is cropped to some "
    "people who have just walked up; the second is the whole frame.\n"
    "- costume: what the people in the FIRST picture are dressed as, all of "
    "them together in at most 8 words, written to follow \"I see you're\" — "
    "so with its article: \"a scary witch\", \"Darth Vader\", \"a skeleton "
    "and a pirate\". Empty if they're in everyday clothes or you can't see "
    "them well enough to tell — never guess.\n"
    "- people: how many people are in the first picture.\n"
    "From the SECOND picture:\n" + COUNT)

RECOUNT = "One picture from the candy camera.\n" + COUNT

COUNT_SCHEMA = {
    "type": "object",
    "properties": {"candies": {"type": "integer"},
                   "blocked": {"type": "boolean"}},
    "required": ["candies", "blocked"],
    "additionalProperties": False,
}
ARRIVAL_SCHEMA = {
    "type": "object",
    "properties": {"costume": {"type": "string"},
                   "people": {"type": "integer"},
                   **COUNT_SCHEMA["properties"]},
    "required": ["costume", "people", "candies", "blocked"],
    "additionalProperties": False,
}

LONGEST_SIDE = 1568             # what Claude reads images at; more is waste


def shrink(jpeg: bytes) -> bytes:
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    image.thumbnail((LONGEST_SIDE, LONGEST_SIDE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88)
    return out.getvalue()


def crop(jpeg: bytes, box: list[float], margin: float = 0.15) -> bytes:
    """`box` (0–1 of the frame) and a margin around it, from a full still."""
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    x1, y1, x2, y2 = box
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    w, h = image.size
    part = image.crop((int(max(0, x1 - mx) * w), int(max(0, y1 - my) * h),
                       int(min(1, x2 + mx) * w), int(min(1, y2 + my) * h)))
    out = io.BytesIO()
    part.save(out, "JPEG", quality=90)
    return out.getvalue()


class Eyes:
    """Claude, asked one question about one or two stills."""

    def __init__(self, client=None, model: str = MODEL):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client, self.model = client, model
        self.looks = 0
        self.last: dict = {}
        self.error = ""

    def ask(self, stills: list[bytes], question: str, schema: dict
            ) -> dict | None:
        import anthropic

        content = [{"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(shrink(s)).decode()}}
            for s in stills]
        content.append({"type": "text", "text": question})
        started = time.time()
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=4000,
                # Short look-and-answer questions: low effort is the speed
                # and cost lever on this model. Thinking can't be off on it.
                output_config={"effort": "low",
                               "format": {"type": "json_schema",
                                          "schema": schema}},
                # If a frame is ever declined, re-run it on the model
                # Anthropic recommends rather than going blind.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM,
                messages=[{"role": "user", "content": content}])
        except anthropic.RateLimitError:
            self.error = "Claude is busy (rate limited)"
            return None
        except anthropic.APIStatusError as error:
            self.error = f"Claude said {error.status_code}"
            print(f"[candy] Claude error {error.status_code}: {error.message}",
                  flush=True)
            return None
        except anthropic.APIConnectionError:
            self.error = "can't reach Claude"
            return None
        self.looks += 1
        if response.stop_reason == "refusal":
            self.error = "Claude declined to look at that frame"
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            seen = json.loads(text)
        except ValueError:
            self.error = "Claude's answer wasn't JSON"
            return None
        seen["seconds"] = round(time.time() - started, 1)
        self.last, self.error = seen, ""
        print(f"[candy] Claude saw {seen}", flush=True)
        return seen


# --- who is who, frame to frame -----------------------------------------------

@dataclass
class Track:
    id: int
    box: list[float]            # 0–1 of the frame
    first_at: float
    last_at: float
    hits: int = 1
    group: int | None = None

    @property
    def confirmed(self) -> bool:
        return self.hits >= 2


class Tracker:
    """Each person keeps an ID while they're in view.

    Matched frame to frame by how much their boxes overlap — or, since a
    frame a second is a long time for a walking child, by a nearby centre
    and a similar size when they barely overlap. Two sightings make an ID
    real; four seconds unseen ends it (the detector misses someone bending
    over now and then, and that shouldn't make them a newcomer).
    """

    def __init__(self, gone_seconds: float = 4, near: float = 0.12):
        self.gone_seconds, self.near = gone_seconds, near
        self.tracks: dict[int, Track] = {}
        self.ids = itertools.count(1)

    def update(self, boxes: list[list[float]], now: float) -> list[Track]:
        """Match this frame's boxes; returns tracks confirmed just now."""
        pairs = []
        for tid, track in self.tracks.items():
            for i, box in enumerate(boxes):
                score = _likeness(track.box, box, self.near)
                if score > 0:
                    pairs.append((score, tid, i))
        used_t, used_b, confirmed = set(), set(), []
        for score, tid, i in sorted(pairs, reverse=True):
            if tid in used_t or i in used_b:
                continue
            used_t.add(tid)
            used_b.add(i)
            track = self.tracks[tid]
            was = track.confirmed
            track.box, track.last_at, track.hits = boxes[i], now, track.hits + 1
            if track.confirmed and not was:
                confirmed.append(track)
        for i, box in enumerate(boxes):
            if i not in used_b:
                tid = next(self.ids)
                self.tracks[tid] = Track(tid, box, now, now)
        for tid, track in list(self.tracks.items()):
            unseen = now - track.last_at
            # A one-sighting track that isn't seen again was noise.
            if (not track.confirmed and tid not in used_t and unseen > 0
                    and track.first_at != now) or unseen >= self.gone_seconds:
                del self.tracks[tid]
        return confirmed

    def present(self) -> list[Track]:
        return [t for t in self.tracks.values() if t.confirmed]


def _likeness(a: list[float], b: list[float], near: float) -> float:
    overlap = _iou(a, b)
    if overlap >= 0.1:
        return overlap
    (ax, ay), (bx, by) = _centre(a), _centre(b)
    distance = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
    size = _area(a) / max(_area(b), 1e-9)
    if distance < near and 0.5 <= size <= 2:
        return 0.09 * (1 - distance / near)     # always below a real overlap
    return 0.0


def _iou(a, b) -> float:
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = w * h
    return inter / max(_area(a) + _area(b) - inter, 1e-9)


def _area(a) -> float:
    return max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])


def _centre(a) -> tuple[float, float]:
    return (a[0] + a[2]) / 2, (a[1] + a[3]) / 2


# --- groups, and what to do about them ----------------------------------------

@dataclass
class Group:
    id: int
    started_at: float
    tracks: set[int] = field(default_factory=set)
    costume: str = ""
    kids: int = 0
    baseline: int | None = None     # candies when they arrived
    looked: bool = False            # the one costume look has been made


class Watcher:
    """The groups at the candy box, from the tracker's say-so.

    `step()` is called with each small frame. It is cheap unless it decides
    to look; looks and anything the speaker says run off to one side, so the
    detector keeps watching while Claude thinks and the speaker talks.
    """

    def __init__(self, camera, detector, eyes, interaction, clock=time.time,
                 forming_seconds: float = 3, gone_seconds: float = 4,
                 recount_every: float = 5, recounts_per_group: int = 6,
                 baseline_every: float = 1800, put_back_wait: float = 60,
                 background=None, state: Path | None = STATE):
        self.camera, self.detector, self.eyes = camera, detector, eyes
        self.interaction, self.clock = interaction, clock
        self.tracker = Tracker(gone_seconds)
        self.forming_seconds = forming_seconds
        self.recount_every, self.recounts_per_group = (recount_every,
                                                       recounts_per_group)
        self.baseline_every, self.put_back_wait = (baseline_every,
                                                   put_back_wait)
        self.background = background or _thread
        self.state = state
        self.on = self._load_on()
        self.groups: dict[int, Group] = {}
        self.group_ids = itertools.count(1)
        self.calling_out = False
        # Each set of groups is called out once: the same kids standing
        # there with the same candy shouldn't hear the gangster on repeat.
        self.called_out: set[int] = set()
        self.baseline: int | None = None        # the box, alone
        self.baseline_at = 0.0
        self.count: int | None = None           # the latest believable count
        self.over = 0                           # counts in a row over
        self.recounts = 0
        self.last_look_at = 0.0
        self.looking = False
        self.events: list[str] = []
        self.lock = threading.RLock()

    # -- every frame

    def step(self, small: bytes) -> None:
        found = self.detector.people(small)
        size = _size(small)
        boxes = [_normal(p["box"], size) for p in found]
        now = self.clock()
        with self.lock:
            for track in self.tracker.update(boxes, now):
                if self.on:
                    self._join(track, now)
            self._end_groups()
            if self.eyes is None or not self.on:    # people only: no Claude,
                return                              # nothing said
            if not self.looking:
                self._next_look(now)

    def _join(self, track: Track, now: float) -> None:
        forming = [g for g in self.groups.values()
                   if now - g.started_at < self.forming_seconds]
        if forming:
            group = forming[-1]
        else:
            # Their starting point is the box as it was before they got
            # there — not whatever is left by the time Claude first looks.
            group = Group(next(self.group_ids), now,
                          baseline=self.count if self.count is not None
                          else self.baseline)
            self.groups[group.id] = group
            self.recounts = 0
            self._event(f"group {group.id} arriving")
        group.tracks.add(track.id)
        group.kids = max(group.kids, len(group.tracks))
        track.group = group.id

    def _end_groups(self) -> None:
        live = set(self.tracker.tracks)
        for gid, group in list(self.groups.items()):
            group.tracks &= live
            if not group.tracks:
                del self.groups[gid]
                self._event(f"group {gid} has gone")
                if not self.groups:
                    self.baseline_at = 0.0      # recount the box, alone
                    self.over = 0

    def _next_look(self, now: float) -> None:
        ready = [g for g in self.groups.values() if not g.looked
                 and now - g.started_at >= self.forming_seconds]
        if ready:
            group = ready[0]
            group.looked = True
            self._look_soon(self._arrival, group)
        elif self.groups:
            # Counting needs a box Claude can see; until one has been
            # counted, recounts would only ever come back "can't see it".
            if (self.count is not None or self.baseline is not None) and (
                    now - self.last_look_at >= self.recount_every
                    and self.recounts < self.recounts_per_group
                    and not self.calling_out):
                self.recounts += 1
                self._look_soon(self._recount)
        elif not self.tracker.tracks and (
                self.baseline is None
                or now - self.baseline_at >= self.baseline_every):
            self._look_soon(self._alone)

    # -- the switch

    def switch(self, on: bool) -> None:
        """On: greet, count, call out. Off: just watch, ask Claude nothing.

        Switching on starts afresh — whoever is already standing there
        isn't greeted until they leave and come back, so switching it on
        from the yard doesn't welcome the person doing it.
        """
        with self.lock:
            if on == self.on:
                return
            self.on = on
            self.groups.clear()
            for track in self.tracker.tracks.values():
                track.group = -1        # here before: not a newcomer
            self.over, self.recounts = 0, 0
            self.called_out.clear()
            self._event("switched on" if on else "switched off")
            self._save_on()

    def _load_on(self) -> bool:
        try:
            return bool(json.loads(self.state.read_text()).get("on"))
        except (OSError, ValueError, AttributeError):
            return False                # off unless it was left on

    def _save_on(self) -> None:
        if self.state is None:
            return
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            self.state.write_text(json.dumps({"on": self.on}))
        except OSError as error:
            print(f"[candy] couldn't save the switch: {error}", flush=True)

    # -- the questions, to one side

    def _look_soon(self, how, *args) -> None:
        self.looking = True
        self.last_look_at = self.clock()

        def look():
            try:
                how(*args)
            finally:
                self.looking = False
        self.background(look)

    def _arrival(self, group: Group) -> None:
        still = self.camera.still()
        if not still:
            return
        # Find them in this very picture. The small frame the tracker saw is
        # a second or two older, and someone walking through has moved on
        # since — cropping where they *were* showed Claude empty yard.
        in_still = [_normal(p["box"], _size(still))
                    for p in self.detector.people(still)]
        tracks = [self.tracker.tracks[t].box for t in group.tracks
                  if t in self.tracker.tracks]
        theirs = [b for b in in_still
                  if any(_likeness(b, t, 0.2) > 0 for t in tracks)] or in_still
        if not theirs:
            with self.lock:
                self._event(f"group {group.id} gone before a look")
            return
        around = [min(b[0] for b in theirs), min(b[1] for b in theirs),
                  max(b[2] for b in theirs), max(b[3] for b in theirs)]
        seen = self.eyes.ask([crop(still, around), still], ARRIVAL,
                             ARRIVAL_SCHEMA)
        if not seen:
            return
        with self.lock:
            group.costume = seen["costume"].strip()
            group.kids = max(group.kids, seen["people"])
            if self._believable(seen):
                self.count = seen["candies"]
                if group.baseline is None:      # nobody had counted it yet
                    group.baseline = self.count
            self._event(f"group {group.id}: {group.kids} × "
                        f"{group.costume or 'no costume seen'}")
        self.background(self.interaction.welcome, group.costume)

    def _recount(self) -> None:
        still = self.camera.still()
        seen = self.eyes.ask([still], RECOUNT, COUNT_SCHEMA) if still else None
        if not seen or not self._believable(seen):
            return
        with self.lock:
            self.count = seen["candies"]
            taken, allowed = self.taken, self.allowed
            if taken is None or taken <= allowed:
                self.over = 0
                return
            self.over += 1
            self._event(f"{taken} taken, {allowed} allowed")
            here = set(self.groups)
            if (self.over >= 2 and not self.calling_out
                    and not here <= self.called_out):
                self.calling_out = True
                self.called_out |= here
                who = " and ".join(g.costume for g in self.groups.values()
                                   if g.costume)
                self.background(self._call_out, who)

    def _alone(self) -> None:
        still = self.camera.still()
        seen = self.eyes.ask([still], RECOUNT, COUNT_SCHEMA) if still else None
        if not seen or not self._believable(seen):
            return
        with self.lock:
            if self.groups:
                return                  # somebody turned up meanwhile
            self.baseline = self.count = seen["candies"]
            self.baseline_at = self.clock()
            self._event(f"{self.baseline} candies in the box")

    def _call_out(self, who: str) -> None:
        def put_back() -> bool:
            still = self.camera.still()
            seen = self.eyes.ask([still], RECOUNT, COUNT_SCHEMA) \
                if still else None
            if seen and self._believable(seen):
                with self.lock:
                    self.count = seen["candies"]
                    return (self.taken or 0) <= self.allowed
            return False

        try:
            self.interaction.call_out(who, put_back, self.put_back_wait)
        finally:
            with self.lock:
                self.calling_out, self.over = False, 0
                self._event("call-out done")

    # -- the sums

    @staticmethod
    def _believable(seen: dict) -> bool:
        return seen["candies"] >= 0 and not seen["blocked"]

    @property
    def allowed(self) -> int:
        return EACH * max(1, sum(g.kids for g in self.groups.values()))

    @property
    def taken(self) -> int | None:
        """Since the earliest group still here arrived."""
        firsts = sorted(self.groups.values(), key=lambda g: g.started_at)
        start = next((g.baseline for g in firsts if g.baseline is not None),
                     self.baseline)
        if start is None or self.count is None:
            return None
        return start - self.count

    def _event(self, text: str) -> None:
        self.events = (self.events + [
            f"{time.strftime('%H:%M:%S', time.localtime(self.clock()))} "
            f"{text}"])[-12:]
        print(f"[candy] {text}", flush=True)

    def status(self) -> dict:
        with self.lock:
            return {
                "on": self.on,
                "people": [{"box": t.box, "id": t.id,
                            "group": t.group if t.group and t.group > 0
                            else None}
                           for t in self.tracker.present()],
                "groups": [{"id": g.id, "costume": g.costume, "kids": g.kids,
                            "baseline": g.baseline}
                           for g in self.groups.values()],
                "calling_out": self.calling_out,
                "allowed": self.allowed, "baseline": self.baseline,
                "count": self.count, "taken": self.taken,
                "detector_ms": round(getattr(self.detector, "last_ms", 0), 1),
                "claude": self.eyes is not None,
                "claude_looks": self.eyes.looks if self.eyes else 0,
                "claude_last": self.eyes.last if self.eyes else {},
                "claude_error": self.eyes.error if self.eyes else "",
                "events": self.events}


def _thread(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


def _size(jpeg: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(jpeg)).size


def _normal(box: list[float], size: tuple[int, int]) -> list[float]:
    w, h = size
    x1, y1, x2, y2 = box
    return [round(max(0.0, x1) / w, 4), round(max(0.0, y1) / h, 4),
            round(min(w, x2) / w, 4), round(min(h, y2) / h, 4)]
