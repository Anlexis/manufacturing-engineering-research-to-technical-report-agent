"""AgentCore Platform v1.0"""

# MFG-C2-063 -- TechnicalReportGenerationNode
# Domain node 3 (terminal of the inner pipeline): generate a structured
# Japanese technical report from the synthesis result.
#
# Pattern: DocGeneration (a peer template conceptual reference -- L1 direct
# inheritance, not a class-level import).
#
# SDK v1.0.0rc1 ships no framework LLM client. This node performs deterministic,
# rule-based report assembly from the structured synthesis -- fully testable
# without an LLM. Production wires the real LLM at the marked seam.
#
# Report structure (Japanese, five mandatory sections):
#   gaiyou / shuyou-chiken / gijutsuteki-kousatsu / suishou-jikou / sankou-bunken
#   (rendered as the JA headings below)
#
# Title policy: the caller's validated report title wins; otherwise the query
# is rendered onto ONE line -- unless the platform's input filter masked part
# of it, in which case the generic title is used, because a masking sentinel
# is not an extracted value and must never be reported as one.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).

import logging
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json
from src.services.service import MASK_SENTINEL, MAX_REPORT_TITLE_CHARS, single_line

logger = logging.getLogger(__name__)

# Section headings (Japanese) -- five mandatory sections.
_H_SUMMARY = "## 概要"
_H_FINDINGS = "## 主要知見"
_H_ANALYSIS = "## 技術的考察"
_H_RECOMMENDATIONS = "## 製造現場への推奨事項"
_H_REFERENCES = "## 参考文献"

# Used when neither a caller title nor an unmasked query is available.
_DEFAULT_TITLE = "製造技術調査レポート"


def _bullets(items: List[Any]) -> str:
    """Render a list as Markdown bullets; empty list -> placeholder line.

    Every item is rendered onto one line, so an item cannot escape the bullet
    it is printed in.
    """
    lines = [f"- {single_line(str(item), 2000)}" for item in items if single_line(str(item), 2000)]
    return "\n".join(lines) if lines else "- (該当する項目はありません)"


def _render_title(requested: Optional[str], query: Optional[str]) -> str:
    """Choose and render the report title (see the title policy above)."""
    if requested:
        rendered = single_line(str(requested), MAX_REPORT_TITLE_CHARS)
        if rendered:
            return rendered
    if query and MASK_SENTINEL not in query:
        rendered = single_line(str(query), MAX_REPORT_TITLE_CHARS)
        if rendered:
            return rendered
    return _DEFAULT_TITLE


def _build_report(synthesis: Dict[str, Any], citations: List[str], title: str) -> str:
    """Assemble the five-section Japanese report (deterministic synthesis).

    Production note: replace this deterministic assembly with an LLM call over
    the synthesis result.
    # Production wires the real LLM here.
    """
    key_findings = synthesis.get("key_findings", []) or []
    methodology_notes = synthesis.get("methodology_notes", []) or []

    summary = (
        f"本レポートは「{title}」に関する技術調査の結果をまとめたものである。"
        f"{len(key_findings)} 件の主要知見と {len(citations)} 件の参考文献に基づき、"
        "製造現場への適用に向けた技術的考察と推奨事項を示す。"
    )
    analysis = (
        "収集した文献・特許・規格の分析から、以下の方法論的観点が得られた。"
        "これらは製造プロセスへの適用可否を判断する際の前提となる。\n\n" + _bullets(methodology_notes)
    )
    recommendations = (
        "上記の知見と考察に基づき、製造現場では次の対応を推奨する。"
        "まずパイロットラインで検証し、効果を確認した上で本格展開すること。\n\n"
        + _bullets(
            [
                "引用した技術標準に設計公差・工程管理を整合させる",
                "特許技術については本番適用前に実施可能性(FTO)を確認する",
                "主要知見をKPIに落とし込み、効果を定量的に測定する",
            ]
        )
    )
    references = _bullets(citations)

    return "\n\n".join(
        [
            f"# {title}",
            _H_SUMMARY,
            summary,
            _H_FINDINGS,
            _bullets(key_findings),
            _H_ANALYSIS,
            analysis,
            _H_RECOMMENDATIONS,
            recommendations,
            _H_REFERENCES,
            references,
        ]
    )


class TechnicalReportGenerationNode(FunctionNode):
    """Generate the Japanese technical report draft + metadata.

    Input state keys:
        synthesis_result: JSON string of {key_findings, methodology_notes, citations}
        citation_list:    JSON string of list[str]
        enriched_context: JSON string of request metadata (report_requirements)

    Output state keys (partial dict):
        report_draft:    Japanese Markdown report (five mandatory sections)
        report_metadata: JSON string of
                         {language, domain, generated_at, total_sources}
        status:          ERROR (+ error_log) when synthesis_result is missing
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        synthesis: Dict[str, Any] = from_json(state.get("synthesis_result"), {})

        if not synthesis or not synthesis.get("key_findings"):
            emit_trace_event("technical_report_skipped", {"reason": "missing_synthesis"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "TechnicalReportGenerationNode: synthesis_result is missing or empty -- cannot generate report"
                ],
            }

        citations: List[str] = from_json(state.get("citation_list"), []) or list(synthesis.get("citations", []))
        enriched_context: Dict[str, Any] = from_json(state.get("enriched_context"), {})
        report_requirements = (
            enriched_context.get("report_requirements", {}) if isinstance(enriched_context, dict) else {}
        )
        if not isinstance(report_requirements, dict):
            report_requirements = {}
        # The caller contract is also bridged into this graph; the title from
        # it takes precedence when the inner graph runs on its own.
        contract: Dict[str, Any] = from_json(state.get("caller_contract"), {}) or {}
        contract_requirements = contract.get("report_requirements", {}) if isinstance(contract, dict) else {}
        requested_title = None
        if isinstance(contract_requirements, dict) and contract_requirements.get("title"):
            requested_title = str(contract_requirements["title"])
        elif report_requirements.get("title"):
            requested_title = str(report_requirements["title"])

        title = _render_title(requested_title, state.get("validated_input"))

        report_draft = _build_report(synthesis, citations, title)

        report_metadata: Dict[str, Any] = {
            "language": "ja",
            "domain": "manufacturing",
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "total_sources": len(citations),
        }

        # S-4 domain audit: technical report drafted.
        emit_trace_event(
            "technical_report_generated",
            {"report_chars": len(report_draft), "total_sources": report_metadata["total_sources"]},
            state,
        )

        logger.info(
            "TechnicalReportGenerationNode: report drafted (%d chars, %d sources)",
            len(report_draft),
            report_metadata["total_sources"],
        )

        return {
            "report_draft": report_draft,
            "report_metadata": to_json(report_metadata),
            "status": AgentStatus.SUCCESS.value,
        }
