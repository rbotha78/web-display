#!/usr/bin/env python3
"""Verify a bundle against the repository's public key, as a device would."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from webdisplay.updater import UpdateError, Updater  # noqa: E402

import make_update_bundle  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: verify_update_bundle.py BUNDLE", file=sys.stderr)
        return 2
    values = make_update_bundle.release_values()
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        release = work / "release"
        release.write_text(f"WEB_DISPLAY_VERSION=0.0.0\nUPDATE_BASE_COMPAT={values['UPDATE_BASE_COMPAT']}\n")
        key = ROOT / "image/stage-web-display/01-install/files/update-key.pub"
        updater = Updater(work / "app", key, release)
        (work / "verify").mkdir()
        try:
            manifest = updater.unpack(Path(sys.argv[1]), work / "verify", work / "unpacked")
        except UpdateError as error:
            print(f"Bundle verification failed: {error}", file=sys.stderr)
            return 1
    print(f"Verified bundle version {manifest['version']} ({len(manifest['files'])} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
