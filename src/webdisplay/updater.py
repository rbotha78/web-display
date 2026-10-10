"""Signed application-bundle updates: verification, installation and rollback.

Trust model:
- Bundles are Ed25519-signed; the public key is part of the base image.
- Installation, the boot guard and health confirmation run as root from the base
  image copy of this module (see the update-action wrapper), never from an
  installed bundle, so a faulty bundle cannot break its own rollback.
- Services load an installed bundle through PYTHONPATH=<root>/current. Removing
  that symlink returns the device to the factory copy of the application.

Bundle layout (an uncompressed tar): manifest.json, manifest.sig and
payload/webdisplay/... The signature covers the exact manifest bytes, and the
manifest lists the SHA-256 and size of every payload file.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

LOG = logging.getLogger("web-display-update")

APP_ROOT = Path("/var/lib/web-display-app")
PUBLIC_KEY = Path("/etc/web-display/update-key.pub")
RELEASE_FILE = Path("/etc/web-display-release")

MANIFEST_FORMAT = 1
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
MAX_SIGNATURE_BYTES = 1024
MAX_MEMBERS = 4000
MAX_BOOT_ATTEMPTS = 3
SERVICES = ("web-display.service", "web-display-kiosk.service", "web-display-network.service")

VERSION_RE = re.compile(r"^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$")
PAYLOAD_RE = re.compile(r"^webdisplay(/[A-Za-z0-9._-]+)+$")


class UpdateError(ValueError):
    """An update was rejected; the message is safe to show to an administrator."""


def parse_version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or not VERSION_RE.match(value):
        raise UpdateError("Invalid version")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def read_release(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
    fsync_directory(path.parent)


def receive(stream: BinaryIO, destination: Path, limit: int = MAX_BUNDLE_BYTES) -> int:
    total = 0
    with open(destination, "wb") as output:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise UpdateError("Update file is too large")
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    return total


class Updater:
    def __init__(
        self,
        root: Path = APP_ROOT,
        public_key: Path = PUBLIC_KEY,
        release_file: Path = RELEASE_FILE,
        openssl: str = "openssl",
    ):
        self.root = root
        self.public_key = public_key
        self.release_file = release_file
        self.openssl = openssl

    # State -----------------------------------------------------------------

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    def state(self) -> dict[str, object]:
        state: dict[str, object] = {"floor": None, "failed": [], "previous": None, "pending": None}
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state.update({key: loaded[key] for key in state if key in loaded})
        except (OSError, ValueError):
            pass
        return state

    def base(self) -> tuple[str, int]:
        release = read_release(self.release_file)
        version = release.get("WEB_DISPLAY_VERSION", "0.0.0")
        try:
            compat = int(release.get("UPDATE_BASE_COMPAT", "0"))
        except ValueError:
            compat = 0
        return version, compat

    def installed_version(self) -> str | None:
        """Version the `current` symlink selects, or None for the factory copy."""
        try:
            name = os.path.basename(os.readlink(self.root / "current"))
        except OSError:
            return None
        if VERSION_RE.match(name) and (self.root / "versions" / name).is_dir():
            return name
        return None

    def effective_version(self) -> str:
        return self.installed_version() or self.base()[0]

    def running_version(self) -> str:
        """Version of the code in this process: a bundle version or the base version."""
        try:
            path = Path(__file__).resolve()
            parts = path.relative_to((self.root / "versions").resolve()).parts
            if VERSION_RE.match(parts[0]):
                return parts[0]
        except (OSError, ValueError, IndexError):
            pass
        return self.base()[0]

    def status(self) -> dict[str, object]:
        state = self.state()
        base_version, compat = self.base()
        return {
            "base_version": base_version,
            "base_compat": compat,
            "running_version": self.running_version(),
            "installed_version": self.effective_version(),
            "previous_version": state["previous"],
            "pending": state["pending"],
            "floor": state["floor"],
            "failed": state["failed"],
        }

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o755)
        os.chmod(self.root, 0o755)
        with open(self.root / "lock", "a", encoding="ascii") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    # Verification ----------------------------------------------------------

    def verify_signature(self, manifest: bytes, signature: bytes, workdir: Path) -> None:
        if not self.public_key.is_file():
            raise UpdateError("This device has no update signing key")
        manifest_path = workdir / "manifest.json"
        signature_path = workdir / "manifest.sig"
        manifest_path.write_bytes(manifest)
        signature_path.write_bytes(signature)
        try:
            result = subprocess.run(
                [
                    self.openssl, "pkeyutl", "-verify", "-pubin",
                    "-inkey", str(self.public_key), "-rawin",
                    "-in", str(manifest_path), "-sigfile", str(signature_path),
                ],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise UpdateError("Signature verification is unavailable") from None
        if result.returncode != 0:
            raise UpdateError("Signature verification failed")

    def read_members(self, archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
        members = archive.getmembers()
        if len(members) > MAX_MEMBERS:
            raise UpdateError("Update contains too many files")
        found: dict[str, tarfile.TarInfo] = {}
        total = 0
        for member in members:
            name = member.name
            allowed = name in ("manifest.json", "manifest.sig") or (
                name.startswith("payload/") and PAYLOAD_RE.match(name[len("payload/"):])
            )
            parts = name.split("/")
            if not allowed or not member.isreg() or "." in parts or ".." in parts or "" in parts:
                raise UpdateError("Update contains an unexpected entry")
            if name in found:
                raise UpdateError("Update contains duplicate entries")
            total += member.size
            if total > MAX_BUNDLE_BYTES:
                raise UpdateError("Update is too large")
            found[name] = member
        if "manifest.json" not in found or "manifest.sig" not in found:
            raise UpdateError("Update is not signed")
        if found["manifest.json"].size > MAX_MANIFEST_BYTES or found["manifest.sig"].size > MAX_SIGNATURE_BYTES:
            raise UpdateError("Update metadata is too large")
        return found

    def parse_manifest(self, data: bytes) -> dict[str, object]:
        try:
            manifest = json.loads(data)
        except ValueError:
            raise UpdateError("Update manifest is invalid") from None
        if not isinstance(manifest, dict) or manifest.get("format") != MANIFEST_FORMAT:
            raise UpdateError("Unsupported update format")
        parse_version(manifest.get("version"))
        if not isinstance(manifest.get("base_compat"), int):
            raise UpdateError("Update manifest is invalid")
        files = manifest.get("files")
        if not isinstance(files, dict) or not files:
            raise UpdateError("Update manifest is invalid")
        for name, entry in files.items():
            if (
                not isinstance(name, str)
                or not PAYLOAD_RE.match(name)
                or ".." in name.split("/")
                or not isinstance(entry, dict)
                or not isinstance(entry.get("size"), int)
                or entry["size"] < 0
                or not isinstance(entry.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            ):
                raise UpdateError("Update manifest is invalid")
        return manifest

    def check_policy(self, manifest: dict[str, object]) -> None:
        version = str(manifest["version"])
        _, compat = self.base()
        if manifest["base_compat"] != compat:
            raise UpdateError("This update needs a different base firmware; flash a newer image")
        state = self.state()
        if version in state["failed"]:  # type: ignore[operator]
            raise UpdateError("This version previously failed on this device")
        newest = parse_version(self.effective_version())
        floor = state["floor"]
        if floor:
            newest = max(newest, parse_version(floor))
        if parse_version(version) <= newest:
            raise UpdateError("This update is not newer than the installed version")

    def unpack(self, bundle: Path, workdir: Path, destination: Path) -> dict[str, object]:
        """Verify the bundle and extract its payload into `destination` (created here)."""
        try:
            archive = tarfile.open(bundle, mode="r:")
        except (tarfile.TarError, OSError):
            raise UpdateError("Update file is not a valid bundle") from None
        with archive:
            try:
                members = self.read_members(archive)
                manifest_bytes = archive.extractfile(members["manifest.json"]).read()  # type: ignore[union-attr]
                signature = archive.extractfile(members["manifest.sig"]).read()  # type: ignore[union-attr]
                self.verify_signature(manifest_bytes, signature, workdir)
                manifest = self.parse_manifest(manifest_bytes)
                self.check_policy(manifest)
                files: dict[str, dict[str, object]] = manifest["files"]  # type: ignore[assignment]
                payload = {name[len("payload/"):] for name in members if name.startswith("payload/")}
                if payload != set(files):
                    raise UpdateError("Update contents do not match the manifest")
                needed = sum(entry["size"] for entry in files.values()) * 2 + 16 * 1024 * 1024  # type: ignore[operator]
                self.root.mkdir(parents=True, exist_ok=True)
                if shutil.disk_usage(self.root).free < needed:
                    raise UpdateError("Not enough free storage for the update")
                destination.mkdir(parents=True)
                for name, entry in files.items():
                    self.extract_file(archive, members["payload/" + name], destination / name, entry)
            except (tarfile.TarError, EOFError, OSError) as error:
                if isinstance(error, OSError) and error.errno == 28:
                    raise UpdateError("Not enough free storage for the update") from None
                raise UpdateError("Update file is corrupt") from None
        (destination / "manifest.json").write_bytes(manifest_bytes)
        (destination / "manifest.sig").write_bytes(signature)
        # The service user must be able to read the code whatever umask the caller has.
        for path in (destination, *destination.rglob("*")):
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
        return manifest

    def extract_file(
        self, archive: tarfile.TarFile, member: tarfile.TarInfo, target: Path, entry: dict[str, object]
    ) -> None:
        if member.size != entry["size"]:
            raise UpdateError("Update contents do not match the manifest")
        source = archive.extractfile(member)
        if source is None:
            raise UpdateError("Update file is corrupt")
        digest = hashlib.sha256()
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with open(target, "wb") as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(target, 0o644)
        if digest.hexdigest() != entry["sha256"]:
            raise UpdateError("Update contents failed integrity verification")

    # Installation ----------------------------------------------------------

    def install(self, bundle: Path) -> dict[str, object]:
        with self.locked():
            state = self.state()
            if state["pending"]:
                raise UpdateError("A previous update has not been confirmed yet; restart and try again")
            staging = self.root / "staging"
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(mode=0o700)
            try:
                (self.root / "versions").mkdir(exist_ok=True, mode=0o755)
                workdir = staging / "verify"
                workdir.mkdir()
                unpacked = staging / "unpacked"
                manifest = self.unpack(bundle, workdir, unpacked)
                version = str(manifest["version"])
                final = self.root / "versions" / version
                shutil.rmtree(final, ignore_errors=True)
                os.replace(unpacked, final)
                fsync_directory(final.parent)
                previous = self.installed_version()
                state["pending"] = {"version": version, "previous": previous, "attempts": 0}
                write_json(self.state_path, state)
                self.switch_current(version)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        LOG.info("Installed update %s", version)
        return {"ok": True, "version": version, "previous": previous, "restart_required": True}

    def switch_current(self, version: str | None) -> None:
        link = self.root / "current"
        if version is None:
            link.unlink(missing_ok=True)
        else:
            temporary = self.root / "current.tmp"
            temporary.unlink(missing_ok=True)
            os.symlink(f"versions/{version}", temporary)
            os.replace(temporary, link)
        fsync_directory(self.root)

    def prune(self) -> None:
        keep = {self.installed_version(), self.state()["previous"]}
        versions = self.root / "versions"
        if not versions.is_dir():
            return
        for entry in versions.iterdir():
            if entry.name not in keep:
                shutil.rmtree(entry, ignore_errors=True)

    # Boot guard and health confirmation -----------------------------------

    def rollback(self, reason: str) -> None:
        state = self.state()
        pending = state["pending"]
        if not isinstance(pending, dict):
            return
        failed = list(state["failed"])  # type: ignore[call-overload]
        failed.append(pending["version"])
        previous = pending.get("previous")
        self.switch_current(previous if isinstance(previous, str) else None)
        state.update({"pending": None, "failed": failed, "previous": None})
        write_json(self.state_path, state)
        self.prune()
        LOG.error("Rolled back update %s: %s", pending["version"], reason)

    def guard(self) -> str:
        """Run early at boot: count unconfirmed boots and revert after repeated failures."""
        with self.locked():
            state = self.state()
            pending = state["pending"]
            if not isinstance(pending, dict):
                return "none"
            pending["attempts"] = int(pending.get("attempts", 0)) + 1
            if pending["attempts"] > MAX_BOOT_ATTEMPTS or self.installed_version() != pending["version"]:
                self.rollback("unconfirmed boots exceeded the limit")
                return "rolled-back"
            write_json(self.state_path, state)
            return "pending"

    def confirm(
        self,
        health: Callable[[], bool],
        timeout: float = 180,
        stable: float = 20,
        interval: float = 5,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        restart: Callable[[], None] | None = None,
    ) -> str:
        """Confirm a pending update once the system stays healthy; otherwise revert it."""
        if not isinstance(self.state()["pending"], dict):
            return "none"
        deadline = clock() + timeout
        healthy_since: float | None = None
        while True:
            now = clock()
            if health():
                healthy_since = now if healthy_since is None else healthy_since
                if now - healthy_since >= stable:
                    break
            else:
                healthy_since = None
            if now >= deadline:
                with self.locked():
                    self.rollback("health check failed after the update")
                if restart:
                    restart()
                return "rolled-back"
            sleep(interval)
        with self.locked():
            state = self.state()
            pending = state["pending"]
            if isinstance(pending, dict):
                floor = state["floor"]
                version = str(pending["version"])
                if not floor or parse_version(version) > parse_version(floor):
                    state["floor"] = version
                state["previous"] = pending.get("previous")
                state["pending"] = None
                write_json(self.state_path, state)
                self.prune()
        return "confirmed"


def system_healthy(port: int = 8443, expected_version: str | None = None) -> bool:
    for service in SERVICES:
        result = subprocess.run(["systemctl", "is-active", "--quiet", service], check=False)
        if result.returncode != 0:
            return False
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(f"https://127.0.0.1:{port}/health", timeout=5, context=context) as reply:
            if reply.status != 200:
                return False
            # The web service must actually be running the version being confirmed.
            return expected_version is None or json.load(reply).get("version") == expected_version
    except (OSError, ValueError):
        return False


def restart_services() -> None:
    subprocess.run(["systemctl", "restart", *SERVICES], check=False, timeout=120)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["install", "guard", "confirm", "status"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    os.umask(0o022)
    updater = Updater()
    try:
        if args.command == "install":
            with contextlib.ExitStack() as stack:
                updater.root.mkdir(parents=True, exist_ok=True, mode=0o755)
                staging = updater.root / "incoming"
                shutil.rmtree(staging, ignore_errors=True)
                staging.mkdir(mode=0o700)
                stack.callback(shutil.rmtree, staging, True)
                bundle = staging / "bundle.wdupdate"
                receive(sys.stdin.buffer, bundle)
                result = updater.install(bundle)
            print(json.dumps(result))
        elif args.command == "status":
            print(json.dumps(updater.status()))
        elif args.command == "guard":
            print(updater.guard())
        else:
            def healthy() -> bool:
                pending = updater.state()["pending"]
                version = str(pending["version"]) if isinstance(pending, dict) else None
                return system_healthy(expected_version=version)

            print(updater.confirm(healthy, restart=restart_services))
        return 0
    except UpdateError as error:
        print(json.dumps({"error": str(error)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
