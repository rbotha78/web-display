import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class BuildConfigTests(unittest.TestCase):
    def test_first_boot_user_rename_is_disabled(self):
        build_script = Path("build-image.sh").read_text(encoding="utf-8")
        self.assertIn("DISABLE_FIRST_BOOT_USER_RENAME=1", build_script)
        self.assertIn('FIRST_USER_NAME="image-build"', build_script)

        install_script = Path(
            "image/stage-web-display/01-install/00-run.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("userdel --remove image-build", install_script)

    def test_build_uses_configurable_https_raspbian_mirror_for_base(self):
        build_script = Path("build-image.sh").read_text(encoding="utf-8")
        self.assertIn("https://mirrorservice.org/sites/archive.raspbian.org/raspbian", build_script)
        self.assertIn("stage0/prerun.sh", build_script)
        self.assertIn("stage0/00-configure-apt/files/raspbian.sources", build_script)
        self.assertIn('BASE_ID="${PI_GEN_COMMIT}:${RASPBIAN_MIRROR}:${WPA_COUNTRY}"', build_script)

    def test_install_manifest_is_complete_and_shared(self):
        files = Path("image/stage-web-display/01-install/files")
        entries = [
            line.split()
            for line in (files / "install-manifest").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertGreater(len(entries), 10)
        for mode, source, destination in entries:
            self.assertRegex(mode, r"^0[0-7]{3}$")
            self.assertTrue((files / source).is_file(), source)
            self.assertTrue(destination.startswith("/"), destination)
        for script in (
            "image/stage-web-display/01-install/00-run.sh",
            "tools/push-to-device.sh",
        ):
            self.assertIn("install-manifest", Path(script).read_text(encoding="utf-8"))

    def test_update_services_are_wired(self):
        files = Path("image/stage-web-display/01-install/files")
        manifest = (files / "install-manifest").read_text(encoding="utf-8")
        for destination in (
            "/usr/lib/web-display/update-action",
            "/etc/web-display/update-key.pub",
            "/etc/systemd/system/web-display-update-guard.service",
            "/etc/systemd/system/web-display-update-confirm.service",
        ):
            self.assertIn(destination, manifest)
        for unit in ("web-display.service", "web-display-network.service", "web-display-kiosk.service"):
            text = (files / unit).read_text(encoding="utf-8")
            self.assertIn("PYTHONPATH=/var/lib/web-display-app/current", text, unit)
        self.assertIn("update-action install", (files / "web-display-sudoers").read_text(encoding="utf-8"))
        self.assertIn("PYTHONPATH", (files / "update-action").read_text(encoding="utf-8"))
        run = Path("image/stage-web-display/01-install/00-run.sh").read_text(encoding="utf-8")
        for unit in ("web-display-update-guard.service", "web-display-update-confirm.service"):
            self.assertIn(f"systemctl enable {unit}", run)
        self.assertIn("-----BEGIN PUBLIC KEY-----", (files / "update-key.pub").read_text(encoding="utf-8"))
        self.assertNotIn("PRIVATE", (files / "update-key.pub").read_text(encoding="utf-8"))

    def test_release_workflow_signs_and_verifies_update_bundle(self):
        workflow = Path(".github/workflows/build-image.yml").read_text(encoding="utf-8")
        self.assertIn("secrets.UPDATE_SIGNING_KEY", workflow)
        self.assertIn("tools/verify_update_bundle.py", workflow)

    def test_image_workflow_is_release_only_and_pinned(self):
        workflow = Path(".github/workflows/build-image.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request", Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
        self.assertIn("types: [published]", workflow)
        self.assertNotIn("--prerelease", workflow)
        pinned = workflow + Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
        for line in pinned.splitlines():
            if "uses:" in line:
                self.assertRegex(line, r"@[0-9a-f]{40}\b")

    def test_certificate_service_creates_its_state_directory(self):
        certificate_service = Path(
            "image/stage-web-display/01-install/files/"
            "web-display-certificate.service"
        ).read_text(encoding="utf-8")
        self.assertIn("StateDirectory=web-display", certificate_service)
        self.assertIn("StateDirectoryMode=0750", certificate_service)

    def test_xorg_uses_modesetting_driver(self):
        xorg_config = Path(
            "image/stage-web-display/01-install/files/20-web-display-modesetting.conf"
        ).read_text(encoding="utf-8")
        self.assertIn('Driver "modesetting"', xorg_config)
        self.assertIn('Option "kmsdev" "/dev/dri/card0"', xorg_config)
        session = Path(
            "image/stage-web-display/01-install/files/kiosk-session"
        ).read_text(encoding="utf-8")
        self.assertIn("--set CTM 1,0,0,0,1,0,0,0,1", session)
    def test_variants_and_wifi_helper_privileges(self):
        build_script = Path("build-image.sh").read_text(encoding="utf-8")
        self.assertIn("development", build_script)
        self.assertIn("release) ;;", build_script)
        files = Path("image/stage-web-display/01-install/files")
        install_script = Path("image/stage-web-display/01-install/00-run.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("debug-authorized_keys", install_script)
        self.assertIn("systemctl mask ssh.service ssh.socket", install_script)
        sudoers = (files / "web-display-sudoers").read_text(encoding="utf-8")
        self.assertIn('wifi-action ""', sudoers)
        service = (files / "web-display.service").read_text(encoding="utf-8")
        self.assertIn("/etc/NetworkManager/system-connections", service)
        self.assertIn("AF_NETLINK", service)
        self.assertEqual(
            (files / "90-web-display-debug.conf").read_text(encoding="utf-8").count(" no"), 3
        )

    def test_debug_key_helper_is_development_only(self):
        files = Path("image/stage-web-display/01-install/files")
        rule = "/usr/lib/web-display/debug-keys-action"
        self.assertIn(rule + ' ""', (files / "web-display-debug-sudoers").read_text(encoding="utf-8"))
        self.assertNotIn("debug-keys", (files / "web-display-sudoers").read_text(encoding="utf-8"))
        self.assertNotIn("debug-keys", (files / "install-manifest").read_text(encoding="utf-8"))
        self.assertIn("debug-keys-action", (files / "disable-web-display-debug").read_text(encoding="utf-8"))
        install_script = Path("image/stage-web-display/01-install/00-run.sh").read_text(encoding="utf-8")
        block = install_script[install_script.index("debug-authorized_keys") :]
        self.assertIn("debug-keys-action", block)
        self.assertEqual(install_script.count("debug-keys-action"), block.count("debug-keys-action"))

    def test_network_supervisor_is_installed_and_enabled(self):
        install_script = Path("image/stage-web-display/01-install/00-run.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("systemctl enable web-display-network.service", install_script)
        unit = Path(
            "image/stage-web-display/01-install/files/web-display-network.service"
        ).read_text(encoding="utf-8")
        self.assertIn("-m webdisplay.netmonitor", unit)
        self.assertIn("ProtectSystem=strict", unit)

    def test_image_suffix_matches_release_milestone(self):
        export_image = Path("image/stage-web-display/EXPORT_IMAGE").read_text(
            encoding="utf-8"
        )
        release = Path(
            "image/stage-web-display/01-install/files/web-display-release"
        ).read_text(encoding="utf-8")
        self.assertIn('IMG_SUFFIX="-milestone2"', export_image)
        self.assertIn("MILESTONE=3", release)

    def test_kiosk_hides_mouse_cursor(self):
        unit = Path(
            "image/stage-web-display/01-install/files/web-display-kiosk.service"
        ).read_text(encoding="utf-8")
        self.assertIn("-nocursor", unit)
        self.assertIn("SupplementaryGroups=audio video input render", unit)


    def test_build_script_has_fast_development_path_only(self):
        script = Path("build-image.sh").read_text(encoding="utf-8")
        self.assertIn("FRESH", script)
        self.assertIn("CONTINUE=1", script)
        self.assertIn('DEPLOY_COMPRESSION="xz"', script)
        self.assertIn('DEPLOY_COMPRESSION="${DEPLOY_COMPRESSION:-gz}"', script)

    def test_admin_ui_bundle_is_committed_and_packaged(self):
        self.assertTrue(Path("src/webdisplay/static/index.html").is_file())
        self.assertTrue(list(Path("src/webdisplay/static/assets").glob("*.js")))
        project = Path("pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("static/**/*", project)


class BuildCountryTests(unittest.TestCase):
    def build(self, country=None, cached_country=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        shutil.copyfile("build-image.sh", root / "build-image.sh")
        pi_gen = root / ".build/pi-gen"
        for path in (
            pi_gen / ".git", pi_gen / "stage1", pi_gen / "stage2",
            root / "image/stage-web-display/01-install/files",
            root / "src/webdisplay", root / "bin",
        ):
            path.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            "image/stage-web-display/01-install/files/web-display-release",
            root / "image/stage-web-display/01-install/files/web-display-release",
        )
        for path in (
            pi_gen / "stage0/prerun.sh",
            pi_gen / "stage0/00-configure-apt/files/raspbian.sources",
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("http://raspbian.raspberrypi.com/raspbian/\n", encoding="ascii")
        for path, content in (
            (root / "bin/git", "#!/bin/sh\nexit 0\n"),
            (root / "bin/docker", "#!/bin/sh\nexit 0\n"),
            (pi_gen / "build-docker.sh", "#!/bin/sh\nmkdir -p deploy\n"),
        ):
            path.write_text(content, encoding="ascii")
            path.chmod(0o755)
        key = root / "debug.pub"
        key.write_text("fixture public key\n", encoding="ascii")
        mirror = "https://mirror.example.test/raspbian"
        if cached_country is not None:
            pinned_commit = Path(
                "image/stage-web-display/01-install/files/web-display-release"
            ).read_text().split("PI_GEN_COMMIT=", 1)[1].splitlines()[0]
            (root / ".build/base-development.stamp").write_text(
                f"{pinned_commit}:{mirror}:{cached_country}\n", encoding="ascii",
            )
        env = {
            **os.environ,
            "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
            "VARIANT": "development", "DEBUG_SSH_PUBLIC_KEY": str(key),
            "RASPBIAN_MIRROR": mirror, "FRESH": "0", "BASE_MAX_AGE_DAYS": "14",
        }
        env.pop("WPA_COUNTRY", None)
        if country is not None:
            env["WPA_COUNTRY"] = country
        result = subprocess.run(
            ["bash", str(root / "build-image.sh")], cwd=root, env=env,
            capture_output=True, text=True, timeout=15,
        )
        return root, result

    def test_default_country_is_passed_to_pi_gen_on_a_fresh_build(self):
        root, result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (root / ".build/pi-gen/config").read_text()
        self.assertIn('WPA_COUNTRY="GB"\n', config)
        self.assertNotIn("Fast build:", result.stdout)

    def test_country_change_rebuilds_base_but_matching_country_reuses_it(self):
        for country, reuse in (("US", False), ("GB", True)):
            with self.subTest(country=country):
                root, result = self.build(country, cached_country="GB")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(
                    f'WPA_COUNTRY="{country}"\n', (root / ".build/pi-gen/config").read_text(),
                )
                self.assertEqual("Fast build:" in result.stdout, reuse)
                for stage in ("stage0", "stage1", "stage2"):
                    self.assertEqual((root / ".build/pi-gen" / stage / "SKIP").exists(), reuse)
                self.assertTrue(
                    (root / ".build/base-development.stamp").read_text().strip().endswith(":" + country),
                )

    def test_invalid_country_fails_before_building(self):
        for country in ("", "gb", "GBR", "G1", 'GB"\nENABLE_SSH=1'):
            with self.subTest(country=country):
                root, result = self.build(country)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("WPA_COUNTRY must be", result.stderr)
                self.assertFalse((root / ".build/pi-gen/config").exists())

    def test_image_country_remains_editable_by_existing_wifi_helper(self):
        stage_script = Path("image/stage-web-display/01-install/00-run.sh").resolve()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rootfs = root / "rootfs"
            (rootfs / "boot/firmware").mkdir(parents=True)
            cmdline = rootfs / "boot/firmware/cmdline.txt"
            cmdline.write_text(
                "console=tty1 cfg80211.ieee80211_regdom=GB root=PARTUUID=test quiet\n",
                encoding="ascii",
            )
            (root / "files/webdisplay").mkdir(parents=True)
            (root / "files/install-manifest").write_text("", encoding="ascii")
            result = subprocess.run(
                ["bash", "-c",
                 'on_chroot() { cat >/dev/null; }; export -f on_chroot; exec bash "$1"',
                 "stage-test", str(stage_script)],
                cwd=root, env={**os.environ, "ROOTFS_DIR": str(rootfs), "WPA_COUNTRY": "US"},
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(cmdline.read_text(), "console=tty1 root=PARTUUID=test quiet\n")
            config = rootfs / "etc/modprobe.d/web-display-regdom.conf"
            self.assertEqual(config.read_text(), "options cfg80211 ieee80211_regdom=US\n")
            self.assertEqual(config.stat().st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()
