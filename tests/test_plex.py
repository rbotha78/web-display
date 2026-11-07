import http.server
import json
import tempfile
import threading
import urllib.parse
import urllib.request
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from webdisplay.app import Application, ConfigStore
from webdisplay.carousel import validate_playlist
from webdisplay.plex import PlexClient, PlexPlaybackProxy, validate_server_url, validate_token
from webdisplay.music import MusicRelay, validate_music
from webdisplay.sleep import DAYS, default_schedule
from webdisplay.storage import atomic_write


class FakePlexHandler(http.server.BaseHTTPRequestHandler):
    sessions = []
    stopped = []
    identifiers = []
    profiles = []
    transcode_params = []
    timelines = []
    queues_created = 0
    queue_queries = []
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
            f'<Track ratingKey="{i}" key="/library/metadata/{i}" playQueueItemID="{i}" title="Track {i}" duration="5000">'
            f'<Media><Part key="/library/parts/{i}/1/file.mp3" /></Media></Track>'
            for i in range(max(1, center - 5), min(32, center + 5) + 1)
        )
        return (f'<MediaContainer playQueueID="{queue_id}" playQueueVersion="1" playQueueSelectedItemID="1"'
                f' playQueueTotalCount="32">{tracks}</MediaContainer>').encode()

    def do_POST(self):
        self.assert_token()
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/playQueues":
            self.send_error(404)
            return
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual_query(query, "type", "audio")
        self.assertEqual_query(query, "uri",
                               "server://machine-1/com.plexapp.plugins.library/library/sections/3/stations/1")
        self.__class__.queues_created += 1
        body = self.queue_xml(self.queues_created)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        self.assertEqual(validate_music({}), {"enabled": False, "station_key": "", "volume": 30})
        for value in ({"enabled": True}, {"volume": True}, {"volume": 101},
                      {"station_key": "/library/metadata/42"}, {"enabled": "true"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_music(value)

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
