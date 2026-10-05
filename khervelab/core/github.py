"""Minimal GitHub REST client for booking requests.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Three calls (list issues, comment, close) do not justify PyGithub and its
dependency tree in an already large installer, so this uses urllib. ETags
make an unchanged poll a 304, which GitHub does not count against the rate
limit.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

API = "https://api.github.com"
KEYRING_SERVICE = "KherveLAB"


class GitHubError(RuntimeError):
    pass


class RateLimited(GitHubError):
    def __init__(self, reset_at: float):
        self.reset_at = reset_at
        super().__init__(f"GitHub rate limit reached; resets at {time.ctime(reset_at)}")


@dataclass
class Issue:
    number: int
    title: str
    body: str
    author: str
    url: str
    created_at: str
    labels: tuple[str, ...] = ()


def get_token(repo: str) -> str:
    import os
    if os.environ.get("KHERVELAB_GITHUB_TOKEN"):
        return os.environ["KHERVELAB_GITHUB_TOKEN"]
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, f"github:{repo}") or \
            keyring.get_password(KEYRING_SERVICE, "github") or ""
    except Exception:
        return ""


def set_token(repo: str, token: str) -> None:
    import keyring
    keyring.set_password(KEYRING_SERVICE, f"github:{repo}", token)


class GitHubClient:
    def __init__(self, repo: str, token: str = "", timeout: float = 15.0):
        self.repo = repo
        self.token = token
        self.timeout = timeout
        self.remaining: int | None = None
        self.reset_at: float = 0.0
        self._etags: dict[str, tuple[str, Any]] = {}

    def _request(self, method: str, path: str, data: dict | None = None) -> Any:
        if self.remaining == 0 and time.time() < self.reset_at:
            raise RateLimited(self.reset_at)
        url = f"{API}{path}"
        req = urllib.request.Request(url, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "KherveLAB")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        cached = self._etags.get(url) if method == "GET" else None
        if cached:
            req.add_header("If-None-Match", cached[0])
        body = json.dumps(data).encode() if data is not None else None
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, body, timeout=self.timeout) as resp:
                self._note_limits(resp.headers)
                payload = json.loads(resp.read() or b"null")
                if method == "GET" and resp.headers.get("ETag"):
                    self._etags[url] = (resp.headers["ETag"], payload)
                return payload
        except urllib.error.HTTPError as exc:
            self._note_limits(exc.headers)
            if exc.code == 304 and cached:
                return cached[1]
            if exc.code in (403, 429) and self.remaining == 0:
                raise RateLimited(self.reset_at) from None
            detail = exc.read().decode(errors="replace")[:300]
            raise GitHubError(f"GitHub {exc.code} on {method} {path}: {detail}") from None
        except urllib.error.URLError as exc:
            raise GitHubError(f"cannot reach GitHub: {exc.reason}") from None

    def _note_limits(self, headers) -> None:
        if headers is None:
            return
        if headers.get("X-RateLimit-Remaining") is not None:
            self.remaining = int(headers["X-RateLimit-Remaining"])
        if headers.get("X-RateLimit-Reset") is not None:
            self.reset_at = float(headers["X-RateLimit-Reset"])

    def open_issues(self, label: str) -> list[Issue]:
        items = self._request("GET", f"/repos/{self.repo}/issues?state=open&labels={label}"
                                     "&per_page=100") or []
        out = []
        for it in items:
            if "pull_request" in it:
                continue
            out.append(Issue(number=it["number"], title=it.get("title") or "",
                             body=it.get("body") or "", author=(it.get("user") or {}).get("login", ""),
                             url=it.get("html_url", ""), created_at=it.get("created_at", ""),
                             labels=tuple(l["name"] for l in it.get("labels", []))))
        return out

    def comment(self, number: int, body: str) -> None:
        self._request("POST", f"/repos/{self.repo}/issues/{number}/comments", {"body": body})

    def close(self, number: int, reason: str = "completed") -> None:
        self._request("PATCH", f"/repos/{self.repo}/issues/{number}",
                      {"state": "closed", "state_reason": reason})
