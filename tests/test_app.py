import json
import http.client
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from webdisplay import app as webdisplay_app
from webdisplay.app import (
    Application,
    ConfigStore,
    LoginThrottle,
    SessionStore,
    hash_password,
    make_handler,
    static_file,
    validate_url,
    verify_password,
)
from webdisplay.kiosk import screen_size
from http.server import ThreadingHTTPServer
from webdisplay.sleep import default_schedule


class ConfigTests(unittest.TestCase):
    def test_wake_survives_application_restart_and_republishes_the_same_deadline(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            root = Path(directory)
            paths = (root / "config", root / "pairing", root / "target", Path("/bin/true"))
            first = Application(*paths)
            schedule = default_schedule()
            schedule["days"]["monday"]["enabled"] = True
            try:
                first.save_sleep_config(schedule)
                deadline = first.wake({"duration_minutes": 60})["wake_until"]
            finally:
                first.close()
            second = Application(*paths)
            try:
                status = second.sleep_config()
                self.assertEqual(status["wake_until"], deadline)
                self.assertEqual(status["days"], schedule["days"])
                published = json.loads((root / "sleep.json").read_text())
                self.assertEqual(published, {**schedule, "wake_until": deadline})
                self.assertFalse(status["sleeping"])
            finally:
                second.close()

    def test_sleep_migration_private_persistence_and_invalid_update_is_atomic(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            path = Path(directory) / "config.json"
            store = ConfigStore(path)
            self.assertEqual(store.load()["sleep"], default_schedule())
            path.write_text('{"url": "https://example.com", "password": null}')
            self.assertIsNone(store.load()["sleep_wake_until"])
            self.assertEqual(store.load()["sleep"], default_schedule())
            schedule = default_schedule()
            schedule["days"]["sunday"]["enabled"] = True
            store.update(sleep=schedule, sleep_wake_until=1793650000)
            reloaded = ConfigStore(path).load()
            self.assertEqual(reloaded["sleep"], schedule)
            self.assertEqual(reloaded["sleep_wake_until"], 1793650000)
            self.assertEqual(reloaded["url"], "https://example.com")
            before = path.read_bytes()
            schedule["days"]["sunday"]["end"] = "22:00"
            with self.assertRaises(ValueError):
                store.update(sleep=schedule)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_safe_area_defaults_migrates_and_persists_without_accepting_invalid_values(self):
        with tempfile.TemporaryDirectory(dir=".") as directory:
            path = Path(directory) / "config.json"
            store = ConfigStore(path)
            self.assertEqual(store.load()["safe_area_percent"], 5)
            path.write_text('{"url": ""}')
            self.assertEqual(store.load()["safe_area_percent"], 5)
            for percent in (0, 10, 5):
                store.update(safe_area_percent=percent)
                self.assertEqual(ConfigStore(path).load()["safe_area_percent"], percent)
            for value in (-1, 11, True, 5.0, "5", None):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    store.update(safe_area_percent=value)
            self.assertEqual(store.load()["safe_area_percent"], 5)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_atomic_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            store = ConfigStore(path)
            store.update(url="https://example.com/display")
            self.assertEqual(store.load()["url"], "https://example.com/display")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())["url"], "https://example.com/display")

    def test_url_validation(self):
        self.assertEqual(validate_url(""), "")
        self.assertEqual(validate_url(" http://example.com/a "), "http://example.com/a")
        for value in ("example.com", "file:///etc/passwd", "https://user:pass@example.com"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_url(value)

    def test_plex_default_bitrate_is_persisted_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory) / "config.json")
            self.assertEqual(store.load()["plex_default_bitrate_kbps"], 4000)
            store.update(plex_default_bitrate_kbps=6500)
            self.assertEqual(store.load()["plex_default_bitrate_kbps"], 6500)
            for value in (499, 20001, True, 6500.0):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    store.update(plex_default_bitrate_kbps=value)

    def test_raildata_key_is_validated_and_stored_privately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            store = ConfigStore(path)
            store.update(raildata_api_key="test-key")
            self.assertEqual(store.load()["raildata_api_key"], "test-key")
            store.update(service_details_api_key="details-key")
            self.assertEqual(store.load()["service_details_api_key"], "details-key")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                store.update(service_details_api_key="bad key")


class AuthenticationTests(unittest.TestCase):
    def test_password_hash(self):
        stored = hash_password("a secure password")
        self.assertTrue(verify_password("a secure password", stored))
        self.assertFalse(verify_password("wrong password", stored))
        self.assertNotIn("a secure password", json.dumps(stored))

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError):
            hash_password("short")

    def test_sessions_expire(self):
        sessions = SessionStore()
        token, _ = sessions.create()
        self.assertIsNotNone(sessions.get(token))
        with patch("webdisplay.app.time.time", return_value=10**12):
            self.assertIsNone(sessions.get(token))

    def test_login_throttle(self):
        throttle = LoginThrottle(limit=2)
        self.assertTrue(throttle.allowed("client"))
        throttle.failure("client")
        throttle.failure("client")
        self.assertFalse(throttle.allowed("client"))
        throttle.success("client")
        self.assertTrue(throttle.allowed("client"))


class ApiTests(unittest.TestCase):
    def test_sleep_api_requires_auth_csrf_validates_and_publishes_private_runtime(self):
        schedule = default_schedule()
        schedule["days"]["monday"]["enabled"] = True
        for method, route, body in (
            ("GET", "/api/sleep", None), ("PUT", "/api/sleep", schedule),
            ("POST", "/api/sleep/wake", {"duration_minutes": 15}),
        ):
            response, _ = self.request(method, route, body)
            self.assertEqual(response.status, 401)
        token, session = self.app.sessions.create()
        cookie = {"Cookie": f"session={token}"}
        for method, route, body in (
            ("PUT", "/api/sleep", schedule), ("POST", "/api/sleep/wake", {"duration_minutes": 15}),
        ):
            response, _ = self.request(method, route, body, cookie)
            self.assertEqual(response.status, 403)
        headers = {**cookie, "X-CSRF-Token": session.csrf}
        response, status = self.request("PUT", "/api/sleep", schedule, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(status["days"], schedule["days"])
        self.assertTrue({"sleeping", "scheduled_sleep", "wake_until", "device_time", "timezone"} <= status.keys())
        for duration in (15, 30, 60, 120, 240):
            before = webdisplay_app.time.time()
            response, status = self.request("POST", "/api/sleep/wake", {"duration_minutes": duration}, headers)
            self.assertEqual(response.status, 200)
            self.assertGreaterEqual(status["wake_until"], before + duration * 60)
            self.assertFalse(status["sleeping"])
        deadline = self.app.config.load()["sleep_wake_until"]
        self.app.publish_target()
        runtime_path = self.app.target_path.with_name("sleep.json")
        runtime = json.loads(runtime_path.read_text())
        self.assertEqual(runtime, {**schedule, "wake_until": deadline})
        self.assertEqual(runtime_path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(ConfigStore(self.app.config.path).load()["sleep_wake_until"], deadline)
        for duration in (True, 15.0, "15", 1, -1, None):
            response, _ = self.request("POST", "/api/sleep/wake", {"duration_minutes": duration}, headers)
            self.assertEqual(response.status, 400)
        response, _ = self.request("PUT", "/api/sleep", {"days": {}}, headers)
        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(runtime_path.read_text()), runtime)
        response, status = self.request("POST", "/api/sleep/wake", {"duration_minutes": 0}, headers)
        self.assertEqual(response.status, 200)
        self.assertIsNone(status["wake_until"])
        self.assertEqual(status["days"], schedule["days"])
        self.assertIsNone(json.loads(runtime_path.read_text())["wake_until"])
        key = self.app.create_key("Sleep manager")["key"]
        response, _ = self.request("PUT", "/api/sleep", schedule, {"Authorization": f"Bearer {key}"})
        self.assertEqual(response.status, 200)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.app = Application(
            root / "config.json",
            root / "pairing-code",
            root / "target-url",
            Path("/bin/true"),
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5
        )

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.app.close()
        self.directory.cleanup()

    def request(self, method, path, body=None, headers=None):
        encoded = json.dumps(body).encode() if body is not None else None
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        self.connection.request(method, path, body=encoded, headers=request_headers)
        response = self.connection.getresponse()
        payload = json.loads(response.read()) if response.length != 0 else {}
        return response, payload

    def test_remote_view_requires_session_or_bearer_and_reports_unavailable_runtime(self):
        from webdisplay import remote_view
        path = Path(self.directory.name) / "remote-view.json"
        response, _ = self.request("GET", "/api/remote-view")
        self.assertEqual(response.status, 401)
        token, _ = self.app.sessions.create()
        key = self.app.create_key("Preview manager")["key"]
        with patch.object(remote_view, "RUNTIME_PATH", path):
            for headers in ({"Cookie": f"session={token}"}, {"Authorization": f"Bearer {key}"}):
                response, body = self.request("GET", "/api/remote-view", headers=headers)
                self.assertEqual(response.status, 503)
                self.assertIn("unavailable", body["error"])
            remote_view.publish(path, remote_view.snapshot(
                "single", {"id": "single", "kind": "web", "url": "https://user:secret@site.example/page?key=x"},
                {"sleeping": False, "scheduled_sleep": False, "wake_until": None},
            ))
            for headers in ({"Cookie": f"session={token}"}, {"Authorization": f"Bearer {key}"}):
                response, body = self.request("GET", "/api/remote-view", headers=headers)
                self.assertEqual(response.status, 200)
                self.assertEqual(body["scene"]["url"], "https://site.example/page")
            value = json.loads(path.read_text())
            path.write_text(json.dumps({**value, "updated": 0}))
            response, _ = self.request("GET", "/api/remote-view", headers={"Cookie": f"session={token}"})
            self.assertEqual(response.status, 503)

    def test_wifi_requires_login_and_csrf(self):
        response, _ = self.request("POST", "/api/wifi/scan", {})
        self.assertEqual(response.status, 401)
        response, _ = self.request("GET", "/api/network")
        self.assertEqual(response.status, 401)

        response, payload = self.request(
            "POST",
            "/api/pair",
            {"code": self.app.pairing_code, "password": "a secure password"},
        )
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        response, _ = self.request("POST", "/api/wifi/scan", {}, {"Cookie": cookie})
        self.assertEqual(response.status, 403)

        calls = []
        self.app.run_wifi = lambda request: calls.append(request) or {"ok": True}
        response, _ = self.request(
            "POST",
            "/api/wifi/connect",
            {"ssid": "Home", "password": "secret123", "extra": "ignored"},
            {"Cookie": cookie, "X-CSRF-Token": payload["csrf"]},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            calls, [{"operation": "connect", "ssid": "Home", "password": "secret123"}]
        )

    def test_root_serves_ui_and_health_reports_pairing(self):
        response, payload = self.request("GET", "/health")
        self.assertEqual({k: payload[k] for k in ("status", "paired")}, {"status": "ok", "paired": False})
        self.assertIn("version", payload)
        self.connection.request("GET", "/")
        response = self.connection.getresponse()
        body = response.read()
        self.assertEqual(response.status, 200)
        self.assertIn(b'<div id="root">', body)
        self.assertNotIn("unsafe-inline", response.getheader("Content-Security-Policy"))
        self.connection.request("GET", "/assets/../app.py")
        response = self.connection.getresponse()
        response.read()
        self.assertEqual(response.status, 404)

    def test_pair_authentication_and_csrf(self):
        response, _ = self.request("GET", "/api/config")
        self.assertEqual(response.status, 401)

        response, payload = self.request(
            "POST",
            "/api/pair",
            {"code": self.app.pairing_code, "password": "a secure password"},
        )
        self.assertEqual(response.status, 200)
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        csrf = payload["csrf"]

        response, _ = self.request(
            "PUT",
            "/api/config",
            {"url": "https://example.com"},
            {"Cookie": cookie},
        )
        self.assertEqual(response.status, 403)

        response, payload = self.request(
            "PUT",
            "/api/config",
            {"url": "https://example.com"},
            {"Cookie": cookie, "X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["url"], "https://example.com")
        self.assertFalse(payload["auto_ap"])
        self.assertEqual(payload["safe_area_percent"], 5)
        headers = {"Cookie": cookie, "X-CSRF-Token": csrf}
        response, _ = self.request("PUT", "/api/config", {"safe_area_percent": 8}, {"Cookie": cookie})
        self.assertEqual(response.status, 403)
        response, payload = self.request("PUT", "/api/config", {"safe_area_percent": 8}, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["safe_area_percent"], 8)
        published = json.loads((Path(self.directory.name) / "playlist.json").read_text())
        self.assertEqual(published["safe_area_percent"], 8)
        self.assertNotIn("password", published)
        response, _ = self.request("PUT", "/api/config", {"safe_area_percent": 11}, headers)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.app.config.load()["safe_area_percent"], 8)

        response, payload = self.request(
            "PUT",
            "/api/config",
            {"auto_ap": True},
            {"Cookie": cookie, "X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status, 200)
        self.assertTrue(payload["auto_ap"])
        self.assertEqual(payload["url"], "https://example.com")
        response, _ = self.request(
            "PUT",
            "/api/config",
            {"auto_ap": "yes"},
            {"Cookie": cookie, "X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status, 400)
        response, payload = self.request(
            "GET", "/api/config", None, {"Cookie": cookie}
        )
        self.assertTrue(payload["auto_ap"])
        self.assertEqual(
            (Path(self.directory.name) / "target-url").read_text().strip(),
            "https://example.com",
        )

    def test_audio_output_auth_availability_and_runtime_publication(self):
        output = "alsa/plughw:CARD=USB,DEV=0"
        response, _ = self.request("GET", "/api/audio/outputs")
        self.assertEqual(response.status, 401)
        token, session = self.app.sessions.create()
        cookie = {"Cookie": f"session={token}"}
        headers = {**cookie, "X-CSRF-Token": session.csrf}
        with patch("webdisplay.app.audio_outputs", return_value=[{"id": output, "label": "USB"}]):
            response, payload = self.request("GET", "/api/audio/outputs", headers=cookie)
            self.assertEqual(payload["items"][0]["id"], output)
            response, _ = self.request("PUT", "/api/config", {"audio_output": output}, cookie)
            self.assertEqual(response.status, 403)
            response, payload = self.request("PUT", "/api/config", {"audio_output": output}, headers)
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["audio_output"], output)
        for name in ("playlist.json", "music.json"):
            self.assertEqual(json.loads((Path(self.directory.name) / name).read_text())["audio_output"], output)
        with patch("webdisplay.app.audio_outputs", return_value=[]):
            response, _ = self.request("PUT", "/api/config", {"audio_output": output}, headers)
            self.assertEqual(response.status, 200)
            response, _ = self.request("PUT", "/api/config",
                                       {"audio_output": "alsa/plughw:CARD=Missing,DEV=0"}, headers)
            self.assertEqual(response.status, 400)
        self.assertEqual(self.app.config.load()["audio_output"], output)

    def test_playlist_auth_validation_and_atomic_publication(self):
        response, _ = self.request("GET", "/api/playlist")
        self.assertEqual(response.status, 401)
        token, session = self.app.sessions.create()
        headers = {"Cookie": f"session={token}", "X-CSRF-Token": session.csrf}
        playlist = {"enabled": True, "scenes": [
            {"id": "clock", "kind": "dashboard", "layout": "clock"},
            {"id": "page", "kind": "web", "url": "https://example.com/"},
        ]}
        response, _ = self.request("PUT", "/api/playlist", playlist)
        self.assertEqual(response.status, 401)
        response, _ = self.request("PUT", "/api/playlist", playlist, {"Cookie": headers["Cookie"]})
        self.assertEqual(response.status, 403)
        response, saved = self.request("PUT", "/api/playlist", playlist, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(saved["scenes"][0]["duration"], 60)
        path = Path(self.directory.name) / "playlist.json"
        published = json.loads(path.read_text())
        self.assertEqual(published, {**saved, "revision": published["revision"], "safe_area_percent": 5,
                                     "audio_output": "alsa/hdmi:CARD=vc4hdmi,DEV=0"})
        self.assertTrue(published["revision"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        private = Path(self.directory.name) / "config.json"
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        response, _ = self.request("PUT", "/api/playlist", {"enabled": True, "scenes": []}, headers)
        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(path.read_text()), published)
        self.assertEqual(self.app.config.load()["playlist"], saved)
        self.request("PUT", "/api/config", {"url": "https://example.com/legacy"}, headers)
        self.assertEqual(self.app.config.load()["playlist"], saved)
        response, loaded = self.request("GET", "/api/playlist", headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(loaded, saved)
        self.assertNotIn("password", path.read_text())
        self.request("PUT", "/api/playlist", saved, headers)
        self.assertNotEqual(json.loads(path.read_text())["revision"], published["revision"])
        restarted = Application(
            private, Path(self.directory.name) / "pairing-code",
            Path(self.directory.name) / "target-url", Path("/bin/true"),
        )
        self.assertEqual(restarted.config.load()["playlist"], saved)
        restarted.close()

    def test_plex_configuration_is_authenticated_write_only_and_csrf_protected(self):
        response, _ = self.request("GET", "/api/plex/config")
        self.assertEqual(response.status, 401)
        token, session = self.app.sessions.create()
        cookie = {"Cookie": f"session={token}"}
        response, config = self.request("GET", "/api/plex/config", headers=cookie)
        self.assertEqual(response.status, 200)
        self.assertEqual(config, {
            "server_url": "", "configured": False, "default_bitrate_kbps": 4000,
        })
        self.assertNotIn("token", config)
        body = {"server_url": "http://plex.example:32400", "token": "private-token"}
        response, _ = self.request("PUT", "/api/plex/config", body, cookie)
        self.assertEqual(response.status, 403)
        self.app.save_plex_config = lambda value: {
            "server_url": value["server_url"], "configured": True, "server_name": "Plex",
            "default_bitrate_kbps": value.get("default_bitrate_kbps", 4000),
        }
        response, result = self.request(
            "PUT", "/api/plex/config", body,
            {**cookie, "X-CSRF-Token": session.csrf},
        )
        self.assertEqual(response.status, 200)
        self.assertNotIn("private-token", json.dumps(result))
        self.assertNotIn("token", result)

    def test_music_settings_and_stations_require_authentication_and_csrf(self):
        for path in ("/api/plex/music", "/api/plex/stations"):
            response, _ = self.request("GET", path)
            self.assertEqual(response.status, 401)
        token, session = self.app.sessions.create()
        cookie = {"Cookie": f"session={token}"}
        response, config = self.request("GET", "/api/plex/music", headers=cookie)
        self.assertEqual(response.status, 200)
        self.assertFalse(config["enabled"])
        body = {"enabled": False, "station_key": "", "volume": 40}
        response, _ = self.request("PUT", "/api/plex/music", body, cookie)
        self.assertEqual(response.status, 403)
        response, result = self.request("PUT", "/api/plex/music", body,
                                       {**cookie, "X-CSRF-Token": session.csrf})
        self.assertEqual(response.status, 200)
        self.assertEqual(result["volume"], 40)
        self.assertNotIn("control_url", result)
        self.assertNotIn("token", result)

    def test_raildata_key_configuration_is_private_and_csrf_protected(self):
        response, _ = self.request("GET", "/api/raildata/config")
        self.assertEqual(response.status, 401)
        token, session = self.app.sessions.create()
        cookie = {"Cookie": f"session={token}"}
        response, config = self.request("GET", "/api/raildata/config", headers=cookie)
        self.assertEqual(response.status, 200)
        self.assertEqual(config, {
            "board_configured": False, "details_configured": False, "refresh_seconds": 60,
        })
        response, _ = self.request(
            "PUT", "/api/raildata/config", {
                "api_key": "private-key",
                "service_details_api_key": "private-details-key",
            }, cookie,
        )
        self.assertEqual(response.status, 403)
        response, result = self.request(
            "PUT", "/api/raildata/config", {
                "api_key": "private-key",
                "service_details_api_key": "private-details-key",
            },
            {**cookie, "X-CSRF-Token": session.csrf},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(result, {
            "board_configured": True, "details_configured": True, "refresh_seconds": 60,
        })
        self.assertNotIn("private-key", json.dumps(result))
        self.assertNotIn("private-details-key", json.dumps(result))
        config_contents = (Path(self.directory.name) / "config.json").read_text()
        self.assertIn("private-key", config_contents)
        self.assertIn("private-details-key", config_contents)
        self.assertNotIn("private-key", (Path(self.directory.name) / "playlist.json").read_text())
        response, _ = self.request(
            "PUT", "/api/raildata/config", {"service_details_api_key": "new-details-key"},
            {**cookie, "X-CSRF-Token": session.csrf},
        )
        self.assertEqual(response.status, 200)
        config = self.app.config.load()
        self.assertEqual(config["raildata_api_key"], "private-key")
        self.assertEqual(config["service_details_api_key"], "new-details-key")
        response, result = self.request(
            "PUT", "/api/raildata/config", {"refresh_seconds": 30},
            {**cookie, "X-CSRF-Token": session.csrf},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(result["refresh_seconds"], 30)
        self.assertEqual(self.app.config.load()["raildata_refresh_seconds"], 30)
        self.assertEqual(self.app.config.load()["raildata_api_key"], "private-key")
        response, _ = self.request(
            "PUT", "/api/raildata/config", {"refresh_seconds": 29},
            {**cookie, "X-CSRF-Token": session.csrf},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(self.app.config.load()["raildata_refresh_seconds"], 30)

    def test_weather_configuration_is_private_and_csrf_protected(self):
        response, _ = self.request("GET", "/api/weather/config")
        self.assertEqual(response.status, 401)
        token, session = self.app.sessions.create()
        cookie = {"Cookie": f"session={token}"}
        response, config = self.request("GET", "/api/weather/config", headers=cookie)
        self.assertEqual(config, {"city": "St Albans", "country": "GB", "configured": False})
        response, _ = self.request("PUT", "/api/weather/config", {"api_key": "private-key"}, cookie)
        self.assertEqual(response.status, 403)
        headers = {**cookie, "X-CSRF-Token": session.csrf}
        response, result = self.request(
            "PUT", "/api/weather/config",
            {"city": "St Albans", "country": "gb", "api_key": "private-key"}, headers,
        )
        self.assertEqual(response.status, 200)
        self.assertTrue(result["configured"])
        self.assertNotIn("private-key", json.dumps(result))
        response, _ = self.request("PUT", "/api/weather/config", {"city": "London"}, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.app.config.load()["weather"]["api_key"], "private-key")
        response, _ = self.request("PUT", "/api/weather/config", {"country": "INVALID"}, headers)
        self.assertEqual(response.status, 400)
        response, result = self.request("PUT", "/api/weather/config", {"clear_key": True}, headers)
        self.assertEqual(response.status, 200)
        self.assertFalse(result["configured"])

    def test_plex_library_browsing_requires_authentication(self):
        response, _ = self.request("GET", "/api/plex/libraries")
        self.assertEqual(response.status, 401)
        token, _ = self.app.sessions.create()
        headers = {"Cookie": f"session={token}"}
        client = type("PlexStub", (), {
            "browse": lambda self, key=None: [{"title": "Movies", "key": key or "1"}],
        })()
        self.app.plex_client = lambda: client
        response, result = self.request(
            "GET", "/api/plex/browse?key=%2Flibrary%2Fmetadata%2F10%2Fchildren",
            headers=headers,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(result["items"], [{
            "title": "Movies", "key": "/library/metadata/10/children",
        }])

    def test_playlist_supports_bearer_keys_and_large_notices(self):
        key = self.app.create_key("manager")["key"]
        playlist = {"enabled": True, "scenes": [
            {"id": str(index), "kind": "dashboard", "text": "\u2603" * 2000}
            for index in range(30)
        ]}
        headers = {"Authorization": f"Bearer {key}"}
        response, saved = self.request("PUT", "/api/playlist", playlist, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(len(saved["scenes"]), 30)
        response, loaded = self.request("GET", "/api/playlist", headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(loaded, saved)

    def test_playback_status_requires_authentication_and_reports_read_errors(self):
        response, _ = self.request("GET", "/api/playlist/status")
        self.assertEqual(response.status, 401)
        key = self.app.create_key("manager")["key"]
        headers = {"Authorization": f"Bearer {key}"}
        path = Path(self.directory.name) / "status.json"
        with patch.object(webdisplay_app, "PLAYBACK_STATUS_PATH", path):
            response, payload = self.request("GET", "/api/playlist/status", headers=headers)
            self.assertEqual(response.status, 200)
            self.assertEqual(payload, {"failures": {}, "updated": None})
            status = {"failures": {"scene": {"message": "Website could not be reached.", "time": 1}}, "updated": 1}
            path.write_text(json.dumps(status))
            response, payload = self.request("GET", "/api/playlist/status", headers=headers)
            self.assertEqual(payload, status)
            path.write_text("invalid")
            with self.assertLogs("web-display", level="ERROR"):
                response, payload = self.request("GET", "/api/playlist/status", headers=headers)
            self.assertEqual(response.status, 500)
            self.assertIn("Could not read", payload["error"])


class StaticUiTests(unittest.TestCase):
    def test_static_file_serves_index_and_assets_only_inside_the_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "static"
            (root / "assets").mkdir(parents=True)
            (root / "index.html").write_text("<html></html>")
            (root / "assets" / "app.js").write_text("1")
            (root / "assets" / "rail.otf").write_bytes(b"font")
            (Path(directory) / "secret.html").write_text("secret")
            self.assertEqual(static_file("/", root), (b"<html></html>", "text/html"))
            self.assertEqual(static_file("/assets/app.js?x=1", root), (b"1", "text/javascript"))
            self.assertEqual(static_file("/assets/rail.otf", root), (b"font", "font/otf"))
            self.assertIsNone(static_file("/../secret.html", root))
            self.assertIsNone(static_file("/assets/../../secret.html", root))
            self.assertIsNone(static_file("/%2e%2e/secret.html", root))
            self.assertIsNone(static_file("/assets/missing.js", root))
            self.assertIsNone(static_file("/assets/app.exe", root))

    def test_built_bundle_is_present_and_has_no_inline_scripts(self):
        index = (Path(webdisplay_app.__file__).parent / "static" / "index.html").read_text()
        self.assertIn('src="/assets/', index)
        self.assertNotRegex(index, r"<script(?![^>]*\bsrc=)")


class KioskTests(unittest.TestCase):
    @patch("webdisplay.kiosk.subprocess.run")
    def test_screen_size_uses_xrandr_current_mode(self, run):
        run.return_value.stdout = (
            "Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 2048 x 2048"
        )
        self.assertEqual(screen_size(), "1920,1080")


if __name__ == "__main__":
    unittest.main()
