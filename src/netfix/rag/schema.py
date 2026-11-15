"""Runbook schema definitions for advisory RAG retrieval."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Runbook:
    """Structured representation of a troubleshooting runbook."""

    id: str
    title: str
    severity_scope: List[str]
    applicable_interfaces: List[str]
    version: str
    symptoms: List[str]
    required_evidence: List[str]
    possible_causes: List[str]
    diagnostic_procedures: List[str]
    recommended_actions: List[str]
    primary_action: str
    primary_parameters: Dict[str, Any]
    secondary_action: Optional[str] = None
    risks_and_blast_radius: List[str] = field(default_factory=list)
    rollback_procedure: str = ""
    post_action_verification: List[str] = field(default_factory=list)
    raw_content: str = ""
    searchable_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert runbook to dictionary."""
        return asdict(self)


@dataclass
class SearchResult:
    """SearchResult holding ranked runbook and BM25 relevance score."""

    runbook: Runbook
    score: float
    matched_terms: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert search result to dictionary."""
        return {
            "runbook_id": self.runbook.id,
            "title": self.runbook.title,
            "score": round(self.score, 4),
            "primary_action": self.runbook.primary_action,
            "matched_terms": self.matched_terms,
            "severity_scope": self.runbook.severity_scope,
        }
