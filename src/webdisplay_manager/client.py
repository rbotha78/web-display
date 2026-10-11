"""HTTPS client for one device, pinned to the device's certificate fingerprint."""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import ssl
from http.cookies import SimpleCookie

DEFAULT_PORT = 8443
FINGERPRINT_CHARS = set("0123456789ABCDEF")


class DeviceError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class Unreachable(DeviceError):
    """The address did not answer; another address of the same device may."""


class CertificateMismatch(DeviceError):
    def __init__(self) -> None:
        super().__init__("The device certificate changed; it must be trusted again", 495)


def format_fingerprint(der: bytes) -> str:
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def normalise_fingerprint(value: str) -> str:
    cleaned = value.replace(":", "").replace(" ", "").upper()
    if len(cleaned) != 64 or not set(cleaned) <= FINGERPRINT_CHARS:
        raise ValueError("A SHA-256 fingerprint has 64 hexadecimal digits")
    return ":".join(cleaned[i : i + 2] for i in range(0, 64, 2))


def insecure_context() -> ssl.SSLContext:
    # Trust comes from the pinned fingerprint, not from a certificate authority.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def fetch_fingerprint(host: str, port: int = DEFAULT_PORT, timeout: float = 5) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with insecure_context().wrap_socket(raw, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as error:
        raise DeviceError(f"Cannot reach {host}:{port} ({error})") from None
    if not der:
        raise DeviceError("The device did not present a certificate")
    return format_fingerprint(der)


class PinnedConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, fingerprint: str, timeout: float):
        super().__init__(host, port, timeout=timeout, context=insecure_context())
        self.fingerprint = fingerprint

    def connect(self) -> None:
        super().connect()
        der = self.sock.getpeercert(binary_form=True)  # type: ignore[union-attr]
        if not der or format_fingerprint(der) != self.fingerprint:
            self.close()
            raise CertificateMismatch()


class DeviceClient:
    def __init__(self, host: str, port: int, fingerprint: str, key: str | None = None):
        self.host = host
        self.port = port
        self.fingerprint = fingerprint
        self.key = key

    def request(
        self,
        method: str,
        path: str,
        body: object | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 10,
    ) -> tuple[int, dict[str, object], dict[str, str]]:
        send = {"Accept": "application/json", **(headers or {})}
        if self.key:
            send["Authorization"] = f"Bearer {self.key}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            send["Content-Type"] = "application/json"
        connection = PinnedConnection(self.host, self.port, self.fingerprint, timeout)
        try:
            connection.request(method, path, body=data, headers=send)
            response = connection.getresponse()
            raw = response.read(1024 * 1024)
            reply_headers = {k.lower(): v for k, v in response.getheaders()}
            status = response.status
        except DeviceError:
            raise
        except (OSError, http.client.HTTPException) as error:
            raise Unreachable(f"Cannot reach {self.host}:{self.port} ({error})") from None
        finally:
            connection.close()
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}
        return status, payload if isinstance(payload, dict) else {}, reply_headers

    def call(self, method: str, path: str, body: object | None = None, timeout: float = 10) -> dict[str, object]:
        status, payload, _ = self.request(method, path, body, timeout=timeout)
        if status >= 400:
            message = str(payload.get("error") or f"Device returned HTTP {status}")
            raise DeviceError(message, 401 if status == 401 else 502)
        return payload


def enroll(host: str, port: int, fingerprint: str, password: str, name: str) -> str:
    """Log in once with the admin password, mint an API key, and forget the password."""
    client = DeviceClient(host, port, fingerprint)
    status, payload, headers = client.request("POST", "/api/login", {"password": password})
    if status == 401 or status == 429:
        raise DeviceError(str(payload.get("error") or "Login failed"), status)
    if status != 200 or "csrf" not in payload:
        raise DeviceError("Login failed")
    cookie = SimpleCookie(headers.get("set-cookie", ""))
    if "session" not in cookie:
        raise DeviceError("Login failed")
    session = {"Cookie": f"session={cookie['session'].value}", "X-CSRF-Token": str(payload["csrf"])}
    status, payload, _ = client.request("POST", "/api/keys", {"name": name}, session)
    if status != 201 or not isinstance(payload.get("key"), str):
        raise DeviceError(str(payload.get("error") or "Could not create an API key"))
    client.request("POST", "/api/logout", {}, session)
    return str(payload["key"])
