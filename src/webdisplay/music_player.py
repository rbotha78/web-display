from __future__ import annotations

import json
import logging
import re
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .scene_player import RendererStopError, stop_process
from . import bluetooth
from .audio import DEFAULT_AUDIO_OUTPUT, audio_outputs, validate_audio_output
from .sleep import SleepRuntime

LOG = logging.getLogger(__name__)
MAX_ART_BYTES = 2 * 1024 * 1024


def track_details(track: dict, endpoint: str) -> dict[str, str]:
    """Optional album name and relay cover URL; a cover must use this relay's capability."""
    details = {}
    album = track.get("album")
    if isinstance(album, str) and album:
        details["album"] = album[:200]
    art, prefix = track.get("art_url"), endpoint.removesuffix("control") + "art/"
    if isinstance(art, str) and art.startswith(prefix) and re.fullmatch(r"[A-Za-z0-9_-]{32}", art[len(prefix):]):
        details["art_url"] = art
    return details


# Load/seek this far ahead of the group, then unpause exactly when the group arrives.
SYNC_PRIME = 0.8
# Before a track is audible, re-prime when the group is already this far ahead.
SYNC_SEEK_THRESHOLD = 0.25
# Sync speed changes resample (shifting pitch slightly) instead of time-stretching:
# mpv's scaletempo2 starves Bluetooth output on a Pi 2, losing time instead of gaining it.
# 0.5% is about 9 cents, below what listeners notice, and pulls in 250 ms per 50 seconds.
SYNC_MAX_SPEED_CHANGE = 0.005


def sync_correction(drift: float) -> float:
    """Return the mpv speed for drift = local - group position, in seconds.

    Audible tracks are never paused or re-seeked: drift is pulled in gently by
    tiny playback-rate changes, and any remainder is realigned when the next
    track starts at the group's scheduled time.
    """
    if abs(drift) <= 0.03:
        return 1.0
    # Converge over roughly ten seconds within the inaudible rate limit.
    change = max(-SYNC_MAX_SPEED_CHANGE, min(SYNC_MAX_SPEED_CHANGE, drift / 10))
    return round(1.0 - change, 4)


def control_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid music endpoint")
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
            or url.username or url.password or url.query or url.fragment
            or not re.fullmatch(r"/music/[A-Za-z0-9_-]{32}/control", url.path)):
        raise ValueError("Invalid music endpoint")
    return value


class MusicPlayer:
    """An independent audio-only mpv, surviving carousel/browser transitions."""

    def __init__(self, config_path: Path, runtime: Path, mpv: str = "/usr/bin/mpv"):
        self.config_path, self.mpv = config_path, mpv
        self.ipc_path = runtime / "music-mpv.sock"
        self.lock = threading.RLock()
        self.shutdown = threading.Event()
        self.process = self.ipc = None
        self.buffer = b""
        self.request_id = 0
        self.ended = None
        self.paused = False
        self.user_paused = False
        self.sync_mode = False
        self.sync = {"track": None, "armed": False, "settle_at": 0.0}
        self._now_playing = {"state": "disabled", "title": "", "artist": ""}
        self.audio_output = DEFAULT_AUDIO_OUTPUT
        self.output_delay = 0.0
        self.sleep_runtime = SleepRuntime(config_path.with_name("sleep.json"))
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.sleep_thread = threading.Thread(target=self._watch_sleep, daemon=True)
        self.thread.start()
        self.sleep_thread.start()

    def _watch_sleep(self):
        while not self.shutdown.wait(0.5):
            process = self.process
            if process is not None and self.sleep_runtime.sleeping():
                # Interrupt mpv even while the worker is waiting on Plex or mpv IPC.
                try:
                    stop_process(process)
                    with self.lock:
                        if self.process is process:
                            self._stop_player()
                except (OSError, RendererStopError):
                    LOG.error("Could not stop the sleeping music player")

    def pause_for_video(self, paused: bool):
        with self.lock:
            self.paused = paused
            if getattr(self, "sync_mode", False):
                # The sync loop rejoins the live group position after video audio ends.
                if paused and self.ipc is not None:
                    self._silence()
                return
            if self.ipc is not None:
                self._pause()

    def now_playing(self) -> dict[str, str]:
        with self.lock:
            status = dict(self._now_playing)
            if (self.paused or self.user_paused) and status["state"] != "disabled":
                status["state"] = "paused"
            return status

    def album_art(self) -> tuple[bytes, str] | None:
        """Current cover from the loopback relay, cached for the track."""
        with self.lock:
            url = self._now_playing.get("art_url", "")
            cached = getattr(self, "_art_cache", None)
        if not url:
            return None
        if cached and cached[0] == url:
            return cached[1]
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                content_type = response.headers.get_content_type()
                body = response.read(MAX_ART_BYTES + 1)
        except (OSError, ValueError):
            return None
        if content_type not in {"image/jpeg", "image/png", "image/webp"} or not body or len(body) > MAX_ART_BYTES:
            return None
        with self.lock:
            self._art_cache = (url, (body, content_type))
        return body, content_type

    def _pause(self):
        if self.paused or self.user_paused:
            self._command(["set_property", "pause", True])
            # Pausing alone retains ALSA's device; release it for the video player.
            self._command(["set_property", "aid", "no"])
        else:
            self._command(["set_property", "aid", "auto"])
            self._command(["set_property", "pause", False])

    def _silence(self):
        self._command(["set_property", "pause", True])
        self._command(["set_property", "aid", "no"])
        self._command(["set_property", "speed", 1.0])
        self.sync["armed"] = False

    def _command(self, command):
        self.request_id += 1
        request_id = self.request_id
        self.ipc.sendall(json.dumps({"command": command, "request_id": request_id}).encode() + b"\n")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                message = json.loads(line)
                if message.get("event") == "end-file":
                    self.ended = message.get("reason")
                if message.get("request_id") == request_id:
                    if message.get("error") != "success":
                        if command[:2] == ["get_property", "time-pos"]:
                            return None
                        raise ValueError("Audio player command failed")
                    return message.get("data")
            chunk = self.ipc.recv(8192)
            if not chunk:
                raise OSError("Audio player disconnected")
            self.buffer += chunk
        raise OSError("Audio player timed out")

    def _resolve_output(self, settings):
        """Return (configured wired output, output to use now), preferring a connected speaker."""
        wired = validate_audio_output(settings.get("audio_output", DEFAULT_AUDIO_OUTPUT))
        output, self.output_delay = bluetooth.active_output(
            wired, self.config_path.with_name("bluetooth.json"), self.config_path.with_name("bluetooth-status.json"))
        return wired, output

    def _apply_output(self, output):
        with self.lock:
            if output == self.audio_output:
                return
            self.audio_output = output
            if self.ipc is not None:
                LOG.info("Switching music output to %s", output)
                self._command(["set_property", "audio-device", output])

    def _output_available(self):
        if bluetooth.is_bluetooth_output(self.audio_output):
            return bluetooth.output_available(self.audio_output, self.config_path.with_name("bluetooth-status.json"))
        return self.audio_output in {item["id"] for item in audio_outputs()}

    def _launch(self):
        if not self._output_available():
            raise OSError("Selected audio output is unavailable")
        self.ipc_path.unlink(missing_ok=True)
        self.process = subprocess.Popen([
            self.mpv, "--no-config", "--no-terminal", "--no-video", "--idle=yes",
            "--ao=alsa", f"--audio-device={self.audio_output}", "--audio-pitch-correction=no",
            "--pause=yes", "--volume=0", f"--input-ipc-server={self.ipc_path}",
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        for _ in range(50):
            if self.shutdown.wait(0.1):
                raise OSError("Music stopped")
            if self.sleep_runtime.sleeping():
                raise OSError("Music sleeping")
            if self.process.poll() is not None:
                raise OSError("Audio player exited")
            if self.ipc_path.exists():
                self.ipc = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.ipc.settimeout(2)
                self.ipc.connect(str(self.ipc_path))
                self.buffer = b""
                return
        raise OSError("Audio player did not start")

    def _stop_player(self):
        with self.lock:
            self._now_playing = {"state": "disabled", "title": "", "artist": ""}
            stop_process(self.process)
            self.process = None
            if self.ipc is not None:
                self.ipc.close()
                self.ipc = None
            self.ipc_path.unlink(missing_ok=True)
            self.ended = None

    @staticmethod
    def _control(url, action, **kwargs):
        request = urllib.request.Request(control_url(url), json.dumps({"action": action, **kwargs}).encode(),
                                         {"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read(8192))

    def _load(self, track, endpoint, volume):
        url = urlsplit(track["url"])
        base = urlsplit(endpoint)
        if (url.scheme != base.scheme or url.netloc != base.netloc or url.query or url.fragment
                or not url.path.startswith(base.path.removesuffix("control") + "stream/")):
            raise ValueError("Invalid audio stream")
        with self.lock:
            if self.paused or self.user_paused or (hasattr(self, "sleep_runtime") and self.sleep_runtime.sleeping()):
                return False
            if self.process is None:
                self._launch()
            if hasattr(self, "sleep_runtime") and self.sleep_runtime.sleeping():
                self._stop_player()
                return False
            self._command(["set_property", "pause", True])
            self._command(["set_property", "aid", "no" if self.paused else "auto"])
            self._command(["set_property", "volume", volume])
            self._command(["loadfile", track["url"], "replace"])
            self.ended = None
            self._pause()
            return True

    def _run(self):
        if not hasattr(self, "sleep_runtime"):
            self.sleep_runtime = SleepRuntime(self.config_path.with_name("sleep.json"))
        endpoint = ""
        identity = None
        track = None
        failures, retry_at, timeline_at = 0, 0.0, 0.0
        volume = None
        pending_next = False
        last_skip_request = None
        last_position, last_progress = None, time.monotonic()
        try:
            while not self.shutdown.is_set():
                try:
                    try:
                        settings = json.loads(self.config_path.read_text())
                    except (FileNotFoundError, ValueError):
                        settings = {"enabled": False}
                    if settings.get("sync") is True:
                        self._stop_player()
                        identity, track, endpoint = None, None, ""
                        self._run_sync()
                        continue
                    audio_output, effective_output = self._resolve_output(settings)
                    enabled = bool(settings.get("enabled") and not self.sleep_runtime.sleeping())
                    new_identity = (enabled, settings.get("station_key"),
                                    settings.get("control_url"), audio_output)
                    if new_identity != identity:
                        self._stop_player()
                        if endpoint:
                            try:
                                self._control(endpoint, "stop")
                            except (OSError, ValueError):
                                pass
                        identity, track = new_identity, None
                        self.audio_output = effective_output
                        pending_next = False
                        failures, retry_at = 0, 0.0
                        endpoint = control_url(settings["control_url"]) if enabled else ""
                        if enabled:
                            with self.lock:
                                self._now_playing = {"state": "waiting", "title": "", "artist": ""}
                        last_skip_request = None
                    self._apply_output(effective_output)
                    with self.lock:
                        user_paused = bool(settings.get("paused", False))
                        if self.user_paused != user_paused:
                            self.user_paused = user_paused
                            timeline_at = 0
                            if self.ipc is not None:
                                self._pause()
                        skip = settings.get("skip") or {}
                        if skip.get("request_id") != last_skip_request:
                            last_skip_request = skip.get("request_id")
                            if track is not None and skip.get("track_id") == track["track_id"]:
                                pending_next = True
                                retry_at = 0
                                if self.process is not None and self.ipc is not None:
                                    self._command(["stop"])
                                self._now_playing = {"state": "waiting", "title": "", "artist": ""}
                    if not endpoint or time.monotonic() < retry_at:
                        self.shutdown.wait(1)
                        continue
                    with self.lock:
                        waiting_for_pause = (self.paused or self.user_paused) and (
                            track is None or pending_next or self.process is None
                        )
                    if waiting_for_pause:
                        self.shutdown.wait(0.5)
                        continue
                    if track is None or pending_next or self.process is None:
                        new_track = self._control(endpoint, "next" if pending_next else "current",
                                                  track_id=track["track_id"] if track else "")
                        if not self._load(new_track, endpoint, settings["volume"]):
                            self.shutdown.wait(0.5)
                            continue
                        track, pending_next = new_track, False
                        last_position, last_progress = None, time.monotonic()
                        volume = settings["volume"]
                        timeline_at = 0
                    with self.lock:
                        if self.process is None:
                            continue
                        if self.process.poll() is not None:
                            raise OSError("Audio player exited")
                        position = self._command(["get_property", "time-pos"])
                        if volume != settings["volume"]:
                            self._command(["set_property", "volume", settings["volume"]])
                            volume = settings["volume"]
                        ended, paused = self.ended, self.paused or self.user_paused
                        if position is not None and ended is None:
                            self._now_playing = {
                                "state": "playing",
                                "title": track.get("title", ""),
                                "artist": track.get("artist", ""),
                                **track_details(track, endpoint),
                            }
                        elif ended is not None:
                            self._now_playing = {"state": "waiting", "title": "", "artist": ""}
                    if paused or (position is not None and position != last_position):
                        if not paused and last_position is not None and position != last_position:
                            failures = 0
                        last_position, last_progress = position, time.monotonic()
                    elif ended is None and time.monotonic() - last_progress > 30:
                        raise OSError("Audio playback stalled")
                    if ended == "eof":
                        pending_next = True
                        failures = 0
                    elif ended == "error":
                        raise OSError("Audio playback failed")
                    elif time.monotonic() >= timeline_at and (paused or position is not None):
                        self._control(endpoint, "timeline", track_id=track["track_id"],
                                      state="paused" if paused else "playing", time=int((position or 0) * 1000))
                        timeline_at = time.monotonic() + 10
                    self.shutdown.wait(0.5)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    self._stop_player()
                    with self.lock:
                        self._now_playing = {"state": "error", "title": "", "artist": ""}
                    failures += 1
                    delay = 5 if failures == 1 else 15 if failures == 2 else 60
                    retry_at = time.monotonic() + delay
                    LOG.warning("Music playback interrupted (%s: %s); retrying in %s seconds",
                                type(error).__name__, error, delay)
                    if failures >= 3 and endpoint:
                        track, pending_next = None, False
                        try:
                            self._control(endpoint, "error")
                        except (OSError, ValueError):
                            pass
        finally:
            self._stop_player()
            if endpoint:
                try:
                    self._control(endpoint, "stop")
                except (OSError, ValueError):
                    pass

    # ----- Music sync group playback ------------------------------------------------

    def _run_sync(self):
        """Follow the relay's group playhead until the display leaves its group."""
        endpoint, failures, retry_at = "", 0, 0.0
        with self.lock:
            self.sync_mode, self.user_paused = True, False
            self.sync = {"track": None, "armed": False, "settle_at": 0.0}
        try:
            while not self.shutdown.is_set():
                try:
                    settings = json.loads(self.config_path.read_text())
                except (FileNotFoundError, ValueError):
                    settings = {}
                if settings.get("sync") is not True:
                    return
                try:
                    new_endpoint = control_url(settings["control_url"])
                    _, output = self._resolve_output(settings)
                    if new_endpoint != endpoint:
                        self._stop_player()
                        endpoint = new_endpoint
                        self.sync["track"], failures, retry_at = None, 0, 0.0
                    self._apply_output(output)
                    if time.monotonic() < retry_at:
                        self.shutdown.wait(0.5)
                        continue
                    sleeping = self.sleep_runtime.sleeping()
                    with self.lock:
                        muted = self.paused or sleeping
                    state = self._control(endpoint, "sync", listening=not muted)
                    self._sync_apply(state, endpoint, muted, sleeping, settings["volume"])
                    failures = 0
                except (OSError, ValueError, KeyError, TypeError) as error:
                    self._stop_player()
                    with self.lock:
                        self._now_playing = {"state": "error", "title": "", "artist": ""}
                        self.sync["track"] = None
                    failures += 1
                    delay = 5 if failures == 1 else 15
                    retry_at = time.monotonic() + delay
                    LOG.warning("Synced music interrupted (%s: %s); retrying in %s seconds",
                                type(error).__name__, error, delay)
                self.shutdown.wait(0.5)
        finally:
            self._stop_player()
            with self.lock:
                self.sync_mode = False

    def _sync_apply(self, state, endpoint, muted, sleeping, volume):
        url = state.get("url")
        if state.get("state") not in {"playing", "paused", "idle"} or not url:
            if self.process is not None:
                self._stop_player()
            with self.lock:
                self.sync["track"] = None
                waiting = state.get("state") in {"waiting", "playing", "paused", "idle"}
                self._now_playing = {"state": "waiting" if waiting else "error" if state.get("state") == "error"
                                     else "disabled", "title": "", "artist": ""}
            return
        prefix = endpoint.removesuffix("control") + "stream/"
        if not url.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", url[len(prefix):]):
            raise ValueError("Invalid audio stream")
        # Run ahead of the group by the speaker's extra latency so sound leaves it in step.
        started_at = float(state["started_at"]) - getattr(self, "output_delay", 0.0)
        duration = float(state["duration"])
        with self.lock:
            self._now_playing = {"state": "playing" if state["state"] == "playing" and not muted else "paused",
                                 "title": str(state.get("title", "")), "artist": str(state.get("artist", "")),
                                 **track_details(state, endpoint)}
            if sleeping:
                # The sleep watcher owns stopping mpv; never relaunch while asleep.
                self.sync["track"] = None
                return
            if muted or state["state"] != "playing":
                if self.ipc is not None:
                    self._silence()
                return
            if self.process is None:
                self._launch()
                self.sync["track"] = None
            if self.process.poll() is not None:
                raise OSError("Audio player exited")
            if self.sync["track"] != state["track_id"]:
                self._command(["set_property", "pause", True])
                self._command(["set_property", "speed", 1.0])
                self._command(["set_property", "volume", volume])
                self._command(["set_property", "aid", "auto"])
                target = max(0.0, time.time() - started_at + SYNC_PRIME)
                self._command(["set_property", "start", f"{target:.3f}"])
                self._command(["loadfile", url, "replace"])
                self.ended = None
                self.sync.update(track=state["track_id"], armed=False, volume=volume)
                return
            if self.sync.get("volume") != volume:
                self._command(["set_property", "volume", volume])
                self.sync["volume"] = volume
            position = self._command(["get_property", "time-pos"])
            if self.ended == "error":
                raise OSError("Audio playback failed")
            if position is None:
                return
            expected = time.time() - started_at
            if not self.sync["armed"]:
                wait = started_at + position - time.time()
                if wait < -SYNC_SEEK_THRESHOLD or position >= max(duration - 0.5, 0.0) > 0:
                    # Too late to start from the primed point; prime again ahead of the group.
                    self._command(["seek", f"{max(0.0, expected + SYNC_PRIME):.3f}", "absolute+exact"])
                    return
                if wait > 1.0:
                    return
                if wait > 0:
                    self.shutdown.wait(wait)
                self._command(["set_property", "aid", "auto"])
                self._command(["set_property", "pause", False])
                self.sync.update(armed=True, settle_at=time.monotonic() + 2)
                return
            if time.monotonic() < self.sync["settle_at"]:
                return
            self._command(["set_property", "speed", sync_correction(position - expected)])
            self.sync["settle_at"] = time.monotonic() + 2

    def close(self):
        self.shutdown.set()
        self.thread.join()
        self.sleep_thread.join()
