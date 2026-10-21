from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .app import atomic_write, static_file
from .carousel import (
    DEFAULT_PLEX_BITRATE_KBPS, RETRY_COOLDOWN, PlaybackState, Scene,
    validate_plex_bitrate, validate_url,
)
from .hls import MAX_VIDEO_BITRATE, resolve_video_url

LOG = logging.getLogger(__name__)
STALL_TIMEOUT = 30


def reachable(url: str) -> bool:
    if not url:
        return False
    request = urllib.request.Request(url, headers={"User-Agent": "WebDisplay/1"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, HTTPException):
        return False


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    # Each renderer has its own session, including Chromium's child processes.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


class SceneServer:
    """Read-only loopback UI. No config, administrator sessions or secrets are served."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._payload: dict[str, object] = {"kind": "recovery"}
        self.ready = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: object) -> None:
                pass

            def do_GET(self) -> None:
                if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                path = urlsplit(self.path)
                if path.path == "/api/scene":
                    with owner._lock:
                        body = json.dumps(owner._payload).encode()
                    content_type = "application/json"
                elif path.path == "/api/scene/ready":
                    with owner._lock:
                        if path.query != f"revision={owner._payload.get('revision')}":
                            self.send_error(HTTPStatus.CONFLICT)
                            return
                        owner.ready.set()
                    body, content_type = b"{}", "application/json"
                else:
                    asset = static_file("/" if path.path == "/scene" else path.path)
                    if asset is None:
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    body, content_type = asset
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type + "; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
                )
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/scene"

    def show(self, scene: Scene | None) -> None:
        with self._lock:
            self.ready.clear()
            self._payload = {
                "kind": "dashboard" if scene else "recovery",
                "revision": secrets.token_hex(16),
                "layout": scene["layout"] if scene else "notice",
                "text": scene["text"] if scene else "",
            }

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class SceneRenderer:
    def __init__(
        self,
        launch_browser: Callable[[str], subprocess.Popen[bytes]],
        runtime: Path,
        mpv: str = "/usr/bin/mpv",
    ):
        self.launch_browser = launch_browser
        self.runtime = runtime
        self.mpv = mpv
        self.server = SceneServer()
        self.probe_process: subprocess.Popen[bytes] | None = None
        self.resolve_process: subprocess.Popen[bytes] | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.scene: Scene | None = None
        self.recovering = False
        self.ipc: socket.socket | None = None
        self.ipc_path = runtime / "mpv.sock"
        self.buffer = b""
        self.video_state: PlaybackState = "starting"
        self.position: float | None = None
        self.last_progress = 0.0
        self.request_id = 0
        self.recovery_retry_at = 0.0
        self.error = ""
        self.failures: dict[str, dict[str, object]] = {}
        self._publish_status()

    def failure(self, reason: str | None = None) -> None:
        if self.scene is None:
            return
        message = reason or self.error or "Renderer failed before playback completed."
        self.failures[self.scene["id"]] = {"message": message, "time": int(time.time())}
        while len(self.failures) > 30:
            del self.failures[next(iter(self.failures))]
        LOG.warning("Scene %s: %s", self.scene["id"], message)
        self._publish_status()

    def _publish_status(self) -> None:
        atomic_write(
            self.runtime / "carousel-status.json",
            json.dumps({"failures": self.failures, "updated": int(time.time())}) + "\n",
            mode=0o640,
        )

    def start(self, scene: Scene) -> None:
        self.stop()
        self.recovery_retry_at = 0.0
        self.scene = scene
        self.error = ""
        try:
            if scene["kind"] == "web":
                self.probe_process = subprocess.Popen(
                    [sys.executable, "-m", "webdisplay.scene_player", "--probe", scene["url"]],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                )
            elif scene["kind"] == "dashboard":
                self.server.show(scene)
                self.process = self.launch_browser(self.server.url)
            else:
                if scene["source"] == "plex":
                    video_url = scene.get("plex_playback_url", "")
                    if not video_url:
                        self.error = "Plex playback is not available in the runtime playlist."
                        self.video_state = "failed"
                        return
                else:
                    video_url = scene["url"]
                self.resolve_process = subprocess.Popen(
                    [
                        sys.executable, "-m", "webdisplay.scene_player",
                        "--resolve-video", video_url,
                        "--max-video-bitrate", str(self._video_bitrate()),
                    ],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
                )
        except (OSError, subprocess.SubprocessError):
            self.error = "Could not start the scene renderer."
            LOG.error("Could not launch renderer for scene %s", scene["id"])
            self.video_state = "failed"

    def _video_bitrate(self) -> int:
        if self.scene and self.scene["source"] == "plex":
            return validate_plex_bitrate(
                self.scene.get("plex_bitrate_kbps") or DEFAULT_PLEX_BITRATE_KBPS
            ) * 1000
        return MAX_VIDEO_BITRATE

    def _launch_video(self, url: str) -> None:
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.ipc_path.unlink(missing_ok=True)
        self.video_state = "starting"
        self.position = None
        self.last_progress = time.monotonic()
        audio_options = (
            [
                "--aid=auto", "--mute=no",
                "--audio-device=alsa/hdmi:CARD=vc4hdmi,DEV=0",
            ]
            if self.scene and self.scene.get("audio_enabled")
            else ["--mute=yes", "--audio=no"]
        )
        self.process = subprocess.Popen([
            self.mpv, "--no-config", "--load-scripts=no", "--ytdl=no",
            "--autoload-files=no",
            "--demuxer-lavf-o=protocol_whitelist=[http,https,tcp,tls,crypto,data]",
            "--fullscreen", "--no-terminal",
            "--vo=gpu", "--gpu-context=x11egl", "--hwdec=v4l2m2m",
            *audio_options, "--idle=yes", "--force-window=immediate",
            f"--hls-bitrate={self._video_bitrate()}",
            f"--input-ipc-server={self.ipc_path}", "--", url,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def _video_status(self) -> PlaybackState:
        if self.ipc is None:
            if not self.ipc_path.exists():
                return "starting"
            self.ipc = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.ipc.settimeout(0.2)
            try:
                self.ipc.connect(str(self.ipc_path))
            except OSError:
                self.ipc.close()
                self.ipc = None
                return "starting"
        try:
            self.request_id += 1
            command = {"command": ["get_property", "time-pos"], "request_id": self.request_id}
            self.ipc.sendall(json.dumps(command).encode() + b"\n")
            for _ in range(16):
                try:
                    chunk = self.ipc.recv(8192)
                except socket.timeout:
                    break
                if not chunk:
                    self.error = "Player IPC disconnected unexpectedly."
                    return "failed"
                self.buffer += chunk
                if len(self.buffer) > 64 * 1024:
                    LOG.error("Player IPC response exceeded limit")
                    return "failed"
                while b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("Invalid player message")
                    if message.get("event") == "end-file":
                        code = message.get("file_error")
                        self.error = {
                            "loading failed": "Stream could not be opened or read.",
                            "audio output initialization failed": "Audio output initialization failed.",
                            "video output initialization failed": "Video output initialization failed.",
                            "no audio or video data played": "Stream contained no playable video.",
                            "unrecognized file format": "Video format is not recognized.",
                        }.get(code, "Player ended with a playback error.")
                        return "finished" if message.get("reason") == "eof" else "failed"
                    position = message.get("data")
                    if "request_id" in message and type(position) in (int, float):
                        self.video_state = "playing"
                        if self.position is None or position != self.position:
                            self.position = position
                            self.last_progress = time.monotonic()
            if self.video_state == "playing" and time.monotonic() - self.last_progress >= STALL_TIMEOUT:
                LOG.warning("Video stalled")
                self.error = "Video made no playback progress for 30 seconds."
                return "failed"
        except (OSError, ValueError):
            LOG.error("Player IPC failed")
            self.error = "Could not communicate with the video player."
            return "failed"
        return self.video_state

    def state(self) -> PlaybackState:
        if self.scene is None:
            return "failed"
        if self.resolve_process is not None:
            result = self.resolve_process.poll()
            if result is None:
                return "starting"
            output, _ = self.resolve_process.communicate(timeout=1)
            stop_process(self.resolve_process)
            self.resolve_process = None
            if result != 0:
                try:
                    code = json.loads(output).get("error")
                except (ValueError, AttributeError):
                    code = None
                self.error = RESOLUTION_ERRORS.get(code, "Could not resolve the video stream.")
                LOG.warning("HLS resolution failed for scene %s", self.scene["id"])
                return "failed"
            try:
                payload = json.loads(output)
                url = validate_url(payload["url"])
                if not url:
                    raise ValueError("Empty video URL")
                self._launch_video(url)
            except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
                self.error = "Could not start mpv. Check that the player is installed."
                LOG.error("Could not prepare video for scene %s", self.scene["id"])
                return "failed"
        if self.probe_process is not None:
            result = self.probe_process.poll()
            if result is None:
                return "starting"
            stop_process(self.probe_process)
            self.probe_process = None
            if result != 0:
                self.error = "Website could not be reached."
                return "failed"
            try:
                self.process = self.launch_browser(self.scene["url"])
            except (OSError, subprocess.SubprocessError):
                LOG.error("Could not launch browser for scene %s", self.scene["id"])
                return "failed"
        if self.process is None:
            return "failed"
        result = self.process.poll()
        if result is not None:
            self.error = f"Renderer exited with code {result}."
            if result != 0 or self.scene["kind"] != "video":
                LOG.warning("Renderer for scene %s exited with code %d", self.scene["id"], result)
            return "finished" if self.scene["kind"] == "video" and result == 0 else "failed"
        if self.scene["kind"] == "video":
            state = self._video_status()
            if state == "playing":
                self._clear_failure()
            return state
        if self.scene["kind"] == "dashboard" and not self.server.ready.is_set():
            return "starting"
        self._clear_failure()
        return "playing"

    def _clear_failure(self) -> None:
        if self.scene is not None and self.scene["id"] in self.failures:
            del self.failures[self.scene["id"]]
            self._publish_status()

    def stop(self) -> None:
        scene = self.scene
        stop_process(self.process)
        self.process = None
        if scene is not None and scene["source"] == "plex" and scene.get("plex_stop_url"):
            try:
                request = urllib.request.Request(scene["plex_stop_url"], method="POST")
                with urllib.request.urlopen(request, timeout=5):
                    pass
            except (OSError, urllib.error.URLError, ValueError):
                LOG.warning("Could not stop Plex playback session for scene %s", scene["id"])
        self.scene = None
        self.recovering = False
        stop_process(self.probe_process)
        self.probe_process = None
        stop_process(self.resolve_process)
        if self.resolve_process is not None and self.resolve_process.stdout is not None:
            self.resolve_process.stdout.close()
        self.resolve_process = None
        if self.ipc is not None:
            self.ipc.close()
            self.ipc = None
        self.buffer = b""
        self.ipc_path.unlink(missing_ok=True)

    def recovery(self) -> None:
        if self.recovering and self.process is not None and self.process.poll() is None:
            return
        if time.monotonic() < self.recovery_retry_at:
            return
        self.stop()
        self.server.show(None)
        self.recovering = True
        self.recovery_retry_at = time.monotonic() + RETRY_COOLDOWN
        try:
            self.process = self.launch_browser(self.server.url)
        except (OSError, subprocess.SubprocessError):
            LOG.error("Could not launch carousel recovery screen")

    def close(self) -> None:
        self.stop()
        self.server.close()


RESOLUTION_ERRORS = {
    "not-hls": "Stream returned a web page or other non-HLS content. Use a direct media playlist URL.",
    "unsupported": "No compatible HLS rendition at or below 720p and the configured bitrate limit.",
    "invalid": "HLS playlist is invalid or exceeds the supported limits.",
    "network": "Stream could not be reached. Check availability and access permissions.",
}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--probe")
    operation.add_argument("--resolve-video")
    parser.add_argument("--max-video-bitrate", type=int, default=MAX_VIDEO_BITRATE)
    args = parser.parse_args()
    if args.probe:
        raise SystemExit(0 if reachable(args.probe) else 1)
    try:
        validate_plex_bitrate(args.max_video_bitrate // 1000)
        payload = json.dumps({
            "url": resolve_video_url(args.resolve_video, args.max_video_bitrate),
        }, ensure_ascii=False)
        if len(payload.encode("utf-8")) > 4096:
            raise ValueError("Resolved stream URL exceeds the IPC limit")
        print(payload)
    except (OSError, ValueError, HTTPException) as error:
        code = "network"
        if isinstance(error, ValueError):
            code = "not-hls" if str(error) == "Stream did not return an HLS playlist" else (
                "unsupported" if str(error).startswith("No compatible HLS rendition") else "invalid"
            )
        print(json.dumps({"error": code}))
        LOG.error("Could not resolve the video stream")
        raise SystemExit(1)
