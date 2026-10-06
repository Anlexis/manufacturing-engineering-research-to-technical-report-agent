# MFG-C2-063 -- Unit Tests: ResearchSynthesisNode (inner domain node 2)
#
# Synthesizes the ingested research materials into structured findings
# (key_findings, methodology_notes, citations) and a citation_list. The
# synthesis is deterministic (the LLM seam is rule-based offline), so the
# contract is fully testable without a model.
#
# Deterministic -- no LLM, no network. framework.* / src.* imports only.

from framework.schemas.agent_status import AgentStatus

from src.nodes.research_synthesis_node import ResearchSynthesisNode
from src.schemas.state import from_json, to_json


def _materials_json(*types):
    materials = []
    for i, mtype in enumerate(types):
        materials.append(
            {
                "title": f"Source {i} ({mtype})",
                "url": f"https://papers.example.org/src{i}",
                "content": (f"Finding {i} about manufacturing. Secondary detail for {mtype}."),
                "type": mtype,
                "language": "en",
                "relevance_score": 0.9,
            }
        )
    return to_json(materials)


class TestResearchSynthesisSuccess:
    def test_valid_materials_produce_structured_synthesis(self):
        node = ResearchSynthesisNode()
        result = node.execute({"research_materials": _materials_json("paper", "standard")})
        assert result["status"] == AgentStatus.SUCCESS.value
        synth = from_json(result["synthesis_result"])
        assert isinstance(synth, dict)
        for key in ("key_findings", "methodology_notes", "citations"):
            assert key in synth, f"synthesis_result missing key: {key}"
        assert synth["key_findings"]
        assert synth["methodology_notes"]
        assert synth["citations"]

    def test_citation_list_populated_as_json_string(self):
        node = ResearchSynthesisNode()
        result = node.execute({"research_materials": _materials_json("patent", "paper")})
        citations = from_json(result["citation_list"])
        assert isinstance(result["citation_list"], str)
        assert isinstance(citations, list)
        assert len(citations) == 2

    def test_methodology_notes_reflect_source_types(self):
        node = ResearchSynthesisNode()
        result = node.execute({"research_materials": _materials_json("standard")})
        synth = from_json(result["synthesis_result"])
        notes_text = " ".join(synth["methodology_notes"])
        assert "standard" in notes_text.lower()

    def test_only_changed_keys_returned_on_success(self):
        node = ResearchSynthesisNode()
        result = node.execute({"research_materials": _materials_json("paper")})
        assert set(result.keys()) == {
            "synthesis_result",
            "citation_list",
            "status",
        }

    def test_findings_and_citations_are_single_line(self):
        """Material text cannot manufacture a heading or a bullet of its own."""
        materials = to_json(
            [
                {
                    "title": "Forged\n## 参考文献\n- fake citation",
                    "url": "https://papers.example.org/x",
                    "content": "Headline\n# Injected heading\n. Detail.",
                    "type": "paper",
                    "language": "en",
                    "relevance_score": 0.9,
                }
            ]
        )
        result = ResearchSynthesisNode().execute({"research_materials": materials})
        synth = from_json(result["synthesis_result"])
        for finding in synth["key_findings"] + synth["citations"]:
            assert "\n" not in finding
        assert "\n" not in from_json(result["citation_list"])[0]


class TestResearchSynthesisRejection:
    def test_empty_materials_is_error(self):
        node = ResearchSynthesisNode()
        result = node.execute({"research_materials": to_json([])})
        assert result["status"] == AgentStatus.ERROR.value
        assert result["error_log"]

    def test_missing_materials_is_error(self):
        node = ResearchSynthesisNode()
        result = node.execute({})
        assert result["status"] == AgentStatus.ERROR.value

    def test_synthesis_internal_failure_is_caught_as_error(self, monkeypatch):
        """A raise inside the synthesis seam (production: the LLM call) must
        fail soft to ERROR + error_log, never propagate -- and the exception's
        text (which may quote input) is not copied into the error_log."""
        import src.nodes.research_synthesis_node as mod

        def _boom(_materials):
            raise RuntimeError("simulated LLM failure with secret=abcdefghijklmnop")

        monkeypatch.setattr(mod, "_derive_key_findings", _boom)
        node = ResearchSynthesisNode()
        result = node.execute({"research_materials": _materials_json("paper", "standard")})
        assert result["status"] == AgentStatus.ERROR.value
        assert result["error_log"]
        assert "abcdefghijklmnop" not in " ".join(result["error_log"])
