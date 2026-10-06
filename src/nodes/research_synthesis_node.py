"""AgentCore Platform v1.0"""

# MFG-C2-063 -- ResearchSynthesisNode
# Domain node 2: synthesize ingested research materials into a structured
# analysis (key findings, methodology notes, citations).
#
# Pattern: synthesis (DocGeneration/RAG conceptual reference -- L1 direct
# inheritance, not a class-level import).
#
# SDK v1.0.0rc1 ships no framework LLM client. This node performs deterministic,
# rule-based synthesis from the structured upstream materials -- fully testable
# without an LLM. Production wires the real LLM at the marked seam.
#
# Every string that reaches a rendered line passed through single_line(): a
# material's title or content cannot open a heading, a bullet or a section of
# its own, however it was written.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).

import logging
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json
from src.services.service import (
    MAX_MATERIAL_CONTENT_CHARS,
    MAX_MATERIAL_TITLE_CHARS,
    MAX_MATERIAL_URL_CHARS,
    single_line,
)

logger = logging.getLogger(__name__)


def _format_citation(material: Dict[str, Any]) -> str:
    """Format a single material as a JIS Z 8301-style citation string."""
    title = single_line(str(material.get("title", "Untitled source")), MAX_MATERIAL_TITLE_CHARS)
    url = single_line(str(material.get("url", "")), MAX_MATERIAL_URL_CHARS)
    mtype = single_line(str(material.get("type", "document")), 32)
    base = f"{title} ({mtype})"
    return f"{base}. {url}" if url else base


def _derive_key_findings(materials: List[Dict[str, Any]]) -> List[str]:
    """Derive key technical findings from the materials (deterministic synthesis).

    Production note: replace this with an LLM call over the materials.
    # Production wires the real LLM here.
    """
    findings: List[str] = []
    for material in materials:
        content = single_line(str(material.get("content", "")), MAX_MATERIAL_CONTENT_CHARS)
        title = single_line(str(material.get("title", "")), MAX_MATERIAL_TITLE_CHARS)
        if not content:
            continue
        # First sentence of the source content is the headline finding.
        headline = content.split(". ")[0].strip().rstrip(".")
        if headline:
            findings.append(f"{headline} (per: {title}).")
    return findings


def _derive_methodology_notes(materials: List[Dict[str, Any]]) -> List[str]:
    """Derive manufacturing-specific methodology observations by source type."""
    by_type: Dict[str, int] = {}
    for material in materials:
        by_type[str(material.get("type", "document"))] = by_type.get(str(material.get("type", "document")), 0) + 1
    notes: List[str] = []
    if by_type.get("standard"):
        notes.append(
            "Applicable technical standards were identified; align design tolerances and process controls to the cited standards."
        )
    if by_type.get("patent"):
        notes.append("Patented methods are referenced; verify freedom-to-operate before production deployment.")
    if by_type.get("paper"):
        notes.append(
            "Peer-reviewed methods inform the recommended approach; validate on a pilot line before full rollout."
        )
    if not notes:
        notes.append(
            "No standards/patents/papers were classified; treat findings as preliminary and corroborate with primary sources."
        )
    return notes


class ResearchSynthesisNode(FunctionNode):
    """Synthesize research materials into structured findings + citations.

    Input state keys:
        research_materials: JSON string of list[material dict] (from ingestion)

    Output state keys (partial dict):
        synthesis_result: JSON string of
                          {key_findings, methodology_notes, citations}
        citation_list:    JSON string of list[str]
        status:           ERROR (+ error_log) on empty materials / synthesis error
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        materials: List[Dict[str, Any]] = from_json(state.get("research_materials"), [])

        if not materials:
            emit_trace_event("research_synthesis_skipped", {"reason": "no_materials"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ResearchSynthesisNode: research_materials is empty -- cannot synthesize"],
            }

        try:
            key_findings = _derive_key_findings(materials)
            methodology_notes = _derive_methodology_notes(materials)
            citations = [_format_citation(m) for m in materials]
        except Exception as exc:  # defensive: synthesis must fail soft to ERROR
            logger.exception("ResearchSynthesisNode: synthesis failed")
            emit_trace_event("research_synthesis_error", {"error": type(exc).__name__}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"ResearchSynthesisNode: synthesis error ({type(exc).__name__})"],
            }

        if not key_findings:
            key_findings = [
                "No explicit findings could be extracted from the supplied materials; review source content quality."
            ]

        synthesis_result: Dict[str, Any] = {
            "key_findings": key_findings,
            "methodology_notes": methodology_notes,
            "citations": citations,
        }

        # S-4 domain audit: synthesis completed.
        emit_trace_event(
            "research_synthesis_completed",
            {"finding_count": len(key_findings), "citation_count": len(citations)},
            state,
        )

        logger.info("ResearchSynthesisNode: %d findings, %d citations", len(key_findings), len(citations))

        return {
            "synthesis_result": to_json(synthesis_result),
            "citation_list": to_json(citations),
            "status": AgentStatus.SUCCESS.value,
        }
