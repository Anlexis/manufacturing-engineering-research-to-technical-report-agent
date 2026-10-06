"""AgentCore Platform v1.0"""

# MFG-C2-063 -- ResearchIngestionNode
# Domain node 1: ingest research materials (academic papers, patents, technical
# standards) for a manufacturing-engineering research query.
#
# Pattern: retrieval (a peer template conceptual reference -- L1 direct inheritance,
# not a class-level import).
#
# Sources, in this order of authority:
#   1. research materials the caller supplied on input_context (validated by
#      PreProcessNode, bridged into this graph as `caller_contract`)
#   2. documents the operator configured in config/config.yaml
#      (`research_report.inline_documents`, seeded as `pipeline_settings`)
#   3. the built-in manufacturing reference corpus -- the baseline used only
#      when neither of the above is present
#
# SDK v1.0.0rc1 ships no framework retrieval client. Ranking is deterministic
# keyword overlap, so the node is fully testable offline. Production wires the
# real vector store / web search at the marked seam.
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
from src.services.service import DEFAULT_MAX_MATERIALS, MATERIAL_KEYS, rank_materials

logger = logging.getLogger(__name__)

# Required keys on every research-material record (re-exported for tests).
_MATERIAL_KEYS = MATERIAL_KEYS


class ResearchIngestionNode(FunctionNode):
    """Ingest research materials for the validated research query.

    Input state keys:
        validated_input:   normalized research query (from PreProcessNode)
        caller_contract:   JSON string of the validated caller contract (bridged)
        pipeline_settings: JSON string of the runtime settings (seeded by the graph)

    Output state keys (partial dict):
        research_materials: JSON string of list[material dict]
        status:             ERROR (+ error_log) when no materials found
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        query = str(state.get("validated_input") or state.get("user_input", "")).strip()

        contract = from_json(state.get("caller_contract"), {}) or {}
        settings = from_json(state.get("pipeline_settings"), {}) or {}
        caller_materials: List[Dict[str, Any]] = (
            list(contract.get("research_materials") or []) if isinstance(contract, dict) else []
        )
        operator_documents: List[Dict[str, Any]] = (
            list(settings.get("inline_documents") or []) if isinstance(settings, dict) else []
        )
        limit = settings.get("max_materials") if isinstance(settings, dict) else None
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            limit = DEFAULT_MAX_MATERIALS

        # Production wires the real retrieval source here.
        materials = rank_materials(query, caller_materials, operator_documents, limit)

        if not materials:
            emit_trace_event("research_materials_empty", {"query_chars": len(query)}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "ResearchIngestionNode: no research materials found for the "
                    "query (check retrieval source configuration)"
                ],
            }

        source = "caller" if caller_materials else ("operator" if operator_documents else "reference")

        # S-4 domain audit: research materials ingested.
        emit_trace_event(
            "research_materials_ingested",
            {
                "material_count": len(materials),
                "source_types": sorted({str(m["type"]) for m in materials}),
                "source": source,
            },
            state,
        )

        logger.info(
            "ResearchIngestionNode: ingested %d materials (types=%s, source=%s)",
            len(materials),
            sorted({str(m["type"]) for m in materials}),
            source,
        )

        return {
            "research_materials": to_json(materials),
            "status": AgentStatus.SUCCESS.value,
        }
