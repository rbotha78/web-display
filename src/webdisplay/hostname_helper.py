"""Privileged hostname change. Runs as root via sudo; reads one JSON request on stdin.

The change is applied by a transient systemd unit running outside the management
service's sandbox, because it rewrites /etc/hosts, regenerates the HTTPS
certificate (its names include the hostname) and restarts the management service.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
RESERVED = {"localhost", "localhost.localdomain"}
HOSTS = Path("/etc/hosts")
TLS_DIR = Path("/var/lib/web-display/tls")
UNIT = "web-display-rename"


def validate_hostname(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Hostname must be a string")
    name = value.strip().lower()
    if not LABEL.fullmatch(name):
        raise ValueError(
            "Hostname must be 1-63 characters: letters, digits and hyphens, "
            "not starting or ending with a hyphen"
        )
    if name in RESERVED:
        raise ValueError("That hostname is reserved")
    return name


def run(command: list[str], timeout: int = 30) -> None:
    subprocess.run(command, check=True, timeout=timeout, capture_output=True)


def rewrite_hosts(content: str, old: str, new: str) -> str:
    """Point the 127.0.1.1 entry at the new name, adding it if it is missing."""
    lines = content.splitlines()
    for index, line in enumerate(lines):
        fields = line.split()
        if fields and fields[0] == "127.0.1.1":
            lines[index] = f"127.0.1.1\t{new}"
            break
    else:
        lines.append(f"127.0.1.1\t{new}")
    return "\n".join(lines) + "\n"


def apply(name: str) -> None:
    name = validate_hostname(name)
    old = socket.gethostname()
    run(["hostnamectl", "set-hostname", name])
    HOSTS.write_text(rewrite_hosts(HOSTS.read_text(encoding="utf-8"), old, name), encoding="utf-8")
    for leaf in ("cert.pem", "key.pem"):
        (TLS_DIR / leaf).unlink(missing_ok=True)
    run(["systemctl", "restart", "avahi-daemon.service"])
    run(["systemctl", "restart", "web-display-certificate.service"], 120)
    run(["systemctl", "restart", "web-display.service"])


def schedule(name: str) -> dict[str, object]:
    name = validate_hostname(name)
    if name == socket.gethostname():
        return {"hostname": name, "changed": False}
    # --on-active gives the HTTP response time to reach the client first.
    run(
        [
            "systemd-run",
            f"--unit={UNIT}",
            "--collect",
            "--quiet",
            "--on-active=2",
            sys.executable,
            "-m",
            "webdisplay.hostname_helper",
            "apply",
            name,
        ]
    )
    return {"hostname": name, "changed": True}


def main() -> int:
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "apply":
            if os.geteuid() != 0:
                raise RuntimeError("must run as root")
            apply(sys.argv[2])
            return 0
        request = json.loads(sys.stdin.read(1024))
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        print(json.dumps(schedule(request.get("hostname"))))
        return 0
    except (ValueError, RuntimeError, subprocess.SubprocessError, OSError) as error:
        print(json.dumps({"error": str(error)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
