# PB -- MFG-C2-063 domain boundaries (PB-1, PB-3, PB-6)
#
# Complements the generic boundary tests in this directory:
#   - test_import_isolation.py  -> PB-4 (no Level-0 imports under src/)
#   - test_state_safety.py      -> PB-2 / PB-5 (msgpack-safe State, no credentials)
#   - test_pb_invoke_order.py   -> PB-6 (gate order for every node under src/nodes/)
#
# This file proves the domain-specific boundaries for MFG-C2-063:
#   PB-1: BaseNode -> EventEmitter -- each node fires emit_trace_event() (S-4).
#   PB-3: L1 -> external service -- ResearchIngestionNode returns non-empty
#         research_materials and TechnicalReportGenerationNode returns a
#         non-empty report_draft (the retrieval / LLM integration seam).
#   PB-6: Invoke execution order -- the S-1 trust gate in BaseNode.__call__ runs
#         BEFORE execute(); insufficient trust is denied and execute() never
#         runs. The outer backbone visits nodes in the fixed order and the
#         surfaced output is guaranteed to have passed the S-3 PostProcess gate.
#
# Deterministic -- no LLM, no network. framework.* / src.* imports only.

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.nodes.research_ingestion_node import ResearchIngestionNode
from src.nodes.research_synthesis_node import ResearchSynthesisNode
from src.nodes.technical_report_generation_node import TechnicalReportGenerationNode
from src.schemas.state import from_json, to_json

_QUERY = "CNC predictive maintenance SPC yield survey for the shop floor."


def _spy_emit(monkeypatch, module):
    """Patch emit_trace_event in a node module with a call-counting spy.

    Each node does `from shared.utils.audit_logger import emit_trace_event`, so
    the name is bound in that module's namespace -- patch it there.
    """
    calls = []

    def _fake(event_name, payload=None, state=None):
        calls.append(event_name)

    monkeypatch.setattr(module, "emit_trace_event", _fake)
    return calls


class TestPB1TraceEmission:
    """PB-1: every node emits at least one S-4 audit trace event."""

    def test_pre_process_emits_trace(self, monkeypatch):
        import src.nodes.pre_process_node as mod

        calls = _spy_emit(monkeypatch, mod)
        PreProcessNode().execute({"user_input": _QUERY})
        assert len(calls) >= 1

    def test_research_ingestion_emits_trace(self, monkeypatch):
        import src.nodes.research_ingestion_node as mod

        calls = _spy_emit(monkeypatch, mod)
        ResearchIngestionNode().execute({"validated_input": _QUERY})
        assert len(calls) >= 1

    def test_research_synthesis_emits_trace(self, monkeypatch):
        import src.nodes.research_synthesis_node as mod

        calls = _spy_emit(monkeypatch, mod)
        materials = to_json(
            [
                {
                    "title": "SPC paper",
                    "url": "https://papers.example.org/spc",
                    "content": "SPC improves yield. Detail.",
                    "type": "paper",
                    "language": "en",
                    "relevance_score": 0.9,
                }
            ]
        )
        ResearchSynthesisNode().execute({"research_materials": materials})
        assert len(calls) >= 1

    def test_technical_report_generation_emits_trace(self, monkeypatch):
        import src.nodes.technical_report_generation_node as mod

        calls = _spy_emit(monkeypatch, mod)
        synthesis = to_json(
            {
                "key_findings": ["SPC improves yield (per: SPC paper)."],
                "methodology_notes": ["Validate on a pilot line."],
                "citations": ["SPC paper. https://papers.example.org/spc"],
            }
        )
        TechnicalReportGenerationNode().execute({"synthesis_result": synthesis, "citation_list": to_json(["c"])})
        assert len(calls) >= 1

    def test_post_process_emits_trace(self, monkeypatch):
        import src.nodes.post_process_node as mod

        calls = _spy_emit(monkeypatch, mod)
        report = "\n\n".join(
            [
                "# Report",
                "## 概要",
                "Clean manufacturing report body content line. " * 6,
                "## 主要知見",
                "- finding",
                "## 技術的考察",
                "analysis",
                "## 製造現場への推奨事項",
                "- action",
                "## 参考文献",
                "- source",
            ]
        )
        PostProcessNode().execute({"report_draft": report})
        assert len(calls) >= 1


class TestPB3ExternalServiceSeam:
    """PB-3: the retrieval and report-generation seams return real, non-empty
    structured data (offline-deterministic stand-in for the live integration)."""

    def test_ingestion_returns_non_empty_materials(self):
        result = ResearchIngestionNode().execute({"validated_input": _QUERY})
        assert result["status"] == AgentStatus.SUCCESS.value
        materials = from_json(result["research_materials"], [])
        assert isinstance(materials, list) and len(materials) >= 1
        # The integration contract: each record carries the retrieval schema.
        first = materials[0]
        for key in ("title", "url", "content", "type", "relevance_score"):
            assert key in first

    def test_report_generation_returns_non_empty_draft(self):
        synthesis = to_json(
            {
                "key_findings": ["Predictive maintenance cuts downtime (per: patent)."],
                "methodology_notes": ["Verify FTO before deployment."],
                "citations": ["CNC patent. https://patents.example.org/jp/cnc"],
            }
        )
        result = TechnicalReportGenerationNode().execute(
            {"synthesis_result": synthesis, "citation_list": to_json(["c"])}
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert isinstance(result["report_draft"], str)
        assert result["report_draft"].strip()


class TestPB6InvokeOrderTrustGateFirst:
    """PB-6: the S-1 trust gate (BaseNode.__call__) runs BEFORE execute().

    A privileged node invoked through __call__ with an insufficient caller trust
    level must be denied WITHOUT execute() running -- proving the gate precedes
    the node body in the invocation order. We detect "execute() ran" by spying
    on the node's emit_trace_event (execute() is the only thing that emits).

    Every node of this agent declares VERIFIED_EXTERNAL -- the level the
    manifest declares for the agent -- so an ANONYMOUS caller is the denied
    case and a caller at the declared level is served."""

    def test_low_trust_denied_before_execute_runs(self, monkeypatch):
        import src.nodes.research_ingestion_node as mod

        calls = _spy_emit(monkeypatch, mod)
        node = ResearchIngestionNode()
        assert node.required_trust_level == TrustLevel.VERIFIED_EXTERNAL
        # __call__ reads caller_trust_level from state (ADR-006). ANONYMOUS < VERIFIED_EXTERNAL.
        out = node({"validated_input": _QUERY, "caller_trust_level": TrustLevel.ANONYMOUS.value})
        assert out["status"] == AgentStatus.ERROR.value
        assert any("trust gate" in e.lower() for e in out.get("error_log", []))
        # execute() never ran -> no trace emitted -> gate is strictly before execute().
        assert calls == []

    def test_declared_trust_allows_execute(self, monkeypatch):
        import src.nodes.research_ingestion_node as mod

        calls = _spy_emit(monkeypatch, mod)
        node = ResearchIngestionNode()
        out = node({"validated_input": _QUERY, "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value})
        assert out["status"] == AgentStatus.SUCCESS.value
        # execute() ran -> at least one trace emitted.
        assert len(calls) >= 1

    def test_every_domain_node_holds_the_declared_level(self):
        for node_cls in (
            PreProcessNode,
            ResearchIngestionNode,
            ResearchSynthesisNode,
            TechnicalReportGenerationNode,
            PostProcessNode,
        ):
            assert node_cls.required_trust_level == TrustLevel.VERIFIED_EXTERNAL, node_cls.__name__


class TestPB6BackboneOrderAndS3Surface:
    """PB-6 (cont.): the outer backbone visits nodes in the fixed order, and the
    surfaced output is guaranteed to have passed the S-3 PostProcess gate."""

    def _run(self):
        ctx = InvocationContext(caller_id="test-suite", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        return Graph().invoke(_QUERY, ctx=ctx)

    def test_pre_process_runs_before_main_wrapper(self):
        history = self._run().get("node_history", [])
        assert "PreProcessNode" in history
        assert "DomainWorkflowGraphNode" in history
        assert history.index("PreProcessNode") < history.index("DomainWorkflowGraphNode")

    def test_post_process_s3_gate_in_surfaced_history(self):
        history = self._run().get("node_history", [])
        assert "PostProcessNode" in history, f"surfaced output did not traverse the S-3 PostProcess gate: {history}"
        # And the gate runs after the main wrapper produced the report.
        assert history.index("DomainWorkflowGraphNode") < history.index("PostProcessNode")
