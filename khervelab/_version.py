"""The release version: ``<major>.<minor>.<commits>``.

``__version__`` in ``khervelab/__init__.py`` holds ``<major>.<minor>`` and is
bumped by hand (see CLAUDE.md). The third number is ``git rev-list --count
HEAD``, so every commit gives a new build number without editing anything.
A frozen (PyInstaller) build has no ``.git``: the packaging scripts write the
resolved ``<major>.<minor>.<n>+<sha>`` to ``khervelab/VERSION`` and bundle it.
In a checkout git always wins, so a VERSION left by a local build is ignored. With neither, the version is ``<major>.<minor>.0``.

Copyright (C) 2026 Gwilherm Kerherve
Licensed under the GNU General Public License v3.0 or later (see LICENSE).
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def git_version(base: str, root: Path | None = None) -> str:
    """``<base>.<count>+<sha>`` from git alone (ignores a stale VERSION file),
    or ``<base>.0`` when git cannot answer."""
    root = Path(root or _HERE.parent)
    try:
        count = subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=root,
                                        stderr=subprocess.DEVNULL).strip().decode()
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                                      stderr=subprocess.DEVNULL).strip().decode()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return f"{base}.0"
    return f"{base}.{count}+{sha}" if count and sha else f"{base}.0"


@lru_cache(maxsize=1)
def full_version(base: str) -> str:
    """Git whenever this is a checkout (a VERSION file left by a local build
    must not win), else the bundled VERSION of a frozen build, else
    ``<base>.0``."""
    if (_HERE.parent / ".git").exists():
        return git_version(base)
    bundled = _HERE / "VERSION"
    if bundled.is_file():
        text = bundled.read_text(encoding="utf-8").strip()
        if text:
            return text
    return f"{base}.0"


def release_version(base: str) -> str:
    """``full_version`` without the ``+sha``: what file names and tags use."""
    return full_version(base).split("+")[0]
