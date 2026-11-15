"""Runbook parser and BM25 indexer."""

import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Union

from rank_bm25 import BM25Okapi

from netfix.rag.schema import Runbook


def tokenize(text: str) -> List[str]:
    """Tokenize text into lowercase alphanumeric and hyphenated/underscored words."""
    if not text:
        return []
    # Match words, allowing hyphens and underscores within tokens
    tokens = re.findall(r"[a-zA-Z0-9_\-]+", text.lower())
    return [t.strip("-_") for t in tokens if len(t.strip("-_")) > 1]


def parse_runbook_markdown(content: str, filename: str = "") -> Runbook:
    """Parse a markdown runbook document into a structured Runbook instance."""
    lines = content.splitlines()

    # Default fallback values
    runbook_id = ""
    title = ""
    severity_scope: List[str] = []
    applicable_interfaces: List[str] = []
    version = "1.0.0"

    # Match top header: # RB-XX: Title
    top_header_match = re.search(r"^#\s+([A-Za-z0-9\-]+):\s*(.+)$", content, re.MULTILINE)
    if top_header_match:
        runbook_id = top_header_match.group(1).strip()
        title = top_header_match.group(2).strip()

    # Section extraction using regex
    sections: Dict[str, str] = {}
    current_section = ""
    section_lines: List[str] = []

    for line in lines:
        header_match = re.match(r"^##\s+(.+)$", line)
        if header_match:
            if current_section:
                sections[current_section] = "\n".join(section_lines).strip()
            current_section = header_match.group(1).strip()
            section_lines = []
        else:
            section_lines.append(line)
    if current_section:
        sections[current_section] = "\n".join(section_lines).strip()

    # Parse Metadata section
    metadata_text = sections.get("Metadata", "")
    for m_line in metadata_text.splitlines():
        id_match = re.search(r"\*\*ID\*\*:\s*`?([A-Za-z0-9\-]+)`?", m_line)
        if id_match and not runbook_id:
            runbook_id = id_match.group(1).strip()

        title_match = re.search(r"\*\*Title\*\*:\s*(.+)$", m_line)
        if title_match and not title:
            title = title_match.group(1).strip()

        severity_match = re.search(r"\*\*Severity Scope\*\*:\s*(.+)$", m_line)
        if severity_match:
            raw_sev = severity_match.group(1)
            severity_scope = [
                s.strip(" `\",[]") for s in raw_sev.split(",") if s.strip(" `\",[]")
            ]

        iface_match = re.search(r"\*\*Applicable Interfaces\*\*:\s*(.+)$", m_line)
        if iface_match:
            raw_ifaces = iface_match.group(1)
            applicable_interfaces = [
                i.strip(" `\",[]") for i in raw_ifaces.split(",") if i.strip(" `\",[]")
            ]

        version_match = re.search(r"\*\*Version\*\*:\s*`?([0-9\.]+)`?", m_line)
        if version_match:
            version = version_match.group(1).strip()

    # Extract bullet points helper
    def extract_bullets(text: str) -> List[str]:
        items = []
        for b_line in text.splitlines():
            clean = b_line.strip()
            if clean.startswith("- ") or clean.startswith("* ") or re.match(r"^\d+\.\s+", clean):
                bullet = re.sub(r"^(-\s+|\*\s+|\d+\.\s+)", "", clean).strip()
                if bullet:
                    items.append(bullet)
        return items

    symptoms = extract_bullets(sections.get("Symptoms", ""))
    required_evidence = extract_bullets(sections.get("Required Telemetry Evidence", ""))
    possible_causes = extract_bullets(sections.get("Possible Causes", ""))
    diagnostic_procedures = extract_bullets(sections.get("Diagnostic Procedures", ""))
    risks = extract_bullets(sections.get("Risks & Blast Radius", ""))
    rollback = sections.get("Rollback Procedure", "").strip()
    post_verification = extract_bullets(sections.get("Post-Action Verification", ""))

    # Extract Recommended Actions
    actions_text = sections.get("Recommended Actions", "")
    recommended_actions = extract_bullets(actions_text)

    primary_action = ""
    secondary_action: Optional[str] = None
    primary_parameters: Dict[str, Any] = {}

    primary_match = re.search(
        r"\*\*Primary Action\*\*:\s*`?([A-Za-z0-9_]+)`?", actions_text, re.IGNORECASE
    )
    if primary_match:
        primary_action = primary_match.group(1).strip().upper()

    sec_match = re.search(
        r"\*\*(?:Secondary|Contingency) Action\*\*:\s*`?([A-Za-z0-9_]+)`?",
        actions_text,
        re.IGNORECASE,
    )
    if sec_match:
        secondary_action = sec_match.group(1).strip().upper()

    # Extract parameters under primary action
    params_match = re.search(r"Parameters:\s*((?:\n\s+-\s+`?[^:\n]+`?:\s*.+)+)", actions_text)
    if params_match:
        param_block = params_match.group(1)
        for p_line in param_block.splitlines():
            kv_match = re.search(r"-\s*`?([a-zA-Z0-9_]+)`?:\s*`?([^`\n]+)`?", p_line)
            if kv_match:
                k = kv_match.group(1).strip()
                v_str = kv_match.group(2).strip()
                # Parse json or literal if applicable
                try:
                    val = json.loads(v_str)
                except Exception:
                    # Clean trailing comments like (bypass port to netfix-s3)
                    val = re.sub(r"\s*\(.*?\)$", "", v_str).strip()
                    if val.lower() == "true":
                        val = True
                    elif val.lower() == "false":
                        val = False
                    elif re.match(r"^-?\d+$", val):
                        val = int(val)
                    elif re.match(r"^-?\d+\.\d+$", val):
                        val = float(val)
                primary_parameters[k] = val

    # Build searchable text
    searchable_parts = [
        runbook_id,
        title,
        " ".join(severity_scope),
        " ".join(applicable_interfaces),
        " ".join(symptoms),
        " ".join(required_evidence),
        " ".join(possible_causes),
        " ".join(diagnostic_procedures),
        primary_action,
        secondary_action or "",
        json.dumps(primary_parameters),
        rollback,
    ]
    searchable_text = " ".join([p for p in searchable_parts if p])

    return Runbook(
        id=runbook_id,
        title=title,
        severity_scope=severity_scope,
        applicable_interfaces=applicable_interfaces,
        version=version,
        symptoms=symptoms,
        required_evidence=required_evidence,
        possible_causes=possible_causes,
        diagnostic_procedures=diagnostic_procedures,
        recommended_actions=recommended_actions,
        primary_action=primary_action,
        primary_parameters=primary_parameters,
        secondary_action=secondary_action,
        risks_and_blast_radius=risks,
        rollback_procedure=rollback,
        post_action_verification=post_verification,
        raw_content=content,
        searchable_text=searchable_text,
    )


class RunbookIndex:
    """Inverted search index for markdown runbooks using BM25."""

    def __init__(self, runbooks: Sequence[Runbook]):
        self.runbooks: Dict[str, Runbook] = {rb.id: rb for rb in runbooks}
        self.runbook_list: List[Runbook] = list(runbooks)
        self.corpus_tokens: List[List[str]] = [
            tokenize(rb.searchable_text) for rb in self.runbook_list
        ]
        self.bm25 = BM25Okapi(self.corpus_tokens) if self.corpus_tokens else None

    @classmethod
    def from_directory(cls, dir_path: Union[str, Path]) -> "RunbookIndex":
        """Load and index all markdown runbooks from a directory."""
        directory = Path(dir_path)
        if not directory.exists() or not directory.is_dir():
            raise FileNotFoundError(f"Runbook directory not found: {directory}")

        runbooks: List[Runbook] = []
        for file_path in sorted(directory.glob("*.md")):
            content = file_path.read_text(encoding="utf-8")
            rb = parse_runbook_markdown(content, filename=file_path.name)
            if rb.id:
                runbooks.append(rb)
        return cls(runbooks)

    def get(self, runbook_id: str) -> Optional[Runbook]:
        """Retrieve runbook by ID."""
        return self.runbooks.get(runbook_id)

    def __len__(self) -> int:
        return len(self.runbooks)
