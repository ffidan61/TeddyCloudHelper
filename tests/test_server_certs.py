import ipaddress

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from teddycloudhelper.certs import ca, server_certs


def load_cert(path):
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_webui_cert_with_dns_name(tmp_path):
    path = server_certs.create_webui_server_cert(tmp_path, "tc.example.com")

    cert = load_cert(path)
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["tc.example.com"]
    assert (server_certs.server_dir(tmp_path) / "server.key").is_file()


def test_webui_cert_with_ip(tmp_path):
    path = server_certs.create_webui_server_cert(tmp_path, "192.168.1.10")
    san = load_cert(path).extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("192.168.1.10")]


def test_webui_cert_empty_hostname_raises(tmp_path):
    with pytest.raises(server_certs.CertError, match="empty"):
        server_certs.create_webui_server_cert(tmp_path, "  ")


def test_webui_cert_matches(tmp_path):
    assert not server_certs.webui_cert_matches(tmp_path, "tc.example.com")  # none yet
    server_certs.create_webui_server_cert(tmp_path, "tc.example.com")
    assert server_certs.webui_cert_matches(tmp_path, "tc.example.com")
    assert server_certs.webui_cert_matches(tmp_path, " tc.example.com ")  # whitespace ok
    assert not server_certs.webui_cert_matches(tmp_path, "other.example.com")  # stale


def test_webui_cert_matches_ip(tmp_path):
    server_certs.create_webui_server_cert(tmp_path, "192.168.1.10")
    assert server_certs.webui_cert_matches(tmp_path, "192.168.1.10")
    assert not server_certs.webui_cert_matches(tmp_path, "192.168.1.11")


def make_box_ca_der(tmp_path):
    """Write a valid DER cert at the teddycloud ca.der location."""
    ca.create_ca(tmp_path)  # reuse our CA machinery to get any valid cert
    cert, _ = ca.load_ca(tmp_path)
    der_path = server_certs.box_ca_path(tmp_path)
    der_path.parent.mkdir(parents=True, exist_ok=True)
    der_path.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    return der_path


def test_export_box_ca_to_directory(tmp_path):
    make_box_ca_der(tmp_path)
    dest_dir = tmp_path / "out"
    dest_dir.mkdir()

    exported = server_certs.export_box_ca(tmp_path, dest_dir)

    assert exported == dest_dir / "ca.der"
    assert exported.read_bytes() == server_certs.box_ca_path(tmp_path).read_bytes()


def test_export_box_ca_to_file_path(tmp_path):
    make_box_ca_der(tmp_path)
    exported = server_certs.export_box_ca(tmp_path, tmp_path / "flash" / "ca.der")
    assert exported.is_file()


def test_export_box_ca_missing(tmp_path):
    with pytest.raises(server_certs.CertError, match="first container"):
        server_certs.export_box_ca(tmp_path, tmp_path)


def test_export_box_ca_invalid_der(tmp_path):
    path = server_certs.box_ca_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("garbage")
    with pytest.raises(server_certs.CertError, match="not a valid DER"):
        server_certs.export_box_ca(tmp_path, tmp_path)
