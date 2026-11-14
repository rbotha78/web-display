from __future__ import annotations

import http.client
import json
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .plex import MIX_KEY, PLAYLIST_KEY, PlexClient, RATING_KEY, valid_music_source
from .sleep import DAYS, SleepRuntime
from . import music_sync
from .music_sync import ClockOffset, LeaderClient, LeaderError

PART_KEY = re.compile(r"/library/parts/[0-9]+/[0-9]+/file\.[A-Za-z0-9]+\Z")
ART_KEY = re.compile(r"/library/metadata/[0-9]+/(?:thumb|art)/[0-9]+\Z")
ART_SIZE = 600
MAX_ART_BYTES = 2 * 1024 * 1024
ART_TYPES = {"image/jpeg", "image/png", "image/webp"}



def track_artist(track) -> str:
    """Prefer a track's own artist (compilations) over its album artist."""
    return track.get("originalTitle") or track.get("grandparentTitle", "")


def track_art(track) -> str:
    """Album cover first, then the track's own or its artist's image."""
    return next((value for name in ("parentThumb", "thumb", "grandparentThumb")
                 if ART_KEY.fullmatch(value := track.get(name, ""))), "")

class EmptyMusicSourceError(ValueError):
    pass


def validate_music(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Music settings must be an object")
    enabled, station, volume = value.get("enabled", False), value.get("station_key", ""), value.get("volume", 30)
    if not isinstance(enabled, bool):
        raise ValueError("Music enabled must be true or false")
    if not isinstance(station, str) or (station and not valid_music_source(station)):
        raise ValueError("Select a Plex station, audio playlist, or artist/album mix")
    if enabled and not station:
        raise ValueError("Select a music source before enabling playback")
    if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
        raise ValueError("Music volume must be an integer from 0 to 100")
    days = value.get("day_sources", {})
    if not isinstance(days, dict) or not set(days) <= set(DAYS):
        raise ValueError("Day music sources must be keyed by weekday")
    day_sources = {}
    for day in DAYS:
        source = days.get(day, "")
        if not isinstance(source, str) or (source and not valid_music_source(source)):
            raise ValueError(f"Select a valid music source for {day.capitalize()}")
        day_sources[day] = source
    return {"enabled": enabled, "station_key": station, "volume": volume, "day_sources": day_sources}


def active_source(settings: dict[str, object], now: datetime | None = None) -> tuple[str, str]:
    """Return today's (device-local) weekday and its source, falling back to the default."""
    now = datetime.fromtimestamp(time.time()).astimezone() if now is None else now
    day = DAYS[now.weekday()]
    return day, settings.get("day_sources", {}).get(day) or settings["station_key"]


class MusicRelay:
    """Private queue owner and range-capable audio relay; kiosk gets only a capability."""

    def __init__(self, client_factory: Callable[[], PlexClient], sleep_path: Path | None = None):
        self.client_factory = client_factory
        self.lock = threading.RLock()
        self.capability = secrets.token_urlsafe(24)
        self.settings = validate_music({})
        self.identity = None
        self.client = None
        self.queue = None
        self.track = None
        self.stream_key = ""
        self.queue_source = ""
        self.clock = lambda: datetime.fromtimestamp(time.time()).astimezone()
        self.user_paused = False
        self.skip_request = None
        self.status = {"state": "disabled", "title": "", "artist": "", "error": ""}
        self.sleep_runtime = SleepRuntime(sleep_path) if sleep_path is not None else None
        self.shutdown = threading.Event()
        self.streams_lock = threading.Lock()
        self.streams = set()
        self.sync: dict[str, object] = {"role": "off"}
        self.sync_name = music_sync.local_name()
        self.machine = ""
        self.listeners: dict[str, tuple[bool, float]] = {}
        self.group = self._new_group()
        self.remote: dict[str, object] | None = None
        self.remote_at = 0.0
        self.offset = ClockOffset()
        self.leader_client: LeaderClient | None = None
        self.link_wake = threading.Event()
        self.link_thread: threading.Thread | None = None
        self.sync_error = ""
        self.sync_retry_at = 0.0
        self.local_listening = (False, 0.0)
        self.link_generation = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def authorized(self):
                return (self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
                        and self.path.startswith(f"/music/{owner.capability}/"))

            def reply(self, status, data):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if not self.authorized() or self.path != f"/music/{owner.capability}/control":
                    self.reply(404, {})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 2048:
                        raise ValueError("Invalid control request")
                    value = json.loads(self.rfile.read(length))
                    self.reply(200, owner.control(value))
                except (ValueError, OSError, http.client.HTTPException) as error:
                    message = str(error) if isinstance(error, EmptyMusicSourceError) else (
                        "Music playback unavailable; check Plex connection."
                    )
                    with owner.lock:
                        owner.status["error"] = message
                        owner.status["state"] = "error"
                    self.reply(502, {"error": message})

            def do_GET(self):
                if not self.authorized():
                    self.reply(404, {})
                    return
                if self.path.startswith(f"/music/{owner.capability}/art/"):
                    self.send_art()
                    return
                with owner.lock:
                    client, track = owner.client, owner.track
                    valid = (not owner.sleeping() and owner.stream_enabled() and track is not None and
                             self.path == f"/music/{owner.capability}/stream/{owner.stream_key}")
                    part = track.find("./Media/Part") if valid else None
                    key = part.get("key", "") if part is not None else ""
                if not valid or client is None or not PART_KEY.fullmatch(key):
                    self.reply(404, {})
                    return
                try:
                    headers = {}
                    byte_range = self.headers.get("Range")
                    if byte_range:
                        if not re.fullmatch(r"bytes=[0-9]*-[0-9]*", byte_range):
                            self.reply(416, {})
                            return
                        headers["Range"] = byte_range
                    with client.open(key, headers=headers, timeout=20) as response:
                        with owner.lock:
                            if owner.sleeping() or owner.track is not track:
                                self.reply(404, {})
                                return
                            with owner.streams_lock:
                                owner.streams.add(response)
                        try:
                            self.send_response(response.status)
                            for name in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                                if response.headers.get(name):
                                    self.send_header(name, response.headers[name])
                            self.send_header("Cache-Control", "no-store")
                            self.end_headers()
                            while not owner.sleeping() and (chunk := response.read(64 * 1024)):
                                self.wfile.write(chunk)
                        finally:
                            with owner.streams_lock:
                                owner.streams.discard(response)
                except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
                    self.close_connection = True

            def send_art(self):
                with owner.lock:
                    client, track = owner.client, owner.track
                    thumb = track_art(track) if track is not None and owner.stream_key and (
                        self.path == f"/music/{owner.capability}/art/{owner.stream_key}") else ""
                if client is None or not thumb:
                    self.reply(404, {})
                    return
                try:
                    with client.open("/photo/:/transcode", {
                        "url": thumb, "width": ART_SIZE, "height": ART_SIZE, "minSize": 1, "upscale": 1,
                    }, timeout=15) as response:
                        content_type = response.headers.get_content_type()
                        body = response.read(MAX_ART_BYTES + 1)
                    if content_type not in ART_TYPES or not body or len(body) > MAX_ART_BYTES:
                        raise ValueError("Unsupported album art")
                except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
                    self.reply(404, {})
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.sleep_thread = threading.Thread(target=self._enforce_sleep, daemon=True)
        self.sleep_thread.start()

    def sleeping(self):
        # In a sync group, sleep silences only the local player; the group keeps time.
        return (self.sync["role"] == "off" and self.sleep_runtime is not None
                and self.sleep_runtime.sleeping())

    def stream_enabled(self):
        return self.sync["role"] == "follower" or bool(self.settings["enabled"])

    def _enforce_sleep(self):
        was_sleeping = False
        while not self.shutdown.wait(0.5):
            if self.sync["role"] == "leader":
                self._leader_tick()
            sleeping = self.sleeping()
            if sleeping:
                # Close relay sockets even if a Plex queue/timeline request holds the queue lock.
                self._close_streams()
                if not was_sleeping or self.queue is not None:
                    self.control({"action": "stop"})
            was_sleeping = sleeping

    def _close_streams(self):
        with self.streams_lock:
            streams = list(self.streams)
            self.streams.clear()
        for response in streams:
            # Interrupt a blocked upstream read before closing its buffered response.
            try:
                raw = getattr(getattr(response, "fp", None), "raw", None)
                upstream = getattr(raw, "_sock", None)
                if upstream is not None:
                    upstream.shutdown(socket.SHUT_RDWR)
                response.close()
            except OSError:
                pass

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/music/{self.capability}"

    def configure(self, settings, identity, sync=None):
        with self.lock:
            sync = self.sync if sync is None else sync
            role_changed = sync != self.sync
            if role_changed:
                self._set_sync(sync)
            now = self.clock()
            follower = self.sync["role"] == "follower"
            if role_changed or self.identity != identity or (not follower and (
                    active_source(self.settings, now) != active_source(settings, now)
                    or self.settings["enabled"] != settings["enabled"])):
                self._stop()
                self.capability = secrets.token_urlsafe(24)
                self.queue = self.track = self.client = None
                self.stream_key = ""
                self.user_paused = False
                self.skip_request = None
                self.status = {"state": "waiting" if settings["enabled"] else "disabled",
                               "title": "", "artist": "", "error": ""}
            self.settings, self.identity = dict(settings), identity
            self.group = self._new_group(self.group)
            if self.sync["role"] != "off":
                return {"enabled": True, "sync": True, "volume": settings["volume"],
                        "control_url": self.url + "/control"}
            return {**settings, "control_url": self.url + "/control",
                    "paused": self.user_paused, "skip": self.skip_request}

    def request_playback(self, action: str) -> None:
        with self.lock:
            if not self.settings["enabled"]:
                raise ValueError("Enable background music before controlling playback")
            if self.sync["role"] == "leader":
                self._group_command(action)
                return
            if action == "pause":
                self.user_paused = True
            elif action == "resume":
                if self.sleeping():
                    raise ValueError("Music is sleeping; wake the display before resuming")
                self.user_paused = False
            elif action == "skip":
                if self.sleeping() or self.track is None:
                    raise ValueError("No current song is available to skip")
                self.skip_request = {
                    "request_id": secrets.token_urlsafe(12),
                    "track_id": self.track.get("playQueueItemID"),
                }
            else:
                raise ValueError("Invalid music playback action")

    def like_track(self, rating_key: object) -> None:
        if not isinstance(rating_key, str) or not RATING_KEY.fullmatch(rating_key):
            raise ValueError("Invalid Plex track")
        with self.lock:
            if (not self.settings["enabled"] or self.sleeping()
                    or self.track is None or self.client is None):
                raise ValueError("No current song is available to like")
            if self.track.get("ratingKey") != rating_key:
                raise ValueError("The song has changed; like the current song instead")
            self.client.like_track(rating_key)
            self.track.set("userRating", "10")

    def _timeline(self, state, position):
        if self.client is not None and self.queue is not None and self.track is not None:
            self.client.xml("/:/timeline", {
                "ratingKey": self.track.get("ratingKey"), "key": self.track.get("key"),
                "state": state, "time": position, "duration": self.track.get("duration", "0"),
                "playQueueID": self.queue.get("playQueueID"),
                "playQueueItemID": self.track.get("playQueueItemID"),
                "playQueueVersion": self.queue.get("playQueueVersion"),
            })

    def _track_details(self):
        """Album name and a capability URL for the current track's cover, when available."""
        details = {"album": self.track.get("parentTitle", ""), **self._upcoming()}
        if track_art(self.track):
            details["art_url"] = self.url + "/art/" + self.stream_key
        return details

    def _upcoming(self):
        """The queued track after the current one, if the loaded queue window already knows it."""
        if self.queue is None or self.track is None or self.sync["role"] == "follower":
            return {}
        if self.queue_source != active_source(self.settings, self.clock())[1]:
            return {}
        tracks = list(self.queue.iter("Track"))
        track_id = self.track.get("playQueueItemID")
        index = next((i for i, t in enumerate(tracks) if t.get("playQueueItemID") == track_id), -1)
        if index < 0 or index + 1 >= len(tracks):
            return {}
        upcoming = tracks[index + 1]
        return {"next_title": upcoming.get("title", "Music"), "next_artist": track_artist(upcoming)}

    def _stop(self):
        self._close_streams()
        try:
            self._timeline("stopped", 0)
        except (ValueError, OSError, http.client.HTTPException):
            pass
        finally:
            self.queue = self.track = self.client = None
            self.stream_key = ""
            self.status.update(title="", artist="")

    def _select(self, track):
        part = track.find("./Media/Part")
        if (not RATING_KEY.fullmatch(track.get("ratingKey", "")) or
                not track.get("playQueueItemID", "").isdigit() or
                part is None or not PART_KEY.fullmatch(part.get("key", ""))):
            raise ValueError("Unsupported music track")
        self.track = track
        self.stream_key = secrets.token_urlsafe(24)
        self.status.update(title=track.get("title", "Music"), artist=track_artist(track), error="")

    def _create_queue(self):
        self.client = self.client_factory()
        machine = self.client.identity()["machine_identifier"]
        self.machine = machine
        key = active_source(self.settings, self.clock())[1]
        query: dict[str, object] = {
            "type": "audio",
            "uri": f"server://{machine}/com.plexapp.plugins.library{key}",
        }
        mix = MIX_KEY.fullmatch(key)
        playlist = PLAYLIST_KEY.fullmatch(key)
        if mix:
            self.client.music_mix_items(key)
            path = urllib.parse.quote_plus("/library/metadata/" + mix.group(2))
            query.update(uri="library:///directory/" + path, shuffle=1, includeRelated=1)
        elif playlist:
            query["playlistID"] = playlist.group(1)
        queue = self.client.xml("/playQueues", query, method="POST")
        tracks = list(queue.iter("Track"))
        if not tracks:
            raise EmptyMusicSourceError("The selected music source contains no playable tracks.")
        if not queue.get("playQueueID", "").isdigit():
            raise ValueError("Invalid music queue")
        selected = queue.get("playQueueSelectedItemID")
        track = next((t for t in tracks if t.get("playQueueItemID") == selected), None)
        if track is None:
            raise ValueError("Empty music queue")
        self._select(track)
        self.queue = queue
        self.queue_source = key

    def _advance(self):
        track_id = self.track.get("playQueueItemID")
        self._timeline("stopped", self.track.get("duration", "0"))
        queue = self.client.xml("/playQueues/" + self.queue.get("playQueueID"),
                                {"center": track_id, "window": 5})
        tracks = list(queue.iter("Track"))
        index = next((i for i, t in enumerate(tracks) if t.get("playQueueItemID") == track_id), -1)
        if index < 0:
            raise ValueError("Music queue lost the current track")
        if (index + 1 >= len(tracks)
                or self.queue_source != active_source(self.settings, self.clock())[1]):
            # Rebuild at the queue end (refreshing smart playlists/mixes) or when
            # today's weekday source changed since this queue was created.
            self._create_queue()
        else:
            self._select(tracks[index + 1])
            self.queue = queue

    def control(self, value):
        if isinstance(value, dict) and self.sync["role"] != "off":
            if value.get("action") != "sync" or set(value) != {"action", "listening"} or not isinstance(
                    value["listening"], bool):
                raise ValueError("Invalid music control")
            return self._sync_control(value["listening"])
        if not isinstance(value, dict) or value.get("action") not in {"current", "next", "timeline", "stop", "error"}:
            raise ValueError("Invalid music control")
        with self.lock:
            if value["action"] == "stop":
                self._stop()
                self.status["state"] = "disabled" if not self.settings["enabled"] else "waiting"
                return {}
            if self.sleeping():
                self._stop()
                raise ValueError("Music sleeping")
            if not self.settings["enabled"]:
                raise ValueError("Music disabled")
            if value["action"] == "error":
                self.status.update(state="error", error="Audio playback interrupted; retrying automatically.")
                self._stop()
                return {}
            if self.queue is None:
                self._create_queue()
                if self.sleeping():
                    self._stop()
                    raise ValueError("Music sleeping")
            track_id = self.track.get("playQueueItemID")
            if value["action"] == "next" and value.get("track_id") == track_id:
                self._advance()
            elif value["action"] == "timeline" and value.get("track_id") == track_id:
                position, state = value.get("time", 0), value.get("state")
                if (isinstance(position, bool) or not isinstance(position, int) or
                        not 0 <= position <= 86400000 or state not in {"playing", "paused"}):
                    raise ValueError("Invalid music timeline")
                self._timeline(state, position)
                self.status["state"] = state
                self.status["error"] = ""
            if self.sleeping():
                self._stop()
                raise ValueError("Music sleeping")
            return {"track_id": self.track.get("playQueueItemID"),
                    "url": self.url + "/stream/" + self.stream_key,
                    "title": self.track.get("title", "Music"),
                    "artist": track_artist(self.track), **self._track_details()}

    def get_status(self):
        with self.lock:
            if self.sync["role"] != "off":
                return self._group_status()
            if self.sleeping():
                return {"state": "sleeping", "title": "", "artist": "", "error": ""}
            status = dict(self.status)
            status["paused_by_user"] = self.user_paused
            if self.track is not None:
                status["rating_key"] = self.track.get("ratingKey")
                status["liked"] = float(self.track.get("userRating", "0")) == 10
            if self.user_paused and self.settings["enabled"]:
                status["state"] = "paused"
            return status

    # ----- Music sync groups -------------------------------------------------------------

    def _new_group(self, previous=None):
        if previous is not None and getattr(self, "queue", None) is not None:
            return previous
        return {"started_at": None, "paused_at": None, "user_paused": False, "idle": False,
                "timeline_at": 0.0}

    def _set_sync(self, sync):
        self._stop()
        self.sync = dict(sync)
        self.listeners.clear()
        self.group = self._new_group()
        self.remote, self.remote_at, self.offset = None, 0.0, ClockOffset()
        self.sync_error, self.sync_retry_at, self.machine = "", 0.0, ""
        self.local_listening = (False, 0.0)
        self.link_generation += 1
        self.leader_client = None
        if self.sync["role"] == "follower":
            self.leader_client = LeaderClient(self.sync["leader"], self.sync["token"])
            self.link_thread = threading.Thread(target=self._link, args=(self.link_generation,), daemon=True)
            self.link_thread.start()
        self.link_wake.set()

    def _frozen(self):
        return self.group["user_paused"] or self.group["idle"]

    def _set_frozen(self, now, user=None, idle=None):
        group, was = self.group, self._frozen()
        if user is not None:
            group["user_paused"] = user
        if idle is not None:
            group["idle"] = idle
        if not was and self._frozen():
            group["paused_at"] = now
        elif was and not self._frozen():
            if group["started_at"] is not None and group["paused_at"] is not None:
                group["started_at"] += now - group["paused_at"]
            group["paused_at"] = None

    def _position(self, now):
        group = self.group
        if group["started_at"] is None:
            return 0.0
        return (group["paused_at"] if group["paused_at"] is not None else now) - group["started_at"]

    def _duration(self):
        try:
            return int(self.track.get("duration", "0")) / 1000
        except ValueError:
            return 0.0

    def _safe_timeline(self, state, position):
        try:
            self._timeline(state, position)
        except (ValueError, OSError, http.client.HTTPException):
            pass

    def _leader_tick(self):
        with self.lock:
            now = music_sync.now()
            if self.sync["role"] != "leader" or now < self.sync_retry_at:
                return
            try:
                self._leader_step(now)
            except (ValueError, OSError, http.client.HTTPException) as error:
                self._stop()
                self.group = self._new_group()
                self.status.update(state="error", error=str(error) if isinstance(error, EmptyMusicSourceError)
                                   else "Music playback unavailable; check Plex connection.")
                self.sync_retry_at = now + 15

    def _leader_step(self, now):
        if not self.settings["enabled"]:
            if self.queue is not None:
                self._stop()
            self.group = self._new_group()
            self.status.update(state="disabled", error="")
            return
        active = any(listening and now - seen <= music_sync.LISTENER_TIMEOUT
                     for listening, seen in self.listeners.values())
        if self.queue is None:
            if not active:
                return
            user_paused = self.group["user_paused"]
            self._create_queue()
            self.group = self._new_group()
            self.group.update(started_at=now + music_sync.START_LEAD, user_paused=user_paused,
                              paused_at=now if user_paused else None)
            self.status.update(state="playing", error="")
        group = self.group
        # Nobody listening: freeze the playhead instead of advancing Plex all night.
        self._set_frozen(now, idle=not active)
        if self._frozen():
            if group["user_paused"] and active and now >= group["timeline_at"]:
                self._safe_timeline("paused", int(max(0.0, self._position(now)) * 1000))
                group["timeline_at"] = now + 10
            return
        duration = self._duration()
        if now >= group["started_at"] + max(duration, 0):
            end = group["started_at"] + max(duration, 0)
            self._advance()
            group["started_at"] = end if 0 <= now - end < 3 else now + music_sync.START_LEAD
            group["timeline_at"] = 0.0
        if now >= group["timeline_at"]:
            self._safe_timeline("playing", int(max(0.0, now - group["started_at"]) * 1000))
            group["timeline_at"] = now + 10

    def _group_command(self, action):
        now = music_sync.now()
        if action == "pause":
            self._set_frozen(now, user=True)
        elif action == "resume":
            self._set_frozen(now, user=False)
        elif action == "skip":
            if self.track is None or self.queue is None:
                raise ValueError("No current song is available to skip")
            try:
                self._advance()
            except (OSError, http.client.HTTPException) as error:
                raise ValueError("Music playback unavailable; check Plex connection.") from error
            self.group.update(started_at=now + music_sync.START_LEAD, timeline_at=0.0)
            if self._frozen():
                self.group["paused_at"] = now
        else:
            raise ValueError("Invalid music playback action")

    def _leader_state(self, now):
        group, track = self.group, self.track
        if not self.settings["enabled"]:
            state = "stopped"
        elif track is None or group["started_at"] is None:
            state = "error" if self.status["state"] == "error" else "waiting"
        elif group["user_paused"]:
            state = "paused"
        elif group["idle"]:
            state = "idle"
        else:
            state = "playing"
        result = {"state": state, "server_time": now, "machine": self.machine,
                  "error": self.status.get("error", "") if state == "error" else ""}
        if state in {"playing", "paused", "idle"}:
            result.update(rating_key=track.get("ratingKey"), title=track.get("title", "Music"),
                          artist=track_artist(track),
                          duration=self._duration(), started_at=group["started_at"],
                          position=self._position(now),
                          liked=float(track.get("userRating", "0")) == 10, **self._upcoming())
        return result

    def peer(self, name, listening):
        """Leader side of a follower poll; returns the group playhead in leader time."""
        with self.lock:
            if self.sync["role"] != "leader":
                raise ValueError("This display is not leading a music group")
            now = music_sync.now()
            self.listeners[name] = (listening, now)
            for member, (_, seen) in list(self.listeners.items()):
                if member and now - seen > music_sync.MEMBER_TIMEOUT:
                    del self.listeners[member]
        self._leader_tick()
        with self.lock:
            return self._leader_state(music_sync.now())

    def members(self):
        with self.lock:
            now = music_sync.now()
            return [{"name": name, "listening": listening, "last_seen": seen}
                    for name, (listening, seen) in sorted(self.listeners.items())
                    if name and now - seen <= music_sync.MEMBER_TIMEOUT]

    def sync_connected(self):
        with self.lock:
            if self.sync["role"] != "follower":
                return True, ""
            connected = self.remote is not None and music_sync.now() - self.remote_at <= 10
            return connected, self.sync_error

    def _remote_state(self, now):
        remote = self.remote
        if remote is None or now - self.remote_at > 30:
            return {"state": "waiting", "error": self.sync_error or "Waiting for the leader display"}
        state = dict(remote)
        if self.sync_error:
            state["error"] = self.sync_error
        if "started_at" in state:
            state["started_at"] -= self.offset.offset
            if state["state"] != "playing":
                # A frozen playhead keeps its position; re-anchor it to the local clock.
                state["started_at"] = now - state["position"]
        return state

    def _sync_control(self, listening):
        if self.sync["role"] == "leader":
            with self.lock:
                self.listeners[""] = (listening, music_sync.now())
            self._leader_tick()
        with self.lock:
            now = music_sync.now()
            if self.sync["role"] == "leader":
                state = self._leader_state(now)
            else:
                self.local_listening = (listening, now)
                state = self._remote_state(now)
                if state.get("rating_key") and (self.track is None
                                                or self.track.get("ratingKey") != state["rating_key"]):
                    state = {"state": "waiting", "error": state.get("error", "")}
            state.pop("server_time", None)
            state.pop("machine", None)
            state["now"] = now
            if state.get("rating_key") and self.track is not None:
                state["track_id"] = self.stream_key
                state["url"] = self.url + "/stream/" + self.stream_key
                state.update(self._track_details())
            return state

    def _group_status(self):
        now = music_sync.now()
        state = self._leader_state(now) if self.sync["role"] == "leader" else self._remote_state(now)
        mapped = {"stopped": "disabled", "idle": "playing"}.get(state["state"], state["state"])
        status = {"state": mapped, "title": state.get("title", ""), "artist": state.get("artist", ""),
                  "error": state.get("error", ""),
                  "paused_by_user": state["state"] == "paused"}
        if state.get("rating_key"):
            status["rating_key"] = state["rating_key"]
            status["liked"] = bool(state.get("liked"))
        return status

    def _link(self, generation):
        while not self.shutdown.is_set() and self.link_generation == generation:
            try:
                self.forward(None, generation)
            except (ValueError, OSError, http.client.HTTPException) as error:
                with self.lock:
                    if self.link_generation == generation:
                        self.sync_error = str(error) if isinstance(error, LeaderError) else (
                            "Cannot follow the leader display")
            self.link_wake.wait(music_sync.POLL_INTERVAL)
            self.link_wake.clear()

    def forward(self, command, generation=None):
        """Follower: poll the leader (optionally sending a group command) and follow its track."""
        with self.lock:
            if self.sync["role"] != "follower" or self.leader_client is None:
                raise ValueError("This display is not following a music group")
            generation = self.link_generation if generation is None else generation
            client = self.leader_client
            listening, seen = self.local_listening
            body = {"name": self.sync_name,
                    "listening": listening and music_sync.now() - seen <= music_sync.LISTENER_TIMEOUT}
        if command is not None:
            body["command"] = command
        sent = music_sync.now()
        payload = client.call(body)
        received = music_sync.now()
        remote = self._validate_remote(payload)
        with self.lock:
            if self.link_generation != generation:
                return
            self.offset.add(sent, payload.get("server_time"), received)
            self.remote, self.remote_at, self.sync_error = remote, received, ""
            if remote.get("rating_key") and (self.track is None
                                             or self.track.get("ratingKey") != remote["rating_key"]):
                self._follow_track(remote["rating_key"], remote["machine"])
            elif not remote.get("rating_key") and self.track is not None:
                self._close_streams()
                self.track, self.stream_key = None, ""

    @staticmethod
    def _validate_remote(payload):
        state = payload.get("state")
        if state not in {"stopped", "waiting", "error", "playing", "paused", "idle"}:
            raise LeaderError("The leader sent an invalid music state")
        remote = {"state": state, "machine": str(payload.get("machine", ""))[:128],
                  "error": str(payload.get("error", ""))[:200]}
        if state in {"playing", "paused", "idle"}:
            numbers = [payload.get(name) for name in ("duration", "started_at", "position", "server_time")]
            if (not isinstance(payload.get("rating_key"), str) or not RATING_KEY.fullmatch(payload["rating_key"])
                    or any(isinstance(n, bool) or not isinstance(n, (int, float)) for n in numbers)):
                raise LeaderError("The leader sent an invalid music state")
            remote.update(rating_key=payload["rating_key"], title=str(payload.get("title", ""))[:200],
                          artist=str(payload.get("artist", ""))[:200], duration=float(payload["duration"]),
                          started_at=float(payload["started_at"]), position=float(payload["position"]),
                          liked=payload.get("liked") is True)
            for key in ("next_title", "next_artist"):
                if isinstance(payload.get(key), str) and payload[key]:
                    remote[key] = payload[key][:200]
        return remote

    def _follow_track(self, rating_key, machine):
        if self.client is None:
            self.client = self.client_factory()
        if not self.machine:
            self.machine = self.client.identity()["machine_identifier"]
        if self.machine != machine:
            raise LeaderError("This display uses a different Plex server from the leader")
        container = self.client.xml("/library/metadata/" + rating_key)
        track = next(container.iter("Track"), None)
        part = track.find("./Media/Part") if track is not None else None
        if (track is None or track.get("ratingKey") != rating_key or part is None
                or not PART_KEY.fullmatch(part.get("key", ""))):
            raise LeaderError("The leader's song is not available from this display's Plex server")
        self._close_streams()
        self.track = track
        self.stream_key = secrets.token_urlsafe(24)
        self.status.update(title=track.get("title", "Music"), artist=track_artist(track), error="")

    def close(self):
        self.shutdown.set()
        self.link_wake.set()
        self.sleep_thread.join()
        if self.link_thread is not None:
            self.link_thread.join()
        with self.lock:
            self._stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
