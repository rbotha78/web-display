"""Optional real-Chromium smoke tests, using only local fixture pages."""

from contextlib import contextmanager
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from webdisplay.browser import BrowserError, PersistentBrowser

BROWSER = shutil.which("google-chrome") or shutil.which("chromium")


@unittest.skipUnless(BROWSER, "Chromium is not installed")
class BrowserIntegrationTests(unittest.TestCase):
    @contextmanager
    def fixture_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("scene", "a", "b", "c"):
                (Path(directory) / name).write_text(
                    "<html><body>Fixture<script>window.marker=0;</script></body></html>",
                )
            log_path = Path(directory) / "browser.log"
            with log_path.open("wb") as logfile:
                process = None

                def launch(destination, pipes):
                    nonlocal process
                    process = subprocess.Popen(
                        [
                            sys.executable, "-m", "webdisplay.browser",
                            "--pipe-in", str(pipes[0]), "--pipe-out", str(pipes[1]),
                            "--", BROWSER, "--headless=new", "--remote-debugging-pipe",
                            "--no-first-run", "--no-default-browser-check", "--disable-gpu",
                            "--disable-background-networking", "--disable-extensions",
                            "--disable-component-update", "--disable-dev-shm-usage",
                            "--disable-backgrounding-occluded-windows",
                            "--disable-renderer-backgrounding",
                            f"--user-data-dir={directory}/profile", destination,
                        ],
                        pass_fds=pipes, start_new_session=True, stdout=logfile, stderr=logfile,
                    )
                    return process

                url = Path(directory).as_uri()
                browser = PersistentBrowser(launch, url + "/scene")
                try:
                    yield browser, url
                except (BrowserError, AssertionError, OSError):
                    print("Browser executable:", BROWSER, file=sys.stderr)
                    print("Browser exited:", process.poll() if process else "not launched", file=sys.stderr)
                    print(log_path.read_text(errors="replace"), file=sys.stderr)
                    raise
                finally:
                    browser.close()

    def test_retains_page_state_and_enforces_two_website_tab_limit(self):
        def ready(browser, tab):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if browser.ready(tab):
                    return
                time.sleep(0.05)
            self.fail(f"Fixture page did not become ready: {tab.url}")

        with self.fixture_browser() as (browser, url):
            # Do not freeze a document while its initial navigation is still committing.
            local = browser.prepare(None, browser.local_url)
            ready(browser, local)
            a = browser.prepare("a", url + "/a")
            ready(browser, a)
            browser.activate(a)
            pid = browser.process.pid
            browser._call("Runtime.evaluate", {"expression": "window.marker=42"}, a)
            b = browser.prepare("b", url + "/b")
            ready(browser, b)
            browser.activate(b)
            self.assertIs(browser.prepare("a", url + "/a"), a)
            ready(browser, a)
            browser.activate(a)
            marker = browser._call("Runtime.evaluate", {"expression": "window.marker"}, a)
            self.assertEqual(marker["result"]["value"], 42)
            c = browser.prepare("c", url + "/c")
            ready(browser, c)
            browser.activate(c)
            self.assertEqual(set(browser.tabs), {"a", "c"})
            self.assertEqual(browser.process.pid, pid)
            b = browser.prepare("b", url + "/b")
            ready(browser, b)
            self.assertEqual(set(browser.tabs), {"b", "c"})
            local = browser.prepare(None, browser.local_url)
            ready(browser, local)
            browser.activate(local)
            browser.freeze()
            self.assertIs(browser.prepare(None, browser.local_url), local)
            ready(browser, local)
            browser.activate(local)
            self.assertEqual(browser.process.pid, pid)
