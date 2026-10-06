"""AgentCore Platform v1.0"""

# MFG-C2-063 -- DomainWorkflowGraph (inner BaseGraph)
#
# This is the INNER graph for the Cat 2 two-layer nested architecture.
# It encapsulates the full manufacturing research-to-technical-report domain
# workflow:
#
#   START -> research_ingestion -> research_synthesis
#         -> technical_report_generation -> END
#
# Called by DomainWorkflowGraphNode.get_subgraph() (graph.py), which hands it
# the `research_report` block of the runtime configuration the outer graph was
# constructed with (config/config.yaml under the registry and the standalone
# entry point alike).
#
# How a value reaches a domain node: FunctionNode.execute() receives no config
# argument, so the graph seeds its validated settings -- and the caller
# contract carried over the context bridge -- into the inner state in
# _extra_initial_state(). Nodes read them back with from_json().
#
# Rules enforced:
#   - Inherits BaseGraph (fully custom topology -- no forced backbone)
#   - Implements all 7 BaseGraph ABC methods
#   - register_nodes() does NOT call super() (abstract in BaseGraph)
#   - register_nodes() instantiates every domain node with NO ctor args
#   - Does NOT register initialize / finalize (outer backbone concerns)
#   - get_output() designed together with DomainWorkflowGraphNode.merge_output()
#   - No agenticstar imports

from typing import Any, Dict, List

from langgraph.graph import END, START

from framework.errors import ConfigError
from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import take_caller_contract
from src.nodes.research_ingestion_node import ResearchIngestionNode
from src.nodes.research_synthesis_node import ResearchSynthesisNode
from src.nodes.technical_report_generation_node import TechnicalReportGenerationNode
from src.schemas.state import State, to_json
from src.services.service import (
    DEFAULT_MAX_MATERIALS,
    MAX_MATERIALS_CEILING,
    ContractError,
    validate_materials,
)


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for MFG-C2-063.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by DomainWorkflowGraphNode.get_subgraph() in graph.py.

    Pipeline (linear):
        START
          -> research_ingestion           (ResearchIngestionNode)
          -> research_synthesis           (ResearchSynthesisNode)
          -> technical_report_generation  (TechnicalReportGenerationNode)
          -> END

    All nodes are FunctionNode subclasses returning partial-dict state updates.
    initialize / finalize are outer backbone concerns -- not registered here.
    """

    # -- Identity --------------------------------------------------------------

    @property
    def name(self) -> str:
        """Unique identifier for this inner graph."""
        return "mfg_c2_063_domain_workflow"

    @property
    def state_schema(self) -> type:
        """TypedDict subclass shared across inner and outer graph."""
        return State

    # -- Config validation -----------------------------------------------------

    def _validate_config(self) -> None:
        """Validate the `research_report` runtime block before compilation.

        `max_materials` must be an integer in [1, MAX_MATERIALS_CEILING];
        `inline_documents` must satisfy the same material schema a caller's
        documents do. An absent block means defaults; an invalid one is a
        ConfigError at compile time rather than a silent fallback at runtime.
        """
        block = self.config.get("research_report")
        if block is None:
            return
        if not isinstance(block, dict):
            raise ConfigError(f"[{self.__class__.__name__}] 'research_report' must be a mapping, got: {block!r}")
        max_materials = block.get("max_materials")
        if max_materials is not None and (
            isinstance(max_materials, bool)
            or not isinstance(max_materials, int)
            or not 1 <= max_materials <= MAX_MATERIALS_CEILING
        ):
            raise ConfigError(
                f"[{self.__class__.__name__}] 'research_report.max_materials' must be an integer "
                f"between 1 and {MAX_MATERIALS_CEILING}, got: {max_materials!r}"
            )
        try:
            validate_materials(block.get("inline_documents"), "research_report.inline_documents")
        except ContractError as exc:
            raise ConfigError(f"[{self.__class__.__name__}] {exc}") from exc

    def _settings(self) -> Dict[str, Any]:
        """The validated runtime settings the domain nodes read from state."""
        block = self.config.get("research_report") or {}
        if not isinstance(block, dict):
            block = {}
        documents: List[Dict[str, Any]] = validate_materials(
            block.get("inline_documents"), "research_report.inline_documents"
        )
        max_materials = block.get("max_materials")
        if isinstance(max_materials, bool) or not isinstance(max_materials, int):
            max_materials = DEFAULT_MAX_MATERIALS
        return {"max_materials": max_materials, "inline_documents": documents}

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the inner state with the caller contract and the runtime settings.

        The caller contract arrives over the context bridge (set by the outer
        GraphNode one step earlier) and is consumed here; the settings come
        from the config this graph was constructed with.
        """
        return {
            "caller_contract": to_json(take_caller_contract()),
            "pipeline_settings": to_json(self._settings()),
        }

    # -- Node registration -----------------------------------------------------

    def register_nodes(self) -> None:
        """Register all 3 domain nodes.

        No super() call -- BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.

        Every node is instantiated with NO constructor arguments; runtime
        values reach them through the seeded state, never a constructor.
        Every key registered here is referenced in add_edges().
        """
        self._nodes["research_ingestion"] = ResearchIngestionNode()
        self._nodes["research_synthesis"] = ResearchSynthesisNode()
        self._nodes["technical_report_generation"] = TechnicalReportGenerationNode()

    # -- Edge wiring -----------------------------------------------------------

    def add_edges(self) -> None:
        """Wire the linear research-to-report domain topology.

        Each step passes its partial-dict output into the shared State.
        For this template the topology is intentionally linear -- no conditional
        branching between domain nodes. route() is implemented as required by
        the ABC but add_conditional_edges() is not used.
        """
        self._sg.add_edge(START, "research_ingestion")
        self._sg.add_edge("research_ingestion", "research_synthesis")
        self._sg.add_edge("research_synthesis", "technical_report_generation")
        self._sg.add_edge("technical_report_generation", END)

    # -- Routing ---------------------------------------------------------------

    def route(self, state: State) -> str:
        """Conditional routing -- required by BaseGraph ABC.

        For this linear topology add_conditional_edges() is not used, so this
        method is never called at runtime. It is implemented to satisfy the ABC
        contract. Returns END on error so an unexpected call does not re-enter a
        processing node.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return str(END)
        return "technical_report_generation"

    # -- Output shape ----------------------------------------------------------

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the output dict returned to the outer graph as sub_result.

        This dict is received by DomainWorkflowGraphNode.merge_output() in
        graph.py as the `sub_result` argument. Both methods are designed
        together to guarantee field-name consistency:

            Inner get_output()  emits: "output", "citation_list",
                                       "report_metadata", "status", ...
            Outer merge_output() reads: sub_result.get("output"),
                                        sub_result.get("citation_list"), ...

        "output" carries the rendered report (report_draft -- the inner pipeline
        has no separate formatted_output; PostProcessNode finalizes it at the
        outer layer).

        citation_list and report_metadata are passed through UNCHANGED as the
        JSON-string State fields (ADR-005 msgpack safety): the outer state
        declares them as JSON-string fields too, and the outer PostProcessNode
        deserializes citation_list via from_json(). Deserializing here would put
        a bare list/dict into the JSON-string outer field and break that
        contract. Additional fields (trace_id, correlation_id, node_history) are
        surfaced for observability / downstream extension.
        """
        return {
            "output": state.get("formatted_output") or state.get("report_draft"),
            "report_draft": state.get("report_draft"),
            "citation_list": state.get("citation_list"),
            "report_metadata": state.get("report_metadata"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
