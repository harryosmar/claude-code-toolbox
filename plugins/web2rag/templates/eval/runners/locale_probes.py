"""Hand-curated bilingual probe runner — complements DeepTeam's auto-attacks.

DeepTeam's simulators (Bias, PromptInjection, Toxicity, PromptLeakage,
LinearJailbreaking, ROT13) auto-generate adversarial inputs and the
attacks default to English templates. That leaves Indonesian-primary
deployments under-tested for two specific failure modes that DeepTeam
does NOT cover well:

  1. Guards FALSE-REFUSING legitimate Indonesian queries — a
     scope/topic guard that over-fires on the operator's own users is
     functionally a denial-of-service. Hard to express as a DeepTeam
     vulnerability because there's no adversary.

  2. Bot LANGUAGE-DRIFTING — answering an Indonesian question in
     English (or vice versa). DeepTeam's Bias simulator has language as
     an axis but it's poorly covered for ID specifically.

This runner reads a hand-curated set of probes from
`eval/locale_probes.json` and asserts:

  - The bot's answer is non-empty (no false refusal)
  - The bot's answer is in the expected language (token-distinctive
    heuristic, same one server/util/lang.py uses)
  - At least one expected source URL substring appears in the cited
    chunks (catches "answered confidently from the wrong page")

It's deterministic — no LLM judge, $0 to run, fast. Add probes by
appending to eval/locale_probes.json. Cases tagged `tags: ["seed"]`
are typically the same questions used as DeepEval seeds (see
eval/testsets/_seeds/) so you get cross-framework coverage on the
same input.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from server.util.lang import detect_language

log = logging.getLogger(__name__)

PROBES_PATH = Path("eval/locale_probes.json")


def _load_probes() -> list[dict[str, Any]]:
    if not PROBES_PATH.exists():
        log.info("no locale_probes.json at %s — skipping", PROBES_PATH)
        return []
    try:
        probes = json.loads(PROBES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("locale_probes.json unreadable, skipping: %s", e)
        return []
    if not isinstance(probes, list):
        log.warning("locale_probes.json is not a list, skipping")
        return []
    return probes


async def run(
    *,
    answer_for: callable,  # type: ignore[type-arg]  # async (question) -> {answer, contexts}
    site_id: str | None = None,
) -> dict[str, Any]:
    """Run every probe; collect per-probe pass/fail; aggregate."""
    probes = _load_probes()
    if not probes:
        return {"summary": {"total": 0, "passed": 0, "skipped": True}, "per_probe": []}

    per_probe: list[dict[str, Any]] = []
    passed = 0

    for probe in probes:
        question = (probe.get("question") or "").strip()
        if not question:
            continue
        # Per-probe site filter — a probe can target a specific site_id;
        # if the audit is scoped to a different site, skip it.
        target = probe.get("site_id")
        if site_id and target and target != site_id:
            continue

        result = await answer_for(question)
        answer = (result.get("answer") or "").strip()
        contexts = result.get("contexts") or []

        checks: dict[str, Any] = {}

        # Check 1: non-empty answer (no false refusal).
        checks["answered"] = {
            "expected": True,
            "actual": bool(answer),
            "passed": bool(answer) == probe.get("must_not_refuse", True),
        }

        # Check 2: language match.
        expected_lang = (probe.get("expected_language") or "").lower() or None
        if expected_lang:
            actual_lang = detect_language(answer) if answer else "?"
            checks["language"] = {
                "expected": expected_lang,
                "actual": actual_lang,
                "passed": actual_lang == expected_lang,
            }

        # Check 3: cited sources include the expected URL fragment.
        expected_substr = probe.get("expected_source_url_substr") or ""
        if expected_substr:
            joined = " || ".join(contexts) if contexts else ""
            # `contexts` from answer_for is the chunk text, not the URLs —
            # most generators put the source URL at the chunk header. Best
            # we can do without re-querying: substring check on chunk text
            # (which usually mentions the URL or page title) PLUS the
            # answer body (which often cites the source). Imperfect but
            # better than skipping.
            haystack = (joined + " " + answer).lower()
            checks["source_match"] = {
                "expected_substr": expected_substr,
                "actual_present": expected_substr.lower() in haystack,
                "passed": expected_substr.lower() in haystack,
            }

        all_passed = all(c.get("passed") for c in checks.values())
        if all_passed:
            passed += 1
        per_probe.append(
            {
                "question": question,
                "tags": probe.get("tags", []),
                "checks": checks,
                "all_passed": all_passed,
            }
        )

    summary = {
        "total": len(per_probe),
        "passed": passed,
        "pass_rate": (passed / len(per_probe)) if per_probe else None,
    }
    return {"summary": summary, "per_probe": per_probe}
