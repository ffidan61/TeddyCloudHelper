import tarfile

import pytest

from teddycloudhelper import backup


@pytest.fixture
def project(tmp_path):
    """A project with config-ish content AND audio content that must stay out."""
    p = tmp_path / "project"
    (p / "config").mkdir(parents=True)
    (p / "config" / "config.ini").write_text("setting=1")
    (p / "certs" / "client").mkdir(parents=True)
    (p / "certs" / "client" / "client.der").write_bytes(b"box cert")
    (p / "webui-pki" / "ca").mkdir(parents=True)
    (p / "webui-pki" / "ca" / "ca.crt").write_text("pem")
    (p / "docker-compose.yml").write_text("services: {}")
    (p / "teddycloudhelper.json").write_text("{}")
    (p / "content").mkdir()
    (p / "content" / "audio.taf").write_bytes(b"x" * 1000)
    (p / "library").mkdir()
    (p / "library" / "song.taf").write_bytes(b"x" * 1000)
    return p


def test_create_backup_includes_config_excludes_audio(project):
    path = backup.create_backup(project)

    assert path.parent == backup.default_backup_dir(project)
    members = backup.archive_members(path)
    assert "config/config.ini" in members
    assert "certs/client/client.der" in members
    assert "teddycloudhelper.json" in members
    assert not any(m.startswith(("content", "library")) for m in members)


def test_create_backup_empty_project_raises(tmp_path):
    with pytest.raises(backup.BackupError, match="Nothing to back up"):
        backup.create_backup(tmp_path)


def test_backup_names_never_collide(project):
    first = backup.create_backup(project)
    second = backup.create_backup(project)
    assert first != second


def test_list_backups_newest_first(project):
    assert backup.list_backups(project) == []
    first = backup.create_backup(project)
    second = backup.create_backup(project)
    assert backup.list_backups(project) == [second, first]


def test_restore_roundtrip(project, tmp_path):
    archive = backup.create_backup(project)
    target = tmp_path / "fresh"
    target.mkdir()

    restored = backup.restore_backup(target, archive)

    assert "config/config.ini" in restored
    assert (target / "config" / "config.ini").read_text() == "setting=1"
    assert (target / "certs" / "client" / "client.der").read_bytes() == b"box cert"
    assert not (target / "content").exists()


def test_restore_overwrites_existing(project):
    archive = backup.create_backup(project)
    (project / "config" / "config.ini").write_text("changed=1")

    backup.restore_backup(project, archive)

    assert (project / "config" / "config.ini").read_text() == "setting=1"


def test_restore_rejects_traversal(tmp_path):
    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("evil")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="config/../../evil.txt")

    with pytest.raises(backup.BackupError, match="unsafe path"):
        backup.restore_backup(tmp_path, evil)
    assert not (tmp_path.parent / "evil.txt").exists()


def test_restore_rejects_unknown_top_level(tmp_path):
    foreign = tmp_path / "foreign.tar.gz"
    payload = tmp_path / "audio.taf"
    payload.write_bytes(b"x")
    with tarfile.open(foreign, "w:gz") as tar:
        tar.add(payload, arcname="content/audio.taf")

    with pytest.raises(backup.BackupError, match="Unexpected item"):
        backup.restore_backup(tmp_path, foreign)
    assert not (tmp_path / "content").exists()


def test_backup_covers_adopted_compose_filenames(tmp_path):
    # Adopted installs keep their original compose filename (compose.yaml
    # etc.) — it must be backed up and restorable like docker-compose.yml.
    (tmp_path / "compose.yaml").write_text("services: {}")
    archive = backup.create_backup(tmp_path)
    assert backup.archive_members(archive) == ["compose.yaml"]


def test_restore_missing_archive(tmp_path):
    with pytest.raises(backup.BackupError, match="not found"):
        backup.restore_backup(tmp_path, tmp_path / "nope.tar.gz")


def test_restore_garbage_archive(tmp_path):
    bad = tmp_path / "bad.tar.gz"
    bad.write_text("this is not a tarball")
    with pytest.raises(backup.BackupError, match="not a readable"):
        backup.restore_backup(tmp_path, bad)
