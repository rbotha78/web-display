import http.server
import json
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from test_plex import FakePlexHandler

from webdisplay import music_sync
from webdisplay.app import Application
from webdisplay.music import MusicRelay
from webdisplay.music_player import sync_correction
from webdisplay.plex import PlexClient

TOKEN = "t" * 43
GROUP = "group-id-0123456789"
FINGERPRINT = ":".join(["AB"] * 32)
LEADER = {"name": "Kitchen", "hostname": "kitchen-pi", "port": 8443,
          "addresses": ["192.168.0.249"], "fingerprint": FINGERPRINT}
SETTINGS = {"enabled": True, "station_key": "/library/sections/3/stations/1", "volume": 20,
            "day_sources": {day: "" for day in ("monday", "tuesday", "wednesday", "thursday",
                                                "friday", "saturday", "sunday")}}


class SyncPlexHandler(FakePlexHandler):
    machine = "machine-1"

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/identity":
            body = f'<MediaContainer machineIdentifier="{self.machine}" version="1" />'.encode()
        elif path.startswith("/library/metadata/") and path.rsplit("/", 1)[1].isdigit() \
                and int(path.rsplit("/", 1)[1]) < 100:
            key = path.rsplit("/", 1)[1]
            body = (f'<MediaContainer><Track ratingKey="{key}" key="/library/metadata/{key}" '
                    f'title="Track {key}" duration="5000"><Media><Part key="/library/parts/{key}/1/file.mp3" />'
                    '</Media></Track></MediaContainer>').encode()
        else:
            return super().do_GET()
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Clock:
    def __init__(self):
        self.value = 1_000_000.0

    def __call__(self):
        return self.value


class ValidationTests(unittest.TestCase):
    def test_requests_store_only_leader_token_hash_and_reject_bad_input(self):
        leader = music_sync.sync_from_request({"role": "leader", "group_id": GROUP, "token": TOKEN})
        self.assertEqual(leader, {"role": "leader", "group_id": GROUP, "token_hash": music_sync.token_hash(TOKEN)})
        self.assertEqual(music_sync.validate_stored_sync(leader), leader)
        self.assertTrue(music_sync.token_matches(TOKEN, leader["token_hash"]))
        self.assertFalse(music_sync.token_matches("u" * 43, leader["token_hash"]))
        follower = music_sync.sync_from_request({"role": "follower", "group_id": GROUP, "token": TOKEN,
                                                 "leader": LEADER})
        self.assertEqual(music_sync.validate_stored_sync(follower), follower)
        for value in ({"role": "leader", "group_id": GROUP, "token": "short"},
                      {"role": "leader", "group_id": "bad id!", "token": TOKEN},
                      {"role": "follower", "group_id": GROUP, "token": TOKEN},
                      {"role": "follower", "group_id": GROUP, "token": TOKEN,
                       "leader": {**LEADER, "fingerprint": "ab:cd"}},
                      {"role": "follower", "group_id": GROUP, "token": TOKEN,
                       "leader": {**LEADER, "addresses": ["bad host!"]}},
                      {"role": "off", "group_id": GROUP}, {"role": "boss"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                music_sync.sync_from_request(value)
        for value in ({"name": "x"}, {"name": "x", "listening": 1},
                      {"name": "x", "listening": True, "command": {"action": "reboot"}},
                      {"name": "x", "listening": True, "command": {"action": "skip", "like": "1"}}):
            with self.subTest(peer=value), self.assertRaises(ValueError):
                music_sync.peer_request(value)

    def test_clock_offset_prefers_lowest_round_trip(self):
        offset = music_sync.ClockOffset()
        offset.add(10.0, 112.0, 11.0)   # rtt 1.0 -> offset 101.5
        offset.add(20.0, 120.1, 20.2)   # rtt 0.2 -> offset 100.0
        offset.add(30.0, "bad", 30.1)
        self.assertAlmostEqual(offset.offset, 100.0)

    def test_drift_correction_only_nudges_rate_within_inaudible_limit(self):
        self.assertEqual(sync_correction(0.01), 1.0)
        self.assertEqual(sync_correction(0.04), 0.996)
        self.assertEqual(sync_correction(-0.2), 1.005)
        self.assertEqual(sync_correction(-2.5), 1.005)
        self.assertEqual(sync_correction(0.3), 0.995)


class GroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SyncPlexHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        SyncPlexHandler.machine = "machine-1"
        self.clock = Clock()
        patcher = patch.object(music_sync, "now", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self):
        return PlexClient(self.url, FakePlexHandler.token, "stable-client-id-1234", "display", "1.0")

    def leader(self):
        relay = MusicRelay(self.client)
        self.addCleanup(relay.close)
        relay.configure(SETTINGS, "identity", {"role": "leader", "group_id": GROUP,
                                               "token_hash": music_sync.token_hash(TOKEN)})
        return relay

    def test_leader_schedules_advances_pauses_and_idles_the_group(self):
        relay = self.leader()
        now = self.clock.value
        state = relay.control({"action": "sync", "listening": True})
        self.assertEqual((state["state"], state["rating_key"]), ("playing", "1"))
        self.assertEqual(state["next_title"], "Track 2")
        self.assertEqual(state["started_at"], now + music_sync.START_LEAD)
        self.assertTrue(state["url"].startswith(relay.url + "/stream/"))
        with self.assertRaises(ValueError):
            relay.control({"action": "current"})
        # The next track starts exactly where the previous one ended.
        self.clock.value = now + music_sync.START_LEAD + 5.2
        state = relay.peer("Lounge", True)
        self.assertEqual((state["rating_key"], state["started_at"]), ("2", now + music_sync.START_LEAD + 5))
        started = state["started_at"]
        relay.request_playback("pause")
        self.clock.value += 1
        paused = relay.peer("Lounge", True)
        self.assertEqual(paused["state"], "paused")
        self.assertAlmostEqual(paused["position"], 0.2)
        relay.request_playback("resume")
        self.assertEqual(relay.peer("Lounge", True)["started_at"], started + 1)
        relay.request_playback("skip")
        state = relay.peer("Lounge", True)
        self.assertEqual((state["rating_key"], state["started_at"]),
                         ("3", self.clock.value + music_sync.START_LEAD))
        self.assertEqual([m["name"] for m in relay.members()], ["Lounge"])
        # With nobody listening the playhead freezes instead of advancing Plex.
        relay.peer("Lounge", False)
        relay.control({"action": "sync", "listening": False})
        self.clock.value += music_sync.LISTENER_TIMEOUT + 1
        frozen = relay.peer("Lounge", False)
        self.assertEqual((frozen["state"], frozen["rating_key"]), ("idle", "3"))
        self.assertEqual(relay.get_status()["state"], "playing")
        relay.sleep_runtime = Mock(sleeping=lambda: True)
        self.assertEqual(relay.get_status()["state"], "sleeping")
        self.clock.value += 100
        self.assertEqual(relay.peer("Lounge", True)["rating_key"], "3")
        self.assertEqual(relay.get_status()["state"], "playing")

    def test_sleeping_leader_with_nobody_listening_reports_sleeping(self):
        relay = self.leader()
        relay.control({"action": "sync", "listening": False})
        self.assertEqual(relay.get_status()["state"], "waiting")
        relay.sleep_runtime = Mock(sleeping=lambda: True)
        self.assertEqual(relay.get_status()["state"], "sleeping")

    def test_follower_follows_leader_track_in_local_time_and_rejects_other_plex(self):
        leader = self.leader()
        leader.control({"action": "sync", "listening": True})
        follower = MusicRelay(self.client)
        self.addCleanup(follower.close)

        class Link:
            # Leader clock runs 100 s ahead of the follower's.
            def __init__(link, *_):
                pass

            def call(link, body):
                state = dict(leader.peer(body["name"], body["listening"]))
                state["server_time"] += 100
                state["started_at"] += 100
                return state

        with patch.object(music_sync, "POLL_INTERVAL", 3600), patch("webdisplay.music.LeaderClient", Link):
            follower.configure({**SETTINGS, "enabled": False}, "identity",
                               {"role": "follower", "group_id": GROUP, "token": TOKEN, "leader": LEADER})
            follower.forward(None)
        group = leader.peer("probe", True)
        state = follower.control({"action": "sync", "listening": True})
        self.assertEqual(state["rating_key"], group["rating_key"])
        self.assertEqual(state["next_title"], group["next_title"])
        self.assertAlmostEqual(state["started_at"], group["started_at"])
        with urllib.request.urlopen(state["url"]) as response:
            self.assertEqual(response.read(), b"test music bytes")
        self.assertEqual(follower.sync_connected(), (True, ""))
        follower.track = None
        follower.machine = ""
        SyncPlexHandler.machine = "machine-2"
        with self.assertRaisesRegex(ValueError, "different Plex server"):
            follower.forward(None)


class ApplicationSyncTests(unittest.TestCase):
    def test_sync_settings_endpoints_and_follower_forwarding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root / "config.json", root / "pairing", root / "target", Path("/bin/true"))
            try:
                self.assertEqual(app.music_sync_status()["role"], "off")
                status = app.save_music_sync({"role": "leader", "group_id": GROUP, "token": TOKEN})
                self.assertEqual((status["role"], status["group_id"], status["members"]), ("leader", GROUP, []))
                private = (root / "config.json").read_text()
                self.assertNotIn(TOKEN, private)
                self.assertTrue(json.loads((root / "music.json").read_text())["sync"])
                self.assertIsNone(app.music_sync_peer("u" * 43, {"name": "x", "listening": True}))
                self.assertEqual(app.music_sync_peer(TOKEN, {"name": "Lounge", "listening": False})["state"],
                                 "stopped")
                status = app.save_music_sync({"role": "follower", "group_id": GROUP, "token": TOKEN,
                                              "leader": LEADER})
                self.assertEqual(status["leader"], {k: LEADER[k] for k in ("name", "hostname", "port", "addresses")})
                self.assertIsNone(app.music_sync_peer(TOKEN, {"name": "x", "listening": True}))
                with patch.object(app.music, "forward") as forward:
                    app.control_music({"action": "skip"})
                    app.like_music({"rating_key": "7"})
                self.assertEqual([c.args[0] for c in forward.call_args_list], [{"action": "skip"}, {"like": "7"}])
                app.save_music_sync({"role": "off"})
                self.assertNotIn("sync", json.loads((root / "music.json").read_text()))
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
