"""Language detection — cheap function-word heuristic, EN+ID bilingual.

Returns 'en' or 'id'. Never 'unknown' — falls back to 'id' when uncertain
because this scaffolder targets Indonesian-primary deployments. Operators
who run an English-primary site can override the default via the
LANG_DETECT_DEFAULT env var (see config.py).

Used by:
  - server/ingestion/scraper.py   (page-level language tag stored in metadata)
  - server/guards/guard_ingest.py (gates prompt-guard + selects Presidio policy)
  - server/guards/guard_query.py  (same)
  - server/guards/guard_output.py (refusal text + Presidio policy)

We don't use a library (langdetect / fastlangdetect / cld3) because every
guard call needs sub-millisecond latency on short messages and our binary
classifier is correct often enough for guard-policy decisions. The cost of
a wrong call is bounded: a misdetected ID query as EN merely runs the
English prompt-injection detector (which then over-fires and produces a
medium-severity warning, not a block).
"""
from __future__ import annotations

# Padded with leading + trailing spaces so we match at sentence boundaries.
# We pre-pad the sample with spaces too so first-word and last-word markers
# fire — that fixes short queries like "apa itu X" which used to score 0.
_ID_MARKERS = (
    " yang ", " dan ", " untuk ", " ini ", " itu ", " adalah ", " dengan ",
    " di ", " ke ", " dari ", " atau ", " akan ", " sudah ", " bisa ",
    " saya ", " kami ", " kita ", " mereka ", " apa ", " bagaimana ",
    " kenapa ", " mengapa ", " kapan ", " dimana ", " siapa ", " mau ",
    " tidak ", " bukan ", " juga ", " hanya ", " agar ", " karena ",
    " kepada ", " pada ", " menjadi ", " telah ", " merupakan ", " seperti ",
    " sebagai ", " dapat ", " cara ", " tentang ", " oleh ", " selama ",
    " setelah ", " sebelum ",
)
_EN_MARKERS = (
    " the ", " and ", " for ", " this ", " that ", " is ", " are ", " was ",
    " were ", " with ", " of ", " our ", " your ", " their ", " what ",
    " how ", " why ", " when ", " where ", " who ", " can ", " do ", " does ",
    " did ", " will ", " would ", " should ", " could ", " has ", " have ",
    " had ", " not ", " a ", " an ", " to ", " from ", " in ", " on ", " at ",
    " by ", " as ", " or ", " if ",
)

# Strong-signal openers — if the (trimmed) text starts with one of these,
# classify decisively without counting markers. Useful for very short
# queries that don't contain any padded markers.
_ID_STRONG_PREFIX = (
    "apa ", "bagaimana ", "bagaiman", "kenapa ", "mengapa ", "kapan ",
    "dimana ", "di mana ", "siapa ", "tolong ", "saya ", "boleh ",
    "berapa ", "yang mana ", "halo ", "selamat ", "mohon ",
)
_EN_STRONG_PREFIX = (
    "what is ", "what are ", "what does ", "how to ", "how do ", "how does ",
    "how can ", "why ", "when ", "where ", "who ", "can you ", "could you ",
    "please ", "tell me ", "explain ", "give me ", "show me ",
)


def detect_language(text: str) -> str:
    """Return 'en' or 'id'.

    Defaults to 'id' for empty/uncertain input — this scaffolder is
    Indonesian-primary by convention.
    """
    if not text:
        return "id"

    # Pre-pad so first/last-word markers match. Lower-case for matching.
    sample = " " + text[:2000].lower().strip() + " "
    stripped = sample.lstrip()

    # Fast path: strong-signal opener decides outright.
    for p in _ID_STRONG_PREFIX:
        if stripped.startswith(p):
            return "id"
    for p in _EN_STRONG_PREFIX:
        if stripped.startswith(p):
            return "en"

    id_score = sum(sample.count(w) for w in _ID_MARKERS)
    en_score = sum(sample.count(w) for w in _EN_MARKERS)

    # No signal in either direction → default to ID.
    if id_score == 0 and en_score == 0:
        return "id"

    # Ties go to ID. Keeping the prior 1.2x asymmetric bias hurt short
    # bilingual queries — equal weighting is fairer for short text and
    # near-equal mixed-language pages still resolve via marker count.
    return "id" if id_score >= en_score else "en"
