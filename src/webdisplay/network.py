"""Unprivileged connectivity probes. Internet failure is reported separately from website failure."""

from __future__ import annotations

import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

INTERNET_ENDPOINTS = (("1.1.1.1", 443), ("8.8.8.8", 443), ("9.9.9.9", 443))
PROBE_TIMEOUT = 3.0


def tcp_probe(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def internet_status(endpoints=INTERNET_ENDPOINTS, probe=tcp_probe) -> dict[str, object]:
    with ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
        results = list(pool.map(lambda endpoint: probe(*endpoint), endpoints))
    return {
        "online": any(results),
        "reachable_endpoints": sum(results),
        "total_endpoints": len(results),
    }


def website_status(url: str, probe=tcp_probe) -> dict[str, object]:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return {"reachable": False}
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return {"host": host, "reachable": probe(host, port)}


def default_route_interface() -> str:
    try:
        result = subprocess.run(
            ["ip", "-4", "route", "show", "default"],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"\bdev (\S+)", result.stdout)
    return match.group(1) if match else ""
