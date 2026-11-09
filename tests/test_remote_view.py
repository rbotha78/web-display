import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from webdisplay.remote_view import MAX_BYTES, load, public_url, publish, sanitize_train, snapshot


SLEEP = {"sleeping": False, "scheduled_sleep": False, "wake_until": None}


class RemoteViewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=".")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "remote-view.json"

    def publish(self, scene=None, mode="recovery", **changes):
        value = snapshot(mode, scene, SLEEP, "Loading" if scene is None else "")
        value.update(changes)
        publish(self.path, value)
        return value

    def test_sanitized_scene_and_url_never_publish_runtime_capabilities(self):
        scene = {"id": "movie", "kind": "video", "source": "plex", "title": "Film",
                 "plex_rating_key": "45", "url": "https://user:password@media.example/movie?token=secret",
                 "plex_token": "private", "plex_playback_url": "http://127.0.0.1:99/play/capability/start.m3u8",
                 "plex_stop_url": "http://127.0.0.1:99/stop/capability"}
        self.publish(scene, "carousel", private_config={"password": "private"})
        reply = load(self.path)
        self.assertEqual(reply["scene"]["url"], "")
        self.assertEqual(reply["scene"]["plex_rating_key"], "45")
        for secret in ("private", "capability", "password", "token=secret", "plex_playback", "plex_stop"):
            self.assertNotIn(secret, json.dumps(reply))
        # The kiosk publication is sanitized too, not just the HTTPS boundary.
        self.assertNotIn("capability", self.path.read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(public_url("https://user:pass@site.example:8443/path?q=secret#frag"),
                         "https://site.example:8443/path")
        for url in ("http://127.0.0.1:8/play/key", "http://[::1]/stop/key", "http://localhost/music/key", "file:///etc/passwd"):
            self.assertEqual(public_url(url), "")

    def test_single_recovery_sleep_and_notice_text(self):
        self.publish({"id": "single", "kind": "web", "url": "https://user:secret@site.example/page?token=x"},
                     "single", sleep={**SLEEP, "sleeping": True, "scheduled_sleep": True})
        value = load(self.path)
        self.assertEqual(value["mode"], "single")
        self.assertEqual(value["scene"]["url"], "https://site.example/page")
        self.assertTrue(value["sleep"]["sleeping"])
        self.assertIsNone(value["train"])
        self.publish(message="Setup access point active")
        value = load(self.path)
        self.assertEqual(value["mode"], "recovery")
        self.assertIsNone(value["scene"])
        self.assertEqual(value["message"], "Setup access point active")
        self.publish({"id": "notice", "kind": "dashboard", "text": "<script>text only</script>"}, "carousel")
        self.assertEqual(load(self.path)["scene"]["text"], "<script>text only</script>")

    def test_stale_stopped_restarted_and_malformed_runtime_are_unavailable(self):
        for changes in ({"updated": time.time() - 16}, {"updated": time.time() + 10},
                        {"updated": float("nan")}, {"mode": "bad"}, {"device_time": "not a clock"},
                        {"device_time": "2026-01-01T10:00:00"}, {"scene": {"id": "bad", "kind": "private"}}):
            self.publish()
            value = json.loads(self.path.read_text())
            value.update(changes)
            self.path.write_text(json.dumps(value))
            with self.subTest(changes=changes), self.assertRaises((ValueError, OSError)):
                load(self.path)
        for identity in ({"kiosk_pid": 999999999}, {"kiosk_start": "previous-process"},
                         {"kiosk_pid": False}):
            self.publish()
            value = json.loads(self.path.read_text())
            self.path.write_text(json.dumps({**value, **identity}))
            with self.subTest(identity=identity), self.assertRaises((ValueError, OSError)):
                load(self.path)
        self.path.write_text("x" * (MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            load(self.path)

    def test_train_joins_only_active_station_and_weather_is_opt_in(self):
        train_path, weather_path = self.root / "train.json", self.root / "weather.json"
        train_path.write_text(json.dumps({
            "active": {"crs": "PAD", "station": "Paddington", "rows": [
                {"scheduled": "10:00", "destination": "Reading", "private_key": "private"}
            ], "api_key": "private"},
            "next": {"crs": "KGX", "station": "Kings Cross"},
        }))
        weather_path.write_text(json.dumps({
            "location": "London", "temperature": 12, "api_key": "private",
            "forecast": [{"date": "2026-11-09", "low": 4, "high": 12, "url": "private"}],
        }))
        scene = {"id": "active", "kind": "train", "station_crs": "PAD"}
        value = snapshot("carousel", scene, SLEEP, train_path=train_path, weather_path=weather_path)
        self.assertEqual(value["train"]["station"], "Paddington")
        self.assertEqual(value["weather"]["temperature"], 12)
        self.assertNotIn("private", json.dumps(value))
        value = snapshot("carousel", {**scene, "station_crs": "KGX"}, SLEEP,
                         train_path=train_path, weather_path=weather_path)
        self.assertIsNone(value["train"])
        for show_weather in (False, True):
            value = snapshot("carousel", {"id": "clock", "kind": "dashboard", "show_weather": show_weather},
                             SLEEP, weather_path=weather_path)
            self.assertEqual(value["weather"] is not None, show_weather)

    def test_heartbeat_matches_live_process_and_never_exposes_process_fields(self):
        self.publish()
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw["kiosk_pid"], os.getpid())
        value = load(self.path)
        self.assertNotIn("kiosk_pid", value)
        self.assertNotIn("kiosk_start", value)
        self.assertLess(value["heartbeat_age_seconds"], 1)

    def test_previous_departure_visibility_survives_snapshot_and_manager_boundary(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.publish({"id": "train", "kind": "train", "station_crs": "PAD",
                              "show_previous_departure": enabled}, "carousel")
                self.assertIs(load(self.path)["scene"]["show_previous_departure"], enabled)

    def test_train_cache_must_match_destination_in_snapshot_and_manager_boundary(self):
        train_path = self.root / "train.json"
        board = {"crs": "HWW", "destination_crs": "WFJ", "filter_station_crs": "BWO", "rows": []}
        train_path.write_text(json.dumps({"active": board}))
        scene = {"id": "active", "kind": "train", "station_crs": "HWW",
                 "destination_crs": "WFJ", "filter_station_crs": "BWO"}
        value = snapshot("carousel", scene, SLEEP, train_path=train_path)
        self.assertEqual(value["train"], board)
        self.assertEqual(value["scene"]["destination_crs"], "WFJ")
        for field, code in (("destination_crs", ""), ("destination_crs", "SAA"),
                            ("filter_station_crs", ""), ("filter_station_crs", "PKS")):
            changed = {**scene, field: code}
            self.assertIsNone(snapshot("carousel", changed, SLEEP, train_path=train_path)["train"])
            self.publish(changed, "carousel", train=board)
            self.assertIsNone(load(self.path)["train"])
        self.publish(scene, "carousel", train=board)
        self.assertEqual(load(self.path)["train"]["destination_crs"], "WFJ")

    def test_previous_display_stops_are_sanitized_without_dropping_terminal_arrival(self):
        board = {"recent": {"stops": [], "display_stops": [
            {"station": "Selected", "crs": "SEL", "scheduled": "12:00", "timing": "departure"},
            {"station": "Filtered", "crs": "FLT", "scheduled": "12:10", "api_key": "private"},
            {"station": "Terminal", "crs": "TRM", "scheduled": "12:20", "timing": "arrival"},
        ]}}
        recent = sanitize_train(board)["recent"]
        self.assertEqual([stop["station"] for stop in recent["display_stops"]],
                         ["Selected", "Filtered", "Terminal"])
        self.assertEqual(recent["display_stops"][-1]["timing"], "arrival")
        self.assertNotIn("private", json.dumps(recent))

    def test_existing_train_cache_shape_is_preserved_including_details_and_empty_next(self):
        board = {
            "crs": "PAD", "station": "Paddington", "generated_at": "2026-11-09T10:00:00Z",
            "updated_at": 123, "services_available": True, "platform_available": True,
            "rows": [{"scheduled": "10:00", "estimated": "On time", "destination": "Reading",
                      "platform": "4", "operator": "GWR", "cancelled": False, "state": "on-time",
                      "service_type": "train", "alerts": ["Service notice"]}],
            "alerts": ["Station notice"], "details_available": True, "details_error": "",
            "next": {"scheduled": "10:00", "estimated": "On time", "actual": "", "status": "On time",
                     "delay_minutes": 0, "calling_at": [[{
                         "station": "Reading", "scheduled": "10:30", "estimated": "", "actual": "",
                         "cancelled": False,
                     }]], "previous_stops": [], "last_seen": {
                         "station": "Paddington", "scheduled": "10:00", "estimated": "On time",
                         "actual": "10:00", "cancelled": False,
                     }},
            "stale": False, "error": "",
        }
        self.assertEqual(sanitize_train(board), board)
        self.assertEqual(sanitize_train({**board, "next": None}), {**board, "next": None})

    def test_recent_departure_is_sanitized_to_public_display_fields_only(self):
        board = {
            "crs": "PAD", "rows": [], "recent_error": "Waiting for a confirmed departure",
            "recent": {
                "scheduled": "10:00", "actual": "10:04", "destination": "Reading",
                "platform": "4", "operator": "GWR", "status": "Departed at 10:04",
                "delay_minutes": 4, "updated_at": 123, "stale": False, "details_error": "",
                "last_seen": {"station": "Reading", "actual": "10:48", "service_id": "private-id"},
                "stops": [{
                    "station": "Reading", "scheduled": "10:45", "actual": "10:48",
                    "estimated": "10:50", "delay_minutes": 3, "timing": "arrival",
                    "cancelled": False, "service_id": "private-id",
                }],
                "service_id": "private-id", "service_url": "https://private.example/capability",
                "raw_payload": {"api_key": "private-key"},
            },
        }
        clean = sanitize_train(board)
        encoded = json.dumps(clean)
        self.assertEqual(clean["recent"]["stops"][0]["timing"], "arrival")
        self.assertEqual(clean["recent"]["last_seen"], {"station": "Reading", "actual": "10:48"})
        for secret in ("private-id", "service_url", "capability", "raw_payload", "private-key"):
            self.assertNotIn(secret, encoded)


if __name__ == "__main__":
    unittest.main()
