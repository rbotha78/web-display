"""HTTP-only discovery page for the setup AP; administration remains HTTPS-only."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address, ip_network


PAGE = b"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Web Display Wi-Fi setup</title></head><body>
<h1>Web Display Wi-Fi setup</h1>
<p>This setup network has no internet access. Stay connected to configure the display.</p>
<p><a href="https://10.42.0.1:8443/">Open secure administration</a></p>
<p>Verify the certificate fingerprint against the display before continuing.
Use the pairing code shown on the display.</p>
<p>If this window cannot open the secure page, open https://10.42.0.1:8443/
in your normal browser. Choose to stay on this Wi-Fi network if your phone asks.</p>
<p>After saving home Wi-Fi settings, reconnect your phone to that network and
open the display's new address.</p>
</body></html>"""


class Portal:
    def __init__(self, address: str = "10.42.0.1", port: int = 80):
        subnet = ip_network(f"{address}/24", strict=False)

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def get_request(self):
                request, client = super().get_request()
                request.settimeout(5)
                return request, client

            def verify_request(self, request, client_address):
                return ip_address(client_address[0]) in subnet

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.respond(body=True)

            def do_HEAD(self):
                self.respond(body=False)

            def respond(self, body: bool):
                landing = self.path == "/"
                self.send_response(200 if landing else 302)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(PAGE)) if landing else "0")
                if not landing:
                    self.send_header("Location", "http://10.42.0.1/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy",
                                 "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Connection", "close")
                self.end_headers()
                if landing and body:
                    try:
                        self.wfile.write(PAGE)
                    except (BrokenPipeError, ConnectionResetError):
                        self.close_connection = True

            def log_message(self, format, *args):
                pass

        self.server = Server((address, port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
