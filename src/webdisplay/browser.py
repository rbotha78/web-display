"""Private Chromium control over inherited pipes; no debugging TCP listener."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable


class BrowserError(Exception):
    pass


class DevTools:
    def __init__(self, read_fd: int, write_fd: int):
        self.read_fd = read_fd
        self.write_fd = write_fd
        self.condition = threading.Condition()
        self.sequence = 0
        self.pending: dict[int, dict[str, object] | None] = {}
        self.closed = False
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        buffer = b""
        try:
            while chunk := os.read(self.read_fd, 65536):
                buffer += chunk
                if len(buffer) > 4 * 1024 * 1024:
                    break
                while b"\0" in buffer:
                    line, buffer = buffer.split(b"\0", 1)
                    reply = json.loads(line)
                    if isinstance(reply, dict):
                        with self.condition:
                            identifier = reply.get("id")
                            if identifier in self.pending:
                                self.pending[identifier] = reply
                                self.condition.notify_all()
        except (OSError, ValueError):
            pass
        finally:
            with self.condition:
                self.closed = True
                self.condition.notify_all()
            os.close(self.read_fd)

    def call(
        self, method: str, params: dict[str, object] | None = None,
        session: str | None = None, timeout: float = 5,
    ) -> dict[str, object]:
        with self.condition:
            if self.closed:
                raise BrowserError("Browser control disconnected")
            self.sequence += 1
            identifier = self.sequence
            message: dict[str, object] = {"id": identifier, "method": method, "params": params or {}}
            if session:
                message["sessionId"] = session
            self.pending[identifier] = None
            try:
                data = json.dumps(message).encode() + b"\0"
                while data:
                    written = os.write(self.write_fd, data)
                    data = data[written:]
                if not self.condition.wait_for(
                    lambda: self.pending[identifier] is not None or self.closed, timeout,
                ):
                    raise BrowserError(f"Browser control timed out: {method}")
                reply = self.pending[identifier]
                if reply is None or "error" in reply:
                    raise BrowserError(f"Browser control command failed: {method}")
                result = reply.get("result")
                if not isinstance(result, dict):
                    raise BrowserError("Invalid browser control reply")
                return result
            except OSError:
                raise BrowserError("Browser control disconnected") from None
            finally:
                del self.pending[identifier]

    def close(self) -> None:
        with self.condition:
            if self.write_fd >= 0:
                os.close(self.write_fd)
                self.write_fd = -1
            self.closed = True
            self.condition.notify_all()
        self.reader.join(timeout=2)


@dataclass
class Tab:
    target: str
    session: str
    url: str


class PersistentBrowser:
    def __init__(self, launch: Callable, local_url: str):
        self.launch = launch
        self.local_url = local_url
        self.process: subprocess.Popen[bytes] | None = None
        self.control: DevTools | None = None
        self.local: Tab | None = None
        self.tabs: OrderedDict[str, Tab] = OrderedDict()
        self.active: Tab | None = None

    def _call(
        self, method: str, params: dict[str, object] | None = None,
        tab: Tab | None = None, timeout: float = 5,
    ) -> dict[str, object]:
        if self.control is None or self.process is None or self.process.poll() is not None:
            raise BrowserError("Browser is not running")
        if timeout != 5:
            return self.control.call(method, params, tab.session if tab else None, timeout=timeout)
        return self.control.call(method, params, tab.session if tab else None)

    def _attach(self, target: str, url: str) -> Tab:
        reply = self._call("Target.attachToTarget", {"targetId": target, "flatten": True})
        session = reply.get("sessionId")
        if not isinstance(session, str):
            raise BrowserError("Could not attach to browser tab")
        return Tab(target, session, url)

    def ensure(self) -> None:
        if self.process is not None:
            if self.process.poll() is not None:
                raise BrowserError("Browser exited unexpectedly")
            return
        chrome_read, parent_write = os.pipe()
        parent_read, chrome_write = os.pipe()
        try:
            self.process = self.launch(self.local_url, (chrome_read, chrome_write))
        except OSError:
            os.close(parent_write)
            os.close(parent_read)
            raise
        finally:
            os.close(chrome_read)
            os.close(chrome_write)
        self.control = DevTools(parent_read, parent_write)
        try:
            self._initialise()
        except BrowserError:
            self.close()
            raise

    def _initialise(self) -> None:
        # Cold Chromium startup on Pi 2 can exceed the normal command timeout.
        targets = self._call("Target.getTargets", timeout=20).get("targetInfos")
        if not isinstance(targets, list):
            raise BrowserError("Could not find browser tabs")
        for target in targets:
            if isinstance(target, dict) and target.get("type") == "page":
                identifier = target.get("targetId")
                if not isinstance(identifier, str):
                    raise BrowserError("Invalid browser tab")
                if self.local is None:
                    self.local = self._attach(identifier, self.local_url)
                    if target.get("url") != self.local_url:
                        self._call("Page.navigate", {"url": self.local_url}, self.local)
                else:
                    self._call("Target.closeTarget", {"targetId": identifier})
        if self.local is None:
            reply = self._call("Target.createTarget", {"url": self.local_url})
            identifier = reply.get("targetId")
            if not isinstance(identifier, str):
                raise BrowserError("Could not create local browser tab")
            self.local = self._attach(identifier, self.local_url)

    def prepare(self, key: str | None, url: str) -> Tab:
        self.ensure()
        self._close_popups()
        if key is None:
            if self.local is None:
                raise BrowserError("Local browser tab is missing")
            return self.local
        cached = self.tabs.get(key)
        if cached and cached.url == url:
            self.tabs.move_to_end(key)
            self._call("Page.setWebLifecycleState", {"state": "active"}, cached)
            return cached
        if cached:
            self._close_tab(cached)
            del self.tabs[key]
        if len(self.tabs) >= 2:
            victim = next((k for k, tab in self.tabs.items() if tab is not self.active), next(iter(self.tabs)))
            self._close_tab(self.tabs.pop(victim))
        reply = self._call("Target.createTarget", {"url": "about:blank", "background": True})
        target = reply.get("targetId")
        if not isinstance(target, str):
            raise BrowserError("Could not create browser tab")
        tab = self._attach(target, url)
        self.tabs[key] = tab
        self._call("Page.enable", tab=tab)
        # Authentication and cookies remain in Chromium, unlike a separate HTTP probe.
        reply = self._call("Page.navigate", {"url": url}, tab)
        if reply.get("errorText") or reply.get("isDownload"):
            self.discard(tab)
            raise BrowserError("Website navigation failed")
        return tab

    def _close_tab(self, tab: Tab) -> None:
        self._call("Target.closeTarget", {"targetId": tab.target})
        if self.active is tab:
            self.active = None

    def discard(self, tab: Tab) -> None:
        for key, cached in list(self.tabs.items()):
            if cached is tab and tab is not self.active:
                self._close_tab(tab)
                del self.tabs[key]

    def _close_popups(self) -> None:
        known = {tab.target for tab in self.tabs.values()}
        if self.local:
            known.add(self.local.target)
        targets = self._call("Target.getTargets").get("targetInfos")
        if not isinstance(targets, list):
            raise BrowserError("Could not inspect browser tabs")
        for target in targets:
            if isinstance(target, dict) and target.get("type") == "page" and target.get("targetId") not in known:
                self._call("Target.closeTarget", {"targetId": target["targetId"]})

    def ready(self, tab: Tab) -> bool:
        self._close_popups()
        frame = self._call("Page.getFrameTree", tab=tab).get("frameTree")
        if not isinstance(frame, dict) or not isinstance(frame.get("frame"), dict):
            raise BrowserError("Could not inspect website readiness")
        if frame["frame"].get("unreachableUrl"):
            raise BrowserError("Website could not be loaded")
        result = self._call(
            "Runtime.evaluate",
            {
                "expression": "({state: document.readyState, status: performance.getEntriesByType('navigation')[0]?.responseStatus || 0})",
                "returnByValue": True,
            }, tab,
        ).get("result")
        if not isinstance(result, dict):
            raise BrowserError("Could not inspect website readiness")
        value = result.get("value")
        if not isinstance(value, dict):
            raise BrowserError("Could not inspect website readiness")
        if isinstance(value.get("status"), (int, float)) and value["status"] >= 500:
            raise BrowserError("Website returned a server error")
        return frame["frame"].get("url") not in ("", "about:blank", None) and value.get("state") == "complete"

    def activate(self, tab: Tab) -> None:
        self._call("Page.setWebLifecycleState", {"state": "active"}, tab)
        self._call("Target.activateTarget", {"targetId": tab.target})
        self.active = tab
        self.freeze(except_tab=tab)

    def freeze(self, except_tab: Tab | None = None) -> None:
        for tab in self.tabs.values():
            if tab is not except_tab:
                self._call("Page.setWebLifecycleState", {"state": "frozen"}, tab)

    def close(self) -> None:
        from .scene_player import stop_process

        stop_process(self.process)
        self.process = None
        if self.control:
            self.control.close()
            self.control = None
        self.tabs.clear()
        self.local = self.active = None


def exec_browser(read_fd: int, write_fd: int, arguments: list[str]) -> None:
    # dup in a fresh helper process, not preexec_fn in the threaded kiosk service.
    read_copy, write_copy = os.dup(read_fd), os.dup(write_fd)
    os.dup2(read_copy, 3, inheritable=True)
    os.dup2(write_copy, 4, inheritable=True)
    os.set_inheritable(3, True)
    os.set_inheritable(4, True)
    for fd in {read_fd, write_fd, read_copy, write_copy} - {3, 4}:
        os.close(fd)
    os.execvp(arguments[0], arguments)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipe-in", type=int, required=True)
    parser.add_argument("--pipe-out", type=int, required=True)
    parser.add_argument("browser", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.browser[1:] if args.browser[:1] == ["--"] else args.browser
    exec_browser(args.pipe_in, args.pipe_out, arguments)
