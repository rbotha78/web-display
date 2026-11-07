import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from webdisplay.sleep import (
    DAYS, SleepRuntime, default_schedule, sleep_status, validate_schedule, validate_wake_until,
)


class SleepTests(unittest.TestCase):
    def status(self, schedule, text, wake_until=None):
        return sleep_status(schedule, wake_until, now=datetime.fromisoformat(text))

    def test_daily_boundaries_and_sunday_overnight_into_disabled_monday(self):
        schedule = default_schedule()
        schedule["days"]["monday"] = {"enabled": True, "start": "09:00", "end": "17:00"}
        for clock, sleeping in (("08:59:59", False), ("09:00:00", True),
                                ("16:59:59", True), ("17:00:00", False)):
            with self.subTest(clock=clock):
                self.assertEqual(self.status(schedule, f"2026-11-02T{clock}+00:00")["sleeping"], sleeping)
        schedule["days"]["monday"]["enabled"] = False
        schedule["days"]["sunday"]["enabled"] = True
        for text, sleeping in (
            ("2026-11-01T21:59:59+00:00", False),
            ("2026-11-01T22:00:00+00:00", True),
            ("2026-11-02T00:00:00+00:00", True),
            ("2026-11-02T06:59:59+00:00", True),
            ("2026-11-02T07:00:00+00:00", False),
            ("2026-11-02T22:00:00+00:00", False),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.status(schedule, text)["sleeping"], sleeping)
        schedule["days"]["sunday"]["enabled"] = False
        self.assertFalse(self.status(schedule, "2026-11-02T02:00:00+00:00")["sleeping"])

    def test_temporary_wake_expires_at_epoch_boundary_without_changing_schedule(self):
        schedule = default_schedule()
        schedule["days"]["monday"]["enabled"] = True
        original = copy.deepcopy(schedule)
        now = datetime(2026, 11, 2, 23, tzinfo=timezone.utc)
        status = sleep_status(schedule, now.timestamp() + 60, now=now)
        self.assertTrue(status["scheduled_sleep"])
        self.assertFalse(status["sleeping"])
        self.assertEqual(status["wake_until"], now.timestamp() + 60)
        status = sleep_status(schedule, now.timestamp(), now=now)
        self.assertTrue(status["sleeping"])
        self.assertIsNone(status["wake_until"])
        self.assertEqual(schedule, original)
        with self.assertRaises(ValueError):
            sleep_status(schedule, now=datetime(2026, 11, 2, 23))

    def test_dst_uses_local_clock_on_both_folds_and_epoch_for_wake(self):
        zone = ZoneInfo("Europe/London")
        schedule = default_schedule()
        schedule["days"]["sunday"] = {"enabled": True, "start": "01:00", "end": "02:00"}
        first = datetime(2026, 10, 25, 1, 30, tzinfo=zone, fold=0)
        second = datetime(2026, 10, 25, 1, 30, tzinfo=zone, fold=1)
        deadline = first.timestamp() + 30 * 60
        self.assertFalse(sleep_status(schedule, deadline, now=first)["sleeping"])
        self.assertTrue(sleep_status(schedule, deadline, now=second)["sleeping"])
        self.assertTrue(sleep_status(schedule, now=first)["scheduled_sleep"])
        self.assertTrue(sleep_status(schedule, now=second)["scheduled_sleep"])
        self.assertFalse(sleep_status(schedule, now=datetime(2026, 10, 25, 2, tzinfo=zone))["sleeping"])
        schedule["days"]["sunday"] = {"enabled": True, "start": "00:30", "end": "02:30"}
        self.assertTrue(sleep_status(schedule, now=datetime(2026, 3, 29, 2, 15, tzinfo=zone))["sleeping"])
        self.assertFalse(sleep_status(schedule, now=datetime(2026, 3, 29, 2, 30, tzinfo=zone))["sleeping"])

    def test_strict_validation_and_disabled_equal_times(self):
        schedule = default_schedule()
        schedule["days"]["monday"]["start"] = schedule["days"]["monday"]["end"] = "12:00"
        self.assertEqual(validate_schedule(schedule), schedule)
        invalid = [None, {}, {"days": {}}, {**schedule, "wake_until": 123}]
        for changes in (
            {"enabled": True}, {"enabled": 1}, {"start": "24:00"},
            {"start": "1:00"}, {"end": "02:60"}, {"start": None}, {"extra": True},
        ):
            value = copy.deepcopy(schedule)
            value["days"]["monday"].update(changes)
            invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_schedule(value)
        for value in (True, "123", -1, float("inf"), float("nan"), 10**1000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_wake_until(value)

    def test_runtime_missing_defaults_and_bad_data_fail_closed_then_recovers(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            path = Path(directory) / "sleep.json"
            runtime = SleepRuntime(path)
            self.assertFalse(runtime.sleeping())
            for value in ("not json", "null", '{"days": {}}'):
                path.write_text(value)
                runtime.failed = False
                with self.assertLogs("webdisplay.sleep", level="ERROR") as logs:
                    self.assertTrue(runtime.sleeping())
                    self.assertTrue(runtime.sleeping())
                self.assertEqual(len(logs.output), 1)
            path.write_text(json.dumps({**default_schedule(), "wake_until": None}))
            self.assertFalse(runtime.sleeping())
            path.unlink()
            path.mkdir()
            with self.assertLogs("webdisplay.sleep", level="ERROR"):
                self.assertTrue(runtime.sleeping())

    def test_default_runtime_recomputes_device_local_time_from_epoch(self):
        schedule = default_schedule()
        for day in DAYS:
            schedule["days"][day] = {"enabled": True, "start": "00:00", "end": "23:59"}
        now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc).timestamp()
        with patch("webdisplay.sleep.time.time", return_value=now):
            status = sleep_status(schedule)
        self.assertEqual(datetime.fromisoformat(status["device_time"]).timestamp(), now)
        self.assertTrue(status["sleeping"])
