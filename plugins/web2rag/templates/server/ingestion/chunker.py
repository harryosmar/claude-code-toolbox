"""Recursive character splitter — markdown-aware, sentence-aware, word-safe.

We chose character-based splitting (not token-based) because BGE-M3 is a
multilingual model where the char/token ratio varies a lot between English
and Indonesian. Char counts give us predictable, fast splits with no
tokeniser dependency at chunk time.

Sizes are pulled from server.config.settings (CHUNK_SIZE_CHARS /
CHUNK_OVERLAP_CHARS) so the operator can retune per-deployment.

Three splitter properties (corpus-agnostic, no per-site config):
  1. **Markdown-aware**: prefers splitting at header boundaries (## / ### /
     ####) so chunks align to logical document sections.
  2. **Sentence-aware**: when a chunk doesn't fit at a paragraph break, falls
     through to sentence-end marks (. / ? / ! / 。 / ؟) before any whitespace
     boundary. Works for EN, ID, CJK, Arabic without language tokenisers.
  3. **Word-safe**: even when forced to a word-level break (separator " ")
     or a hard char-cut (last-resort), the OVERLAP window now snaps to the
     nearest preceding whitespace so chunks never start mid-word. (The
     previous implementation produced artifacts like "u program makan…"
     where the overlap window cut through "untuk".)

The empty separator "" is intentionally NOT in the list anymore — by the
time we'd reach it, sentence-end and word-break separators have already
divided the text. A piece that survives all those is essentially a single
unbreakable token, and we'd rather emit it whole than slice mid-token.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from server.config import settings

# Splitting boundaries, in priority order. Higher boundaries (markdown
# headers) are tried first so chunks align to logical document structure
# when possible. The mid-tier sentence-end separators are MULTI-CHAR so the
# split-and-rejoin in `_recursive_split` preserves the punctuation in the
# previous chunk (the period/question mark stays with its sentence).
#
# Multilingual sentence-enders covered: latin (. ! ?), CJK (。), arabic (؟).
# We keep the "newline" separator above sentence-enders so a paragraph break
# wins over a mid-sentence newline (which doesn't really exist in well-
# formed prose anyway).
_SEPARATORS: tuple[str, ...] = (
    "\n## ",
    "\n### ",
    "\n#### ",
    "\n\n",
    "\n",
    ". ",
    "? ",
    "! ",
    "。",
    "؟",
    " ",
)


@dataclass(frozen=True)
class Chunk:
    text: str
    section_title: str  # nearest preceding header within the page
    index: int

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def chunk_markdown(
    md: str,
    *,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[Chunk]:
    chunk_size = chunk_size if chunk_size is not None else settings.chunk_size_chars
    chunk_overlap = chunk_overlap if chunk_overlap is not None else settings.chunk_overlap_chars
    """Split a markdown document into Chunks with section-title attribution."""
    if not md.strip():
        return []
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be < chunk_size")

    pieces = _recursive_split(md, chunk_size=chunk_size, separators=list(_SEPARATORS))
    overlapped = _add_overlap(pieces, overlap=chunk_overlap)

    chunks: list[Chunk] = []
    current_section = ""
    for i, piece in enumerate(overlapped):
        # Track the most recent header we've passed for section_title attribution.
        for line in piece.splitlines():
            if line.startswith("## ") or line.startswith("### ") or line.startswith("#### "):
                current_section = line.lstrip("#").strip()
        chunks.append(Chunk(text=piece.strip(), section_title=current_section, index=i))
    return [c for c in chunks if c.text]


def _recursive_split(text: str, *, chunk_size: int, separators: list[str]) -> list[str]:
    """Recursively split until each piece fits chunk_size or runs out of separators.

    When we run out of separators, we emit the oversize piece intact rather
    than hard-cutting mid-token. The reranker sees a too-long chunk
    (possibly truncated by max_length=512 inside the cross-encoder) but the
    embedder still encodes a coherent unit. Hard char-cuts produced
    "u program makan…"-style artifacts that polluted the embedding space.
    """
    if len(text) <= chunk_size:
        return [text]
    if not separators:
        # Last resort: emit whole. Reranker has its own truncation;
        # better to lose a tail than corrupt the head with a mid-word cut.
        return [text]

    sep, rest = separators[0], separators[1:]
    parts = text.split(sep)
    out: list[str] = []
    buf = ""
    for part in parts:
        candidate = (buf + sep + part) if buf else part
        if len(candidate) <= chunk_size:
            buf = candidate
        else:
            if buf:
                out.append(buf)
            if len(part) <= chunk_size:
                buf = part
            else:
                out.extend(_recursive_split(part, chunk_size=chunk_size, separators=rest))
                buf = ""
    if buf:
        out.append(buf)
    return out


_WHITESPACE_RE = re.compile(r"\s")


def _snap_to_word_boundary(text: str, target_len: int) -> str:
    """Return the last `target_len` chars of `text`, snapped FORWARD to the
    nearest whitespace so the returned slice doesn't start mid-word.

    Example with target_len=10:
        "...untuk anak-anak makan bergizi"
                  └─ raw [-10:] would be "ak makan b" (mid-word "anak")
                  └─ snapped forward to first space → "makan b"

    Returns "" when no whitespace exists in the window (single-token tail).
    Callers treat that as "no useful overlap" and append nothing.
    """
    if target_len <= 0 or not text:
        return ""
    window = text[-target_len:]
    m = _WHITESPACE_RE.search(window)
    if m is None:
        return ""
    return window[m.end():]


def _add_overlap(pieces: list[str], *, overlap: int) -> list[str]:
    """Sliding-window overlap so neighbouring chunks share `overlap` chars,
    snapped to word boundaries so chunks never start mid-token.

    The previous implementation took a raw `pieces[i-1][-overlap:]` slice
    which caused chunks to start mid-word when `pieces[i-1]` ended with a
    long token. The fix is `_snap_to_word_boundary`: walk forward from the
    raw window's start to the first whitespace, then keep everything after.
    For pieces that end with a single very long token, the snap returns ""
    and the overlap is silently dropped for that pair — acceptable, since
    forcing an arbitrary cut would re-introduce the original artifact.
    """
    if overlap <= 0 or len(pieces) <= 1:
        return pieces
    out: list[str] = [pieces[0]]
    for i in range(1, len(pieces)):
        tail = _snap_to_word_boundary(pieces[i - 1], overlap)
        if tail:
            # Add a single space if the snapped tail doesn't already end
            # with whitespace; this avoids "tailWord<chunk>" concatenation.
            joiner = "" if tail.endswith((" ", "\n", "\t")) else " "
            out.append(tail + joiner + pieces[i])
        else:
            out.append(pieces[i])
    return out
