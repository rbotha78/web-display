from __future__ import annotations

import re
import urllib.request
from urllib.parse import urljoin, urlsplit

from .carousel import validate_url

MAX_MANIFEST_BYTES = 256 * 1024
MAX_VIDEO_BITRATE = 4_000_000


def select_rendition(
    manifest: str, base_url: str, max_bitrate: int = MAX_VIDEO_BITRATE,
) -> str:
    lines = manifest.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "#EXTM3U":
        raise ValueError("Stream did not return an HLS playlist")
    variants: list[tuple[int, int, str]] = []
    master = False
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        master = True
        attributes = {
            key: value.strip('"')
            for key, value in re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]+)', line)
        }
        try:
            bitrate = int(attributes.get("BANDWIDTH", "0"))
            resolution = attributes.get("RESOLUTION")
            width, height = map(int, resolution.split("x")) if resolution else (0, 0)
        except ValueError:
            raise ValueError("HLS playlist has invalid variant metadata") from None
        if not 0 < bitrate <= max_bitrate or not 0 <= width <= 1280 or not 0 <= height <= 720:
            continue
        codecs = attributes.get("CODECS", "").lower().split(",")
        if any(codec.startswith(("hev", "hvc", "av01", "vp09")) for codec in codecs):
            continue
        if index + 1 >= len(lines) or not lines[index + 1].strip() or lines[index + 1].startswith("#"):
            raise ValueError("HLS playlist is missing a variant URL")
        try:
            destination = validate_url(urljoin(base_url, lines[index + 1].strip()))
        except ValueError:
            raise ValueError("HLS variant must use an HTTP(S) URL without credentials") from None
        variants.append((width * height, bitrate, destination))
    if not master:
        return base_url
    if not variants:
        raise ValueError(
            f"No compatible HLS rendition at or below 720p and {max_bitrate / 1_000_000:g} Mbps"
        )
    return max(variants)[2]


def resolve_video_url(url: str, max_bitrate: int = MAX_VIDEO_BITRATE) -> str:
    if not urlsplit(url).path.lower().endswith(".m3u8"):
        return url
    request = urllib.request.Request(url, headers={"User-Agent": "WebDisplay/1"})
    with urllib.request.urlopen(request, timeout=8) as response:
        data = response.read(MAX_MANIFEST_BYTES + 1)
        if len(data) > MAX_MANIFEST_BYTES:
            raise ValueError("HLS playlist exceeds the size limit")
        try:
            manifest = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("HLS playlist is not UTF-8") from None
        return select_rendition(manifest, response.url, max_bitrate)
