"""The release version: git in a checkout, the bundled VERSION when frozen."""

from khervelab import _version


def test_git_wins_over_a_stale_version_file(tmp_path, monkeypatch):
    pkg = tmp_path / "khervelab"
    pkg.mkdir()
    (pkg / "VERSION").write_text("0.1.2+stale")
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(_version, "_HERE", pkg)
    monkeypatch.setattr(_version, "git_version", lambda base, root=None: f"{base}.42+abc")
    _version.full_version.cache_clear()
    assert _version.full_version("9.9") == "9.9.42+abc"
    _version.full_version.cache_clear()


def test_frozen_build_reads_version_file(tmp_path, monkeypatch):
    pkg = tmp_path / "khervelab"
    pkg.mkdir()
    (pkg / "VERSION").write_text("9.9.7+sha\n")
    monkeypatch.setattr(_version, "_HERE", pkg)
    _version.full_version.cache_clear()
    assert _version.full_version("9.9") == "9.9.7+sha"
    assert _version.release_version("9.9") == "9.9.7"
    _version.full_version.cache_clear()


def test_no_git_no_file_is_dot_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(_version, "_HERE", tmp_path / "khervelab")
    _version.full_version.cache_clear()
    assert _version.full_version("9.9") == "9.9.0"
    _version.full_version.cache_clear()
