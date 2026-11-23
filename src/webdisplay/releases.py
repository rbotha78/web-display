"""Find and download signed update bundles from GitHub Releases.

Trust never rests on this module or on TLS: every download is verified by the root
updater against the signing key in the base image. Only the configured repository
is contacted, and clients cannot supply URLs.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import BinaryIO, Callable

from .updater import MAX_BUNDLE_BYTES, UpdateError, parse_version

REPOSITORY = "rbotha78/web-display"
ASSET_RE = re.compile(r"^web-display-update-(\d+\.\d+\.\d+)\.wdupdate$")
CYD_BIN_RE = re.compile(r"^web-display-cyd-(\d+\.\d+\.\d+)\.bin$")
CYD_MANIFEST_RE = re.compile(r"^web-display-cyd-(\d+\.\d+\.\d+)\.manifest\.json$")
CYD_SIGNATURE_RE = re.compile(r"^web-display-cyd-(\d+\.\d+\.\d+)\.manifest\.sig$")
MAX_API_BYTES = 2 * 1024 * 1024
MAX_CYD_MANIFEST_BYTES = 64 * 1024
MAX_CYD_FIRMWARE_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 20

Opener = Callable[..., BinaryIO]


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "web-display-updater"},
    )


def latest_bundle(repository: str = REPOSITORY, opener: Opener = urllib.request.urlopen) -> dict[str, object] | None:
    """Return the update bundle attached to the newest published release, if any."""
    try:
        with opener(_request(f"https://api.github.com/repos/{repository}/releases/latest"), timeout=TIMEOUT_SECONDS) as reply:
            release = json.loads(reply.read(MAX_API_BYTES + 1)[:MAX_API_BYTES])
    except (OSError, ValueError):
        raise UpdateError("Could not reach the release server") from None
    if not isinstance(release, dict):
        raise UpdateError("Unexpected reply from the release server")
    prefix = f"https://github.com/{repository}/releases/download/"
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        match = ASSET_RE.match(str(asset.get("name", "")))
        url = str(asset.get("browser_download_url", ""))
        size = asset.get("size")
        if match and url.startswith(prefix) and isinstance(size, int) and 0 < size <= MAX_BUNDLE_BYTES:
            parse_version(match.group(1))
            return {"version": match.group(1), "url": url, "size": size}
    return None


def latest_cyd_firmware(
    repository: str = REPOSITORY, opener: Opener = urllib.request.urlopen
) -> dict[str, object] | None:
    """Return the newest CYD firmware asset set attached to the latest release."""
    try:
        with opener(
            _request(f"https://api.github.com/repos/{repository}/releases/latest"),
            timeout=TIMEOUT_SECONDS,
        ) as reply:
            release = json.loads(reply.read(MAX_API_BYTES + 1)[:MAX_API_BYTES])
    except (OSError, ValueError):
        raise UpdateError("Could not reach the release server") from None
    if not isinstance(release, dict):
        raise UpdateError("Unexpected reply from the release server")

    prefix = f"https://github.com/{repository}/releases/download/"
    found: dict[str, dict[str, object]] = {}
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", ""))
        size = asset.get("size")
        if not url.startswith(prefix) or not isinstance(size, int) or size <= 0:
            continue
        match = CYD_BIN_RE.fullmatch(name)
        if match:
            found.setdefault(match.group(1), {})["bin"] = {"url": url, "size": size}
            continue
        match = CYD_MANIFEST_RE.fullmatch(name)
        if match:
            found.setdefault(match.group(1), {})["manifest"] = {"url": url, "size": size}
            continue
        match = CYD_SIGNATURE_RE.fullmatch(name)
        if match:
            found.setdefault(match.group(1), {})["signature"] = {"url": url, "size": size}

    candidates = []
    for version, assets in found.items():
        if set(("bin", "manifest", "signature")) - assets.keys():
            continue
        parse_version(version)
        bin_asset = assets["bin"]
        manifest_asset = assets["manifest"]
        signature_asset = assets["signature"]
        if (int(bin_asset["size"]) > MAX_CYD_FIRMWARE_BYTES
                or int(manifest_asset["size"]) > MAX_CYD_MANIFEST_BYTES
                or int(signature_asset["size"]) != 64):
            continue
        candidates.append({
            "version": version,
            "bin": bin_asset,
            "manifest": manifest_asset,
            "signature": signature_asset,
        })
    if not candidates:
        return None
    candidates.sort(key=lambda item: parse_version(str(item["version"])), reverse=True)
    return candidates[0]


def open_download(
    url: str, repository: str = REPOSITORY, opener: Opener = urllib.request.urlopen
) -> BinaryIO:
    if not url.startswith(f"https://github.com/{repository}/releases/download/"):
        raise UpdateError("Refusing to download from an unexpected location")
    try:
        return opener(_request(url), timeout=TIMEOUT_SECONDS)
    except OSError:
        raise UpdateError("Could not download the update") from None
