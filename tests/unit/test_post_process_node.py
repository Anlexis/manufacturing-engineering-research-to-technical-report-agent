# MFG-C2-063 -- Unit Tests: PostProcessNode (outer post_process slot; S-3 point)
#
# PostProcessNode reads state["report_draft"] (merged from the inner graph) plus
# citation_list, appends the references block, enforces the report invariant
# (five mandatory sections, minimum length, no credential shape) and surfaces
# formatted_output. On a violation it returns a sanitised stub + ERROR and
# clears every output-bearing field. A too-short, structurally incomplete or
# empty report is rejected with ERROR.
#
# The S-3 credential-block boundary is also exercised here as a unit (the full
# boundary proof lives in tests/proof_of_boundary/test_pb_mfg_c2_063.py and the
# end-to-end containment proof in tests/integration/test_invoke_contract.py).
#
# Deterministic -- no LLM, no network. framework.* / src.* imports only.

import pytest
from framework.schemas.agent_status import AgentStatus

from src.nodes.post_process_node import (
    _CLEARED_ON_VIOLATION,
    _MIN_REPORT_CHARS,
    PostProcessNode,
    _check_output_credentials,
)
from src.schemas.state import to_json

# A clean five-section report comfortably above the _MIN_REPORT_CHARS gate.
_CLEAN_REPORT = "\n\n".join(
    [
        "# Manufacturing Technical Report",
        "## 概要",
        "FY2025 predictive-maintenance survey across CNC machining lines. " * 3,
        "## 主要知見",
        "- Two lines show measurable downtime reduction after rollout.",
        "## 技術的考察",
        "Findings are consistent across both production lines surveyed.",
        "## 製造現場への推奨事項",
        "- Validate on a pilot line before full rollout.",
        "## 参考文献",
        "- CNC predictive maintenance (patent). https://patents.example.org/jp/cnc",
    ]
)

# Simulated credential strings -- NOT real secrets.
_API_KEY = "sk-TESTKEY1234567890abcdefghijklmn"
_CRED_STR = "password=super_secret_value_abc123"
_AWS_KEY = "AKIAABCDEFGHIJKLMNOP"


class TestPostProcessSuccess:
    def test_valid_report_surfaces_formatted_output(self):
        node = PostProcessNode()
        result = node.execute({"report_draft": _CLEAN_REPORT})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert _CLEAN_REPORT in result["formatted_output"]

    def test_citations_not_duplicated_when_draft_has_references(self):
        node = PostProcessNode()
        result = node.execute(
            {
                "report_draft": _CLEAN_REPORT,
                "citation_list": to_json(["Predictive maintenance (patent). https://patents.example.org/jp/cnc"]),
            }
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"].count("## 参考文献") == 1

    def test_only_changed_keys_returned_on_clean_success(self):
        node = PostProcessNode()
        result = node.execute({"report_draft": _CLEAN_REPORT})
        assert set(result.keys()) == {"formatted_output", "status"}


class TestPostProcessRejection:
    def test_empty_report_is_error(self):
        node = PostProcessNode()
        result = node.execute({"report_draft": ""})
        assert result["status"] == AgentStatus.ERROR.value
        assert result["error_log"]

    def test_missing_report_is_error(self):
        node = PostProcessNode()
        result = node.execute({})
        assert result["status"] == AgentStatus.ERROR.value

    def test_too_short_report_is_error(self):
        node = PostProcessNode()
        short = "x" * (_MIN_REPORT_CHARS - 10)
        result = node.execute({"report_draft": short})
        assert result["status"] == AgentStatus.ERROR.value

    @pytest.mark.parametrize(
        "heading", ["## 概要", "## 主要知見", "## 技術的考察", "## 製造現場への推奨事項", "## 参考文献"]
    )
    def test_report_missing_a_mandatory_section_is_error(self, heading):
        draft = _CLEAN_REPORT.replace(heading, "## removed")
        result = PostProcessNode().execute({"report_draft": draft})
        assert result["status"] == AgentStatus.ERROR.value
        assert "formatted_output" not in result
        assert "mandatory section" in result["error_log"][0]


class TestPostProcessS3Gate:
    """S-3 output gate: credential-like content is blocked at the surface."""

    def test_api_key_in_report_is_blocked(self):
        node = PostProcessNode()
        report_with_key = _CLEAN_REPORT + f"\n<!-- debug: api_key={_API_KEY} -->"
        result = node.execute({"report_draft": report_with_key})
        assert result["status"] == AgentStatus.ERROR.value
        assert _API_KEY not in result.get("formatted_output", "")

    def test_blocked_output_is_sanitised_stub(self):
        node = PostProcessNode()
        report_with_key = _CLEAN_REPORT + f"\nleaked api_key={_API_KEY}"
        result = node.execute({"report_draft": report_with_key})
        out = result.get("formatted_output", "")
        assert "BLOCKED" in out or "S-3" in out
        assert result.get("error_log")

    def test_credential_assignment_is_blocked(self):
        node = PostProcessNode()
        report_with_cred = _CLEAN_REPORT + f"\nInternal note: {_CRED_STR}"
        result = node.execute({"report_draft": report_with_cred})
        assert result["status"] == AgentStatus.ERROR.value
        assert _CRED_STR not in result.get("formatted_output", "")

    def test_framework_only_shape_is_blocked_by_the_node_itself(self):
        """AWS key ids are not in the local pattern set; the framework floor catches them
        inside this node -- before the framework's own gate would have to."""
        result = PostProcessNode().execute({"report_draft": _CLEAN_REPORT + f"\nkey id {_AWS_KEY}"})
        assert result["status"] == AgentStatus.ERROR.value
        assert _AWS_KEY not in result.get("formatted_output", "")
        assert _AWS_KEY not in " ".join(result["error_log"])

    def test_violation_clears_every_output_bearing_field(self):
        result = PostProcessNode().execute(
            {"report_draft": _CLEAN_REPORT + f"\n{_CRED_STR}", "citation_list": to_json(["c"])}
        )
        assert result["status"] == AgentStatus.ERROR.value
        assert result["formatted_output"]  # truthy notice: the fallback stays closed
        for field, value in _CLEARED_ON_VIOLATION.items():
            assert field in result and result[field] is value
        assert _CRED_STR not in str(result)

    def test_clean_report_passes_gate_unchanged(self):
        node = PostProcessNode()
        result = node.execute({"report_draft": _CLEAN_REPORT})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == _CLEAN_REPORT

    def test_output_credential_check_detects_violation(self):
        """Module-level S-3 credential checker detects disallowed content."""
        reason = _check_output_credentials({"report_draft": _CLEAN_REPORT + f"\napi_key={_API_KEY}"})
        assert reason is not None

    def test_output_credential_check_allows_clean(self):
        """Module-level S-3 credential checker passes clean content."""
        reason = _check_output_credentials({"report_draft": _CLEAN_REPORT})
        assert reason is None
