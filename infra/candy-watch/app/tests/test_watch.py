"""The tracker and the watcher, with a fake detector, Claude and clock.

    python -m unittest discover -s tests
"""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

import watch  # noqa: E402


def jpeg(w=640, h=360) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h)).save(out, "JPEG")
    return out.getvalue()


FRAME, STILL = jpeg(), jpeg(1280, 720)


def person(x, y=0.2, w=0.1, h=0.6):
    """A person at x (0–1), as the detector reports it in 640×360 pixels."""
    return {"box": [x * 640, y * 360, (x + w) * 640, (y + h) * 360],
            "score": 0.9}


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


class Detector:
    def __init__(self):
        self.next, self.last_ms = [], 22.0
        self.in_still = None            # None: the still shows what frames do

    def people(self, frame):
        if frame is STILL and self.in_still is not None:
            return list(self.in_still)
        return list(self.next)


class Camera:
    def still(self):
        return STILL


class Eyes:
    """Claude, from a script. Arrival and count questions are answered from
    separate lists, so a test says what each kind of look sees."""

    def __init__(self, costumes=(), counts=()):
        self.costumes, self.counts = list(costumes), list(counts)
        self.questions, self.looks, self.last, self.error = [], 0, {}, ""

    def ask(self, stills, question, schema):
        self.looks += 1
        if question is watch.ARRIVAL:
            self.questions.append(("arrival", len(stills)))
            costume, people = self.costumes.pop(0)
            return {"costume": costume, "people": people}
        self.questions.append(("count", len(stills)))
        count = self.counts.pop(0) if self.counts else self.last_count
        self.last_count = count
        return {"candies": count[0], "blocked": count[1]}

    def joke(self, costume):
        self.jokes = getattr(self, "jokes", []) + [costume]
        return getattr(self, "joke_text", None) or f"a joke about {costume}"

    last_count = (30, False)


class Interaction:
    def __init__(self):
        self.welcomed, self.called_out, self.farewells = [], [], []

    def welcome(self, costume):
        self.welcomed.append(costume)

    def farewell(self, joke):
        self.farewells.append(joke)

    def call_out(self, who, put_back, wait):
        self.called_out.append(who)
        self.put_back = put_back


def now(target, *args):         # "background" work, straight away
    target(*args)


class Tracking(unittest.TestCase):
    def setUp(self):
        self.tracker, self.t = watch.Tracker(), 1000.0

    def frame(self, *boxes):
        self.t += 1
        return self.tracker.update([list(b) for b in boxes], self.t)

    def test_two_sightings_make_an_id(self):
        self.assertEqual(self.frame((0.1, 0.2, 0.2, 0.8)), [])
        confirmed = self.frame((0.11, 0.2, 0.21, 0.8))
        self.assertEqual([t.id for t in confirmed], [1])

    def test_walking_keeps_the_same_id(self):
        x = 0.05
        ids = set()
        for _ in range(10):
            self.frame((x, 0.2, x + 0.1, 0.8))
            ids |= {t.id for t in self.tracker.present()}
            x += 0.06                   # a brisk walk, a frame a second
        self.assertEqual(ids, {1})

    def test_a_missed_frame_or_two_isnt_a_new_person(self):
        self.frame((0.1, 0.2, 0.2, 0.8))
        self.frame((0.1, 0.2, 0.2, 0.8))
        self.frame()
        self.frame()
        self.frame((0.1, 0.2, 0.2, 0.8))
        self.assertEqual([t.id for t in self.tracker.present()], [1])

    def test_four_seconds_unseen_is_gone(self):
        self.frame((0.1, 0.2, 0.2, 0.8))
        self.frame((0.1, 0.2, 0.2, 0.8))
        for _ in range(4):
            self.frame()
        self.assertEqual(self.tracker.tracks, {})

    def test_one_sighting_is_noise(self):
        self.frame((0.1, 0.2, 0.2, 0.8))
        self.frame()
        self.frame()
        self.assertEqual(self.tracker.tracks, {})

    def test_two_people_side_by_side_are_two(self):
        for _ in range(3):
            self.frame((0.1, 0.2, 0.2, 0.8), (0.5, 0.2, 0.6, 0.8))
        self.assertEqual(len(self.tracker.present()), 2)


class Groups(unittest.TestCase):
    def setUp(self):
        self.clock, self.detector = Clock(), Detector()
        self.interaction = Interaction()

    def watcher(self, costumes=(), counts=()):
        self.eyes = Eyes(costumes, counts)
        w = watch.Watcher(Camera(), self.detector, self.eyes,
                          self.interaction, clock=self.clock,
                          background=now, state=None)
        w.switch(True)
        return w

    def run_frames(self, w, people, n):
        self.detector.next = people
        for _ in range(n):
            w.step(FRAME)
            self.clock.now += 1

    def test_an_empty_yard_only_counts_the_box_now_and_then(self):
        w = self.watcher(counts=[(30, False)])
        self.run_frames(w, [], 600)
        self.assertEqual(self.eyes.questions, [("count", 1)])
        self.assertEqual(w.baseline, 30)
        self.run_frames(w, [], 1800)
        self.assertEqual(len(self.eyes.questions), 2)

    def test_one_costume_look_per_group(self):
        w = self.watcher(costumes=[("scary witch", 1)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 30)
        arrivals = [q for q in self.eyes.questions if q[0] == "arrival"]
        self.assertEqual(arrivals, [("arrival", 1)])    # just the crop
        self.assertEqual(self.interaction.welcomed, ["scary witch"])

    def test_the_costume_look_happens_as_soon_as_they_are_confirmed(self):
        w = self.watcher(costumes=[("scary witch", 1)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 2)            # seen twice: confirmed
        self.assertEqual(self.interaction.welcomed, ["scary witch"])

    def test_the_joke_is_fetched_early_and_said_when_they_go(self):
        w = self.watcher(costumes=[("scary witch", 1)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 3)
        self.assertEqual(self.eyes.jokes, ["scary witch"])   # already fetched
        self.assertEqual(self.interaction.farewells, [])     # not said yet
        self.run_frames(w, [], 6)                            # they've gone
        self.assertEqual(self.interaction.farewells,
                         ["a joke about scary witch"])

    def test_a_joke_that_comes_back_late_is_still_said(self):
        w = self.watcher(costumes=[("scary witch", 1)])
        w.background = lambda target, *args: None if target == \
            w._fetch_joke else target(*args)
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 3)
        group = next(iter(w.groups.values()))
        self.run_frames(w, [], 6)                            # gone, no joke yet
        self.assertEqual(self.interaction.farewells, [])
        w._fetch_joke(group)                                 # it arrives now
        self.assertEqual(self.interaction.farewells,
                         ["a joke about scary witch"])

    def test_a_joke_too_late_is_dropped(self):
        w = self.watcher(costumes=[("scary witch", 1)])
        w.background = lambda target, *args: None if target == \
            w._fetch_joke else target(*args)
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 3)
        group = next(iter(w.groups.values()))
        self.run_frames(w, [], 30)                           # long gone
        w._fetch_joke(group)
        self.assertEqual(self.interaction.farewells, [])

    def test_no_costume_no_joke(self):
        w = self.watcher(costumes=[("", 1)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 3)
        self.run_frames(w, [], 6)
        self.assertEqual(self.interaction.farewells, [])
        self.assertFalse(hasattr(self.eyes, "jokes"))

    def test_gone_by_the_time_of_the_still_isnt_welcomed(self):
        # walked straight through: in the small frames, not in the 4K still
        w = self.watcher(costumes=[("a person in a jacket", 1)])
        self.detector.in_still = []
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 10)
        self.assertEqual(self.interaction.welcomed, [])
        self.assertEqual([q for q in self.eyes.questions
                          if q[0] == "arrival"], [])     # Claude not asked
        self.assertTrue(any("gone before a look" in e for e in w.events))

    def test_kids_a_step_behind_join_the_group(self):
        w = self.watcher(costumes=[("a witch and a skeleton", 2)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 1)
        self.run_frames(w, [person(0.1), person(0.5)], 10)
        self.assertEqual(len(self.interaction.welcomed), 1)
        self.assertEqual(w.allowed, 4)

    def test_a_new_group_while_the_last_is_still_there(self):
        w = self.watcher(costumes=[("scary witch", 1), ("tiny astronaut", 1)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 10)
        self.run_frames(w, [person(0.1), person(0.6)], 10)
        self.assertEqual(self.interaction.welcomed,
                         ["scary witch", "tiny astronaut"])
        self.assertEqual(w.allowed, 4)

    def test_the_first_group_leaving_doesnt_end_the_second(self):
        w = self.watcher(costumes=[("scary witch", 1), ("tiny astronaut", 1)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 10)
        self.run_frames(w, [person(0.1), person(0.6)], 10)
        self.run_frames(w, [person(0.6)], 10)
        self.assertEqual([g["costume"] for g in w.status()["groups"]],
                         ["tiny astronaut"])
        self.assertEqual(w.allowed, 2)

    def test_two_each_is_fine(self):
        w = self.watcher(costumes=[("two ghosts", 2)],
                         counts=[(30, False), (26, False)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1), person(0.4)], 30)
        self.assertEqual(w.taken, 4)
        self.assertEqual(self.interaction.called_out, [])

    def test_too_many_twice_running_is_called_out_by_costume(self):
        w = self.watcher(costumes=[("scary witch", 1)],
                         counts=[(30, False), (27, False)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 30)
        self.assertEqual(self.interaction.called_out, ["scary witch"])

    def test_the_same_kids_are_only_called_out_once(self):
        w = self.watcher(costumes=[("scary witch", 1)],
                         counts=[(30, False), (27, False)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 120)      # they just stand there
        self.assertEqual(self.interaction.called_out, ["scary witch"])

    def test_a_new_group_can_still_be_called_out(self):
        # alone 30 · witch: 30 · 27 · 27 → called out · 27 (while her group
        # lingers) · alone again 27 · astronaut: 20 → 7 taken
        w = self.watcher(costumes=[("scary witch", 1), ("tiny astronaut", 1)],
                         counts=[(30, False), (30, False), (27, False),
                                 (27, False), (27, False), (27, False),
                                 (20, False)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 17)       # three recounts
        self.run_frames(w, [], 6)                   # the witch leaves
        self.run_frames(w, [person(0.6)], 40)       # the astronaut takes 7
        self.assertEqual(self.interaction.called_out,
                         ["scary witch", "tiny astronaut"])

    def test_grabbing_before_claude_first_looks_still_counts(self):
        # the box had 30 before they got there; first count 25 — 5 taken
        w = self.watcher(costumes=[("scary witch", 1)],
                         counts=[(30, False), (25, False)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 20)
        self.assertEqual(self.interaction.called_out, ["scary witch"])

    def test_once_is_not_believed(self):
        w = self.watcher(costumes=[("scary witch", 1)],
                         counts=[(30, False), (27, False), (29, False)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 30)
        self.assertEqual(self.interaction.called_out, [])

    def test_counts_through_an_arm_dont_count(self):
        w = self.watcher(costumes=[("scary witch", 1)],
                         counts=[(30, False), (10, True), (10, True),
                                 (29, False)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 30)
        self.assertEqual(self.interaction.called_out, [])
        self.assertEqual(w.count, 29)

    def test_putting_back_is_judged_against_the_allowance(self):
        w = self.watcher(costumes=[("scary witch", 1)],
                         counts=[(30, False), (27, False), (27, False),
                                 (28, False)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 30)
        self.assertTrue(self.interaction.put_back())    # 2 taken now

    def test_claude_is_asked_a_bounded_number_of_times(self):
        w = self.watcher(costumes=[("scary witch", 1)])
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 300)          # five minutes there
        self.assertLessEqual(self.eyes.looks, 2 + w.recounts_per_group)

    def test_without_claude_it_still_tracks_people(self):
        w = watch.Watcher(Camera(), self.detector, None, self.interaction,
                          clock=self.clock, background=now, state=None)
        self.run_frames(w, [person(0.1), person(0.5)], 3)
        self.assertEqual(len(w.status()["people"]), 2)
        self.assertFalse(w.status()["claude"])


class Pictures(unittest.TestCase):
    def test_4k_goes_to_claude_at_its_own_size(self):
        small = Image.open(io.BytesIO(watch.shrink(jpeg(3840, 2160))))
        self.assertEqual(max(small.size), watch.LONGEST_SIDE)

    def test_the_crop_is_around_the_newcomers(self):
        part = Image.open(io.BytesIO(watch.crop(jpeg(3840, 2160),
                                                [0.5, 0.25, 0.75, 0.75])))
        self.assertTrue(900 < part.width < 1300 and 1100 < part.height < 1500)


class Switch(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"
        self.clock, self.detector = Clock(), Detector()
        self.interaction = Interaction()

    def tearDown(self):
        self.tmp.cleanup()

    def watcher(self, costumes=()):
        self.eyes = Eyes(costumes)
        return watch.Watcher(Camera(), self.detector, self.eyes,
                             self.interaction, clock=self.clock,
                             background=now, state=self.state)

    def run_frames(self, w, people, n):
        self.detector.next = people
        for _ in range(n):
            w.step(FRAME)
            self.clock.now += 1

    def test_off_unless_switched_on(self):
        w = self.watcher()
        self.assertFalse(w.on)
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 30)
        self.assertEqual(self.eyes.looks, 0)            # Claude never asked
        self.assertEqual(self.interaction.welcomed, [])
        self.assertEqual(len(w.status()["people"]), 1)  # but still seen

    def test_on_greets(self):
        w = self.watcher(costumes=[("scary witch", 1)])
        w.switch(True)
        self.run_frames(w, [], 1)
        self.run_frames(w, [person(0.1)], 10)
        self.assertEqual(self.interaction.welcomed, ["scary witch"])

    def test_whoever_switched_it_on_isnt_greeted(self):
        w = self.watcher(costumes=[("a person in a jacket", 1),
                                   ("tiny astronaut", 1)])
        self.run_frames(w, [person(0.1)], 5)            # standing there
        w.switch(True)
        self.run_frames(w, [person(0.1)], 20)
        self.assertEqual(self.interaction.welcomed, [])
        self.run_frames(w, [person(0.1), person(0.6)], 10)
        self.assertEqual(self.interaction.welcomed, ["a person in a jacket"])

    def test_the_switch_survives_a_restart(self):
        self.watcher().switch(True)
        self.assertTrue(self.watcher().on)
        self.watcher().switch(False)
        self.assertFalse(self.watcher().on)
