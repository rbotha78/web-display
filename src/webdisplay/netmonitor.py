"""Root network supervisor: raises a setup access point when there is no usable internet.

Policy:
- Internet is judged by TCP probes to several endpoints, never by link or association alone.
- Automatic AP start is opt-in (the "auto_ap" setting, disabled by default).
- The AP starts only after OUTAGE_GRACE seconds of continuous outage.
- Every AP session gets a new random password (credential rotation).
- While the AP is up, the saved Wi-Fi network is retried every RETRY_INTERVAL seconds;
  if the retry fails the AP is restored with a new password.
- The AP stops as soon as internet is reachable again.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .network import internet_status
from .wifi_helper import KEYFILE as CLIENT_KEYFILE
from .wifi_helper import current_country, run, wifi_device

LOG = logging.getLogger("web-display-network")
AP_ID = "web-display-setup"
AP_KEYFILE = Path("/run/NetworkManager/system-connections") / f"{AP_ID}.nmconnection"
AP_INFO = Path("/run/web-display/setup-ap.json")
LOCK_PATH = Path("/run/web-display/network.lock")
CONFIG_PATH = Path("/var/lib/web-display/config.json")
AP_ADDRESS = "10.42.0.1"
OUTAGE_GRACE = 120
RETRY_INTERVAL = 600
TICK_SECONDS = 10
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def new_password(length: int = 12) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def new_ssid() -> str:
    return f"WebDisplay-{secrets.token_hex(2).upper()}"


def render_ap_keyfile(ssid: str, password: str, interface: str) -> str:
    return "\n".join(
        [
            "[connection]",
            f"id={AP_ID}",
            "type=wifi",
            f"interface-name={interface}",
            "autoconnect=false",
            "",
            "[wifi]",
            "mode=ap",
            "band=bg",
            f"ssid={ssid}",
            "",
            "[wifi-security]",
            "key-mgmt=wpa-psk",
            "proto=rsn",
            "pairwise=ccmp",
            "group=ccmp",
            f"psk={password}",
            "",
            "[ipv4]",
            "method=shared",
            f"address1={AP_ADDRESS}/24",
            "",
            "[ipv6]",
            "method=ignore",
            "",
        ]
    )


def ap_capable() -> bool:
    result = run(["iw", "list"])
    modes = re.search(r"Supported interface modes:(.*?)(?:\n\t\S|\Z)", result.stdout, re.S)
    return bool(modes and re.search(r"\*\s+AP\s*$", modes.group(1), re.M))


def write_private(path: Path, content: str, mode: int, group: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".wd-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, mode)
        if group:
            import grp

            os.chown(temporary, 0, grp.getgrnam(group).gr_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def auto_ap_enabled() -> bool:
    try:
        with CONFIG_PATH.open(encoding="utf-8") as stream:
            return json.load(stream).get("auto_ap") is True
    except (OSError, ValueError, AttributeError):
        return False


class Supervisor:
    def __init__(self, clock=time.monotonic, auto_ap=auto_ap_enabled):
        self.clock = clock
        self.auto_ap = auto_ap
        self.offline_since: float | None = None
        self.ap_active = False
        self.last_retry = 0.0

    def start_ap(self) -> bool:
        device = wifi_device()
        if device is None or not ap_capable():
            LOG.warning("No AP-capable Wi-Fi adapter; cannot start setup AP")
            return False
        if not current_country() or current_country() == "00":
            LOG.warning("Wi-Fi country is not set; cannot start setup AP")
            return False
        run(["rfkill", "unblock", "wifi"])
        ssid, password = new_ssid(), new_password()
        run(["nmcli", "connection", "down", "web-display-wifi"])
        write_private(AP_KEYFILE, render_ap_keyfile(ssid, password, device), 0o600)
        run(["nmcli", "connection", "reload"])
        result = run(["nmcli", "--wait", "20", "connection", "up", AP_ID, "ifname", device], 30)
        if result.returncode != 0:
            LOG.error(
                "Setup AP failed to start: %s",
                result.stderr.strip() or "NetworkManager returned an error",
            )
            self.remove_ap_profile()
            self.restore_client()
            return False
        write_private(
            AP_INFO,
            json.dumps({"ssid": ssid, "password": password, "address": AP_ADDRESS}) + "\n",
            0o640,
            group="web-display",
        )
        self.ap_active = True
        self.last_retry = self.clock()
        LOG.info("Setup AP %s started", ssid)
        return True

    def remove_ap_profile(self) -> None:
        run(["nmcli", "connection", "down", AP_ID])
        run(["nmcli", "connection", "delete", AP_ID])
        AP_KEYFILE.unlink(missing_ok=True)
        AP_INFO.unlink(missing_ok=True)

    def stop_ap(self) -> None:
        self.remove_ap_profile()
        self.ap_active = False
        LOG.info("Setup AP stopped")

    def restore_client(self) -> None:
        if CLIENT_KEYFILE.exists():
            device = wifi_device() or ""
            if not device:
                LOG.warning("Saved Wi-Fi profile exists but no Wi-Fi adapter was found")
                return
            active = run(
                ["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"]
            )
            if "web-display-wifi" in active.stdout.splitlines():
                return
            result = run(
                [
                    "nmcli",
                    "--wait",
                    "20",
                    "connection",
                    "up",
                    "web-display-wifi",
                    "ifname",
                    device,
                ],
                30,
            )
            if result.returncode != 0:
                LOG.warning("Could not restore the saved Wi-Fi connection")

    def retry_client(self) -> bool:
        if not CLIENT_KEYFILE.exists():
            return False
        LOG.info("Retrying saved Wi-Fi network")
        self.stop_ap()
        device = wifi_device()
        run(["nmcli", "--wait", "30", "connection", "up", "web-display-wifi", "ifname", device or ""], 45)
        return bool(internet_status()["online"])

    def ap_is_up(self) -> bool:
        result = run(["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"])
        return AP_ID in result.stdout.splitlines()

    def tick(self, online: bool | None = None) -> None:
        now = self.clock()
        if self.ap_active and not self.ap_is_up():
            LOG.info("Setup AP was displaced by another connection")
            self.stop_ap()
            self.restore_client()
        if online is None:
            online = bool(internet_status()["online"])
        if online:
            self.offline_since = None
            if self.ap_active:
                self.stop_ap()
                self.restore_client()
            return
        if not self.auto_ap():
            self.offline_since = None
            if self.ap_active:
                LOG.info("Automatic setup AP was disabled; stopping it")
                self.stop_ap()
                self.restore_client()
            return
        if self.offline_since is None:
            self.offline_since = now
        if self.ap_active:
            if now - self.last_retry >= RETRY_INTERVAL:
                if not self.retry_client():
                    self.start_ap()
                else:
                    self.offline_since = None
            return
        if now - self.offline_since >= OUTAGE_GRACE:
            if not self.start_ap():
                # Avoid hammering NetworkManager when AP cannot start.
                self.offline_since = now - OUTAGE_GRACE + 60


def locked_tick(supervisor: Supervisor) -> None:
    with LOCK_PATH.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        supervisor.tick()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="run", choices=["run", "start-ap", "stop-ap"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    supervisor = Supervisor()
    if args.command == "start-ap":
        return 0 if supervisor.start_ap() else 1
    if args.command == "stop-ap":
        supervisor.stop_ap()
        supervisor.restore_client()
        return 0
    supervisor.remove_ap_profile()
    supervisor.restore_client()
    while True:
        try:
            locked_tick(supervisor)
        except Exception:
            LOG.exception("Network supervision tick failed")
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
