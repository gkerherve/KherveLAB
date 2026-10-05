"""Sample registry: generated ids, lineage, links to bookings.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Samples live in the facility repository (``samples/S-YYYY-NNNN.yaml``) so
that bookings, the data index and every working copy agree on what a
sample id means. The owner is an anonymous user id, like everything else
shared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from .yamlio import ConfigError, dump_yaml, load_yaml

SAMPLE_RE = re.compile(r"^S-(\d{4})-(\d{4,})$")
# not \b: "S-2026-0001_Pt.vms" must match, and "_" counts as a word character
SAMPLE_IN_TEXT = re.compile(r"(?<![A-Za-z0-9])S-\d{4}-\d{4,}(?!\d)")


@dataclass(frozen=True)
class Sample:
    id: str
    name: str
    composition: str = ""
    preparation: str = ""
    owner: str = ""               # anonymous user id
    parent: str = ""              # sample it was derived from
    created: date | None = None
    notes: str = ""
    derived_from_file: str = ""   # analysis result (e.g. a KherveFitting workbook) it records

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "name": self.name}
        for k in ("composition", "preparation", "owner", "parent", "notes", "derived_from_file"):
            v = getattr(self, k)
            if v:
                d[k] = v
        if self.created:
            d["created"] = self.created.isoformat()
        return d


def sample_path(sid: str) -> str:
    return f"samples/{sid}.yaml"


def load_sample(path: Path) -> Sample:
    d = load_yaml(path)
    if not isinstance(d, dict):
        raise ConfigError(path, "top level must be a mapping")
    sid = str(d.get("id", ""))
    if not SAMPLE_RE.match(sid):
        raise ConfigError(path, f"id {sid!r} must look like S-2026-0001", "id")
    if sid != path.stem:
        raise ConfigError(path, f"id {sid!r} must match the file name", "id")
    if not d.get("name"):
        raise ConfigError(path, "required key is missing", "name")
    created = d.get("created")
    try:
        created = date.fromisoformat(str(created)) if created else None
    except ValueError:
        raise ConfigError(path, "created must be a date", "created") from None
    parent = str(d.get("parent", "") or "")
    if parent and not SAMPLE_RE.match(parent):
        raise ConfigError(path, f"parent {parent!r} is not a sample id", "parent")
    return Sample(sid, str(d["name"]), str(d.get("composition", "") or ""),
                  str(d.get("preparation", "") or ""), str(d.get("owner", "") or ""), parent,
                  created, str(d.get("notes", "") or ""), str(d.get("derived_from_file", "") or ""))


def load_samples(root: Path) -> dict[str, Sample]:
    out = {}
    for p in sorted((root / "samples").glob("S-*.yaml")):
        s = load_sample(p)
        out[s.id] = s
    for s in out.values():
        if s.parent and s.parent not in out:
            raise ConfigError(root / sample_path(s.id), f"unknown parent {s.parent}", "parent")
    return out


def next_sample_id(existing: dict[str, Sample] | set[str], year: int) -> str:
    """Generated, never typed: the next free number for the year."""
    n = 0
    for sid in existing:
        m = SAMPLE_RE.match(sid)
        if m and int(m.group(1)) == year:
            n = max(n, int(m.group(2)))
    return f"S-{year}-{n + 1:04d}"


def lineage(samples: dict[str, Sample], sid: str) -> list[Sample]:
    """The sample and its ancestors, origin last."""
    out, seen = [], set()
    while sid and sid in samples and sid not in seen:
        seen.add(sid)
        out.append(samples[sid])
        sid = samples[sid].parent
    return out


def children(samples: dict[str, Sample], sid: str) -> list[Sample]:
    return sorted((s for s in samples.values() if s.parent == sid), key=lambda s: s.id)


def sample_yaml(s: Sample) -> str:
    return dump_yaml(s.to_dict())


def derived(parent: Sample, sid: str, name: str, preparation: str, created: date) -> Sample:
    return replace(parent, id=sid, name=name, preparation=preparation, parent=parent.id,
                   created=created, notes="", derived_from_file="")
