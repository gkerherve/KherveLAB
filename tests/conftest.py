from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from khervelab.core.repo import FacilityRepo


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test User")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test User")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.invalid")


@pytest.fixture
def facility(tmp_path) -> FacilityRepo:
    """A throwaway facility working copy with no remote."""
    return FacilityRepo.create(tmp_path / "facility", name="Test facility")


@pytest.fixture
def two_copies(tmp_path):
    """Two working copies sharing one bare repository."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    a = FacilityRepo.create(tmp_path / "a", name="Shared", remote_url=str(bare))
    assert a.push()
    b = FacilityRepo.clone(str(bare), tmp_path / "b")
    return a, b


@pytest.fixture
def template_copy(tmp_path) -> Path:
    from khervelab.core.repo import TEMPLATE
    dst = tmp_path / "plain"
    shutil.copytree(TEMPLATE, dst)
    return dst
