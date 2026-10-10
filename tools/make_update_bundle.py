#!/usr/bin/env python3
"""Build and sign a Web Display application update bundle (.wdupdate).

The signing key is an Ed25519 private key in PEM form. Pass it with --key-file, or
set UPDATE_SIGNING_KEY to the PEM text (it is written to a private temporary file).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE_FILE = ROOT / "image/stage-web-display/01-install/files/web-display-release"
PACKAGE = ROOT / "src" / "webdisplay"
MANIFEST_FORMAT = 1


def release_values(path: Path = RELEASE_FILE) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


def payload_files(package: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(package)
        if path.is_dir() or "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        files["webdisplay/" + relative.as_posix()] = path
    return files


def build_manifest(version: str, base_compat: int, files: dict[str, Path]) -> bytes:
    entries = {}
    for name, path in files.items():
        data = path.read_bytes()
        entries[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    manifest = {
        "format": MANIFEST_FORMAT,
        "product": "web-display",
        "version": version,
        "base_compat": base_compat,
        "files": entries,
    }
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()


def sign(manifest: bytes, key_file: Path) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "manifest.json"
        source.write_bytes(manifest)
        result = subprocess.run(
            ["openssl", "pkeyutl", "-sign", "-inkey", str(key_file), "-rawin", "-in", str(source)],
            capture_output=True,
            check=True,
        )
    return result.stdout


def add_member(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    archive.addfile(info, io.BytesIO(data))


def build_bundle(
    output: Path, key_file: Path, version: str, base_compat: int, package: Path = PACKAGE
) -> Path:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"Version {version!r} must look like 1.2.3")
    files = payload_files(package)
    if not files:
        raise SystemExit("No payload files found")
    manifest = build_manifest(version, base_compat, files)
    signature = sign(manifest, key_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:", format=tarfile.PAX_FORMAT) as archive:
        add_member(archive, "manifest.json", manifest)
        add_member(archive, "manifest.sig", signature)
        for name, path in files.items():
            add_member(archive, "payload/" + name, path.read_bytes())
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "deploy")
    parser.add_argument("--version", help="defaults to WEB_DISPLAY_VERSION from the release file")
    args = parser.parse_args()
    values = release_values()
    version = args.version or values["WEB_DISPLAY_VERSION"]
    base_compat = int(values["UPDATE_BASE_COMPAT"])
    key_file = args.key_file
    temporary = None
    if key_file is None:
        pem = os.environ.get("UPDATE_SIGNING_KEY")
        if not pem:
            raise SystemExit("Provide --key-file or UPDATE_SIGNING_KEY")
        descriptor, name = tempfile.mkstemp(prefix="wd-sign-")
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(pem if pem.endswith("\n") else pem + "\n")
        key_file = temporary
    try:
        output = build_bundle(
            args.output_dir / f"web-display-update-{version}.wdupdate", key_file, version, base_compat
        )
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
