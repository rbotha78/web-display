"""Persistent list of enrolled devices (API keys and pinned fingerprints), mode 0600."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path


def default_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "web-display-manager" / "devices.json"


MAX_ADDRESSES = 8


def addresses(entry: dict[str, object]) -> list[tuple[str, int]]:
    """Known (host, port) pairs for a device, most recently working first."""
    result: list[tuple[str, int]] = []
    for item in entry.get("addresses") or []:
        if isinstance(item, dict) and isinstance(item.get("host"), str) and isinstance(item.get("port"), int):
            result.append((item["host"], item["port"]))
    primary = (str(entry["host"]), int(entry["port"]))  # type: ignore[arg-type]
    if primary not in result:
        result.insert(0, primary)
    return result


def device_id(fingerprint: str) -> str:
    return re.sub(r"[^0-9A-F]", "", fingerprint)[:12].lower()


class DeviceStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict[str, object]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        devices = data.get("devices") if isinstance(data, dict) else None
        return devices if isinstance(devices, dict) else {}

    def _write(self, devices: dict[str, dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".devices-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"devices": devices}, stream, indent=1, sort_keys=True)
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def all(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return self._read()

    def get(self, key: str) -> dict[str, object] | None:
        return self.all().get(key)

    def save(self, host: str, port: int, fingerprint: str, api_key: str, name: str) -> str:
        identifier = device_id(fingerprint)
        with self._lock:
            devices = self._read()
            known = [(a, p) for a, p in addresses(devices[identifier]) if (a, p) != (host, port)] if identifier in devices else []
            devices[identifier] = {
                "host": host,
                "port": port,
                "fingerprint": fingerprint,
                "key": api_key,
                "name": name,
                "addresses": [{"host": a, "port": p} for a, p in [(host, port), *known][:MAX_ADDRESSES]],
            }
            self._write(devices)
        return identifier

    def promote(self, identifier: str, host: str, port: int) -> None:
        """Make (host, port) the first address to try."""
        with self._lock:
            devices = self._read()
            entry = devices.get(identifier)
            if entry is None:
                return
            rest = [(a, p) for a, p in addresses(entry) if (a, p) != (host, port)]
            entry.update(host=host, port=port)
            entry["addresses"] = [{"host": a, "port": p} for a, p in [(host, port), *rest][:MAX_ADDRESSES]]
            self._write(devices)

    def learn(self, identifier: str, hosts: list[str], port: int, hostname: str | None) -> None:
        """Remember extra addresses and the hostname the device itself reported."""
        with self._lock:
            devices = self._read()
            entry = devices.get(identifier)
            if entry is None:
                return
            known = addresses(entry)
            merged = list(known)
            for host in hosts:
                if (host, port) not in merged and len(merged) < MAX_ADDRESSES:
                    merged.append((host, port))
            if merged == known and entry.get("hostname") == hostname:
                return
            entry["addresses"] = [{"host": a, "port": p} for a, p in merged]
            if hostname:
                entry["hostname"] = hostname
            self._write(devices)

    def remove(self, identifier: str) -> bool:
        with self._lock:
            devices = self._read()
            if identifier not in devices:
                return False
            del devices[identifier]
            self._write(devices)
            return True
