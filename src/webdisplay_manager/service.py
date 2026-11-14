"""Device operations used by the manager API: status, enrolment and actions."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import re
import secrets
import threading
from urllib.parse import urlsplit

from . import client as device_client
from .client import DeviceClient, DeviceError
from .discovery import discover
from .version import MIN_FIRMWARE, firmware_too_old
from .store import DeviceStore, addresses

HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?$")
SETTINGS_PATHS = {
    "GET": {
        "/api/config", "/api/audio/outputs", "/api/sleep", "/api/playlist", "/api/playlist/status",
        "/api/status", "/api/network", "/api/plex/config", "/api/plex/music", "/api/plex/stations",
        "/api/plex/browse", "/api/plex/music/browse", "/api/plex/music/filters",
        "/api/raildata/config", "/api/weather/config", "/api/update/status", "/api/debug-keys",
        "/api/music/sync", "/api/bluetooth",
    },
    "PUT": {
        "/api/config", "/api/sleep", "/api/playlist", "/api/plex/config", "/api/plex/music",
        "/api/raildata/config", "/api/weather/config", "/api/music/sync", "/api/bluetooth",
    },
    "POST": {
        "/api/playlist/control",
        "/api/sleep/wake", "/api/plex/music/control", "/api/plex/music/like",
        "/api/plex/music/playlists", "/api/plex/music/playlists/preview",
        "/api/hostname", "/api/wifi/scan", "/api/wifi/country", "/api/wifi/connect",
        "/api/wifi/forget", "/api/update/check", "/api/update/install", "/api/reboot", "/api/debug-keys",
        "/api/bluetooth/scan", "/api/bluetooth/pair", "/api/bluetooth/forget",
    },
}


def normalized_hostname(value: object) -> str:
    return str(value or "").strip().rstrip(".").lower().removesuffix(".local")


def matches_discovered(entry: dict[str, object], found: dict[str, object]) -> bool:
    host, name = normalized_hostname(found["host"]), normalized_hostname(found["name"])
    hostname = normalized_hostname(entry.get("hostname"))
    return any(
        port == found["port"] and (
            normalized_hostname(address) in (host, name) or (bool(hostname) and hostname == name)
        )
        for address, port in addresses(entry)
    )


def validate_host(value: object) -> str:
    if not isinstance(value, str) or not HOST_RE.match(value.strip()):
        raise ValueError("Enter a hostname or IP address")
    return value.strip()


def validate_port(value: object) -> int:
    port = device_client.DEFAULT_PORT if value in (None, "") else value
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("Port must be 1-65535")
    return port


def validate_name(value: object, default: str) -> str:
    name = default if value in (None, "") else value
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9 ._@-]{1,40}", name.strip()):
        raise ValueError("Name must be 1-40 letters, digits, spaces or . _ @ -")
    return name.strip()


class Manager:
    def __init__(self, store: DeviceStore, discover_fn=discover):
        self.store = store
        self.discover_fn = discover_fn
        self._sync_states: dict[str, dict[str, object]] = {}
        self._sync_lock = threading.RLock()

    def connect(self, identifier: str) -> tuple[DeviceClient, dict[str, object]]:
        """Return a client for the first known address that answers, and its /health reply."""
        entry = self.store.get(identifier)
        if entry is None:
            raise LookupError("Unknown device")
        failure: DeviceError | None = None
        for host, port in addresses(entry):
            device = DeviceClient(host, port, str(entry["fingerprint"]), str(entry["key"]))
            try:
                health = device.call("GET", "/health", timeout=5)
            except device_client.CertificateMismatch as error:
                failure = error
                continue
            except device_client.Unreachable as error:
                failure = failure or error
                continue
            if (host, port) != (str(entry["host"]), int(entry["port"])):  # type: ignore[arg-type]
                self.store.promote(identifier, host, port)
            return device, health
        raise failure or DeviceError("No known address")

    def client(self, identifier: str) -> DeviceClient:
        return self.connect(identifier)[0]

    def learn_addresses(self, identifier: str, device: DeviceClient, status: dict[str, object]) -> None:
        found: list[str] = []
        for value in status.get("addresses") or []:  # type: ignore[union-attr]
            try:
                address = ipaddress.ip_address(str(value))
            except ValueError:
                continue
            if address.version == 4 and not (address.is_loopback or address.is_link_local):
                found.append(str(address))
        hostname = status.get("hostname")
        self.store.learn(identifier, found, device.port, hostname if isinstance(hostname, str) else None)

    def summary(self, identifier: str, entry: dict[str, object]) -> dict[str, object]:
        result: dict[str, object] = {
            "id": identifier,
            "name": entry["name"],
            "host": entry["host"],
            "port": entry["port"],
            "online": False,
        }
        try:
            device, health = self.connect(identifier)
            result["host"], result["port"] = device.host, device.port
            result["version"] = health.get("version")
            if firmware_too_old(health.get("version")):
                result["firmware_outdated"] = True
                result["min_firmware"] = MIN_FIRMWARE
            result["online"] = True
            status = device.call("GET", "/api/status")
            result["hostname"] = status.get("hostname")
            self.learn_addresses(identifier, device, status)
            try:
                result["music_sync"] = self._read_sync(identifier, device)
            except DeviceError:
                result["music_sync"] = None
            result["addresses"] = [a for a, _ in addresses(self.store.get(identifier) or entry)]
            result["url"] = device.call("GET", "/api/config").get("url")
            network = device.call("GET", "/api/network")
            internet = network.get("internet") or {}
            wifi = network.get("wifi") or {}
            result["internet"] = bool(internet.get("online")) if isinstance(internet, dict) else None
            result["default_route"] = network.get("default_route")
            devices = wifi.get("devices") if isinstance(wifi, dict) else None
            result["wifi_connected"] = any(
                d.get("type") == "wifi" and d.get("state") == "connected"
                for d in devices if isinstance(d, dict)
            ) if isinstance(devices, list) else None
            ssid = wifi.get("saved_ssid") if isinstance(wifi, dict) else None
            result["wifi_ssid"] = ssid if isinstance(ssid, str) else None
            update = device.call("GET", "/api/update/status")
            result["installed_version"] = update.get("installed_version")
            result["restart_needed"] = update.get("installed_version") != update.get("running_version")
        except device_client.CertificateMismatch as error:
            result["error"] = str(error)
            result["certificate_changed"] = True
        except DeviceError as error:
            result["error"] = str(error)
            if error.status == 401:
                result["key_rejected"] = True
        with self._sync_lock:
            if not result.get("music_sync") and identifier in self._sync_states:
                result["music_sync_last_known"] = self._sync_states[identifier]
        return result

    def devices(self) -> list[dict[str, object]]:
        entries = self.store.all()
        if not entries:
            return []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {i: pool.submit(self.summary, i, e) for i, e in entries.items()}
            summaries = [future.result() for future in futures.values()]
        return sorted(summaries, key=lambda d: str(d["name"]).lower())

    def discovered(self) -> list[dict[str, object]]:
        raw = self.discover_fn()
        entries = self.store.all()
        for item in raw:
            for identifier, entry in entries.items():
                if matches_discovered(entry, item):
                    self.store.learn(identifier, [str(item["host"])], int(item["port"]), None)
                    break
        unique: dict[object, list[dict[str, object]]] = {}
        for item in raw:
            unique.setdefault((normalized_hostname(item["name"]), item["port"]), []).append(item)
        return [
            {**items[0], "enrolled": any(
                matches_discovered(entry, item) for entry in entries.values() for item in items
            )}
            for items in unique.values()
        ]

    def fingerprint(self, host: object, port: object) -> dict[str, object]:
        host, port = validate_host(host), validate_port(port)
        return {"host": host, "port": port, "fingerprint": device_client.fetch_fingerprint(host, port)}

    def enroll(self, body: dict[str, object]) -> dict[str, object]:
        host, port = validate_host(body.get("host")), validate_port(body.get("port"))
        confirmed = device_client.normalise_fingerprint(str(body.get("fingerprint", "")))
        password = body.get("password")
        if not isinstance(password, str) or not password:
            raise ValueError("Enter the device's administrator password")
        name = validate_name(body.get("name"), host)
        version = DeviceClient(host, port, confirmed).call("GET", "/health", timeout=10).get("version")
        if firmware_too_old(version):
            raise DeviceError(
                f"This device runs firmware {version}; the manager needs {MIN_FIRMWARE} or newer. Update the device first.", 409
            )
        key = device_client.enroll(host, port, confirmed, password, "Web Display Manager")
        return {"id": self.store.save(host, port, confirmed, key, name)}

    def forget(self, identifier: str) -> None:
        if not self.store.remove(identifier):
            raise LookupError("Unknown device")
        with self._sync_lock:
            self._sync_states.pop(identifier, None)

    @staticmethod
    def _public_sync(payload: dict[str, object]) -> dict[str, object]:
        # Keep only the public contract, even if firmware echoes private PUT fields.
        result = {key: payload[key] for key in (
            "supported", "role", "group_id", "connected", "error",
        ) if key in payload}
        leader = payload.get("leader")
        result["leader"] = {key: leader[key] for key in (
            "name", "hostname", "port", "addresses",
        ) if key in leader} if isinstance(leader, dict) else None
        members = payload.get("members")
        result["members"] = [{key: member[key] for key in (
            "name", "listening", "last_seen",
        ) if key in member} for member in members if isinstance(member, dict)] if isinstance(members, list) else []
        return result

    def _read_sync(self, identifier: str, device: DeviceClient) -> dict[str, object]:
        with self._sync_lock:
            status, payload, _ = device.request("GET", "/api/music/sync")
            if status == 404 or (status < 400 and payload.get("supported") is False):
                state = {"supported": False}
            elif status >= 400:
                raise DeviceError(str(payload.get("error") or "Could not read music sync state"), status)
            else:
                state = self._public_sync(payload)
            self._sync_states[identifier] = state
            return state

    def _set_sync(self, identifier: str, body: dict[str, object], token: str = "") -> dict[str, object]:
        entry = self.store.get(identifier)
        if entry is None:
            raise LookupError("Unknown device")
        result: dict[str, object] = {"id": identifier, "name": entry["name"]}
        try:
            status, payload, _ = self.client(identifier).request("PUT", "/api/music/sync", body)
            if status >= 400:
                raise DeviceError(str(payload.get("error") or f"Device returned HTTP {status}"), status)
            state = self._public_sync(payload)
            # Some firmware errors echo input; never retain the shared capability.
            if token:
                state = self._redact_sync(state, token)
            self._sync_states[identifier] = state
            result["ok"] = True
        except DeviceError as error:
            result.update(ok=False, error=str(error).replace(token, "[redacted]") if token else str(error))
        return result

    @staticmethod
    def _redact_sync(value, token: str):
        if isinstance(value, dict):
            return {key: Manager._redact_sync(item, token) for key, item in value.items()}
        if isinstance(value, list):
            return [Manager._redact_sync(item, token) for item in value]
        return value.replace(token, "[redacted]") if isinstance(value, str) else value

    def create_sync_group(self, leader: object, followers: object) -> dict[str, object]:
        if (not isinstance(leader, str) or not isinstance(followers, list)
                or not followers or any(not isinstance(item, str) for item in followers)):
            raise ValueError("Choose a leader and at least one follower")
        if leader in followers or len(set(followers)) != len(followers):
            raise ValueError("Leader and followers must be different devices, without duplicates")
        with self._sync_lock:
            identifiers = [leader, *followers]
            if any(self.store.get(identifier) is None for identifier in identifiers):
                raise ValueError("All group members must be enrolled devices")
            entry = self.store.get(leader)
            assert entry is not None
            primary = str(entry["host"])
            hosts = list(dict.fromkeys([primary, *(host for host, _ in addresses(entry))]))[:8]
            leader_info = {
                "name": entry["name"], "hostname": entry.get("hostname") or "",
                "port": entry["port"], "addresses": hosts,
                "fingerprint": device_client.normalise_fingerprint(str(entry["fingerprint"])),
            }
            group_id, token = secrets.token_urlsafe(16), secrets.token_urlsafe(32)
            results = [self._set_sync(leader, {"role": "leader", "group_id": group_id, "token": token}, token)]
            if not results[0]["ok"]:
                results.extend({"id": identifier, "name": self.store.get(identifier)["name"],
                                "ok": False, "error": "Not attempted: leader configuration failed"}
                               for identifier in followers)
            else:
                results.extend(self._set_sync(identifier, {
                    "role": "follower", "group_id": group_id, "token": token, "leader": leader_info,
                }, token) for identifier in followers)
            return {"group_id": group_id, "results": results}

    def dissolve_sync_group(self, group_id: object) -> dict[str, object]:
        if not isinstance(group_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", group_id):
            raise ValueError("Invalid music sync group ID")
        with self._sync_lock:
            members = [(identifier, state) for identifier, state in self._sync_states.items()
                       if state.get("group_id") == group_id and self.store.get(identifier) is not None]
            if not members:
                raise LookupError("Unknown music sync group; refresh devices first")
            members.sort(key=lambda item: item[1].get("role") == "leader")
            return {"results": [self._set_sync(identifier, {"role": "off"}) for identifier, _ in members]}

    def leave_sync_group(self, identifier: str) -> dict[str, object]:
        with self._sync_lock:
            if self.store.get(identifier) is None:
                raise LookupError("Unknown device")
            state = self._sync_states.get(identifier, {})
            if state.get("role") == "leader":
                return self.dissolve_sync_group(state.get("group_id"))
            if state.get("role") != "follower":
                raise ValueError("Device is not a known follower; refresh devices first")
            return {"results": [self._set_sync(identifier, {"role": "off"})]}

    def action(self, identifier: str, action: str, body: dict[str, object]) -> dict[str, object]:
        device = self.client(identifier)
        if action == "admin-login":
            status, payload, _ = device.request("POST", "/api/admin-login-token", {})
            if status == 404:
                raise DeviceError("Automatic login needs newer device software. Open the admin page manually or update the device.")
            if status != 201:
                raise DeviceError(str(payload.get("error") or "Could not create an admin login link"))
            token = payload.get("token")
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                raise DeviceError("The device returned an invalid admin login link")
            host = f"[{device.host}]" if ":" in device.host else device.host
            return {"url": f"https://{host}:{device.port}/#admin-login={token}"}
        if action == "reboot":
            return device.call("POST", "/api/reboot", {})
        if action == "url":
            url = body.get("url")
            if not isinstance(url, str):
                raise ValueError("url must be a string")
            return device.call("PUT", "/api/config", {"url": url})
        if action == "update-check":
            return device.call("POST", "/api/update/check", {}, timeout=30)
        if action == "update-install":
            return device.call("POST", "/api/update/install", {}, timeout=120)
        raise LookupError("Unknown action")

    def debug_keys(self, identifier: str, operation: str, body: dict[str, object], key_id: str = "") -> dict[str, object]:
        device = self.client(identifier)
        if operation == "list":
            return device.call("GET", "/api/debug-keys")
        if operation == "add":
            payload = {"key": body.get("key")}
            if body.get("name"):
                payload["name"] = body["name"]
            return device.call("POST", "/api/debug-keys", payload, timeout=60)
        if operation == "remove":
            return device.call("DELETE", "/api/debug-keys/" + key_id, timeout=60)
        raise LookupError("Unknown action")

    def settings(self, identifier: str, value: dict[str, object]) -> dict[str, object]:
        if set(value) - {"method", "path", "body"}:
            raise ValueError("Invalid configuration request")
        method, path, body = value.get("method"), value.get("path"), value.get("body")
        if (not isinstance(method, str) or not isinstance(path, str) or len(path) > 4096
                or any(ord(char) < 32 or ord(char) == 127 for char in path)):
            raise ValueError("Invalid configuration request")
        parsed = urlsplit(path)
        allowed = parsed.path in SETTINGS_PATHS.get(method, set()) or (
            method == "DELETE" and re.fullmatch(r"/api/debug-keys/[0-9a-f]{12}", parsed.path)
        )
        if (not allowed or parsed.scheme or parsed.netloc or parsed.fragment
                or (parsed.query and (method != "GET" or parsed.path not in {
                    "/api/plex/browse", "/api/plex/music/browse", "/api/plex/music/filters",
                }))):
            raise ValueError("This operation is not available in manager configuration")
        if (method == "GET" and body is not None) or (method != "GET" and not isinstance(body, dict)):
            raise ValueError("Invalid configuration body")
        status, payload, headers = self.client(identifier).request(method, path, body, timeout=120)
        if not 200 <= status < 300:
            raise DeviceError(str(payload.get("error") or f"Device returned HTTP {status}"), status)
        if "application/json" not in headers.get("content-type", ""):
            raise DeviceError("The device returned an invalid configuration response", 502)
        return payload

    def remote_view(self, identifier: str) -> dict[str, object]:
        status, payload, _ = self.client(identifier).request("GET", "/api/remote-view")
        if status == 404:
            raise DeviceError("Remote preview needs newer device firmware. Update this device first.", 404)
        if status != 200:
            raise DeviceError(str(payload.get("error") or "Remote preview is unavailable"), status)
        if payload.get("available") is not True:
            raise DeviceError("The device returned an invalid remote preview", 502)
        return payload

    def update_all(self) -> list[dict[str, object]]:
        def one(identifier: str, entry: dict[str, object]) -> dict[str, object]:
            result: dict[str, object] = {"id": identifier, "name": entry["name"]}
            try:
                device = self.client(identifier)
                check = device.call("POST", "/api/update/check", {}, timeout=30)
                if not check.get("available"):
                    result["outcome"] = "up to date"
                else:
                    installed = device.call("POST", "/api/update/install", {}, timeout=120)
                    result["outcome"] = "installed"
                    result["version"] = installed.get("version")
            except DeviceError as error:
                result["outcome"] = "failed"
                result["error"] = str(error)
            return result

        entries = self.store.all()
        if not entries:
            return []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            return [f.result() for f in [pool.submit(one, i, e) for i, e in entries.items()]]
