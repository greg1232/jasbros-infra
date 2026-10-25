"""The video stream reader, with a fake camera stream.

    python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

import stream  # noqa: E402


class Frame:
    def __init__(self, shade):
        self.shade = shade

    def to_image(self):
        return Image.new("RGB", (640, 360), (self.shade, 0, 0))


class Container:
    def __init__(self, frames):
        self.frames, self.closed = frames, False
        self.streams = type("S", (), {"video": ["video"]})()

    def decode(self, video):
        yield from self.frames

    def close(self):
        self.closed = True


class Stop(BaseException):
    """Ends the reader's forever loop in a test."""


class Reading(unittest.TestCase):
    def reader(self, openings):
        self.slept, self.urls = [], []
        openings = list(openings)

        def opener(url, options, timeout):
            self.urls.append(url)
            if not openings:
                raise Stop()
            got = openings.pop(0)
            if isinstance(got, Exception):
                raise got
            return got

        def sleep(seconds):
            self.slept.append(seconds)
            if len(self.slept) > 10:
                raise Stop()

        self.clock = [1000.0]

        def clock():
            self.clock[0] += 0.1
            return self.clock[0]

        return stream.Stream("10.0.0.7", "solus", "p@ss/word", opener, sleep,
                             clock)

    def test_keeps_the_newest_frame(self):
        s = self.reader([Container([Frame(1), Frame(2), Frame(3)])])
        with self.assertRaises(Stop):
            s.run()
        image, _ = s.newest()
        self.assertEqual(image.getpixel((0, 0)), (3, 0, 0))
        self.assertEqual(s.frames, 3)

    def test_the_login_is_quoted_into_the_url(self):
        s = self.reader([])
        self.assertEqual(s.url, "rtsp://solus:p%40ss%2Fword@10.0.0.7:554/"
                                "h264Preview_01_sub")

    def test_a_dropped_stream_reconnects_and_backs_off(self):
        s = self.reader([OSError("reset"), OSError("reset"),
                         Container([Frame(9)])])
        with self.assertRaises(Stop):
            s.run()
        self.assertEqual(self.slept[:2], [2.0, 4.0])
        self.assertEqual(s.frames, 1)

    def test_a_refused_login_waits_ten_minutes(self):
        # RTSP logins count towards the camera's five-strike lockout too.
        s = self.reader([Exception("Server returned 401 Unauthorized")])
        with self.assertRaises(Stop):
            s.run()
        self.assertEqual(self.slept[0], 600.0)
        self.assertIn("refused", s.error)

    def test_not_live_without_recent_frames(self):
        s = self.reader([])
        self.assertFalse(s.live())
        s.connected, s.frame, s.frame_at = True, Frame(1), self.clock[0]
        self.assertTrue(s.live())
        self.clock[0] += 10
        self.assertFalse(s.live())

    def test_the_live_view_jpeg_is_made_once_per_frame(self):
        s = self.reader([])
        s.frame, s.frame_at = Frame(5), 1.0
        first = s.jpeg()
        self.assertTrue(first.startswith(b"\xff\xd8"))
        s.frame = Frame(200)                # same frame_at: cached
        self.assertIs(s.jpeg(), first)
        s.frame_at = 2.0
        self.assertIsNot(s.jpeg(), first)
