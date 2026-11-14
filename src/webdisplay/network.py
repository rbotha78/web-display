"""Unprivileged connectivity probes. Internet failure is reported separately from website failure."""

from __future__ import annotations

import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

INTERNET_ENDPOINTS = (("1.1.1.1", 443), ("8.8.8.8", 443), ("9.9.9.9", 443))
PROBE_TIMEOUT = 3.0
# What the setup-AP supervisor treats as "connected": the local router, or the internet.
AP_CHECKS = ("gateway", "internet")
DEFAULT_AP_CHECK = "gateway"
GATEWAY_ROUTE = re.compile(r"^default via (\S+) dev (\S+)", re.MULTILINE)


def validate_ap_check(value: object) -> str:
    if value not in AP_CHECKS:
        raise ValueError("Connectivity check must be gateway or internet")
    return str(value)


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


def default_gateways() -> list[tuple[str, str]]:
    """IPv4 default gateways with their interfaces, e.g. Ethernet and Wi-Fi."""
    try:
        result = subprocess.run(["ip", "-4", "route", "show", "default"],
                                capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    return list(dict.fromkeys(GATEWAY_ROUTE.findall(result.stdout)))


def gateway_probe(gateway: str, device: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """Ping the gateway; a router that drops ICMP still counts if it just answered ARP."""
    try:
        if subprocess.run(["ping", "-n", "-q", "-c", "1", "-W", str(int(timeout)), "-I", device, gateway],
                          capture_output=True, timeout=timeout + 2, check=False).returncode == 0:
            return True
        neighbour = subprocess.run(["ip", "neigh", "show", gateway, "dev", device],
                                   capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return "REACHABLE" in neighbour.stdout.split()


def gateway_status(gateways=None, probe=gateway_probe) -> dict[str, object]:
    gateways = default_gateways() if gateways is None else gateways
    if not gateways:
        return {"online": False, "reachable_gateways": 0, "total_gateways": 0}
    with ThreadPoolExecutor(max_workers=len(gateways)) as pool:
        results = list(pool.map(lambda route: probe(*route), gateways))
    return {"online": any(results), "reachable_gateways": sum(results), "total_gateways": len(results)}


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
