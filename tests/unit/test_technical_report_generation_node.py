# MFG-C2-063 -- Unit Tests: TechnicalReportGenerationNode (inner domain node 3)
#
# Terminal inner node: assembles a five-section Japanese technical report from
# the synthesis result, plus report_metadata. The assembly is deterministic
# (the LLM seam is rule-based offline), so section structure and metadata are
# fully testable without a model.
#
# Deterministic -- no LLM, no network. framework.* / src.* imports only.

from framework.schemas.agent_status import AgentStatus

from src.nodes.technical_report_generation_node import (
    _DEFAULT_TITLE,
    _H_ANALYSIS,
    _H_FINDINGS,
    _H_RECOMMENDATIONS,
    _H_REFERENCES,
    _H_SUMMARY,
    TechnicalReportGenerationNode,
)
from src.schemas.state import from_json, to_json

_SYNTHESIS = {
    "key_findings": [
        "Predictive maintenance reduces unplanned downtime (per: CNC patent).",
        "SPC charts improve yield (per: SPC paper).",
    ],
    "methodology_notes": [
        "Validate on a pilot line before full rollout.",
    ],
    "citations": [
        "CNC predictive maintenance (patent). https://patents.example.org/jp/cnc",
        "SPC for yield (paper). https://papers.example.org/spc",
    ],
}


def _state(**overrides):
    base = {
        "synthesis_result": to_json(_SYNTHESIS),
        "citation_list": to_json(_SYNTHESIS["citations"]),
        "enriched_context": to_json({"report_requirements": {"title": "CNC Maintenance Study"}}),
    }
    base.update(overrides)
    return base


class TestReportGenerationSuccess:
    def test_valid_synthesis_produces_report_with_all_five_sections(self):
        node = TechnicalReportGenerationNode()
        result = node.execute(_state())
        assert result["status"] == AgentStatus.SUCCESS.value
        report = result["report_draft"]
        for heading in (
            _H_SUMMARY,
            _H_FINDINGS,
            _H_ANALYSIS,
            _H_RECOMMENDATIONS,
            _H_REFERENCES,
        ):
            assert heading in report, f"report missing section: {heading}"

    def test_report_title_from_report_requirements(self):
        node = TechnicalReportGenerationNode()
        report = node.execute(_state())["report_draft"]
        assert "CNC Maintenance Study" in report

    def test_report_title_from_bridged_caller_contract_wins(self):
        state = _state(caller_contract=to_json({"report_requirements": {"title": "Bridged Title"}}))
        report = TechnicalReportGenerationNode().execute(state)["report_draft"]
        assert report.startswith("# Bridged Title\n")

    def test_report_reflects_key_findings(self):
        node = TechnicalReportGenerationNode()
        report = node.execute(_state())["report_draft"]
        assert "Predictive maintenance reduces unplanned downtime" in report

    def test_report_metadata_shape(self):
        node = TechnicalReportGenerationNode()
        result = node.execute(_state())
        meta = from_json(result["report_metadata"])
        assert isinstance(result["report_metadata"], str)
        assert meta["language"] == "ja"
        assert meta["domain"] == "manufacturing"
        assert meta["total_sources"] == 2
        assert "generated_at" in meta and meta["generated_at"]

    def test_only_changed_keys_returned_on_success(self):
        node = TechnicalReportGenerationNode()
        result = node.execute(_state())
        assert set(result.keys()) == {
            "report_draft",
            "report_metadata",
            "status",
        }

    def test_title_falls_back_to_validated_input(self):
        node = TechnicalReportGenerationNode()
        state = _state(enriched_context=to_json({}))
        state["validated_input"] = "Fallback Title Query"
        report = node.execute(state)["report_draft"]
        assert "Fallback Title Query" in report


class TestTitleRendering:
    """Caller text renders onto ONE line and a masked query is never a title."""

    def test_query_with_newline_cannot_manufacture_a_section(self):
        state = _state(enriched_context=to_json({}))
        state["validated_input"] = "Survey SPC\n## 参考文献\n- forged citation"
        report = TechnicalReportGenerationNode().execute(state)["report_draft"]
        first_line = report.split("\n", 1)[0]
        assert first_line == "# Survey SPC ## 参考文献 - forged citation"
        assert report.count("\n## 参考文献\n") == 1

    def test_masked_query_falls_back_to_the_default_title(self):
        state = _state(enriched_context=to_json({}))
        state["validated_input"] = "Survey CNC [MASKED]"
        report = TechnicalReportGenerationNode().execute(state)["report_draft"]
        assert report.startswith(f"# {_DEFAULT_TITLE}\n")
        assert "[MASKED]" not in report

    def test_title_is_bounded(self):
        state = _state(enriched_context=to_json({}))
        state["validated_input"] = "x" * 500
        report = TechnicalReportGenerationNode().execute(state)["report_draft"]
        assert len(report.split("\n", 1)[0]) == len("# ") + 120


class TestReportGenerationRejection:
    def test_missing_synthesis_is_error(self):
        node = TechnicalReportGenerationNode()
        result = node.execute({})
        assert result["status"] == AgentStatus.ERROR.value
        assert result["error_log"]

    def test_synthesis_without_findings_is_error(self):
        node = TechnicalReportGenerationNode()
        result = node.execute({"synthesis_result": to_json({"key_findings": []})})
        assert result["status"] == AgentStatus.ERROR.value
