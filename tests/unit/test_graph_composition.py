# MFG-C2-063 -- Unit Tests: nested Cat-2 graph composition (end-to-end)
#
# Drives the REAL outer agent
# (ManufacturingEngineeringResearchToTechnicalReportAgent / Graph) end-to-end
# via the AgentBaseGraph compile-on-first-use invoke() path. The inner
# DomainWorkflowGraph runs all 3 domain nodes; the terminal
# TechnicalReportGenerationNode sets status=SUCCESS, which the outer
# merge_output maps to the outer state so the backbone routes
# main -> post_process -> finalize.
#
# The caller runs at VERIFIED_EXTERNAL -- the trust level the manifest
# declares -- so these tests prove the agent serves a request at exactly the
# level it is deployed for, not only for a fully-trusted internal caller.
#
# Also proven here: the runtime configuration reaches the inner graph
# (a declared value changes the released report), the validated caller
# contract crosses the outer/inner boundary, and the output cannot be
# reached through the framework's `result` fallback.
#
# Deterministic -- the nodes synthesize the report rule-based (no LLM, no
# network). framework.* / src.* imports only.

import pytest
from framework.errors import ConfigError
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.domain_workflow_graph import DomainWorkflowGraph
from src.graph.graph import DomainWorkflowGraphNode, Graph, ManufacturingEngineeringResearchToTechnicalReportAgent
from src.schemas.state import State, from_json

_QUERY = "Survey CNC predictive maintenance and SPC yield methods for the shop floor."


def _ctx(level=TrustLevel.VERIFIED_EXTERNAL):
    return InvocationContext(caller_id="test-suite", caller_trust_level=level)


def _run(user_input: str, input_context=None, config=None, level=TrustLevel.VERIFIED_EXTERNAL) -> dict:
    agent = Graph(config=config or {})
    return agent.invoke(user_input, ctx=_ctx(level), input_context=input_context or {})


def _doc(title, content, **overrides):
    base = {"title": title, "content": content, "type": "paper", "language": "en"}
    base.update(overrides)
    return base


def _reference_lines(output: str) -> list:
    refs = output.split("## 参考文献", 1)[1]
    return [line for line in refs.splitlines() if line.startswith("- ")]


class TestOuterGraphConstruction:
    def test_state_schema_is_state(self):
        assert ManufacturingEngineeringResearchToTechnicalReportAgent().state_schema is State

    def test_agent_name(self):
        assert (
            ManufacturingEngineeringResearchToTechnicalReportAgent().name
            == "ManufacturingEngineeringResearchToTechnicalReportAgent"
        )

    def test_compile_fills_all_backbone_slots(self):
        agent = ManufacturingEngineeringResearchToTechnicalReportAgent()
        agent.compile()
        for slot in ("initialize", "pre_process", "main", "post_process", "finalize"):
            assert agent._nodes.get(slot) is not None, f"slot not filled: {slot}"

    def test_merge_output_never_writes_the_result_fallback(self):
        """AgentBaseGraph.get_output falls back to state['result'] whenever
        formatted_output is empty; a value there would bypass the output gate."""
        delta = DomainWorkflowGraphNode().merge_output({}, {"output": "R", "status": "success"})
        assert "result" not in delta
        assert "formatted_output" not in delta


class TestInnerGraphConstruction:
    def test_inner_graph_registers_three_domain_nodes(self):
        inner = DomainWorkflowGraph()
        inner.register_nodes()
        assert set(inner._nodes.keys()) == {
            "research_ingestion",
            "research_synthesis",
            "technical_report_generation",
        }

    def test_inner_graph_name_and_schema(self):
        inner = DomainWorkflowGraph()
        assert inner.name == "mfg_c2_063_domain_workflow"
        assert inner.state_schema is State

    def test_inner_graph_get_output_maps_report_to_output(self):
        inner = DomainWorkflowGraph()
        out = inner.get_output({"report_draft": "R", "status": AgentStatus.SUCCESS.value})
        assert out["output"] == "R"
        assert out["report_draft"] == "R"
        assert out["status"] == AgentStatus.SUCCESS.value

    @pytest.mark.parametrize(
        "block",
        [
            "not a mapping",
            {"max_materials": 0},
            {"max_materials": 51},
            {"max_materials": True},
            {"max_materials": "8"},
            {"inline_documents": "doc"},
            {"inline_documents": [{"title": "t"}]},
            {"inline_documents": [{"title": "t", "content": "c", "relevance_score": "NaN"}]},
        ],
    )
    def test_invalid_runtime_block_fails_at_compile(self, block):
        with pytest.raises(ConfigError):
            DomainWorkflowGraph(config={"research_report": block}).compile()

    def test_outer_max_retry_reaches_the_backbone_validator(self):
        with pytest.raises(ConfigError):
            Graph(config={"max_retry": 99}).compile()


class TestEndToEndInvoke:
    """Full agent run at the declared trust level: outer backbone + inner workflow, no LLM."""

    def test_invoke_returns_success(self):
        result = _run(_QUERY)
        assert result.get("status") == AgentStatus.SUCCESS.value, f"result={result!r}"

    def test_invoke_output_is_populated_report(self):
        output = _run(_QUERY).get("output")
        assert isinstance(output, str) and output.strip()

    def test_report_contains_five_japanese_sections(self):
        output = _run(_QUERY).get("output", "")
        for heading in ("## 概要", "## 主要知見", "## 技術的考察", "## 製造現場への推奨事項", "## 参考文献"):
            assert heading in output, f"e2e report missing section: {heading}"

    def test_report_carries_citations(self):
        output = _run(_QUERY).get("output", "")
        # The offline reference corpus contributes example.org citation URLs.
        assert "example.org" in output

    def test_node_history_records_backbone_traversal(self):
        history = _run(_QUERY).get("node_history", [])
        assert isinstance(history, list)
        for cls_name in ("PreProcessNode", "DomainWorkflowGraphNode", "PostProcessNode"):
            assert cls_name in history, f"node_history missing {cls_name}: {history}"

    def test_anonymous_caller_is_denied_without_a_report(self, monkeypatch):
        import src.nodes.research_ingestion_node as ingestion

        ran = []
        monkeypatch.setattr(ingestion, "emit_trace_event", lambda *a, **k: ran.append(a[0]))
        result = _run(_QUERY, level=TrustLevel.ANONYMOUS)
        assert result["status"] == AgentStatus.ERROR.value
        assert not result.get("output")
        # Denied at pre_process; the inner pipeline never runs and the output
        # gate is never reached.
        assert ran == []
        assert "PostProcessNode" not in result.get("node_history", [])

    def test_internal_caller_is_also_served(self):
        assert _run(_QUERY, level=TrustLevel.INTERNAL)["status"] == AgentStatus.SUCCESS.value


class TestCallerContractReachesTheInnerGraph:
    def test_caller_materials_are_cited_in_the_report(self):
        ctx = {"research_materials": [_doc("Spindle vibration signatures study", "Bearing wear predicted early.")]}
        output = _run(_QUERY, input_context=ctx)["output"]
        assert "Spindle vibration signatures study" in output
        assert "example.org" not in output  # the reference corpus did not apply

    def test_caller_title_is_the_report_title(self):
        ctx = {"report_requirements": {"title": "予知保全 技術調査レポート"}}
        assert _run(_QUERY, input_context=ctx)["output"].startswith("# 予知保全 技術調査レポート\n")

    def test_citation_count_follows_the_number_of_materials(self):
        one = {"research_materials": [_doc("A", "Finding a.")]}
        five = {"research_materials": [_doc(f"Doc {i}", f"Finding {i}.") for i in range(5)]}
        assert len(_reference_lines(_run(_QUERY, input_context=one)["output"])) == 1
        assert len(_reference_lines(_run(_QUERY, input_context=five)["output"])) == 5

    def test_invalid_caller_field_is_refused_end_to_end(self, monkeypatch):
        import src.nodes.research_ingestion_node as ingestion

        ran = []
        monkeypatch.setattr(ingestion, "emit_trace_event", lambda *a, **k: ran.append(a[0]))
        ctx = {"research_materials": [_doc("A", "Finding a.", relevance_score="NaN")]}
        result = _run(_QUERY, input_context=ctx)
        assert result["status"] == AgentStatus.ERROR.value
        assert not result.get("output")
        # Refused at pre_process: the framework skips every later execute()
        # once the state carries an error, so the inner pipeline never ran on
        # the raw query and the output gate was never reached.
        assert ran == []
        assert "PostProcessNode" not in result.get("node_history", [])

    def test_contract_does_not_leak_across_invocations(self):
        ctx = {"research_materials": [_doc("Only once", "Finding once.")]}
        assert "Only once" in _run(_QUERY, input_context=ctx)["output"]
        assert "Only once" not in _run(_QUERY)["output"]


class TestRuntimeConfigIsLive:
    def test_max_materials_changes_the_released_report(self):
        ctx = {"research_materials": [_doc(f"Doc {i}", f"Finding {i}.") for i in range(6)]}
        n_one = len(
            _reference_lines(
                _run(_QUERY, input_context=ctx, config={"research_report": {"max_materials": 1}})["output"]
            )
        )
        n_three = len(
            _reference_lines(
                _run(_QUERY, input_context=ctx, config={"research_report": {"max_materials": 3}})["output"]
            )
        )
        assert (n_one, n_three) == (1, 3)

    def test_operator_documents_from_config_are_cited(self):
        config = {"research_report": {"inline_documents": [_doc("Operator welding spec WS-42", "Weld parameters.")]}}
        assert "Operator welding spec WS-42" in _run(_QUERY, config=config)["output"]

    def test_config_file_values_are_the_ones_the_entry_points_load(self):
        from src.graph.graph import runtime_config

        loaded = runtime_config()
        assert loaded["research_report"]["max_materials"] == 8
        assert loaded["max_retry"] == 3
        # And the loaded file drives the same path the tests above exercised.
        ctx = {"research_materials": [_doc(f"Doc {i}", f"Finding {i}.") for i in range(10)]}
        assert len(_reference_lines(_run(_QUERY, input_context=ctx, config=loaded)["output"])) == 8


class TestStateRoundTrip:
    """The JSON-string State fields survive the full producer->consumer chain."""

    def test_inner_nodes_chain_via_json_string_fields(self):
        from src.nodes.research_ingestion_node import ResearchIngestionNode
        from src.nodes.research_synthesis_node import ResearchSynthesisNode
        from src.nodes.technical_report_generation_node import (
            TechnicalReportGenerationNode,
        )

        state: dict = {"validated_input": "CNC predictive maintenance SPC yield"}

        # ingestion -> research_materials (JSON string)
        state.update(ResearchIngestionNode().execute(state))
        assert isinstance(state["research_materials"], str)

        # synthesis consumes research_materials, emits synthesis_result + citations
        state.update(ResearchSynthesisNode().execute(state))
        assert isinstance(state["synthesis_result"], str)
        assert isinstance(state["citation_list"], str)

        # report generation consumes synthesis_result -> report_draft + metadata
        state.update(TechnicalReportGenerationNode().execute(state))
        assert state["status"] == AgentStatus.SUCCESS.value
        assert isinstance(state["report_draft"], str) and state["report_draft"].strip()

        meta = from_json(state["report_metadata"])
        assert meta["domain"] == "manufacturing"
        # total_sources matches the citation count produced upstream.
        assert meta["total_sources"] == len(from_json(state["citation_list"]))
