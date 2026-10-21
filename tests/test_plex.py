import http.server
import json
import tempfile
import threading
import urllib.parse
import urllib.request
import unittest
from pathlib import Path

from webdisplay.app import Application, ConfigStore
from webdisplay.carousel import validate_playlist
from webdisplay.plex import PlexClient, PlexPlaybackProxy, validate_server_url, validate_token


class FakePlexHandler(http.server.BaseHTTPRequestHandler):
    sessions = []
    stopped = []
    identifiers = []
    profiles = []
    transcode_params = []
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
            body = b'<MediaContainer><Directory key="1" title="Movies" type="movie" /></MediaContainer>'
            content_type = "application/xml"
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
