"""The person detector, on real (public-domain) photos.

Runs only where the model is — in the image's test stage, or with
VISION_MODEL pointing at a copy.
"""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vision  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@unittest.skipUnless(vision.MODEL.is_file(), f"no model at {vision.MODEL}")
class People(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = vision.Detector()

    def test_two_trick_or_treaters(self):
        self.assertEqual(len(self.detector.people(
            FIXTURES / "trick-or-treat-sweden.jpg")), 2)

    def test_two_children_and_the_woman_at_the_door(self):
        self.assertEqual(len(self.detector.people(
            FIXTURES / "trick-or-treat-beaumont-1950.jpg")), 3)

    def test_nobody(self):
        from PIL import Image
        empty = io.BytesIO()
        Image.new("RGB", (640, 360), (90, 80, 70)).save(empty, "JPEG")
        self.assertEqual(self.detector.people(empty.getvalue()), [])

    def test_fast_enough_on_one_thread(self):
        self.detector.people(FIXTURES / "trick-or-treat-sweden.jpg")
        self.assertLess(self.detector.last_ms, 500)
