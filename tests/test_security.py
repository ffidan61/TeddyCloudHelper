import bcrypt
import pytest

from teddycloudhelper import security

# --- htpasswd -------------------------------------------------------------


def test_set_user_writes_bcrypt_line(tmp_path):
    path = security.set_user(tmp_path, "alice", "s3cret")

    line = path.read_text().strip()
    username, hashed = line.split(":", 1)
    assert username == "alice"
    assert bcrypt.checkpw(b"s3cret", hashed.encode())


def test_set_user_updates_existing(tmp_path):
    security.set_user(tmp_path, "alice", "old")
    security.set_user(tmp_path, "bob", "pw")
    security.set_user(tmp_path, "alice", "new")

    assert security.load_users(tmp_path) == ["bob", "alice"]
    lines = security.htpasswd_path(tmp_path).read_text().splitlines()
    alice_hash = next(line for line in lines if line.startswith("alice:")).split(":", 1)[1]
    assert bcrypt.checkpw(b"new", alice_hash.encode())
    assert not bcrypt.checkpw(b"old", alice_hash.encode())


def test_load_users_without_file(tmp_path):
    assert security.load_users(tmp_path) == []


def test_remove_user(tmp_path):
    security.set_user(tmp_path, "alice", "pw")
    security.set_user(tmp_path, "bob", "pw")

    assert security.remove_user(tmp_path, "alice") is True
    assert security.load_users(tmp_path) == ["bob"]
    assert security.remove_user(tmp_path, "alice") is False


@pytest.mark.parametrize("bad", ["", "with space", "with:colon", "tab\tname"])
def test_invalid_usernames_rejected(tmp_path, bad):
    with pytest.raises(security.SecurityError, match="Invalid username"):
        security.set_user(tmp_path, bad, "pw")


def test_empty_password_rejected(tmp_path):
    with pytest.raises(security.SecurityError, match="Password"):
        security.set_user(tmp_path, "alice", "")


# --- IP allowlist ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("192.168.1.5", "192.168.1.5"),
        ("192.168.1.5/32", "192.168.1.5"),
        (" 192.168.0.0/24 ", "192.168.0.0/24"),
        ("192.168.0.17/24", "192.168.0.0/24"),  # host bits tolerated
        ("2001:db8::1", "2001:db8::1"),
        ("2001:db8::/64", "2001:db8::/64"),
    ],
)
def test_normalize_allowlist_entry(raw, expected):
    assert security.normalize_allowlist_entry(raw) == expected


@pytest.mark.parametrize("bad", ["", "not-an-ip", "192.168.0.256", "10.0.0.0/33"])
def test_invalid_allowlist_entries_rejected(bad):
    with pytest.raises(security.SecurityError):
        security.normalize_allowlist_entry(bad)
