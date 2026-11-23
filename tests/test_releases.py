import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from webdisplay import releases


class ReleasesTests(unittest.TestCase):
    def test_latest_cyd_firmware_requires_complete_asset_set(self):
        release = {
            "assets": [
                {
                    "name": "web-display-cyd-0.2.20.bin",
                    "browser_download_url": "https://github.com/rbotha78/web-display/releases/download/v0.2.20/web-display-cyd-0.2.20.bin",
                    "size": 1200000,
                },
                {
                    "name": "web-display-cyd-0.2.20.manifest.json",
                    "browser_download_url": "https://github.com/rbotha78/web-display/releases/download/v0.2.20/web-display-cyd-0.2.20.manifest.json",
                    "size": 300,
                },
                {
                    "name": "web-display-cyd-0.2.20.manifest.sig",
                    "browser_download_url": "https://github.com/rbotha78/web-display/releases/download/v0.2.20/web-display-cyd-0.2.20.manifest.sig",
                    "size": 64,
                },
            ]
        }
        class Reply(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        reply = Reply(json.dumps(release).encode())

        opener = Mock(return_value=reply)
        latest = releases.latest_cyd_firmware(opener=opener)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["version"], "0.2.20")
        self.assertEqual(latest["bin"]["size"], 1200000)
        self.assertEqual(latest["manifest"]["size"], 300)
        self.assertEqual(latest["signature"]["size"], 64)

    def test_cyd_manifest_builder_signs_and_hashes_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "key.pem"
            pub = root / "key.pub"
            payload = root / "firmware.bin"
            output = root / "output"
            payload.write_bytes(b"ESP32 OTA test payload")
            subprocess.run(
                ["openssl", "genpkey", "-algorithm", "Ed25519", "-out", str(key)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(pub)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "python3", "tools/make_cyd_firmware.py",
                    "--payload", str(payload),
                    "--output-dir", str(output),
                    "--key-file", str(key),
                    "--version", "0.2.21",
                ],
                check=True,
                capture_output=True,
            )
            manifest_path = output / "web-display-cyd-0.2.21.manifest.json"
            signature_path = output / "web-display-cyd-0.2.21.manifest.sig"
            bin_path = output / "web-display-cyd-0.2.21.bin"
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["version"], "0.2.21")
            self.assertEqual(manifest["size"], bin_path.stat().st_size)
            self.assertEqual(len(signature_path.read_bytes()), 64)


if __name__ == "__main__":
    unittest.main()
