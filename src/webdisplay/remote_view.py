"""Credential-free reconstruction of the kiosk's active display, never a screenshot."""

from __future__ import annotations

import ipaddress
import json
import math
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .raildata import board_matches
from .storage import atomic_write

RUNTIME_PATH = Path("/run/web-display-kiosk/remote-view.json")
MAX_BYTES = 1024 * 1024
MAX_AGE = 15
KINDS = {"web", "dashboard", "train", "weather", "video", "music"}


def read_json(path: Path) -> object:
    with path.open("rb") as stream:
        body = stream.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("Remote view data exceeds the size limit")
    try:
        return json.loads(body)
    except RecursionError:
        raise ValueError("Remote view data is nested too deeply") from None


def public_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if url.scheme not in ("http", "https") or not host or host.lower() == "localhost":
            return ""
        try:
            if ipaddress.ip_address(host).is_loopback:
                return ""
        except ValueError:
            pass
        authority = f"[{host}]" if ":" in host else host
        if url.port is not None:
            authority += f":{url.port}"
        return urlunsplit((url.scheme, authority, url.path, "", ""))
    except ValueError:
        return ""


def _finite_number(value: object) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _fields(value: object, strings=(), numbers=(), booleans=()) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Invalid remote view data")
    result = {key: value[key][:8192] for key in strings if isinstance(value.get(key), str)}
    result.update({key: value[key] for key in numbers if _finite_number(value.get(key))})
    result.update({key: value[key] for key in booleans if type(value.get(key)) is bool})
    return result


def _texts(value: object) -> list[str]:
    return [item[:8192] for item in value[:100] if isinstance(item, str)] if isinstance(value, list) else []


def sanitize_scene(value: object) -> dict | None:
    if value is None:
        return None
    result = _fields(value, ("id", "kind", "title", "layout", "text", "station_crs", "destination_crs", "filter_station_crs",
                             "source", "plex_rating_key"), ("duration",),
                     ("show_weather", "audio_enabled", "show_previous_departure"))
    if result.get("kind") not in KINDS or not result.get("id"):
        raise ValueError("Invalid active scene")
    if value.get("duration") is None:
        result["duration"] = None
    if result["kind"] in ("web", "video"):
        result["url"] = "" if result.get("source") == "plex" else public_url(value.get("url"))
    return result


def sanitize_weather(value: object) -> dict:
    result = _fields(value, ("location", "description", "icon", "error", "forecast_error"),
                     ("temperature", "feels_like", "humidity", "wind_kmh", "observed_at", "updated_at"),
                     ("stale",))
    if isinstance(value.get("forecast"), list):
        result["forecast"] = [_fields(day, ("date", "description", "icon"), ("low", "high"))
                              for day in value["forecast"][:10]]
    return result


def sanitize_music(value: object) -> dict:
    """Now-playing text only; the relay's album art capability is never published."""
    result = {key: text[:200] for key, text in _fields(value, ("state", "title", "artist", "album", "next_title", "next_artist")).items()}
    result.setdefault("state", "disabled")
    return result


def sanitize_train(value: object) -> dict:
    result = _fields(value, ("crs", "destination_crs", "filter_station_crs", "station", "generated_at", "error", "details_error", "recent_error"),
                     ("updated_at",), ("services_available", "platform_available", "details_available", "stale"))
    for key in ("alerts",):
        if key in value:
            result[key] = _texts(value[key])
    if isinstance(value.get("rows"), list):
        result["rows"] = []
        for row in value["rows"][:100]:
            clean = _fields(row, ("scheduled", "estimated", "destination", "platform", "operator",
                                  "status", "state", "service_type"), (), ("cancelled",))
            if "alerts" in row:
                clean["alerts"] = _texts(row["alerts"])
            result["rows"].append(clean)
    if isinstance(value.get("next"), dict):
        following = value["next"]
        result["next"] = _fields(following, ("scheduled", "estimated", "actual", "status"), ("delay_minutes",))
        if following.get("delay_minutes") is None:
            result["next"]["delay_minutes"] = None
        for key in ("calling_at", "previous_stops"):
            if isinstance(following.get(key), list):
                result["next"][key] = [
                    [_fields(point, ("station", "scheduled", "estimated", "actual"), (), ("cancelled",))
                     for point in group[:100]] for group in following[key][:10] if isinstance(group, list)
                ]
        if following.get("last_seen") is None:
            result["next"]["last_seen"] = None
        else:
            result["next"]["last_seen"] = _fields(
                following["last_seen"], ("station", "scheduled", "estimated", "actual"), (), ("cancelled",),
            )
    elif "next" in value and value["next"] is None:
        result["next"] = None
    if isinstance(value.get("recent"), dict):
        recent = value["recent"]
        clean = _fields(
            recent, ("scheduled", "actual", "destination", "destination_crs", "platform", "operator", "status", "details_error"),
            ("delay_minutes", "updated_at"), ("stale",),
        )
        if recent.get("delay_minutes") is None:
            clean["delay_minutes"] = None
        clean["last_seen"] = (
            _fields(recent["last_seen"], ("station", "actual"))
            if isinstance(recent.get("last_seen"), dict) else None
        )
        for key in ("stops", "display_stops"):
            if not isinstance(recent.get(key), list):
                continue
            clean[key] = [
                _fields(
                    point, ("station", "crs", "scheduled", "actual", "estimated", "timing"),
                    ("delay_minutes",), ("cancelled",),
                )
                for point in recent[key][:100]
            ]
            for point, original in zip(clean[key], recent[key][:100]):
                if original.get("delay_minutes") is None:
                    point["delay_minutes"] = None
        result["recent"] = clean
    elif "recent" in value and value["recent"] is None:
        result["recent"] = None
    return result


def process_identity(pid: int) -> str:
    # PID alone can be reused after a kiosk restart. Linux start ticks bind this heartbeat
    # to the still-running process without requiring access to its environment or config.
    stat = Path(f"/proc/{pid}/stat").read_text()
    fields = stat[stat.rindex(")") + 2:].split()
    if fields[0] == "Z":
        raise ValueError("Kiosk has stopped")
    return fields[19]


def snapshot(mode: str, scene: object, sleep: dict, message: str = "", *,
             train_path: Path = Path("/run/web-display/train-board.json"),
             weather_path: Path = Path("/run/web-display/weather.json"),
             music: dict | None = None) -> dict:
    active = sanitize_scene(scene)
    now = datetime.now().astimezone()
    result = {
        "available": True, "mode": mode, "updated": time.time(),
        "device_time": now.isoformat(timespec="seconds"), "timezone": now.tzname() or "",
        "scene": active, "train": None, "weather": None, "music": None, "message": message,
        "sleep": {key: sleep.get(key) for key in ("sleeping", "scheduled_sleep", "wake_until")},
    }
    if active and active["kind"] == "train":
        try:
            boards = read_json(train_path)
            board = boards.get(active["id"]) if isinstance(boards, dict) else None
            if board_matches(board, active.get("station_crs"), active.get("destination_crs", ""),
                             active.get("filter_station_crs", "")):
                result["train"] = sanitize_train(board)
        except (OSError, ValueError):
            pass
    if active and active["kind"] == "music":
        result["music"] = sanitize_music(music or {})
    if active and (active["kind"] in ("weather", "train") or active.get("show_weather")):
        try:
            result["weather"] = sanitize_weather(read_json(weather_path))
        except (OSError, ValueError):
            pass
    return result


def publish(path: Path, value: dict) -> None:
    pid = os.getpid()
    payload = {**value, "kiosk_pid": pid, "kiosk_start": process_identity(pid)}
    body = json.dumps(payload, allow_nan=False) + "\n"
    if len(body.encode()) > MAX_BYTES:
        raise ValueError("Remote view data exceeds the size limit")
    atomic_write(path, body, mode=0o640)


def load(path: Path | None = None) -> dict:
    value = read_json(path or RUNTIME_PATH)
    if not isinstance(value, dict):
        raise ValueError("Invalid remote view runtime")
    if not {"scene", "train", "weather", "sleep", "message", "timezone", "device_time"} <= value.keys():
        raise ValueError("Incomplete remote view runtime")
    if not isinstance(value["timezone"], str) or not isinstance(value["message"], str):
        raise ValueError("Invalid remote view text")
    updated = value.get("updated")
    if not _finite_number(updated) or not -2 <= time.time() - updated <= MAX_AGE:
        raise ValueError("Kiosk remote view heartbeat is stale")
    pid = value.get("kiosk_pid")
    if type(pid) is not int or pid <= 0 or value.get("kiosk_start") != process_identity(pid):
        raise ValueError("Kiosk remote view process is no longer current")
    mode = value.get("mode")
    if value.get("available") is not True or mode not in ("carousel", "single", "recovery"):
        raise ValueError("Invalid remote view mode")
    active = sanitize_scene(value.get("scene"))
    if (mode == "recovery") != (active is None) or (mode == "single" and active["kind"] != "web"):
        raise ValueError("Invalid remote view scene")
    stamp = value.get("device_time")
    if not isinstance(stamp, str) or datetime.fromisoformat(stamp).utcoffset() is None:
        raise ValueError("Invalid remote view clock")
    sleep = _fields(value.get("sleep"), (), ("wake_until",), ("sleeping", "scheduled_sleep"))
    if set(sleep) - {"wake_until"} != {"sleeping", "scheduled_sleep"}:
        raise ValueError("Invalid remote view sleep state")
    sleep.setdefault("wake_until", None)
    if value["sleep"].get("wake_until") is not None and (
        "wake_until" not in sleep or sleep["wake_until"] is None or sleep["wake_until"] < 0
    ):
        raise ValueError("Invalid remote view wake deadline")
    result = _fields(value, ("device_time", "timezone", "message"))
    result.update(available=True, mode=mode, updated=updated, scene=active, sleep=sleep, train=None, weather=None,
                  music=None)
    if active and active["kind"] == "music" and isinstance(value.get("music"), dict):
        result["music"] = sanitize_music(value["music"])
    if active and active["kind"] == "train" and value.get("train") is not None:
        board = sanitize_train(value["train"])
        if board_matches(board, active.get("station_crs"), active.get("destination_crs", ""),
                         active.get("filter_station_crs", "")):
            result["train"] = board
    if active and (active["kind"] in ("weather", "train") or active.get("show_weather")) and value.get("weather") is not None:
        result["weather"] = sanitize_weather(value["weather"])
    result["heartbeat_age_seconds"] = max(0, time.time() - updated)
    return result
