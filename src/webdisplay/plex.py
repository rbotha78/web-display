from __future__ import annotations

import base64
import http.client
import logging
import platform
import re
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .carousel import DEFAULT_PLEX_BITRATE_KBPS, validate_plex_bitrate, validate_url

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_TOKEN_LENGTH = 512
MAX_XML_BYTES = 2 * 1024 * 1024
HLS_PROFILE_EXTRA = (
    "add-transcode-target(type=videoProfile&context=streaming&protocol=hls"
    "&container=mpegts&videoCodec=h264&audioCodec=aac)"
)
RATING_KEY = re.compile(r"[0-9]{1,20}\Z")
LIBRARY_KEY = re.compile(r"[0-9]{1,10}\Z")
METADATA_KEY = re.compile(r"/library/metadata/[0-9]{1,20}/children\Z")
STATION_KEY = re.compile(r"/library/sections/[0-9]{1,10}/stations/(?:1|2|3|8)\Z")
PLAYLIST_KEY = re.compile(r"/playlists/([0-9]{1,20})/items\Z")
MIX_KEY = re.compile(r"mix:(artist|album):([0-9]{1,20}(?:,[0-9]{1,20}){0,19})\Z")
ADVERTISED_MIX_KEY = re.compile(
    r"/library/(?:metadata/[0-9]{1,20}/station|sections/[0-9]{1,10}/stations)/"
    r"[A-Za-z0-9_-]{1,128}(?:\?type=(?:8|9|10))?\Z"
)
LOG = logging.getLogger(__name__)


def valid_music_source(key: str) -> bool:
    return any(pattern.fullmatch(key) for pattern in (
        STATION_KEY, PLAYLIST_KEY, MIX_KEY, ADVERTISED_MIX_KEY,
    ))


def validate_server_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Plex server URL must be a string")
    value = value.strip().rstrip("/")
    if not value:
        raise ValueError("Plex server URL must be an HTTP(S) server address")
    try:
        parsed = urllib.parse.urlsplit(validate_url(value))
    except ValueError:
        raise ValueError("Plex server URL must be an HTTP(S) server address") from None
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Plex server URL must not include a path, query, or fragment")
    return value


def validate_token(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TOKEN_LENGTH:
        raise ValueError("Plex token must contain 1-512 characters")
    if any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise ValueError("Plex token contains invalid characters")
    return value


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme, (parsed.hostname or "").lower(), parsed.port


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin: tuple[str, str, int | None]):
        self.origin = origin

    def redirect_request(self, request, fp, code, message, headers, new_url):
        if _origin(new_url) != self.origin:
            raise urllib.error.URLError("cross-origin Plex redirect rejected")
        return super().redirect_request(request, fp, code, message, headers, new_url)


class PlexClient:
    def __init__(
        self,
        server_url: str,
        token: str,
        client_identifier: str,
        device_name: str,
        version: str,
    ):
        self.server_url = validate_server_url(server_url)
        self.token = validate_token(token)
        self.client_identifier = client_identifier
        self.headers = {
            "Accept": "application/xml",
            "User-Agent": "Web Display",
            "X-Plex-Token": self.token,
            "X-Plex-Client-Identifier": client_identifier,
            "X-Plex-Device-Name": device_name,
            "X-Plex-Platform": "Generic",
            "X-Plex-Platform-Version": platform.release(),
            "X-Plex-Device": "Raspberry Pi",
            "X-Plex-Model": "Raspberry Pi",
            "X-Plex-Product": "Web Display",
            "X-Plex-Version": version,
        }
        handler = _SameOriginRedirect(_origin(self.server_url))
        self.opener = urllib.request.build_opener(handler)

    def url(self, path: str, query: dict[str, object] | None = None) -> str:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Invalid Plex path")
        url = self.server_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return url

    def open(self, path: str, query: dict[str, object] | None = None, timeout: int = 10,
             method: str = "GET", headers: dict[str, str] | None = None):
        request = urllib.request.Request(self.url(path, query), headers={**self.headers, **(headers or {})},
                                         method=method)
        response = self.opener.open(request, timeout=timeout)
        if _origin(response.geturl()) != _origin(self.server_url):
            response.close()
            raise ValueError("Plex server redirected to another host")
        return response

    def xml(self, path: str, query: dict[str, object] | None = None,
            method: str = "GET") -> ET.Element:
        try:
            with self.open(path, query, method=method) as response:
                data = response.read(MAX_XML_BYTES + 1)
                if len(data) > MAX_XML_BYTES:
                    raise ValueError("Plex response exceeds the size limit")
                return ET.fromstring(data)
        except (OSError, urllib.error.URLError, ET.ParseError, ValueError):
            raise ValueError("Could not read data from the Plex server") from None

    def identity(self) -> dict[str, str]:
        root = self.xml("/identity")
        machine_identifier = root.attrib.get("machineIdentifier", "")
        if not machine_identifier:
            raise ValueError("The configured server is not a Plex Media Server")
        return {
            "machine_identifier": machine_identifier,
            "name": root.attrib.get("friendlyName", "Plex Media Server"),
            "version": root.attrib.get("version", ""),
        }

    def like_track(self, rating_key: str) -> None:
        if not isinstance(rating_key, str) or not RATING_KEY.fullmatch(rating_key):
            raise ValueError("Invalid Plex track")
        try:
            with self.open("/:/rate", {
                "key": rating_key, "identifier": "com.plexapp.plugins.library", "rating": 10,
            }, method="PUT"):
                pass
        except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
            raise ValueError("Could not save the track rating in Plex; check the connection and permissions") from None

    def libraries(self) -> list[dict[str, str]]:
        root = self.xml("/library/sections")
        return [
            {
                "key": item.attrib["key"],
                "title": item.attrib.get("title", "Untitled library"),
                "type": item.attrib.get("type", ""),
            }
            for item in root.findall("Directory")
            if item.attrib.get("type") in {"movie", "show"}
            and LIBRARY_KEY.fullmatch(item.attrib.get("key", ""))
        ]

    def stations(self) -> list[dict[str, str]]:
        return [
            {key: item[key] for key in ("key", "title", "library")}
            for item in self._station_sources() if item["kind"] == "station"
        ]

    def _station_sources(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        seen: set[str] = set()
        for section in self.xml("/library/sections").findall("Directory"):
            key = section.get("key", "")
            if section.get("type") != "artist" or not LIBRARY_KEY.fullmatch(key):
                continue
            hubs = self.xml(f"/hubs/sections/{key}", {"includeStations": 1})
            for item in hubs.iter("Playlist"):
                source_key = item.get("key", "")
                supported = STATION_KEY.fullmatch(source_key) or ADVERTISED_MIX_KEY.fullmatch(source_key)
                if supported and source_key not in seen:
                    result.append({
                        "key": source_key, "title": item.get("title", "Radio"),
                        "library": section.get("title", "Music"),
                        "kind": "station" if STATION_KEY.fullmatch(source_key) else "mix",
                    })
                    seen.add(source_key)
        return result

    def music_sources(self) -> list[dict[str, object]]:
        result: list[dict[str, object]] = [dict(item) for item in self._station_sources()]
        seen = {item["key"] for item in result}
        for item in self.xml("/playlists", {"playlistType": "audio"}).findall("Playlist"):
            key = item.get("key", "")
            if item.get("playlistType") == "audio" and PLAYLIST_KEY.fullmatch(key) and key not in seen:
                result.append({
                    "key": key, "title": item.get("title", "Playlist"), "library": "Playlists",
                    "kind": "playlist", "smart": item.get("smart") == "1",
                })
                seen.add(key)
        return result

    def music_mix_items(self, key: str) -> list[dict[str, str]]:
        match = MIX_KEY.fullmatch(key)
        if match is None:
            raise ValueError("Invalid artist or album mix")
        kind, identifiers = match.groups()
        keys = identifiers.split(",")
        if len(set(keys)) != len(keys):
            raise ValueError("Mix selections must be unique")
        root = self.xml("/library/metadata/" + identifiers)
        items = {
            item.get("ratingKey"): {
                "id": item.get("ratingKey", ""), "title": item.get("title", "Untitled"),
                "type": kind, "artist": item.get("parentTitle", ""),
            }
            for item in root.findall("Directory") if item.get("type") == kind
        }
        if any(identifier not in items for identifier in keys):
            raise ValueError("Some selected artists or albums are no longer available")
        return [items[identifier] for identifier in keys]

    def validate_music_source(self, key: str) -> None:
        if MIX_KEY.fullmatch(key):
            self.music_mix_items(key)
        elif key not in {item["key"] for item in self.music_sources()}:
            raise ValueError("Select a music source available from this Plex server")

    def music_browse(self, library: str = "", kind: str = "artist", search: str = "",
                     offset: int = 0) -> dict[str, object]:
        if kind not in {"artist", "album"} or len(search) > 100 or not 0 <= offset <= 1000000:
            raise ValueError("Invalid music browse request")
        libraries = [
            {"id": item.get("key"), "title": item.get("title", "Music"), "type": "library"}
            for item in self.xml("/library/sections").findall("Directory")
            if item.get("type") == "artist" and LIBRARY_KEY.fullmatch(item.get("key", ""))
        ]
        if not library:
            return {"items": libraries, "total": len(libraries), "offset": 0}
        if library not in {item["id"] for item in libraries}:
            raise ValueError("Select a music library from this Plex server")
        query: dict[str, object] = {
            "type": 8 if kind == "artist" else 9, "sort": "titleSort",
            "X-Plex-Container-Start": offset, "X-Plex-Container-Size": 100,
        }
        if search:
            query["title"] = search
        root = self.xml(f"/library/sections/{library}/all", query)
        items = [
            {"id": item.get("ratingKey", ""), "title": item.get("title", "Untitled"),
             "type": kind, "artist": item.get("parentTitle", "")}
            for item in root.findall("Directory")
            if item.get("type") == kind and RATING_KEY.fullmatch(item.get("ratingKey", ""))
        ]
        total = root.get("totalSize", root.get("size", str(len(items))))
        if not total.isdigit():
            raise ValueError("Plex returned an invalid music result count")
        return {"items": items, "total": int(total), "offset": offset}

    def music_filters(self, library: str) -> dict[str, list[dict[str, str]]]:
        if not isinstance(library, str) or library not in {
            item["id"] for item in self.music_browse()["items"]
        }:
            raise ValueError("Select a music library from this Plex server")
        result = {}
        for field, media_type in (("decade", 9), ("genre", 10), ("mood", 10)):
            root = self.xml(f"/library/sections/{library}/{field}", {"type": media_type})
            result[field] = [
                {"id": item.get("key", ""), "title": item.get("title", "")}
                for item in root.findall("Directory")
                if RATING_KEY.fullmatch(item.get("key", "")) and item.get("title")
            ]
        return result

    def _music_filter_query(self, criteria: dict) -> tuple[str, dict[str, object]]:
        if set(criteria) - {"library", "decade", "genre", "mood"}:
            raise ValueError("Invalid smart playlist criteria")
        library = criteria.get("library", "")
        choices = self.music_filters(library)
        query: dict[str, object] = {"type": 10, "sort": "random"}
        for field, plex_field in (("decade", "album.decade"), ("genre", "track.genre"),
                                  ("mood", "track.mood")):
            value = criteria.get(field, "")
            if not isinstance(value, str):
                raise ValueError("Invalid smart playlist criteria")
            if value:
                if value not in {item["id"] for item in choices[field]}:
                    raise ValueError(f"Select a {field} available in this music library")
                query[plex_field] = value
        if len(query) == 2:
            raise ValueError("Select at least one decade, genre or mood")
        return library, query

    def _music_match_count(self, library: str, query: dict[str, object]) -> int:
        root = self.xml(f"/library/sections/{library}/all", {
            **query, "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 1,
        })
        count = root.get("totalSize", root.get("size", ""))
        if not count.isascii() or not count.isdigit():
            raise ValueError("Plex returned an invalid music result count")
        return int(count)

    def preview_smart_playlist(self, criteria: dict) -> dict[str, int]:
        library, query = self._music_filter_query(criteria)
        return {"count": self._music_match_count(library, query)}

    def create_smart_playlist(self, body: dict) -> dict[str, object]:
        title = body.get("title")
        if (not isinstance(title, str) or not title.strip() or len(title) > 100
                or any(ord(char) < 32 or ord(char) == 127 for char in title)):
            raise ValueError("Playlist name must contain 1-100 characters without control characters")
        title = title.strip()
        library, query = self._music_filter_query({key: value for key, value in body.items() if key != "title"})
        if not self._music_match_count(library, query):
            raise ValueError("No tracks match all selected criteria; change the filters before creating a playlist")
        playlists = self.xml("/playlists", {"playlistType": "audio"})
        if any(item.get("title", "").casefold() == title.casefold() for item in playlists.findall("Playlist")):
            raise ValueError("An audio playlist with this name already exists in Plex; choose another name")
        machine = self.identity()["machine_identifier"]
        uri = (f"server://{machine}/com.plexapp.plugins.library/library/sections/{library}/all?"
               + urllib.parse.urlencode(query))
        root = self.xml("/playlists", {"type": "audio", "smart": 1, "title": title, "uri": uri}, method="POST")
        item = root.find("Playlist")
        if (item is None or item.get("playlistType") != "audio" or item.get("smart") != "1"
                or not RATING_KEY.fullmatch(item.get("ratingKey", ""))):
            raise ValueError("Plex did not confirm smart playlist creation; check Plex before retrying")
        return {
            "key": f"/playlists/{item.get('ratingKey')}/items", "title": item.get("title", title),
            "library": "Playlists", "kind": "playlist", "smart": True,
        }

    def browse(self, key: str | None = None) -> list[dict[str, object]]:
        if key is None:
            return [
                {**item, "container": True}
                for item in self.libraries()
            ]
        if LIBRARY_KEY.fullmatch(key):
            path, query = f"/library/sections/{key}/all", None
        elif METADATA_KEY.fullmatch(key):
            path, query = key, None
        else:
            raise ValueError("Invalid Plex library location")
        root = self.xml(path, query)
        items: list[dict[str, object]] = []
        for child in root:
            rating_key = child.attrib.get("ratingKey", "")
            title = child.attrib.get("title", "Untitled")
            kind = child.attrib.get("type", "")
            if child.tag == "Directory" and RATING_KEY.fullmatch(rating_key):
                items.append({
                    "id": rating_key,
                    "title": title,
                    "type": kind or "collection",
                    "container": True,
                    "key": f"/library/metadata/{rating_key}/children",
                })
            elif child.tag == "Video" and RATING_KEY.fullmatch(rating_key):
                items.append({
                    "id": rating_key,
                    "title": title,
                    "type": kind or "video",
                    "container": False,
                    "duration": int(child.attrib["duration"]) if child.attrib.get("duration", "").isdigit() else None,
                })
        return items

    def transcode_url(
        self, rating_key: str, session: str, bitrate_kbps: int = DEFAULT_PLEX_BITRATE_KBPS,
    ) -> str:
        if not RATING_KEY.fullmatch(rating_key):
            raise ValueError("Invalid Plex media identifier")
        bitrate_kbps = validate_plex_bitrate(bitrate_kbps)
        return self.url(
            "/video/:/transcode/universal/start.m3u8",
            {
                "path": f"/library/metadata/{rating_key}",
                "mediaIndex": 0,
                "partIndex": 0,
                "protocol": "hls",
                "fastSeek": 1,
                "directPlay": 0,
                "directStream": 1,
                "videoQuality": 60,
                "videoResolution": "1280x720",
                "maxVideoBitrate": bitrate_kbps,
                "session": session,
                "X-Plex-Platform": self.headers["X-Plex-Platform"],
                "X-Plex-Platform-Version": self.headers["X-Plex-Platform-Version"],
                "X-Plex-Device": self.headers["X-Plex-Device"],
                "X-Plex-Model": self.headers["X-Plex-Model"],
                "X-Plex-Client-Profile-Extra": HLS_PROFILE_EXTRA,
            },
        )

    def stop_transcode(self, session: str) -> None:
        try:
            with self.open(
                "/video/:/transcode/universal/stop", {"session": session}, timeout=5,
            ):
                pass
        except (OSError, urllib.error.URLError, ValueError):
            LOG.warning("Could not stop a Plex transcoder session")


@dataclass
class _Playback:
    rating_key: str
    bitrate_kbps: int
    session: str = ""
    client: PlexClient | None = None


class PlexPlaybackProxy:
    """Loopback-only HLS relay; the Plex token is injected only upstream."""

    def __init__(self, client_factory: Callable[[], PlexClient]):
        self.client_factory = client_factory
        self._lock = threading.RLock()
        self._items: dict[str, _Playback] = {}
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_: object) -> None:
                pass

            def do_GET(self) -> None:
                if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                parsed = urllib.parse.urlsplit(self.path)
                parts = parsed.path.strip("/").split("/")
                if len(parts) == 3 and parts[0] == "play" and parts[2] == "start.m3u8":
                    owner._serve_start(self, parts[1])
                    return
                if len(parts) == 3 and parts[0] == "resource":
                    encoded = parts[2]
                    if encoded.endswith(".m3u8"):
                        encoded = encoded[:-5]
                    owner._serve_resource(self, parts[1], encoded)
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:
                if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                parts = urllib.parse.urlsplit(self.path).path.strip("/").split("/")
                if len(parts) == 2 and parts[0] == "stop":
                    owner._stop(self, parts[1])
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

        self._handler_class = Handler

    @property
    def address(self) -> str:
        if self.server is None:
            raise RuntimeError("Plex playback proxy has not started")
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self) -> None:
        if self.server is None:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class)
            self.server.daemon_threads = True
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        if self.thread is not None and not self.thread.is_alive():
            self.thread.start()

    def publish(
        self, rating_key: str, bitrate_kbps: int = DEFAULT_PLEX_BITRATE_KBPS,
    ) -> tuple[str, str]:
        self.start()
        capability = secrets.token_urlsafe(24)
        with self._lock:
            self._items[capability] = _Playback(
                rating_key, validate_plex_bitrate(bitrate_kbps),
            )
        return (
            f"{self.address}/play/{capability}/start.m3u8",
            f"{self.address}/stop/{capability}",
        )

    def _lookup(self, capability: str) -> _Playback | None:
        with self._lock:
            return self._items.get(capability)

    @staticmethod
    def _reply(handler: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)

    def _serve_start(self, handler: BaseHTTPRequestHandler, capability: str) -> None:
        item = self._lookup(capability)
        if item is None:
            self._reply(handler, HTTPStatus.NOT_FOUND, b"", "text/plain")
            return
        client = None
        session = ""
        try:
            client = self.client_factory()
            session = str(uuid.uuid4())
            target = urllib.parse.urlsplit(
                client.transcode_url(item.rating_key, session, item.bitrate_kbps)
            )
            response = client.open(target.path + "?" + target.query, timeout=30)
            with self._lock:
                item.session, item.client = session, client
            self._relay(handler, response, capability)
        except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
            if client is not None and session:
                client.stop_transcode(session)
            self._reply(handler, HTTPStatus.BAD_GATEWAY, b"Plex playback failed", "text/plain")

    def _decode_resource(self, encoded: str, client: PlexClient) -> tuple[str, str]:
        if len(encoded) > 8192:
            raise ValueError("Invalid Plex media resource")
        padded = encoded + "=" * (-len(encoded) % 4)
        url = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        parsed = urllib.parse.urlsplit(url)
        if parsed.username or parsed.password or _origin(url) != _origin(client.server_url):
            raise ValueError("Invalid Plex media resource")
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query = [(key, value) for key, value in query if key.lower() != "x-plex-token"]
        return parsed.path, urllib.parse.urlencode(query)

    def _serve_resource(self, handler: BaseHTTPRequestHandler, capability: str, encoded: str) -> None:
        item = self._lookup(capability)
        if item is None or item.client is None:
            self._reply(handler, HTTPStatus.NOT_FOUND, b"", "text/plain")
            return
        try:
            path, query = self._decode_resource(encoded, item.client)
            response = item.client.open(path + ("?" + query if query else ""))
            self._relay(handler, response, capability)
        except (OSError, urllib.error.URLError, http.client.HTTPException, UnicodeError, ValueError):
            self._reply(handler, HTTPStatus.BAD_GATEWAY, b"Plex media request failed", "text/plain")

    def _relay(self, handler: BaseHTTPRequestHandler, response, capability: str) -> None:
        with response:
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            location = response.geturl()
            if location.lower().split("?", 1)[0].endswith(".m3u8") or "mpegurl" in content_type.lower():
                data = response.read(MAX_MANIFEST_BYTES + 1)
                if len(data) > MAX_MANIFEST_BYTES:
                    self._reply(handler, HTTPStatus.BAD_GATEWAY, b"Plex playlist too large", "text/plain")
                    return
                try:
                    text = data.decode("utf-8-sig")
                    text = re.sub(
                        r'URI="([^"]+)"',
                        lambda match: f'URI="{self._local_resource(capability, match.group(1), location)}"',
                        text,
                    )
                    text = "\n".join(
                        self._local_resource(capability, line, location)
                        if line and not line.startswith("#") else line
                        for line in text.splitlines()
                    ) + "\n"
                    self._reply(handler, HTTPStatus.OK, text.encode("utf-8"), "application/vnd.apple.mpegurl")
                except (UnicodeError, ValueError):
                    self._reply(handler, HTTPStatus.BAD_GATEWAY, b"Invalid Plex playlist", "text/plain")
                return
            handler.send_response(HTTPStatus.OK)
            handler.send_header("Content-Type", content_type)
            length = response.headers.get("Content-Length")
            if length and length.isdigit():
                handler.send_header("Content-Length", length)
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            while chunk := response.read(64 * 1024):
                handler.wfile.write(chunk)

    def _local_resource(self, capability: str, value: str, base: str) -> str:
        target = urllib.parse.urljoin(base, value)
        parsed = urllib.parse.urlsplit(target)
        item = self._lookup(capability)
        if item is None:
            raise ValueError("Expired Plex playback")
        playback = item.client
        if (
            playback is None or parsed.username or parsed.password
            or _origin(target) != _origin(playback.server_url)
        ):
            raise ValueError("Plex playlist references another host")
        query = [
            (key, val)
            for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() != "x-plex-token"
        ]
        cleaned = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), ""))
        encoded = base64.urlsafe_b64encode(cleaned.encode()).decode().rstrip("=")
        suffix = ".m3u8" if parsed.path.lower().endswith(".m3u8") else ""
        return f"{self.address}/resource/{capability}/{encoded}{suffix}"

    def _stop(self, handler: BaseHTTPRequestHandler, capability: str) -> None:
        with self._lock:
            item = self._items.get(capability)
            if item is not None:
                client, session = item.client, item.session
                item.client = None
                item.session = ""
        if item is None:
            self._reply(handler, HTTPStatus.NOT_FOUND, b"", "text/plain")
            return
        if client is not None and session:
            client.stop_transcode(session)
        self._reply(handler, HTTPStatus.OK, b"{}", "application/json")

    def close(self) -> None:
        self.clear()
        if self.server is not None:
            if self.thread is not None and self.thread.is_alive():
                self.server.shutdown()
                self.thread.join()
            self.server.server_close()
            self.server = None
            self.thread = None

    def clear(self) -> None:
        with self._lock:
            items = list(self._items.values())
            self._items.clear()
        for item in items:
            if item.client is not None and item.session:
                item.client.stop_transcode(item.session)
