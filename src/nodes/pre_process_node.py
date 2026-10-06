"""AgentCore Platform v1.0"""

# MFG-C2-063 -- PreProcessNode (outer pre_process slot; S-1 input validation)
#
# Node contract (SDK v1.0.0rc1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants -- never plain strings
#  - Read input_context via state.get("input_context", {}) -- read-only
#  - Never import from mediator/, api/, or other agents
#
# S-1: reject empty / malformed / oversized input before the inner domain
# workflow runs. Normalizes the research query into validated_input and
# validates the caller's structured parameters (input_context) into the
# caller contract that the inner pipeline consumes.
#
# This node OWNS the caller contract: every refusal below is enforced here,
# in execute(), so it holds with or without the framework's own input gate in
# front of it. Rejections name the field, never the value.
#
# S-2 domain check: _check_input_length() enforces the input length limit
# (<= 2000 chars). Implemented as a module-level function (not a method) so
# the framework does not chain it into the LangGraph node runnable during
# _build_graph. When the real wheel detects _extra_security_gate_input() as an
# instance method, it builds a gate | execute runnable chain and passes the
# gate's return value (None on pass) as the state to the next step, causing
# state=None to reach BaseNode.__call__ and crash at line 207.

from typing import Any, ClassVar, Dict, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import to_json
from src.services.service import (
    ContractError,
    screen_injection,
    strip_control_chars,
    validate_input_context,
)

# Maximum accepted length of a research query (S-2 domain input check).
_MAX_INPUT_CHARS = 2000


def _check_input_length(state: AgentState) -> Optional[str]:
    """S-2 domain check: enforce the research-query length limit.

    Module-level function (not an instance method) so the framework does not
    register it as a security-gate hook during node build. Returns a violation
    reason string when the input exceeds _MAX_INPUT_CHARS, or None when it is
    within bounds.

    The same check runs inline in PreProcessNode.execute() so no oversized
    query ever reaches the inner domain workflow.
    """
    user_input = state.get("user_input", "")
    if isinstance(user_input, str) and len(user_input) > _MAX_INPUT_CHARS:
        return f"user_input exceeds the {_MAX_INPUT_CHARS}-char research-query limit ({len(user_input)} chars)"
    return None


class PreProcessNode(FunctionNode):
    """S-1 input validation: validate and normalize the research request.

    Rejects empty / invalid / oversized input and any caller parameter that
    fails its contract before the inner domain workflow graph runs.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        user_input = state.get("user_input", "")
        input_context = state.get("input_context", {})  # read-only

        if not user_input or not isinstance(user_input, str) or not user_input.strip():
            emit_trace_event("research_request_rejected", {"reason": "empty_input"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: user_input is empty or missing"],
            }

        if len(user_input) > _MAX_INPUT_CHARS:
            emit_trace_event(
                "research_request_rejected",
                {"reason": "input_too_long", "input_chars": len(user_input)},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    f"PreProcessNode: user_input exceeds the {_MAX_INPUT_CHARS}-char limit ({len(user_input)} chars)"
                ],
            }

        # Chat-template control tokens and directive phrases are refused as a
        # class, on the raw text and on its normalised form. The framework's
        # own input gate covers the high-confidence phrases; this screen also
        # holds for the token forms it scores low.
        injection = screen_injection(user_input)
        if injection is not None:
            emit_trace_event(
                "research_request_rejected",
                {"reason": "injection_pattern", "pattern_class": injection},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PreProcessNode: user_input contains a prompt-injection pattern ({injection})"],
            }

        validated_input = strip_control_chars(user_input.strip())

        # Structured caller parameters: bounded, closed, finite, screened.
        try:
            contract = validate_input_context(input_context)
        except ContractError as exc:
            emit_trace_event(
                "research_request_rejected",
                {"reason": "invalid_input_context", "field": exc.field},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PreProcessNode: {exc.field} {exc.reason}"],
            }

        enriched_context: Dict[str, Any] = {
            "source": "ManufacturingEngineeringResearchToTechnicalReportAgent",
            "channel": contract["channel"],
            "report_requirements": contract["report_requirements"],
        }

        # S-4 domain audit: a manufacturing research-to-report request was
        # accepted and normalized.
        emit_trace_event(
            "research_request_accepted",
            {
                "input_chars": len(validated_input),
                "caller_material_count": len(contract["research_materials"]),
            },
            state,
        )

        return {
            "validated_input": validated_input,
            "enriched_context": to_json(enriched_context),
            "caller_contract": to_json(contract),
            "status": AgentStatus.SUCCESS.value,
        }
