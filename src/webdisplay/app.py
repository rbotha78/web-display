from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO
from urllib.parse import parse_qs, urlsplit

from . import releases
from .carousel import (
    DEFAULT_PLEX_BITRATE_KBPS,
    validate_plex_bitrate,
    validate_playlist,
    validate_url,
)
from .hostname_helper import validate_hostname
from .network import default_route_interface, internet_status, website_status
from .plex import PlexClient, PlexPlaybackProxy, validate_server_url, validate_token
from .updater import MAX_BUNDLE_BYTES, UpdateError, Updater, parse_version

LOG = logging.getLogger("web-display")
SESSION_TTL_SECONDS = 8 * 60 * 60
MAX_BODY_BYTES = 16 * 1024
PLAYBACK_STATUS_PATH = Path("/run/web-display-kiosk/carousel-status.json")
WIFI_OPERATIONS = {
    "/api/wifi/scan": "scan",
    "/api/wifi/country": "country",
    "/api/wifi/connect": "connect",
    "/api/wifi/forget": "forget",
}


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


def validate_flag(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("Setting must be true or false")
    return value


MAX_API_KEYS = 10
KEY_PREFIX = "wdk_"


def valid_keys(value: object) -> list[dict[str, object]]:
    keys = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict) and all(isinstance(item.get(f), str) for f in ("id", "name", "hash")):
            keys.append(item)
    return keys


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def validate_key_name(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9 ._@-]{1,40}", value.strip()):
        raise ValueError("Key name must be 1-40 letters, digits, spaces or . _ @ -")
    return value.strip()


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> dict[str, object]:
        with self._lock:
            if not self.path.exists():
                return {"url": "", "password": None, "auto_ap": False,
                        "api_keys": [], "playlist": {"enabled": False, "scenes": []},
                        "plex_server_url": "", "plex_token": "", "plex_client_identifier": "",
                        "plex_default_bitrate_kbps": DEFAULT_PLEX_BITRATE_KBPS}
            with self.path.open(encoding="utf-8") as stream:
                data = json.load(stream)
            return {
                "url": validate_url(data.get("url", "")),
                "password": data.get("password"),
                "auto_ap": data.get("auto_ap") is True,
                "api_keys": valid_keys(data.get("api_keys")),
                "playlist": validate_playlist(data.get("playlist", {"enabled": False, "scenes": []})),
                "playlist_revision": data.get("playlist_revision", ""),
                "plex_server_url": validate_server_url(data["plex_server_url"]) if data.get("plex_server_url") else "",
                "plex_token": data.get("plex_token", ""),
                "plex_client_identifier": data.get("plex_client_identifier", ""),
                "plex_default_bitrate_kbps": validate_plex_bitrate(
                    data.get("plex_default_bitrate_kbps", DEFAULT_PLEX_BITRATE_KBPS)
                ),
            }

    def update(self, **changes: object) -> dict[str, object]:
        with self._lock:
            if self.path.exists():
                with self.path.open(encoding="utf-8") as stream:
                    data = json.load(stream)
            else:
                data = {"url": "", "password": None, "auto_ap": False}
            data.update(changes)
            data["url"] = validate_url(data.get("url", ""))
            data["auto_ap"] = validate_flag(data.get("auto_ap", False))
            data["playlist"] = validate_playlist(data.get("playlist", {"enabled": False, "scenes": []}))
            data["plex_server_url"] = validate_server_url(data["plex_server_url"]) if data.get("plex_server_url") else ""
            data["plex_token"] = validate_token(data["plex_token"]) if data.get("plex_token") else ""
            data["plex_default_bitrate_kbps"] = validate_plex_bitrate(
                data.get("plex_default_bitrate_kbps", DEFAULT_PLEX_BITRATE_KBPS)
            )
            identifier = data.get("plex_client_identifier", "")
            if identifier and (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", identifier)):
                raise ValueError("Invalid Plex client identifier")
            atomic_write(self.path, json.dumps(data, sort_keys=True) + "\n")
            return data

    def ensure_plex_client_identifier(self) -> str:
        with self._lock:
            if self.path.exists():
                with self.path.open(encoding="utf-8") as stream:
                    data = json.load(stream)
            else:
                data = {"url": "", "password": None, "auto_ap": False}
            identifier = data.get("plex_client_identifier")
            if isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9_-]{16,128}", identifier):
                return identifier
            identifier = str(uuid.uuid4())
            data["plex_client_identifier"] = identifier
            atomic_write(self.path, json.dumps(data, sort_keys=True) + "\n")
            return identifier


def hash_password(password: str) -> dict[str, str | int]:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=2**15,
        r=8,
        p=1,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )
    return {
        "algorithm": "scrypt",
        "n": 2**15,
        "r": 8,
        "p": 1,
        "salt": salt.hex(),
        "digest": digest.hex(),
    }


def verify_password(password: str, stored: object) -> bool:
    if not isinstance(stored, dict) or stored.get("algorithm") != "scrypt":
        return False
    try:
        digest = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(str(stored["salt"])),
            n=int(stored["n"]),
            r=int(stored["r"]),
            p=int(stored["p"]),
            dklen=32,
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(digest.hex(), str(stored["digest"]))
    except (KeyError, TypeError, ValueError):
        return False


@dataclass
class Session:
    csrf: str
    expires_at: float


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self) -> tuple[str, Session]:
        token = secrets.token_urlsafe(32)
        session = Session(
            csrf=secrets.token_urlsafe(32), expires_at=time.time() + SESSION_TTL_SECONDS
        )
        with self._lock:
            self._sessions[token] = session
        return token, session

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        now = time.time()
        with self._lock:
            session = self._sessions.get(token)
            if not session or session.expires_at <= now:
                self._sessions.pop(token, None)
                return None
            session.expires_at = now + SESSION_TTL_SECONDS
            return session

    def delete(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(token, None)


class LoginThrottle:
    def __init__(self, limit: int = 5, window_seconds: int = 300):
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allowed(self, client: str) -> bool:
        cutoff = time.time() - self.window_seconds
        with self._lock:
            recent = [value for value in self._attempts.get(client, []) if value > cutoff]
            self._attempts[client] = recent
            return len(recent) < self.limit

    def failure(self, client: str) -> None:
        with self._lock:
            self._attempts.setdefault(client, []).append(time.time())

    def success(self, client: str) -> None:
        with self._lock:
            self._attempts.pop(client, None)


class Application:
    def __init__(
        self,
        state_path: Path,
        pairing_path: Path,
        target_path: Path,
        action_helper: Path,
        wifi_helper: Path | None = None,
        hostname_helper: Path | None = None,
        update_helper: Path | None = None,
        updater: Updater | None = None,
        debug_keys_helper: Path | None = None,
    ):
        self.update_helper = update_helper
        self.debug_keys_helper = debug_keys_helper
        self.debug_keys_lock = threading.Lock()
        self.updater = updater or Updater()
        self.update_lock = threading.Lock()
        self.keys_lock = threading.Lock()
        self.playlist_lock = threading.Lock()
        self.publish_lock = threading.Lock()
        self.wifi_helper = wifi_helper
        self.hostname_helper = hostname_helper
        self.config = ConfigStore(state_path)
        self.plex_client_identifier = self.config.ensure_plex_client_identifier()
        self.plex_proxy = PlexPlaybackProxy(self.plex_client)
        self.sessions = SessionStore()
        self.throttle = LoginThrottle()
        self.pairing_path = pairing_path
        self.target_path = target_path
        self.action_helper = action_helper
        self.pairing_code = self._load_or_create_pairing_code()
        self.publish_target()

    def _load_or_create_pairing_code(self) -> str:
        if self.config.load()["password"]:
            self.pairing_path.unlink(missing_ok=True)
            return ""
        if self.pairing_path.exists():
            return self.pairing_path.read_text(encoding="ascii").strip()
        code = f"{secrets.randbelow(1_000_000):06d}"
        atomic_write(self.pairing_path, code + "\n", mode=0o640)
        return code

    def create_key(self, name: str) -> dict[str, object]:
        with self.keys_lock:
            keys = self.config.load()["api_keys"]
            if len(keys) >= MAX_API_KEYS:  # type: ignore[arg-type]
                raise ValueError("Too many API keys; revoke one first")
            key = KEY_PREFIX + secrets.token_urlsafe(32)
            entry = {"id": secrets.token_hex(4), "name": name, "hash": hash_key(key), "created": int(time.time())}
            self.config.update(api_keys=[*keys, entry])  # type: ignore[list-item]
        return {"id": entry["id"], "name": name, "key": key}

    def list_keys(self) -> list[dict[str, object]]:
        return [
            {"id": k["id"], "name": k["name"], "created": k.get("created")}
            for k in self.config.load()["api_keys"]  # type: ignore[union-attr]
        ]

    def revoke_key(self, key_id: str) -> None:
        with self.keys_lock:
            keys = self.config.load()["api_keys"]
            remaining = [k for k in keys if k["id"] != key_id]  # type: ignore[union-attr]
            if len(remaining) == len(keys):  # type: ignore[arg-type]
                raise ValueError("No such API key")
            self.config.update(api_keys=remaining)

    def key_valid(self, key: str) -> bool:
        digest = hash_key(key)
        found = False
        for entry in self.config.load()["api_keys"]:  # type: ignore[union-attr]
            if hmac.compare_digest(str(entry["hash"]), digest):
                found = True
        return found

    def paired(self) -> bool:
        return bool(self.config.load()["password"])

    def publish_target(self) -> None:
        with self.publish_lock:
            config = self.config.load()
            playlist = validate_playlist(config["playlist"])
            self.plex_proxy.clear()
            runtime_scenes = []
            for scene in playlist["scenes"]:
                published = dict(scene)
                if scene["source"] == "plex":
                    published["plex_playback_url"], published["plex_stop_url"] = self.plex_proxy.publish(
                        scene["plex_rating_key"],
                        scene["plex_bitrate_kbps"] or config["plex_default_bitrate_kbps"],
                    )
                runtime_scenes.append(published)
            atomic_write(
                self.target_path,
                str(config["url"]) + "\n",
                mode=0o640,
            )
            atomic_write(
                self.target_path.with_name("playlist.json"),
                json.dumps({
                    **playlist, "scenes": runtime_scenes,
                    "revision": config.get("playlist_revision", ""),
                }) + "\n",
                mode=0o640,
            )

    def close(self) -> None:
        self.plex_proxy.close()

    def plex_client(self) -> PlexClient:
        config = self.config.load()
        if not config["plex_server_url"] or not config["plex_token"]:
            raise ValueError("Configure a Plex server and token first")
        return PlexClient(
            str(config["plex_server_url"]),
            str(config["plex_token"]),
            self.plex_client_identifier,
            socket.gethostname(),
            self.updater.running_version(),
        )

    def plex_config(self) -> dict[str, object]:
        config = self.config.load()
        return {
            "server_url": config["plex_server_url"],
            "configured": bool(config["plex_server_url"] and config["plex_token"]),
            "default_bitrate_kbps": config["plex_default_bitrate_kbps"],
        }

    def save_plex_config(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError("Plex settings must be an object")
        current = self.config.load()
        clear_token = value.get("clear_token", False)
        if not isinstance(clear_token, bool):
            raise ValueError("clear_token must be true or false")
        token_input = value.get("token", "")
        default_bitrate = validate_plex_bitrate(
            value.get("default_bitrate_kbps", current["plex_default_bitrate_kbps"])
        )
        if token_input:
            token = validate_token(token_input)
        elif clear_token:
            self.config.update(
                plex_server_url="",
                plex_token="",
                plex_default_bitrate_kbps=default_bitrate,
                playlist_revision=secrets.token_hex(16),
            )
            self.publish_target()
            return {
                "server_url": "", "configured": False, "server_name": "",
                "default_bitrate_kbps": default_bitrate,
            }
        else:
            token = str(current["plex_token"])
        server_url = validate_server_url(value.get("server_url") or current["plex_server_url"])
        identity = PlexClient(
            server_url, token, self.plex_client_identifier,
            socket.gethostname(), self.updater.running_version(),
        ).identity()
        self.config.update(
            plex_server_url=server_url,
            plex_token=token,
            plex_default_bitrate_kbps=default_bitrate,
            playlist_revision=secrets.token_hex(16),
        )
        self.publish_target()
        return {
            "server_url": server_url,
            "configured": True,
            "server_name": identity["name"],
            "default_bitrate_kbps": default_bitrate,
        }

    def save_playlist(self, value: object) -> dict[str, object]:
        playlist = validate_playlist(value)
        with self.playlist_lock:
            self.config.update(playlist=playlist, playlist_revision=secrets.token_hex(16))
            self.publish_target()
        return dict(playlist)

    def run_action(self, action: str) -> None:
        subprocess.run(
            ["sudo", "--non-interactive", str(self.action_helper), action],
            check=True,
            timeout=10,
        )

    def run_wifi(self, request: dict[str, object]) -> dict[str, object]:
        if self.wifi_helper is None:
            raise ValueError("Wi-Fi management is not available")
        result = subprocess.run(
            ["sudo", "--non-interactive", str(self.wifi_helper)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            LOG.error("Wi-Fi helper returned invalid output (exit %d)", result.returncode)
            raise ValueError("Wi-Fi helper failed") from None
        if result.returncode != 0 or not isinstance(payload, dict):
            message = payload.get("error") if isinstance(payload, dict) else None
            raise ValueError(str(message or "Wi-Fi helper failed"))
        return payload

    def set_hostname(self, name: str) -> dict[str, object]:
        if self.hostname_helper is None:
            raise ValueError("Hostname changes are not available")
        result = subprocess.run(
            ["sudo", "--non-interactive", str(self.hostname_helper)],
            input=json.dumps({"hostname": name}),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            LOG.error("Hostname helper returned invalid output (exit %d)", result.returncode)
            raise ValueError("Hostname helper failed") from None
        if result.returncode != 0 or not isinstance(payload, dict):
            message = payload.get("error") if isinstance(payload, dict) else None
            raise ValueError(str(message or "Hostname helper failed"))
        return payload

    def debug_keys_available(self) -> bool:
        # Only development images install this helper.
        return self.debug_keys_helper is not None and self.debug_keys_helper.exists()

    def run_debug_keys(self, request: dict[str, object]) -> dict[str, object]:
        if not self.debug_keys_available():
            if request.get("operation") == "list":
                return {"enabled": False, "keys": []}
            raise ValueError("Development SSH access is not available on this device")
        with self.debug_keys_lock:
            result = subprocess.run(
                ["sudo", "--non-interactive", str(self.debug_keys_helper)],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            LOG.error("SSH key helper returned invalid output (exit %d)", result.returncode)
            raise ValueError("SSH key helper failed") from None
        if result.returncode != 0 or not isinstance(payload, dict):
            message = payload.get("error") if isinstance(payload, dict) else None
            raise ValueError(str(message or "SSH key helper failed"))
        return payload

    def update_status(self) -> dict[str, object]:
        status = self.updater.status()
        status["repository"] = releases.REPOSITORY
        return status

    def newer_than_installed(self, version: str) -> bool:
        status = self.updater.status()
        newest = parse_version(str(status["installed_version"]))
        if status["floor"]:
            newest = max(newest, parse_version(str(status["floor"])))
        return parse_version(version) > newest and version not in status["failed"]  # type: ignore[operator]

    def check_update(self) -> dict[str, object]:
        latest = releases.latest_bundle()
        available = bool(latest) and self.newer_than_installed(str(latest["version"]))  # type: ignore[index]
        return {
            "available": available,
            "latest": {"version": latest["version"], "size": latest["size"]} if latest else None,
        }

    def install_release_update(self) -> dict[str, object]:
        latest = releases.latest_bundle()
        if not latest or not self.newer_than_installed(str(latest["version"])):
            raise UpdateError("No newer update is available")
        with releases.open_download(str(latest["url"])) as download:
            return self.install_update(download, int(latest["size"]))  # type: ignore[call-overload]

    def install_update(self, source: BinaryIO, limit: int = MAX_BUNDLE_BYTES) -> dict[str, object]:
        """Stream a bundle to the root updater, which performs all verification."""
        if self.update_helper is None:
            raise ValueError("Updates are not available")
        if not self.update_lock.acquire(blocking=False):
            raise UpdateError("An update is already in progress")
        try:
            process = subprocess.Popen(
                ["sudo", "--non-interactive", str(self.update_helper), "install"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
            assert process.stdin is not None and process.stdout is not None
            sent = 0
            try:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    sent += len(chunk)
                    if sent > limit:
                        raise UpdateError("Update file is too large")
                    process.stdin.write(chunk)
            except BrokenPipeError:
                pass
            except BaseException:
                process.kill()
                process.wait()
                raise
            finally:
                with contextlib.suppress(OSError):
                    process.stdin.close()
            output = process.stdout.read(65536)
            process.stdout.close()
            process.wait(timeout=300)
        finally:
            self.update_lock.release()
        try:
            payload = json.loads(output)
        except ValueError:
            LOG.error("Update helper returned invalid output (exit %d)", process.returncode)
            raise ValueError("Update helper failed") from None
        if process.returncode != 0 or not isinstance(payload, dict):
            message = payload.get("error") if isinstance(payload, dict) else None
            raise UpdateError(str(message or "Update failed"))
        return payload

    def network_status(self) -> dict[str, object]:
        status: dict[str, object] = {
            "internet": internet_status(),
            "website": website_status(str(self.config.load()["url"])),
            "default_route": default_route_interface(),
        }
        try:
            status["wifi"] = self.run_wifi({"operation": "status"})
        except ValueError as error:
            status["wifi"] = {"error": str(error)}
        return status


STATIC_DIR = Path(__file__).parent / "static"
CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


def static_file(url_path: str, root: Path = STATIC_DIR) -> tuple[bytes, str] | None:
    """Return a file from the built UI bundle, or None if it is not a plain file inside it."""
    relative = "index.html" if url_path == "/" else url_path.split("?", 1)[0].lstrip("/")
    content_type = CONTENT_TYPES.get(Path(relative).suffix)
    if content_type is None:
        return None
    try:
        base = root.resolve()
        target = (base / relative).resolve()
        if base not in target.parents or not target.is_file():
            return None
        return target.read_bytes(), content_type
    except OSError:
        return None


class BoundedReader:
    """Read at most `length` bytes from a request body."""

    def __init__(self, stream: BinaryIO, length: int):
        self.stream = stream
        self.remaining = length

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        size = self.remaining if size < 0 else min(size, self.remaining)
        data = self.stream.read(size)
        self.remaining -= len(data)
        if not data:
            raise ValueError("Upload ended early")
        return data


def make_handler(app: Application) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "WebDisplay/1"

        def log_message(self, format_string: str, *args: object) -> None:
            LOG.info("%s - %s", self.client_address[0], format_string % args)

        def _send(
            self,
            status: HTTPStatus,
            body: bytes = b"",
            content_type: str = "application/json",
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
            )
            if headers:
                for name, value in headers.items():
                    self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(
            self, status: HTTPStatus, value: object, headers: dict[str, str] | None = None
        ) -> None:
            self._send(status, json.dumps(value).encode(), headers=headers)

        def _body(self, limit: int = MAX_BODY_BYTES) -> dict[str, object]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ValueError("Invalid Content-Length") from error
            if length <= 0 or length > limit:
                raise ValueError("Invalid request body size")
            try:
                value = json.loads(self.rfile.read(length))
            except json.JSONDecodeError as error:
                raise ValueError("Request body must be valid JSON") from error
            if not isinstance(value, dict):
                raise ValueError("Request body must be a JSON object")
            return value

        def _session_token(self) -> str | None:
            cookie = SimpleCookie(self.headers.get("Cookie"))
            item = cookie.get("session")
            return item.value if item else None

        def _session(self) -> Session | None:
            return app.sessions.get(self._session_token())

        def _bearer_key(self) -> str | None:
            header = self.headers.get("Authorization", "")
            return header[7:].strip() if header.startswith("Bearer ") else None

        def _require_session(self, csrf: bool = False, allow_key: bool = True) -> Session | None:
            key = self._bearer_key() if allow_key else None
            if key is not None:
                client = self.client_address[0]
                if not app.throttle.allowed(client):
                    self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "try again later"})
                    return None
                if not app.key_valid(key):
                    app.throttle.failure(client)
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "invalid API key"})
                    return None
                # Bearer keys are not ambient credentials, so no CSRF token is needed.
                return Session(csrf="", expires_at=0)
            session = self._session()
            if not session:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return None
            if csrf and not hmac.compare_digest(
                self.headers.get("X-CSRF-Token", ""), session.csrf
            ):
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid CSRF token"})
                return None
            return session

        def do_GET(self) -> None:
            if self.path == "/health":
                self._json(HTTPStatus.OK, {"status": "ok", "paired": app.paired(), "version": app.updater.running_version()})
                return
            if not self.path.startswith("/api/"):
                asset = static_file(self.path)
                if asset is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                else:
                    self._send(HTTPStatus.OK, asset[0], asset[1])
                return
            if self.path == "/api/session":
                session = self._require_session()
                if session:
                    self._json(HTTPStatus.OK, {"csrf": session.csrf})
                return
            if self.path == "/api/keys":
                if self._require_session(allow_key=False):
                    self._json(HTTPStatus.OK, {"keys": app.list_keys()})
                return
            if self.path == "/api/debug-keys":
                if self._require_session():
                    try:
                        self._json(HTTPStatus.OK, app.run_debug_keys({"operation": "list"}))
                    except (ValueError, subprocess.SubprocessError, OSError):
                        LOG.exception("Listing SSH keys failed")
                        self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "system action failed"})
                return
            if self.path == "/api/config":
                if not self._require_session():
                    return
                config = app.config.load()
                self._json(
                    HTTPStatus.OK,
                    {"url": config["url"], "auto_ap": config["auto_ap"]},
                )
                return
            request_path = urlsplit(self.path)
            if request_path.path == "/api/plex/config" and not request_path.query:
                if self._require_session():
                    self._json(HTTPStatus.OK, app.plex_config())
                return
            if request_path.path in ("/api/plex/libraries", "/api/plex/browse"):
                if not self._require_session():
                    return
                try:
                    client = app.plex_client()
                    if request_path.path.endswith("/libraries"):
                        if request_path.query:
                            raise ValueError("Invalid Plex library location")
                        result = {"items": client.browse()}
                    else:
                        query = parse_qs(request_path.query, keep_blank_values=True)
                        if set(query) - {"key"}:
                            raise ValueError("Invalid Plex library location")
                        key_values = query.get("key", [])
                        if len(key_values) > 1:
                            raise ValueError("Invalid Plex library location")
                        result = {"items": client.browse(key_values[0] if key_values else None)}
                    self._json(HTTPStatus.OK, result)
                except ValueError as error:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(error)})
                return
            if self.path == "/api/playlist":
                if self._require_session():
                    self._json(HTTPStatus.OK, app.config.load()["playlist"])
                return
            if self.path == "/api/playlist/status":
                if not self._require_session():
                    return
                try:
                    status = json.loads(PLAYBACK_STATUS_PATH.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    status = {"failures": {}, "updated": None}
                except (OSError, ValueError):
                    LOG.exception("Reading carousel status failed")
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Could not read carousel playback status"})
                    return
                self._json(HTTPStatus.OK, status)
                return
            if self.path == "/api/status":
                if not self._require_session():
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "hostname": socket.gethostname(),
                        "addresses": local_addresses(),
                        "paired": app.paired(),
                    },
                )
                return
            if self.path == "/api/network":
                if not self._require_session():
                    return
                self._json(HTTPStatus.OK, app.network_status())
                return
            if self.path == "/api/update/status":
                if not self._require_session():
                    return
                self._json(HTTPStatus.OK, app.update_status())
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def _upload_update(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ValueError("Invalid Content-Length") from error
            if length <= 0 or length > MAX_BUNDLE_BYTES:
                raise ValueError("Invalid update size")
            self.connection.settimeout(60)
            upload = BoundedReader(self.rfile, length)
            try:
                result = app.install_update(upload, length)
            finally:
                # Read the rest of the body so a client sending a rejected upload can see the error.
                with contextlib.suppress(OSError, ValueError):
                    while upload.read(1024 * 1024):
                        pass
            self._json(HTTPStatus.OK, result)

        def do_POST(self) -> None:
            try:
                if self.path == "/api/update/upload":
                    if self._require_session(csrf=True):
                        self._upload_update()
                    return
                body = self._body()
                if self.path == "/api/pair":
                    self._pair(body)
                elif self.path == "/api/login":
                    self._login_response(body)
                elif self.path == "/api/logout":
                    if not self._require_session(csrf=True):
                        return
                    app.sessions.delete(self._session_token())
                    self._json(
                        HTTPStatus.OK,
                        {"ok": True},
                        headers={"Set-Cookie": expired_cookie()},
                    )
                elif self.path == "/api/keys":
                    if not self._require_session(csrf=True, allow_key=False):
                        return
                    self._json(HTTPStatus.CREATED, app.create_key(validate_key_name(body.get("name"))))
                elif self.path == "/api/debug-keys":
                    if not self._require_session(csrf=True):
                        return
                    request = {"operation": "add", "key": body.get("key")}
                    if "name" in body:
                        request["name"] = body["name"]
                    self._json(HTTPStatus.CREATED, app.run_debug_keys(request))
                elif self.path == "/api/reboot":
                    if not self._require_session(csrf=True):
                        return
                    app.run_action("reboot")
                    self._json(HTTPStatus.ACCEPTED, {"ok": True})
                elif self.path == "/api/update/check":
                    if not self._require_session(csrf=True):
                        return
                    self._json(HTTPStatus.OK, app.check_update())
                elif self.path == "/api/update/install":
                    if not self._require_session(csrf=True):
                        return
                    self._json(HTTPStatus.OK, app.install_release_update())
                elif self.path == "/api/hostname":
                    if not self._require_session(csrf=True):
                        return
                    result = app.set_hostname(validate_hostname(body.get("hostname")))
                    self._json(HTTPStatus.ACCEPTED, result)
                elif self.path in WIFI_OPERATIONS:
                    if not self._require_session(csrf=True):
                        return
                    request = {"operation": WIFI_OPERATIONS[self.path]}
                    for field in ("country", "ssid", "password"):
                        if field in body:
                            request[field] = body[field]
                    self._json(HTTPStatus.OK, app.run_wifi(request))
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except ValueError as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except (subprocess.SubprocessError, OSError):
                LOG.exception("System action failed")
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "system action failed"},
                )

        def do_DELETE(self) -> None:
            try:
                if self.path.startswith("/api/debug-keys/"):
                    if not self._require_session(csrf=True):
                        return
                    result = app.run_debug_keys(
                        {"operation": "remove", "id": self.path[len("/api/debug-keys/"):]}
                    )
                    self._json(HTTPStatus.OK, result)
                    return
                prefix = "/api/keys/"
                if not self.path.startswith(prefix):
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                if not self._require_session(csrf=True, allow_key=False):
                    return
                app.revoke_key(self.path[len(prefix):])
                self._json(HTTPStatus.OK, {"ok": True})
            except ValueError as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except (subprocess.SubprocessError, OSError):
                LOG.exception("System action failed")
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "system action failed"})

        def do_PUT(self) -> None:
            try:
                if self.path == "/api/plex/config":
                    if self._require_session(csrf=True):
                        self._json(HTTPStatus.OK, app.save_plex_config(self._body()))
                    return
                if self.path == "/api/playlist":
                    if self._require_session(csrf=True):
                        self._json(HTTPStatus.OK, app.save_playlist(self._body(512 * 1024)))
                    return
                if self.path != "/api/config":
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                if not self._require_session(csrf=True):
                    return
                body = self._body()
                changes: dict[str, object] = {}
                if "url" in body:
                    changes["url"] = validate_url(body["url"])
                if "auto_ap" in body:
                    changes["auto_ap"] = validate_flag(body["auto_ap"])
                if not changes:
                    raise ValueError("No settings supplied")
                config = app.config.update(**changes)
                app.publish_target()
                self._json(
                    HTTPStatus.OK,
                    {"url": config["url"], "auto_ap": config["auto_ap"]},
                )
            except ValueError as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except OSError:
                LOG.exception("Saving display configuration failed")
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Could not save display configuration"})

        def _pair(self, body: dict[str, object]) -> None:
            client = self.client_address[0]
            if app.paired():
                self._json(HTTPStatus.CONFLICT, {"error": "already paired"})
                return
            if not app.throttle.allowed(client):
                self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "try again later"})
                return
            code = body.get("code")
            if not isinstance(code, str) or not hmac.compare_digest(code, app.pairing_code):
                app.throttle.failure(client)
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "invalid pairing code"})
                return
            password = body.get("password")
            if not isinstance(password, str):
                raise ValueError("Password must be a string")
            app.config.update(password=hash_password(password))
            app.pairing_code = ""
            app.pairing_path.unlink(missing_ok=True)
            app.throttle.success(client)
            self._login_response({"password": password})

        def _login_response(self, body: dict[str, object]) -> None:
            client = self.client_address[0]
            if not app.throttle.allowed(client):
                self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "try again later"})
                return
            password = body.get("password")
            if not isinstance(password, str) or not verify_password(
                password, app.config.load()["password"]
            ):
                app.throttle.failure(client)
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "invalid credentials"})
                return
            app.throttle.success(client)
            token, session = app.sessions.create()
            self._send(
                HTTPStatus.OK,
                json.dumps({"csrf": session.csrf}).encode(),
                headers={"Set-Cookie": session_cookie(token)},
            )

    return Handler


def session_cookie(token: str) -> str:
    return (
        f"session={token}; Path=/; Max-Age={SESSION_TTL_SECONDS}; "
        "Secure; HttpOnly; SameSite=Strict"
    )


def expired_cookie() -> str:
    return "session=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict"


def local_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        output = subprocess.run(
            ["ip", "-o", "addr", "show", "scope", "global"],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout
        for line in output.splitlines():
            fields = line.split()
            if "inet" in fields or "inet6" in fields:
                addresses.add(fields[fields.index("inet" if "inet" in fields else "inet6") + 1].split("/", 1)[0])
    except (OSError, subprocess.SubprocessError):
        pass
    if addresses:
        return sorted(addresses)
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None):
            address = item[4][0].split("%", 1)[0]
            parsed = ipaddress.ip_address(address)
            if not parsed.is_loopback:
                addresses.add(address)
    except (socket.gaierror, ValueError):
        pass
    return sorted(addresses)


def serve(args: argparse.Namespace) -> None:
    app = Application(
        Path(args.state),
        Path(args.pairing_code),
        Path(args.target),
        Path(args.action_helper),
        Path(args.wifi_helper),
        Path(args.hostname_helper),
        Path(args.update_helper),
        debug_keys_helper=Path(args.debug_keys_helper),
    )
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(app))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.certificate, args.private_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    LOG.info("Management service listening on https://%s:%d", args.bind, args.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        app.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--state", default="/var/lib/web-display/config.json")
    parser.add_argument("--pairing-code", default="/run/web-display/pairing-code")
    parser.add_argument("--target", default="/run/web-display/target-url")
    parser.add_argument("--certificate", default="/var/lib/web-display/tls/cert.pem")
    parser.add_argument("--private-key", default="/var/lib/web-display/tls/key.pem")
    parser.add_argument(
        "--action-helper", default="/usr/lib/web-display/system-action"
    )
    parser.add_argument("--wifi-helper", default="/usr/lib/web-display/wifi-action")
    parser.add_argument(
        "--hostname-helper", default="/usr/lib/web-display/hostname-action"
    )
    parser.add_argument("--update-helper", default="/usr/lib/web-display/update-action")
    parser.add_argument(
        "--debug-keys-helper", default="/usr/lib/web-display/debug-keys-action"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    serve(args)


if __name__ == "__main__":
    main()
