"""Internet radio: Radio Browser search, saved stations and a frame-indexed live buffer.

The relay ingests one station stream, strips ICY metadata and splits the audio into
MP3/ADTS frames. Frames have a constant duration, so every group member can start
from the same frame index at the same moment and stay in step without seeking.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import urllib.parse
from collections import deque
from typing import Callable, Iterator

STATION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
RADIO_KEY = re.compile(r"radio:(" + STATION_ID.pattern + r")\Z")
MAX_STATIONS = 50
MAX_URL = 2048
SEARCH_LIMIT = 30
SEARCH_HOSTS = ("all.api.radio-browser.info", "de1.api.radio-browser.info", "de2.api.radio-browser.info")
USER_AGENT = "WebDisplay/1 (+https://github.com/rbotha78/web-display)"
# MP3 and ADTS AAC frames have a fixed duration, which tight group sync relies on.
CODECS = {"MP3": "MP3", "AAC": "AAC", "AAC+": "AAC+", "HE-AAC": "AAC+"}
PLAYLIST_TYPES = {"audio/x-mpegurl", "audio/mpegurl", "application/x-mpegurl", "application/vnd.apple.mpegurl",
                  "audio/x-scpls", "application/pls+xml", "text/plain"}
BUFFER_SECONDS = 90.0


def radio_key(value: object) -> str | None:
    """Return the station id of a ``radio:<uuid>`` music source, if it is one."""
    match = RADIO_KEY.fullmatch(value) if isinstance(value, str) else None
    return match.group(1) if match else None


def valid_url(value: object, *, empty: bool = False) -> bool:
    if value == "" and empty:
        return True
    if not isinstance(value, str) or not 0 < len(value) <= MAX_URL or any(ord(c) < 33 or ord(c) == 127 for c in value):
        return False
    try:
        url = urllib.parse.urlsplit(value)
        url.port
    except ValueError:
        return False
    return url.scheme in {"http", "https"} and bool(url.hostname) and not url.username and not url.password


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()[:limit]


def validate_station(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Radio stations must be objects")
    station_id, name, url = value.get("id"), value.get("name"), value.get("url")
    favicon, codec, bitrate = value.get("favicon", ""), value.get("codec", ""), value.get("bitrate", 0)
    if not isinstance(station_id, str) or not STATION_ID.fullmatch(station_id):
        raise ValueError("Invalid radio station id")
    if not isinstance(name, str) or not _text(name, 200):
        raise ValueError("Radio stations need a name")
    if not valid_url(url):
        raise ValueError("Radio station streams must be http or https URLs")
    if not valid_url(favicon, empty=True):
        favicon = ""
    if codec not in CODECS:
        raise ValueError("Only MP3 and AAC radio streams are supported")
    if isinstance(bitrate, bool) or not isinstance(bitrate, int) or not 0 <= bitrate <= 10000:
        raise ValueError("Invalid radio station bitrate")
    return {"id": station_id, "name": _text(name, 200), "url": url, "favicon": favicon,
            "country": _text(value.get("country", ""), 100), "codec": CODECS[codec], "bitrate": bitrate}


def validate_stations(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > MAX_STATIONS:
        raise ValueError(f"Save up to {MAX_STATIONS} radio stations")
    stations = [validate_station(item) for item in value]
    if len({item["id"] for item in stations}) != len(stations):
        raise ValueError("Radio stations must be unique")
    return stations


def _station_from_search(item: dict) -> dict[str, object] | None:
    if not isinstance(item, dict) or item.get("hls") not in (0, "0", None):
        return None
    try:
        station = validate_station({
            "id": item.get("stationuuid"), "name": item.get("name"),
            "url": item.get("url_resolved") or item.get("url"), "favicon": item.get("favicon") or "",
            "country": item.get("country") or "", "codec": str(item.get("codec") or "").upper(),
            "bitrate": item.get("bitrate") if isinstance(item.get("bitrate"), int) else 0,
        })
    except ValueError:
        return None
    station["tags"] = _text(item.get("tags", ""), 200)
    return station


def search_query(query: dict[str, list[str]]) -> dict[str, object]:
    """Validate the device API's search query string."""
    if not set(query) <= {"name", "tag", "country", "offset"} or any(len(v) != 1 for v in query.values()):
        raise ValueError("Invalid radio search")
    values = {key: query[key][0].strip() for key in ("name", "tag", "country") if key in query}
    if any(len(value) > 100 or re.search(r"[\x00-\x1f\x7f]", value) for value in values.values()):
        raise ValueError("Radio search terms must be at most 100 characters")
    if "country" in values and values["country"] and not re.fullmatch(r"[A-Za-z]{2}", values["country"]):
        raise ValueError("Country must be a two-letter country code")
    if not any(values.values()):
        raise ValueError("Enter a station name or genre to search")
    offset = query.get("offset", ["0"])[0]
    if not offset.isdigit() or int(offset) > 1000:
        raise ValueError("Invalid radio search offset")
    return {**{key: value for key, value in values.items() if value}, "offset": int(offset)}


def _api_get(path: str, params: dict[str, object] | None = None, timeout: float = 15,
             hosts: tuple[str, ...] = SEARCH_HOSTS) -> object:
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    errors = []
    for host in hosts:
        connection = http.client.HTTPSConnection(host, timeout=timeout, context=ssl.create_default_context())
        try:
            connection.request("GET", path + query, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            response = connection.getresponse()
            body = response.read(2 * 1024 * 1024 + 1)
            if response.status != 200 or len(body) > 2 * 1024 * 1024:
                raise OSError(f"HTTP {response.status}")
            return json.loads(body)
        except (OSError, http.client.HTTPException, ValueError) as error:
            errors.append(str(error))
        finally:
            connection.close()
    raise OSError("Radio Browser is unavailable" + (f" ({errors[-1]})" if errors else ""))


def search(terms: dict[str, object], fetch: Callable[..., object] = _api_get) -> dict[str, object]:
    params: dict[str, object] = {"hidebroken": "true", "order": "clickcount", "reverse": "true",
                                 "limit": SEARCH_LIMIT, "offset": terms.get("offset", 0)}
    if terms.get("name"):
        params["name"] = terms["name"]
    if terms.get("tag"):
        params["tag"] = terms["tag"]
    if terms.get("country"):
        params["countrycode"] = str(terms["country"]).upper()
    raw = fetch("/json/stations/search", params)
    if not isinstance(raw, list):
        raise OSError("Radio Browser returned an invalid response")
    items, seen = [], set()
    for item in raw:
        station = _station_from_search(item)
        if station is not None and station["id"] not in seen:
            seen.add(station["id"])
            items.append(station)
    return {"items": items, "offset": params["offset"], "more": len(raw) >= SEARCH_LIMIT}


def register_click(station_id: str, fetch: Callable[..., object] = _api_get) -> None:
    """Tell Radio Browser a station was chosen, as its API guidelines request (best effort)."""
    if STATION_ID.fullmatch(station_id):
        try:
            fetch("/json/url/" + station_id, None, 5)
        except OSError:
            pass


# ----- Stream access -------------------------------------------------------------------

def _allowed_address(host: str, port: int) -> None:
    """Refuse loopback, link-local and other special addresses (LAN radio servers stay allowed)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise OSError(f"Cannot resolve the radio station ({error})") from error
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if (address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified
                or address.is_reserved):
            raise OSError("Radio station addresses must not be local to the display")


class _IcyResponse(http.client.HTTPResponse):
    """SHOUTcast v1 servers answer ``ICY 200 OK``; treat it as HTTP/1.0."""

    def _read_status(self):  # type: ignore[override]
        line = str(self.fp.readline(65537), "iso-8859-1")
        if not line:
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        if line.startswith("ICY "):
            line = "HTTP/1.0 " + line[4:]
        parts = line.split(None, 2)
        if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
            raise http.client.BadStatusLine(line)
        return parts[0], int(parts[1]), parts[2] if len(parts) > 2 else ""


def _connect(url: str, timeout: float):
    parts = urllib.parse.urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    _allowed_address(parts.hostname, port)
    if parts.scheme == "https":
        connection = http.client.HTTPSConnection(parts.hostname, port, timeout=timeout,
                                                 context=ssl.create_default_context())
    else:
        connection = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
    connection.response_class = _IcyResponse
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    connection.request("GET", path, headers={"User-Agent": USER_AGENT, "Icy-MetaData": "1", "Accept": "*/*"})
    return connection, connection.getresponse()


def _playlist_target(body: bytes) -> str:
    text = body.decode("utf-8", "replace")
    if "#EXT-X-" in text:
        raise OSError("HLS radio streams are not supported")
    for line in text.splitlines():
        line = line.strip()
        candidate = line.split("=", 1)[1].strip() if re.match(r"(?i)file[0-9]+=", line) else line
        if valid_url(candidate):
            return candidate
    raise OSError("The radio playlist contains no stream")


def open_stream(url: str, timeout: float = 15):
    """Open a station stream, following redirects and .m3u/.pls playlists; returns (connection, response)."""
    for _ in range(6):
        if not valid_url(url):
            raise OSError("Invalid radio stream address")
        connection, response = _connect(url, timeout)
        if response.status in {301, 302, 303, 307, 308} and response.getheader("Location"):
            url = urllib.parse.urljoin(url, response.getheader("Location"))
            connection.close()
            continue
        if response.status != 200:
            connection.close()
            raise OSError(f"The radio station returned HTTP {response.status}")
        kind = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
        if kind in PLAYLIST_TYPES or urllib.parse.urlsplit(url).path.lower().endswith((".m3u", ".m3u8", ".pls")):
            body = response.read(65536)
            connection.close()
            url = _playlist_target(body)
            continue
        if kind.startswith("text/") or kind.endswith(("html", "xml", "json")):
            connection.close()
            raise OSError("The radio station did not return audio")
        return connection, response
    raise OSError("The radio stream redirects too many times")


IMAGE_SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
                    (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"), (b"\x00\x00\x01\x00", "image/x-icon"))
MAX_IMAGE_BYTES = 2 * 1024 * 1024


def image_type(body: bytes) -> str | None:
    """Identify a station logo by its content (servers often mislabel favicons)."""
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return next((kind for signature, kind in IMAGE_SIGNATURES if body.startswith(signature)), None)


def fetch_image(url: str, timeout: float = 10) -> tuple[bytes, str]:
    for _ in range(4):
        if not valid_url(url):
            raise OSError("Invalid station logo address")
        parts = urllib.parse.urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        _allowed_address(parts.hostname, port)
        connection = (http.client.HTTPSConnection(parts.hostname, port, timeout=timeout,
                                                  context=ssl.create_default_context())
                      if parts.scheme == "https" else http.client.HTTPConnection(parts.hostname, port, timeout=timeout))
        try:
            connection.request("GET", (parts.path or "/") + (f"?{parts.query}" if parts.query else ""),
                               headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308} and response.getheader("Location"):
                url = urllib.parse.urljoin(url, response.getheader("Location"))
                continue
            body = response.read(MAX_IMAGE_BYTES + 1)
        finally:
            connection.close()
        kind = image_type(body)
        if response.status != 200 or kind is None or len(body) > MAX_IMAGE_BYTES:
            raise OSError("The station logo is unavailable")
        return body, kind
    raise OSError("The station logo redirects too many times")


def icy_title(metadata: bytes) -> str | None:
    text = metadata.rstrip(b"\0")
    try:
        decoded = text.decode("utf-8")
    except UnicodeDecodeError:
        decoded = text.decode("latin-1")
    match = re.search(r"StreamTitle='(.*?)';", decoded, re.S)
    return _text(match.group(1), 300) if match else None


def split_title(value: str) -> tuple[str, str]:
    """ICY titles are conventionally ``Artist - Title``; returns (title, artist)."""
    artist, separator, title = value.partition(" - ")
    return (title.strip(), artist.strip()) if separator and title.strip() and artist.strip() else (value.strip(), "")


def icy_chunks(response, read_size: int = 16384) -> Iterator[tuple[bytes, str | None]]:
    """Yield (audio bytes, title-or-None), removing interleaved ICY metadata blocks."""
    try:
        metaint = int(response.getheader("icy-metaint") or 0)
    except ValueError:
        metaint = 0
    if not 0 < metaint <= 1024 * 1024:
        metaint = 0
    remaining = metaint
    while True:
        chunk = response.read(min(read_size, remaining) if metaint else read_size)
        if not chunk:
            return
        if not metaint:
            yield chunk, None
            continue
        remaining -= len(chunk)
        yield chunk, None
        if remaining == 0:
            length = response.read(1)
            if not length:
                return
            size, metadata = length[0] * 16, b""
            while len(metadata) < size:
                part = response.read(size - len(metadata))
                if not part:
                    return
                metadata += part
            remaining = metaint
            if metadata:
                title = icy_title(metadata)
                if title is not None:
                    yield b"", title


# ----- Frame parsing -------------------------------------------------------------------

_MP3_BITRATES = {
    (3, 3): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (3, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (3, 1): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 3): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
_ADTS_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)


def frame_header(data: bytes | bytearray, offset: int = 0) -> tuple[str, int, int, int] | None:
    """Return (kind, frame bytes, samples, sample rate) for an MP3 or ADTS header at offset."""
    if len(data) - offset < 7 or data[offset] != 0xFF or data[offset + 1] & 0xE0 != 0xE0:
        return None
    b1, b2 = data[offset + 1], data[offset + 2]
    if b1 & 0xF6 == 0xF0:
        index = (b2 >> 2) & 0x0F
        length = ((data[offset + 3] & 3) << 11) | (data[offset + 4] << 3) | (data[offset + 5] >> 5)
        if index >= len(_ADTS_RATES) or length < 7:
            return None
        return "aac", length, 1024 * ((data[offset + 6] & 3) + 1), _ADTS_RATES[index]
    version, layer = (b1 >> 3) & 3, (b1 >> 1) & 3
    bitrate_index, rate_index = b2 >> 4, (b2 >> 2) & 3
    if version == 1 or layer == 0 or bitrate_index in (0, 15) or rate_index == 3:
        return None
    rate = _MP3_RATES[version][rate_index]
    bitrate = _MP3_BITRATES[(3, layer) if version == 3 else (2, 3 if layer == 3 else 2)][bitrate_index] * 1000
    padding = (b2 >> 1) & 1
    if layer == 3:
        return "mp3", (12 * bitrate // rate + padding) * 4, 384, rate
    samples = 576 if layer == 1 and version != 3 else 1152
    return "mp3", samples // 8 * bitrate // rate + padding, samples, rate


class FrameParser:
    """Split a byte stream into whole frames, resynchronising after junk or ID3 tags."""

    def __init__(self):
        self.buffer = bytearray()
        self.kind: str | None = None

    def feed(self, data: bytes) -> list[tuple[bytes, int, int]]:
        self.buffer += data
        frames, offset, buffer = [], 0, self.buffer
        while True:
            header = frame_header(buffer, offset)
            if header is None or (self.kind is not None and header[0] != self.kind):
                if len(buffer) - offset < 7:
                    break
                offset += 1
                continue
            kind, length, samples, rate = header
            end = offset + length
            if len(buffer) < end + 7:
                break
            following = frame_header(buffer, end)
            if following is None or following[0] != kind or following[3] != rate:
                # A false sync inside audio data; keep scanning.
                offset += 1
                continue
            self.kind = kind
            frames.append((bytes(buffer[offset:end]), samples, rate))
            offset = end
        del buffer[:offset]
        if len(buffer) > 1024 * 1024:
            del buffer[:-8192]
        return frames


class RadioBuffer:
    """Ingest one station into a ring of frames indexed from the start of the connection."""

    def __init__(self, url: str, opener: Callable[[str], tuple] = open_stream, keep: float = BUFFER_SECONDS):
        self.url, self.opener, self.keep = url, opener, keep
        self.cond = threading.Condition()
        self.frames: deque[bytes] = deque()
        self.first = 0
        self.frame_duration = 0.0
        self.titles: list[tuple[int, str]] = []
        self.error = ""
        self.ended = self.closed = False
        self.connection = None
        self.parser = FrameParser()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    @property
    def content_type(self) -> str:
        return "audio/aac" if self.parser.kind == "aac" else "audio/mpeg"

    @property
    def head(self) -> int:
        """Index of the next frame to arrive."""
        return self.first + len(self.frames)

    def buffered(self) -> float:
        with self.cond:
            return len(self.frames) * self.frame_duration

    def _run(self):
        parser = self.parser
        try:
            connection, response = self.opener(self.url)
            with self.cond:
                self.connection = connection
                if self.closed:
                    return
            for chunk, title in icy_chunks(response):
                frames = parser.feed(chunk) if chunk else []
                with self.cond:
                    if self.closed:
                        return
                    if title is not None:
                        self.titles.append((self.head, title))
                    for data, samples, rate in frames:
                        duration = samples / rate
                        if not self.frame_duration:
                            self.frame_duration = duration
                        elif abs(duration - self.frame_duration) > 1e-9:
                            raise OSError("The radio stream changed format")
                        self.frames.append(data)
                    excess = len(self.frames) - int(self.keep / self.frame_duration) if self.frame_duration else 0
                    for _ in range(max(0, excess)):
                        self.frames.popleft()
                        self.first += 1
                    while len(self.titles) > 1 and self.titles[1][0] <= self.first:
                        self.titles.pop(0)
                    if frames or title is not None:
                        self.cond.notify_all()
            raise OSError("The radio station ended the stream")
        except (OSError, http.client.HTTPException, ValueError, AttributeError) as error:
            # AttributeError: http.client races with close() while reading a chunked body.
            with self.cond:
                if not self.closed:
                    self.error = str(error) or "Radio stream unavailable"
        finally:
            with self.cond:
                self.ended = True
                self.cond.notify_all()
            self._disconnect()

    def _disconnect(self):
        connection = self.connection
        if connection is None:
            return
        try:
            if connection.sock is not None:
                connection.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        connection.close()

    def close(self):
        with self.cond:
            self.closed = True
            self.cond.notify_all()
        self._disconnect()

    def wait_ready(self, seconds: float, timeout: float) -> bool:
        """Wait until ``seconds`` of audio are buffered; False on timeout or failure."""
        with self.cond:
            self.cond.wait_for(lambda: self.ended or self.closed or (
                self.frame_duration and len(self.frames) * self.frame_duration >= seconds), timeout)
            return bool(not self.ended and not self.closed and self.frame_duration
                        and len(self.frames) * self.frame_duration >= seconds)

    def title_at(self, index: int) -> str:
        with self.cond:
            title = ""
            for start, value in self.titles:
                if start > index:
                    break
                title = value
            return title

    def live_index(self, lead: float) -> int:
        """Frame index ``lead`` seconds behind the live edge (clamped to the buffer)."""
        with self.cond:
            back = int(lead / self.frame_duration) if self.frame_duration else 0
            return max(self.first, self.head - back)

    def read(self, start: int, stop: Callable[[], bool] = lambda: False) -> Iterator[bytes]:
        """Yield audio from frame ``start`` onwards, following the live edge until closed."""
        index = start
        while True:
            with self.cond:
                self.cond.wait_for(lambda: self.closed or self.ended or index < self.head or stop(), 1.0)
                if self.closed or stop() or index < self.first:
                    return
                if index >= self.head:
                    if self.ended:
                        return
                    continue
                chunk = b"".join(self.frames[i - self.first] for i in range(index, self.head))
                index = self.head
            yield chunk
