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

    def test_image_workflow_is_release_only_and_pinned(self):
        workflow = Path(".github/workflows/build-image.yml").read_text(encoding="utf-8")
        self.assertIn("types: [published]", workflow)
        self.assertIn("--prerelease", workflow)
        for line in workflow.splitlines():
            if "uses:" in line:
                self.assertRegex(line, r"@[0-9a-f]{40}\b")

    def test_certificate_service_creates_its_state_directory(self):
        certificate_service = Path(
            "image/stage-web-display/01-install/files/"
            "web-display-certificate.service"
        ).read_text(encoding="utf-8")
        self.assertIn("StateDirectory=web-display", certificate_service)
        self.assertIn("StateDirectoryMode=0750", certificate_service)

    def test_pi2_uses_framebuffer_xorg_driver(self):
        xorg_config = Path(
            "image/stage-web-display/01-install/files/20-web-display-fbdev.conf"
        ).read_text(encoding="utf-8")
        self.assertIn('Driver "fbdev"', xorg_config)
        self.assertIn('Option "fbdev" "/dev/fb0"', xorg_config)
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
        self.assertIn("MILESTONE=2", release)

    def test_kiosk_hides_mouse_cursor(self):
        unit = Path(
            "image/stage-web-display/01-install/files/web-display-kiosk.service"
        ).read_text(encoding="utf-8")
        self.assertIn("-nocursor", unit)


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


if __name__ == "__main__":
    unittest.main()
