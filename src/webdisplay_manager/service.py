"""Device operations used by the manager API: status, enrolment and actions."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import re

from . import client as device_client
from .client import DeviceClient, DeviceError
from .discovery import discover
from .version import MIN_FIRMWARE, firmware_too_old
from .store import DeviceStore, addresses

HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?$")


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
        # Scan replies name each device by hostname; use them to learn extra addresses of enrolled devices.
        by_hostname = {str(e["hostname"]): i for i, e in self.store.all().items() if e.get("hostname")}
        for item in raw:
            identifier = by_hostname.get(str(item["name"]))
            if identifier:
                self.store.learn(identifier, [str(item["host"])], int(item["port"]), None)  # type: ignore[arg-type]
        entries = list(self.store.all().values())
        known = {(a, p) for e in entries for a, p in addresses(e)}
        hostnames = {str(e["hostname"]) for e in entries if e.get("hostname")}
        unique: dict[object, dict[str, object]] = {}
        for item in raw:
            unique.setdefault(item["name"], item)  # one row per device, not per interface
        return [
            {**d, "enrolled": (d["host"], d["port"]) in known or d["name"] in hostnames}
            for d in unique.values()
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

    def action(self, identifier: str, action: str, body: dict[str, object]) -> dict[str, object]:
        device = self.client(identifier)
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
