"""Test package for the WaveSafe QAM plugin backend.

Puts the plugin dir (for `main`/`mpd_client`) and the `stubs/` dir (for the
`decky` stub that main.py imports) on sys.path so `python3 -m unittest` works
from anywhere.
"""
import os
import sys

_HERE = os.path.dirname(__file__)
_PKG = os.path.dirname(_HERE)
for _p in (_PKG, os.path.join(_HERE, "stubs")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
