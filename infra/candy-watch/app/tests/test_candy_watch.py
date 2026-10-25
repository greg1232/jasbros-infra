"""Tests for candy watch, against a fake speaker.

    python -m unittest discover -s tests       # from infra/candy-watch/app
"""

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import candy_watch  # noqa: E402


class FakeSpeaker:
    def __init__(self, answers=True, plays=True):
        self.heard, self.answers, self.plays = [], answers, plays

    def post(self, url, body, timeout):
        self.heard.append((url, body))
        if not self.answers:
            return None
        if body["what"] == "play":
            return {"said": "Playing it — 35 seconds." if self.plays
                    else "I only play recordings from a numbered address."}
        return {"said": "Saying it."}


class Saying(unittest.TestCase):
    def test_says_through_the_speakers_own_api(self):
        fake = FakeSpeaker()
        speaker = candy_watch.Speaker("pi4", "http://10.0.0.2:8080/", fake.post)
        reply = speaker.say("Put   it\nback.")
        self.assertTrue(reply["ok"])
        self.assertEqual(fake.heard, [("http://10.0.0.2:8080/api/do",
                                       {"what": "say", "value": "Put it back."})])

    def test_never_more_than_the_speaker_accepts(self):
        fake = FakeSpeaker()
        candy_watch.Speaker("pi4", "http://x", fake.post).say("a" * 500)
        self.assertEqual(len(fake.heard[0][1]["value"]), 200)

    def test_the_gangster_line_fits(self):
        self.assertLessEqual(len(candy_watch.GANGSTER), candy_watch.SAY_LIMIT)

    def test_a_speaker_that_doesnt_answer_is_reported(self):
        reply = candy_watch.Speaker("pi4", "http://x",
                                    FakeSpeaker(False).post).say("hi")
        self.assertFalse(reply["ok"])
        self.assertIn("didn't answer", reply["said"])

    def test_nothing_to_say_says_nothing(self):
        fake = FakeSpeaker()
        candy_watch.Speaker("pi4", "http://x", fake.post).say("   ")
        self.assertEqual(fake.heard, [])


class Gangster(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clip = Path(self.tmp.name) / "home-alone.flac"

    def tearDown(self):
        self.tmp.cleanup()

    def test_plays_the_clip_when_there_is_one(self):
        self.clip.write_bytes(b"fLaC")
        fake = FakeSpeaker()
        reply = candy_watch.gangster(candy_watch.Speaker("pi4", "http://x",
                                                         fake.post),
                                     "http://10.0.0.1/candy", self.clip)
        self.assertTrue(reply["ok"])
        self.assertEqual(fake.heard[0][1], {
            "what": "play",
            "value": "http://10.0.0.1/candy/clip/home-alone.flac"})

    def test_says_the_line_without_a_clip(self):
        fake = FakeSpeaker()
        candy_watch.gangster(candy_watch.Speaker("pi4", "http://x", fake.post),
                             "http://10.0.0.1/candy", self.clip)
        self.assertEqual(fake.heard[0][1]["value"], candy_watch.GANGSTER)

    def test_says_the_line_if_the_speaker_cant_play_it(self):
        self.clip.write_bytes(b"fLaC")
        fake = FakeSpeaker(plays=False)       # a speaker on older code
        reply = candy_watch.gangster(candy_watch.Speaker("pi4", "http://x",
                                                         fake.post),
                                     "http://10.0.0.1/candy", self.clip)
        self.assertEqual([b["what"] for _, b in fake.heard], ["play", "say"])
        self.assertTrue(reply["ok"])


class Page(unittest.TestCase):
    def test_say_and_gangster_over_http(self):
        fake = FakeSpeaker()
        server = candy_watch.serve(
            candy_watch.Speaker("pi4", "http://x", fake.post), 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def post(body):
            request = urllib.request.Request(
                base + "/api/say", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request) as r:
                return json.loads(r.read())

        try:
            self.assertTrue(post({"text": "hello"})["ok"])
            self.assertEqual(post({"line": "gangster"})["text"],
                             candy_watch.GANGSTER)
            with urllib.request.urlopen(base + "/api/status") as r:
                self.assertEqual(json.loads(r.read())["speaker"], "pi4")
            with urllib.request.urlopen(base + "/") as r:
                self.assertIn(b"Candy watch", r.read())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
