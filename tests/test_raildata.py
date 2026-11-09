import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from webdisplay.raildata import (
    RailDataError,
    RailDataClient,
    RailDataWorker,
    _delay_minutes,
    _recent_details,
    _request,
    validate_api_key,
    validate_crs,
    validate_refresh_interval,
)


class PreviousServiceTests(unittest.TestCase):
    def setUp(self):
        self.board = {
            "locationName": "How Wood", "generatedAt": "2026-11-09T12:00:00Z",
            "trainServices": [{"std": "12:30", "destination": [{"locationName": "Next train"}]}],
        }
        self.history = [{
            "serviceID": "previous/id", "std": "11:57", "etd": "On time",
            "destination": [{"locationName": "St Albans Abbey"}],
            "subsequentCallingPoints": [{"callingPoint": [
                {"locationName": "Park Street", "st": "11:59", "et": "On time"},
            ]}],
        }]
        self.details = {"std": "12:30", "etd": "On time"}
        self.history_error = None
        self.details_error = None
        request = patch("webdisplay.raildata._request", side_effect=self.reply)
        self.request = request.start()
        self.addCleanup(request.stop)

    def reply(self, url, key):
        if "timeOffset=" in url:
            if self.history_error:
                raise self.history_error
            return {"trainServices": self.history}
        if "GetServiceDetails/" in url:
            if self.details_error:
                raise self.details_error
            return self.details
        return self.board

    def test_fetches_previous_service_immediately_without_inventing_actuals(self):
        self.history.extend([
            {"serviceID": "cancelled", "std": "11:59", "isCancelled": True},
            {"serviceID": "bus", "std": "11:59", "serviceType": "bus"},
            {"serviceID": "future", "std": "12:01"},
            {"serviceID": "older", "std": "11:56"},
        ])
        board = RailDataClient("board-key", "details-key").fetch("HWW")
        previous = board["recent"]
        self.assertEqual(previous["scheduled"], "11:57")
        self.assertEqual(previous["destination"], "St Albans Abbey")
        self.assertEqual(previous["status"], "Departure not confirmed")
        self.assertEqual(previous["actual"], "")
        self.assertIsNone(previous["delay_minutes"])
        self.assertIsNone(previous["last_seen"])
        self.assertEqual(previous["stops"][-1]["estimated"], "On time")
        self.assertIsNone(previous["stops"][-1]["delay_minutes"])
        self.assertEqual(board["rows"][0]["scheduled"], "12:30")
        self.assertEqual(self.request.call_count, 3)
        self.assertIn("timeOffset=-5&timeWindow=5", self.request.call_args_list[2].args[0])
        self.assertEqual(self.request.call_args_list[2].args[1], "board-key")
        self.assertFalse(any("GetServiceDetails/" in call.args[0]
                             for call in self.request.call_args_list))
        self.assertNotIn("previous/id", json.dumps(board))

    def test_refreshes_previous_then_promotes_actual_and_stops_lookback(self):
        client = RailDataClient("board-key", "details-key")
        self.assertEqual(client.fetch("HWW")["recent"]["actual"], "")
        self.history[0]["atd"] = "11:59"
        confirmed = client.fetch("HWW")["recent"]
        self.assertEqual(confirmed["actual"], "11:59")
        self.assertEqual(confirmed["delay_minutes"], 2)
        self.request.reset_mock()
        self.history[0]["subsequentCallingPoints"][0]["callingPoint"][0]["at"] = "12:02"
        refreshed = client.fetch("HWW")["recent"]
        self.assertEqual(refreshed["last_seen"], {"station": "How Wood", "actual": "11:59"})
        self.assertEqual(self.request.call_count, 1)
        self.assertNotIn("timeOffset=", self.request.call_args.args[0])
        self.assertNotIn("GetServiceDetails/", self.request.call_args.args[0])

    def test_newer_scheduled_service_replaces_unconfirmed_previous(self):
        client = RailDataClient("board-key", "details-key")
        self.assertEqual(client.fetch("HWW")["recent"]["scheduled"], "11:57")
        self.board["generatedAt"] = "2026-11-09T12:02:00Z"
        self.history = [{"serviceID": "new-previous", "std": "12:01"}]
        previous = client.fetch("HWW")["recent"]
        self.assertEqual(previous["scheduled"], "12:01")
        self.assertEqual(previous["actual"], "")
        self.assertEqual(previous["status"], "Departure not confirmed")

    def test_keeps_previous_snapshot_on_history_failure_and_recovers(self):
        client = RailDataClient("board-key", "details-key")
        first = client.fetch("HWW")["recent"]
        self.history_error = RailDataError("history unavailable")
        failed = client.fetch("HWW")
        self.assertEqual(failed["recent"]["stops"], first["stops"])
        self.assertTrue(failed["recent"]["stale"])
        self.assertEqual(failed["recent_error"], "history unavailable")
        self.history_error = None
        self.assertFalse(client.fetch("HWW")["recent"]["stale"])

    def test_previous_board_needs_neither_details_key_nor_service_id(self):
        self.history[0].pop("serviceID")
        board = RailDataClient("board-key", "").fetch("HWW")
        previous = board["recent"]
        self.assertEqual(previous["scheduled"], "11:57")
        self.assertEqual(previous["actual"], "")
        self.assertFalse(previous["stale"])
        self.assertEqual(board["recent_error"], "")
        self.assertEqual(previous["stops"][-1]["station"], "Park Street")
        self.assertFalse(any("GetServiceDetails/" in call.args[0]
                             for call in self.request.call_args_list))

    def test_details_requests_only_target_next_live_service_across_refreshes(self):
        client = RailDataClient("board-key", "details-key")
        self.board["trainServices"][0]["serviceID"] = "next/id"
        for _ in range(2):
            board = client.fetch("HWW")
            self.assertTrue(board["details_available"])
            self.assertEqual(board["recent"]["scheduled"], "11:57")
        self.board["trainServices"] = []
        client.fetch("HWW")
        detail_calls = [call for call in self.request.call_args_list
                        if "GetServiceDetails/" in call.args[0]]
        self.assertEqual(len(detail_calls), 2)
        for call in detail_calls:
            self.assertTrue(call.args[0].endswith("/next%2Fid"))
            self.assertEqual(call.args[1], "details-key")

    def test_destination_filter_is_sent_to_live_and_all_lookback_board_calls(self):
        board = RailDataClient("board-key", "").fetch("hww", "wfj")
        self.assertEqual(board["destination_crs"], "WFJ")
        self.assertEqual(self.request.call_count, 3)
        for call in self.request.call_args_list:
            query = parse_qs(urlsplit(call.args[0]).query)
            self.assertEqual(query["filterCrs"], ["WFJ"])
            self.assertEqual(query["filterType"], ["to"])
            self.assertEqual(call.args[1], "board-key")

    def test_calling_station_filters_live_and_previous_routes_alongside_destination(self):
        matching = {
            "std": "11:57", "destination": [{"locationName": "Terminus", "crs": "TRM"}],
            "previousCallingPoints": [{"callingPoint": [
                {"locationName": "How Wood", "crs": "HWW", "st": "11:00"},
            ]}],
            "subsequentCallingPoints": [{"callingPoint": [
                {"locationName": "Filtered", "crs": "FLT", "st": "12:02"},
                {"locationName": "Other", "crs": "OTH", "st": "12:04"},
                {"locationName": "Terminus", "crs": "TRM", "sta": "12:10"},
            ]}],
        }
        wrong_route = {**matching, "subsequentCallingPoints": []}
        wrong_destination = {**matching, "destination": [{"locationName": "Elsewhere", "crs": "ELS"}]}
        self.board["trainServices"] = [
            {**wrong_route, "std": "12:20"}, {**wrong_destination, "std": "12:25"},
            {**matching, "std": "12:30"},
        ]
        self.board["busServices"] = [{**wrong_route, "std": "12:35"}]
        self.history = [
            {**wrong_route, "std": "11:59"}, {**wrong_destination, "std": "11:58"}, matching,
        ]
        board = RailDataClient("board-key", "").fetch("HWW", "TRM", "flt")
        self.assertEqual([row["scheduled"] for row in board["rows"]], ["12:30"])
        self.assertEqual(board["recent"]["scheduled"], "11:57")
        display = board["recent"]["display_stops"]
        self.assertEqual([stop["station"] for stop in display], ["How Wood", "Filtered", "Terminus"])
        self.assertEqual(display[0]["scheduled"], "11:57")
        self.assertEqual(display[-1]["timing"], "arrival")
        self.assertEqual(display[-1]["scheduled"], "12:10")
        self.assertEqual(len(board["recent"]["stops"]), 5)
        for call in self.request.call_args_list:
            query = parse_qs(urlsplit(call.args[0]).query)
            self.assertEqual(query["filterCrs"], ["FLT"])
            self.assertIn(query["filterType"], (["to"], ["from"]))
            self.assertNotIn("GetServiceDetails/", call.args[0])
        self.assertEqual(
            [parse_qs(urlsplit(call.args[0]).query)["filterType"][0]
             for call in self.request.call_args_list],
            ["to", "from"] * 3,
        )

    def test_calling_filter_combines_earlier_and_upcoming_matches_for_live_and_previous(self):
        def reply(url, key):
            if "GetServiceDetails/" in url:
                self.assertTrue(url.endswith("/live-earlier"))
                return {"std": "12:05"}
            query = parse_qs(urlsplit(url).query)
            earlier = query["filterType"] == ["from"]
            historical = "timeOffset" in query
            service = {
                "serviceID": ("previous-" if historical else "live-") + ("earlier" if earlier else "upcoming"),
                "std": ("11:59" if earlier else "11:58") if historical else ("12:05" if earlier else "12:10"),
                "destination": [{"locationName": "Terminus", "crs": "TRM"}],
            }
            wrong_destination = {
                **service, "serviceID": "wrong-destination",
                "destination": [{"locationName": "Elsewhere", "crs": "ELS"}],
            }
            return {
                **self.board, "filtercrs": "FLT", "filterLocationName": "Filtered",
                "trainServices": [service, wrong_destination],
            }

        self.request.side_effect = reply
        board = RailDataClient("board-key", "details-key").fetch("HWW", "TRM", "FLT")
        self.assertEqual([row["scheduled"] for row in board["rows"]], ["12:05", "12:10"])
        self.assertEqual(board["recent"]["scheduled"], "11:59")
        self.assertEqual([stop["station"] for stop in board["recent"]["display_stops"]],
                         ["How Wood", "Filtered", "Terminus"])
        self.assertEqual(board["recent"]["display_stops"][1]["scheduled"], "")
        self.assertEqual(self.request.call_count, 5)
        self.assertEqual(sum("GetServiceDetails/" in call.args[0]
                             for call in self.request.call_args_list), 1)

    def test_calling_filter_deduplicates_both_directions_and_limits_combined_services(self):
        self.board["filtercrs"] = "FLT"
        self.board["trainServices"] = [
            {"serviceID": str(index), "std": f"12:{index:02d}"}
            for index in range(10, 0, -1)
        ]
        self.board["busServices"] = [{"serviceID": "bus", "std": "12:00"}]
        self.history = []
        board = RailDataClient("board-key", "").fetch("HWW", "", "FLT")
        self.assertEqual([row["scheduled"] for row in board["rows"]],
                         [f"12:{index:02d}" for index in range(8)])
        self.assertEqual(board["rows"][0]["service_type"], "bus")

    def test_calling_filter_on_selected_station_does_not_exclude_its_services_upstream(self):
        board = RailDataClient("board-key", "").fetch("HWW", "", "HWW")
        self.assertEqual([row["scheduled"] for row in board["rows"]], ["12:30"])
        for call in self.request.call_args_list:
            self.assertNotIn("filterCrs", parse_qs(urlsplit(call.args[0]).query))

    def test_calling_filter_reports_failure_in_either_direction(self):
        self.request.side_effect = [self.board, RailDataError("earlier services unavailable")]
        with self.assertRaisesRegex(RailDataError, "earlier services unavailable"):
            RailDataClient("board-key", "").fetch("HWW", "", "FLT")

    def test_previous_display_deduplicates_roles_and_does_not_invent_destination_times(self):
        self.history = [{
            "std": "11:57", "destination": [{"locationName": "Terminus", "crs": "TRM"}],
        }]
        for filter_crs in ("", "HWW", "TRM"):
            with self.subTest(filter_crs=filter_crs):
                recent = RailDataClient("board-key", "").fetch("HWW", "", filter_crs)["recent"]
                display = recent["display_stops"]
                self.assertEqual([stop["station"] for stop in display], ["How Wood", "Terminus"])
                self.assertEqual(display[-1]["scheduled"], "")
                self.assertEqual(display[-1]["actual"], "")
                self.assertEqual(display[-1]["estimated"], "")

    def test_midnight_lookback_and_empty_or_failed_history(self):
        self.board["generatedAt"] = "2026-11-09T00:02:00Z"
        self.history = [{"serviceID": "yesterday", "std": "23:58"}]
        previous = RailDataClient("board-key", "details-key").fetch("HWW")["recent"]
        self.assertEqual(previous["scheduled"], "23:58")
        self.history = []
        self.request.reset_mock()
        empty = RailDataClient("board-key", "details-key").fetch("HWW")
        self.assertIsNone(empty["recent"])
        self.assertIn("two-hour", empty["recent_error"])
        self.assertEqual(self.request.call_count, 5)
        self.history_error = RailDataError("history unavailable")
        failed = RailDataClient("board-key", "details-key").fetch("HWW")
        self.assertEqual(failed["recent_error"], "history unavailable")


class RailDataTests(unittest.TestCase):
    def setUp(self):
        previous = patch.object(RailDataClient, "_previous_service", return_value=None)
        previous.start()
        self.addCleanup(previous.stop)

    def test_retains_live_confirmed_departure_without_polling_after_disappearance(self):
        service = {"serviceID": "run-1", "std": "11:55", "etd": "On time",
                   "destination": [{"locationName": "Terminus"}]}
        board_with_service = {"locationName": "Origin", "generatedAt": "2026-11-09T12:00:00Z",
                              "trainServices": [service]}
        details_without_actual = {"std": "11:55", "etd": "On time"}
        board_empty = {"locationName": "Origin", "generatedAt": "2026-11-09T12:05:00Z",
                       "trainServices": []}
        details_with_actual = {
            "std": "11:55", "atd": "12:02",
            "previousCallingPoints": [{"callingPoint": [
                {"locationName": "Earlier", "st": "11:30", "at": "11:33"},
            ]}],
            "subsequentCallingPoints": [{"callingPoint": [
                {"locationName": "Later", "st": "12:15", "et": "12:20", "at": "12:23"},
                {"locationName": "Terminus", "sta": "12:30", "ata": "12:36"},
            ]}],
        }
        client = RailDataClient("key", "details-key")
        with patch("webdisplay.raildata._request",
                   side_effect=[board_with_service, details_without_actual,
                                board_with_service, details_with_actual, board_empty]) as request:
            first = client.fetch("HWW")
            self.assertIsNone(first["recent"])
            confirmed = client.fetch("HWW")
            second = client.fetch("HWW")
        self.assertEqual(request.call_count, 5)
        self.assertEqual(second["recent"], confirmed["recent"])
        recent = second["recent"]
        self.assertEqual(recent["scheduled"], "11:55")
        self.assertEqual(recent["actual"], "12:02")
        self.assertEqual(recent["delay_minutes"], 7)
        self.assertEqual(recent["status"], "Arrived at Terminus at 12:36")
        self.assertEqual(recent["last_seen"], {"station": "Terminus", "actual": "12:36"})
        self.assertEqual([point["station"] for point in recent["stops"]],
                         ["Earlier", "Origin", "Later", "Terminus"])
        self.assertEqual(recent["stops"][0]["timing"], "reported")
        self.assertEqual(recent["stops"][0]["delay_minutes"], 3)
        self.assertEqual(recent["stops"][1]["timing"], "departure")
        self.assertEqual(recent["stops"][-1]["timing"], "arrival")
        self.assertEqual(recent["stops"][-1]["delay_minutes"], 6)

    def test_monitored_arrival_is_not_reported_as_destination_arrival(self):
        details = {
            "sta": "08:55", "ata": "08:55", "std": "08:56", "atd": "08:57",
            "previousCallingPoints": [{"callingPoint": [
                {"locationName": "Previous", "st": "08:50", "at": "08:51"},
            ]}],
            "subsequentCallingPoints": [{"callingPoint": [
                {"locationName": "St Albans Abbey", "crs": "SAA", "st": "09:02", "et": "09:03"},
            ]}],
        }
        recent = _recent_details(
            details, {"destination": [{"locationName": "St Albans Abbey"}]},
            "HWW", "How Wood", datetime.fromisoformat("2026-11-09T08:56:00+00:00"), 123,
        )
        self.assertEqual(recent["last_seen"], {"station": "How Wood", "actual": "08:57"})
        self.assertEqual(recent["status"], "Departed at 08:57 (1 min late)")
        self.assertEqual([(point["station"], point["timing"]) for point in recent["stops"]],
                         [("Previous", "reported"), ("How Wood", "arrival"),
                          ("How Wood", "departure"), ("St Albans Abbey", "reported")])
        self.assertEqual(recent["stops"][-1]["actual"], "")
        self.assertIsNone(recent["stops"][-1]["delay_minutes"])

    def test_monitored_arrival_and_departure_same_minute_prefers_departure_across_midnight(self):
        for arrival, scheduled_arrival in (("00:01", "23:59"), ("23:59", "23:59")):
            with self.subTest(arrival=arrival):
                recent = _recent_details(
                    {"sta": scheduled_arrival, "ata": arrival, "std": "00:01", "atd": "00:01"},
                    {}, "HWW", "How Wood",
                    datetime.fromisoformat("2026-11-09T00:01:00+00:00"), 123,
                )
                self.assertEqual(recent["last_seen"], {"station": "How Wood", "actual": "00:01"})
                self.assertEqual(recent["status"], "Departed at 00:01")
                self.assertEqual([point["timing"] for point in recent["stops"]],
                                 ["arrival", "departure"])

    def test_delayed_older_schedule_can_overtake_recent_departure(self):
        client = RailDataClient("key", "details-key")
        a = {"serviceID": "a", "std": "10:00"}
        b = {"serviceID": "b", "std": "10:05"}
        initial = {"generatedAt": "2026-11-09T10:06:00Z", "trainServices": [b, a]}
        delayed = {"generatedAt": "2026-11-09T10:11:00Z", "trainServices": [a]}
        with patch("webdisplay.raildata._request", side_effect=[
            initial, {"std": "10:05", "atd": "10:05"},
            delayed, {"std": "10:00", "atd": "10:10"},
        ]) as request:
            self.assertEqual(client.fetch("HWW")["recent"]["actual"], "10:05")
            recent = client.fetch("HWW")["recent"]
        self.assertEqual(recent["scheduled"], "10:00")
        self.assertEqual(recent["actual"], "10:10")
        self.assertEqual(request.call_count, 4)

    def test_dropped_candidates_never_trigger_details_requests(self):
        client = RailDataClient("key", "details-key")
        board = {"generatedAt": "2026-11-09T10:06:00Z", "trainServices": [
            {"serviceID": "a", "std": "10:00"}, {"serviceID": "b", "std": "10:05"},
        ]}
        empty = {"generatedAt": "2026-11-09T10:11:00Z", "trainServices": []}
        expired = {"generatedAt": "2026-11-10T10:06:00Z", "trainServices": []}
        with patch("webdisplay.raildata._request", side_effect=[
            board, {"std": "10:00"}, empty, empty, expired,
        ]) as request:
            for value in (board, empty, empty, expired):
                self.assertIsNone(client.fetch("HWW")["recent"])
        self.assertEqual(request.call_count, 5)
        detail_calls = [call for call in request.call_args_list
                        if "GetServiceDetails/" in call.args[0]]
        self.assertEqual(len(detail_calls), 1)
        self.assertTrue(detail_calls[0].args[0].endswith("/a"))

    def test_live_details_refresh_last_seen_even_when_departure_unchanged(self):
        client = RailDataClient("key", "details-key")
        board = {"generatedAt": "2026-11-09T10:06:00Z", "trainServices": [
            {"serviceID": "a", "std": "10:00"},
        ]}
        details = {"std": "10:00", "atd": "10:01"}
        progressed = {**details, "subsequentCallingPoints": [{"callingPoint": [
            {"locationName": "Next station", "st": "10:05", "at": "10:06"},
        ]}]}
        with patch("webdisplay.raildata._request", side_effect=[
            board, details, board, progressed,
        ]) as request:
            self.assertEqual(client.fetch("HWW")["recent"]["last_seen"]["actual"], "10:01")
            recent = client.fetch("HWW")["recent"]
        self.assertEqual(recent["actual"], "10:01")
        self.assertEqual(recent["last_seen"], {"station": "Next station", "actual": "10:06"})
        self.assertEqual(recent["status"], "Reported at Next station at 10:06 (timing unspecified)")
        self.assertEqual(request.call_count, 4)

    def test_disappearance_schedule_and_cancellation_never_confirm_departure(self):
        service = {"serviceID": "run-1", "std": "11:55", "etd": "On time"}
        boards = [
            {"generatedAt": "2026-11-09T12:00:00Z", "trainServices": [service]},
            {"generatedAt": "2026-11-09T12:05:00Z", "trainServices": []},
            {"generatedAt": "2026-11-09T12:06:00Z", "trainServices": [
                {**service, "serviceID": "", "isCancelled": True, "etd": "Cancelled"},
            ]},
        ]
        details = {"std": "11:55", "etd": "On time", "ata": "12:30"}
        with patch("webdisplay.raildata._request",
                   side_effect=[boards[0], details, boards[1], boards[2]]) as request:
            client = RailDataClient("key", "details-key")
            self.assertIsNone(client.fetch("HWW")["recent"])
            self.assertIsNone(client.fetch("HWW")["recent"])
            self.assertIsNone(client.fetch("HWW")["recent"])
        self.assertEqual(request.call_count, 4)

    def test_promotes_actual_departures_across_midnight_by_dated_run_order(self):
        client = RailDataClient("key", "details-key")
        before_midnight = {"generatedAt": "2026-11-08T23:58:00Z", "trainServices": [
            {"serviceID": "late", "std": "23:55", "etd": "On time"},
        ]}
        after_midnight = {"generatedAt": "2026-11-09T00:02:00Z", "trainServices": [
            {"serviceID": "early", "std": "00:01", "etd": "On time"},
        ]}
        with patch("webdisplay.raildata._request", side_effect=[
            before_midnight, {"std": "23:55", "atd": "23:59"},
            after_midnight, {"std": "00:01", "atd": "00:05"},
        ]):
            first = client.fetch("HWW")
            second = client.fetch("HWW")
        self.assertEqual(first["recent"]["scheduled"], "23:55")
        self.assertEqual(second["recent"]["scheduled"], "00:01")
        self.assertEqual(second["recent"]["delay_minutes"], 4)

    def test_scheduled_order_does_not_replace_a_later_actual_departure(self):
        client = RailDataClient("key", "details-key")
        first_board = {"generatedAt": "2026-11-09T12:21:00Z", "trainServices": [
            {"serviceID": "actual-later", "std": "11:55", "etd": "Delayed"},
        ]}
        second_board = {"generatedAt": "2026-11-09T12:22:00Z", "trainServices": [
            {"serviceID": "scheduled-later", "std": "12:10", "etd": "Delayed"},
        ]}
        with patch("webdisplay.raildata._request", side_effect=[
            first_board, {"std": "11:55", "atd": "12:20"},
            second_board, {"std": "12:10", "atd": "12:15"},
        ]):
            first = client.fetch("HWW")
            second = client.fetch("HWW")
        self.assertEqual(first["recent"]["scheduled"], "11:55")
        self.assertEqual(second["recent"]["scheduled"], "11:55")
        self.assertEqual(second["recent"]["actual"], "12:20")

    def test_live_details_failure_does_not_replace_retained_departure(self):
        client = RailDataClient("key", "details-key")
        board = {"generatedAt": "2026-11-09T12:00:00Z", "trainServices": [
            {"serviceID": "run-1", "std": "11:50", "etd": "On time"},
        ]}
        empty = {"generatedAt": "2026-11-09T12:05:00Z", "trainServices": []}
        confirmed = {"std": "11:50", "atd": "11:52"}
        with patch("webdisplay.raildata._request", side_effect=[
            board, confirmed, board, RailDataError("unavailable"), empty,
        ]) as request:
            first = client.fetch("HWW")
            second = client.fetch("HWW")
            third = client.fetch("HWW")
        self.assertEqual(first["recent"]["actual"], "11:52")
        self.assertEqual(second["recent"]["actual"], "11:52")
        self.assertFalse(second["details_available"])
        self.assertEqual(second["details_error"], "unavailable")
        self.assertFalse(third["recent"]["stale"])
        self.assertEqual(third["recent"]["actual"], "11:52")
        self.assertEqual(request.call_count, 5)
        self.assertEqual(third["recent_error"], "")

    def test_multiple_dropped_services_do_not_trigger_detail_requests(self):
        client = RailDataClient("key", "details-key")
        board = {"generatedAt": "2026-11-09T12:00:00Z", "trainServices": [
            {"serviceID": f"run-{index}", "std": f"11:{index:02d}", "etd": "On time"}
            for index in range(8)
        ]}
        empty = {"generatedAt": "2026-11-09T12:05:00Z", "trainServices": []}
        with patch("webdisplay.raildata._request", side_effect=[
            board, {"std": "11:00", "etd": "On time"}, empty,
        ]) as request:
            client.fetch("HWW")
            client.fetch("HWW")
        self.assertEqual(request.call_count, 3)

    def test_changing_station_resets_client_recent_history(self):
        client = RailDataClient("key", "details-key")
        board = {"generatedAt": "2026-11-09T12:00:00Z", "trainServices": [
            {"serviceID": "run-1", "std": "11:55", "etd": "On time"},
        ]}
        with patch("webdisplay.raildata._request", side_effect=[
            board, {"std": "11:55", "atd": "11:57"}, board, {"std": "11:55"},
        ]):
            self.assertIsNotNone(client.fetch("HWW")["recent"])
            changed_station = client.fetch("PAD")
        self.assertIsNone(changed_station["recent"])

    def test_changing_or_clearing_destination_resets_retained_departure(self):
        client = RailDataClient("key", "details-key")
        board = {"generatedAt": "2026-11-09T12:00:00Z", "trainServices": [
            {"serviceID": "run-1", "std": "11:55"},
        ]}
        with patch("webdisplay.raildata._request", side_effect=[
            board, {"std": "11:55", "atd": "11:57"},
            board, {"std": "11:55"},
            board, {"std": "11:55", "atd": "11:58"},
            board, {"std": "11:55"},
        ]) as request:
            self.assertIsNotNone(client.fetch("HWW", "WFJ")["recent"])
            self.assertIsNone(client.fetch("HWW", "SAA")["recent"])
            self.assertIsNotNone(client.fetch("HWW", "SAA")["recent"])
            self.assertIsNone(client.fetch("HWW", "")["recent"])
        self.assertNotIn("filterCrs", request.call_args_list[-2].args[0])

    def test_worker_reuses_tracker_and_drops_cached_board_when_station_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "train-board.json"
            scene = {"id": "station", "kind": "train", "enabled": True, "station_crs": "HWW"}
            config = {"raildata_api_key": "key", "service_details_api_key": "details",
                      "playlist": {"enabled": True, "scenes": [scene]}}
            service = {"serviceID": "run-1", "std": "11:55", "etd": "On time"}
            first_board = {"generatedAt": "2026-11-09T12:00:00Z", "trainServices": [service]}
            second_board = {"generatedAt": "2026-11-09T12:05:00Z", "trainServices": []}
            new_station_board = {"locationName": "Paddington", "trainServices": [service]}
            with patch("webdisplay.raildata._request", side_effect=[
                first_board, {"std": "11:55", "atd": "12:02"}, second_board,
                new_station_board, {"std": "11:55", "etd": "On time"},
            ]):
                worker = RailDataWorker(lambda: config, path, interval=3600)
                try:
                    worker.close()
                    worker.refresh_once()
                    worker._last_refresh_at = None
                    worker.refresh_once()
                    self.assertEqual(json.loads(path.read_text())["station"]["recent"]["actual"], "12:02")
                    scene["station_crs"] = "PAD"
                    worker._last_refresh_at = None
                    worker.refresh_once()
                finally:
                    worker.close()
            cached = json.loads(path.read_text())["station"]
            self.assertEqual(cached["crs"], "PAD")
            self.assertIsNone(cached["recent"])

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

    def test_api_preserves_http_status(self):
        error = HTTPError("https://api.example/details", 500, "Internal Server Error", {},
                          io.BytesIO(b'{"Message":"Unable to retrieve the requested data"}'))
        self.addCleanup(error.close)
        with patch("webdisplay.raildata.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RailDataError, "HTTP 500") as raised:
                _request("https://api.example/details", "private-key")
        self.assertEqual(raised.exception.status_code, 500)

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

            def fetch(key, details_key, crs, destination_crs, filter_station_crs):
                calls.append((key, details_key, crs, destination_crs, filter_station_crs))
                return {"crs": crs, "rows": [], "stale": False}

            worker = RailDataWorker(lambda: config, path, interval=3600, fetch=fetch)
            try:
                worker.refresh_once()
                worker.refresh_once()
                published = path.read_text()
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0], ("private-key", "private-details-key", "PAD", "", ""))
                self.assertNotIn("private-key", published)
                self.assertNotIn("private-details-key", published)
                self.assertEqual(len(json.loads(published)), 1)
            finally:
                worker.close()

    def test_worker_groups_by_destination_and_discards_changed_filter_cache_during_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "train-board.json"
            scenes = [
                {"id": scene_id, "kind": "train", "enabled": True,
                 "station_crs": "HWW", "destination_crs": destination}
                for scene_id, destination in (("one", "WFJ"), ("copy", "WFJ"), ("other", "SAA"))
            ]
            config = {"raildata_api_key": "key", "playlist": {"enabled": False, "scenes": scenes}}
            worker = RailDataWorker(lambda: config, path, interval=3600)
            worker.close()
            config["playlist"]["enabled"] = True

            def board_for_destination(url, _key):
                destination = parse_qs(urlsplit(url).query)["filterCrs"][0]
                return {"trainServices": [{
                    "std": "12:30", "destination": [{"locationName": destination}],
                }]}

            with patch("webdisplay.raildata._request", side_effect=board_for_destination) as request:
                for _ in range(2):
                    worker._last_refresh_at = None
                    worker.refresh_once()
                self.assertEqual(request.call_count, 2)
                self.assertEqual(len(worker._clients), 2)
                cached = json.loads(path.read_text())
                self.assertEqual(cached["one"], cached["copy"])
                self.assertEqual(cached["one"]["destination_crs"], "WFJ")
                self.assertEqual(cached["other"]["destination_crs"], "SAA")
                scenes[0]["destination_crs"] = ""
                worker.refresh_once()
                cached = json.loads(path.read_text())
                self.assertNotIn("one", cached)
                self.assertEqual(cached["copy"]["destination_crs"], "WFJ")
                self.assertEqual(request.call_count, 2)
            worker._last_refresh_at = None
            worker._cursor = 0
            with patch("webdisplay.raildata._request", side_effect=RailDataError("offline")):
                worker.refresh_once()
            cached = json.loads(path.read_text())["one"]
            self.assertEqual(cached["destination_crs"], "")
            self.assertEqual(cached["error"], "offline")
            self.assertNotIn("rows", cached)

    def test_worker_isolates_calling_station_filters_and_clears_changed_filter_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "train-board.json"
            scenes = [
                {"id": scene_id, "kind": "train", "enabled": True,
                 "station_crs": "HWW", "filter_station_crs": filter_crs}
                for scene_id, filter_crs in (("one", "BWO"), ("other", "PKS"))
            ]
            config = {"raildata_api_key": "key", "playlist": {"enabled": False, "scenes": scenes}}
            worker = RailDataWorker(lambda: config, path, interval=3600)
            worker.close()
            config["playlist"]["enabled"] = True
            with patch("webdisplay.raildata._request", return_value={"trainServices": []}) as request:
                for _ in range(2):
                    worker._last_refresh_at = None
                    worker.refresh_once()
                cached = json.loads(path.read_text())
                self.assertEqual(cached["one"]["filter_station_crs"], "BWO")
                self.assertEqual(cached["other"]["filter_station_crs"], "PKS")
                self.assertEqual(len(worker._clients), 2)
                scenes[0]["filter_station_crs"] = "PKS"
                worker.refresh_once()
                self.assertNotIn("one", json.loads(path.read_text()))
                self.assertEqual(request.call_count, 4)

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
                fetch=lambda *args: calls.append(args) or {},
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
            path.write_text(json.dumps({"pad": {"crs": "PAD", "rows": [{"scheduled": "10:00"}]}}))
            config = {
                "raildata_api_key": "private-key",
                "playlist": {"enabled": True, "scenes": [
                    {"id": "pad", "kind": "train", "enabled": True, "station_crs": "PAD"},
                ]},
            }

            def fail(_key, _details_key, _crs, _destination_crs, _filter_station_crs):
                raise RailDataError("upstream unavailable")

            worker = RailDataWorker(lambda: config, path, interval=3600, fetch=fail)
            try:
                worker.refresh_once()
                payload = json.loads(path.read_text())
                self.assertEqual(payload["pad"]["rows"], [{"scheduled": "10:00"}])
                self.assertTrue(payload["pad"]["stale"])
            finally:
                worker.close()
