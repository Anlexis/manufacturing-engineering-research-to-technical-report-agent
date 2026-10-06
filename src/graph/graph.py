"""AgentCore Platform v1.0"""

# MFG-C2-063 -- Outer graph (AgentBaseGraph; Cat 2 two-layer nested architecture)
#
# Manufacturing Engineering Research To Technical Report Agent
# (DocGeneration pattern, Cat 2).
#
# Architecture (Cat 2):
#
#   Outer backbone (fixed -- identical to Cat 1, do NOT override add_edges()):
#     START -> initialize -> pre_process -> main -> {route} -> post_process -> finalize -> END
#                                              | (RETRY, max 3)
#                                           pre_process
#
#   `main` slot is a GraphNode subclass (DomainWorkflowGraphNode) that
#   delegates the full research-to-report domain workflow to
#   DomainWorkflowGraph (inner BaseGraph).
#
#   Domain complexity is fully encapsulated inside the inner graph. The outer
#   backbone is never modified.
#
# Directory layout:
#   src/graph/graph.py                 <- outer graph (this file)
#   src/graph/domain_workflow_graph.py <- inner graph (multi-step topology)
#   src/graph/context_bridge.py        <- validated caller contract across the boundary
#
# Runtime configuration: the registry loads config/config.yaml and passes it as
# Graph(config=...); the standalone entry point does the same through
# runtime_config(). AgentBaseGraph consumes max_retry (retry routing); the
# `research_report` block is forwarded to the inner graph, which seeds it into
# the domain nodes' state.
#
# Rules enforced:
#   - ManufacturingEngineeringResearchToTechnicalReportAgent inherits
#     AgentBaseGraph (L1 Base -- direct inheritance)
#   - super().register_nodes() called first (fills initialize + finalize)
#   - DomainWorkflowGraphNode assigned to self._nodes["main"]
#   - merge_output() returns only changed keys
#   - add_edges() NOT overridden on the outer graph
#   - No Level 0 agenticstar imports

from pathlib import Path
from typing import Any, ClassVar, Dict, Optional

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from framework.utils.config_loader import load_agent_config
from src.graph.context_bridge import set_caller_contract
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State, from_json

# Repo root: src/graph/graph.py -> parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]


def runtime_config() -> Dict[str, Any]:
    """Load config/config.yaml -- the live runtime parameters.

    The registry loads this file and passes it to the graph constructor; the
    standalone HTTP entry point does the same, so a declared value is live in
    both deployments rather than declared in one and ignored in the other.

    Reading the static manifest (config/agent.yaml) here instead would return
    nothing: the manifest carries identity and compile-time requirements only.
    """
    loaded = load_agent_config(_REPO_ROOT)
    return dict(loaded) if isinstance(loaded, dict) else {}


class DomainWorkflowGraphNode(GraphNode):
    """GraphNode subclass assigned to the `main` slot of the outer agent.

    Wraps DomainWorkflowGraph (inner Cat 2 BaseGraph).
    Called by AgentBaseGraph backbone after pre_process and before post_process.

    Contracts:
      get_subgraph()    -- instantiate DomainWorkflowGraph with the forwarded runtime block
      extract_input()   -- bridge the validated caller contract, return validated_input
      merge_output()    -- map sub_result fields into outer state delta (changed keys only)
      error_strategy    -- "propagate": re-raise inner errors as SubgraphError (fail-fast)
    """

    # "propagate": re-raise inner graph exceptions as SubgraphError (default -- fail fast).
    # An inner error status is re-raised the same way, so the outer state never
    # receives a merged result on a non-success path.
    error_strategy: ClassVar[str] = "propagate"

    # False: HITL interrupts are handled inside the inner graph only.
    # True: surface inner HITL interrupt to the outer caller.
    propagate_hitl: ClassVar[bool] = False

    # The manifest declares VERIFIED_EXTERNAL for the agent; the wrapper holds
    # the same line as every node it fronts, so the trust decision is uniform
    # across the backbone rather than implicit at the subgraph boundary.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, parent_config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self._parent_config_value: Dict[str, Any] = dict(parent_config or {})

    def _parent_config(self) -> Dict[str, Any]:
        """The runtime block the inner graph reads, taken from the outer graph's config.

        Only `research_report` crosses: the backbone keys (max_retry, hitl,
        memory_enabled) belong to the outer graph and must not re-trigger the
        inner graph's validation.
        """
        block = self._parent_config_value.get("research_report")
        return {"research_report": dict(block)} if isinstance(block, dict) else {}

    def get_subgraph(self) -> Any:
        """Instantiate and return the inner domain workflow graph.

        DomainWorkflowGraph is imported lazily (inside the method) to avoid
        circular-import risk at module load time and to match the pattern in
        the Cat 2 scaffold sample. It receives the forwarded runtime block; its
        domain nodes take no constructor arguments and read their settings
        from the seeded state.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Return the string input passed into inner_graph.invoke(), and bridge the contract.

        The framework hands only a string to the inner graph, so the validated
        caller contract (research materials, report requirements) travels on
        the context bridge instead -- set here, one step before the inner
        invoke, and consumed by the inner graph's initial-state hook.

        PreProcessNode validates and normalizes the raw user_input and writes
        the result to validated_input. Prefer that; fall back to user_input if
        validated_input is absent (e.g. in unit tests). On a real invocation
        that fallback is never taken after a refusal: once PreProcessNode has
        set an error status, the framework's node wrapper skips every later
        execute() -- this method included -- and the backbone routes to
        finalize, so the inner pipeline never runs on the raw query.
        """
        set_caller_contract(from_json(state.get("caller_contract"), {}) or {})
        return str(state.get("validated_input") or state.get("user_input", ""))

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map inner graph sub_result back into the outer state delta.

        sub_result is the dict returned by DomainWorkflowGraph.get_output().
        Returns ONLY changed keys -- never the full state.

        Key coupling (designed together with DomainWorkflowGraph.get_output()):
          Inner get_output() emits  -> "output", "citation_list",
                                        "report_metadata", "status"
          This merge_output() reads -> sub_result.get("output"), etc.

        report_draft (str | None): the generated Japanese technical report; the
          outer PostProcessNode (post_process slot) reads state["report_draft"],
          appends citations, runs the output gate, and surfaces formatted_output.
          The inner graph emits the report under "output", so map it to
          "report_draft". citation_list / report_metadata are passed through as
          the JSON-string fields PostProcessNode expects.

        `result` is deliberately NOT written: the framework's output projection
        falls back to state["result"] whenever formatted_output is empty, so a
        value there would reach the caller on every error path without passing
        the output gate.
        """
        return {
            "report_draft": sub_result.get("output"),
            "citation_list": sub_result.get("citation_list"),
            "report_metadata": sub_result.get("report_metadata"),
            "status": sub_result.get("status"),
        }


class ManufacturingEngineeringResearchToTechnicalReportAgent(AgentBaseGraph):
    """Outer graph for MFG-C2-063 (Cat 2).

    Inherits AgentBaseGraph directly (L1 Base). Domain logic is fully
    encapsulated in DomainWorkflowGraphNode (main slot), which delegates to
    DomainWorkflowGraph (inner BaseGraph).

    Backbone (fixed -- identical to Cat 1):
        START -> initialize -> pre_process -> main -> post_process -> finalize -> END

    register_nodes() is the ONLY override:
      - super().register_nodes() fills: initialize, finalize (framework defaults)
      - pre_process:  PreProcessNode (input validation + caller contract)
      - main:         DomainWorkflowGraphNode (delegates to DomainWorkflowGraph)
      - post_process: PostProcessNode (output gate)

    add_edges() is NOT overridden -- backbone wiring belongs to the framework.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with AgentRegistry."""
        return "ManufacturingEngineeringResearchToTechnicalReportAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first -- it injects the
        framework's default InitializeNode (sets schema_version, session_id,
        trust_level) and FinalizeNode (builds response_metadata, total_time_ms).
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = DomainWorkflowGraphNode(parent_config=self.config)
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden -- backbone wiring belongs to the framework.


# Back-compat alias -- src/api/server.py imports `Graph`; config/agent.yaml
# declares the descriptive class name as the manifest entry point.
Graph = ManufacturingEngineeringResearchToTechnicalReportAgent
