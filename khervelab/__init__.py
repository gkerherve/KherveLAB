"""KherveLAB — instrument booking for a single research lab.

Copyright (C) 2026 Gwilherm Kerherve

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

__version__ = "0.28"     # <major>.<minor>; the release adds .<commit count>
__app_name__ = "KherveLAB"
__author__ = "Gwilherm Kerherve"

from ._version import release_version as _release  # noqa: E402

#: What the app reports and what a release is called: <major>.<minor>.<commits>.
RELEASE = _release(__version__)
