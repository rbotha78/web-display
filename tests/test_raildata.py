import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import Mock, patch

from webdisplay.raildata import (
    RailDataError,
    RailDataClient,
    RailDataWorker,
    _delay_minutes,
    _request,
    validate_api_key,
    validate_crs,
    validate_refresh_interval,
)


class RailDataTests(unittest.TestCase):
    def test_service_notices_include_board_and_details_reasons_without_duplicates(self):
        board = {"trainServices": [
            {"std": "12:00", "etd": "Cancelled", "serviceID": "next",
             "adhocAlerts": ["<p>Use replacement buses</p>"], "cancelReason": "Staff shortage"},
            {"std": "12:30", "etd": "Delayed", "delayReason": "Signal failure"},
        ]}
        details = {
            "adhocAlerts": ["Use replacement buses", "Tickets accepted on local buses"],
            "cancelReason": "Staff shortage", "overdueMessage": "Awaiting an update",
        }
        with patch("webdisplay.raildata._request", side_effect=[board, details]):
            result = RailDataClient("key", "details-key").fetch("HWW")
        self.assertEqual(result["rows"][0]["alerts"], [
            "Use replacement buses", "Cancellation reason: Staff shortage",
            "Tickets accepted on local buses", "Service update: Awaiting an update",
        ])
        self.assertEqual(result["rows"][1]["alerts"], ["Delay reason: Signal failure"])

    def test_replacement_buses_merge_with_trains_in_departure_order_across_midnight(self):
        board = {
            "generatedAt": "2026-11-07T23:50:00+00:00",
            "trainServices": [{"std": "00:10", "etd": "On time"}],
            "busServices": [
                {"std": "23:55", "etd": "On time", "serviceID": "bus-id"},
                {"std": "00:20", "etd": "Cancelled"},
            ],
        }
        with patch("webdisplay.raildata._request", side_effect=[board, {"std": "23:55", "etd": "On time"}]) as request:
            result = RailDataClient("key", "details-key").fetch("HWW")
        self.assertEqual([row["scheduled"] for row in result["rows"]], ["23:55", "00:10", "00:20"])
        self.assertEqual([row["service_type"] for row in result["rows"]], ["bus", "train", "bus"])
        self.assertIn("bus-id", request.call_args.args[0])
        self.assertEqual(result["rows"][2]["state"], "cancelled")
        board.pop("trainServices")
        with patch("webdisplay.raildata._request", return_value=board):
            result = RailDataClient("key", "").fetch("HWW")
        self.assertEqual(len(result["rows"]), 2)

    def test_failed_startup_retries_after_thirty_seconds_then_resumes_configured_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"raildata_api_key": "key", "raildata_refresh_seconds": 120,
                      "playlist": {"enabled": False, "scenes": []}}
            fetch = Mock(side_effect=[
                RailDataError("offline"), {"rows": [{"scheduled": "12:00"}]},
                {"rows": [{"scheduled": "12:10"}]},
            ])
            worker = RailDataWorker(lambda: config, Path(directory) / "board.json", fetch=fetch)
            worker.close()
            config["playlist"] = {"enabled": True, "scenes": [
                {"id": "hww", "kind": "train", "station_crs": "HWW", "enabled": True},
            ]}
            for stamp, expected in ((100, 1), (129, 1), (130, 2), (160, 2), (249, 2), (250, 3)):
                with patch("webdisplay.raildata.time.monotonic", return_value=stamp):
                    worker.refresh_once()
                self.assertEqual(fetch.call_count, expected)

    def test_status_colours_and_station_message_shapes(self):
        board = {
            "trainServices": [
                {"std": "12:00", "etd": "Cancelled"},
                {"std": "12:10", "etd": "12:15"},
                {"std": "12:20", "etd": "On time"},
                {"std": "23:58", "etd": "00:03"},
                {"std": "12:30", "etd": ""},
            ],
            "nrccMessages": [
                {"value": "<p>Station update</p><p>Use platform &amp; lift.</p>"},
                {"Value": "Legacy message"}, "Plain message",
            ],
        }
        with patch("webdisplay.raildata._request", return_value=board):
            result = RailDataClient("key", "").fetch("HWW")
        self.assertEqual([row["state"] for row in result["rows"]],
                         ["cancelled", "delayed", "on-time", "delayed", ""])
        self.assertEqual(result["rows"][0]["status"], "Cancelled")
        self.assertEqual(result["alerts"], [
            "Station update Use platform & lift.", "Legacy message", "Plain message",
        ])

    def test_refresh_interval_requires_at_least_thirty_whole_seconds(self):
        self.assertEqual(validate_refresh_interval(30), 30)
        self.assertEqual(validate_refresh_interval(120), 120)
        for value in (0, 29, 30.5, True, "60", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_refresh_interval(value)

    def test_worker_applies_changed_interval_without_bypassing_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {
                "raildata_api_key": "private-key",
                "raildata_refresh_seconds": 60,
                "playlist": {"enabled": True, "scenes": [
                    {"id": "pad", "kind": "train", "enabled": True, "station_crs": "PAD"},
                ]},
            }
            calls = []
            worker = RailDataWorker(
                lambda: config, Path(directory) / "board.json",
                fetch=lambda *args: calls.append(args) or {"rows": []},
            )
            worker.close()
            calls.clear()
            worker._last_refresh_at = None
            with patch("webdisplay.raildata.time.monotonic", return_value=100):
                worker.refresh_once()
            config["raildata_refresh_seconds"] = 30
            with patch("webdisplay.raildata.time.monotonic", return_value=129):
                worker.refresh_once()
            self.assertEqual(len(calls), 1)
            with patch("webdisplay.raildata.time.monotonic", return_value=130):
                worker.refresh_once()
            self.assertEqual(len(calls), 2)
            config["raildata_refresh_seconds"] = 120
            with patch("webdisplay.raildata.time.monotonic", return_value=190):
                worker.refresh_once()
            self.assertEqual(len(calls), 2)

    def test_validates_credentials_and_station_codes(self):
        self.assertEqual(validate_api_key("test-key"), "test-key")
        self.assertEqual(validate_crs("pad"), "PAD")
        for value in ("", "bad key", "x" * 513):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_api_key(value)
        for value in ("PA", "PADD", "P1D", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_crs(value)

    def test_http_authorization_errors_are_actionable_and_do_not_leak_credentials(self):
        error = HTTPError(
            "https://api.example/test", 403, "Forbidden", {}, None,
        )
        with patch("webdisplay.raildata.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RailDataError, "HTTP 403.*check API key and product access"):
                RailDataClient("private-key").fetch("PAD")

    def test_api_request_sets_a_user_agent_accepted_by_the_provider(self):
        with patch("webdisplay.raildata.urllib.request.urlopen", return_value=io.BytesIO(b"{}")) as open_url:
            _request("https://api.example/test", "private-key")
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), "WebDisplay/1")
        self.assertEqual(request.get_header("X-apikey"), "private-key")

    def test_fetches_board_then_details_for_only_the_first_departure(self):
        board = {
            "locationName": "London Paddington",
            "generatedAt": "2026-11-07T10:00:00Z",
            "platformAvailable": True,
            "departures": [
                {"service": {
                    "serviceID": "service/id",
                    "std": "10:00",
                    "etd": "10:07",
                    "destination": [{"locationName": "Bristol Temple Meads"}],
                    "platform": "4",
                    "operator": "Great Western Railway",
                }},
                {"service": {"std": "10:15", "destination": [{"locationName": "Oxford"}]}},
            ],
        }
        details = {"std": "10:00", "etd": "10:07"}
        with patch("webdisplay.raildata._request", side_effect=[board, details]) as request:
            result = RailDataClient("test-key").fetch("pad")
        self.assertEqual(request.call_count, 2)
        self.assertIn("GetDepBoardWithDetails/PAD?numRows=8", request.call_args_list[0].args[0])
        self.assertIn("GetServiceDetails/service%2Fid", request.call_args_list[1].args[0])
        self.assertEqual(result["station"], "London Paddington")
        self.assertEqual(result["rows"][0]["destination"], "Bristol Temple Meads")
        self.assertEqual(result["rows"][0]["status"], "Delayed by 7 min")
        self.assertEqual(result["next"]["delay_minutes"], 7)
        self.assertEqual(len(result["rows"]), 2)
        self.assertNotIn("service/id", json.dumps(result))

    def test_uses_separate_keys_for_board_and_service_details(self):
        board = {"trainServices": [{
            "serviceID": "service-1", "std": "10:00", "etd": "On time",
        }]}
        details = {"std": "10:00", "etd": "On time"}
        with patch("webdisplay.raildata._request", side_effect=[board, details]) as request:
            result = RailDataClient("board-key", "details-key").fetch("PAD")
        self.assertEqual(request.call_args_list[0].args[1], "board-key")
        self.assertEqual(request.call_args_list[1].args[1], "details-key")
        self.assertTrue(result["details_available"])

    def test_delay_calculation_handles_midnight(self):
        self.assertEqual(_delay_minutes("23:58", "00:04"), 6)
        self.assertEqual(_delay_minutes("10:00", "On time"), None)

    def test_next_train_includes_calling_points_and_last_actual_report(self):
        board = {"trainServices": [{
            "serviceID": "service-1", "std": "12:14", "etd": "On time",
        }]}
        details = {
            "subsequentCallingPoints": [{"callingPoint": [
                {"locationName": "Bricket Wood", "st": "12:16", "et": "12:18"},
                {"locationName": "Watford Junction", "st": "12:25", "isCancelled": True},
            ]}],
            "previousCallingPoints": [{"callingPoint": [
                {"locationName": "St Albans Abbey", "st": "12:08", "at": "12:09"},
                {"locationName": "Park Street", "st": "12:11", "et": "On time"},
            ]}],
        }
        with patch("webdisplay.raildata._request", side_effect=[board, details]):
            result = RailDataClient("board-key", "details-key").fetch("HWW")
        upcoming = result["next"]
        self.assertEqual(upcoming["calling_at"][0][0]["station"], "Bricket Wood")
        self.assertEqual(upcoming["calling_at"][0][0]["estimated"], "12:18")
        self.assertTrue(upcoming["calling_at"][0][1]["cancelled"])
        self.assertEqual(upcoming["previous_stops"][0][0]["actual"], "12:09")
        self.assertEqual(upcoming["previous_stops"][0][1]["station"], "Park Street")
        self.assertEqual(upcoming["previous_stops"][0][1]["actual"], "")
        self.assertEqual(upcoming["last_seen"]["station"], "St Albans Abbey")
        self.assertEqual(upcoming["last_seen"]["actual"], "12:09")
        details["subsequentCallingPoints"].append({"callingPoint": [
            {"locationName": "Other destination", "st": "12:30", "et": "On time"},
        ]})
        details["previousCallingPoints"].append({"callingPoint": [
            {"locationName": "Earlier branch", "at": "12:07"},
            {"locationName": "Cancelled stop", "at": "12:12", "isCancelled": True},
            {"locationName": "Unconfirmed stop", "at": "On time"},
        ]})
        with patch("webdisplay.raildata._request", side_effect=[board, details]):
            result = RailDataClient("board-key", "details-key").fetch("HWW")
        self.assertEqual(len(result["next"]["calling_at"]), 2)
        self.assertEqual(len(result["next"]["previous_stops"]), 2)
        self.assertEqual(result["next"]["last_seen"]["station"], "St Albans Abbey")
        details["previousCallingPoints"][0]["callingPoint"][0].pop("at")
        details["previousCallingPoints"][1]["callingPoint"][0].pop("at")
        with patch("webdisplay.raildata._request", side_effect=[board, details]):
            result = RailDataClient("board-key", "details-key").fetch("HWW")
        self.assertIsNone(result["next"]["last_seen"])

    def test_keeps_board_when_service_details_are_temporarily_unavailable(self):
        board = {
            "locationName": "Paddington",
            "departures": [{"service": {
                "serviceID": "service-1",
                "std": "10:00",
                "etd": "10:05",
                "destination": [{"locationName": "Bristol"}],
            }}],
        }
        with patch(
            "webdisplay.raildata._request",
            side_effect=[board, RailDataError("upstream unavailable")],
        ):
            result = RailDataClient("test-key").fetch("PAD")
        self.assertEqual(result["rows"][0]["destination"], "Bristol")
        self.assertEqual(result["rows"][0]["status"], "Delayed by 5 min")
        self.assertFalse(result["details_available"])

    def test_reads_real_station_board_train_services_shape(self):
        board = {
            "locationName": "How Wood",
            "generatedAt": "2026-11-07T11:53:15Z",
            "platformAvailable": True,
            "trainServices": [{
                "serviceID": "service-1",
                "std": "11:57",
                "etd": "On time",
                "destination": [{"locationName": "St Albans Abbey"}],
                "platform": "1",
                "operator": "LNR & WMR",
            }],
        }
        details = {"std": "11:57", "etd": "On time"}
        with patch("webdisplay.raildata._request", side_effect=[board, details]):
            result = RailDataClient("test-key").fetch("HWW")
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["destination"], "St Albans Abbey")
        self.assertEqual(result["rows"][0]["status"], "On time")
        self.assertEqual(result["next"]["delay_minutes"], 0)

    def test_worker_updates_one_station_per_refresh_without_publishing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "train-board.json"
            config = {
                "raildata_api_key": "private-key",
                "service_details_api_key": "private-details-key",
                "playlist": {"enabled": True, "scenes": [
                    {"id": "pad", "kind": "train", "enabled": True, "station_crs": "PAD"},
                    {"id": "brs", "kind": "train", "enabled": True, "station_crs": "BRS"},
                ]},
            }
            calls = []

            def fetch(key, details_key, crs):
                calls.append((key, details_key, crs))
                return {"crs": crs, "rows": [], "stale": False}

            worker = RailDataWorker(lambda: config, path, interval=3600, fetch=fetch)
            try:
                worker.refresh_once()
                worker.refresh_once()
                published = path.read_text()
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0], ("private-key", "private-details-key", "PAD"))
                self.assertNotIn("private-key", published)
                self.assertNotIn("private-details-key", published)
                self.assertEqual(len(json.loads(published)), 1)
            finally:
                worker.close()

    def test_worker_does_not_poll_when_carousel_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "train-board.json"
            calls = []
            config = {
                "raildata_api_key": "private-key",
                "playlist": {"enabled": False, "scenes": [
                    {"id": "pad", "kind": "train", "enabled": True, "station_crs": "PAD"},
                ]},
            }
            worker = RailDataWorker(
                lambda: config, path, interval=3600,
                fetch=lambda key, details_key, crs: calls.append((key, details_key, crs)) or {},
            )
            try:
                worker.refresh_once()
                self.assertEqual(calls, [])
                self.assertEqual(json.loads(path.read_text()), {})
            finally:
                worker.close()

    def test_worker_keeps_last_successful_board_on_api_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "train-board.json"
            path.write_text(json.dumps({"pad": {"rows": [{"scheduled": "10:00"}]}}))
            config = {
                "raildata_api_key": "private-key",
                "playlist": {"enabled": True, "scenes": [
                    {"id": "pad", "kind": "train", "enabled": True, "station_crs": "PAD"},
                ]},
            }

            def fail(_key, _details_key, _crs):
                raise RailDataError("upstream unavailable")

            worker = RailDataWorker(lambda: config, path, interval=3600, fetch=fail)
            try:
                worker.refresh_once()
                payload = json.loads(path.read_text())
                self.assertEqual(payload["pad"]["rows"], [{"scheduled": "10:00"}])
                self.assertTrue(payload["pad"]["stale"])
            finally:
                worker.close()
