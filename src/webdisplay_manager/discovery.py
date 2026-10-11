"""Find devices advertising _web-display._tcp with a minimal multicast-DNS browser."""

from __future__ import annotations

import socket
import struct
import time

SERVICE = "_web-display._tcp.local"
MDNS_GROUP = ("224.0.0.251", 5353)
TYPE_PTR = 12
TYPE_SRV = 33


def encode_name(name: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".") if p) + b"\0"


def build_query(name: str = SERVICE) -> bytes:
    return struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + encode_name(name) + struct.pack(">HH", TYPE_PTR, 1)


def read_name(data: bytes, offset: int, depth: int = 0) -> tuple[str, int]:
    if depth > 10:
        raise ValueError("name compression loop")
    labels: list[str] = []
    end = None
    while True:
        if offset >= len(data):
            raise ValueError("truncated name")
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if end is None:
                end = offset + 2
            suffix, _ = read_name(data, pointer, depth + 1)
            labels.append(suffix)
            offset = len(data)
            break
        offset += 1
        labels.append(data[offset : offset + length].decode("utf-8", "replace"))
        offset += length
    return ".".join(l for l in labels if l), end if end is not None else offset


def parse_response(data: bytes) -> list[dict[str, object]]:
    """Return {name, port} for each service instance announced in one packet."""
    try:
        _, flags, questions, answers, authority, additional = struct.unpack(">HHHHHH", data[:12])
        if not flags & 0x8000:
            return []
        offset = 12
        for _ in range(questions):
            _, offset = read_name(data, offset)
            offset += 4
        instances: list[str] = []
        ports: dict[str, int] = {}
        for _ in range(answers + authority + additional):
            name, offset = read_name(data, offset)
            rtype, _, _, length = struct.unpack(">HHIH", data[offset : offset + 10])
            offset += 10
            rdata = offset
            offset += length
            if rtype == TYPE_PTR and name.lower() == SERVICE:
                target, _ = read_name(data, rdata)
                instances.append(target)
            elif rtype == TYPE_SRV:
                ports[name] = struct.unpack(">H", data[rdata + 4 : rdata + 6])[0]
    except (struct.error, ValueError, IndexError):
        return []
    found = []
    for instance in instances:
        label = instance[: -len(SERVICE) - 1] if instance.lower().endswith(SERVICE) else instance
        found.append({"name": label, "port": ports.get(instance, 8443)})
    return found


def discover(duration: float = 2.0) -> list[dict[str, object]]:
    """Browse for devices; each result is {name, host, port}. The sender is the device."""
    found: dict[tuple[str, int], dict[str, object]] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.settimeout(0.3)
        deadline = time.monotonic() + duration
        next_query = 0.0
        while time.monotonic() < deadline:
            if time.monotonic() >= next_query:
                sock.sendto(build_query(), MDNS_GROUP)
                next_query = time.monotonic() + 0.7
            try:
                data, (host, _) = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            for item in parse_response(data):
                found[(host, int(item["port"]))] = {"name": item["name"], "host": host, "port": item["port"]}
    except OSError:
        pass
    finally:
        sock.close()
    return sorted(found.values(), key=lambda d: (str(d["name"]), str(d["host"])))
