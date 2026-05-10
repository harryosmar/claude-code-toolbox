"""Shared types for the 3-guard architecture.

Each guard returns a GuardResult. severity=high rejects the request;
low/med logs and continues. See CLAUDE.md > "3-guard architecture".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["low", "med", "high"]


@dataclass
class GuardResult:
    passed: bool
    severity: Severity = "low"
    reasons: list[str] = field(default_factory=list)
    redacted_text: str | None = None  # if the guard rewrote the input

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "severity": self.severity,
            "reasons": self.reasons,
        }
