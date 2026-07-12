"""One-time .p12 download link — single use, token-gated, self-expiring."""

import threading
import urllib.error
import urllib.request

import pytest

from teddycloudhelper import share


@pytest.fixture
def p12(tmp_path):
    path = tmp_path / "admin.p12"
    path.write_bytes(b"\x30\x82fake-pkcs12-bytes")
    return path


def serve_in_thread(one: share.OneTimeShare) -> tuple[threading.Thread, list]:
    outcome: list = []
    thread = threading.Thread(
        target=lambda: outcome.append(one.serve_until_downloaded()), daemon=True
    )
    thread.start()
    return thread, outcome


def test_share_serves_the_file_exactly_once(p12):
    one = share.OneTimeShare(p12, "127.0.0.1", timeout=10)
    thread, outcome = serve_in_thread(one)

    with urllib.request.urlopen(one.url, timeout=5) as response:
        assert response.read() == p12.read_bytes()
        assert 'filename="admin.p12"' in response.headers["Content-Disposition"]
        assert response.headers["Content-Type"] == "application/x-pkcs12"

    thread.join(timeout=5)
    assert outcome == [True]
    # The server is gone — a second fetch cannot succeed.
    with pytest.raises((urllib.error.URLError, ConnectionError)):
        urllib.request.urlopen(one.url, timeout=2)


def test_share_rejects_wrong_token_and_keeps_waiting(p12):
    one = share.OneTimeShare(p12, "127.0.0.1", timeout=10)
    thread, outcome = serve_in_thread(one)

    base = one.url.rsplit("/", 2)[0]  # strip token + filename
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(f"{base}/wrong-token/admin.p12", timeout=5)
    assert excinfo.value.code == 404

    # The real link still works after a bad guess.
    with urllib.request.urlopen(one.url, timeout=5) as response:
        assert response.read() == p12.read_bytes()
    thread.join(timeout=5)
    assert outcome == [True]


def test_share_expires_without_a_download(p12):
    one = share.OneTimeShare(p12, "127.0.0.1", timeout=0.2)
    assert one.serve_until_downloaded() is False


def test_share_url_contains_token_and_filename(p12):
    one = share.OneTimeShare(p12, "192.168.1.5", timeout=1)
    try:
        assert one.url.startswith("http://192.168.1.5:")
        assert one.url.endswith("/admin.p12")
        # Unguessable path segment between port and filename.
        token = one.url.rsplit("/", 2)[1]
        assert len(token) >= 16
    finally:
        one._server.server_close()


def test_lan_ip_is_none_or_non_loopback():
    ip = share.lan_ip()
    assert ip is None or not ip.startswith("127.")
