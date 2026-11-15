import io
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from webdisplay import music_sync, radio
from webdisplay.app import Application
from webdisplay.music import MusicRelay, validate_music
from webdisplay.radio import FrameParser, RadioBuffer, frame_header, icy_chunks, split_title

STATION_ID = "96062a7b-0601-11e8-ae97-52543be04c81"
STATION = {"id": STATION_ID, "name": "Groove Salad", "url": "https://radio.invalid/groove.mp3",
           "favicon": "", "country": "US", "codec": "MP3", "bitrate": 128}
DAYS = {day: "" for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}
RADIO_SETTINGS = {"enabled": True, "station_key": "radio:" + STATION_ID, "volume": 20, "day_sources": DAYS,
                  "radio_stations": [STATION]}
TOKEN = "t" * 43
GROUP = "group-id-0123456789"

# MPEG-1 layer III, 128 kbit/s, 44.1 kHz: 417 bytes and 1152 samples per frame.
MP3_FRAME = b"\xff\xfb\x90\x00" + bytes(413)


def adts_frame(length=200):
    return bytes([0xFF, 0xF1, 0x50, 0x80 | (length >> 11), (length >> 3) & 0xFF,
                  ((length & 7) << 5) | 0x1F, 0xFC]) + bytes(length - 7)


class FakeResponse:
    def __init__(self, data, metaint=0):
        self.stream = io.BytesIO(data)
        self.metaint = metaint

    def getheader(self, name):
        return str(self.metaint) if name == "icy-metaint" and self.metaint else None

    def read(self, size):
        return self.stream.read(size)


class FakeConnection:
    sock = None

    def close(self):
        pass


def icy(audio, metaint, titles):
    out = b""
    for index in range(0, len(audio), metaint):
        out += audio[index:index + metaint]
        title = titles.get(index // metaint, "")
        block = f"StreamTitle='{title}';".encode() if title else b""
        size = (len(block) + 15) // 16
        out += bytes([size]) + block.ljust(size * 16, b"\0")
    return out


class ParsingTests(unittest.TestCase):
    def test_frames_are_split_across_chunks_after_junk_and_confirmed_by_next_header(self):
        self.assertEqual(frame_header(MP3_FRAME), ("mp3", 417, 1152, 44100))
        self.assertEqual(frame_header(adts_frame()), ("aac", 200, 1024, 44100))
        self.assertIsNone(frame_header(b"\xff\xfb\xf0\x00\x00\x00\x00"))
        parser, frames = FrameParser(), []
        data = b"ID3junk\xff\xfb" + MP3_FRAME * 5
        for index in range(0, len(data), 100):
            frames += parser.feed(data[index:index + 100])
        # The last frame waits for a following header to confirm it.
        self.assertEqual([(len(f), s, r) for f, s, r in frames], [(417, 1152, 44100)] * 4)
        self.assertTrue(all(frame == MP3_FRAME for frame, _, _ in frames))
        aac = FrameParser().feed(adts_frame() * 3)
        self.assertEqual(len(aac), 2)

    def test_icy_metadata_is_removed_and_titles_reported(self):
        audio = bytes(range(250)) * 4
        response = FakeResponse(icy(audio, 100, {1: "Artist - Song", 3: "Other"}), metaint=100)
        chunks = list(icy_chunks(response, read_size=64))
        self.assertEqual(b"".join(chunk for chunk, _ in chunks), audio)
        self.assertEqual([title for _, title in chunks if title], ["Artist - Song", "Other"])
        self.assertEqual(split_title("Artist - Song - Live"), ("Song - Live", "Artist"))
        self.assertEqual(split_title("Station ident"), ("Station ident", ""))

    def test_search_validates_terms_and_keeps_supported_streams(self):
        self.assertEqual(radio.search_query({"tag": ["jazz "], "country": ["gb"]}),
                         {"tag": "jazz", "country": "gb", "offset": 0})
        for query in ({}, {"name": [""]}, {"name": ["x"], "country": ["GBR"]}, {"name": ["x"], "offset": ["-1"]},
                      {"name": ["x"], "evil": ["1"]}, {"name": ["a", "b"]}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                radio.search_query(query)
        base = {"stationuuid": STATION_ID, "name": "Groove", "url_resolved": "https://a.invalid/s",
                "codec": "MP3", "bitrate": 128, "hls": 0, "tags": "ambient"}
        raw = [base, base, {**base, "stationuuid": STATION_ID[:-1] + "0", "codec": "OGG"},
               {**base, "stationuuid": STATION_ID[:-1] + "1", "hls": 1},
               {**base, "stationuuid": STATION_ID[:-1] + "2", "codec": "HE-AAC", "url_resolved": "ftp://x"},
               {**base, "stationuuid": STATION_ID[:-1] + "3", "codec": "HE-AAC", "favicon": "javascript:x"}]
        calls = []
        result = radio.search({"tag": "ambient", "country": "us", "offset": 0},
                              lambda path, params: calls.append(params) or raw)
        self.assertEqual([(item["id"][-1], item["codec"], item["favicon"]) for item in result["items"]],
                         [("1", "MP3", ""), ("3", "AAC+", "")])
        self.assertEqual((calls[0]["countrycode"], calls[0]["hidebroken"]), ("US", "true"))
        self.assertFalse(result["more"])

    def test_radio_sources_must_be_saved_stations(self):
        settings = validate_music(RADIO_SETTINGS)
        self.assertEqual(settings["radio_stations"][0]["codec"], "MP3")
        validate_music({**RADIO_SETTINGS, "day_sources": {**DAYS, "monday": "radio:" + STATION_ID}})
        for value in ({**RADIO_SETTINGS, "radio_stations": []},
                      {**RADIO_SETTINGS, "radio_stations": [{**STATION, "url": "file:///etc/passwd"}]},
                      {**RADIO_SETTINGS, "radio_stations": [STATION, STATION]},
                      {**RADIO_SETTINGS, "radio_stations": [{**STATION, "codec": "OGG"}]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_music(value)

    def test_streams_to_local_or_special_addresses_are_refused(self):
        for host in ("127.0.0.1", "169.254.1.1", "::1", "0.0.0.0", "224.0.0.1"):
            with self.subTest(host=host), self.assertRaises(OSError):
                radio._allowed_address(host, 80)
        radio._allowed_address("192.168.0.10", 80)


class BufferTests(unittest.TestCase):
    def buffer(self, data, metaint=0, keep=radio.BUFFER_SECONDS):
        gate = threading.Event()

        class Response(FakeResponse):
            def read(self, size):
                chunk = super().read(size)
                if not chunk:
                    gate.wait(5)
                return chunk

        buffer = RadioBuffer("https://radio.invalid/s", lambda url: (FakeConnection(), Response(data, metaint)), keep)
        self.addCleanup(gate.set)
        self.addCleanup(buffer.close)
        return buffer

    def test_buffer_indexes_frames_titles_and_follows_live_edge(self):
        audio = MP3_FRAME * 120
        buffer = self.buffer(icy(audio, 417 * 40, {0: "Artist - Song"}), metaint=417 * 40)
        self.assertTrue(buffer.wait_ready(3.0, 5))
        self.assertAlmostEqual(buffer.frame_duration, 1152 / 44100)
        self.assertEqual((buffer.first, buffer.head), (0, 119))
        self.assertEqual((buffer.title_at(30), buffer.title_at(41)), ("", "Artist - Song"))
        self.assertEqual(buffer.live_index(1.0), 119 - int(1.0 / buffer.frame_duration))
        self.assertEqual(buffer.content_type, "audio/mpeg")
        reader = buffer.read(117)
        self.assertEqual(next(reader), MP3_FRAME * 2)
        buffer.close()
        self.assertEqual(list(reader), [])

    def test_buffer_trims_old_audio_and_reports_failures(self):
        buffer = self.buffer(MP3_FRAME * 120, keep=1.0)
        self.assertTrue(buffer.wait_ready(0.5, 5))
        self.assertEqual(len(buffer.frames), int(1.0 / buffer.frame_duration))
        self.assertEqual(list(buffer.read(0)), [])

        def refuse(url):
            raise OSError("HTTP 404")

        failed = RadioBuffer("https://radio.invalid/s", refuse)
        self.assertFalse(failed.wait_ready(1.0, 5))
        self.assertEqual((failed.ended, failed.error), (True, "HTTP 404"))


class FakeBuffer:
    """A RadioBuffer stand-in whose live edge the test controls."""

    def __init__(self, url):
        self.url = url
        self.first, self.head, self.frame_duration = 0, 400, 0.1
        self.ended = self.closed = False
        self.error = ""
        self.content_type = "audio/mpeg"

    def wait_ready(self, seconds, timeout):
        return not self.closed

    def buffered(self):
        return (self.head - self.first) * self.frame_duration

    def close(self):
        self.closed = True

    def live_index(self, lead):
        return max(self.first, self.head - int(lead / self.frame_duration))

    def title_at(self, index):
        return "Artist - Song" if index >= 100 else ""

    def read(self, start, stop=lambda: False):
        if start < self.head:
            yield b"frames %d-%d" % (start, self.head)


class RelayTests(unittest.TestCase):
    def relay(self, sync=None):
        relay = MusicRelay(lambda: None)
        relay.radio_factory = FakeBuffer
        self.addCleanup(relay.close)
        relay.configure(RADIO_SETTINGS, "identity", sync or {"role": "off"})
        return relay

    def test_single_display_streams_station_from_near_live_edge_without_plex(self):
        relay = self.relay()
        track = relay.control({"action": "current"})
        self.assertTrue(track["live"])
        self.assertEqual((track["title"], track["artist"]), ("Groove Salad", ""))
        with urllib.request.urlopen(track["url"]) as response:
            self.assertEqual(response.headers["Content-Type"], "audio/mpeg")
            self.assertEqual(response.read(), b"frames 100-400")
        track = relay.control({"action": "timeline", "track_id": track["track_id"], "state": "playing", "time": 1000})
        self.assertEqual((track["title"], track["artist"], track["album"]), ("Song", "Artist", "Groove Salad"))
        self.assertEqual(relay.get_status()["source"], "radio")
        with self.assertRaisesRegex(ValueError, "cannot skip"):
            relay.request_playback("skip")

    def test_leader_anchors_group_and_relays_frames_to_followers(self):
        clock = [1_000_000.0]
        with patch.object(music_sync, "now", lambda: clock[0]):
            relay = self.relay({"role": "leader", "group_id": GROUP, "token_hash": music_sync.token_hash(TOKEN)})
            state = relay.control({"action": "sync", "listening": True})
            self.assertEqual((state["state"], state["live"], state["duration"]), ("playing", True, 0))
            self.assertEqual(state["frame_duration"], 0.1)
            session = relay.radio_session
            self.assertEqual(session["anchor"], 100)
            content_type, chunks = relay.follower_radio(session["id"], 40)
            self.assertEqual((content_type, list(chunks)), ("audio/mpeg", [b"frames 140-400"]))
            with self.assertRaises(LookupError):
                relay.follower_radio("x" * 22, 0)
            with urllib.request.urlopen(state["url"] + "/12") as response:
                self.assertEqual(response.read(), b"frames 112-400")
            # Once the playhead falls out of the ingest buffer the group re-anchors on a new stream.
            relay.radio.first, relay.radio.head = 3600, 4000
            clock[0] += 5
            moved = relay.control({"action": "sync", "listening": True})
            self.assertNotEqual(moved["track_id"], state["track_id"])
            self.assertNotEqual(relay.radio_session["id"], session["id"])

    def test_leader_waits_for_stall_margin_before_starting_group(self):
        clock = [1_000_000.0]
        original = FakeBuffer.__init__

        def slow(buffer, url):
            original(buffer, url)
            buffer.head = 100

        with patch.object(music_sync, "now", lambda: clock[0]), patch.object(FakeBuffer, "__init__", slow):
            relay = self.relay({"role": "leader", "group_id": GROUP, "token_hash": music_sync.token_hash(TOKEN)})
            self.assertEqual(relay.control({"action": "sync", "listening": True})["state"], "waiting")
            relay.radio.head = 160
            clock[0] += 6
            state = relay.control({"action": "sync", "listening": True})
            self.assertEqual(state["state"], "playing")
            self.assertEqual(relay.radio_session["anchor"], 0)

    def test_leader_pause_survives_settings_publication_and_resume_reconnects(self):
        clock = [1_000_000.0]
        sync = {"role": "leader", "group_id": GROUP, "token_hash": music_sync.token_hash(TOKEN)}
        with patch.object(music_sync, "now", lambda: clock[0]):
            relay = self.relay(sync)
            relay.control({"action": "sync", "listening": True})
            first = relay.radio_session["id"]
            relay.request_playback("pause")
            relay.configure(RADIO_SETTINGS, "identity", sync)
            clock[0] += 5
            paused = relay.control({"action": "sync", "listening": True})
            self.assertEqual(paused["state"], "paused")
            self.assertIsNone(relay.radio)
            relay.request_playback("resume")
            relay.configure(RADIO_SETTINGS, "identity", sync)
            resumed = relay.control({"action": "sync", "listening": True})
            self.assertEqual(resumed["state"], "playing")
            self.assertNotEqual(relay.radio_session["id"], first)
            self.assertEqual(resumed["started_at"], clock[0] + music_sync.START_LEAD)


class ApplicationRadioTests(unittest.TestCase):
    def test_radio_plays_without_plex_and_relay_endpoint_needs_group_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root / "config.json", root / "pairing", root / "target", Path("/bin/true"))
            self.addCleanup(app.close)
            with patch("webdisplay.app.radio.register_click") as click:
                saved = app.save_music_config({k: v for k, v in RADIO_SETTINGS.items() if k != "enabled"}
                                              | {"enabled": False})
                self.assertEqual(saved["radio_stations"][0]["id"], STATION_ID)
                # Older managers omit radio stations; the saved list is kept.
                saved = app.save_music_config({"enabled": False, "station_key": "radio:" + STATION_ID, "volume": 20})
                self.assertEqual(len(saved["radio_stations"]), 1)
                for _ in range(100):
                    if click.called:
                        break
                    time.sleep(0.02)
                click.assert_called_once_with(STATION_ID)
            app.control_music({"action": "play"})
            self.assertTrue(app.config.load()["music"]["enabled"])
            app.save_plex_config({"clear_token": True})
            self.assertTrue(app.config.load()["music"]["enabled"])


if __name__ == "__main__":
    unittest.main()
