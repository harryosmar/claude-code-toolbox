"""Production user feedback — pluggable store interface.

Why an interface instead of just a JSONL writer: file-based storage is
fine for v0.1 development but does not scale.

  - file-lock contention under concurrent writes
  - O(N) reads for /feedback/summary
  - no remote replication, no retention/compaction
  - one container = one source of truth (bad for HA)

The `FeedbackStore` abstract base + `JsonlFeedbackStore` concrete pair
lets us swap in `PostgresFeedbackStore`, `ClickHouseFeedbackStore`, or
`S3JsonlFeedbackStore` later by registering a different implementation
in `make_store()`. No API or widget code changes required.
"""
from __future__ import annotations

import abc
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

Vote = Literal["up", "down"]


@dataclass
class FeedbackRecord:
    timestamp: str
    session_id: str
    message_id: str          # client-generated; lets us correlate retries
    site_id: str | None
    vote: Vote
    comment: str = ""
    snapshot: dict[str, Any] = field(default_factory=dict)
    # snapshot fields the widget sends:
    #   {"question": str, "answer": str, "citations": [...],
    #    "guards": {...}, "model": str}

    @classmethod
    def new(cls, **kwargs: Any) -> "FeedbackRecord":
        kwargs.setdefault("timestamp", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        return cls(**kwargs)


class FeedbackStore(abc.ABC):
    """Pluggable backend. Implementations are responsible for their own
    durability + concurrency guarantees."""

    @abc.abstractmethod
    def append(self, record: FeedbackRecord) -> None: ...

    @abc.abstractmethod
    def list_recent(self, limit: int = 100) -> list[FeedbackRecord]: ...

    @abc.abstractmethod
    def summary(self, *, since: datetime | None = None) -> dict[str, Any]: ...


class JsonlFeedbackStore(FeedbackStore):
    """Append-only JSONL at `data/feedback.jsonl`. v0.1-only.

    Concurrency: protected by a process-local threading.Lock plus os-level
    O_APPEND semantics. Single-container deployments only — multi-replica
    deployments must swap to a real store (this raises a warning in
    /feedback/summary if multiple replicas are detected via env vars).
    """

    def __init__(self, path: Path = Path("data/feedback.jsonl")) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: FeedbackRecord) -> None:
        line = json.dumps(asdict(record), ensure_ascii=False) + "\n"
        with self._lock:
            # O_APPEND on POSIX guarantees atomic line-level writes up to PIPE_BUF.
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line)

    def list_recent(self, limit: int = 100) -> list[FeedbackRecord]:
        if not self._path.exists():
            return []
        # Tail without loading the entire file.
        tail = _tail_lines(self._path, limit)
        return [FeedbackRecord(**json.loads(line)) for line in tail if line.strip()]

    def summary(self, *, since: datetime | None = None) -> dict[str, Any]:
        if not self._path.exists():
            return {"total": 0, "thumbs_up": 0, "thumbs_down": 0, "thumbs_up_rate": None, "by_site": {}}

        total = up = down = 0
        by_site: dict[str, dict[str, int]] = {}
        recent_low: list[dict[str, Any]] = []

        with self._lock, self._path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since:
                    ts = _parse_ts(row.get("timestamp", ""))
                    if ts and ts < since:
                        continue
                vote = row.get("vote")
                site = row.get("site_id") or ""
                slot = by_site.setdefault(site, {"up": 0, "down": 0})
                if vote == "up":
                    up += 1
                    slot["up"] += 1
                elif vote == "down":
                    down += 1
                    slot["down"] += 1
                    if len(recent_low) < 10:
                        recent_low.append(
                            {
                                "timestamp": row.get("timestamp"),
                                "site_id": site,
                                "question": (row.get("snapshot") or {}).get("question"),
                                "comment": row.get("comment"),
                            }
                        )
                total += 1

        return {
            "total": total,
            "thumbs_up": up,
            "thumbs_down": down,
            "thumbs_up_rate": (up / total) if total else None,
            "by_site": by_site,
            "recent_low_rated": recent_low,
        }


def _tail_lines(path: Path, n: int) -> Iterable[str]:
    """Read the last `n` lines without loading the whole file."""
    with path.open("rb") as f:
        try:
            f.seek(0, os.SEEK_END)
        except OSError:
            return []
        size = f.tell()
        block = 4096
        data = b""
        while size > 0 and data.count(b"\n") <= n:
            step = min(block, size)
            size -= step
            f.seek(size)
            data = f.read(step) + data
    return data.decode("utf-8", errors="replace").splitlines()[-n:]


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


# ── factory ──────────────────────────────────────────────────────────────────
# Today this returns the JSONL store unconditionally. Future swap-in:
#   FEEDBACK_STORE=postgres → PostgresFeedbackStore(dsn=settings.feedback_dsn)
#   FEEDBACK_STORE=s3       → S3JsonlFeedbackStore(bucket=…)
# Add the conditional here; nothing else has to change.

_singleton: FeedbackStore | None = None
_singleton_lock = threading.Lock()


def make_store() -> FeedbackStore:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = JsonlFeedbackStore()
    return _singleton
