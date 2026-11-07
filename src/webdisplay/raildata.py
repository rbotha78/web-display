from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

from .storage import atomic_write

LOG = logging.getLogger(__name__)
REFRESH_INTERVAL = 60
RETRY_INTERVAL = 30
REQUEST_TIMEOUT = 10
BOARD_URL = (
    "https://api1.raildata.org.uk/1010-live-departure-board-dep1_2/"
    "LDBWS/api/20220120/GetDepBoardWithDetails/"
)
DETAILS_URL = (
    "https://api1.raildata.org.uk/1010-service-details1_2/"
    "LDBWS/api/20220120/GetServiceDetails/"
)


class RailDataError(ValueError):
    pass


def validate_api_key(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise ValueError("Rail Data API key must contain 1-512 characters")
    if any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise ValueError("Rail Data API key contains invalid characters")
    return value


def validate_crs(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{3}", value):
        raise ValueError("Station CRS code must contain three letters")
    return value.upper()


def validate_refresh_interval(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 30:
        raise ValueError("Rail Data refresh interval must be a whole number of at least 30 seconds")
    return value


def _request(url: str, api_key: str) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        headers={
            "x-apikey": api_key,
            "Accept": "application/json",
            "User-Agent": "WebDisplay/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            payload = json.loads(response.read(1024 * 1024))
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise RailDataError(
                f"National Rail API rejected the request (HTTP {error.code}); "
                "check API key and product access"
            ) from error
        if error.code == 429:
            raise RailDataError("National Rail API request was throttled (HTTP 429)") from error
        raise RailDataError(f"National Rail API returned HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise RailDataError("National Rail data is temporarily unavailable") from error
    if not isinstance(payload, dict):
        raise RailDataError("National Rail returned an invalid response")
    return payload


def _location_name(value: object) -> str:
    if not isinstance(value, list):
        return ""
    names = [
        item.get("locationName")
        for item in value
        if isinstance(item, dict) and isinstance(item.get("locationName"), str)
    ]
    return names[-1] if names else ""


def _clock_minutes(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError:
        return None
    return parsed.hour * 60 + parsed.minute


def _delay_minutes(scheduled: object, actual: object) -> int | None:
    planned = _clock_minutes(scheduled)
    reported = _clock_minutes(actual)
    if planned is None or reported is None:
        return None
    difference = reported - planned
    if difference < -720:
        difference += 1440
    elif difference > 720:
        difference -= 1440
    return difference


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


class _MessageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br", "p", "div"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("p", "div"):
            self.parts.append(" ")


def _station_messages(value: object) -> list[str]:
    messages = []
    for item in value if isinstance(value, list) else []:
        text = item if isinstance(item, str) else (
            item.get("value", item.get("Value", "")) if isinstance(item, dict) else ""
        )
        if not isinstance(text, str):
            continue
        parser = _MessageText()
        parser.feed(text)
        parser.close()
        message = " ".join("".join(parser.parts).split())
        if message:
            messages.append(message)
    return messages


def _service_state(cancelled: bool, scheduled: str, estimated: str) -> str:
    if cancelled or estimated.lower() == "cancelled":
        return "cancelled"
    delay = _delay_minutes(scheduled, estimated)
    if estimated.lower() in ("delayed", "late") or delay is not None and delay > 0:
        return "delayed"
    if estimated.lower() == "on time" or delay == 0:
        return "on-time"
    return ""


def _service_notices(service: dict[str, object]) -> list[str]:
    notices = _station_messages(service.get("adhocAlerts", []))
    for field, label in (
        ("cancelReason", "Cancellation reason"),
        ("delayReason", "Delay reason"),
        ("overdueMessage", "Service update"),
    ):
        messages = _station_messages([service.get(field)])
        notices.extend(f"{label}: {message}" for message in messages)
    return list(dict.fromkeys(notices))


def _calling_points(value: object) -> list[list[dict[str, object]]]:
    groups = []
    for group in value if isinstance(value, list) else []:
        if not isinstance(group, dict):
            continue
        points = group.get("callingPoint")
        if not isinstance(points, list):
            continue
        normalized = [
            {
                "station": _string(point.get("locationName")),
                "scheduled": _string(point.get("st")),
                "estimated": _string(point.get("et")),
                "actual": _string(point.get("at")),
                "cancelled": point.get("isCancelled") is True,
            }
            for point in points
            if isinstance(point, dict) and _string(point.get("locationName"))
        ]
        if normalized:
            groups.append(normalized)
    return groups


class RailDataClient:
    def __init__(self, api_key: str, service_details_api_key: str | None = None):
        self.api_key = validate_api_key(api_key)
        self.service_details_api_key = (
            self.api_key if service_details_api_key is None
            else validate_api_key(service_details_api_key) if service_details_api_key
            else ""
        )

    def fetch(self, crs: str) -> dict[str, object]:
        crs = validate_crs(crs)
        board_url = BOARD_URL + urllib.parse.quote(crs, safe="") + "?numRows=8"
        board = _request(board_url, self.api_key)
        services: list[dict[str, object]] = []
        train_services = board.get("trainServices")
        if isinstance(train_services, list):
            services.extend(service for service in train_services if isinstance(service, dict))
        else:
            departures = board.get("departures", [])
            if not isinstance(departures, list):
                raise RailDataError("National Rail returned an invalid departure list")
            for departure in departures:
                if not isinstance(departure, dict):
                    continue
                service = departure.get("service")
                if isinstance(service, dict):
                    services.append(service)
        bus_services = board.get("busServices", [])
        if not isinstance(bus_services, list):
            raise RailDataError("National Rail returned an invalid bus departure list")
        buses = [
            {**service, "serviceType": "bus"}
            for service in bus_services if isinstance(service, dict)
        ]
        if buses:
            services.extend(buses)
            generated = _string(board.get("generatedAt"))
            try:
                reference = datetime.fromisoformat(generated.replace("Z", "+00:00")).strftime("%H:%M")
            except ValueError:
                reference = _string(services[0].get("std"))
            services.sort(key=lambda service: (
                _delay_minutes(reference, service.get("std"))
                if _clock_minutes(service.get("std")) is not None
                and _clock_minutes(reference) is not None else 1440
            ))

        rows = []
        for service in services:
            destinations = service.get("currentDestinations") or service.get("destination")
            scheduled = _string(service.get("std"))
            estimated = _string(service.get("etd")) or _string(service.get("eta"))
            cancelled = service.get("isCancelled") is True or estimated.lower() == "cancelled"
            rows.append({
                "scheduled": scheduled,
                "estimated": estimated,
                "destination": _location_name(destinations),
                "platform": _string(service.get("platform")),
                "operator": _string(service.get("operator")),
                "cancelled": cancelled,
                "state": _service_state(cancelled, scheduled, estimated),
                "service_type": "bus" if service.get("serviceType") == "bus" else "train",
                "alerts": _service_notices(service),
            })

        details: dict[str, object] = {}
        details_available = bool(self.service_details_api_key)
        details_error = "" if details_available else "Configure a Service Details API key"
        if services:
            service_id = _string(services[0].get("serviceID"))
            if service_id and self.service_details_api_key:
                details_url = DETAILS_URL + urllib.parse.quote(service_id, safe="")
                try:
                    details = _request(details_url, self.service_details_api_key)
                except RailDataError as error:
                    details_available = False
                    details_error = str(error)
                    LOG.warning("National Rail service details are temporarily unavailable: %s", error)
            elif self.service_details_api_key:
                details_available = False
                details_error = "The next service has no service ID"

        scheduled = _string(details.get("std")) or (rows[0]["scheduled"] if rows else "")
        estimated = _string(details.get("etd")) or (rows[0]["estimated"] if rows else "")
        actual = _string(details.get("atd"))
        cancelled = (details.get("isCancelled") is True or bool(rows and rows[0]["cancelled"])
                     or estimated.lower() == "cancelled")
        compare_time = actual or (estimated if estimated.lower() != "on time" else scheduled)
        delay = _delay_minutes(scheduled, compare_time)
        if cancelled:
            status = "Cancelled"
        elif actual:
            status = f"Departed at {actual}" + (f" ({delay} min late)" if delay and delay > 0 else "")
        elif estimated.lower() == "on time":
            status, delay = "On time", 0
        elif delay is not None and delay > 0:
            status = f"Delayed by {delay} min"
        elif delay is not None and delay < 0:
            status = f"Early by {abs(delay)} min"
        elif estimated:
            status = estimated
        else:
            status = "Status unavailable"
        if rows:
            rows[0]["alerts"] = list(dict.fromkeys([
                *rows[0]["alerts"], *_service_notices(details),
            ]))
            rows[0]["status"] = status
            rows[0]["state"] = _service_state(cancelled, scheduled, compare_time)
            if estimated.lower() == "on time" and not actual and not cancelled:
                rows[0]["state"] = "on-time"
        next_service = services[0] if services else {}
        calling_at = _calling_points(
            details.get("subsequentCallingPoints", next_service.get("subsequentCallingPoints"))
        )
        previous = _calling_points(
            details.get("previousCallingPoints", next_service.get("previousCallingPoints"))
        )
        reported = [
            point for group in previous for point in group
            if _clock_minutes(point["actual"]) is not None and not point["cancelled"]
        ]
        reference = scheduled if _clock_minutes(scheduled) is not None else "00:00"
        last_seen = max(
            reported, key=lambda point: _delay_minutes(reference, point["actual"]),
        ) if reported else None
        current_actual = actual or _string(details.get("ata"))
        if _clock_minutes(current_actual) is not None:
            last_seen = {
                "station": _string(board.get("locationName")) or crs,
                "actual": current_actual,
            }
        alerts = _station_messages(board.get("nrccMessages", []))
        return {
            "crs": crs,
            "station": _string(board.get("locationName")) or crs,
            "generated_at": _string(board.get("generatedAt")),
            "updated_at": int(time.time()),
            "services_available": board.get("areServicesAvailable") is not False,
            "platform_available": board.get("platformAvailable") is True,
            "rows": rows,
            "alerts": alerts,
            "details_available": details_available,
            "details_error": details_error,
            "next": {
                "scheduled": scheduled,
                "estimated": estimated,
                "actual": actual,
                "status": status,
                "delay_minutes": delay,
                "calling_at": calling_at,
                "previous_stops": previous,
                "last_seen": last_seen,
            } if rows else None,
            "stale": False,
            "error": "",
        }


class RailDataWorker:
    def __init__(
        self,
        config: Callable[[], dict[str, object]],
        path: Path,
        interval: int = REFRESH_INTERVAL,
        fetch: Callable[[str, str, str], dict[str, object]] | None = None,
    ):
        self.config = config
        self.path = path
        self.interval = interval
        self.fetch = fetch or (
            lambda key, details_key, crs: RailDataClient(key, details_key).fetch(crs)
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._cursor = 0
        self._last_refresh_at: float | None = None
        self._refresh_failed = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh_once()
            interval = self.config().get("raildata_refresh_seconds", self.interval)
            if self._refresh_failed:
                interval = min(interval, RETRY_INTERVAL)
            elapsed = (
                time.monotonic() - self._last_refresh_at
                if self._last_refresh_at is not None else 0
            )
            self._wake.wait(interval - elapsed if elapsed < interval else interval)
            self._wake.clear()

    def refresh_once(self) -> None:
        config = self.config()
        key = str(config.get("raildata_api_key", ""))
        playlist = config.get("playlist", {})
        if not isinstance(playlist, dict) or not playlist.get("enabled"):
            self._publish({})
            return
        scenes = playlist.get("scenes", [])
        train_scenes = [
            scene for scene in scenes
            if isinstance(scene, dict) and scene.get("kind") == "train" and scene.get("enabled")
        ]
        if not train_scenes:
            self._publish({})
            return
        boards = self._read()
        active_ids = {str(scene["id"]) for scene in train_scenes}
        boards = {scene_id: board for scene_id, board in boards.items() if scene_id in active_ids}
        if not key:
            for scene in train_scenes:
                boards[str(scene["id"])] = {
                    "error": "Configure a National Rail API key in device settings",
                    "stale": False,
                }
            self._publish(boards)
            return
        details_key = str(config.get("service_details_api_key", ""))
        station_groups: dict[str, list[str]] = {}
        for scene in train_scenes:
            try:
                crs = validate_crs(scene.get("station_crs"))
            except ValueError:
                continue
            station_groups.setdefault(crs, []).append(str(scene["id"]))
        if not station_groups:
            return
        with self._lock:
            grouped = list(station_groups.items())
            now = time.monotonic()
            interval = config.get("raildata_refresh_seconds", self.interval)
            if self._refresh_failed:
                interval = min(interval, RETRY_INTERVAL)
            if self._last_refresh_at is not None and now - self._last_refresh_at < interval:
                return
            cursor = self._cursor % len(grouped)
            crs, scene_ids = grouped[cursor]
            self._cursor = cursor + 1
            self._last_refresh_at = now
            try:
                result = self.fetch(key, details_key, crs)
            except RailDataError as error:
                self._refresh_failed = True
                LOG.warning("National Rail refresh failed for %s: %s", crs, error)
                for scene_id in scene_ids:
                    previous = boards.get(scene_id, {})
                    if isinstance(previous, dict) and previous.get("rows"):
                        previous = {**previous, "stale": True, "error": str(error)}
                    else:
                        previous = {"stale": False, "error": str(error)}
                    boards[scene_id] = previous
            else:
                self._refresh_failed = False
                for scene_id in scene_ids:
                    boards[scene_id] = result
            self._publish(boards)

    def _publish(self, boards: dict[str, object]) -> None:
        try:
            atomic_write(self.path, json.dumps(boards) + "\n", mode=0o640)
        except OSError:
            LOG.exception("Could not publish cached National Rail data")

    def _read(self) -> dict[str, object]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            LOG.exception("Could not read cached National Rail data")
            return {}
        return payload if isinstance(payload, dict) else {}

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=5)
