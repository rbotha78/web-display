from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CydConfigTests(unittest.TestCase):
    def test_preferences_keys_fit_esp32_nvs_limit(self):
        source = (ROOT / "cyd/src/CompanionConfig.cpp").read_text(encoding="utf-8")
        keys = re.findall(
            r'preferences\.(?:get|put|remove)[A-Za-z0-9]*\("([^"]+)"',
            source,
        )
        self.assertTrue(keys)
        self.assertFalse(
            [(key, len(key)) for key in keys if len(key) > 15],
            "ESP32 NVS keys are limited to 15 characters",
        )


if __name__ == "__main__":
    unittest.main()
