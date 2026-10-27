import json
import os
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock

from webdisplay.browser import BrowserError, DevTools, PersistentBrowser, Tab


class DevToolsTests(unittest.TestCase):
    def test_pipe_transport_ignores_events_and_matches_fragmented_replies(self):
        read_fd, reply_fd = os.pipe()
        command_fd, write_fd = os.pipe()
        control = DevTools(read_fd, write_fd)

        def browser():
            try:
                data = b""
                while b"\0" not in data:
                    data += os.read(command_fd, 1024)
                command = json.loads(data[:-1])
                self.assertEqual(command["sessionId"], "tab-session")
                reply = json.dumps({"id": command["id"], "result": {"ready": True}}).encode() + b"\0"
                os.write(reply_fd, b'{"method":"Page.event"}\0' + reply[:10])
                os.write(reply_fd, reply[10:])
            finally:
                os.close(command_fd)
                os.close(reply_fd)

        thread = threading.Thread(target=browser)
        thread.start()
        try:
            self.assertEqual(control.call("Page.test", session="tab-session"), {"ready": True})
        finally:
            thread.join()
            control.close()

    def test_browser_exit_wakes_waiter_and_timeout_does_not_accumulate_requests(self):
        read_fd, reply_fd = os.pipe()
        command_fd, write_fd = os.pipe()
        control = DevTools(read_fd, write_fd)
        try:
            with self.assertRaisesRegex(BrowserError, "timed out"):
                control.call("Page.test", timeout=0.01)
            self.assertEqual(control.pending, {})
            os.close(reply_fd)
            with self.assertRaises(BrowserError):
                control.call("Page.test")
        finally:
            os.close(command_fd)
            control.close()

    def test_launch_helper_preserves_chromium_pipe_descriptors(self):
        chrome_read, parent_write = os.pipe()
        parent_read, chrome_write = os.pipe()
        script = (
            "import os,json; data=os.read(3,4096); "
            "message=json.loads(data[:-1]); "
            "os.write(4,json.dumps({'id':message['id'],'result':{'ok':True}}).encode()+b'\\0')"
        )
        process = subprocess.Popen(
            [sys.executable, "-m", "webdisplay.browser", "--pipe-in", str(chrome_read),
             "--pipe-out", str(chrome_write), "--", sys.executable, "-c", script],
            pass_fds=(chrome_read, chrome_write),
        )
        os.close(chrome_read)
        os.close(chrome_write)
        control = DevTools(parent_read, parent_write)
        try:
            self.assertEqual(control.call("test"), {"ok": True})
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            control.close()


class PersistentBrowserTests(unittest.TestCase):
    def setUp(self):
        self.browser = PersistentBrowser(Mock(), "http://127.0.0.1:1234/scene")
        self.browser.process = Mock(pid=42)
        self.browser.process.poll.return_value = None
        self.browser.local = Tab("local", "local-session", self.browser.local_url)
        self.targets = {"local": self.browser.local.url}
        self.sequence = 0
        self.browser.control = Mock()
        self.browser.control.call.side_effect = self.call
        self.ready_state = "complete"
        self.unreachable = ""
        self.navigation_error = ""
        self.status = 200

    def call(self, method, params=None, session=None):
        params = params or {}
        if method == "Target.getTargets":
            return {"targetInfos": [
                {"targetId": target, "type": "page"} for target in self.targets
            ]}
        if method == "Target.createTarget":
            self.sequence += 1
            target = str(self.sequence)
            self.targets[target] = params["url"]
            return {"targetId": target}
        if method == "Target.attachToTarget":
            return {"sessionId": params["targetId"] + "-session"}
        if method == "Target.closeTarget":
            del self.targets[params["targetId"]]
            return {"success": True}
        if method == "Page.navigate":
            self.targets[session.removesuffix("-session")] = params["url"]
            return {"errorText": self.navigation_error} if self.navigation_error else {}
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {
                "url": self.targets[session.removesuffix("-session")],
                "unreachableUrl": self.unreachable,
            }}}
        if method == "Runtime.evaluate":
            return {"result": {"value": {"state": self.ready_state, "status": self.status}}}
        return {}

    def test_two_websites_retain_tabs_without_renavigation_and_third_evicts_lru(self):
        a = self.browser.prepare("a", "https://a.example")
        self.browser.activate(a)
        b = self.browser.prepare("b", "https://b.example")
        self.browser.activate(b)
        self.assertIs(self.browser.prepare("a", "https://a.example"), a)
        self.browser.activate(a)
        c = self.browser.prepare("c", "https://c.example")
        self.assertEqual(list(self.browser.tabs), ["a", "c"])
        self.assertNotIn(b.target, self.targets)
        self.assertIn(a.target, self.targets)
        self.assertEqual(len(self.targets), 3)  # two websites plus the local scene UI
        navigations = [c for c in self.browser.control.call.call_args_list if c.args[0] == "Page.navigate"]
        self.assertEqual(len(navigations), 3)
        self.browser.activate(c)
        self.browser.prepare("b", "https://b.example")
        self.assertNotIn(a.target, self.targets)
        self.browser.launch.assert_not_called()

    def test_hidden_tabs_freeze_and_cached_tab_resumes_before_activation(self):
        a = self.browser.prepare("a", "https://a.example")
        self.browser.activate(a)
        b = self.browser.prepare("b", "https://b.example")
        self.browser.activate(b)
        self.browser.control.call.assert_any_call("Page.setWebLifecycleState", {"state": "frozen"}, a.session)
        self.browser.prepare("a", "https://a.example")
        self.browser.control.call.assert_any_call("Page.setWebLifecycleState", {"state": "active"}, a.session)
        self.assertIs(self.browser.active, b)

    def test_navigation_changes_reload_only_changed_tab_and_popups_are_closed(self):
        a = self.browser.prepare("a", "https://a.example")
        self.browser.activate(a)
        self.targets["popup"] = "https://popup.example"
        changed = self.browser.prepare("a", "https://changed.example")
        self.assertNotEqual(changed.target, a.target)
        self.assertNotIn(a.target, self.targets)
        self.assertNotIn("popup", self.targets)
        self.assertEqual(len(self.browser.tabs), 1)

    def test_loading_and_navigation_errors_do_not_activate_pending_tab(self):
        tab = self.browser.prepare("a", "https://a.example")
        self.ready_state = "loading"
        self.assertFalse(self.browser.ready(tab))
        self.ready_state = "complete"
        self.assertTrue(self.browser.ready(tab))
        self.status = 503
        with self.assertRaises(BrowserError):
            self.browser.ready(tab)
        self.status = 200
        self.unreachable = "https://a.example"
        with self.assertRaises(BrowserError):
            self.browser.ready(tab)
        self.assertIsNone(self.browser.active)
        self.browser.discard(tab)
        self.assertEqual(self.browser.tabs, {})
        self.navigation_error = "net::ERR_CONNECTION_REFUSED"
        with self.assertRaises(BrowserError):
            self.browser.prepare("b", "https://b.example")
        self.assertEqual(self.browser.tabs, {})

    def test_browser_crash_is_not_mistaken_for_a_cached_ready_page(self):
        tab = self.browser.prepare("a", "https://a.example")
        self.browser.process.poll.return_value = 1
        with self.assertRaises(BrowserError):
            self.browser.ready(tab)

    def test_initial_control_timeout_closes_partial_browser_and_allows_clean_retry(self):
        from unittest.mock import patch

        process = Mock(pid=42)
        process.poll.return_value = None
        launch = Mock(return_value=process)
        browser = PersistentBrowser(launch, "http://127.0.0.1:1234/scene")
        with patch("webdisplay.browser.DevTools") as transport, \
             patch("webdisplay.scene_player.stop_process") as stop:
            control = transport.return_value
            control.call.side_effect = BrowserError("Browser control timed out")
            with self.assertRaises(BrowserError):
                browser.ensure()
            control.call.assert_called_once_with("Target.getTargets", None, None, timeout=20)
            stop.assert_called_once_with(process)
            control.close.assert_called_once()
            self.assertIsNone(browser.process)
            self.assertIsNone(browser.control)
            self.assertIsNone(browser.local)
            control.call.side_effect = [
                {"targetInfos": [{"type": "page", "targetId": "local", "url": browser.local_url}]},
                {"sessionId": "session"},
            ]
            browser.ensure()
            self.assertEqual(browser.local.target, "local")
            self.assertEqual(launch.call_count, 2)
            browser.close()
