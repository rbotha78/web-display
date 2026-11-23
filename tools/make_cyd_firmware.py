#!/usr/bin/env python3
"""Build and sign CYD OTA manifest assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$")


def cyd_version(platformio: Path = ROOT / "cyd/platformio.ini") -> str:
    match = re.search(
        r'CYD_VERSION=\\\\"([^"\\\\]+)\\\\\"',
        platformio.read_text(encoding="utf-8"),
    )
    if not match:
        raise SystemExit("CYD_VERSION is missing from cyd/platformio.ini")
    return match.group(1)


def sign(manifest: bytes, key_file: Path) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "manifest.json"
        source.write_bytes(manifest)
        result = subprocess.run(
            [
                "openssl", "pkeyutl", "-sign", "-inkey", str(key_file),
                "-rawin", "-in", str(source),
            ],
            capture_output=True,
            check=True,
        )
    if len(result.stdout) != 64:
        raise SystemExit("Ed25519 signature must be 64 bytes")
    return result.stdout


def build(payload: Path, output_dir: Path, version: str, key_file: Path) -> tuple[Path, Path, Path]:
    if not VERSION_RE.fullmatch(version):
        raise SystemExit(f"Invalid CYD version: {version}")
    if not payload.exists() or payload.stat().st_size <= 0:
        raise SystemExit(f"Missing CYD firmware: {payload}")
    data = payload.read_bytes()
    manifest = {
        "format": 1,
        "product": "web-display-cyd",
        "version": version,
        "chip": "esp32",
        "board": "esp32dev",
        "partition_layout": "min_spiffs",
        "payload": f"web-display-cyd-{version}.bin",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    signature = sign(manifest_bytes, key_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / f"web-display-cyd-{version}.manifest.json"
    signature_path = output_dir / f"web-display-cyd-{version}.manifest.sig"
    bin_path = output_dir / f"web-display-cyd-{version}.bin"
    bin_path.write_bytes(data)
    manifest_path.write_bytes(manifest_bytes)
    signature_path.write_bytes(signature)
    return bin_path, manifest_path, signature_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--version")
    args = parser.parse_args()
    version = args.version or cyd_version()

    key_file = args.key_file
    temporary = None
    if key_file is None:
        pem = os.environ.get("UPDATE_SIGNING_KEY")
        if not pem:
            raise SystemExit("Provide --key-file or UPDATE_SIGNING_KEY")
        descriptor, name = tempfile.mkstemp(prefix="cyd-sign-")
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(pem if pem.endswith("\n") else pem + "\n")
        key_file = temporary
    try:
        bin_path, manifest_path, signature_path = build(
            args.payload, args.output_dir, version, key_file
        )
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    print(bin_path)
    print(manifest_path)
    print(signature_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
