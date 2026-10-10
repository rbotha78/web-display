import io
import os
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import make_update_bundle as builder  # noqa: E402
from webdisplay import updater  # noqa: E402
from webdisplay.updater import UpdateError, Updater  # noqa: E402


def generate_keys(directory: Path, name: str) -> tuple[Path, Path]:
    private = directory / f"{name}.pem"
    public = directory / f"{name}.pub"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(private)], check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True, capture_output=True)
    return private, public


class UpdaterCase(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.private, self.public = generate_keys(self.directory, "good")
        self.other_private, _ = generate_keys(self.directory, "other")
        self.release = self.directory / "release"
        self.release.write_text("WEB_DISPLAY_VERSION=1.0.0\nUPDATE_BASE_COMPAT=1\n")
        self.root = self.directory / "app"
        self.updater = Updater(self.root, self.public, self.release)
        self.package = self.directory / "package"
        (self.package / "static").mkdir(parents=True)
        (self.package / "app.py").write_text("VERSION = 'x'\n")
        (self.package / "static" / "index.html").write_text("<html></html>")
        (self.package / "__pycache__").mkdir()
        (self.package / "__pycache__" / "app.pyc").write_bytes(b"junk")

    def bundle(self, version="1.1.0", key=None, compat=1, name=None) -> Path:
        output = self.directory / (name or f"bundle-{version}.wdupdate")
        return builder.build_bundle(output, key or self.private, version, compat, self.package)

    def raw_bundle(self, members, manifest=None, key=None) -> Path:
        """Hand-built tar. `members` are (name, data, tar type) payload entries."""
        files = {n: self.directory / "f" for n, _, _ in members}
        entries = {
            n: {"sha256": __import__("hashlib").sha256(d).hexdigest(), "size": len(d)}
            for n, d, _ in members
            if n.startswith("webdisplay/")
        }
        manifest = manifest or json.dumps(
            {"format": 1, "version": "1.1.0", "base_compat": 1, "files": entries}
        ).encode()
        signature = builder.sign(manifest, key or self.private)
        path = self.directory / "raw.wdupdate"
        with tarfile.open(path, "w:") as archive:
            builder.add_member(archive, "manifest.json", manifest)
            builder.add_member(archive, "manifest.sig", signature)
            for name, data, kind in members:
                info = tarfile.TarInfo(name)
                info.size = len(data) if kind == tarfile.REGTYPE else 0
                info.type = kind
                if kind == tarfile.SYMTYPE:
                    info.linkname = "/etc/passwd"
                archive.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        del files
        return path

    def install(self, **kwargs):
        return self.updater.install(self.bundle(**kwargs))

    def healthy_confirm(self):
        return self.updater.confirm(lambda: True, timeout=10, stable=0, sleep=lambda _: None)


class InstallTests(UpdaterCase):
    def test_install_switches_current_and_marks_pending(self):
        result = self.install()
        self.assertEqual(result["version"], "1.1.0")
        self.assertTrue(result["restart_required"])
        self.assertEqual(self.updater.installed_version(), "1.1.0")
        self.assertTrue((self.root / "current" / "webdisplay" / "app.py").is_file())
        self.assertTrue((self.root / "current" / "webdisplay" / "static" / "index.html").is_file())
        self.assertFalse((self.root / "current" / "webdisplay" / "__pycache__").exists())
        self.assertEqual(self.updater.state()["pending"], {"version": "1.1.0", "previous": None, "attempts": 0})
        self.assertFalse((self.root / "staging").exists())

    def test_installed_files_are_readable_under_a_restrictive_umask(self):
        previous = os.umask(0o077)
        try:
            self.install()
        finally:
            os.umask(previous)
        for path in (self.root / "current").resolve().rglob("*"):
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o755 if path.is_dir() else 0o644, path)

    def test_bundle_is_deterministic(self):
        first = self.bundle(name="a.wdupdate").read_bytes()
        second = self.bundle(name="b.wdupdate").read_bytes()
        self.assertEqual(first, second)

    def test_wrong_key_is_rejected_without_changes(self):
        with self.assertRaisesRegex(UpdateError, "Signature"):
            self.updater.install(self.bundle(key=self.other_private))
        self.assertIsNone(self.updater.installed_version())
        self.assertFalse(self.updater.state()["pending"])

    def test_missing_public_key_is_rejected(self):
        self.public.unlink()
        with self.assertRaisesRegex(UpdateError, "no update signing key"):
            self.install()

    def test_tampered_payload_is_rejected(self):
        original = self.bundle()
        tampered = self.directory / "tampered.wdupdate"
        with tarfile.open(original) as source, tarfile.open(tampered, "w:") as target:
            for member in source.getmembers():
                data = source.extractfile(member).read()
                if member.name == "payload/webdisplay/app.py":
                    data = b"VERSION = 'y'\n"
                    member.size = len(data)
                target.addfile(member, io.BytesIO(data))
        with self.assertRaisesRegex(UpdateError, "integrity"):
            self.updater.install(tampered)
        self.assertIsNone(self.updater.installed_version())

    def test_unlisted_payload_file_is_rejected(self):
        original = self.bundle()
        extra = self.directory / "extra.wdupdate"
        with tarfile.open(original) as source, tarfile.open(extra, "w:") as target:
            for member in source.getmembers():
                target.addfile(member, io.BytesIO(source.extractfile(member).read()))
            builder.add_member(target, "payload/webdisplay/backdoor.py", b"x")
        with self.assertRaisesRegex(UpdateError, "do not match"):
            self.updater.install(extra)

    def test_path_traversal_entry_is_rejected(self):
        bundle = self.raw_bundle([("payload/webdisplay/../../etc/x", b"x", tarfile.REGTYPE)])
        with self.assertRaisesRegex(UpdateError, "unexpected entry"):
            self.updater.install(bundle)

    def test_entry_outside_package_is_rejected(self):
        bundle = self.raw_bundle([("payload/etc/passwd", b"x", tarfile.REGTYPE)])
        with self.assertRaisesRegex(UpdateError, "unexpected entry"):
            self.updater.install(bundle)

    def test_symlink_entry_is_rejected(self):
        bundle = self.raw_bundle([("payload/webdisplay/link", b"", tarfile.SYMTYPE)])
        with self.assertRaisesRegex(UpdateError, "unexpected entry"):
            self.updater.install(bundle)

    def test_non_bundle_is_rejected(self):
        junk = self.directory / "junk"
        junk.write_bytes(b"not a tar file" * 100)
        with self.assertRaisesRegex(UpdateError, "not a valid bundle"):
            self.updater.install(junk)

    def test_unsigned_bundle_is_rejected(self):
        unsigned = self.directory / "unsigned.wdupdate"
        with tarfile.open(unsigned, "w:") as archive:
            builder.add_member(archive, "payload/webdisplay/app.py", b"x")
        with self.assertRaisesRegex(UpdateError, "not signed"):
            self.updater.install(unsigned)

    def test_receive_enforces_size_limit(self):
        with self.assertRaisesRegex(UpdateError, "too large"):
            updater.receive(io.BytesIO(b"x" * 100), self.directory / "out", limit=10)


class PolicyTests(UpdaterCase):
    def test_same_or_older_version_is_rejected(self):
        for version in ("1.0.0", "0.9.0"):
            with self.assertRaisesRegex(UpdateError, "not newer"):
                self.install(version=version)

    def test_base_compat_mismatch_is_rejected(self):
        with self.assertRaisesRegex(UpdateError, "different base"):
            self.install(compat=2)

    def test_pending_update_blocks_another_install(self):
        self.install()
        with self.assertRaisesRegex(UpdateError, "not been confirmed"):
            self.install(version="1.2.0")

    def test_floor_blocks_reinstalling_older_version_after_factory_reset(self):
        self.install(version="1.2.0")
        self.healthy_confirm()
        (self.root / "current").unlink()
        with self.assertRaisesRegex(UpdateError, "not newer"):
            self.install(version="1.1.0")
        self.install(version="1.3.0")

    def test_failed_version_is_not_retried(self):
        self.install(version="1.1.0")
        self.updater.confirm(lambda: False, timeout=0, stable=0, sleep=lambda _: None)
        with self.assertRaisesRegex(UpdateError, "previously failed"):
            self.install(version="1.1.0")
        self.install(version="1.1.1")

    def test_version_format_is_strict(self):
        for value in ("1.0", "01.0.0", "1.0.0-beta", "../1.0.0", 5, None):
            with self.assertRaises(UpdateError):
                updater.parse_version(value)


class ConfirmAndRollbackTests(UpdaterCase):
    def test_healthy_system_confirms_update(self):
        self.install(version="1.1.0")
        self.assertEqual(self.healthy_confirm(), "confirmed")
        state = self.updater.state()
        self.assertIsNone(state["pending"])
        self.assertEqual(state["floor"], "1.1.0")
        self.assertEqual(self.updater.installed_version(), "1.1.0")

    def test_confirm_requires_stable_health(self):
        self.install()
        clock = iter(range(0, 1000))
        results = iter([True, False, True, True, True, True, True])
        outcome = self.updater.confirm(
            lambda: next(results), timeout=100, stable=3, interval=1,
            sleep=lambda _: None, clock=lambda: next(clock) * 1.0,
        )
        self.assertEqual(outcome, "confirmed")

    def test_unhealthy_update_rolls_back_to_factory_and_restarts(self):
        self.install()
        restarted = []
        outcome = self.updater.confirm(
            lambda: False, timeout=0, stable=0, sleep=lambda _: None, restart=lambda: restarted.append(1)
        )
        self.assertEqual(outcome, "rolled-back")
        self.assertEqual(restarted, [1])
        self.assertIsNone(self.updater.installed_version())
        self.assertFalse((self.root / "current").exists())
        state = self.updater.state()
        self.assertEqual(state["failed"], ["1.1.0"])
        self.assertIsNone(state["pending"])
        self.assertFalse((self.root / "versions" / "1.1.0").exists())

    def test_rollback_returns_to_previous_installed_version(self):
        self.install(version="1.1.0")
        self.healthy_confirm()
        self.install(version="1.2.0")
        self.updater.confirm(lambda: False, timeout=0, stable=0, sleep=lambda _: None)
        self.assertEqual(self.updater.installed_version(), "1.1.0")
        self.assertTrue((self.root / "versions" / "1.1.0").is_dir())
        self.assertFalse((self.root / "versions" / "1.2.0").exists())

    def test_old_versions_are_pruned_after_confirmation(self):
        for version in ("1.1.0", "1.2.0", "1.3.0"):
            self.install(version=version)
            self.healthy_confirm()
        names = sorted(path.name for path in (self.root / "versions").iterdir())
        self.assertEqual(names, ["1.2.0", "1.3.0"])

    def test_confirm_without_pending_does_nothing(self):
        self.assertEqual(self.healthy_confirm(), "none")

    def test_guard_counts_boots_then_rolls_back(self):
        self.install()
        outcomes = [self.updater.guard() for _ in range(updater.MAX_BOOT_ATTEMPTS)]
        self.assertEqual(outcomes, ["pending"] * updater.MAX_BOOT_ATTEMPTS)
        self.assertEqual(self.updater.guard(), "rolled-back")
        self.assertIsNone(self.updater.installed_version())
        self.assertEqual(self.updater.state()["failed"], ["1.1.0"])

    def test_guard_without_pending_does_nothing(self):
        self.assertEqual(self.updater.guard(), "none")

    def test_guard_reverts_when_link_does_not_match_pending(self):
        self.install()
        (self.root / "current").unlink()
        self.assertEqual(self.updater.guard(), "rolled-back")


class StatusTests(UpdaterCase):
    def test_status_reports_versions(self):
        status = self.updater.status()
        self.assertEqual(status["base_version"], "1.0.0")
        self.assertEqual(status["installed_version"], "1.0.0")
        self.assertEqual(status["running_version"], "1.0.0")
        self.install()
        status = self.updater.status()
        self.assertEqual(status["installed_version"], "1.1.0")
        self.assertEqual(status["running_version"], "1.0.0")
        self.assertEqual(status["pending"]["version"], "1.1.0")


class CliTests(UpdaterCase):
    def test_install_command_reports_errors_as_json(self):
        junk = io.BytesIO(b"nope")
        stdout = io.StringIO()
        with (
            patch.object(updater, "Updater", return_value=self.updater),
            patch.object(sys, "stdin", new=io.TextIOWrapper(junk)),
            patch.object(sys, "stdout", new=stdout),
        ):
            code = updater.main(["install"])
        self.assertEqual(code, 1)
        self.assertIn("not a valid bundle", json.loads(stdout.getvalue())["error"])

    def test_install_command_installs_valid_bundle(self):
        data = io.BytesIO(self.bundle().read_bytes())
        stdout = io.StringIO()
        with (
            patch.object(updater, "Updater", return_value=self.updater),
            patch.object(sys, "stdin", new=io.TextIOWrapper(data)),
            patch.object(sys, "stdout", new=stdout),
        ):
            code = updater.main(["install"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["version"], "1.1.0")
        self.assertFalse((self.root / "incoming").exists())


if __name__ == "__main__":
    unittest.main()
