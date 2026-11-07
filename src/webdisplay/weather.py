from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .storage import atomic_write

LOG = logging.getLogger(__name__)
REFRESH_SECONDS = 600
RETRY_SECONDS = 30


class WeatherError(ValueError):
    pass


def validate_weather_config(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("Weather settings must be an object")
    city = value.get("city", "St Albans")
    country = value.get("country", "GB")
    key = value.get("api_key", "")
    if not isinstance(city, str) or not 1 <= len(city.strip()) <= 100 or any(
        ord(char) < 32 for char in city
    ):
        raise ValueError("Weather city must contain 1-100 characters")
    if not isinstance(country, str) or not re.fullmatch(r"[A-Za-z]{2}", country):
        raise ValueError("Weather country must be a two-letter country code")
    if not isinstance(key, str) or len(key) > 512 or any(
        ord(char) < 33 or ord(char) > 126 for char in key
    ):
        raise ValueError("Invalid OpenWeather API key")
    return {"city": city.strip(), "country": country.upper(), "api_key": key}


def _request(endpoint: str, settings: dict[str, str]) -> dict[str, object]:
    query = urllib.parse.urlencode({
        "q": f'{settings["city"]},{settings["country"]}',
        "appid": settings["api_key"], "units": "metric",
    })
    request = urllib.request.Request(
        f"https://api.openweathermap.org/data/2.5/{endpoint}?{query}",
        headers={"Accept": "application/json", "User-Agent": "WebDisplay/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            value = json.load(response)
    except urllib.error.HTTPError as error:
        error.close()
        messages = {
            401: "OpenWeather rejected the API key; check that it is valid and activated",
            404: "OpenWeather could not find this city and country",
            429: "OpenWeather request limit reached",
        }
        raise WeatherError(messages.get(error.code, f"OpenWeather returned HTTP {error.code}")) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise WeatherError("OpenWeather data is temporarily unavailable") from None
    if not isinstance(value, dict):
        raise WeatherError("OpenWeather returned invalid data")
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise WeatherError("OpenWeather returned invalid weather measurements")
    return value


def _description(value: dict[str, object]) -> str:
    conditions = value.get("weather")
    if not isinstance(conditions, list) or not conditions or not isinstance(conditions[0], dict):
        raise WeatherError("OpenWeather returned invalid conditions")
    description = conditions[0].get("description")
    if not isinstance(description, str) or not description:
        raise WeatherError("OpenWeather returned invalid conditions")
    return description


def _icon(value: dict[str, object]) -> str:
    conditions = value.get("weather")
    if isinstance(conditions, list) and conditions and isinstance(conditions[0], dict):
        icon = conditions[0].get("icon")
        if isinstance(icon, str) and re.fullmatch(r"(01|02|03|04|09|10|11|13|50)[dn]", icon):
            return icon
    return ""


def fetch_weather(settings: dict[str, str]) -> dict[str, object]:
    current = _request("weather", settings)
    main, wind = current.get("main"), current.get("wind")
    if not isinstance(main, dict) or not isinstance(wind, dict):
        raise WeatherError("OpenWeather returned invalid current weather")
    result = {
        "location": f'{settings["city"]}, {settings["country"]}',
        "temperature": _number(main.get("temp")),
        "feels_like": _number(main.get("feels_like")),
        "humidity": _number(main.get("humidity")),
        "wind_kmh": round(_number(wind.get("speed")) * 3.6, 1),
        "description": _description(current),
        "icon": _icon(current),
        "observed_at": _number(current.get("dt")),
        "updated_at": int(time.time()),
        "forecast": [],
        "forecast_error": "",
        "stale": False,
        "error": "",
    }
    try:
        forecast = _request("forecast", settings)
        entries, city = forecast.get("list"), forecast.get("city")
        if not isinstance(entries, list) or not entries or not isinstance(city, dict):
            raise WeatherError("OpenWeather returned invalid forecast data")
        offset = _number(city.get("timezone"))
        days: dict[str, dict[str, object]] = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("main"), dict):
                raise WeatherError("OpenWeather returned invalid forecast data")
            stamp = datetime.fromtimestamp(_number(entry.get("dt")) + offset, timezone.utc)
            date = stamp.strftime("%Y-%m-%d")
            temperature = _number(entry["main"].get("temp"))
            description = _description(entry)
            proximity = abs(stamp.hour - 12)
            if date not in days:
                days[date] = {
                    "date": date, "low": temperature, "high": temperature,
                    "description": description, "proximity": proximity,
                    "icon": _icon(entry),
                }
            else:
                day = days[date]
                day["low"] = min(day["low"], temperature)
                day["high"] = max(day["high"], temperature)
                if proximity < day["proximity"]:
                    day["description"], day["proximity"] = description, proximity
                    day["icon"] = _icon(entry)
        result["forecast"] = [
            {key: value for key, value in day.items() if key != "proximity"}
            for _, day in sorted(days.items())[:5]
        ]
    except (WeatherError, OverflowError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, WeatherError) else "OpenWeather returned invalid forecast times"
        LOG.warning("Weather forecast unavailable: %s", message)
        result["forecast_error"] = message
    return result


class WeatherWorker:
    def __init__(
        self, config: Callable[[], dict[str, object]], path: Path,
        fetch: Callable[[dict[str, str]], dict[str, object]] = fetch_weather,
    ):
        self.config, self.path, self.fetch = config, path, fetch
        self._stop, self._wake = threading.Event(), threading.Event()
        self._last_refresh: float | None = None
        self._refresh_failed = False
        self._settings: dict[str, str] | None = None
        self._cached: dict[str, object] = {}
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def _publish(self, data: dict[str, object]) -> None:
        self._cached = data
        try:
            atomic_write(self.path, json.dumps(data) + "\n", mode=0o640)
        except OSError:
            LOG.exception("Could not publish cached weather")

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh_once()
            elapsed = time.monotonic() - self._last_refresh if self._last_refresh is not None else 0
            interval = RETRY_SECONDS if self._refresh_failed else REFRESH_SECONDS
            self._wake.wait(interval - elapsed if elapsed < interval else interval)
            self._wake.clear()

    def refresh_once(self) -> None:
        config = self.config()
        settings = validate_weather_config(config.get("weather", {}))
        if settings != self._settings:
            self._settings = settings
            self._publish({"error": "Waiting for weather information", "stale": False})
        if not settings["api_key"]:
            self._publish({"error": "Configure an OpenWeather API key in device settings", "stale": False})
            return
        playlist = config.get("playlist", {})
        if not playlist.get("enabled") or not any(
            scene.get("enabled") and (scene.get("kind") in ("weather", "train") or
                                     scene.get("kind") == "dashboard" and scene.get("show_weather"))
            for scene in playlist.get("scenes", [])
        ):
            return
        now = time.monotonic()
        interval = RETRY_SECONDS if self._refresh_failed else REFRESH_SECONDS
        if self._last_refresh is not None and now - self._last_refresh < interval:
            return
        self._last_refresh = now
        try:
            data = self.fetch(settings)
        except WeatherError as error:
            self._refresh_failed = True
            LOG.warning("Weather refresh failed: %s", error)
            data = {**self._cached, "error": str(error), "stale": "temperature" in self._cached}
            self._publish(data)
        else:
            self._refresh_failed = bool(data.get("forecast_error"))
            self._publish(data)

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=25)
