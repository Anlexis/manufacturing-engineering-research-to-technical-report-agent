"""AgentCore Platform v1.0"""

# MFG-C2-063 -- PostProcessNode (outer post_process slot; S-3 output gate point)
#
# Reads the generated technical report from state["report_draft"] (forwarded
# from the inner graph via DomainWorkflowGraphNode.merge_output) plus the
# citation_list, appends the citation/footer block, and surfaces the final
# deliverable as formatted_output AFTER enforcing the report's output
# invariant:
#
#   1. the five mandatory sections are present (structure),
#   2. the report is at least _MIN_REPORT_CHARS long (substance),
#   3. no credential shape appears anywhere in it (content) -- the union of
#      the framework detector and the local pattern set, see
#      src/services/service.py.
#
# This template renders no monetary aggregates, so a numeric precision grid
# does not apply; the invariant above is the one this gate enforces.
#
# On a violation the node returns ERROR AND clears every output-bearing field:
# the framework's output projection falls back to other state when
# formatted_output is empty, so raising -- or returning ERROR with the draft
# still in state -- is not containment. formatted_output carries a sanitised,
# non-empty notice; report_draft, citation_list, report_metadata and result are
# emptied; the error_log names the violation class, never the matched text.
#
# `_security_gate_output()` and `_check_output_credentials()` are implemented as
# MODULE-LEVEL FREE FUNCTIONS (not instance methods) so the framework does not
# chain them into the LangGraph node runnable during _build_graph. When the real
# wheel detects _extra_security_gate_output() as an instance method, it builds a
# gate | execute runnable chain and passes the gate's return value (None on pass)
# as the state to the next step, causing state=None to reach BaseNode.__call__
# and crash at line 207.

import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json
from src.services.service import credential_violation

logger = logging.getLogger(__name__)

# Minimum acceptable length of the final report (domain output check).
_MIN_REPORT_CHARS = 200

# The five mandatory sections every released report carries.
_REQUIRED_HEADINGS = ("## 概要", "## 主要知見", "## 技術的考察", "## 製造現場への推奨事項", "## 参考文献")

_SANITISED_STUB = (
    "[OUTPUT BLOCKED by S-3 gate -- disallowed content detected. "
    "Review the generated technical report and retry without "
    "credential-like strings.]"
)

# Every field that can carry released text; all are cleared on a violation.
_CLEARED_ON_VIOLATION: Dict[str, Any] = {
    "report_draft": None,
    "citation_list": None,
    "report_metadata": None,
    "synthesis_result": None,
    "research_materials": None,
    "result": None,
}


def _security_gate_output(content: str) -> Optional[str]:
    """Run the S-3 output content gate.

    Returns the name of the first matched violation, or None if clean. The
    check is the union of the local credential patterns and the framework's
    own detector, so nothing the framework would block can reach it unhandled.
    """
    return credential_violation(content)


def _check_output_credentials(state: AgentState) -> Optional[str]:
    """Domain output check: credential scan on the report draft.

    Module-level function (not an instance method) so the framework does not
    register it as a gate hook callable during node build. Returns a violation
    reason string to block, or None to allow.

    The execute() path also re-runs _security_gate_output on the final
    assembled output so the surfaced formatted_output is always scanned.
    """
    draft = state.get("report_draft") or ""
    violation = _security_gate_output(str(draft))
    if violation:
        return f"report_draft contains disallowed content ({violation})"
    return None


def _build_references_block(citations: List[str]) -> str:
    """Render the appended citation block for the final output."""
    if not citations:
        return ""
    lines = ["", "## 参考文献", ""]
    lines.extend(f"- {str(c).strip()}" for c in citations if str(c).strip())
    return "\n".join(lines)


class PostProcessNode(FunctionNode):
    """Output gate: enforce the report invariant and finalize the technical report.

    Outer backbone post_process slot. Reads state["report_draft"] (the merged
    inner-graph output) + state["citation_list"], appends the citation/footer
    block, and applies the output invariant before the response is returned
    to the caller. This node runs _security_gate_output() itself on the final
    assembled text; the framework's own gate runs on the result afterwards.

    Input state keys:
        report_draft:  generated Japanese report (from merge_output)
        citation_list: JSON string of list[str]

    Output state keys (partial dict):
        formatted_output: finalized report (sanitised notice on a violation)
        status:           AgentStatus.SUCCESS or AgentStatus.ERROR
        error_log:        (on error) list of error messages
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        report_draft = state.get("report_draft") or ""
        citations: List[str] = from_json(state.get("citation_list"), []) or []

        if not report_draft or not str(report_draft).strip():
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PostProcessNode: report_draft is missing or empty"],
            }

        # Append the citation block only if the draft does not already end with
        # a references section (the report node may already include the JA
        # references heading).
        if citations and "## 参考文献" not in report_draft:
            final_output = str(report_draft) + "\n" + _build_references_block(citations)
        else:
            final_output = str(report_draft)

        if len(final_output.strip()) < _MIN_REPORT_CHARS:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    f"PostProcessNode: final report too short ({len(final_output.strip())} < {_MIN_REPORT_CHARS} chars)"
                ],
            }

        missing = [h for h in _REQUIRED_HEADINGS if h not in final_output]
        if missing:
            emit_trace_event("technical_report_rejected", {"reason": "missing_sections", "count": len(missing)}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PostProcessNode: final report is missing {len(missing)} mandatory section(s)"],
            }

        violation = _security_gate_output(final_output)
        if violation:
            logger.error("PostProcessNode [S-3]: OUTPUT BLOCKED -- violation type: %s", violation)
            emit_trace_event("technical_report_blocked", {"violation": violation}, state)
            return {
                "formatted_output": _SANITISED_STUB,
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PostProcessNode [S-3]: output blocked -- disallowed content detected ({violation})"],
                **_CLEARED_ON_VIOLATION,
            }

        # Clean -- S-4 domain audit: a finalized technical report was emitted.
        emit_trace_event("technical_report_emitted", {"report_chars": len(final_output)}, state)

        return {
            "formatted_output": final_output,
            "status": AgentStatus.SUCCESS.value,
        }
