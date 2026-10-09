import tempfile
import unittest
from pathlib import Path

from webdisplay.kiosk import clear_stale_profile_locks


class ProfileLockTests(unittest.TestCase):
    def test_removes_stale_locks_and_keeps_data(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            (profile / "SingletonLock").symlink_to("oldhost-123")
            (profile / "SingletonCookie").symlink_to("456")
            (profile / "SingletonSocket").symlink_to("/tmp/x/SingletonSocket")
            (profile / "Preferences").write_text("{}")
            clear_stale_profile_locks(profile)
            self.assertEqual([p.name for p in profile.iterdir()], ["Preferences"])

    def test_missing_profile_is_fine(self):
        clear_stale_profile_locks(Path("/nonexistent-profile"))


if __name__ == "__main__":
    unittest.main()
