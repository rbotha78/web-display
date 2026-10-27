from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .app import local_addresses
from .carousel import Carousel, Playlist, validate_playlist
from .scene_player import SceneRenderer, reachable, stop_process

LOG = logging.getLogger(__name__)


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


def wifi_qr_payload(ssid: str, password: str) -> str:
    def escape(value: str) -> str:
        return re.sub(r'([\\;,:"])', r"\\\1", value)

    return f"WIFI:T:WPA;S:{escape(ssid)};P:{escape(password)};;"


def wifi_qr_svg(ssid: str, password: str) -> str:
    try:
        import segno
    except ImportError:
        return ""
    code = segno.make(wifi_qr_payload(ssid, password), error="m")
    stream = io.BytesIO()
    code.save(stream, kind="svg", scale=8, border=2, dark="#000", light="#fff", xmldecl=False, svgns=True)
    return stream.getvalue().decode("utf-8")


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
    qr_section = ""
    if setup_ap:
        address = setup_ap["address"]
        ap_section = (
            "<p>No internet connection. Join the setup Wi-Fi network:</p>"
            f"<p>Network: <strong>{html.escape(setup_ap['ssid'])}</strong></p>"
            f"<p>Password: <strong>{html.escape(setup_ap['password'])}</strong></p>"
        )
        qr = wifi_qr_svg(setup_ap["ssid"], setup_ap["password"])
        if qr:
            qr_section = f'<aside><div class="qr">{qr}</div><p>Scan to join</p></aside>'
    admin_url = f"https://{address}:{port}/"
    pairing = ""
    if pairing_path.exists():
        code = pairing_path.read_text(encoding="ascii").strip()
        pairing = f"<p>First-use pairing code:</p><strong>{html.escape(code)}</strong>"
    fingerprint = certificate_fingerprint(certificate_path)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5"><style>
html,body{{height:100%}}
body{{margin:0;background:#111;color:#eee;font:2.2vw/1.25 system-ui,sans-serif;
display:grid;place-items:center;overflow:hidden;text-align:center}}
.wrap{{display:flex;align-items:center;justify-content:center;gap:4vw;max-width:96vw;max-height:96vh}}
main{{flex:1 1 auto;min-width:0}} h1{{font-size:1.6em;margin:.2em 0}} p{{margin:.4em 0}}
strong{{font-size:1.5em;letter-spacing:.15em}} small{{font-size:.7em;word-break:break-all}}
aside{{flex:0 0 auto}} .qr svg{{display:block;width:min(40vh,30vw);height:min(40vh,30vw)}}
</style></head><body><div class="wrap"><main><h1>Web display setup</h1>
{ap_section}<p>Administration: {html.escape(admin_url)}</p>{pairing}
<p>TLS certificate SHA-256:<br><small>{html.escape(fingerprint)}</small></p>
<p>No configured web page is currently reachable.</p></main>{qr_section}</div></body></html>"""


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


PROFILE_DIR = Path("/var/lib/web-display-kiosk/chromium")


class DisplayRecovery:
    """Recover outputs X leaves disabled after a monitor power cycle."""

    def __init__(self) -> None:
        self.next_check = 0.0
        self.outputs: dict[str, bool] = {}

    def check(self, now: float) -> bool:
        if now < self.next_check:
            return False
        self.next_check = now + 5
        recovered = False
        try:
            result = subprocess.run(
                ["xrandr", "--prop"], check=True, capture_output=True, text=True, timeout=5,
            )
            sections = re.split(r"(?m)^(?=\S+ (?:connected|disconnected)\b)", result.stdout)
            for section in sections:
                match = re.match(r"(\S+) (connected|disconnected)\b([^\n]*)", section)
                if not match:
                    continue
                output, status, geometry = match.groups()
                if status == "disconnected":
                    self.outputs[output] = False
                    continue
                active = re.search(r"\b\d+x\d+\+\d+\+\d+\b", geometry) is not None
                if active and self.outputs.get(output, True):
                    continue
                # Mark pending before changing modes so a failed CTM reset is retried.
                self.outputs[output] = False
                subprocess.run(
                    ["xrandr", "--output", output, "--auto"],
                    check=True, capture_output=True, text=True, timeout=5,
                )
                if re.search(r"(?m)^\s+CTM:", section):
                    subprocess.run(
                        ["xrandr", "--output", output, "--set", "CTM", "1,0,0,0,1,0,0,0,1"],
                        check=True, capture_output=True, text=True, timeout=5,
                    )
                self.outputs[output] = True
                recovered = True
                LOG.warning("Recovered display output %s", output)
        except (OSError, subprocess.SubprocessError) as error:
            LOG.error("Display recovery failed: %s", error)
        return recovered


def clear_stale_profile_locks(profile: Path = PROFILE_DIR) -> None:
    """Chromium locks its profile as hostname-pid; a renamed host sees a stale foreign lock."""
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            LOG.warning("Could not remove %s", name)


def launch(
    browser: str, destination: str, control: tuple[int, int] | None = None,
) -> subprocess.Popen[bytes]:
    clear_stale_profile_locks()
    arguments = [
        browser,
        "--kiosk",
        "--no-first-run",
        "--disable-translate",
        "--disable-sync",
        "--disable-features=Translate",
        "--password-store=basic",
        f"--user-data-dir={PROFILE_DIR}",
        "--window-position=0,0",
    ]
    size = screen_size()
    if size:
        arguments.append(f"--window-size={size}")
    arguments.append(destination)
    if control:
        arguments[1:1] = [
            "--remote-debugging-pipe", "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding", "--disable-background-timer-throttling",
            "--disable-session-crashed-bubble",
        ]
        return subprocess.Popen(
            [
                sys.executable, "-m", "webdisplay.browser",
                "--pipe-in", str(control[0]), "--pipe-out", str(control[1]), "--", *arguments,
            ],
            pass_fds=control, start_new_session=True,
        )
    return subprocess.Popen(arguments, start_new_session=True)


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
    parser.add_argument("--playlist", default="/run/web-display/playlist.json")
    parser.add_argument("--mpv", default="/usr/bin/mpv")
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
    renderer: SceneRenderer | None = None
    carousel: Carousel | None = None
    previous_playlist: Playlist | None = None
    previous_revision = ""
    playlist_error = False
    display_recovery = DisplayRecovery()

    def stop(*_: object) -> None:
        raise SystemExit

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        while True:
            if display_recovery.check(time.monotonic()):
                stop_process(process)
                process = None
                # Keep destination so single-page outage recovery preserves its semantics.
                if carousel is not None:
                    carousel.stop()
                    carousel = None
            revision = ""
            try:
                published = json.loads(Path(args.playlist).read_text(encoding="utf-8"))
                playlist = validate_playlist(published, allow_runtime=True)
                revision = published.get("revision", "")
                playlist_error = False
            except FileNotFoundError:
                playlist = Playlist(enabled=False, scenes=[])
            except (OSError, ValueError):
                if not playlist_error:
                    LOG.error("Could not read published playlist; showing carousel recovery")
                playlist_error = True
                playlist = Playlist(enabled=True, scenes=[])
            ap_active = read_setup_ap(setup_ap_path) is not None
            if playlist["enabled"] and not ap_active:
                if renderer is None:
                    stop_process(process)
                    process = None
                    destination = ""
                    renderer = SceneRenderer(
                        lambda url, pipes: launch(args.browser, url, pipes), fallback_path.parent, args.mpv
                    )
                if carousel is None or playlist != previous_playlist or revision != previous_revision:
                    if carousel:
                        carousel.stop()
                    carousel = Carousel(playlist, renderer)
                    previous_playlist = playlist
                    previous_revision = revision
                icon.set_visible(False)
                carousel.tick(time.monotonic())
                time.sleep(1)
                continue
            if renderer is not None:
                renderer.close()
                renderer = None
                carousel = None
                previous_playlist = None
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
                ap_active,
                destination,
                fallback_path.as_uri(),
                offline_path.as_uri(),
            )
            if desired != destination or process is None or process.poll() is not None:
                stop_process(process)
                process = launch(args.browser, desired)
                destination = desired
            icon.set_visible(show_icon)
            time.sleep(1)
    finally:
        stop_process(process)
        if renderer is not None:
            renderer.close()


if __name__ == "__main__":
    main()
