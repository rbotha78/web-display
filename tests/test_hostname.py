import unittest

from webdisplay.hostname_helper import rewrite_hosts, validate_hostname


class HostnameTests(unittest.TestCase):
    def test_accepts_valid_names(self):
        self.assertEqual(validate_hostname("Lobby-1"), "lobby-1")
        self.assertEqual(validate_hostname(" a "), "a")
        self.assertEqual(validate_hostname("a" * 63), "a" * 63)

    def test_rejects_invalid_names(self):
        for bad in ["", "-a", "a-", "a_b", "a.b", "a b", "a" * 64, "localhost", 5, None, "ü"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_hostname(bad)

    def test_rewrite_hosts_replaces_entry(self):
        content = "127.0.0.1\tlocalhost\n127.0.1.1\told\n"
        self.assertEqual(
            rewrite_hosts(content, "old", "new"), "127.0.0.1\tlocalhost\n127.0.1.1\tnew\n"
        )

    def test_rewrite_hosts_adds_missing_entry(self):
        self.assertEqual(
            rewrite_hosts("127.0.0.1\tlocalhost\n", "old", "new"),
            "127.0.0.1\tlocalhost\n127.0.1.1\tnew\n",
        )


if __name__ == "__main__":
    unittest.main()
