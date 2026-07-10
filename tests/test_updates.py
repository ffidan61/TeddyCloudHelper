"""Best-effort update hint from GitHub tags — never breaks startup."""

from teddycloudhelper import updates


def fake_tags(monkeypatch, *names):
    monkeypatch.setattr(
        updates, "_get_json", lambda url: [{"name": n} for n in names]
    )


def test_parse_version():
    assert updates._parse_version("v0.11.1") == (0, 11, 1)
    assert updates._parse_version("0.12.0") == (0, 12, 0)
    assert updates._parse_version("v1.2.3-rc1") == (1, 2, 3)
    assert updates._parse_version("nightly") is None


def test_latest_helper_tag_picks_highest(monkeypatch):
    # Not sorted, and a non-version tag mixed in — must be ignored.
    fake_tags(monkeypatch, "v0.9.0", "v0.11.1", "latest", "v0.10.2")
    assert updates.latest_helper_tag() == "v0.11.1"


def test_latest_helper_tag_none_on_network_error(monkeypatch):
    def boom(url):
        raise OSError("no network")

    monkeypatch.setattr(updates, "_get_json", boom)
    monkeypatch.setattr(updates, "_tag_names_from_git", lambda url: None)
    assert updates.latest_helper_tag() is None


def test_latest_helper_tag_falls_back_to_git(monkeypatch):
    # The anonymous GitHub API 404s while the repo is private — git
    # ls-remote over the install SSH URL must take over.
    def api_404(url):
        raise OSError("HTTP Error 404")

    monkeypatch.setattr(updates, "_get_json", api_404)
    monkeypatch.setattr(
        updates, "_tag_names_from_git", lambda url: ["v0.9.0", "v0.15.0", "latest"]
    )
    assert updates.latest_helper_tag() == "v0.15.0"


def test_git_tag_names_parses_ls_remote(monkeypatch):
    class Done:
        returncode = 0
        stdout = (
            "abc\trefs/tags/v0.14.0\n"
            "def\trefs/tags/v0.15.0\n"
            "ghi\trefs/tags/v0.15.0^{}\n"  # annotated-tag deref entry
        )

    monkeypatch.setattr(updates.subprocess, "run", lambda *a, **kw: Done())
    assert updates._tag_names_from_git("git@example:x.git") == [
        "v0.14.0",
        "v0.15.0",
        "v0.15.0",
    ]


def test_git_tag_names_none_when_git_fails(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("git not found")

    monkeypatch.setattr(updates.subprocess, "run", boom)
    assert updates._tag_names_from_git("git@example:x.git") is None


def test_update_notice_when_newer_available(monkeypatch):
    fake_tags(monkeypatch, "v0.12.0")
    notice = updates.update_notice("0.11.1")
    assert notice is not None
    assert "v0.12.0" in notice
    assert "0.11.1" in notice
    # Must name the tool explicitly — this notice is about TeddyCloudHelper
    # itself, not the TeddyCloud server (see doctor's image-freshness check).
    assert "TeddyCloudHelper" in notice


def test_update_notice_none_when_current(monkeypatch):
    fake_tags(monkeypatch, "v0.11.1")
    assert updates.update_notice("0.11.1") is None


def test_update_notice_none_when_installed_is_newer(monkeypatch):
    # Running an unreleased local build must not nag.
    fake_tags(monkeypatch, "v0.11.1")
    assert updates.update_notice("0.12.0") is None


def test_update_notice_none_on_failure(monkeypatch):
    monkeypatch.setattr(updates, "latest_helper_tag", lambda repo=updates.HELPER_REPO: None)
    assert updates.update_notice("0.11.1") is None
