"""Tests for candy watch, against a fake speaker.

    python -m unittest discover -s tests       # from infra/candy-watch/app
"""

import json
import sys
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import candy_watch  # noqa: E402


class FakeSpeaker:
    def __init__(self, answers=True):
        self.heard, self.answers = [], answers

    def post(self, url, body, timeout):
        self.heard.append((url, body))
        return {"said": "Saying it."} if self.answers else None


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
