"""Local web server for the manager UI and its JSON API (127.0.0.1 only)."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import mimetypes
import re
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .client import DeviceError
from .service import Manager
from .store import DeviceStore

LOG = logging.getLogger("web-display-manager")
STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 16 * 1024
ACTIONS = {"reboot", "url", "update-check", "update-install"}
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'"


def static_file(url_path: str, root: Path = STATIC_DIR) -> tuple[bytes, str] | None:
    relative = urlparse(url_path).path.lstrip("/") or "manager.html"
    target = (root / relative).resolve()
    if root.resolve() not in target.parents or not target.is_file():
        return None
    return target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream"


def make_handler(manager: Manager, token: str, port_getter) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "WebDisplayManager"

        def log_message(self, format_string: str, *args: object) -> None:
            LOG.info("%s %s", self.address_string(), format_string % args)

        def _send(self, status: HTTPStatus, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: HTTPStatus, value: object) -> None:
            self._send(status, json.dumps(value).encode())

        def _host_ok(self) -> bool:
            # Rejecting other Host values defeats DNS-rebinding attacks from web pages.
            return self.headers.get("Host", "") in (f"127.0.0.1:{port_getter()}", f"localhost:{port_getter()}")

        def _authorised(self) -> bool:
            if not hmac.compare_digest(self.headers.get("X-Manager-Token", ""), token):
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "missing or invalid manager token"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{port_getter()}", f"http://localhost:{port_getter()}"):
                self._json(HTTPStatus.FORBIDDEN, {"error": "origin not allowed"})
                return False
            return True

        def _body(self) -> dict[str, object]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ValueError("Invalid Content-Length") from None
            if length == 0:
                return {}
            if length < 0 or length > MAX_BODY:
                raise ValueError("Invalid request body size")
            try:
                value = json.loads(self.rfile.read(length))
            except ValueError:
                raise ValueError("Request body must be valid JSON") from None
            if not isinstance(value, dict):
                raise ValueError("Request body must be a JSON object")
            return value

        def _guard(self, handler) -> None:
            if not self._host_ok():
                self._json(HTTPStatus.FORBIDDEN, {"error": "host not allowed"})
                return
            try:
                handler()
            except ValueError as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except LookupError as error:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(error)})
            except DeviceError as error:
                self._json(HTTPStatus.BAD_GATEWAY, {"error": str(error), "status": error.status})
            except Exception:
                LOG.exception("Unexpected error")
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal error"})

        def do_GET(self) -> None:
            self._guard(self._get)

        def do_POST(self) -> None:
            self._guard(self._post)

        def do_DELETE(self) -> None:
            self._guard(self._delete)

        def _get(self) -> None:
            if not self.path.startswith("/api/"):
                asset = static_file(self.path)
                if asset is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                else:
                    self._send(HTTPStatus.OK, asset[0], asset[1])
                return
            if not self._authorised():
                return
            if self.path == "/api/devices":
                self._json(HTTPStatus.OK, {"devices": manager.devices()})
            elif self.path == "/api/discover":
                self._json(HTTPStatus.OK, {"found": manager.discovered()})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def _post(self) -> None:
            if not self._authorised():
                return
            body = self._body()
            if self.path == "/api/fingerprint":
                self._json(HTTPStatus.OK, manager.fingerprint(body.get("host"), body.get("port")))
            elif self.path == "/api/enroll":
                self._json(HTTPStatus.CREATED, manager.enroll(body))
            elif self.path == "/api/update-all":
                self._json(HTTPStatus.OK, {"results": manager.update_all()})
            else:
                match = re.fullmatch(r"/api/devices/([0-9a-f]{12})/([a-z-]+)", self.path)
                if not match or match.group(2) not in ACTIONS:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                self._json(HTTPStatus.OK, manager.action(match.group(1), match.group(2), body))

        def _delete(self) -> None:
            if not self._authorised():
                return
            match = re.fullmatch(r"/api/devices/([0-9a-f]{12})", self.path)
            if not match:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            manager.forget(match.group(1))
            self._json(HTTPStatus.OK, {"ok": True})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(prog="webdisplay_manager", description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--store", type=Path, help="devices file (default ~/.config/web-display-manager/devices.json)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    token = secrets.token_urlsafe(24)
    holder: dict[str, int] = {}
    manager = Manager(DeviceStore(args.store))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(manager, token, lambda: holder["port"]))
    holder["port"] = server.server_port
    url = f"http://127.0.0.1:{server.server_port}/#token={token}"
    print(f"Web Display Manager: {url}")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
