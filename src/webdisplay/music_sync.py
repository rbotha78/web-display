"""Music sync groups: validation, the pinned leader client and clock-offset estimation.

A leader display owns the group's playhead; followers poll it over pinned HTTPS with
a shared group token and play the same Plex track from their own Plex connection.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time

GROUP_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")
TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}")
TOKEN_HASH = re.compile(r"[0-9a-f]{64}")
FINGERPRINT = re.compile(r"(?:[0-9A-F]{2}:){31}[0-9A-F]{2}")
HOST_LABEL = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")
NAME = re.compile(r"[^\x00-\x1f\x7f]{1,64}")
PEER_COMMANDS = {"pause", "resume", "skip", "play", "stop"}
# Seconds between a scheduled track and its start, so every member can buffer first.
START_LEAD = 1.5
LISTENER_TIMEOUT = 15.0
MEMBER_TIMEOUT = 60.0
POLL_INTERVAL = 2.0


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_matches(token: object, stored_hash: str) -> bool:
    return (isinstance(token, str) and TOKEN.fullmatch(token) is not None
            and hmac.compare_digest(token_hash(token), stored_hash))


def _host(value: object) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 253:
        raise ValueError("Invalid leader address")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    host = value.rstrip(".")
    if not host or not all(HOST_LABEL.fullmatch(label) for label in host.split(".")):
        raise ValueError("Invalid leader address")
    return host


def _leader(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"name", "hostname", "port", "addresses", "fingerprint"}:
        raise ValueError("Leader must contain name, hostname, port, addresses and fingerprint")
    name, hostname, port = value["name"], value["hostname"], value["port"]
    addresses, fingerprint = value["addresses"], value["fingerprint"]
    if not isinstance(name, str) or not NAME.fullmatch(name.strip()):
        raise ValueError("Leader name must be 1-64 characters")
    if hostname != "":
        hostname = _host(hostname)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError("Invalid leader port")
    if not isinstance(addresses, list) or not 0 < len(addresses) <= 8:
        raise ValueError("Leader needs 1-8 addresses")
    hosts = list(dict.fromkeys(_host(item) for item in addresses))
    if not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("Leader fingerprint must be a colon-separated SHA-256 fingerprint")
    return {"name": name.strip(), "hostname": hostname, "port": port,
            "addresses": hosts, "fingerprint": fingerprint}


def validate_stored_sync(value: object) -> dict[str, object]:
    """Validate the private configuration form (leader tokens are stored only as hashes)."""
    if value is None:
        return {"role": "off"}
    if not isinstance(value, dict):
        raise ValueError("Invalid music sync configuration")
    role = value.get("role")
    if role == "off" and set(value) == {"role"}:
        return {"role": "off"}
    if role == "leader" and set(value) == {"role", "group_id", "token_hash"}:
        if (isinstance(value["group_id"], str) and GROUP_ID.fullmatch(value["group_id"])
                and isinstance(value["token_hash"], str) and TOKEN_HASH.fullmatch(value["token_hash"])):
            return dict(value)
    if role == "follower" and set(value) == {"role", "group_id", "token", "leader"}:
        if (isinstance(value["group_id"], str) and GROUP_ID.fullmatch(value["group_id"])
                and isinstance(value["token"], str) and TOKEN.fullmatch(value["token"])):
            return {**value, "leader": _leader(value["leader"])}
    raise ValueError("Invalid music sync configuration")


def sync_from_request(value: object) -> dict[str, object]:
    """Convert a manager PUT body into the stored configuration."""
    if not isinstance(value, dict):
        raise ValueError("Music sync settings must be an object")
    role = value.get("role")
    if role == "off":
        if set(value) != {"role"}:
            raise ValueError("Leaving a group takes only the role")
        return {"role": "off"}
    if role not in {"leader", "follower"}:
        raise ValueError("Role must be off, leader or follower")
    expected = {"role", "group_id", "token"} | ({"leader"} if role == "follower" else set())
    if set(value) != expected:
        raise ValueError(f"A {role} needs exactly: {', '.join(sorted(expected))}")
    group, token = value["group_id"], value["token"]
    if not isinstance(group, str) or not GROUP_ID.fullmatch(group):
        raise ValueError("Group ID must be 16-64 letters, digits, _ or -")
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError("Group token must be 32-128 letters, digits, _ or -")
    if role == "leader":
        return {"role": "leader", "group_id": group, "token_hash": token_hash(token)}
    return {"role": "follower", "group_id": group, "token": token, "leader": _leader(value["leader"])}


def peer_request(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not {"name", "listening"} <= set(value) <= {"name", "listening", "command"}:
        raise ValueError("Invalid music sync request")
    name, listening = value["name"], value["listening"]
    if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(listening, bool):
        raise ValueError("Invalid music sync request")
    command = value.get("command")
    if command is not None:
        if not isinstance(command, dict) or len(command) != 1:
            raise ValueError("Invalid music sync command")
        if "action" in command:
            if command["action"] not in PEER_COMMANDS:
                raise ValueError("Invalid music sync command")
        elif "like" not in command or not isinstance(command["like"], str):
            raise ValueError("Invalid music sync command")
    return {"name": name, "listening": listening, "command": command}


def format_fingerprint(der: bytes) -> str:
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


class LeaderError(ValueError):
    pass


class _PinnedConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, fingerprint: str, timeout: float):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # Trust comes from the manager-supplied pinned fingerprint, not a CA.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(host, port, timeout=timeout, context=context)
        self.fingerprint = fingerprint

    def connect(self) -> None:
        super().connect()
        der = self.sock.getpeercert(binary_form=True)  # type: ignore[union-attr]
        if not der or format_fingerprint(der) != self.fingerprint:
            self.close()
            raise LeaderError("The leader's certificate changed; recreate the group in the manager")


class LeaderClient:
    """Pinned HTTPS client for the leader, trying each known address in turn."""

    def __init__(self, leader: dict[str, object], token: str, timeout: float = 5):
        self.leader, self.token, self.timeout = leader, token, timeout
        hosts = list(leader["addresses"])
        if leader["hostname"]:
            hosts.append(leader["hostname"] if "." in leader["hostname"] else leader["hostname"] + ".local")
        self.hosts = list(dict.fromkeys(hosts))

    def call(self, body: dict[str, object]) -> dict[str, object]:
        data = json.dumps(body).encode()
        errors = []
        for host in list(self.hosts):
            connection = _PinnedConnection(host, int(self.leader["port"]), str(self.leader["fingerprint"]),
                                           self.timeout)
            try:
                connection.request("POST", "/api/music/sync/peer", body=data, headers={
                    "Content-Type": "application/json", "X-Music-Sync-Token": self.token,
                })
                response = connection.getresponse()
                raw = response.read(64 * 1024)
                status = response.status
            except LeaderError:
                raise
            except (OSError, http.client.HTTPException, ssl.SSLError) as error:
                errors.append(f"{host}: {error}")
                continue
            finally:
                connection.close()
            # Prefer the address that answered next time.
            self.hosts.remove(host)
            self.hosts.insert(0, host)
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            if status >= 400:
                raise LeaderError(str(payload.get("error") or f"The leader returned HTTP {status}"))
            return payload
        raise LeaderError("Cannot reach the leader display" + (f" ({errors[-1]})" if errors else ""))


class ClockOffset:
    """Estimate leader-minus-local clock offset from request round trips (lowest RTT wins)."""

    def __init__(self, samples: int = 8):
        self.samples: list[tuple[float, float]] = []
        self.limit = samples

    def add(self, sent: float, server_time: object, received: float) -> None:
        if isinstance(server_time, bool) or not isinstance(server_time, (int, float)):
            return
        rtt = received - sent
        if rtt < 0 or rtt > 2:
            return
        self.samples.append((rtt, float(server_time) - (sent + received) / 2))
        self.samples = self.samples[-self.limit:]

    @property
    def offset(self) -> float:
        return min(self.samples)[1] if self.samples else 0.0


def local_name() -> str:
    name = socket.gethostname().strip()[:64]
    return name if name and NAME.fullmatch(name) else "display"


def now() -> float:
    return time.time()
