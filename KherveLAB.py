"""Launcher shim: ``python KherveLAB.py [facility-folder]``."""

import sys

from khervelab.app import main

if __name__ == "__main__":
    sys.exit(main())
