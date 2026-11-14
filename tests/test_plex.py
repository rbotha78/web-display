import http.server
import json
import tempfile
import threading
import urllib.parse
import urllib.request
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from webdisplay.app import Application, ConfigStore
from webdisplay.carousel import validate_playlist
from webdisplay.plex import PlexClient, PlexPlaybackProxy, validate_server_url, validate_token
from webdisplay.music import MusicRelay, validate_music
from webdisplay.sleep import DAYS, default_schedule
from webdisplay.storage import atomic_write

NO_DAYS = {"day_sources": {day: "" for day in DAYS}}


class FakePlexHandler(http.server.BaseHTTPRequestHandler):
    sessions = []
    stopped = []
    identifiers = []
    profiles = []
    transcode_params = []
    timelines = []
    queues_created = 0
    queue_queries = []
    created_queue_queries = []
    music_browse_queries = []
    created_playlist_queries = []
    rating_queries = []
    art_queries = []
    slow_music = None
    token = "private-test-token"

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.__class__.sessions = self.sessions
        self.assert_token()
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/identity":
            body = b'<MediaContainer friendlyName="Test Plex" machineIdentifier="machine-1" version="1" />'
            content_type = "application/xml"
        elif parsed.path == "/library/sections":
            body = b'<MediaContainer><Directory key="1" title="Movies" type="movie" /><Directory key="3" title="Music" type="artist" /></MediaContainer>'
            content_type = "application/xml"
        elif parsed.path == "/hubs/sections/3":
            self.assertEqual_query(urllib.parse.parse_qs(parsed.query), "includeStations", "1")
            body = (b'<MediaContainer><Hub><Playlist key="/library/sections/3/stations/1" title="Library Radio" />'
                    b'<Playlist key="/library/sections/3/stations/8" title="Deep Cuts Radio" />'
                    b'<Playlist key="/library/metadata/42/station/x" title="Not a library station" />'
                    b'</Hub></MediaContainer>')
            content_type = "application/xml"
        elif parsed.path == "/playlists":
            self.assertEqual_query(urllib.parse.parse_qs(parsed.query), "playlistType", "audio")
            body = (b'<MediaContainer>'
                    b'<Playlist key="/playlists/10/items" ratingKey="10" title="Saved mix" playlistType="audio" smart="0" />'
                    b'<Playlist key="/playlists/11/items" ratingKey="11" title="Favourites" playlistType="audio" smart="1" />'
                    b'<Playlist key="/playlists/12/items" ratingKey="12" title="Empty" playlistType="audio" smart="1" />'
                    b'<Playlist key="/playlists/13/items" title="Video" playlistType="video" />'
                    b'<Playlist key="https://other.invalid/private" title="Unsafe" playlistType="audio" />'
                    b'</MediaContainer>')
            content_type = "application/xml"
        elif parsed.path in ("/library/sections/3/decade", "/library/sections/3/genre", "/library/sections/3/mood"):
            field = parsed.path.rsplit("/", 1)[1]
            self.assertEqual_query(urllib.parse.parse_qs(parsed.query), "type", "9" if field == "decade" else "10")
            choices = {
                "decade": (("1990", "1990s"), ("2000", "2000s")),
                "genre": (("101", "Rock"), ("102", "R&B")),
                "mood": (("201", "Energetic"), ("202", "Unavailable mood")),
            }[field]
            root = ET.Element("MediaContainer")
            for key, title in choices:
                ET.SubElement(root, "Directory", key=key, title=title)
            body, content_type = ET.tostring(root), "application/xml"
        elif parsed.path == "/library/sections/3/all" and urllib.parse.parse_qs(parsed.query).get("type") == ["10"]:
            query = urllib.parse.parse_qs(parsed.query)
            self.music_browse_queries.append(query)
            self.assertEqual_query(query, "X-Plex-Container-Start", "0")
            self.assertEqual_query(query, "X-Plex-Container-Size", "1")
            count = 0 if query.get("track.mood") == ["202"] else 7
            body = f'<MediaContainer size="1" totalSize="{count}"><Track ratingKey="1" /></MediaContainer>'.encode()
            content_type = "application/xml"
        elif parsed.path == "/library/sections/3/all":
            query = urllib.parse.parse_qs(parsed.query)
            self.music_browse_queries.append(query)
            self.assertEqual_query(query, "X-Plex-Container-Size", "100")
            kind = "artist" if query["type"] == ["8"] else "album"
            base = 501 if kind == "artist" else 601
            body = (f'<MediaContainer size="2" totalSize="202">'
                    f'<Directory ratingKey="{base}" title="First {kind}" type="{kind}" parentTitle="Artist" />'
                    f'<Directory ratingKey="{base + 1}" title="Second {kind}" type="{kind}" parentTitle="Artist" />'
                    '</MediaContainer>').encode()
            content_type = "application/xml"
        elif parsed.path.startswith("/library/metadata/"):
            keys = parsed.path.rsplit("/", 1)[1].split(",")
            body = ('<MediaContainer>' + ''.join(
                f'<Directory ratingKey="{key}" title="Music {key}" type="{"artist" if int(key) < 600 else "album"}" />'
                for key in keys if key in {"501", "502", "601", "602"}
            ) + '</MediaContainer>').encode()
            content_type = "application/xml"
        elif parsed.path == "/:/timeline":
            self.timelines.append(urllib.parse.parse_qs(parsed.query))
            body, content_type = b"<MediaContainer />", "application/xml"
        elif parsed.path.startswith("/playQueues/"):
            query = urllib.parse.parse_qs(parsed.query)
            self.queue_queries.append(query)
            center = int(query["center"][0])
            body, content_type = self.queue_xml(int(parsed.path.rsplit("/", 1)[1]), center), "application/xml"
        elif parsed.path.startswith("/library/parts/"):
            if self.slow_music is not None:
                started, closed = self.slow_music
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", "131072")
                self.end_headers()
                self.wfile.write(b"x")
                self.wfile.flush()
                started.set()
                self.connection.settimeout(3)
                if self.connection.recv(1) == b"":
                    closed.set()
                return
            body, content_type = b"test music bytes", "audio/mpeg"
            if self.headers.get("Range") == "bytes=5-":
                body = body[5:]
                self.send_response(206)
                self.send_header("Content-Range", "bytes 5-15/16")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        elif parsed.path == "/photo/:/transcode":
            self.art_queries.append(urllib.parse.parse_qs(parsed.query))
            body, content_type = b"\xff\xd8album cover", "image/jpeg"
        elif parsed.path == "/library/sections/1/all":
            body = b'<MediaContainer><Video ratingKey="42" title="Example" type="movie" /></MediaContainer>'
            content_type = "application/xml"
        elif parsed.path == "/video/:/transcode/universal/start.m3u8":
            query = urllib.parse.parse_qs(parsed.query)
            self.sessions.append(query["session"][0])
            self.transcode_params.append(query)
            body = (
                b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin?X-Plex-Token=private-test-token"\n'
                b"#EXTINF:4,\nsegment.ts\n"
            )
            content_type = "application/vnd.apple.mpegurl"
        elif parsed.path.endswith("/segment.ts"):
            body = b"transport stream"
            content_type = "video/mp2t"
        elif parsed.path == "/video/:/transcode/universal/stop":
            self.stopped.append(urllib.parse.parse_qs(parsed.query)["session"][0])
            body = b"{}"
            content_type = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def assertEqual_query(query, key, expected):
        if query.get(key) != [expected]:
            raise AssertionError("missing station discovery option")

    @staticmethod
    def queue_xml(queue_id, center=1):
        tracks = "".join(
            f'<Track ratingKey="{i}" key="/library/metadata/{i}" playQueueItemID="{i}" title="Track {i}" '
            f'grandparentTitle="Album artist" originalTitle="Artist {i}" duration="5000" parentTitle="Album {i}"'
            + (f' parentThumb="/library/metadata/{1000 + i}/thumb/123"' if i % 2 else "") + '>'
            f'<Media><Part key="/library/parts/{i}/1/file.mp3" /></Media></Track>'
            for i in range(max(1, center - 5), min(32, center + 5) + 1)
        )
        return (f'<MediaContainer playQueueID="{queue_id}" playQueueVersion="1" playQueueSelectedItemID="1"'
                f' playQueueTotalCount="32">{tracks}</MediaContainer>').encode()

    def do_POST(self):
        self.assert_token()
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/playlists":
            query = urllib.parse.parse_qs(parsed.query)
            self.assertEqual_query(query, "smart", "1")
            self.assertEqual_query(query, "type", "audio")
            self.created_playlist_queries.append(query)
            root = ET.Element("MediaContainer")
            ET.SubElement(root, "Playlist", ratingKey="901", key="/playlists/901/items",
                          title=query["title"][0], playlistType="audio", smart="1")
            body = ET.tostring(root)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path != "/playQueues":
            self.send_error(404)
            return
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual_query(query, "type", "audio")
        self.created_queue_queries.append(query)
        uri = query["uri"][0]
        if uri.startswith("library:///directory/"):
            self.assertEqual_query(query, "shuffle", "1")
            self.assertEqual_query(query, "includeRelated", "1")
            if urllib.parse.unquote_plus(uri.removeprefix("library:///directory/")) not in (
                "/library/metadata/501,502", "/library/metadata/601,602",
            ):
                raise AssertionError("Invalid mix seed URI")
        elif "/playlists/" in uri:
            key = uri.rsplit("/playlists/", 1)[1].split("/")[0]
            self.assertEqual_query(query, "playlistID", key)
        else:
            self.assertEqual_query(query, "uri",
                                  "server://machine-1/com.plexapp.plugins.library/library/sections/3/stations/1")
        self.__class__.queues_created += 1
        body = b"<MediaContainer />" if uri.endswith("/playlists/12/items") else self.queue_xml(self.queues_created)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        self.assert_token()
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/:/rate":
            self.send_error(404)
            return
        self.rating_queries.append(urllib.parse.parse_qs(parsed.query))
        self.send_response(204)
        self.end_headers()

    def assert_token(self):
        if self.headers.get("X-Plex-Token") != self.token:
            self.send_error(401)
            raise AssertionError("missing Plex token header")
        self.identifiers.append(self.headers.get("X-Plex-Client-Identifier"))
        self.profiles.append((
            self.headers.get("X-Plex-Platform"),
            self.headers.get("X-Plex-Platform-Version"),
            self.headers.get("X-Plex-Device"),
            self.headers.get("X-Plex-Model"),
        ))


class PlexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakePlexHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def client(self):
        return PlexClient(self.url, FakePlexHandler.token, "stable-client-id-1234", "display", "1.0")

    def test_validators_reject_credentials_and_controls(self):
        self.assertEqual(validate_server_url(self.url + "/"), self.url)
        for value in ("", "http://user:pass@example.com", self.url + "/path", self.url + "?token=x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_server_url(value)
        with self.assertRaises(ValueError):
            validate_token("bad\nvalue")

    def test_like_track_sends_five_star_rating_and_sanitizes_plex_errors(self):
        client = self.client()
        FakePlexHandler.rating_queries = []
        client.like_track("42")
        self.assertEqual(FakePlexHandler.rating_queries, [{
            "key": ["42"], "identifier": ["com.plexapp.plugins.library"], "rating": ["10"],
        }])
        for key in ("", "42&rating=1", "/library/metadata/42", 42, None):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Invalid Plex track"):
                client.like_track(key)
        with patch.object(client, "open", side_effect=urllib.error.URLError(FakePlexHandler.token)):
            with self.assertRaisesRegex(ValueError, "Could not save the track rating") as error:
                client.like_track("42")
            self.assertNotIn(FakePlexHandler.token, str(error.exception))

    def test_playlist_stores_only_plex_rating_key_and_runtime_urls_must_be_local(self):
        playlist = {"enabled": False, "scenes": [{
            "id": "plex", "kind": "video", "source": "plex", "plex_rating_key": "42",
        }]}
        saved = validate_playlist(playlist)
        self.assertEqual(saved["scenes"][0]["url"], "")
        self.assertEqual(saved["scenes"][0]["plex_rating_key"], "42")
        runtime = {
            **playlist,
            "scenes": [{
                **playlist["scenes"][0],
                "plex_playback_url": "http://127.0.0.1:4321/play/cap/start.m3u8",
                "plex_stop_url": "http://127.0.0.1:4321/stop/cap",
            }],
        }
        self.assertEqual(validate_playlist(runtime, allow_runtime=True)["scenes"][0]["source"], "plex")
        runtime["scenes"][0]["plex_playback_url"] = "https://example.invalid/media"
        with self.assertRaises(ValueError):
            validate_playlist(runtime, allow_runtime=True)

    def test_client_identity_and_library_browse(self):
        client = self.client()
        self.assertEqual(client.identity()["name"], "Test Plex")
        self.assertEqual(client.browse(), [{
            "key": "1", "title": "Movies", "type": "movie", "container": True,
        }])
        self.assertEqual(client.browse("1"), [{
            "id": "42", "title": "Example", "type": "movie",
            "container": False, "duration": None,
        }])

    def test_station_discovery_uses_advertised_library_stations_only(self):
        self.assertEqual(self.client().stations(), [
            {"key": "/library/sections/3/stations/1", "title": "Library Radio", "library": "Music"},
            {"key": "/library/sections/3/stations/8", "title": "Deep Cuts Radio", "library": "Music"},
        ])

    def test_music_defaults_off_and_requires_valid_station_and_volume(self):
        self.assertEqual(validate_music({}), {"enabled": False, "station_key": "", "volume": 30, **NO_DAYS})
        for value in ({"enabled": True}, {"volume": True}, {"volume": 101},
                      {"station_key": "/library/metadata/42"}, {"enabled": "true"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_music(value)

    def test_music_sources_include_audio_playlists_and_advertised_mixes_only(self):
        sources = {item["key"]: item for item in self.client().music_sources()}
        self.assertEqual(sources["/library/sections/3/stations/1"]["kind"], "station")
        self.assertEqual(sources["/library/metadata/42/station/x"]["kind"], "mix")
        self.assertFalse(sources["/playlists/10/items"]["smart"])
        self.assertTrue(sources["/playlists/11/items"]["smart"])
        self.assertNotIn("/playlists/13/items", sources)
        self.assertNotIn("https://other.invalid/private", sources)
        for invalid in ("mix:track:1", "mix:artist:", "mix:artist:" + ",".join(["1"] * 21),
                        "/playlists/10/items?X-Plex-Token=secret", "/library/metadata/1/station/x?evil=1",
                        "https://other.invalid/playlists/10/items"):
            with self.subTest(key=invalid), self.assertRaises(ValueError):
                validate_music({"station_key": invalid})

    def test_music_browse_pages_searches_and_checks_mix_members(self):
        client = self.client()
        self.assertEqual(client.music_browse()["items"], [{"id": "3", "title": "Music", "type": "library"}])
        result = client.music_browse("3", "album", "First", 100)
        self.assertEqual(result["total"], 202)
        self.assertEqual(result["items"][0]["id"], "601")
        query = FakePlexHandler.music_browse_queries[-1]
        self.assertEqual(query["title"], ["First"])
        self.assertEqual(query["X-Plex-Container-Start"], ["100"])
        self.assertEqual(query["type"], ["9"])
        self.assertEqual([item["id"] for item in client.music_mix_items("mix:artist:502,501")], ["502", "501"])
        for key in ("mix:artist:501,999", "mix:artist:601", "mix:album:501", "mix:artist:501,501"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                client.validate_music_source(key)
        for key in ("/playlists/999/items", "/library/metadata/999/station/not-advertised"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                client.validate_music_source(key)
        with self.assertRaises(ValueError):
            client.music_browse("1")

    def test_playlist_and_custom_mix_queues_renew_with_the_same_source(self):
        for key in ("/playlists/10/items", "/playlists/11/items", "mix:artist:501,502", "mix:album:601,602"):
            with self.subTest(key=key):
                relay = MusicRelay(self.client)
                try:
                    relay.configure({"enabled": True, "station_key": key, "volume": 30}, "identity")
                    current = relay.control({"action": "current"})
                    first_query = FakePlexHandler.created_queue_queries[-1]
                    self.assertEqual(current["artist"], "Artist 1")
                    for _ in range(32):
                        current = relay.control({"action": "next", "track_id": current["track_id"]})
                    self.assertEqual(current["track_id"], "1")
                    self.assertEqual(FakePlexHandler.created_queue_queries[-1], first_query)
                    if key.startswith("mix:"):
                        self.assertEqual(first_query["shuffle"], ["1"])
                        self.assertEqual(first_query["includeRelated"], ["1"])
                    else:
                        self.assertEqual(first_query["playlistID"], [key.split("/")[2]])
                finally:
                    relay.close()

    def test_weekday_source_overrides_default_and_switches_at_next_song(self):
        saturday = datetime(2026, 11, 14, 23, 59, tzinfo=timezone.utc)
        sunday = datetime(2026, 11, 15, 0, 1, tzinfo=timezone.utc)
        settings = validate_music({"enabled": True, "station_key": "/playlists/11/items", "volume": 30,
                                   "day_sources": {"sunday": "mix:artist:501,502"}})
        relay = MusicRelay(self.client)
        try:
            relay.clock = lambda: saturday
            relay.configure(settings, "identity")
            current = relay.control({"action": "current"})
            self.assertEqual(FakePlexHandler.created_queue_queries[-1]["playlistID"], ["11"])
            created = len(FakePlexHandler.created_queue_queries)
            relay.clock = lambda: sunday
            # The day change neither restarts the song nor rebuilds on unrelated republication.
            relay.configure(dict(settings), "identity")
            self.assertEqual(relay.control({"action": "current"})["track_id"], current["track_id"])
            self.assertEqual(len(FakePlexHandler.created_queue_queries), created)
            relay.control({"action": "next", "track_id": current["track_id"]})
            self.assertEqual(len(FakePlexHandler.created_queue_queries), created + 1)
            self.assertEqual(FakePlexHandler.created_queue_queries[-1]["shuffle"], ["1"])
        finally:
            relay.close()

    def test_weekday_sources_validate_and_survive_clients_that_omit_them(self):
        for days in ([], {"funday": ""}, {"monday": "/library/metadata/1"}, {"monday": 3}):
            with self.subTest(days=days), self.assertRaises(ValueError):
                validate_music({"day_sources": days})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root / "config.json", root / "pairing", root / "target", Path("/bin/true"))
            try:
                app.save_plex_config({"server_url": self.url, "token": FakePlexHandler.token})
                days = {**NO_DAYS["day_sources"], "friday": "mix:album:601,602"}
                app.save_music_config({"enabled": True, "station_key": "/playlists/11/items",
                                       "volume": 20, "day_sources": days})
                with self.assertRaises(ValueError):
                    app.save_music_config({"enabled": True, "station_key": "/playlists/11/items", "volume": 20,
                                           "day_sources": {**days, "monday": "mix:artist:999"}})
                app.save_music_config({"enabled": True, "station_key": "/playlists/11/items", "volume": 25})
                self.assertEqual(app.config.load()["music"]["day_sources"], days)
                self.assertEqual(app.config.load()["music"]["volume"], 25)
                result = app.music_config()
                self.assertEqual(result["active_station_key"],
                                 days[result["active_day"]] or "/playlists/11/items")
            finally:
                app.close()

    def test_smart_playlist_filters_preview_and_persist_typed_match_all_query(self):
        client = self.client()
        self.assertEqual(client.music_filters("3")["decade"], [
            {"id": "1990", "title": "1990s"}, {"id": "2000", "title": "2000s"},
        ])
        criteria = {"library": "3", "decade": "1990", "genre": "102", "mood": "201"}
        self.assertEqual(client.preview_smart_playlist(criteria), {"count": 7})
        preview = FakePlexHandler.music_browse_queries[-1]
        self.assertEqual(preview["album.decade"], ["1990"])
        self.assertEqual(preview["track.genre"], ["102"])
        self.assertEqual(preview["track.mood"], ["201"])
        created = client.create_smart_playlist({**criteria, "title": "  90s R&B + Energy  "})
        self.assertEqual(created, {
            "key": "/playlists/901/items", "title": "90s R&B + Energy",
            "library": "Playlists", "kind": "playlist", "smart": True,
        })
        query = FakePlexHandler.created_playlist_queries[-1]
        uri = urllib.parse.urlsplit(query["uri"][0])
        self.assertEqual((uri.scheme, uri.netloc, uri.path),
                         ("server", "machine-1", "/com.plexapp.plugins.library/library/sections/3/all"))
        saved = urllib.parse.parse_qs(uri.query)
        self.assertEqual(saved, {
            "type": ["10"], "sort": ["random"], "album.decade": ["1990"],
            "track.genre": ["102"], "track.mood": ["201"],
        })
        self.assertNotIn("X-Plex-Token", query["uri"][0])
        self.assertNotIn("X-Plex-Container-Size", saved)
        self.assertEqual(client.preview_smart_playlist({"library": "3", "mood": "202"}), {"count": 0})

    def test_smart_playlist_rejects_invalid_empty_duplicate_or_no_match_rules_before_creating(self):
        client = self.client()
        before = len(FakePlexHandler.created_playlist_queries)
        for body in (
            {"library": "3", "title": "New"},
            {"library": "1", "title": "New", "genre": "101"},
            {"library": ["3"], "title": "New", "genre": "101"},
            {"library": "3", "title": "", "genre": "101"},
            {"library": "3", "title": "Bad\nname", "genre": "101"},
            {"library": "3", "title": "x" * 101, "genre": "101"},
            {"library": "3", "title": " favourites ", "genre": "101"},
            {"library": "3", "title": "New", "mood": "202"},
            {"library": "3", "title": "New", "decade": "1999"},
            {"library": "3", "title": "New", "genre": "unknown"},
            {"library": "3", "title": "New", "mood": True},
            {"library": "3", "title": "New", "genre": "101", "url": "https://other.invalid"},
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                client.create_smart_playlist(body)
        self.assertEqual(len(FakePlexHandler.created_playlist_queries), before)
        with patch.object(client, "xml", return_value=ET.fromstring('<MediaContainer size="invalid" />')):
            with self.assertRaisesRegex(ValueError, "invalid music result count"):
                client._music_match_count("3", {"type": 10})

    def test_empty_playlist_reports_an_actionable_error(self):
        relay = MusicRelay(self.client)
        try:
            relay.configure({"enabled": True, "station_key": "/playlists/12/items", "volume": 30}, "identity")
            request = urllib.request.Request(
                relay.url + "/control", json.dumps({"action": "current"}).encode(),
                {"Content-Type": "application/json"}, method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            with error.exception as response:
                self.assertIn("no playable tracks", json.load(response)["error"])
            self.assertEqual(relay.get_status()["state"], "error")
            self.assertIn("no playable tracks", relay.get_status()["error"])
        finally:
            relay.close()

    def test_new_music_sources_persist_and_invalid_selections_leave_settings_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root / "config.json", root / "pairing", root / "target", Path("/bin/true"))
            try:
                app.save_plex_config({"server_url": self.url, "token": FakePlexHandler.token})
                for key in ("/playlists/11/items", "mix:artist:501,502", "mix:album:601,602"):
                    settings = {"enabled": True, "station_key": key, "volume": 20}
                    app.save_music_config(settings)
                    self.assertEqual(app.config.load()["music"], {**settings, **NO_DAYS})
                    self.assertEqual(json.loads((root / "music.json").read_text())["station_key"], key)
                with self.assertRaises(ValueError):
                    app.save_music_config({"enabled": True, "station_key": "mix:artist:999", "volume": 20})
                self.assertEqual(app.config.load()["music"]["station_key"], "mix:album:601,602")
                app.save_music_config({"enabled": False, "station_key": "mix:album:601,602", "volume": 20})
                self.assertFalse(app.music_config()["enabled"])
            finally:
                app.close()

    def test_music_relay_ranges_queue_window_renewal_and_idempotent_next(self):
        FakePlexHandler.queues_created = 0
        FakePlexHandler.queue_queries = []
        FakePlexHandler.timelines = []
        relay = MusicRelay(self.client)
        try:
            runtime = relay.configure({"enabled": True, "station_key": "/library/sections/3/stations/1",
                                       "volume": 15}, "identity")
            self.assertNotIn(FakePlexHandler.token, json.dumps(runtime))
            control = lambda value: relay.control(value)
            current = control({"action": "current"})
            self.assertEqual((current["title"], current["artist"]), ("Track 1", "Artist 1"))
            status = relay.get_status()
            self.assertEqual((status["title"], status["artist"]), ("Track 1", "Artist 1"))
            self.assertEqual(current["album"], "Album 1")
            FakePlexHandler.art_queries = []
            with urllib.request.urlopen(current["art_url"]) as response:
                self.assertEqual((response.headers.get_content_type(), response.read()),
                                 ("image/jpeg", b"\xff\xd8album cover"))
            self.assertEqual(FakePlexHandler.art_queries[0]["url"], ["/library/metadata/1001/thumb/123"])
            self.assertTrue(current["art_url"].startswith(relay.url + "/art/"))
            self.assertEqual(FakePlexHandler.queues_created, 1)
            request = urllib.request.Request(current["url"], headers={"Range": "bytes=5-"})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), b"music bytes")
            for _ in range(31):
                previous = current
                control({"action": "timeline", "track_id": current["track_id"], "state": "playing", "time": 100})
                current = control({"action": "next", "track_id": previous["track_id"]})
                self.assertEqual(control({"action": "next", "track_id": previous["track_id"]}), current)
            self.assertEqual(current["track_id"], "32")
            self.assertEqual((current["title"], current["artist"]), ("Track 32", "Artist 32"))
            renewed = control({"action": "next", "track_id": "32"})
            self.assertEqual(renewed["track_id"], "1")
            self.assertEqual(FakePlexHandler.queues_created, 2)
            self.assertEqual(FakePlexHandler.queue_queries[-1]["center"], ["32"])
            self.assertEqual(FakePlexHandler.queue_queries[-1]["window"], ["5"])
            self.assertTrue(any(q.get("state") == ["stopped"] and q.get("time") == ["5000"]
                                for q in FakePlexHandler.timelines))
            old_url = renewed["url"]
            relay.configure({"enabled": False, "station_key": "/library/sections/3/stations/1",
                             "volume": 15}, "identity")
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(old_url)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        finally:
            relay.close()

    def test_sleep_stops_relay_upstream_queue_and_timeline_and_wake_creates_new_queue(self):
        FakePlexHandler.queues_created = 0
        FakePlexHandler.timelines = []
        with tempfile.TemporaryDirectory(dir=".") as directory:
            sleep_path = Path(directory) / "sleep.json"
            schedule = default_schedule()
            atomic_write(sleep_path, json.dumps({**schedule, "wake_until": None}))
            relay = MusicRelay(self.client, sleep_path)
            try:
                relay.configure({"enabled": True, "station_key": "/library/sections/3/stations/1",
                                 "volume": 30}, "identity")
                current = relay.control({"action": "current"})
                self.assertEqual(FakePlexHandler.queues_created, 1)
                upstream = Mock()
                with relay.streams_lock:
                    relay.streams.add(upstream)
                stopped = threading.Event()
                original_stop = relay._stop

                def stop():
                    original_stop()
                    stopped.set()

                for day in DAYS:
                    schedule["days"][day] = {"enabled": True, "start": "00:00", "end": "23:59"}
                with patch.object(relay, "_stop", side_effect=stop), \
                     patch("webdisplay.sleep.time.time", return_value=1793660400):
                    atomic_write(sleep_path, json.dumps({**schedule, "wake_until": None}))
                    self.assertTrue(stopped.wait(2), "Sleep monitor did not stop the upstream relay")
                    self.assertIsNone(relay.queue)
                    self.assertIsNone(relay.track)
                    upstream.fp.raw._sock.shutdown.assert_called_once()
                    upstream.close.assert_called_once()
                    self.assertEqual(relay.get_status()["state"], "sleeping")
                    self.assertTrue(any(t["state"] == ["stopped"] for t in FakePlexHandler.timelines))
                    with self.assertRaises(ValueError):
                        relay.control({"action": "current"})
                    self.assertEqual(FakePlexHandler.queues_created, 1)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(current["url"])
                    self.assertEqual(error.exception.code, 404)
                    error.exception.close()
                    atomic_write(sleep_path, json.dumps({**schedule, "wake_until": 1793664000}))
                    renewed = relay.control({"action": "current"})
                    self.assertEqual(FakePlexHandler.queues_created, 2)
                    self.assertNotEqual(renewed["url"], current["url"])
                    self.assertTrue(relay.settings["enabled"])
            finally:
                relay.close()

    def test_sleep_interrupts_a_live_stalled_upstream_audio_read(self):
        started, closed = threading.Event(), threading.Event()
        FakePlexHandler.slow_music = (started, closed)
        with tempfile.TemporaryDirectory(dir=".") as directory:
            sleep_path = Path(directory) / "sleep.json"
            schedule = default_schedule()
            atomic_write(sleep_path, json.dumps({**schedule, "wake_until": None}))
            relay = MusicRelay(self.client, sleep_path)
            response = None
            try:
                relay.configure({"enabled": True, "station_key": "/library/sections/3/stations/1",
                                 "volume": 30}, "identity")
                current = relay.control({"action": "current"})
                response = urllib.request.urlopen(current["url"], timeout=3)
                self.assertTrue(started.wait(1))
                for day in DAYS:
                    schedule["days"][day] = {"enabled": True, "start": "00:00", "end": "23:59"}
                with patch("webdisplay.sleep.time.time", return_value=1793660400):
                    atomic_write(sleep_path, json.dumps({**schedule, "wake_until": None}))
                    self.assertTrue(closed.wait(2), "The upstream audio socket remained open during sleep")
            finally:
                if response is not None:
                    response.close()
                relay.close()
                FakePlexHandler.slow_music = None

    def test_music_application_settings_private_and_independent_of_playlist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root / "config.json", root / "pairing", root / "target", Path("/bin/true"))
            try:
                self.assertFalse(app.music_config()["enabled"])
                app.save_plex_config({"server_url": self.url, "token": FakePlexHandler.token})
                app.save_music_config({"enabled": True, "station_key": "/library/sections/3/stations/1",
                                       "volume": 20})
                published = (root / "music.json").read_text()
                self.assertNotIn(FakePlexHandler.token, published)
                self.assertNotIn(self.url, published)
                app.music.control({"action": "current"})
                queue = app.music.queue
                app.save_playlist({"enabled": False, "scenes": []})
                self.assertIs(app.music.queue, queue)
                app.save_music_config({"enabled": True, "station_key": "/library/sections/3/stations/1",
                                       "volume": 50})
                self.assertIs(app.music.queue, queue)
                app.save_plex_config({"clear_token": True})
                self.assertFalse(app.music_config()["enabled"])
                self.assertIsNone(app.music.queue)
                with self.assertRaises(ValueError):
                    app.save_music_config({"enabled": True, "station_key": "/library/sections/3/stations/2",
                                           "volume": 20})
            finally:
                app.close()

    def test_admin_transport_preserves_pause_across_publication_and_stop_keeps_saved_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root / "config.json", root / "pairing", root / "target", Path("/bin/true"))
            try:
                app.save_plex_config({"server_url": self.url, "token": FakePlexHandler.token})
                settings = {"enabled": True, "station_key": "/playlists/11/items", "volume": 20}
                app.save_music_config(settings)
                current = app.music.control({"action": "current"})
                queue = app.music.queue
                capability = app.music.capability
                paused = app.control_music({"action": "pause"})
                self.assertEqual(paused["status"]["state"], "paused")
                self.assertTrue(paused["status"]["paused_by_user"])
                self.assertEqual(app.config.load()["music"], {**settings, **NO_DAYS})
                app.save_playlist({"enabled": False, "scenes": []})
                runtime = json.loads((root / "music.json").read_text())
                self.assertTrue(runtime["paused"])
                self.assertEqual(app.music.capability, capability)
                self.assertIs(app.music.queue, queue)
                self.assertEqual((root / "music.json").stat().st_mode & 0o777, 0o640)
                app.control_music({"action": "skip"})
                runtime = json.loads((root / "music.json").read_text())
                self.assertEqual(runtime["skip"]["track_id"], current["track_id"])
                self.assertTrue(runtime["skip"]["request_id"])
                app.control_music({"action": "resume"})
                self.assertFalse(json.loads((root / "music.json").read_text())["paused"])
                app.control_music({"action": "stop"})
                self.assertIsNone(app.music.queue)
                self.assertEqual(app.config.load()["music"], {**settings, **NO_DAYS, "enabled": False})
                self.assertFalse(json.loads((root / "music.json").read_text())["enabled"])
                with self.assertRaisesRegex(ValueError, "Enable background music"):
                    app.control_music({"action": "pause"})
                app.control_music({"action": "play"})
                self.assertEqual(app.config.load()["music"], {**settings, **NO_DAYS})
                self.assertFalse(app.music.user_paused)
                self.assertIsNone(json.loads((root / "music.json").read_text())["skip"])
                for value in ({"action": []}, {"action": "next"}, {"action": "pause", "url": "http://other.invalid"}):
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        app.control_music(value)
                with self.assertRaisesRegex(ValueError, "No current song"):
                    app.control_music({"action": "skip"})
                with patch.object(app.music, "sleeping", return_value=True):
                    with self.assertRaisesRegex(ValueError, "sleeping"):
                        app.control_music({"action": "resume"})
                self.assertNotIn(FakePlexHandler.token, (root / "music.json").read_text())
                self.assertNotIn("control_url", json.dumps(paused))
            finally:
                app.close()

    def test_liking_targets_current_song_and_preserves_playback_and_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root / "config.json", root / "pairing", root / "target", Path("/bin/true"))
            try:
                app.save_plex_config({"server_url": self.url, "token": FakePlexHandler.token})
                settings = {"enabled": True, "station_key": "/playlists/11/items", "volume": 20}
                app.save_music_config(settings)
                with self.assertRaisesRegex(ValueError, "No current song"):
                    app.like_music({"rating_key": "1"})
                current = app.music.control({"action": "current"})
                queue = app.music.queue
                app.control_music({"action": "pause"})
                runtime = (root / "music.json").read_bytes()
                FakePlexHandler.rating_queries = []
                with patch.object(app.music.client, "like_track", side_effect=ValueError("Plex unavailable")):
                    with self.assertRaisesRegex(ValueError, "Plex unavailable"):
                        app.like_music({"rating_key": "1"})
                self.assertFalse(app.music_config()["status"]["liked"])
                liked = app.like_music({"rating_key": "1"})
                self.assertTrue(liked["status"]["liked"])
                self.assertTrue(liked["status"]["paused_by_user"])
                self.assertEqual(liked["status"]["rating_key"], "1")
                self.assertIs(app.music.queue, queue)
                self.assertEqual(app.config.load()["music"], {**settings, **NO_DAYS})
                self.assertEqual((root / "music.json").read_bytes(), runtime)
                self.assertNotIn("control_url", json.dumps(liked))
                self.assertNotIn(FakePlexHandler.token, json.dumps(liked))
                app.music.control({"action": "next", "track_id": current["track_id"]})
                self.assertFalse(app.music_config()["status"]["liked"])
                with self.assertRaisesRegex(ValueError, "song has changed"):
                    app.like_music({"rating_key": "1"})
                for value in ({}, {"rating_key": []}, {"rating_key": "2", "rating": 1}):
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        app.like_music(value)
                with patch.object(app.music, "sleeping", return_value=True):
                    with self.assertRaisesRegex(ValueError, "No current song"):
                        app.like_music({"rating_key": "2"})
                app.music.track.set("userRating", "10.0")
                self.assertTrue(app.music_config()["status"]["liked"])
                app.control_music({"action": "stop"})
                self.assertNotIn("rating_key", app.music_config()["status"])
                with self.assertRaisesRegex(ValueError, "No current song"):
                    app.like_music({"rating_key": "2"})
                self.assertEqual(len(FakePlexHandler.rating_queries), 1)
            finally:
                app.close()

    def test_proxy_keeps_token_out_of_stream_urls_and_stops_session(self):
        FakePlexHandler.sessions = []
        FakePlexHandler.stopped = []
        FakePlexHandler.identifiers = []
        FakePlexHandler.profiles = []
        FakePlexHandler.transcode_params = []
        proxy = PlexPlaybackProxy(self.client)
        try:
            playback_url, stop_url = proxy.publish("42")
            self.assertNotIn(FakePlexHandler.token, playback_url)
            with urllib.request.urlopen(playback_url, timeout=5) as response:
                manifest = response.read().decode()
            self.assertNotIn(FakePlexHandler.token, manifest)
            segment_url = next(line for line in manifest.splitlines() if line.startswith("http"))
            with urllib.request.urlopen(segment_url, timeout=5) as response:
                self.assertEqual(response.read(), b"transport stream")
            request = urllib.request.Request(stop_url, method="POST")
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
            with urllib.request.urlopen(playback_url, timeout=5) as response:
                self.assertNotIn(FakePlexHandler.token.encode(), response.read())
            request = urllib.request.Request(stop_url, method="POST")
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(len(FakePlexHandler.sessions), 2)
            self.assertEqual(FakePlexHandler.stopped, FakePlexHandler.sessions)
            self.assertEqual(set(FakePlexHandler.identifiers), {"stable-client-id-1234"})
            self.assertTrue(all(all(profile) for profile in FakePlexHandler.profiles))
            query = FakePlexHandler.transcode_params[0]
            self.assertEqual(query["maxVideoBitrate"], ["4000"])
            self.assertEqual(query["X-Plex-Platform"], ["Generic"])
            self.assertTrue(query["X-Plex-Platform-Version"][0])
            self.assertEqual(query["X-Plex-Device"], ["Raspberry Pi"])
            self.assertEqual(query["X-Plex-Model"], ["Raspberry Pi"])
            self.assertEqual(query["X-Plex-Client-Profile-Extra"], [
                "add-transcode-target(type=videoProfile&context=streaming&protocol=hls"
                "&container=mpegts&videoCodec=h264&audioCodec=aac)"
            ])
        finally:
            proxy.close()

    def test_proxy_uses_per_scene_bitrate_override(self):
        FakePlexHandler.transcode_params = []
        proxy = PlexPlaybackProxy(self.client)
        try:
            playback_url, _ = proxy.publish("42", 6500)
            with urllib.request.urlopen(playback_url, timeout=5):
                pass
            self.assertEqual(
                FakePlexHandler.transcode_params[0]["maxVideoBitrate"], ["6500"],
            )
        finally:
            proxy.close()

    def test_config_client_identifier_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigStore(Path(directory) / "config.json")
            identifier = config.ensure_plex_client_identifier()
            self.assertEqual(config.ensure_plex_client_identifier(), identifier)
            self.assertEqual(config.load()["plex_client_identifier"], identifier)
            raw = json.loads(config.path.read_text())
            self.assertEqual(raw["plex_client_identifier"], identifier)

    def test_application_persists_identity_and_publishes_only_scoped_urls(self):
        FakePlexHandler.sessions = []
        FakePlexHandler.stopped = []
        FakePlexHandler.identifiers = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(
                root / "config.json", root / "pairing", root / "target", Path("/bin/true"),
            )
            try:
                app.save_plex_config({
                    "server_url": self.url,
                    "token": FakePlexHandler.token,
                    "default_bitrate_kbps": 6000,
                })
                identifier = app.plex_client_identifier
                self.assertEqual(app.plex_config(), {
                    "server_url": self.url, "configured": True, "default_bitrate_kbps": 6000,
                })
                playlist = {
                    "enabled": False,
                    "scenes": [
                        {
                            "id": "plex-scene", "kind": "video", "source": "plex",
                            "plex_rating_key": "42", "duration": 300, "plex_bitrate_kbps": 7500,
                        },
                        {
                            "id": "plex-default", "kind": "video", "source": "plex",
                            "plex_rating_key": "43", "duration": 300,
                        },
                    ],
                }
                app.save_playlist(playlist)
                published = (root / "playlist.json").read_text()
                self.assertNotIn(FakePlexHandler.token, published)
                self.assertIn("plex_playback_url", published)
                runtime = json.loads(published)["scenes"][0]
                self.assertEqual(runtime["plex_bitrate_kbps"], 7500)
                with urllib.request.urlopen(runtime["plex_playback_url"], timeout=5) as response:
                    self.assertNotIn(FakePlexHandler.token.encode(), response.read())
                self.assertEqual(FakePlexHandler.transcode_params[-1]["maxVideoBitrate"], ["7500"])
                request = urllib.request.Request(runtime["plex_stop_url"], method="POST")
                with urllib.request.urlopen(request, timeout=5):
                    pass
                runtime_default = json.loads(published)["scenes"][1]
                self.assertEqual(runtime_default["plex_bitrate_kbps"], 6000)
                self.assertIsNone(
                    app.config.load()["playlist"]["scenes"][1]["plex_bitrate_kbps"]
                )
                with urllib.request.urlopen(runtime_default["plex_playback_url"], timeout=5):
                    pass
                self.assertEqual(FakePlexHandler.transcode_params[-1]["maxVideoBitrate"], ["6000"])
                request = urllib.request.Request(runtime_default["plex_stop_url"], method="POST")
                with urllib.request.urlopen(request, timeout=5):
                    pass
                first_session = FakePlexHandler.sessions[-1]
                app.close()
                restarted = Application(
                    root / "config.json", root / "pairing", root / "target", Path("/bin/true"),
                )
                try:
                    self.assertEqual(restarted.plex_client_identifier, identifier)
                    self.assertEqual(restarted.config.load()["plex_token"], FakePlexHandler.token)
                    runtime = json.loads((root / "playlist.json").read_text())["scenes"][0]
                    with urllib.request.urlopen(runtime["plex_playback_url"], timeout=5):
                        pass
                    self.assertEqual(
                        FakePlexHandler.transcode_params[-1]["maxVideoBitrate"], ["7500"],
                    )
                    self.assertNotEqual(FakePlexHandler.sessions[-1], first_session)
                    self.assertEqual(set(FakePlexHandler.identifiers), {identifier})
                finally:
                    restarted.close()
                self.assertEqual(FakePlexHandler.stopped, FakePlexHandler.sessions)
            finally:
                app.close()

    def test_proxy_rejects_nonlocal_playlist_references(self):
        proxy = PlexPlaybackProxy(self.client)
        try:
            playback_url, _ = proxy.publish("42")
            with urllib.request.urlopen(playback_url, timeout=5) as response:
                response.read()
            capability = urllib.parse.urlsplit(playback_url).path.split("/")[2]
            with self.assertRaises(ValueError):
                proxy._local_resource(capability, "https://example.invalid/segment.ts", self.url + "/start.m3u8")
        finally:
            proxy.close()


if __name__ == "__main__":
    unittest.main()
