"""Privileged management of the development SSH keys. Runs as root via sudo.

Only development images install this helper, its sudoers rule and the webdebug
account. It reads one JSON request on stdin: {"operation": "list"},
{"operation": "add", "key": "...", "name": "..."} or {"operation": "remove", "id": "..."}.
The file edit runs in a transient systemd unit because the management service's
sandbox hides /home.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
from pathlib import Path

USER = "webdebug"
AUTHORIZED_KEYS = Path("/home/webdebug/.ssh/authorized_keys")
# Created with the account and removed by disable-web-display-debug.
ENABLED_MARKER = Path("/etc/ssh/sshd_config.d/90-web-display-debug.conf")
UNIT = "web-display-debug-keys"
MAX_KEYS = 20
MAX_REQUEST_BYTES = 8192
MAX_KEY_LENGTH = 4096
KEY_TYPES = {
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
}
NAME_PATTERN = re.compile(r"[A-Za-z0-9 ._@-]{1,40}")
COMMENT_PATTERN = re.compile(r"[A-Za-z0-9 ._@+:=-]{1,80}")
ID_PATTERN = re.compile(r"[0-9a-f]{12}")
OPERATIONS = {"list", "add", "remove"}


def parse_line(line: str) -> dict[str, str] | None:
    """Return the key described by an authorized_keys line, or None if it is not a plain key."""
    fields = line.strip().split(None, 2)
    if len(fields) < 2 or fields[0] not in KEY_TYPES:
        return None
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except ValueError:
        return None
    length = int.from_bytes(blob[:4], "big")
    if len(blob) < 4 or blob[4 : 4 + length] != fields[0].encode():
        return None
    digest = hashlib.sha256(blob).digest()
    return {
        "type": fields[0],
        "key": fields[1],
        "comment": fields[2].strip() if len(fields) > 2 else "",
        "fingerprint": "SHA256:" + base64.b64encode(digest).decode().rstrip("="),
        "id": digest.hex()[:12],
    }


def validate_public_key(value: object) -> dict[str, str]:
    if not isinstance(value, str) or len(value) > MAX_KEY_LENGTH:
        raise ValueError("Public key must be a string")
    value = value.strip()
    parsed = None if "\n" in value or "\r" in value else parse_line(value)
    if parsed is None:
        raise ValueError(
            "Enter one public key on a single line, such as 'ssh-ed25519 AAAA... name'. "
            "Key options are not allowed."
        )
    return parsed


def validate_name(value: object) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not NAME_PATTERN.fullmatch(value.strip()):
        raise ValueError("Name must be 1-40 letters, digits, spaces or . _ @ -")
    return value.strip()


def validate_id(value: object) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValueError("No such SSH key")
    return value


def clean_request(request: object) -> dict[str, str]:
    if not isinstance(request, dict) or request.get("operation") not in OPERATIONS:
        raise ValueError("unsupported operation")
    operation = str(request["operation"])
    if operation == "add":
        parsed = validate_public_key(request.get("key"))
        name = validate_name(request.get("name"))
        if not name and COMMENT_PATTERN.fullmatch(parsed["comment"]):
            name = parsed["comment"]
        return {"operation": operation, "key": f"{parsed['type']} {parsed['key']}", "name": name}
    if operation == "remove":
        return {"operation": operation, "id": validate_id(request.get("id"))}
    return {"operation": operation}


def read_lines() -> list[str]:
    if not AUTHORIZED_KEYS.exists():
        return []
    return AUTHORIZED_KEYS.read_text(encoding="utf-8", errors="replace").splitlines()


def write_lines(lines: list[str]) -> None:
    account = pwd.getpwnam(USER)
    directory = AUTHORIZED_KEYS.parent
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chown(directory, account.pw_uid, account.pw_gid)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".authorized_keys.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("".join(line + "\n" for line in lines))
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, account.pw_uid, account.pw_gid)
        os.chmod(temporary, 0o600)
        os.replace(temporary, AUTHORIZED_KEYS)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def listing(lines: list[str]) -> dict[str, object]:
    keys = [key for key in map(parse_line, lines) if key]
    return {
        "enabled": True,
        "keys": [{k: key[k] for k in ("id", "type", "comment", "fingerprint")} for key in keys],
    }


def enabled() -> bool:
    if not ENABLED_MARKER.exists():
        return False
    try:
        pwd.getpwnam(USER)
    except KeyError:
        return False
    return True


def apply(request: dict[str, str]) -> dict[str, object]:
    if not enabled():
        if request["operation"] == "list":
            return {"enabled": False, "keys": []}
        raise ValueError("Development SSH access is not enabled on this device")
    lines = read_lines()
    if request["operation"] == "add":
        parsed = validate_public_key(request["key"])
        existing = [key for key in map(parse_line, lines) if key]
        if any(key["id"] == parsed["id"] for key in existing):
            raise ValueError("That key is already authorised")
        if len(existing) >= MAX_KEYS:
            raise ValueError("Too many SSH keys; remove one first")
        lines.append(f"{parsed['type']} {parsed['key']} {request['name']}".rstrip())
        write_lines(lines)
    elif request["operation"] == "remove":
        remaining = [line for line in lines if (parse_line(line) or {}).get("id") != request["id"]]
        if len(remaining) == len(lines):
            raise ValueError("No such SSH key")
        write_lines(remaining)
    return listing(read_lines())


def schedule(request: dict[str, str]) -> tuple[int, str]:
    result = subprocess.run(
        [
            "systemd-run",
            f"--unit={UNIT}",
            "--pipe",
            "--wait",
            "--collect",
            "--quiet",
            sys.executable,
            "-m",
            "webdisplay.debug_keys_helper",
            "apply",
        ],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.returncode, result.stdout


def main() -> int:
    try:
        raw = sys.stdin.read(MAX_REQUEST_BYTES)
        if len(sys.argv) == 2 and sys.argv[1] == "apply":
            if os.geteuid() != 0:
                raise RuntimeError("must run as root")
            print(json.dumps(apply(clean_request(json.loads(raw)))))
            return 0
        status, output = schedule(clean_request(json.loads(raw)))
        if not output.strip():
            raise RuntimeError("SSH key update failed")
        print(output.strip())
        return status
    except (ValueError, RuntimeError, subprocess.SubprocessError, OSError) as error:
        print(json.dumps({"error": str(error)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
