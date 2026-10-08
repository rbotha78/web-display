from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .app import local_addresses


def reachable(url: str) -> bool:
    if not url:
        return False
    request = urllib.request.Request(url, headers={"User-Agent": "WebDisplay/1"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def certificate_fingerprint(certificate_path: Path) -> str:
    if not certificate_path.exists():
        return "certificate not available"
    import ssl

    pem = certificate_path.read_text(encoding="ascii")
    der = ssl.PEM_cert_to_DER_cert(pem)
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[index : index + 2] for index in range(0, len(digest), 2))


def read_setup_ap(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not all(
        isinstance(data.get(key), str) for key in ("ssid", "password", "address")
    ):
        return None
    return data


def fallback_html(
    pairing_path: Path,
    certificate_path: Path,
    port: int,
    setup_ap_path: Path | None = None,
) -> str:
    addresses = local_addresses()
    address = addresses[0] if addresses else socket.gethostname()
    setup_ap = read_setup_ap(setup_ap_path)
    ap_section = ""
    if setup_ap:
        address = setup_ap["address"]
        ap_section = (
            "<p>No internet connection. Join the setup Wi-Fi network:</p>"
            f"<p>Network: <strong>{html.escape(setup_ap['ssid'])}</strong></p>"
            f"<p>Password: <strong>{html.escape(setup_ap['password'])}</strong></p>"
        )
    admin_url = f"https://{address}:{port}/"
    pairing = ""
    if pairing_path.exists():
        code = pairing_path.read_text(encoding="ascii").strip()
        pairing = f"<p>First-use pairing code:</p><strong>{html.escape(code)}</strong>"
    fingerprint = certificate_fingerprint(certificate_path)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5"><style>
body{{margin:0;background:#111;color:#eee;font:3vw system-ui,sans-serif;
display:grid;place-items:center;min-height:100vh;text-align:center}}
main{{max-width:85vw}} strong{{font-size:2em;letter-spacing:.2em}}
</style></head><body><main><h1>Web display setup</h1>
{ap_section}<p>Administration: {html.escape(admin_url)}</p>{pairing}
<p>TLS certificate SHA-256:<br><small>{html.escape(fingerprint)}</small></p>
<p>No configured web page is currently reachable.</p></main></body></html>"""


def write_fallback(
    path: Path,
    pairing_path: Path,
    certificate_path: Path,
    port: int,
    setup_ap_path: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".fallback.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(fallback_html(pairing_path, certificate_path, port, setup_ap_path))
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def screen_size() -> str | None:
    result = subprocess.run(
        ["xrandr", "--current"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    match = re.search(r"\bcurrent\s+(\d+)\s+x\s+(\d+)", result.stdout)
    return f"{match.group(1)},{match.group(2)}" if match else None


def launch(browser: str, destination: str) -> subprocess.Popen[bytes]:
    arguments = [
        browser,
        "--kiosk",
        "--no-first-run",
        "--disable-translate",
        "--disable-sync",
        "--disable-features=Translate",
        "--password-store=basic",
        "--user-data-dir=/var/lib/web-display-kiosk/chromium",
        "--window-position=0,0",
    ]
    size = screen_size()
    if size:
        arguments.append(f"--window-size={size}")
    arguments.append(destination)
    return subprocess.Popen(arguments)


OFFLINE_HTML = (
    '<!doctype html><html><head><meta charset="utf-8"></head>'
    '<body style="margin:0;background:#111;color:#777;font:2vw system-ui,sans-serif;'
    'display:grid;place-items:center;min-height:100vh">Waiting for network connection</body></html>'
)
FAILURE_THRESHOLD = 2


def choose_destination(
    target: str,
    reachable_now: bool,
    failures: int,
    ap_active: bool,
    current: str,
    fallback_uri: str,
    offline_uri: str,
    threshold: int = FAILURE_THRESHOLD,
) -> tuple[str, bool]:
    """Return (page to show, whether to show the offline icon)."""
    if ap_active or not target:
        return fallback_uri, False
    if reachable_now:
        return target, False
    if current == target:
        return target, failures >= threshold
    return offline_uri, True


class Reachability(threading.Thread):
    def __init__(self, target_path: Path, interval: int):
        super().__init__(daemon=True)
        self.target_path = target_path
        self.interval = interval
        self.ok = False
        self.failures = 0
        self.checked = threading.Event()

    def run(self) -> None:
        while True:
            try:
                target = self.target_path.read_text(encoding="utf-8").strip()
            except OSError:
                target = ""
            ok = reachable(target)
            self.failures = 0 if ok else self.failures + 1
            self.ok = ok
            self.checked.set()
            time.sleep(self.interval)


class OfflineIcon:
    """Small override-redirect X window shown above the browser while offline."""

    SIZE = 72

    def __init__(self) -> None:
        self.display = None
        self.window = None
        self.visible = False
        self.failed = False

    def _create(self) -> None:
        from Xlib import X, display

        self.display = display.Display()
        screen = self.display.screen()
        size = self.SIZE
        colormap = screen.default_colormap
        red = colormap.alloc_color(0xC000, 0x3900, 0x2B00).pixel
        white = colormap.alloc_color(0xFFFF, 0xFFFF, 0xFFFF).pixel
        pixmap = screen.root.create_pixmap(size, size, screen.root_depth)
        gc = screen.root.create_gc(foreground=red, line_width=5, cap_style=X.CapRound)
        pixmap.fill_rectangle(gc, 0, 0, size, size)
        gc.change(foreground=red)
        pixmap.fill_arc(gc, 2, 2, size - 4, size - 4, 0, 360 * 64)
        gc.change(foreground=white, line_width=5)
        for radius in (14, 24, 34):
            pixmap.arc(gc, size // 2 - radius, 50 - radius, 2 * radius, 2 * radius, 40 * 64, 100 * 64)
        pixmap.fill_arc(gc, size // 2 - 4, 46, 8, 8, 0, 360 * 64)
        pixmap.line(gc, 14, 58, 58, 14)
        x = max(screen.width_in_pixels - size - 16, 0)
        self.window = screen.root.create_window(
            x, 16, size, size, 0, screen.root_depth,
            X.InputOutput, X.CopyFromParent,
            override_redirect=1, background_pixmap=pixmap,
        )
        self.display.sync()

    def set_visible(self, visible: bool) -> None:
        if self.failed:
            return
        try:
            if self.window is None:
                if not visible:
                    return
                self._create()
            from Xlib import X

            if visible:
                if not self.visible:
                    self.window.map()
                self.window.configure(stack_mode=X.Above)
            elif self.visible:
                self.window.unmap()
            self.visible = visible
            self.display.sync()
        except Exception as error:
            print(f"Offline icon disabled: {error}", flush=True)
            self.failed = True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", default="/usr/bin/chromium")
    parser.add_argument("--target", default="/run/web-display/target-url")
    parser.add_argument("--pairing-code", default="/run/web-display/pairing-code")
    parser.add_argument("--certificate", default="/var/lib/web-display/tls/cert.pem")
    parser.add_argument("--setup-ap", default="/run/web-display/setup-ap.json")
    parser.add_argument("--fallback", default="/run/web-display-kiosk/fallback.html")
    parser.add_argument("--admin-port", type=int, default=8443)
    parser.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()
    target_path = Path(args.target)
    pairing_path = Path(args.pairing_code)
    certificate_path = Path(args.certificate)
    fallback_path = Path(args.fallback)
    setup_ap_path = Path(args.setup_ap)
    offline_path = fallback_path.with_name("offline.html")
    process: subprocess.Popen[bytes] | None = None
    destination = ""
    icon = OfflineIcon()
    monitor = Reachability(target_path, args.interval)
    monitor.start()
    monitor.checked.wait(timeout=args.interval)
    last_fallback = ""

    def stop(*_: object) -> None:
        if process and process.poll() is None:
            process.terminate()
        raise SystemExit

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while True:
        target = target_path.read_text(encoding="utf-8").strip() if target_path.exists() else ""
        fallback = fallback_html(pairing_path, certificate_path, args.admin_port, setup_ap_path)
        if fallback != last_fallback:
            write_fallback(
                fallback_path, pairing_path, certificate_path, args.admin_port, setup_ap_path
            )
            offline_path.write_text(OFFLINE_HTML, encoding="utf-8")
            last_fallback = fallback
        desired, show_icon = choose_destination(
            target,
            monitor.ok,
            monitor.failures,
            read_setup_ap(setup_ap_path) is not None,
            destination,
            fallback_path.as_uri(),
            offline_path.as_uri(),
        )
        if desired != destination or process is None or process.poll() is not None:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process = launch(args.browser, desired)
            destination = desired
        icon.set_visible(show_icon)
        time.sleep(1)


if __name__ == "__main__":
    main()
