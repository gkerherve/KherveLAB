"""Run the KherveLAB booking web pages without a window, in a console.

The Windows build ships this as ``KherveLAB-server.exe`` beside
``KherveLAB.exe`` (a console program, so it can print the address to hand
out); it is ``KherveLAB --serve`` with the same options.
"""

import sys

from khervelab.server import main

if __name__ == "__main__":
    main(["--serve", *[a for a in sys.argv[1:] if a != "--serve"]])
