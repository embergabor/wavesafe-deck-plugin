"""Stub of the `decky` module that Decky Loader injects at runtime.

main.py does `import decky`; on a dev box that module doesn't exist, so tests put
this directory on sys.path. Captures emitted events in `emitted` for assertions.
"""
import logging

logger = logging.getLogger("wavesafe-test")
DECKY_PLUGIN_DIR = "/tmp/wavesafe-test-plugin"

emitted: list[tuple] = []


async def emit(event, *args):
    # async, matching the real decky module (un-awaited emits never send!)
    emitted.append((event, args))
