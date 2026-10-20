from __future__ import annotations

import logging
import re
from typing import Literal, Protocol, TypedDict
from urllib.parse import urlparse

LOG = logging.getLogger(__name__)
MAX_SCENES = 30
STARTUP_TIMEOUT = 30
RETRY_COOLDOWN = 60
DEFAULT_PLEX_BITRATE_KBPS = 4000
MIN_PLEX_BITRATE_KBPS = 500
MAX_PLEX_BITRATE_KBPS = 20000


class Scene(TypedDict):
    id: str
    kind: str
    title: str
    enabled: bool
    duration: int | None
    audio_enabled: bool
    plex_bitrate_kbps: int | None
    url: str
    source: str
    plex_rating_key: str
    plex_playback_url: str
    plex_stop_url: str
    layout: str
    text: str


class Playlist(TypedDict):
    enabled: bool
    scenes: list[Scene]


def validate_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("URL must be a string")
    value = value.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use http:// or https:// and include a host")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed")
    if len(value) > 2048 or any(ord(char) < 32 for char in value):
        raise ValueError("URL is too long or contains control characters")
    try:
        parsed.port
    except ValueError:
        raise ValueError("Invalid URL port") from None
    return value


def validate_plex_bitrate(value: object) -> int:
    if type(value) is not int or not MIN_PLEX_BITRATE_KBPS <= value <= MAX_PLEX_BITRATE_KBPS:
        raise ValueError(
            f"Plex bitrate must be {MIN_PLEX_BITRATE_KBPS}-{MAX_PLEX_BITRATE_KBPS} kbps"
        )
    return value


def validate_playlist(value: object, *, allow_runtime: bool = False) -> Playlist:
    if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
        raise ValueError("Playlist enabled must be true or false")
    items = value.get("scenes")
    if not isinstance(items, list) or len(items) > MAX_SCENES:
        raise ValueError(f"Playlist must contain at most {MAX_SCENES} scenes")
    scenes: list[Scene] = []
    ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each scene must be an object")
        scene_id = item.get("id")
        if not isinstance(scene_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", scene_id):
            raise ValueError("Each scene needs a valid ID")
        if scene_id in ids:
            raise ValueError("Scene IDs must be unique")
        ids.add(scene_id)
        kind = item.get("kind")
        if kind not in ("web", "dashboard", "video"):
            raise ValueError("Supported scene types are web, dashboard and video")
        title = item.get("title", "")
        if not isinstance(title, str) or len(title) > 80:
            raise ValueError("Scene titles must be at most 80 characters")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("Scene enabled must be true or false")
        duration = item.get("duration", 300 if kind == "video" else 60)
        if not (kind == "video" and duration is None):
            if type(duration) is not int or not 5 <= duration <= 86400:
                raise ValueError("Scene duration must be 5-86400 seconds (or null for video)")
        audio_enabled = item.get("audio_enabled", False) if kind == "video" else False
        if not isinstance(audio_enabled, bool):
            raise ValueError("Video audio must be true or false")
        source = item.get("source", "url") if kind == "video" else "url"
        if source not in ("url", "plex"):
            raise ValueError("Video source must be a URL or Plex")
        plex_bitrate = item.get("plex_bitrate_kbps") if source == "plex" else None
        if plex_bitrate is not None:
            plex_bitrate = validate_plex_bitrate(plex_bitrate)
        url = validate_url(item.get("url", "")) if kind != "dashboard" else ""
        plex_rating_key = item.get("plex_rating_key", "") if source == "plex" else ""
        if source == "plex":
            if kind != "video" or not isinstance(plex_rating_key, str) or not re.fullmatch(r"[0-9]{1,20}", plex_rating_key):
                raise ValueError("Plex video scenes require a valid media selection")
            if url:
                raise ValueError("Plex video scenes must not contain a URL")
        elif kind != "dashboard" and not url:
            raise ValueError("Web and video scenes require a URL")
        layout = item.get("layout", "clock-notice") if kind == "dashboard" else "clock-notice"
        if layout not in ("clock", "notice", "clock-notice"):
            raise ValueError("Unknown dashboard layout")
        text = item.get("text", "") if kind == "dashboard" else ""
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError("Notice text must be at most 2000 characters")
        scene = Scene(
            id=scene_id, kind=kind, title=title.strip(), enabled=enabled,
            duration=duration, audio_enabled=audio_enabled, url=url, source=source,
            plex_rating_key=plex_rating_key, plex_bitrate_kbps=plex_bitrate,
            layout=layout, text=text,
        )
        if allow_runtime and source == "plex":
            playback_url = validate_url(item.get("plex_playback_url", ""))
            stop_url = validate_url(item.get("plex_stop_url", ""))
            playback = urlparse(playback_url)
            stop = urlparse(stop_url)
            play_match = re.fullmatch(r"/play/([A-Za-z0-9_-]+)/start\.m3u8", playback.path)
            stop_match = re.fullmatch(r"/stop/([A-Za-z0-9_-]+)", stop.path)
            if (
                playback.scheme != "http" or stop.scheme != "http"
                or playback.hostname != "127.0.0.1" or stop.hostname != "127.0.0.1"
                or playback.netloc != stop.netloc
                or playback.query or playback.fragment or stop.query or stop.fragment
                or play_match is None or stop_match is None
                or play_match.group(1) != stop_match.group(1)
            ):
                raise ValueError("Plex runtime playback URLs must be local")
            scene["plex_playback_url"] = playback_url
            scene["plex_stop_url"] = stop_url
        scenes.append(scene)
    if value["enabled"] and not any(scene["enabled"] for scene in scenes):
        raise ValueError("Enable at least one scene before enabling the carousel")
    return Playlist(enabled=value["enabled"], scenes=scenes)


PlaybackState = Literal["starting", "playing", "finished", "failed"]


class Renderer(Protocol):
    def start(self, scene: Scene) -> None: ...
    def state(self) -> PlaybackState: ...
    def stop(self) -> None: ...
    def recovery(self) -> None: ...
    def failure(self, reason: str | None = None) -> None: ...


class Carousel:
    """Monotonic-time scheduler; renderer resources belong to exactly one scene."""

    def __init__(self, playlist: Playlist, renderer: Renderer):
        self.scenes = [scene for scene in playlist["scenes"] if scene["enabled"]]
        self.renderer = renderer
        self.current: Scene | None = None
        self.cursor = -1
        self.attempted_at = 0.0
        self.started_at: float | None = None
        self.cooldowns: dict[str, float] = {}

    def stop(self) -> None:
        self.renderer.stop()
        self.current = None

    def tick(self, now: float) -> None:
        if self.current is not None:
            state = self.renderer.state()
            timed_out = self.started_at is None and now - self.attempted_at >= STARTUP_TIMEOUT
            if state == "playing" and self.started_at is None:
                self.started_at = now
            failed = state == "failed" or timed_out
            duration = self.current["duration"]
            expired = (
                self.started_at is not None and duration is not None
                and now - self.started_at >= duration
            )
            if not failed and state != "finished" and not expired:
                return
            if failed:
                self.renderer.failure("Scene did not start within 30 seconds." if timed_out else None)
                LOG.warning("Scene %s failed; retrying after cooldown", self.current["id"])
                self.cooldowns[self.current["id"]] = now + RETRY_COOLDOWN
            self.stop()
        for offset in range(1, len(self.scenes) + 1):
            index = (self.cursor + offset) % len(self.scenes)
            scene = self.scenes[index]
            if self.cooldowns.get(scene["id"], 0) > now:
                continue
            self.cursor = index
            self.current = scene
            self.attempted_at = now
            self.started_at = None
            self.renderer.start(scene)
            return
        self.renderer.recovery()
