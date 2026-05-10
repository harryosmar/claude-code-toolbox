"""Tiny memory-footprint helper.

We avoid adding `psutil` for one function — `resource.getrusage` is in the
stdlib. ru_maxrss is in KB on Linux, bytes on macOS.
"""
from __future__ import annotations

import resource
import sys


def process_memory_mb() -> int:
    """Best-effort current process RSS in megabytes."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        # macOS reports bytes
        return rss // (1024 * 1024)
    # Linux reports kilobytes
    return rss // 1024
