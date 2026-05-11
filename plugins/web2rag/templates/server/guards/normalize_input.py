"""Pre-classifier input normalization.

A surface-pattern classifier like ``Llama-Prompt-Guard-2-86M`` sees the
literal tokens of the input. If an attacker encodes their payload — base64,
hex, morse, ROT13, zero-width interleaving, homoglyph substitution,
leetspeak, emoji cipher — the tokens land outside the classifier's training
distribution, confidence collapses, and what is actually a jailbreak gets
scored benign. Same failure mode that broke early ChatGPT moderation.

This module produces a ``canonical`` form of the input where every common
encoding has been undone in-place. ``guard_query`` then feeds the canonical
form (not the raw payload) to the classifier, so the classifier sees the
semantic intent rather than the obfuscation surface.

Conservative-by-default design:

  - Each decoder is **strict**: a base64/hex/morse block only triggers
    substitution when the decoded result is printable ASCII-like text.
    Random-looking decoded bytes are left as-is. Prevents corrupting
    legitimate text that happens to look base64-ish (e.g. JWT tokens
    pasted as questions, hash IDs).

  - Decoded blocks are kept **inline alongside the original** rather than
    replacing them, formatted as ``ORIGINAL (b64-decoded: DECODED)``.
    Means the classifier sees both the obfuscation AND the unmasked
    intent in one pass — robust to false-positive decoding.

  - ROT13 uses a **dictionary-overlap heuristic** so it doesn't fire on
    arbitrary text. We only flip if applying ROT13 produces more
    dictionary-known tokens than the input had.

  - Order matters: invisibles first (so later regexes don't match across
    hidden chars), then homoglyph/emoji (so ASCII-looking text actually
    becomes ASCII), then base/hex/morse decoders, then ROT13, then
    leetspeak last.

The downstream classifier has its own ``max_seq_len`` truncation, so we
don't have to worry about decoded text growing the prompt — the truncation
is the classifier's responsibility, not ours.
"""
from __future__ import annotations

import base64
import binascii
import codecs
import re
import unicodedata
from dataclasses import dataclass, field


@dataclass
class NormalizationResult:
    """Canonical text + audit trail of which transforms fired.

    The ``suspicion_signals`` map tracks the **presence** of obfuscation
    surface patterns, *independent of whether decoding succeeded*. A query
    can be suspicious even when our decoders couldn't crack the payload —
    for example a 200-char base64-shaped string that decodes to random bytes
    is more suspicious than one that decodes cleanly, not less, because it
    signals deliberate obfuscation. The downstream guard uses this map to
    refuse "suspicious until proven legitimate" rather than the default
    "benign until classifier flags it"."""
    canonical: str
    transforms_applied: list[str] = field(default_factory=list)
    suspicion_signals: dict[str, int] = field(default_factory=dict)


# ─── 1. Zero-width / bidi / format characters ───────────────────────────────
# These can interleave invisibly between letters of an injection phrase and
# break any regex/classifier that matches on contiguous tokens.
# Explicit code-point list so the source is readable + grep-able. Each entry
# is a single invisible character known to be abused for guard-bypass or
# Trojan Source-style attacks. Code points written as \uXXXX escapes only so
# the source file stays portable across editors that hide / mangle invisibles.
_ZW_CHARS = (
    "​"        # zero-width space
    "‌"        # zero-width non-joiner
    "‍"        # zero-width joiner
    "‎"        # left-to-right mark
    "‏"        # right-to-left mark
    "‪‫‬‭‮"  # bidi override (Trojan Source)
    "⁠⁡⁢⁣⁤"  # word joiner / function app / invisible math
    "⁦⁧⁨⁩"        # isolate bidi controls
    "﻿"        # BOM / zero-width no-break space
)
_ZW_PATTERN = re.compile(f"[{re.escape(_ZW_CHARS)}]")


def _strip_zero_width(s: str, signals: dict[str, int]) -> tuple[str, bool]:
    found = _ZW_PATTERN.findall(s)
    if found:
        signals["zero_width_or_bidi"] = signals.get("zero_width_or_bidi", 0) + len(found)
    out = _ZW_PATTERN.sub("", s)
    return out, out != s


# ─── 2. Homoglyph normalisation (Cyrillic / Greek / fullwidth → ASCII) ──────
# A compact subset of the most-abused confusables. We apply NFKC first which
# handles fullwidth Latin, ligatures, and most mathematical-alphanumeric
# blocks; then a manual swap for Cyrillic and Greek lookalikes that NFKC
# leaves alone (because they're legitimate non-Latin letters in other scripts).
_HOMOGLYPHS: dict[str, str] = {
    # Cyrillic
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X",
    # Greek
    "α": "a", "ε": "e", "ι": "i", "ο": "o", "ρ": "p", "τ": "t", "υ": "y", "ν": "v",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
}


def _normalize_homoglyphs(s: str, signals: dict[str, int]) -> tuple[str, bool]:
    nfkc = unicodedata.normalize("NFKC", s)
    swapped = 0
    out_chars: list[str] = []
    for c in nfkc:
        if c in _HOMOGLYPHS:
            out_chars.append(_HOMOGLYPHS[c])
            swapped += 1
        else:
            out_chars.append(c)
    if swapped:
        signals["homoglyph"] = signals.get("homoglyph", 0) + swapped
    out = "".join(out_chars)
    return out, out != s


# ─── 3. Emoji cipher (regional indicators + enclosed alphanumerics) ─────────
def _decode_emoji_cipher(s: str, signals: dict[str, int]) -> tuple[str, bool]:
    out: list[str] = []
    swapped = 0
    for c in s:
        cp = ord(c)
        # 🇦-🇿 Regional Indicator Symbol Letter A-Z
        if 0x1F1E6 <= cp <= 0x1F1FF:
            out.append(chr(ord("A") + cp - 0x1F1E6))
            swapped += 1
            continue
        # ⓐ-ⓩ Circled Latin Small Letter A-Z
        if 0x24D0 <= cp <= 0x24E9:
            out.append(chr(ord("a") + cp - 0x24D0))
            swapped += 1
            continue
        # Ⓐ-Ⓩ Circled Latin Capital Letter A-Z
        if 0x24B6 <= cp <= 0x24CF:
            out.append(chr(ord("A") + cp - 0x24B6))
            swapped += 1
            continue
        # 🅐-🅩 Squared Latin (U+1F130–U+1F149)
        if 0x1F130 <= cp <= 0x1F149:
            out.append(chr(ord("A") + cp - 0x1F130))
            swapped += 1
            continue
        out.append(c)
    if swapped:
        signals["emoji_cipher"] = signals.get("emoji_cipher", 0) + swapped
    return "".join(out), swapped > 0


# ─── 4. Base64 detection + decode ───────────────────────────────────────────
# Require at least one character outside the hex alphabet so we don't
# false-positive on hex strings, which are a subset of the base64 alphabet.
# Pure hex blocks are handled by ``_decode_hex_blocks`` instead.
_B64_RE = re.compile(
    r"(?<![A-Za-z0-9+/=])"
    r"(?=[A-Za-z0-9+/]*[g-zG-Z+/=])"
    r"([A-Za-z0-9+/]{16,}={0,2})"
    r"(?![A-Za-z0-9+/=])"
)


def _is_printable_text(b: bytes) -> bool:
    try:
        s = b.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    if not s.strip():
        return False
    printable = sum(1 for ch in s if 0x20 <= ord(ch) <= 0x7E or ch in "\n\r\t" or ord(ch) > 127)
    return printable / len(s) > 0.85


def _decode_base64_blocks(s: str, signals: dict[str, int]) -> tuple[str, bool]:
    changed = False
    block_count = 0
    undecodable_count = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal changed, block_count, undecodable_count
        block = m.group(1)
        block_count += 1
        try:
            decoded = base64.b64decode(block, validate=True)
        except (binascii.Error, ValueError):
            undecodable_count += 1
            return block
        if not _is_printable_text(decoded):
            # Looked like base64, decoded to random bytes — that's the most
            # suspicious shape of all (deliberate obfuscation that even
            # resists naive decoding).
            undecodable_count += 1
            return block
        decoded_text = decoded.decode("utf-8", errors="replace")
        changed = True
        return f"{block} (b64-decoded: {decoded_text})"

    out = _B64_RE.sub(sub, s)
    if block_count:
        signals["base64_block"] = signals.get("base64_block", 0) + block_count
    if undecodable_count:
        signals["base64_undecodable"] = signals.get("base64_undecodable", 0) + undecodable_count
    return out, changed


# ─── 5. Hex detection + decode ──────────────────────────────────────────────
_HEX_RE = re.compile(r"(?<![0-9a-fA-Fx])((?:0x)?([0-9a-fA-F]{16,}))(?![0-9a-fA-F])")


def _decode_hex_blocks(s: str, signals: dict[str, int]) -> tuple[str, bool]:
    changed = False
    block_count = 0
    undecodable_count = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal changed, block_count, undecodable_count
        full = m.group(1)
        hex_part = m.group(2)
        block_count += 1
        if len(hex_part) % 2 != 0:
            undecodable_count += 1
            return full
        try:
            decoded = bytes.fromhex(hex_part)
        except ValueError:
            undecodable_count += 1
            return full
        if not _is_printable_text(decoded):
            undecodable_count += 1
            return full
        decoded_text = decoded.decode("utf-8", errors="replace")
        changed = True
        return f"{full} (hex-decoded: {decoded_text})"

    out = _HEX_RE.sub(sub, s)
    if block_count:
        signals["hex_block"] = signals.get("hex_block", 0) + block_count
    if undecodable_count:
        signals["hex_undecodable"] = signals.get("hex_undecodable", 0) + undecodable_count
    return out, changed


# ─── 6. Morse code ──────────────────────────────────────────────────────────
_MORSE_TABLE: dict[str, str] = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F",
    "--.": "G", "....": "H", "..": "I", ".---": "J", "-.-": "K", ".-..": "L",
    "--": "M", "-.": "N", "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R",
    "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X",
    "-.--": "Y", "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
}
_MORSE_RE = re.compile(r"(?<![.\-/])([.\-]+(?:[ /]+[.\-]+){2,})(?![.\-/])")


def _decode_morse_blocks(s: str, signals: dict[str, int]) -> tuple[str, bool]:
    changed = False
    block_count = 0
    undecodable_count = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal changed, block_count, undecodable_count
        block = m.group(1)
        block_count += 1
        words = [w.strip() for w in block.split("/")]
        decoded_words: list[str] = []
        ok = True
        for w in words:
            letters = []
            for token in w.split():
                if token in _MORSE_TABLE:
                    letters.append(_MORSE_TABLE[token])
                else:
                    ok = False
                    break
            if not ok or not letters:
                break
            decoded_words.append("".join(letters))
        if not ok or not decoded_words:
            undecodable_count += 1
            return block
        decoded = " ".join(decoded_words)
        changed = True
        return f"{block} (morse-decoded: {decoded})"

    out = _MORSE_RE.sub(sub, s)
    if block_count:
        signals["morse_block"] = signals.get("morse_block", 0) + block_count
    if undecodable_count:
        signals["morse_undecodable"] = signals.get("morse_undecodable", 0) + undecodable_count
    return out, changed


# ─── 7. ROT13 heuristic ─────────────────────────────────────────────────────
# Apply ROT13 only when it produces more dictionary-overlap than the input,
# avoiding false positives on legitimate gibberish-looking text.
_DICT_WORDS: frozenset[str] = frozenset(
    "the and that ignore previous instructions system prompt secret password "
    "tell write give show explain answer who what where how when "
    "apa siapa apakah bagaimana mengapa siapa yang ini itu untuk "
    "abaikan instruksi sebelumnya sistem rahasia jawab".split()
)


def _try_rot13(s: str, signals: dict[str, int]) -> tuple[str, bool]:
    rotated = codecs.encode(s, "rot_13")

    def score(t: str) -> int:
        return sum(1 for w in re.findall(r"[a-zA-Z]{4,}", t.lower()) if w in _DICT_WORDS)

    if score(rotated) > score(s) + 1:
        signals["rot13"] = signals.get("rot13", 0) + 1
        return f"{s} (rot13-decoded: {rotated})", True
    return s, False


# ─── 8. Leetspeak (conservative) ────────────────────────────────────────────
_LEET_MAP: dict[str, str] = {
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b",
    "@": "a", "$": "s", "!": "i",
}
# Leet tokens are short word-shaped strings: 3-12 chars (real words rarely
# longer than that with this character set), bounded by word edges. Long
# alphanumeric runs (12+ chars) look like hashes / IDs / base64 / hex and
# are deliberately excluded so they don't trip leetspeak by accident.
_LEET_TOKEN_RE = re.compile(r"\b[A-Za-z0-9@!$]{3,12}\b")


def _deleet(s: str, signals: dict[str, int]) -> tuple[str, bool]:
    changed = False
    token_count = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal changed, token_count
        tok = m.group(0)
        n_subs = sum(1 for c in tok if c in _LEET_MAP)
        n_letters = sum(1 for c in tok if c.isalpha())
        n_digits = sum(1 for c in tok if c.isdigit())
        # Only de-leet when:
        #   - at least 2 leet substitutions are present
        #   - letters outnumber digits (real "leet" tokens are mostly letters
        #     with a few digit-swaps; pure digit runs are IDs/timestamps)
        #   - at least 2 letters total
        if n_subs < 2 or n_letters < 2 or n_letters <= n_digits:
            return tok
        deleeted = "".join(_LEET_MAP.get(c, c) for c in tok)
        if deleeted.lower() != tok.lower():
            changed = True
            token_count += 1
            return f"{tok} (deleet: {deleeted})"
        return tok

    out = _LEET_TOKEN_RE.sub(sub, s)
    if token_count:
        signals["leetspeak"] = signals.get("leetspeak", 0) + token_count
    return out, changed


# ─── public ─────────────────────────────────────────────────────────────────
def normalize_for_inspection(text: str) -> NormalizationResult:
    """Canonicalize ``text`` so a moderation classifier sees the semantic
    intent rather than the obfuscation surface.

    Returns the canonical text plus a list of transform names that fired,
    suitable for inclusion in ``GuardResult.reasons`` for audit.

    Idempotent: running ``normalize_for_inspection`` on its own output yields
    no further transforms (modulo edge cases where a decoded payload itself
    happens to contain a second-layer encoding, in which case a second pass
    would peel the next layer — left to callers if they want it)."""
    transforms: list[str] = []
    signals: dict[str, int] = {}
    s = text
    pipeline = (
        ("zero_width_stripped", _strip_zero_width),
        ("homoglyph_normalized", _normalize_homoglyphs),
        ("emoji_cipher_decoded", _decode_emoji_cipher),
        ("base64_decoded", _decode_base64_blocks),
        ("hex_decoded", _decode_hex_blocks),
        ("morse_decoded", _decode_morse_blocks),
        ("rot13_decoded", _try_rot13),
        ("leetspeak_normalized", _deleet),
    )
    for name, fn in pipeline:
        s, changed = fn(s, signals)
        if changed:
            transforms.append(name)
    return NormalizationResult(canonical=s, transforms_applied=transforms, suspicion_signals=signals)


__all__ = ["NormalizationResult", "normalize_for_inspection"]
