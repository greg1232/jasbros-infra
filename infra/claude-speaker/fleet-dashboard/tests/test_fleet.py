"""Tests for the fleet dashboard, against a fake network and a fake clock.

    python -m unittest discover -s tests       # from fleet-dashboard/

The fixtures are real answers from a speaker, with every transcript replaced
by a marker (TRANSCRIPT-…), the neighbours' networks renamed, and the rescue
password replaced. The markers are what lets these tests prove nothing that
was said in a room ever reaches this page.
"""

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import fleet  # noqa: E402

FIXTURES = HERE / "fixtures"
SPEAKER = json.loads((FIXTURES / "speaker.json").read_text())
SYSTEM = json.loads((FIXTURES / "system.json").read_text())
WIFI = json.loads((FIXTURES / "wifi.json").read_text())
LEAKS = ("TRANSCRIPT", "NEIGHBOUR-NETWORK", "RESCUE-PASSWORD")


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


class Network:
    """Speakers by address. `None` for a version means old code (404)."""

    def __init__(self):
        self.at: dict[str, dict] = {}

    def put(self, host, name, commit="abc1234", changed=False, **overrides):
        self.at[host] = {"name": name, "commit": commit, "changed": changed,
                         **overrides}

    def get(self, url, timeout):
        parsed = urllib.request.urlparse(url)
        speaker = self.at.get(parsed.hostname)
        if speaker is None:
            return None, None
        path = parsed.path
        if path == "/api/version":
            if speaker["commit"] is None:
                return 404, None
            return 200, {"name": speaker["name"], "commit": speaker["commit"],
                         "changed": speaker["changed"]}
        if path == "/api/speaker":
            return 200, speaker.get("speaker", SPEAKER)
        if path == "/api/system":
            return 200, speaker.get("system", SYSTEM)
        if path == "/api/wifi":
            return 200, WIFI
        return 404, None

    def probe(self, host, port):
        return host in self.at


def make(speakers, network, clock, db=None):
    config = {"speakers": [{"name": n, "url": f"http://{h}:8080"}
                           for n, h in speakers],
              "rescan": {"subnet": "10.0.0.0/28", "port": 8080}}
    store = fleet.Store(db or ":memory:")
    return fleet.Fleet(config, store, clock=clock, get=network.get,
                       probe=network.probe)


def by_name(snapshot):
    return {s["name"]: s for s in snapshot["speakers"]}


class Transcripts(unittest.TestCase):
    def test_summary_keeps_how_turns_ended_not_what_was_said(self):
        summary = fleet.summarize_speaker(SPEAKER)
        self.assertEqual(summary["endings"], {"answered": len(SPEAKER["recent"])})
        self.assertNotIn("TRANSCRIPT", json.dumps(summary))

    def test_nothing_leaks_into_what_the_page_is_sent(self):
        network, clock = Network(), Clock()
        network.put("10.0.0.2", "bedroom")
        f = make([("bedroom", "10.0.0.2")], network, clock)
        f.poll_round()
        sent = json.dumps(f.snapshot())
        for marker in LEAKS:
            self.assertNotIn(marker, sent)
        self.assertEqual(by_name(f.snapshot())["bedroom"]["wifi"],
                         {"network": "solus", "signal": 84})


class Polling(unittest.TestCase):
    def setUp(self):
        self.network, self.clock = Network(), Clock()

    def test_a_speaker_that_answers_is_up(self):
        self.network.put("10.0.0.2", "bedroom")
        f = make([("bedroom", "10.0.0.2")], self.network, self.clock)
        f.poll_round()
        s = by_name(f.snapshot())["bedroom"]
        self.assertEqual(s["status"], "up")
        self.assertEqual(s["version"]["commit"], "abc1234")
        self.assertEqual(s["system"]["temperature"], SYSTEM["temperature"])
        self.assertEqual(f.snapshot()["problems"], [])

    def test_old_code_without_api_version_still_counts(self):
        self.network.put("10.0.0.2", "bedroom", commit=None)
        f = make([("bedroom", "10.0.0.2")], self.network, self.clock)
        f.poll_round()
        s = by_name(f.snapshot())["bedroom"]
        self.assertEqual(s["status"], "up")
        self.assertEqual(s["version"], {"commit": "unknown"})

    def test_looking_then_down_after_five_minutes(self):
        self.network.put("10.0.0.2", "bedroom")
        f = make([("bedroom", "10.0.0.2")], self.network, self.clock)
        f.poll_round()
        del self.network.at["10.0.0.2"]
        self.clock.tick(15)
        f.poll_round()
        self.assertEqual(by_name(f.snapshot())["bedroom"]["status"], "looking")
        self.clock.tick(5 * 60)
        self.assertEqual(by_name(f.snapshot())["bedroom"]["status"], "down")
        self.assertTrue(any("bedroom is down" in p
                            for p in f.snapshot()["problems"]))

    def test_never_seen_counts_from_when_the_page_started(self):
        f = make([("bedroom", "10.0.0.2")], self.network, self.clock)
        f.poll_round()
        self.assertEqual(by_name(f.snapshot())["bedroom"]["status"], "looking")
        self.clock.tick(5 * 60)
        self.assertEqual(by_name(f.snapshot())["bedroom"]["status"], "down")

    def test_backs_off_after_three_failures(self):
        f = make([("bedroom", "10.0.0.2")], self.network, self.clock)
        s = f.speakers["bedroom"]
        for _ in range(3):
            f.poll(s)
        self.clock.tick(15)
        self.assertFalse(f.due(s, self.clock()))
        self.clock.tick(45)
        self.assertTrue(f.due(s, self.clock()))

    def test_comes_back_on_its_own(self):
        f = make([("bedroom", "10.0.0.2")], self.network, self.clock)
        f.poll_round()
        self.clock.tick(600)
        self.network.put("10.0.0.2", "bedroom")
        f.poll_round()
        self.assertEqual(by_name(f.snapshot())["bedroom"]["status"], "up")

    def test_wifi_is_asked_only_every_five_minutes(self):
        asked = []
        self.network.put("10.0.0.2", "bedroom")
        get = self.network.get
        self.network.get = lambda url, t: (asked.append(url), get(url, t))[1]
        f = make([("bedroom", "10.0.0.2")], self.network, self.clock)
        for _ in range(10):
            f.poll_round()
            self.clock.tick(15)
        self.assertEqual(sum(u.endswith("/api/wifi") for u in asked), 1)


class Moving(unittest.TestCase):
    def setUp(self):
        self.network, self.clock = Network(), Clock()

    def test_a_speaker_that_moved_is_found(self):
        self.network.put("10.0.0.9", "bedroom")         # not where it was
        f = make([("bedroom", "10.0.0.2")], self.network, self.clock)
        f.poll_round()                  # fails, and the rescan finds it
        f.poll_round()
        s = by_name(f.snapshot())["bedroom"]
        self.assertEqual(s["status"], "up")
        self.assertEqual(s["address"], "10.0.0.9")
        self.assertEqual(s["moved_from"], "10.0.0.2")

    def test_two_speakers_that_swapped_addresses(self):
        self.network.put("10.0.0.2", "pi4")
        self.network.put("10.0.0.3", "bedroom")
        f = make([("bedroom", "10.0.0.2"), ("pi4", "10.0.0.3")],
                 self.network, self.clock)
        f.poll_round()
        snap = by_name(f.snapshot())
        # Each answers to the other's name, so neither is believed...
        self.assertIn("pi4 answers at 10.0.0.2", f.speakers["bedroom"].error)
        f.poll_round()
        snap = by_name(f.snapshot())
        # ...and one scan puts both right.
        self.assertEqual(snap["bedroom"]["address"], "10.0.0.3")
        self.assertEqual(snap["pi4"]["address"], "10.0.0.2")
        self.assertEqual(snap["bedroom"]["status"], "up")
        self.assertEqual(snap["pi4"]["status"], "up")

    def test_rescan_waits_five_minutes_between_tries(self):
        f = make([("bedroom", "10.0.0.2")], self.network, self.clock)
        f.poll_round()
        first = f.last_rescan
        self.clock.tick(60)
        f.poll_round()
        self.assertEqual(f.last_rescan, first)
        self.clock.tick(5 * 60)
        f.poll_round()
        self.assertGreater(f.last_rescan, first)

    def test_no_scan_while_everything_answers(self):
        self.network.put("10.0.0.2", "bedroom")
        f = make([("bedroom", "10.0.0.2")], self.network, self.clock)
        f.poll_round()
        self.assertIsNone(f.snapshot()["last_rescan"])

    def test_a_speaker_not_in_the_inventory_is_listed(self):
        self.network.put("10.0.0.5", "kitchen")
        f = make([("bedroom", "10.0.0.2")], self.network, self.clock)
        f.poll_round()
        self.assertEqual(f.snapshot()["unknown"],
                         [{"name": "kitchen", "address": "10.0.0.5"}])

    def test_a_new_address_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fleet.db"
            self.network.put("10.0.0.9", "bedroom")
            f = make([("bedroom", "10.0.0.2")], self.network, self.clock, db)
            f.poll_round()
            again = make([("bedroom", "10.0.0.2")], self.network, self.clock,
                         db)
            self.assertEqual(again.speakers["bedroom"].base,
                             "http://10.0.0.9:8080")
            self.assertEqual(again.speakers["bedroom"].moved_from, "10.0.0.2")


class Problems(unittest.TestCase):
    def up(self, name, system=None, speaker=None, version=None):
        return {"name": name, "status": "up", "now": 0, "last_seen": 0,
                "system": fleet.summarize_system({**SYSTEM, **(system or {})}),
                "speaker": fleet.summarize_speaker({**SPEAKER,
                                                    **(speaker or {})}),
                "version": version or {"commit": "abc1234"}}

    def test_a_healthy_house_has_none(self):
        self.assertEqual(fleet.problems([self.up("a"), self.up("b")]), [])

    def test_each_kind(self):
        found = fleet.problems([
            self.up("hot", system={"temperature": 81}),
            self.up("full", system={"disk": {"used_percent": 95}}),
            self.up("slow", system={"throttling": {"throttled now": True}}),
            self.up("quiet", system={"services": {"librespot": "failed"}}),
            self.up("deaf", speaker={"microphone": {"hearing": False}}),
        ])
        joined = "\n".join(found)
        for expected in ("hot is hot — 81 °C", "full's disk is 95% full",
                         "slow is throttling", "quiet: librespot is failed",
                         "deaf has gone deaf"):
            self.assertIn(expected, joined)

    def test_one_speaker_on_different_code(self):
        found = fleet.problems([
            self.up("a"), self.up("b"),
            self.up("c", version={"commit": "fff0000"}),
            self.up("d", version={"commit": "abc1234", "changed": True})])
        self.assertIn("c runs fff0000, the others abc1234", found)
        self.assertIn("d runs uncommitted changes on abc1234", found)


class Page(unittest.TestCase):
    def test_serves_the_page_the_api_and_health(self):
        network, clock = Network(), Clock()
        network.put("10.0.0.2", "bedroom")
        f = make([("bedroom", "10.0.0.2")], network, clock)
        server = fleet.serve(f, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            def fetch(path):
                try:
                    with urllib.request.urlopen(base + path) as r:
                        return r.status, r.read()
                except urllib.error.HTTPError as e:
                    with e:
                        return e.code, e.read()

            self.assertEqual(fetch("/readyz")[0], 503)    # no round yet
            f.poll_round()
            self.assertEqual(fetch("/readyz")[0], 200)
            self.assertEqual(fetch("/healthz")[0], 200)
            status, page = fetch("/")
            self.assertEqual(status, 200)
            self.assertIn(b"<title>Speakers</title>", page)
            status, body = fetch("/api/fleet")
            self.assertEqual(json.loads(body)["speakers"][0]["name"],
                             "bedroom")
            self.assertEqual(fetch("/nope")[0], 404)
            clock.tick(3600)
            self.assertEqual(fetch("/healthz")[0], 503)   # the poller stuck
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
