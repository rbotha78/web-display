from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .hostname_helper import validate_hostname
from .network import default_route_interface, internet_status, website_status

LOG = logging.getLogger("web-display")
SESSION_TTL_SECONDS = 8 * 60 * 60
MAX_BODY_BYTES = 16 * 1024
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
    if len(value) > 2048:
        raise ValueError("URL is too long")
    return value


def validate_flag(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("Setting must be true or false")
    return value


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> dict[str, object]:
        with self._lock:
            if not self.path.exists():
                return {"url": "", "password": None, "auto_ap": False}
            with self.path.open(encoding="utf-8") as stream:
                data = json.load(stream)
            return {
                "url": validate_url(data.get("url", "")),
                "password": data.get("password"),
                "auto_ap": data.get("auto_ap") is True,
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
            atomic_write(self.path, json.dumps(data, sort_keys=True) + "\n")
            return data


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
    ):
        self.wifi_helper = wifi_helper
        self.hostname_helper = hostname_helper
        self.config = ConfigStore(state_path)
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

    def paired(self) -> bool:
        return bool(self.config.load()["password"])

    def publish_target(self) -> None:
        atomic_write(
            self.target_path,
            str(self.config.load()["url"]) + "\n",
            mode=0o640,
        )

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

        def _json(self, status: HTTPStatus, value: object) -> None:
            self._send(status, json.dumps(value).encode())

        def _body(self) -> dict[str, object]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ValueError("Invalid Content-Length") from error
            if length <= 0 or length > MAX_BODY_BYTES:
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

        def _require_session(self, csrf: bool = False) -> Session | None:
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
                self._json(HTTPStatus.OK, {"status": "ok", "paired": app.paired()})
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
            if self.path == "/api/config":
                if not self._require_session():
                    return
                config = app.config.load()
                self._json(
                    HTTPStatus.OK,
                    {"url": config["url"], "auto_ap": config["auto_ap"]},
                )
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
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            try:
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
                elif self.path == "/api/reboot":
                    if not self._require_session(csrf=True):
                        return
                    app.run_action("reboot")
                    self._json(HTTPStatus.ACCEPTED, {"ok": True})
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

        def do_PUT(self) -> None:
            try:
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
        for item in socket.getaddrinfo(socket.gethostname(), None):
            address = item[4][0].split("%", 1)[0]
            parsed = ipaddress.ip_address(address)
            if not parsed.is_loopback:
                addresses.add(address)
    except socket.gaierror:
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
    )
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(app))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.certificate, args.private_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    LOG.info("Management service listening on https://%s:%d", args.bind, args.port)
    server.serve_forever()


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
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    serve(args)


if __name__ == "__main__":
    main()
