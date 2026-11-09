from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
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
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


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


def validate_optional_crs(value: object) -> str:
    return "" if value == "" else validate_crs(value)


def board_matches(board: object, crs: str, destination_crs: str = "", filter_station_crs: str = "") -> bool:
    return isinstance(board, dict) and (
        board.get("destination_crs", ""), board.get("filter_station_crs", "")
    ) == (destination_crs, filter_station_crs) and (
        board.get("crs") == crs or
        (not board.get("crs") and not board.get("rows") and isinstance(board.get("error"), str))
    )


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
                "check API key and product access", error.code,
            ) from error
        if error.code == 429:
            raise RailDataError("National Rail API request was throttled (HTTP 429)", error.code) from error
        raise RailDataError(f"National Rail API returned HTTP {error.code}", error.code) from error
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


def _board_trains(board: dict[str, object]) -> list[dict[str, object]]:
    services = board.get("trainServices")
    if isinstance(services, list):
        return [service for service in services if isinstance(service, dict)]
    departures = board.get("departures", [])
    if not isinstance(departures, list):
        raise RailDataError("National Rail returned an invalid departure list")
    return [
        departure["service"] for departure in departures
        if isinstance(departure, dict) and isinstance(departure.get("service"), dict)
    ]


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
                "crs": _string(point.get("crs")).upper(),
                "scheduled": _string(point.get("st")) or _string(point.get("sta")),
                "estimated": _string(point.get("et")) or _string(point.get("eta")),
                "actual": _string(point.get("at")) or _string(point.get("ata")),
                "delay_minutes": _delay_minutes(
                    _string(point.get("st")) or _string(point.get("sta")),
                    _string(point.get("at")) or _string(point.get("ata")),
                ),
                "timing": "arrival" if _string(point.get("sta")) or _string(point.get("ata"))
                else "reported",
                "cancelled": point.get("isCancelled") is True,
            }
            for point in points
            if isinstance(point, dict) and _string(point.get("locationName"))
        ]
        if normalized:
            groups.append(normalized)
    return groups


def _service_datetime(scheduled: object, reference: datetime) -> datetime | None:
    minutes = _clock_minutes(scheduled)
    if minutes is None:
        return None
    result = reference.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
    difference = (result - reference).total_seconds()
    if difference < -12 * 3600:
        result += timedelta(days=1)
    elif difference > 12 * 3600:
        result -= timedelta(days=1)
    return result


def _actual_datetime(actual: object, scheduled: object, run_time: datetime) -> datetime | None:
    minutes = _clock_minutes(actual)
    planned = _clock_minutes(scheduled)
    if minutes is None:
        return None
    result = run_time.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
    if planned is not None:
        difference = minutes - planned
        if difference < -720:
            result += timedelta(days=1)
        elif difference > 720:
            result -= timedelta(days=1)
    return result


def _point_run_time(scheduled: object, service_scheduled: object, run_time: datetime,
                    side: str, actual: object = "") -> datetime:
    point_minutes = _clock_minutes(scheduled)
    if point_minutes is None:
        point_minutes = _clock_minutes(actual)
    service_minutes = _clock_minutes(service_scheduled)
    if point_minutes is None:
        return run_time
    result = run_time
    if service_minutes is not None:
        difference = point_minutes - service_minutes
        if side == "previous" and difference > 720:
            result -= timedelta(days=1)
        elif side == "subsequent" and difference < -720:
            result += timedelta(days=1)
    return result


def _recent_details(details: dict[str, object], service: dict[str, object], crs: str,
                    station: str, run_time: datetime, updated_at: int) -> dict[str, object]:
    scheduled = _string(details.get("std")) or _string(service.get("std"))
    actual = _string(details.get("atd")) if _clock_minutes(details.get("atd")) is not None else ""
    estimated = _string(details.get("etd"))
    delay = _delay_minutes(scheduled, actual)
    destination_locations = details.get("currentDestinations") or (
        service.get("currentDestinations") or service.get("destination")
    )
    destination = _location_name(destination_locations)
    destination_crs = next((
        _string(location.get("crs")).upper() for location in reversed(destination_locations)
        if isinstance(location, dict) and location.get("locationName") == destination
    ), "") if isinstance(destination_locations, list) else ""
    if actual:
        status = f"Departed at {actual}"
        if delay is not None and delay:
            status += f" ({abs(delay)} min {'late' if delay > 0 else 'early'})"
    else:
        status = "Departure not confirmed"
    stops: list[dict[str, object]] = []
    timed_points: list[tuple[dict[str, object], datetime]] = []

    def add_calling_points(field: str, side: str) -> None:
        for group in _calling_points(details.get(field)):
            for point in group:
                # Preserve terminal-arrival semantics when the provider supplies its
                # distinct arrival fields; generic calling-point at-times stay reported.
                stops.append(point)
                point_run_time = _point_run_time(
                    point.get("scheduled"), scheduled, run_time, side, point.get("actual"),
                )
                timed_points.append((point, point_run_time))

    add_calling_points("previousCallingPoints", "previous")
    if _string(details.get("ata")) or _string(details.get("sta")):
        arrival_actual = _string(details.get("ata"))
        arrival_scheduled = _string(details.get("sta"))
        stops.append({
            "station": station or crs, "crs": crs, "scheduled": arrival_scheduled,
            "actual": arrival_actual, "estimated": _string(details.get("eta")),
            "delay_minutes": _delay_minutes(arrival_scheduled, arrival_actual),
            "timing": "arrival", "cancelled": details.get("isCancelled") is True,
        })
        timed_points.append((
            stops[-1], _point_run_time(
                arrival_scheduled, scheduled, run_time, "previous", arrival_actual,
            ),
        ))
    if scheduled or actual or estimated:
        stops.append({
            "station": station or crs, "crs": crs, "scheduled": scheduled, "actual": actual,
            "estimated": estimated, "delay_minutes": delay,
            "timing": "departure", "cancelled": details.get("isCancelled") is True,
        })
        timed_points.append((stops[-1], run_time))
    add_calling_points("subsequentCallingPoints", "subsequent")

    # Compare reports as clock times on the service date, adjusted against each
    # station's scheduled time so a midnight crossing is represented correctly.
    latest: tuple[datetime, dict[str, object]] | None = None
    for point, point_run_time in timed_points:
        point_actual = _string(point.get("actual"))
        if not point_actual or point.get("cancelled"):
            continue
        point_time = _actual_datetime(
            point_actual, point.get("scheduled"), point_run_time,
        )
        # A later route event wins a same-minute tie, notably departure after
        # arrival at the monitored station.
        if point_time is not None and (latest is None or point_time >= latest[0]):
            latest = (point_time, point)
    last_seen = None
    if latest:
        latest_point = latest[1]
        latest_station = _string(latest_point.get("station"))
        latest_actual = _string(latest_point.get("actual"))
        last_seen = {"station": latest_station, "actual": latest_actual}
        if latest_point.get("timing") == "arrival":
            status = f"Arrived at {latest_station} at {latest_actual}"
        elif latest_point.get("timing") == "reported":
            status = f"Reported at {latest_station} at {latest_actual} (timing unspecified)"
    return {
        "scheduled": scheduled, "actual": actual, "destination": destination, "destination_crs": destination_crs,
        "platform": _string(details.get("platform")) or _string(service.get("platform")),
        "operator": _string(details.get("operator")) or _string(service.get("operator")),
        "status": status, "delay_minutes": delay,
        "last_seen": last_seen,
        "stops": stops, "updated_at": updated_at, "stale": False, "details_error": "",
    }


def _recent_display_stops(recent: dict[str, object], crs: str, station: str,
                          filter_station_crs: str, filter_station: str = "") -> list[dict[str, object]]:
    stops = recent["stops"]
    destination = _string(recent.get("destination"))
    destination_crs = _string(recent.get("destination_crs"))
    selected = next((
        stop for stop in stops if stop.get("timing") == "departure" and (
            stop.get("crs") == crs or _string(stop.get("station")).casefold() == station.casefold()
        )
    ), {"station": station or crs, "crs": crs, "scheduled": recent["scheduled"],
        "actual": recent["actual"], "timing": "departure"})
    display = [selected]
    if filter_station_crs:
        display.append(next((
            stop for stop in stops if stop.get("crs") == filter_station_crs
            and stop.get("timing") != "arrival"
        ), next((
            stop for stop in stops if stop.get("crs") == filter_station_crs
        ), {"station": destination if filter_station_crs == destination_crs else filter_station or filter_station_crs,
            "crs": filter_station_crs})))
    if destination:
        display.append(next((
            stop for stop in reversed(stops)
            if (destination_crs and stop.get("crs") == destination_crs)
            or _string(stop.get("station")).casefold() == destination.casefold()
        ), {"station": destination, "crs": destination_crs}))
    result = []
    seen_codes: set[str] = set()
    seen_names: set[str] = set()
    for stop in display:
        code = _string(stop.get("crs"))
        name = _string(stop.get("station")).casefold()
        if (code and code in seen_codes) or name in seen_names:
            continue
        result.append({
            "scheduled": "", "estimated": "", "actual": "", "cancelled": False,
            "timing": "reported", "delay_minutes": None, **stop,
        })
        if code:
            seen_codes.add(code)
        seen_names.add(name)
    return result


class RailDataClient:
    def __init__(self, api_key: str, service_details_api_key: str | None = None):
        self.api_key = validate_api_key(api_key)
        self.service_details_api_key = (
            self.api_key if service_details_api_key is None
            else validate_api_key(service_details_api_key) if service_details_api_key
            else ""
        )
        self._crs = ""
        self._destination_crs = ""
        self._filter_station_crs = ""
        self._recent_id = ""
        self._recent: dict[str, object] | None = None
        self._recent_run_time: datetime | None = None
        self._previous: dict[str, object] | None = None
        self._previous_error = ""

    def _previous_service(
        self, crs: str, station: str, reference: datetime,
    ) -> dict[str, object] | None:
        self._previous_error = ""
        try:
            candidates = []
            # Start close to now so busy stations do not fill the result with
            # much older services. Widen only when no eligible train is found.
            for minutes in (1, 5, 30, 119):
                board = self._load_board(crs, f"&timeOffset=-{minutes}&timeWindow={minutes}")
                candidates = [
                    (run_time, service)
                    for service in _board_trains(board)
                    if service.get("serviceType") != "bus"
                    and service.get("isCancelled") is not True
                    and _string(service.get("etd")).lower() != "cancelled"
                    and (run_time := _service_datetime(service.get("std"), reference)) is not None
                    and reference - timedelta(minutes=minutes) <= run_time < reference
                ]
                if candidates:
                    break
        except RailDataError as error:
            LOG.warning("National Rail previous services are temporarily unavailable: %s", error)
            self._previous_error = str(error)
            if self._previous:
                self._previous = {**self._previous, "stale": True, "details_error": str(error)}
                return self._previous
            return None

        for run_time, service in sorted(candidates, key=lambda candidate: candidate[0], reverse=True):
            service_id = _string(service.get("serviceID"))
            self._previous = _recent_details(
                service, service, crs, station, run_time, int(time.time()),
            )
            departure_time = _actual_datetime(service.get("atd"), service.get("std"), run_time)
            if departure_time is not None:
                self._recent_id = service_id
                self._recent_run_time = departure_time
                self._recent = self._previous
            return self._previous
        if self._previous:
            self._previous = {
                **self._previous, "stale": True,
                "details_error": "No previous service returned within the two-hour look-back",
            }
        return self._previous

    def _board_url(self, crs: str, direction: str = "to") -> str:
        query = {"numRows": "8"}
        filter_crs = (
            self._filter_station_crs if self._filter_station_crs != crs else ""
        ) or self._destination_crs
        if filter_crs:
            query.update(filterCrs=filter_crs, filterType=direction)
        return BOARD_URL + crs + "?" + urllib.parse.urlencode(query)

    def _load_board(self, crs: str, suffix: str = "") -> dict[str, object]:
        board = _request(self._board_url(crs) + suffix, self.api_key)
        if not self._filter_station_crs:
            return board
        boards = [board]
        if self._filter_station_crs != crs:
            boards.append(_request(self._board_url(crs, "from") + suffix, self.api_key))
        trains: list[dict[str, object]] = []
        buses: list[dict[str, object]] = []
        seen_trains: set[str] = set()
        seen_buses: set[str] = set()
        for response in boards:
            # Board responses can omit earlier stops even when the API's filter matched them.
            provider_match = _string(response.get("filtercrs")).upper() == self._filter_station_crs
            bus_services = response.get("busServices", [])
            if not isinstance(bus_services, list):
                raise RailDataError("National Rail returned an invalid bus departure list")
            for services, combined, seen in (
                (_board_trains(response), trains, seen_trains),
                (bus_services, buses, seen_buses),
            ):
                for service in services:
                    if not isinstance(service, dict) or not self._matches_service(service, provider_match):
                        continue
                    identity = _string(service.get("serviceID")) or json.dumps(service, sort_keys=True)
                    if identity not in seen:
                        seen.add(identity)
                        combined.append(service)
        return {**board, "trainServices": trains, "busServices": buses}

    def _matches_service(self, service: dict[str, object], provider_match: bool = False) -> bool:
        if not self._filter_station_crs:
            return True
        destinations = service.get("currentDestinations") or service.get("destination", [])
        destination_codes = {
            _string(location.get("crs")).upper()
            for location in destinations if isinstance(location, dict)
        } if isinstance(destinations, list) else set()
        route_codes = set(destination_codes)
        for field in ("origin", "currentOrigins"):
            locations = service.get(field, [])
            if isinstance(locations, list):
                route_codes.update(_string(location.get("crs")).upper()
                                   for location in locations if isinstance(location, dict))
        for field in ("previousCallingPoints", "subsequentCallingPoints"):
            route_codes.update(point["crs"] for group in _calling_points(service.get(field)) for point in group)
        return (
            provider_match or self._filter_station_crs == self._crs or self._filter_station_crs in route_codes
        ) and (not self._destination_crs or self._destination_crs in destination_codes)

    def fetch(self, crs: str, destination_crs: str = "", filter_station_crs: str = "") -> dict[str, object]:
        crs = validate_crs(crs)
        destination_crs = validate_optional_crs(destination_crs)
        filter_station_crs = validate_optional_crs(filter_station_crs)
        if (self._crs, self._destination_crs, self._filter_station_crs) != (
            crs, destination_crs, filter_station_crs,
        ):
            self._crs = crs
            self._destination_crs = destination_crs
            self._filter_station_crs = filter_station_crs
            self._recent_id = ""
            self._recent = None
            self._recent_run_time = None
            self._previous = None
            self._previous_error = ""
        board = self._load_board(crs)
        services = _board_trains(board)
        bus_services = board.get("busServices", [])
        if not isinstance(bus_services, list):
            raise RailDataError("National Rail returned an invalid bus departure list")
        buses = [
            {**service, "serviceType": "bus"}
            for service in bus_services if isinstance(service, dict)
        ]
        generated = _string(board.get("generatedAt"))
        try:
            reference = datetime.fromisoformat(generated.replace("Z", "+00:00")).astimezone()
        except ValueError:
            reference = datetime.now().astimezone()
        if buses or filter_station_crs:
            services.extend(buses)
            reference_clock = reference.strftime("%H:%M")
            services.sort(key=lambda service: (
                _delay_minutes(reference_clock, service.get("std"))
                if _clock_minutes(service.get("std")) is not None
                and _clock_minutes(reference_clock) is not None else 1440
            ))
            if filter_station_crs:
                services = services[:8]

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
                try:
                    details = _request(
                        DETAILS_URL + urllib.parse.quote(service_id, safe=""),
                        self.service_details_api_key,
                    )
                except RailDataError as error:
                    details_available = False
                    details_error = str(error)
                    LOG.warning("National Rail service details are temporarily unavailable: %s", error)
            elif self.service_details_api_key:
                details_available = False
                details_error = "The next service has no service ID"

        # A just-observed actual on the next service can confirm it before it
        # disappears from the board. Never use etd/eta or schedule as actual.
        next_service = services[0] if services else {}
        next_id = _string(next_service.get("serviceID"))
        next_run_time = _service_datetime(next_service.get("std"), reference)
        if (next_id and next_service.get("serviceType") != "bus" and
                _clock_minutes(details.get("atd")) is not None and
                details.get("isCancelled") is not True and
                _string(details.get("etd")).lower() != "cancelled" and
                next_service.get("isCancelled") is not True and
                _string(next_service.get("etd")).lower() != "cancelled" and
                next_run_time is not None):
            departure_time = _actual_datetime(
                details.get("atd"), details.get("std") or next_service.get("std"), next_run_time,
            )
            if departure_time is not None and (
                self._recent_run_time is None or departure_time > self._recent_run_time or
                next_id == self._recent_id and departure_time == self._recent_run_time
            ):
                self._recent_id = next_id
                self._recent_run_time = departure_time
                self._recent = _recent_details(
                    details, next_service, crs, _string(board.get("locationName")) or crs,
                    next_run_time, int(time.time()),
                )

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
        recent_fields = _recent_details(
            details, next_service if isinstance(next_service, dict) else {}, crs,
            _string(board.get("locationName")) or crs,
            next_run_time or reference, int(time.time()),
        )
        last_seen = recent_fields["last_seen"]
        alerts = _station_messages(board.get("nrccMessages", []))
        recent = self._recent
        if recent is None:
            recent = self._previous_service(
                crs, _string(board.get("locationName")) or crs, reference,
            )
        if recent is not None:
            recent = {**recent, "display_stops": _recent_display_stops(
                recent, crs, _string(board.get("locationName")) or crs, filter_station_crs,
                _string(board.get("filterLocationName"))
                if _string(board.get("filtercrs")).upper() == filter_station_crs else "",
            )}
        return {
            "crs": crs,
            "destination_crs": destination_crs,
            "filter_station_crs": filter_station_crs,
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
            "recent": recent,
            "recent_error": (
                _string(recent.get("details_error")) if recent and recent.get("stale")
                else "" if recent else
                self._previous_error or
                "No previous service available from the API within the two-hour look-back"
            ),
            "stale": False,
            "error": "",
        }


class RailDataWorker:
    def __init__(
        self,
        config: Callable[[], dict[str, object]],
        path: Path,
        interval: int = REFRESH_INTERVAL,
        fetch: Callable[[str, str, str, str, str], dict[str, object]] | None = None,
    ):
        self.config = config
        self.path = path
        self.interval = interval
        self.fetch = fetch
        self._clients: dict[tuple[str, str, str, str, str], RailDataClient] = {}
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
        expected_boards: dict[str, tuple[str, str, str]] = {}
        for scene in train_scenes:
            try:
                expected_boards[str(scene["id"])] = (
                    validate_crs(scene.get("station_crs")),
                    validate_optional_crs(scene.get("destination_crs", "")),
                    validate_optional_crs(scene.get("filter_station_crs", "")),
                )
            except ValueError:
                continue
        filtered_boards = {
            scene_id: board for scene_id, board in boards.items()
            if scene_id in expected_boards and board_matches(board, *expected_boards[scene_id])
        }
        boards_changed = filtered_boards != boards
        boards = filtered_boards
        if boards_changed:
            self._publish(boards)
        if not key:
            for scene in train_scenes:
                boards[str(scene["id"])] = {
                    "crs": scene.get("station_crs", ""),
                    "destination_crs": scene.get("destination_crs", ""),
                    "filter_station_crs": scene.get("filter_station_crs", ""),
                    "error": "Configure a National Rail API key in device settings",
                    "stale": False,
                }
            self._publish(boards)
            return
        details_key = str(config.get("service_details_api_key", ""))
        station_groups: dict[tuple[str, str, str], list[str]] = {}
        for scene in train_scenes:
            board_key = expected_boards.get(str(scene["id"]))
            if board_key is None:
                continue
            station_groups.setdefault(board_key, []).append(str(scene["id"]))
        if not station_groups:
            return
        if self.fetch is None:
            active_boards = set(station_groups)
            self._clients = {
                client_key: client for client_key, client in self._clients.items()
                if client_key[:2] == (key, details_key) and client_key[2:] in active_boards
            }
        with self._lock:
            grouped = list(station_groups.items())
            now = time.monotonic()
            interval = config.get("raildata_refresh_seconds", self.interval)
            if self._refresh_failed:
                interval = min(interval, RETRY_INTERVAL)
            if self._last_refresh_at is not None and now - self._last_refresh_at < interval:
                return
            cursor = self._cursor % len(grouped)
            (crs, destination_crs, filter_station_crs), scene_ids = grouped[cursor]
            self._cursor = cursor + 1
            self._last_refresh_at = now
            try:
                if self.fetch is not None:
                    result = self.fetch(key, details_key, crs, destination_crs, filter_station_crs)
                else:
                    client_key = (key, details_key, crs, destination_crs, filter_station_crs)
                    client = self._clients.setdefault(
                        client_key, RailDataClient(key, details_key),
                    )
                    result = client.fetch(crs, destination_crs, filter_station_crs)
            except RailDataError as error:
                self._refresh_failed = True
                LOG.warning("National Rail refresh failed for %s: %s", crs, error)
                for scene_id in scene_ids:
                    previous = boards.get(scene_id, {})
                    if board_matches(previous, crs, destination_crs, filter_station_crs) and (
                        previous.get("rows") or previous.get("recent")
                    ):
                        previous = {**previous, "stale": True, "error": str(error)}
                    else:
                        previous = {
                            "crs": crs, "destination_crs": destination_crs,
                            "filter_station_crs": filter_station_crs,
                            "stale": False, "error": str(error),
                        }
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
