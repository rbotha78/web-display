import http.client
import unittest

from webdisplay.captive import Portal


class CaptivePortalTests(unittest.TestCase):
    def setUp(self):
        self.portal = Portal("127.0.0.1", 0)
        self.addCleanup(self.portal.close)
        self.client = http.client.HTTPConnection(*self.portal.server.server_address, timeout=5)
        self.addCleanup(self.client.close)

    def test_phone_connectivity_probes_redirect_to_fixed_http_landing_page(self):
        for path in ("/generate_204", "/hotspot-detect.html", "/connecttest.txt", "//example.test/"):
            with self.subTest(path=path):
                self.client.request("GET", path, headers={"Host": "untrusted.example"})
                response = self.client.getresponse()
                self.assertEqual(response.status, 302)
                self.assertEqual(response.getheader("Location"), "http://10.42.0.1/")
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                self.assertEqual(response.read(), b"")

    def test_landing_page_keeps_credentials_on_https_administration(self):
        self.client.request("GET", "/")
        response = self.client.getresponse()
        self.assertEqual(response.status, 200)
        page = response.read().decode()
        self.assertIn('href="https://10.42.0.1:8443/"', page)
        self.assertIn("fingerprint", page)
        self.assertNotIn("<form", page)
        self.assertIn("form-action 'none'", response.getheader("Content-Security-Policy"))

    def test_head_probes_redirect_and_posts_are_not_accepted(self):
        self.client.request("HEAD", "/generate_204")
        response = self.client.getresponse()
        self.assertEqual(response.status, 302)
        self.assertEqual(response.read(), b"")
        self.client.request("POST", "/", body="password=not-accepted")
        response = self.client.getresponse()
        self.assertEqual(response.status, 501)
        response.read()
