"""One-time LAN download link (+ terminal QR code) for a single file.

Getting a browser client certificate (``.p12``) onto a phone or tablet is
the fiddly part of mTLS — mail and messengers are the wrong place for key
material. This serves ONE file, ONCE, for a few minutes, at an unguessable
token URL on the LAN; the ``.p12`` password (if one was set) protects the
key itself on top. After the download (or the timeout) the server is gone.
"""

from __future__ import annotations

import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import qrcode

SHARE_TIMEOUT_SECONDS = 300.0


def lan_ip() -> str | None:
    """Best-effort primary LAN IP of this machine (no packets are sent —
    a connected UDP socket only selects the outgoing interface)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))  # TEST-NET-1, never reached
            ip = sock.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


def print_qr(url: str) -> None:
    """Render *url* as a scannable QR code on the terminal."""
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    # invert=True draws dark modules on the (usually dark) terminal
    # background's inverse — the variant phone cameras actually scan.
    qr.print_ascii(out=sys.stdout, invert=True)


class OneTimeShare:
    """Serve *file_path* once at a random token URL, then refuse everything.

    The server binds an ephemeral port on all interfaces immediately;
    ``url`` is built from *display_host* (the address the phone can reach).
    Nothing is served until :meth:`serve_until_downloaded` runs.
    """

    def __init__(
        self,
        file_path: Path,
        display_host: str,
        timeout: float = SHARE_TIMEOUT_SECONDS,
    ) -> None:
        data = file_path.read_bytes()
        url_path = f"/{secrets.token_urlsafe(16)}/{quote(file_path.name)}"
        self._timeout = timeout
        self._downloaded = threading.Event()
        downloaded = self._downloaded

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                if self.path != url_path or downloaded.is_set():
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-pkcs12")
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{file_path.name}"'
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                downloaded.set()

            def log_message(self, *args) -> None:  # keep the terminal clean
                pass

        self._server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        # Poll in short slices so Ctrl-C stays responsive between requests.
        self._server.timeout = 0.5
        self.url = f"http://{display_host}:{self._server.server_port}{url_path}"

    def serve_until_downloaded(self) -> bool:
        """Block until the file was fetched (True) or the window expired.

        Always tears the server down afterwards — the link is dead either
        way, including on Ctrl-C.
        """
        deadline = time.monotonic() + self._timeout
        try:
            while not self._downloaded.is_set() and time.monotonic() < deadline:
                self._server.handle_request()
        finally:
            self._server.server_close()
        return self._downloaded.is_set()
