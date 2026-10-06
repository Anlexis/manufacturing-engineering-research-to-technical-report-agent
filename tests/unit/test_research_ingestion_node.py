# MFG-C2-063 -- Unit Tests: ResearchIngestionNode (inner domain node 1)
#
# Ingests research materials (papers / patents / standards) for the validated
# research query. Sources, in order of authority: the caller's materials
# (bridged in as caller_contract), the operator's documents (seeded as
# pipeline_settings), and the built-in reference corpus as the baseline.
# Writes research_materials as a JSON string of material dicts.
#
# Deterministic -- no LLM, no network. framework.* / src.* imports only.

from framework.schemas.agent_status import AgentStatus

from src.nodes.research_ingestion_node import ResearchIngestionNode, _MATERIAL_KEYS
from src.schemas.state import from_json, to_json


def _materials(result: dict) -> list:
    return from_json(result["research_materials"], [])


def _doc(title, content, **overrides):
    base = {"title": title, "url": "", "content": content, "type": "paper", "language": "en"}
    base.update(overrides)
    return base


class TestResearchIngestionContract:
    def test_valid_query_populates_materials(self):
        node = ResearchIngestionNode()
        result = node.execute({"validated_input": "CNC predictive maintenance"})
        assert result["status"] == AgentStatus.SUCCESS.value
        materials = _materials(result)
        assert isinstance(materials, list)
        assert len(materials) >= 1

    def test_research_materials_is_json_string(self):
        node = ResearchIngestionNode()
        result = node.execute({"validated_input": "tolerance machining standard"})
        assert isinstance(result["research_materials"], str)

    def test_each_material_has_required_keys(self):
        node = ResearchIngestionNode()
        result = node.execute({"validated_input": "semiconductor yield SPC"})
        materials = _materials(result)
        assert materials
        for material in materials:
            for key in _MATERIAL_KEYS:
                assert key in material, f"material missing key: {key}"
            assert isinstance(material["relevance_score"], float)

    def test_materials_sorted_by_relevance_desc(self):
        node = ResearchIngestionNode()
        result = node.execute({"validated_input": "semiconductor yield SPC defect"})
        scores = [m["relevance_score"] for m in _materials(result)]
        assert scores == sorted(scores, reverse=True)

    def test_only_changed_keys_returned_on_success(self):
        node = ResearchIngestionNode()
        result = node.execute({"validated_input": "lean manufacturing"})
        assert set(result.keys()) == {"research_materials", "status"}


class TestCallerMaterials:
    def test_caller_materials_replace_the_reference_corpus(self):
        contract = {"research_materials": [_doc("Robotic welding spec WS-42", "Robotic welding parameters.")]}
        result = ResearchIngestionNode().execute(
            {"validated_input": "robotic welding thin-gauge steel", "caller_contract": to_json(contract)}
        )
        titles = [m["title"] for m in _materials(result)]
        assert titles == ["Robotic welding spec WS-42"]

    def test_caller_material_with_no_overlap_is_still_kept(self):
        # The caller chose the document; the ranking orders it, never drops it.
        contract = {"research_materials": [_doc("Unrelated source", "Nothing in common with the query.")]}
        result = ResearchIngestionNode().execute(
            {"validated_input": "semiconductor yield", "caller_contract": to_json(contract)}
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert [m["title"] for m in _materials(result)] == ["Unrelated source"]

    def test_number_of_materials_follows_the_input(self):
        one = {"research_materials": [_doc("A", "Finding a.")]}
        five = {"research_materials": [_doc(f"Doc {i}", f"Finding {i}.") for i in range(5)]}
        n_one = len(
            _materials(ResearchIngestionNode().execute({"validated_input": "q", "caller_contract": to_json(one)}))
        )
        n_five = len(
            _materials(ResearchIngestionNode().execute({"validated_input": "q", "caller_contract": to_json(five)}))
        )
        assert (n_one, n_five) == (1, 5)

    def test_supplied_relevance_score_orders_the_result(self):
        contract = {
            "research_materials": [
                _doc("Low", "Nothing relevant.", relevance_score=0.1),
                _doc("High", "Nothing relevant either.", relevance_score=0.9),
            ]
        }
        result = ResearchIngestionNode().execute({"validated_input": "q", "caller_contract": to_json(contract)})
        assert [m["title"] for m in _materials(result)] == ["High", "Low"]


class TestOperatorDocumentsAndSettings:
    def test_inline_documents_from_runtime_settings_are_included(self):
        settings = {
            "max_materials": 8,
            "inline_documents": [
                _doc(
                    "Internal welding spec WS-42",
                    "Robotic welding parameters for thin-gauge steel.",
                    url="https://intranet.example.org/ws42",
                    type="spec",
                )
            ],
        }
        result = ResearchIngestionNode().execute(
            {"validated_input": "robotic welding thin-gauge steel", "pipeline_settings": to_json(settings)}
        )
        titles = [m["title"] for m in _materials(result)]
        assert "Internal welding spec WS-42" in titles

    def test_max_materials_caps_the_working_set(self):
        contract = {"research_materials": [_doc(f"Doc {i}", f"Finding {i}.") for i in range(6)]}
        result = ResearchIngestionNode().execute(
            {
                "validated_input": "q",
                "caller_contract": to_json(contract),
                "pipeline_settings": to_json({"max_materials": 2, "inline_documents": []}),
            }
        )
        assert len(_materials(result)) == 2

    def test_caller_and_operator_documents_are_ranked_together(self):
        contract = {"research_materials": [_doc("Caller doc", "Spindle vibration signatures.")]}
        settings = {"max_materials": 8, "inline_documents": [_doc("Operator doc", "Takt-time balancing.")]}
        result = ResearchIngestionNode().execute(
            {
                "validated_input": "spindle vibration",
                "caller_contract": to_json(contract),
                "pipeline_settings": to_json(settings),
            }
        )
        titles = [m["title"] for m in _materials(result)]
        assert titles == ["Caller doc", "Operator doc"]


class TestMaskedQuery:
    def test_fully_masked_query_falls_back_to_the_baseline(self):
        # The platform's input filter can replace a title-case query with its
        # sentinel; a sentinel is not a search term, so the baseline applies
        # instead of an empty result.
        result = ResearchIngestionNode().execute({"validated_input": "[MASKED]"})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert len(_materials(result)) >= 1

    def test_partially_masked_query_scores_on_the_remaining_terms(self):
        result = ResearchIngestionNode().execute({"validated_input": "Survey CNC [MASKED]"})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert any("CNC" in m["title"] for m in _materials(result))


class TestResearchIngestionEmptyResults:
    def test_no_matches_with_query_returns_error(self):
        node = ResearchIngestionNode()
        # A query with no keyword overlap with any corpus entry scores 0.0 on
        # every reference item, so all are skipped -> ERROR (no materials).
        result = node.execute({"validated_input": "zzqqxx nonexistent unrelatedterm flumbax"})
        assert result["status"] == AgentStatus.ERROR.value
        assert result["error_log"]
        assert "research_materials" not in result
