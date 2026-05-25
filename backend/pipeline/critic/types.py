from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    FAIL = "FAIL"  # blocks chapter commit
    WARN = "WARN"  # note for next chapter / author
    INFO = "INFO"  # observation


@dataclass(frozen=True)
class Finding:
    """A single continuity finding produced by one of the typed checks."""

    check: str                       # e.g. "entity_mention", "knowledge_state"
    severity: Severity
    message: str                     # one-line human summary
    quote: str | None = None         # exact draft text that triggered the finding
    suggested_fix: str | None = None
    context: dict[str, Any] = field(default_factory=dict)  # check-specific evidence


@dataclass
class CritiqueReport:
    """The result of running all checks against a draft chapter."""

    novel_id: str
    chapter_number: int
    findings: list[Finding] = field(default_factory=list)

    @property
    def fails(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.FAIL]

    @property
    def warns(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.WARN]

    @property
    def passed(self) -> bool:
        return len(self.fails) == 0

    def by_check(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {}
        for f in self.findings:
            out.setdefault(f.check, []).append(f)
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "novel_id": self.novel_id,
            "chapter_number": self.chapter_number,
            "passed": self.passed,
            "total_findings": len(self.findings),
            "fails": len(self.fails),
            "warns": len(self.warns),
            "by_check": {k: len(v) for k, v in self.by_check().items()},
        }


@dataclass
class DraftChapter:
    """Input to the Critic: the draft + the structured facts extracted from it.

    Constructing this is the caller's job. In the eventual pipeline this is
    populated by the Extractor running on the draft prose; in tests it's
    constructed directly.
    """

    novel_id: str
    chapter_number: int
    text: str
    # Facts the extractor pulled out of the draft.
    mentions: list[dict[str, Any]] = field(default_factory=list)
    # [{character_id, fact_description, source_type}] — knowledge implied by the draft
    knowledge_claims: list[dict[str, Any]] = field(default_factory=list)
    # [{character_id, location_id}] — characters at locations per the draft
    location_claims: list[dict[str, Any]] = field(default_factory=list)
    # [{character_id, object_id}] — characters possessing objects per the draft
    possession_claims: list[dict[str, Any]] = field(default_factory=list)
    # Events extracted from the draft (used for temporal + commitment checks)
    events: list[dict[str, Any]] = field(default_factory=list)
    # Plot threads the planner said this chapter would advance.
    planned_thread_ids: list[str] = field(default_factory=list)
    # Commitment IDs the planner said this chapter would plant/satisfy.
    planned_commitment_ids: list[str] = field(default_factory=list)
