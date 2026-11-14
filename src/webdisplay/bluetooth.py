"""Bluetooth speaker support.

``python3 -m webdisplay.bluetooth`` is the privileged helper (root via sudo, one JSON
request on stdin). ``python3 -m webdisplay.bluetooth supervise`` is the root reconnect
supervisor. The kiosk only reads the published desired/status files.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LOG = logging.getLogger(__name__)
RUN_DIR = Path("/run/web-display")
DESIRED_PATH = RUN_DIR / "bluetooth.json"
STATUS_PATH = RUN_DIR / "bluetooth-status.json"
LOCK_PATH = RUN_DIR / "bluetooth.lock"
SYSFS_BLUETOOTH = Path("/sys/class/bluetooth")
SYSFS_RFKILL = Path("/sys/class/rfkill")
BLUETOOTHCTL = "/usr/bin/bluetoothctl"
BLUEALSA_CLI = "/usr/bin/bluealsa-cli"
ADDRESS_PATTERN = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")
OUTPUT_PATTERN = re.compile(r"^alsa/bluealsa:DEV=((?:[0-9A-F]{2}:){5}[0-9A-F]{2}),PROFILE=a2dp$")
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|[\x01\x02\r]")
MAX_DELAY_MS = 1000
STATUS_MAX_AGE = 30
TICK_SECONDS = 5
AUDIO_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"


def validate_address(value: object) -> str:
    if not isinstance(value, str) or not ADDRESS_PATTERN.fullmatch(value.upper()):
        raise ValueError("Invalid Bluetooth address")
    return value.upper()


def validate_delay(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not -MAX_DELAY_MS <= value <= MAX_DELAY_MS:
        raise ValueError(f"Bluetooth delay must be between -{MAX_DELAY_MS} and {MAX_DELAY_MS} ms")
    return value


def validate_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid Bluetooth device name")
    return "".join(c for c in value if c.isprintable()).strip()[:80]


def validate_bluetooth_config(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Invalid Bluetooth settings")
    address = validate_address(value["address"]) if value.get("address") else ""
    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("Bluetooth enabled must be true or false")
    return {
        "address": address,
        "name": validate_name(value.get("name", "")) if address else "",
        "enabled": enabled and bool(address),
        "delay_ms": validate_delay(value.get("delay_ms", 0)),
    }


def output_for(address: str) -> str:
    return f"alsa/bluealsa:DEV={validate_address(address)},PROFILE=a2dp"


def is_bluetooth_output(output: object) -> bool:
    return isinstance(output, str) and OUTPUT_PATTERN.fullmatch(output) is not None


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def connected_address(status_path: Path = STATUS_PATH, now: float | None = None) -> str:
    """The speaker the supervisor recently confirmed as connected with an audio PCM, or ''."""
    status = _read_json(status_path) or {}
    updated = status.get("updated")
    now = time.time() if now is None else now
    if (status.get("connected") is not True or isinstance(updated, bool)
            or not isinstance(updated, (int, float)) or abs(now - updated) > STATUS_MAX_AGE):
        return ""
    try:
        return validate_address(status.get("address"))
    except ValueError:
        return ""


def active_output(wired: str, desired_path: Path = DESIRED_PATH, status_path: Path = STATUS_PATH,
                  now: float | None = None) -> tuple[str, float]:
    """Return (mpv audio device, extra output delay in seconds), falling back to the wired output."""
    desired = _read_json(desired_path) or {}
    try:
        settings = validate_bluetooth_config(desired)
    except (KeyError, ValueError):
        return wired, 0.0
    if settings["enabled"] and connected_address(status_path, now) == settings["address"]:
        return output_for(str(settings["address"])), int(settings["delay_ms"]) / 1000  # type: ignore[arg-type]
    return wired, 0.0


def output_available(output: str, status_path: Path = STATUS_PATH) -> bool:
    match = OUTPUT_PATTERN.fullmatch(output)
    return match is not None and connected_address(status_path) == match[1]


# ----- Privileged operations (root) ------------------------------------------------------

def _clean(text: str) -> str:
    return ANSI_PATTERN.sub("", text)


def run_ctl(*args: str, timeout: float = 15, agent: bool = False) -> str:
    command = [BLUETOOTHCTL, *(["--agent", "NoInputNoOutput"] if agent else []), *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    return _clean(result.stdout + result.stderr)


def parse_info(text: str) -> dict[str, object] | None:
    fields: dict[str, str] = {}
    uuids: list[str] = []
    found = False
    for line in text.splitlines():
        if re.search(r"(?:^|\s)Device (?:[0-9A-F]{2}:){5}[0-9A-F]{2} \(", line):
            found = True
            continue
        match = re.match(r"^\s+([A-Za-z]+):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        if key == "UUID":
            uuids.append(value.lower())
        else:
            fields.setdefault(key, value.strip())
    if not found:
        return None
    icon = fields.get("Icon", "")
    return {
        "name": validate_name(fields.get("Alias") or fields.get("Name") or ""),
        "paired": fields.get("Paired") == "yes",
        "trusted": fields.get("Trusted") == "yes",
        "connected": fields.get("Connected") == "yes",
        "audio": icon.startswith("audio") or any(AUDIO_SINK_UUID in uuid for uuid in uuids),
    }


def device_info(address: str) -> dict[str, object] | None:
    return parse_info(run_ctl("info", validate_address(address)))


def parse_devices(text: str) -> list[tuple[str, str]]:
    devices = []
    for line in text.splitlines():
        match = re.match(r"^\s*Device ((?:[0-9A-F]{2}:){5}[0-9A-F]{2}) ?(.*)$", line)
        if match:
            devices.append((match[1], match[2].strip()))
    return devices


def adapter_present(root: Path = SYSFS_BLUETOOTH) -> bool:
    try:
        return any(entry.name.startswith("hci") for entry in root.iterdir())
    except OSError:
        return False


def unblock(root: Path = SYSFS_RFKILL) -> None:
    """Clear the soft rfkill block that leaves fresh Raspberry Pi OS radios powered off."""
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if (entry / "type").read_text().strip() == "bluetooth" and (entry / "soft").read_text().strip() != "0":
                (entry / "soft").write_text("0")
        except OSError:
            LOG.warning("Could not clear Bluetooth rfkill block on %s", entry.name)


def prepare() -> None:
    if not adapter_present():
        raise ValueError("No Bluetooth adapter is available on this display")
    unblock()
    if "succeeded" not in run_ctl("power", "on") and "Powered: yes" not in run_ctl("show"):
        time.sleep(1)
        if "Powered: yes" not in run_ctl("show"):
            raise ValueError("Could not power on the Bluetooth adapter")


def pcm_present(address: str) -> bool:
    try:
        result = subprocess.run([BLUEALSA_CLI, "list-pcms"], capture_output=True, text=True,
                                timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    device = "dev_" + validate_address(address).replace(":", "_")
    return any(device in line and "/a2dpsrc/" in line for line in result.stdout.splitlines())


class Discovery:
    """Keep classic (BR/EDR) discovery running so found devices stay addressable."""

    def __init__(self):
        self.process = subprocess.Popen([BLUETOOTHCTL], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, text=True)
        self._send("scan bredr")

    def _send(self, command: str) -> None:
        if self.process.stdin is not None:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()

    def close(self) -> None:
        try:
            self._send("scan off")
            self._send("quit")
            self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()

    def __enter__(self) -> Discovery:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def scan(seconds: float = 10) -> list[dict[str, object]]:
    prepare()
    with Discovery():
        time.sleep(seconds)
        items = []
        for address, _ in parse_devices(run_ctl("devices")):
            info = device_info(address)
            if info and info["name"] and info["name"] != address.replace(":", "-"):
                items.append({"address": address, **info})
    items.sort(key=lambda item: (not item["audio"], not item["paired"], str(item["name"]).lower()))
    return items[:50]


def connect(address: str, timeout: float = 25) -> bool:
    address = validate_address(address)
    run_ctl("connect", address, timeout=timeout)
    info = device_info(address)
    return bool(info and info["connected"])


def pair(address: str) -> dict[str, object]:
    address = validate_address(address)
    prepare()
    info = device_info(address)
    if not info or not info["paired"]:
        with Discovery():
            deadline = time.monotonic() + 20
            while (info is None) and time.monotonic() < deadline:
                time.sleep(1)
                info = device_info(address)
            if info is None:
                raise ValueError("Speaker not found; put it in pairing mode and try again")
            run_ctl("pair", address, timeout=40, agent=True)
        info = device_info(address)
        if not info or not info["paired"]:
            raise ValueError("Pairing failed; put the speaker in pairing mode and try again")
    run_ctl("trust", address)
    if not connect(address):
        raise ValueError("Paired, but could not connect to the speaker")
    return {"address": address, **(device_info(address) or info)}


def handle(request: dict[str, object]) -> dict[str, object]:
    action = request.get("action")
    if action == "scan":
        return {"items": scan()}
    if action == "pair":
        return {"device": pair(str(request.get("address", "")))}
    address = validate_address(request.get("address"))
    if action == "connect":
        prepare()
        if not connect(address):
            raise ValueError("Could not connect to the speaker")
        return {"ok": True}
    if action == "disconnect":
        run_ctl("disconnect", address)
        return {"ok": True}
    if action == "remove":
        run_ctl("remove", address)
        return {"ok": True}
    raise ValueError("Unknown Bluetooth action")


def write_status(status: dict[str, object], path: Path | None = None) -> None:
    path = path or STATUS_PATH
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".bt-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(status, handle)
            handle.write("\n")
        os.chmod(temporary, 0o640)
        try:
            import grp

            os.chown(temporary, 0, grp.getgrnam("web-display").gr_gid)
        except (KeyError, PermissionError):
            pass
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Supervisor:
    """Reconnect the chosen speaker and publish whether its audio PCM is usable."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.next_attempt = 0.0
        self.failures = 0
        self.target = ""

    def tick(self) -> dict[str, object]:
        status: dict[str, object] = {"available": adapter_present(), "address": "", "connected": False}
        try:
            desired = validate_bluetooth_config(_read_json(DESIRED_PATH) or {})
        except (KeyError, ValueError):
            desired = validate_bluetooth_config({})
        address = str(desired["address"]) if desired["enabled"] else ""
        if address != self.target:
            self.target, self.next_attempt, self.failures = address, 0.0, 0
        if address and status["available"]:
            status["address"] = address
            with LOCK_PATH.open("a+") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return status  # The helper is pairing or scanning; skip this tick.
                info = device_info(address)
                if info and info["paired"] and not info["connected"] and self.clock() >= self.next_attempt:
                    prepare()
                    if connect(address):
                        self.failures = 0
                    else:
                        self.failures += 1
                        # Paging an absent speaker occupies the shared radio; back off to a minute.
                        self.next_attempt = self.clock() + min(60, 15 * self.failures)
                    info = device_info(address)
                status["connected"] = bool(info and info["connected"] and pcm_present(address))
        status["updated"] = time.time()
        write_status(status)
        return status


def supervise() -> int:
    supervisor = Supervisor()
    while True:
        try:
            supervisor.tick()
        except Exception:
            LOG.exception("Bluetooth supervision tick failed")
        time.sleep(TICK_SECONDS)


def helper() -> int:
    try:
        request = json.loads(sys.stdin.read(4096) or "{}")
        if not isinstance(request, dict):
            raise ValueError("Invalid request")
        with LOCK_PATH.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            payload = handle(request)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, ValueError) else "Bluetooth operation failed"
        print(json.dumps({"error": message}))
        return 1
    print(json.dumps(payload))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if sys.argv[1:] == ["supervise"]:
        return supervise()
    if sys.argv[1:]:
        print(json.dumps({"error": "Invalid arguments"}))
        return 2
    return helper()


if __name__ == "__main__":
    sys.exit(main())
