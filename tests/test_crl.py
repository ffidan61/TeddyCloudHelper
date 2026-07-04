import pytest
from cryptography import x509

from teddycloudhelper.certs import ca, crl


@pytest.fixture
def project(tmp_path):
    ca.create_ca(tmp_path)
    return tmp_path


def load_crl(project):
    return x509.load_pem_x509_crl(crl.crl_path(project).read_bytes())


def test_ensure_crl_creates_empty_crl(project):
    path = crl.ensure_crl(project)

    assert path.is_file()
    assert crl.revoked_serials(project) == []
    parsed = load_crl(project)
    ca_cert, _ = ca.load_ca(project)
    assert parsed.is_signature_valid(ca_cert.public_key())


def test_ensure_crl_keeps_existing(project):
    crl.ensure_crl(project)
    crl.revoke_serial(project, 7)
    crl.ensure_crl(project)  # must not wipe the revocation
    assert crl.revoked_serials(project) == [7]


def test_revoke_serial(project):
    crl.revoke_serial(project, 42)
    assert crl.revoked_serials(project) == [42]


def test_revoke_works_without_prior_crl(project):
    # No ensure_crl() call first — revoke must bootstrap the file itself.
    crl.revoke_serial(project, 1)
    assert crl.revoked_serials(project) == [1]


def test_revoke_is_idempotent_and_preserves_entries(project):
    crl.revoke_serial(project, 1)
    crl.revoke_serial(project, 2)
    crl.revoke_serial(project, 1)

    assert sorted(crl.revoked_serials(project)) == [1, 2]
    assert len(list(load_crl(project))) == 2


def test_revoked_serials_without_crl(project):
    assert crl.revoked_serials(project) == []


def test_garbage_crl_raises(project):
    crl.ensure_crl(project)
    crl.crl_path(project).write_text("not a crl")
    with pytest.raises(crl.CertError, match="not a valid PEM CRL"):
        crl.revoked_serials(project)


def test_crl_without_ca_raises(tmp_path):
    with pytest.raises(crl.CertError, match="No WebUI CA"):
        crl.ensure_crl(tmp_path)
