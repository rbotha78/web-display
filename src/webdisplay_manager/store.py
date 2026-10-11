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
            devices[identifier] = {
                "host": host,
                "port": port,
                "fingerprint": fingerprint,
                "key": api_key,
                "name": name,
            }
            self._write(devices)
        return identifier

    def update_host(self, identifier: str, host: str, port: int) -> None:
        with self._lock:
            devices = self._read()
            if identifier in devices:
                devices[identifier].update(host=host, port=port)
                self._write(devices)

    def remove(self, identifier: str) -> bool:
        with self._lock:
            devices = self._read()
            if identifier not in devices:
                return False
            del devices[identifier]
            self._write(devices)
            return True
