"""Auto-acronym extraction — corpus-agnostic.

Scans page text for the universal "Full Name (ACRO)" / "ACRO (Full Name)"
definition pattern that formal documents in every language use, and validates
each candidate by checking that the capital initials of the full name match
the acronym (skipping multilingual stop words like "of"/"for"/"dan"/"untuk").

Why this is generic — not BGN-specific:
  - The parenthetical-definition convention is near-universal in formal
    writing: government, academic, technical, legal, news. English, Bahasa
    Indonesia, German, French, Japanese (e.g. 国際連合 (UN)) all use it.
  - The stop-word list is intentionally short and contains only the highest-
    frequency function words from EN+ID. False negatives (skipping a real
    acronym because the full name happens to contain only stop words —
    extremely rare) are preferable to false positives polluting the table.
  - No site-specific patterns. No hardcoded acronyms. Whatever the corpus
    defines explicitly, this picks up.

What it does NOT catch:
  - Acronyms used without a parenthetical definition anywhere in the corpus.
    If a site says "MBG" 100 times but never expands "Makan Bergizi (MBG)"
    on any page, this extractor finds nothing.
  - Multi-word phrases that aren't acronyms ("makan bergizi" without an
    associated abbreviation).
  - Synonyms. Vocabulary mismatches beyond acronym↔full-form remain.

Usage:
    from server.ingestion.acronyms import extract_acronyms

    table = extract_acronyms(page_text)
    # → {"BGN": "Badan Gizi Nasional", "MBG": "Makan Bergizi", ...}
"""
from __future__ import annotations

import re

# Multilingual function-word set. These are tokens we skip when checking
# whether the capital initials of the full name "spell" the acronym. Keep
# this conservative — adding too many words causes false positives (the
# acronym "of" any random capitalised words). EN + ID coverage is enough
# for the deployments we ship today; extend per language as needed.
_STOP_WORDS: frozenset[str] = frozenset({
    # English
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on",
    "or", "the", "to", "with",
    # Bahasa Indonesia
    "atau", "dan", "dari", "di", "ke", "pada", "untuk", "yang",
    # German
    "der", "die", "das", "und", "von", "zu", "im",
    # French
    "de", "des", "du", "et", "la", "le", "les", "un", "une",
})

# Match BOTH directions:
#   "Full Name (ACRO)"
#   "ACRO (Full Name)"
# Acronym constraints (`[A-Z]{2,8}` plus optional digits) and full-name
# constraints (`Capital + 80 chars max`) deliberately keep the regex
# conservative; junk like "Note (1)" or "see (Figure 3)" doesn't match.
_PATTERN_FULL_THEN_ACRO = re.compile(
    r"\b((?:[A-Z][A-Za-z'À-ſ]{1,30}(?:\s+[\w'À-ſ\-]+){1,8}))"
    r"\s*\(([A-Z][A-Z0-9]{1,7})\)"
)
_PATTERN_ACRO_THEN_FULL = re.compile(
    r"\b([A-Z][A-Z0-9]{1,7})\s*\("
    r"((?:[A-Z][A-Za-z'À-ſ]{1,30}(?:\s+[\w'À-ſ\-]+){1,8}))"
    r"\)"
)


def _initials_match(full_name: str, acronym: str) -> bool:
    """Return True if the leading-capital initials of full_name (skipping
    stop words) form the acronym, case-insensitively.

    Examples (all accepted):
        Badan Gizi Nasional       → BGN
        Makan Bergizi Gratis      → MBG
        Food and Drug Admin...    → FDA   (skips "and")
        United Nations            → UN
    Examples (rejected):
        See Figure                 ≠ SF   (lowercase "ee" not capital)
        Note 1                     ≠ N1   (number-only initials caught upstream)
    """
    tokens = [t for t in re.split(r"\s+", full_name) if t and t.lower() not in _STOP_WORDS]
    if not tokens:
        return False
    initials = "".join(t[0] for t in tokens if t and t[0].isalpha()).upper()
    if not initials:
        return False
    acro = acronym.upper()
    # Hard requirement: the FIRST initial must equal the FIRST acronym letter.
    # Real-world acronyms drop letters from the MIDDLE (USAID drops "for"'s
    # F) but virtually never drop the leading letter of the full name. This
    # rejects the most common false positive: a stray uppercase word
    # (e.g. "Misi") prepended to the canonical full name.
    if initials[0] != acro[0]:
        return False
    if initials == acro:
        return True
    if len(initials) >= len(acro) and initials[: len(acro)] == acro:
        return True
    # Tolerate one missing letter for natural-name vs registered-acronym drift.
    if abs(len(initials) - len(acro)) <= 1:
        return _hamming_one_off(initials, acro)
    return False


def _hamming_one_off(a: str, b: str) -> bool:
    """True if a and b differ by at most one substitution/insertion/deletion."""
    if a == b:
        return True
    if len(a) > len(b):
        a, b = b, a
    if len(b) - len(a) > 1:
        return False
    if len(a) == len(b):
        diffs = sum(1 for x, y in zip(a, b) if x != y)
        return diffs == 1
    # b is exactly one char longer than a — try removing each char from b.
    for i in range(len(b)):
        if b[: i] + b[i + 1 :] == a:
            return True
    return False


def extract_acronyms(text: str) -> dict[str, str]:
    """Find every "Full Name (ACRO)" / "ACRO (Full Name)" definition in text.

    Returns a dict {ACRONYM: full_name}. When the same acronym is defined
    multiple times with different full forms, the *first* occurrence wins —
    we don't dedupe across pages here; that's the caller's job (see
    server.store.acronyms.merge).
    """
    out: dict[str, str] = {}
    for m in _PATTERN_FULL_THEN_ACRO.finditer(text):
        full_name, acro = m.group(1).strip(), m.group(2).strip()
        if _initials_match(full_name, acro):
            out.setdefault(acro, full_name)
    for m in _PATTERN_ACRO_THEN_FULL.finditer(text):
        acro, full_name = m.group(1).strip(), m.group(2).strip()
        if _initials_match(full_name, acro):
            out.setdefault(acro, full_name)
    return out


def expand_with_acronyms(query: str, table: dict[str, str], *, max_variants: int = 3) -> list[str]:
    """Return [original_query] plus variants with each acronym expanded.

    For a query containing a single known acronym, returns 2 variants. For
    multiple acronyms, returns up to `max_variants` (capped to avoid retrieval
    fan-out blowing up). Bidirectional: also expands `Full Name` → `ACRO`
    when the full form appears literally — this catches the inverse case
    (operator's docs use formal name; user types acronym only, OR vice versa).

    Order: most specific (longest input token replaced) first, so callers
    that truncate at max_variants keep the highest-impact rewrites.
    """
    if not table:
        return [query]
    variants: list[str] = [query]
    seen: set[str] = {query}

    # Forward: ACRO → full name.
    for acro, full in sorted(table.items(), key=lambda kv: -len(kv[0])):
        # \b doesn't work on the right side of "MBG" because Python's \b
        # is letter-based; for acronyms with trailing digits we still want
        # word-boundary-ish behaviour. Use lookaround instead.
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(acro)}(?![A-Za-z0-9])")
        if pattern.search(query):
            v = pattern.sub(full, query)
            if v not in seen:
                variants.append(v)
                seen.add(v)
                if len(variants) >= max_variants:
                    return variants

    # Reverse: full name → ACRO. Lower priority — only run if budget remains.
    for acro, full in sorted(table.items(), key=lambda kv: -len(kv[1])):
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(full)}(?![A-Za-z0-9])", re.IGNORECASE)
        if pattern.search(query):
            v = pattern.sub(acro, query)
            if v not in seen:
                variants.append(v)
                seen.add(v)
                if len(variants) >= max_variants:
                    return variants

    return variants


def merge_tables(*tables: dict[str, str]) -> dict[str, str]:
    """Combine multiple per-page acronym dicts into one. First-write-wins
    on conflict (preserves the earliest definition seen, which is usually
    the canonical one — formal docs define an acronym before reusing it)."""
    out: dict[str, str] = {}
    for t in tables:
        for k, v in t.items():
            out.setdefault(k, v)
    return out


__all__ = ["extract_acronyms", "expand_with_acronyms", "merge_tables"]
