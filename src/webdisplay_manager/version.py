"""Manager version (same as the firmware release it shipped with) and the oldest firmware it supports."""

from __future__ import annotations

import re
from pathlib import Path

# API keys, which the manager needs to authenticate, arrived in firmware 0.5.7.
MIN_FIRMWARE = "0.5.7"

RELEASE_FILE = Path(__file__).resolve().parents[2] / "image/stage-web-display/01-install/files/web-display-release"


def parse(value: object) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value) if isinstance(value, str) else None
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def manager_version() -> str:
    """Read from the release file in a repository checkout; 'unknown' when run elsewhere."""
    try:
        for line in RELEASE_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("WEB_DISPLAY_VERSION="):
                return line.partition("=")[2].strip()
    except OSError:
        pass
    return "unknown"


def firmware_too_old(version: object) -> bool:
    parsed, minimum = parse(version), parse(MIN_FIRMWARE)
    return parsed is not None and minimum is not None and parsed < minimum
