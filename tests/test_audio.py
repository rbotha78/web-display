import tempfile
import unittest
from pathlib import Path

from webdisplay.audio import DEFAULT_AUDIO_OUTPUT, audio_outputs, validate_audio_output
from webdisplay.app import ConfigStore


class AudioTests(unittest.TestCase):
    def test_discovery_uses_stable_ids_and_excludes_capture_only_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cards").write_text(
                " 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n"
                " 2 [vc4hdmi        ]: vc4-hdmi - vc4-hdmi\n"
                " 3 [USB            ]: USB-Audio - USB speakers\n"
                " 4 [Mic            ]: USB-Audio - Microphone\n"
            )
            (root / "pcm").write_text(
                "00-00: headphones : playback 8\n"
                "02-00: HDMI : playback 1\n"
                "03-00: USB : playback 1 : capture 1\n"
                "04-00: mic : capture 1\n"
            )
            self.assertEqual([item["id"] for item in audio_outputs(root)], [
                "alsa/plughw:CARD=Headphones,DEV=0", DEFAULT_AUDIO_OUTPUT,
                "alsa/plughw:CARD=USB,DEV=0",
            ])
            self.assertEqual(audio_outputs(root / "missing"), [])

    def test_default_migration_and_validation_preserve_saved_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            store = ConfigStore(path)
            self.assertEqual(store.load()["audio_output"], DEFAULT_AUDIO_OUTPUT)
            path.write_text('{"url": ""}')
            self.assertEqual(store.load()["audio_output"], DEFAULT_AUDIO_OUTPUT)
            output = "alsa/plughw:CARD=USB,DEV=0"
            store.update(audio_output=output)
            self.assertEqual(store.load()["audio_output"], output)
            for invalid in (None, True, "auto", "alsa/default", "alsa/plughw:CARD=USB,DEV=0\n",
                            "--ao=null", "alsa/plughw:CARD=USB,DEV=-1"):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    store.update(audio_output=invalid)
            self.assertEqual(store.load()["audio_output"], output)
            self.assertEqual(validate_audio_output(output), output)
