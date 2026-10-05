"""YAML reading and writing for facility files.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


class ConfigError(ValueError):
    """A facility file is missing, malformed or fails validation.

    The message always names the file and, where known, the key at fault.
    """

    def __init__(self, path: Path | str, message: str, key: str | None = None):
        self.path = Path(path)
        self.key = key
        where = f"{self.path}" + (f" [{key}]" if key else "")
        super().__init__(f"{where}: {message}")


def _yaml() -> YAML:
    y = YAML()  # round-trip: keeps comments and key order on rewrite
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 100
    return y


def load_yaml(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            data = _yaml().load(fh)
    except FileNotFoundError:
        raise ConfigError(path, "file not found") from None
    except YAMLError as exc:
        raise ConfigError(path, f"invalid YAML: {exc}") from None
    return data


def dump_yaml(data: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def loads_yaml(text: str, path: Path | str = "<string>") -> Any:
    try:
        return _yaml().load(text)
    except YAMLError as exc:
        raise ConfigError(path, f"invalid YAML: {exc}") from None
