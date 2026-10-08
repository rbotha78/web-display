"""Privileged Wi-Fi operations. Runs as root via sudo; reads one JSON request on stdin."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CONNECTION_ID = "web-display-wifi"
KEYFILE = Path("/etc/NetworkManager/system-connections") / f"{CONNECTION_ID}.nmconnection"
LOCK_PATH = Path("/run/web-display/network.lock")
COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")
MODPROBE_CONFIG = Path("/etc/modprobe.d/web-display-regdom.conf")


def validate_country(value: object) -> str:
    if not isinstance(value, str) or not COUNTRY_PATTERN.fullmatch(value):
        raise ValueError("Country must be a two-letter ISO code such as GB")
    return value


def validate_ssid(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("SSID is required")
    if len(value.encode("utf-8")) > 32:
        raise ValueError("SSID must be at most 32 bytes")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("SSID contains control characters")
    return value


def validate_psk(value: object) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not 8 <= len(value) <= 63:
        raise ValueError("Wi-Fi password must be 8 to 63 characters")
    if any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError("Wi-Fi password must be printable ASCII")
    return value


def render_keyfile(ssid: str, psk: str, interface: str = "wlan0") -> str:
    lines = [
        "[connection]",
        f"id={CONNECTION_ID}",
        "type=wifi",
        f"interface-name={interface}",
        "autoconnect=true",
        "autoconnect-priority=0",
        "",
        "[wifi]",
        "mode=infrastructure",
        f"ssid={ssid}",
        "",
    ]
    if psk:
        lines += ["[wifi-security]", "key-mgmt=wpa-psk", f"psk={psk}", ""]
    lines += [
        "[ipv4]",
        "method=auto",
        "route-metric=600",
        "",
        "[ipv6]",
        "method=auto",
        "route-metric=600",
        "",
    ]
    return "\n".join(lines)


def run(command: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=False
    )


def wifi_device() -> str | None:
    result = run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"])
    for line in result.stdout.splitlines():
        device, _, kind = line.partition(":")
        if kind == "wifi":
            return device
    return None


def set_country(country: str) -> None:
    MODPROBE_CONFIG.write_text(
        f"options cfg80211 ieee80211_regdom={country}\n", encoding="ascii"
    )
    run(["iw", "reg", "set", country])
    run(["rfkill", "unblock", "wifi"])
    run(["nmcli", "radio", "wifi", "on"])


def current_country() -> str:
    result = run(["iw", "reg", "get"])
    match = re.search(r"country ([A-Z0-9]{2}):", result.stdout)
    return match.group(1) if match else ""


def scan() -> list[dict[str, object]]:
    run(["nmcli", "device", "wifi", "rescan"], timeout=15)
    result = run(["nmcli", "-t", "-e", "yes", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"])
    networks: dict[str, dict[str, object]] = {}
    for line in result.stdout.splitlines():
        fields = re.split(r"(?<!\\):", line)
        if len(fields) < 3:
            continue
        ssid = fields[0].replace("\\:", ":").replace("\\\\", "\\")
        if not ssid:
            continue
        signal = int(fields[1]) if fields[1].isdigit() else 0
        entry = {"ssid": ssid, "signal": signal, "secured": bool(fields[2].strip())}
        if ssid not in networks or signal > networks[ssid]["signal"]:  # type: ignore[operator]
            networks[ssid] = entry
    return sorted(networks.values(), key=lambda n: -int(n["signal"]))  # type: ignore[call-overload]


def status() -> dict[str, object]:
    devices = []
    result = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 4 and parts[1] in ("ethernet", "wifi"):
            devices.append(
                {"device": parts[0], "type": parts[1], "state": parts[2], "connection": parts[3]}
            )
    blocked = bool(re.search(r"(Soft|Hard) blocked: yes", run(["rfkill", "list", "wifi"]).stdout))
    return {
        "country": current_country(),
        "wifi_blocked": blocked,
        "devices": devices,
        "saved_network": KEYFILE.exists(),
    }


def connect(ssid: str, psk: str) -> None:
    device = wifi_device()
    if device is None:
        raise RuntimeError("No Wi-Fi adapter was found")
    previous = KEYFILE.read_bytes() if KEYFILE.exists() else None
    KEYFILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=KEYFILE.parent, prefix=".wd-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(render_keyfile(ssid, psk, device))
        os.chmod(temporary, 0o600)
        os.replace(temporary, KEYFILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    run(["nmcli", "connection", "reload"])
    result = run(["nmcli", "--wait", "30", "connection", "up", CONNECTION_ID, "ifname", device], 45)
    if result.returncode != 0:
        run(["nmcli", "connection", "delete", CONNECTION_ID])
        if previous is not None:
            KEYFILE.write_bytes(previous)
            os.chmod(KEYFILE, 0o600)
            run(["nmcli", "connection", "reload"])
            run(["nmcli", "connection", "up", CONNECTION_ID, "ifname", device], 45)
        raise RuntimeError("Could not connect to the Wi-Fi network")


def forget() -> None:
    run(["nmcli", "connection", "delete", CONNECTION_ID])
    KEYFILE.unlink(missing_ok=True)


def handle(request: dict[str, object]) -> dict[str, object]:
    operation = request.get("operation")
    if operation == "status":
        return status()
    if operation == "scan":
        return {"networks": scan()}
    if operation == "country":
        set_country(validate_country(request.get("country")))
        return {"country": current_country()}
    if operation == "connect":
        ssid = validate_ssid(request.get("ssid"))
        psk = validate_psk(request.get("password"))
        with LOCK_PATH.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            from .netmonitor import AP_INFO, Supervisor

            ap_was_active = AP_INFO.exists()
            try:
                connect(ssid, psk)
            except RuntimeError:
                if ap_was_active and not Supervisor().start_ap():
                    raise RuntimeError(
                        "Wi-Fi connection failed and the setup access point could not be restored"
                    ) from None
                raise
        return {"connected": True}
    if operation == "forget":
        with LOCK_PATH.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            forget()
        return {"forgotten": True}
    raise ValueError("unsupported operation")


def main() -> int:
    try:
        request = json.loads(sys.stdin.read(4096))
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        print(json.dumps(handle(request)))
        return 0
    except (ValueError, RuntimeError, subprocess.SubprocessError, OSError) as error:
        print(json.dumps({"error": str(error)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
