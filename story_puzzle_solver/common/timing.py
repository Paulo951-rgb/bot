"""Tiny timing helpers."""
from __future__ import annotations

import time
from typing import Optional


def now_ms() -> float:
    return time.monotonic() * 1000.0


class Timer:
    """Context manager that records elapsed milliseconds in ``self.elapsed_ms``.

    Usage::

        with Timer() as t:
            do_work()
        print(t.elapsed_ms)
    """

    __slots__ = ("start", "elapsed_ms")

    def __init__(self) -> None:
        self.start: float = 0.0
        self.elapsed_ms: float = 0.0

    def __enter__(self) -> "Timer":
        self.start = time.monotonic()
        return self

    def __exit__(self, *exc) -> None:
        self.elapsed_ms = (time.monotonic() - self.start) * 1000.0
