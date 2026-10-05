"""The facility working copy: every read and write of Git goes through here.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import git
from git.exc import GitCommandError

TEMPLATE = Path(__file__).resolve().parent.parent / "data" / "facility_template"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_OFFLINE_HINTS = ("could not resolve host", "unable to access", "could not read from remote",
                  "connection timed out", "network is unreachable", "failed to connect",
                  "connection refused", "does not appear to be a git repository")


class RepoError(RuntimeError):
    pass


class OfflineError(RepoError):
    """The remote could not be reached. Local commits are kept and queued."""


class PersonalDataError(RepoError):
    """A file about to be committed looks like it holds an email address."""


@dataclass
class FileConflict:
    path: str
    ours: str | None    # None: deleted on our side
    theirs: str | None  # None: deleted on theirs


class ConflictError(RepoError):
    def __init__(self, conflicts: list[FileConflict]):
        self.conflicts = conflicts
        names = ", ".join(c.path for c in conflicts)
        super().__init__(f"conflicting changes to {names}")


@dataclass
class RepoStatus:
    branch: str
    has_remote: bool
    ahead: int = 0          # local commits not yet pushed
    behind: int = 0
    dirty: list[str] = field(default_factory=list)
    last_sync: datetime | None = None


def _is_offline(exc: GitCommandError) -> bool:
    text = f"{exc.stderr or ''} {exc}".lower()
    return any(h in text for h in _OFFLINE_HINTS)


class FacilityRepo:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        try:
            self.git = git.Repo(self.path)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            raise RepoError(f"{self.path} is not a Git working copy") from None
        self.last_sync: datetime | None = None

    # -- creation ---------------------------------------------------------
    @classmethod
    def clone(cls, url: str, path: Path | str) -> "FacilityRepo":
        try:
            git.Repo.clone_from(url, str(path))
        except GitCommandError as exc:
            if _is_offline(exc):
                raise OfflineError(f"cannot reach {url}") from None
            raise RepoError(f"clone failed: {exc.stderr or exc}") from None
        return cls(path)

    @classmethod
    def create(cls, path: Path | str, name: str | None = None,
               remote_url: str | None = None) -> "FacilityRepo":
        """Scaffold a new facility from the bundled template and commit it."""
        path = Path(path)
        if path.exists() and any(path.iterdir()):
            raise RepoError(f"{path} is not empty")
        shutil.copytree(TEMPLATE, path, dirs_exist_ok=True)
        if name:
            fac = path / "facility.yaml"
            text = fac.read_text(encoding="utf-8")
            text = re.sub(r"(?m)^name:.*$", f"name: {_yaml_str(name)}", text)
            fac.write_text(text, encoding="utf-8")
        repo = git.Repo.init(str(path), initial_branch="main")
        if remote_url:
            repo.create_remote("origin", remote_url)
        self = cls(path)
        self.git.git.add(A=True)
        self._check_personal_data()
        self.git.index.commit("create facility")
        return self

    # -- state ------------------------------------------------------------
    @property
    def branch(self) -> str:
        try:
            return self.git.active_branch.name
        except TypeError:
            return "HEAD"

    @property
    def has_remote(self) -> bool:
        return any(r.name == "origin" for r in self.git.remotes)

    def _upstream(self) -> str | None:
        if not self.has_remote:
            return None
        ref = f"origin/{self.branch}"
        return ref if ref in [r.name for r in self.git.remote("origin").refs] else None

    def status(self) -> RepoStatus:
        st = RepoStatus(branch=self.branch, has_remote=self.has_remote, last_sync=self.last_sync)
        up = self._upstream()
        if up:
            st.ahead = int(self.git.git.rev_list("--count", f"{up}..HEAD"))
            st.behind = int(self.git.git.rev_list("--count", f"HEAD..{up}"))
        elif self.has_remote:
            st.ahead = int(self.git.git.rev_list("--count", "HEAD"))
        st.dirty = [l[3:] for l in self.git.git.status("--porcelain").splitlines()]
        return st

    # -- files ------------------------------------------------------------
    def read_file(self, rel: str) -> str | None:
        p = self.path / rel
        return p.read_text(encoding="utf-8") if p.exists() else None

    def write_file(self, rel: str, text: str) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")

    def delete_file(self, rel: str) -> None:
        p = self.path / rel
        if p.exists():
            p.unlink()
        parent = p.parent
        while parent != self.path and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

    def commit(self, message: str, paths: list[str]) -> str:
        """Stage exactly `paths` (added, changed or removed) and commit them.
        The author is the user's own git identity, as for a CLI commit."""
        for rel in paths:
            if (self.path / rel).exists():
                self.git.git.add("--", rel)
            else:
                self.git.git.rm("--cached", "--ignore-unmatch", "-q", "--", rel)
        self._check_personal_data()
        self.git.git.commit("-m", message, "--allow-empty-message", "--no-verify")
        return self.git.head.commit.hexsha

    def _check_personal_data(self) -> None:
        staged = self.git.git.diff("--cached", "--name-only", "--diff-filter=AM").splitlines()
        for rel in staged:
            p = self.path / rel
            if p.suffix.lower() not in (".yaml", ".yml", ".json", ".md", ".txt", ".csv", ".ics",
                                        ".html"):
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            m = _EMAIL_RE.search(text)
            if m:
                self.git.git.reset("-q", "--", rel)
                raise PersonalDataError(
                    f"{rel} contains what looks like an email address ({m.group(0)}); "
                    "personal data must not go into the facility repository")

    # -- sync -------------------------------------------------------------
    def pull(self) -> int:
        """Fetch and merge. Returns how many commits came in. A merge
        conflict is aborted and raised as ConflictError with both versions."""
        if not self.has_remote:
            return 0
        try:
            self.git.git.fetch("origin")
        except GitCommandError as exc:
            if _is_offline(exc):
                raise OfflineError("remote unreachable; working offline") from None
            raise RepoError(f"fetch failed: {exc.stderr or exc}") from None
        up = self._upstream()
        if not up:
            self.last_sync = datetime.now(timezone.utc)
            return 0
        behind = int(self.git.git.rev_list("--count", f"HEAD..{up}"))
        if behind:
            try:
                self.git.git.merge("--no-edit", up)
            except GitCommandError:
                conflicts = self._collect_conflicts()
                self.git.git.merge("--abort")
                if conflicts:
                    raise ConflictError(conflicts) from None
                raise
        self.last_sync = datetime.now(timezone.utc)
        return behind

    def _collect_conflicts(self) -> list[FileConflict]:
        out = []
        unmerged = self.git.git.diff("--name-only", "--diff-filter=U").splitlines()
        for rel in unmerged:
            out.append(FileConflict(rel, self._stage(2, rel), self._stage(3, rel)))
        return out

    def _stage(self, n: int, rel: str) -> str | None:
        try:
            return self.git.git.show(f":{n}:{rel}")
        except GitCommandError:
            return None

    def push(self) -> bool:
        """Push queued commits. Returns False when offline (they stay queued).
        A rejected push pulls first, so a real clash surfaces as ConflictError."""
        if not self.has_remote:
            return False
        for attempt in range(2):
            try:
                self.git.git.push("-u", "origin", self.branch)
                self.last_sync = datetime.now(timezone.utc)
                return True
            except GitCommandError as exc:
                if _is_offline(exc):
                    return False
                text = f"{exc.stderr or ''}".lower()
                if attempt == 0 and ("rejected" in text or "fetch first" in text
                                     or "non-fast-forward" in text):
                    self.pull()
                    continue
                raise RepoError(f"push failed: {exc.stderr or exc}") from None
        return False

    def sync(self) -> RepoStatus:
        """Pull, then push anything queued. Offline is not an error here."""
        try:
            self.pull()
            self.push()
        except OfflineError:
            pass
        return self.status()


def _yaml_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"
