import unittest
from unittest.mock import Mock, patch

from webdisplay.hls import MAX_MANIFEST_BYTES, resolve_video_url, select_rendition


class HlsTests(unittest.TestCase):
    def test_selects_720p_before_player_opens_all_variants(self):
        manifest = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=582000,RESOLUTION=426x240
240.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2742000,CODECS="avc1.4d001f,mp4a.40.2",RESOLUTION=1280x720
720.m3u8?signature=example
#EXT-X-STREAM-INF:BANDWIDTH=5400000,RESOLUTION=1920x1080
1080.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=10768000,RESOLUTION=3840x2160
2160.m3u8
"""
        self.assertEqual(
            select_rendition(manifest, "https://example.com/video/master.m3u8?key=example"),
            "https://example.com/video/720.m3u8?signature=example",
        )

    def test_uses_lower_supported_variant_and_preserves_media_playlists(self):
        manifest = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=640x360
360.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1280x720
720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS="hvc1.1.6.L93,mp4a.40.2",RESOLUTION=1280x720
hevc.m3u8
"""
        base = "https://example.com/master.m3u8"
        self.assertEqual(select_rendition(manifest, base), "https://example.com/360.m3u8")
        self.assertEqual(select_rendition("#EXTM3U\n#EXTINF:10,\npart.ts\n", base), base)

    def test_rejects_unsupported_and_malformed_variants(self):
        for manifest in (
            "<html>Error</html>",
            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=9999999,RESOLUTION=3840x2160\n4k.m3u8",
            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=bad\nchild.m3u8",
            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nfile:///etc/passwd",
            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\n",
        ):
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                select_rendition(manifest, "https://example.com/master.m3u8")

    def test_resolution_is_bounded_and_uses_redirect_base(self):
        response = Mock()
        response.url = "https://cdn.example.com/folder/master.m3u8"
        response.read.return_value = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nchild.m3u8\n"
        with patch("webdisplay.hls.urllib.request.urlopen") as open_url:
            open_url.return_value.__enter__.return_value = response
            self.assertEqual(resolve_video_url("https://example.com/master.m3u8"),
                             "https://cdn.example.com/folder/child.m3u8")
            response.read.assert_called_once_with(MAX_MANIFEST_BYTES + 1)
            response.read.return_value = b"x" * (MAX_MANIFEST_BYTES + 1)
            with self.assertRaisesRegex(ValueError, "size limit"):
                resolve_video_url("https://example.com/master.m3u8")
            open_url.reset_mock()
            self.assertEqual(resolve_video_url("https://example.com/video.mp4"), "https://example.com/video.mp4")
            open_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
