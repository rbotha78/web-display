"""Optional real-Chromium smoke tests, using only local fixture pages."""

import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from webdisplay.browser import PersistentBrowser

BROWSER = shutil.which("google-chrome") or shutil.which("chromium")


@unittest.skipUnless(BROWSER, "Chromium is not installed")
class BrowserIntegrationTests(unittest.TestCase):
    def test_retains_page_state_and_enforces_two_website_tab_limit(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        logfile = (Path(directory.name) / "browser.log").open("wb")
        self.addCleanup(logfile.close)
        for name in ("scene", "a", "b", "c"):
            (Path(directory.name) / name).write_text(
                "<html><body>Fixture<script>window.marker=0;</script></body></html>",
            )
        url = Path(directory.name).as_uri()

        def launch(destination, pipes):
            return subprocess.Popen(
                [
                    sys.executable, "-m", "webdisplay.browser", "--pipe-in", str(pipes[0]),
                    "--pipe-out", str(pipes[1]), "--", BROWSER, "--headless=new",
                    "--remote-debugging-pipe", "--no-first-run", "--disable-gpu",
                    "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                    f"--user-data-dir={directory.name}/profile", destination,
                ],
                pass_fds=pipes, start_new_session=True, stdout=logfile, stderr=logfile,
            )

        browser = PersistentBrowser(launch, url + "/scene")
        self.addCleanup(browser.close)

        def ready(tab):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if browser.ready(tab):
                    return
                time.sleep(0.05)
            self.fail("Fixture page did not become ready")

        try:
            a = browser.prepare("a", url + "/a")
        except Exception:
            logfile.flush()
            print((Path(directory.name) / "browser.log").read_text())
            print("Browser exited:", browser.process.poll())
            raise
        ready(a)
        browser.activate(a)
        pid = browser.process.pid
        browser._call("Runtime.evaluate", {"expression": "window.marker=42"}, a)
        b = browser.prepare("b", url + "/b")
        ready(b)
        browser.activate(b)
        self.assertIs(browser.prepare("a", url + "/a"), a)
        ready(a)
        browser.activate(a)
        marker = browser._call("Runtime.evaluate", {"expression": "window.marker"}, a)
        self.assertEqual(marker["result"]["value"], 42)
        c = browser.prepare("c", url + "/c")
        ready(c)
        browser.activate(c)
        self.assertEqual(set(browser.tabs), {"a", "c"})
        self.assertEqual(browser.process.pid, pid)
        browser.prepare("b", url + "/b")
        self.assertEqual(set(browser.tabs), {"b", "c"})
