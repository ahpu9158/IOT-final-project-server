"""A wall clock that never jumps.

Docker Desktop on Windows (WSL) can drift minutes behind and snap back and forth, which made
session durations negative. We read the wall clock once and then advance with the monotonic
timer, so every timestamp the server writes is consistent with every other one.
"""
import time

_wall0 = time.time()
_mono0 = time.monotonic()


def now() -> float:
    return _wall0 + (time.monotonic() - _mono0)
