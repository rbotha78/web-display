from __future__ import annotations

import http.client
import json
import re
import secrets
import socket
import threading
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .plex import PlexClient, RATING_KEY, STATION_KEY
from .sleep import SleepRuntime

PART_KEY = re.compile(r"/library/parts/[0-9]+/[0-9]+/file\.[A-Za-z0-9]+\Z")


def validate_music(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Music settings must be an object")
    enabled, station, volume = value.get("enabled", False), value.get("station_key", ""), value.get("volume", 30)
    if not isinstance(enabled, bool):
        raise ValueError("Music enabled must be true or false")
    if not isinstance(station, str) or (station and not STATION_KEY.fullmatch(station)):
        raise ValueError("Select an advertised library radio station")
    if enabled and not station:
        raise ValueError("Select a music station before enabling playback")
    if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
        raise ValueError("Music volume must be an integer from 0 to 100")
    return {"enabled": enabled, "station_key": station, "volume": volume}


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
        self.status = {"state": "disabled", "title": "", "error": ""}
        self.sleep_runtime = SleepRuntime(sleep_path) if sleep_path is not None else None
        self.shutdown = threading.Event()
        self.streams_lock = threading.Lock()
        self.streams = set()
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
                except (ValueError, OSError, http.client.HTTPException):
                    with owner.lock:
                        owner.status["error"] = "Music playback unavailable; check Plex connection."
                        owner.status["state"] = "error"
                    self.reply(502, {"error": "Music playback unavailable; check Plex connection."})

            def do_GET(self):
                if not self.authorized():
                    self.reply(404, {})
                    return
                with owner.lock:
                    client, track = owner.client, owner.track
                    valid = (not owner.sleeping() and owner.settings["enabled"] and track is not None and
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

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.sleep_thread = threading.Thread(target=self._enforce_sleep, daemon=True)
        self.sleep_thread.start()

    def sleeping(self):
        return self.sleep_runtime is not None and self.sleep_runtime.sleeping()

    def _enforce_sleep(self):
        was_sleeping = False
        while not self.shutdown.wait(0.5):
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

    def configure(self, settings, identity):
        with self.lock:
            if (self.settings["station_key"] != settings["station_key"] or self.identity != identity
                    or self.settings["enabled"] != settings["enabled"]):
                self._stop()
                self.capability = secrets.token_urlsafe(24)
                self.queue = self.track = self.client = None
                self.stream_key = ""
                self.status = {"state": "waiting" if settings["enabled"] else "disabled", "title": "", "error": ""}
            self.settings, self.identity = dict(settings), identity
            return {**settings, "control_url": self.url + "/control"}

    def _timeline(self, state, position):
        if self.client is not None and self.queue is not None and self.track is not None:
            self.client.xml("/:/timeline", {
                "ratingKey": self.track.get("ratingKey"), "key": self.track.get("key"),
                "state": state, "time": position, "duration": self.track.get("duration", "0"),
                "playQueueID": self.queue.get("playQueueID"),
                "playQueueItemID": self.track.get("playQueueItemID"),
                "playQueueVersion": self.queue.get("playQueueVersion"),
            })

    def _stop(self):
        self._close_streams()
        try:
            self._timeline("stopped", 0)
        except (ValueError, OSError, http.client.HTTPException):
            pass
        finally:
            self.queue = self.track = self.client = None
            self.stream_key = ""
            self.status["title"] = ""

    def _select(self, track):
        part = track.find("./Media/Part")
        if (not RATING_KEY.fullmatch(track.get("ratingKey", "")) or
                not track.get("playQueueItemID", "").isdigit() or
                part is None or not PART_KEY.fullmatch(part.get("key", ""))):
            raise ValueError("Unsupported music track")
        self.track = track
        self.stream_key = secrets.token_urlsafe(24)
        self.status.update(title=track.get("title", "Music"), error="")

    def _create_queue(self):
        self.client = self.client_factory()
        machine = self.client.identity()["machine_identifier"]
        queue = self.client.xml("/playQueues", {
            "type": "audio",
            "uri": f"server://{machine}/com.plexapp.plugins.library{self.settings['station_key']}",
        }, method="POST")
        if not queue.get("playQueueID", "").isdigit():
            raise ValueError("Invalid music queue")
        tracks = list(queue.iter("Track"))
        selected = queue.get("playQueueSelectedItemID")
        track = next((t for t in tracks if t.get("playQueueItemID") == selected), None)
        if track is None:
            raise ValueError("Empty music queue")
        self._select(track)
        self.queue = queue

    def control(self, value):
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
                self._timeline("stopped", self.track.get("duration", "0"))
                queue = self.client.xml("/playQueues/" + self.queue.get("playQueueID"),
                                        {"center": track_id, "window": 5})
                tracks = list(queue.iter("Track"))
                index = next((i for i, t in enumerate(tracks) if t.get("playQueueItemID") == track_id), -1)
                if index < 0:
                    raise ValueError("Music queue lost the current track")
                if index + 1 >= len(tracks):
                    # Plex's advertised stations are finite on some servers.
                    # Start another server-generated station queue at its actual end.
                    self._create_queue()
                else:
                    self._select(tracks[index + 1])
                    self.queue = queue
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
                    "title": self.track.get("title", "Music")}

    def get_status(self):
        with self.lock:
            if self.sleeping():
                return {"state": "sleeping", "title": "", "error": ""}
            return dict(self.status)

    def close(self):
        self.shutdown.set()
        self.sleep_thread.join()
        with self.lock:
            self._stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
