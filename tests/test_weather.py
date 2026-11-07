import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from webdisplay.weather import (
    WeatherError, WeatherWorker, _request, fetch_weather, validate_weather_config,
)


class WeatherTests(unittest.TestCase):
    def test_location_and_key_validation(self):
        self.assertEqual(validate_weather_config({"city": " St Albans ", "country": "gb"}),
                         {"city": "St Albans", "country": "GB", "api_key": ""})
        for changes in ({"city": ""}, {"country": "UKK"}, {"api_key": "bad key"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_weather_config(changes)

    def test_requests_use_metric_units_and_errors_never_expose_the_key(self):
        settings = validate_weather_config({"api_key": "private-key"})
        with patch("webdisplay.weather.urllib.request.urlopen") as opener:
            opener.return_value.__enter__.return_value = io.BytesIO(b'{"main": {}}')
            _request("weather", settings)
            url = opener.call_args.args[0].full_url
            self.assertIn("q=St+Albans%2CGB", url)
            self.assertIn("units=metric", url)
            opener.side_effect = HTTPError(url, 401, "bad key", {}, io.BytesIO())
            with self.assertRaises(WeatherError) as caught:
                _request("weather", settings)
            self.assertNotIn("private-key", str(caught.exception))
            self.assertIn("activated", str(caught.exception))

    def test_current_conditions_and_forecast_use_location_local_dates(self):
        current = {
            "main": {"temp": 10.5, "feels_like": 8, "humidity": 80},
            "wind": {"speed": 2}, "weather": [{"description": "cloudy"}], "dt": 100,
        }
        entries = [
            {"dt": stamp, "main": {"temp": temp}, "weather": [{"description": "rain"}]}
            for stamp, temp in ((82800, 9), (93600, 11))
        ]
        with patch("webdisplay.weather._request", side_effect=[
            current, {"city": {"timezone": 3600}, "list": entries},
        ]):
            result = fetch_weather(validate_weather_config({"api_key": "private-key"}))
        self.assertEqual(result["wind_kmh"], 7.2)
        self.assertEqual(result["forecast"], [{
            "date": "1970-01-02", "low": 9, "high": 11, "description": "rain",
        }])
        self.assertNotIn("private-key", json.dumps(result))
        with patch("webdisplay.weather._request", side_effect=[current, WeatherError("forecast unavailable")]):
            result = fetch_weather(validate_weather_config({}))
        self.assertEqual(result["temperature"], 10.5)
        self.assertEqual(result["forecast_error"], "forecast unavailable")

    def test_worker_only_fetches_for_active_weather_and_retains_stale_data(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"weather": {"api_key": "private-key"},
                      "playlist": {"enabled": False, "scenes": []}}
            fetch = Mock(return_value={"temperature": 12, "error": "", "stale": False})
            worker = WeatherWorker(lambda: config, Path(directory) / "weather.json", fetch)
            worker.close()
            fetch.assert_not_called()
            config["playlist"] = {"enabled": True, "scenes": [
                {"kind": "dashboard", "enabled": True, "show_weather": True},
            ]}
            with patch("webdisplay.weather.time.monotonic", return_value=100):
                worker.refresh_once()
            fetch.assert_called_once()
            with patch("webdisplay.weather.time.monotonic", return_value=699):
                worker.refresh_once()
            fetch.assert_called_once()
            fetch.side_effect = WeatherError("offline")
            with patch("webdisplay.weather.time.monotonic", return_value=700):
                worker.refresh_once()
            data = json.loads(worker.path.read_text())
            self.assertEqual(data["temperature"], 12)
            self.assertTrue(data["stale"])
            self.assertNotIn("private-key", json.dumps(data))
            config["weather"]["city"] = "London"
            worker.refresh_once()
            self.assertNotIn("temperature", json.loads(worker.path.read_text()))
            config["playlist"]["scenes"] = [{"kind": "train", "enabled": True}]
            worker._last_refresh = None
            fetch.side_effect = None
            worker.refresh_once()
            self.assertEqual(json.loads(worker.path.read_text())["temperature"], 12)
