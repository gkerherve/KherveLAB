from __future__ import annotations

import re

import pytest

from khervelab import db
from khervelab.web import create_app


@pytest.fixture
def app(tmp_path):
    a = create_app(tmp_path / "data")
    a.config["TESTING"] = True
    return a


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "lab.db")
    db.init(c)
    yield c
    c.close()


class Browser:
    """A test client that carries the CSRF token like a real form would."""

    def __init__(self, client):
        self.c = client

    def token(self) -> str:
        with self.c.session_transaction() as s:
            if "csrf" not in s:
                s["csrf"] = "test-token"
            return s["csrf"]

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data.setdefault("csrf", self.token())
        return self.c.post(url, data=data, **kw)

    def text(self, url) -> str:
        return self.c.get(url, follow_redirects=True).get_data(as_text=True)


@pytest.fixture
def browser(app):
    return Browser(app.test_client())


@pytest.fixture
def new_browser(app):
    return lambda: Browser(app.test_client())


def flashed(html: str) -> list[str]:
    return re.findall(r'<div class="flash \w+">(.*?)</div>', html, re.S)
