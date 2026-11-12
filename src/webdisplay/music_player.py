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
from .audio import DEFAULT_AUDIO_OUTPUT, audio_outputs, validate_audio_output
from .sleep import SleepRuntime

LOG = logging.getLogger(__name__)


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
        self._now_playing = {"state": "disabled", "title": "", "artist": ""}
        self.audio_output = DEFAULT_AUDIO_OUTPUT
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
            if self.ipc is not None:
                self._pause()

    def now_playing(self) -> dict[str, str]:
        with self.lock:
            status = dict(self._now_playing)
            if (self.paused or self.user_paused) and status["state"] != "disabled":
                status["state"] = "paused"
            return status

    def _pause(self):
        if self.paused or self.user_paused:
            self._command(["set_property", "pause", True])
            # Pausing alone retains ALSA's device; release it for the video player.
            self._command(["set_property", "aid", "no"])
        else:
            self._command(["set_property", "aid", "auto"])
            self._command(["set_property", "pause", False])

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

    def _launch(self):
        if self.audio_output not in {item["id"] for item in audio_outputs()}:
            raise OSError("Selected audio output is unavailable")
        self.ipc_path.unlink(missing_ok=True)
        self.process = subprocess.Popen([
            self.mpv, "--no-config", "--no-terminal", "--no-video", "--idle=yes",
            "--ao=alsa", f"--audio-device={self.audio_output}",
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
                    audio_output = validate_audio_output(settings.get("audio_output", DEFAULT_AUDIO_OUTPUT))
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
                        self.audio_output = audio_output
                        pending_next = False
                        failures, retry_at = 0, 0.0
                        endpoint = control_url(settings["control_url"]) if enabled else ""
                        if enabled:
                            with self.lock:
                                self._now_playing = {"state": "waiting", "title": "", "artist": ""}
                        last_skip_request = None
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
                except (OSError, ValueError, KeyError, TypeError):
                    self._stop_player()
                    with self.lock:
                        self._now_playing = {"state": "error", "title": "", "artist": ""}
                    failures += 1
                    delay = 5 if failures == 1 else 15 if failures == 2 else 60
                    retry_at = time.monotonic() + delay
                    LOG.warning("Music playback interrupted; retrying in %s seconds", delay)
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

    def close(self):
        self.shutdown.set()
        self.thread.join()
        self.sleep_thread.join()
