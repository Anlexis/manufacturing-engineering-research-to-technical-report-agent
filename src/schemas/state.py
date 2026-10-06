"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict -- never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.
#
# WARNING -- ADR-005 (msgpack safety): structured fields (dict / list[dict]) are
# stored as JSON STRINGS, not bare Python containers -- a bare dict/list in a
# checkpointed State field is a CoE gate-state-safety violation. Producers
# serialize with to_json() on write; consumers deserialize with from_json()
# on read.
#
# MFG-C2-063 -- Manufacturing Engineering Research To Technical Report Agent
# Two-layer nested Cat 2 graph: outer backbone (AgentBaseGraph) + inner
# domain workflow (BaseGraph).  Fields below cover both layers.
#
# Pipeline: a manufacturing-engineering research request (JA or EN) is ingested
# from research sources (papers / patents / standards), synthesized by an LLM
# seam into structured findings, and drafted into a Japanese technical report
# with citations.  No secrets / credentials are ever written to State (checkpoint
# DB leakage risk).

import json
from typing import Any, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> Optional[str]:
    """Serialize a dict/list State field to a JSON string (ADR-005 msgpack safety).

    None passes through unchanged so an 'unset' field stays distinguishable
    from an empty container.
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON-string State field back to its dict/list.

    None / empty / malformed input -> the supplied ``default`` so a missing or
    corrupt field is non-fatal for the consuming node.
    """
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class State(AgentState):
    """Flat TypedDict for MFG-C2-063.

    All shared fields (user_input, status, session_id, node_history,
    error_log, validated_input, enriched_context, formatted_output,
    trace_id, correlation_id, hitl_*, etc.) are inherited from AgentState
    (via SubgraphContext) -- do NOT re-declare them here.

    Re-declaring any AgentState / SubgraphContext field with a narrower type
    (e.g. Optional[str] over str, or Optional[str] over Any) corrupts
    LangGraph channel assembly on the real SDK (agenticstar-agentcore wheel),
    causing state=None to reach backbone nodes at runtime.

    Safe to redeclare: validated_input (AgentState: Any -> Optional[str])
    confirmed green by a peer template reference template.

    NOT safe to redeclare:
    - formatted_output (AgentState: Any)       -- keeps inherited Any
    - enriched_context (AgentState: dict)      -- keeps inherited dict
    - trace_id (SubgraphContext: str)          -- keeps inherited str
    - correlation_id (SubgraphContext: str)    -- keeps inherited str
    """

    # ------------------------------------------------------------------
    # Outer layer -- set by PreProcessNode / DomainWorkflowGraphNode.merge_output
    # ------------------------------------------------------------------

    # Sanitized / normalized research query produced by PreProcessNode (S-1).
    # Raw user_input is NOT persisted beyond PreProcessNode.
    # NOTE: validated_input is also in AgentState (as Any). Redeclaring as
    # Optional[str] is safe here (confirmed by green a peer template reference).
    validated_input: Optional[str]

    # enriched_context -- inherited from AgentState (as dict).
    # PreProcessNode writes to_json(enriched_context) = JSON string.
    # NOT re-declared here: re-declaring as Optional[str] overrides AgentState's
    # enriched_context: dict and breaks LangGraph channel assembly on the wheel.

    # formatted_output -- inherited from AgentState (as Any).
    # PostProcessNode writes the final report string here after the S-3 gate.
    # NOT re-declared here: re-declaring as Optional[str] overrides AgentState's
    # formatted_output: Any and breaks LangGraph channel assembly on the wheel.

    # trace_id / correlation_id -- inherited from SubgraphContext (as str).
    # Framework-managed; do NOT write from node code, do NOT re-declare here.
    # Re-declaring as Optional[str] narrows str -> Optional[str] and corrupts
    # LangGraph channel assembly on the real SDK wheel.

    # PreProcessNode output. JSON STRING (to_json) of the VALIDATED caller
    # contract extracted from input_context:
    # {"channel": str, "report_requirements": {"title"?: str, "language"?: str},
    #  "research_materials": [material dict, ...]}
    # Every value passed its bound / enum / finite-range / screen before it was
    # written here. Bridged into the inner graph by DomainWorkflowGraphNode.
    caller_contract: Optional[str]

    # ------------------------------------------------------------------
    # Inner layer -- domain nodes (DomainWorkflowGraph)
    # ------------------------------------------------------------------

    # Seeded by DomainWorkflowGraph._extra_initial_state() from the runtime
    # parameters (config/config.yaml `research_report` block) the graph was
    # constructed with. JSON STRING (to_json) of
    # {"max_materials": int, "inline_documents": [material dict, ...]}.
    # State seeding is the only route a runtime value can travel into a domain
    # node: FunctionNode.execute() receives no config argument.
    pipeline_settings: Optional[str]

    # ResearchIngestionNode output.
    # JSON STRING (to_json) of the list of ingested research materials. Each
    # item dict shape:
    # {"title": str, "url": str, "content": str, "type": str,
    #  "language": str, "relevance_score": float}
    # Consumer (ResearchSynthesisNode) reads it back via from_json().
    research_materials: Optional[str]

    # ResearchSynthesisNode output.
    # JSON STRING (to_json) of the structured synthesis. Deserialised dict shape:
    # {"key_findings": [str], "methodology_notes": [str], "citations": [str]}
    # Consumer (TechnicalReportGenerationNode) reads it back via from_json().
    synthesis_result: Optional[str]

    # Ordered list of formatted citation strings (JIS Z 8301 / APA style).
    # JSON STRING (to_json) of list[str]. Produced by ResearchSynthesisNode,
    # appended to the report by TechnicalReportGenerationNode / PostProcessNode.
    citation_list: Optional[str]

    # TechnicalReportGenerationNode output.
    # Generated Japanese technical report draft (Markdown). Contains the five
    # mandatory sections; raw resident/credential data is never included here.
    report_draft: Optional[str]

    # TechnicalReportGenerationNode output.
    # JSON STRING (to_json) of report metadata. Deserialised dict shape:
    # {"language": "ja", "domain": "manufacturing",
    #  "generated_at": ISO8601 str, "total_sources": int}
    report_metadata: Optional[str]
