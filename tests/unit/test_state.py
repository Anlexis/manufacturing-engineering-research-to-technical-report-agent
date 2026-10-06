# MFG-C2-063 -- Unit Tests: State schema + JSON field helpers
#
# State is a flat TypedDict extending AgentState (ADR-005 msgpack safety): no
# Pydantic / dataclass fields, structured fields stored as JSON strings via
# to_json() / from_json(). These tests pin the schema contract and the
# serialization round-trip the producer/consumer nodes rely on.
#
# Deterministic -- no LLM, no network. framework.* / src.* imports only.

import typing


from src.schemas.state import State, to_json, from_json


class TestStateSchema:
    def test_state_is_typeddict_subclass_of_agent_state(self):
        # TypedDict inheritance is structural: the child TypedDict's
        # get_type_hints() includes all parent annotations. On the real SDK
        # wheel, TypedDict sets __orig_bases__ = (dict,) due to metaclass
        # behavior -- not (AgentState,). The reliable check is via
        # get_type_hints() which reflects all inherited annotations.
        hints = typing.get_type_hints(State)
        # AgentState defines these fields; all must be reachable from State.
        for field in (
            "user_input",
            "status",
            "session_id",
            "formatted_output",
            "enriched_context",
            "node_history",
        ):
            assert field in hints, f"State is missing inherited AgentState field: {field}"

    def test_state_instance_is_a_plain_dict(self):
        # A TypedDict instantiates to a builtin dict at runtime (msgpack-safe).
        s: State = {"user_input": "x"}  # type: ignore[assignment]
        assert isinstance(s, dict)

    def test_domain_fields_declared_in_annotations(self):
        ann = typing.get_type_hints(State)
        for field in (
            "validated_input",
            "enriched_context",
            "formatted_output",
            "research_materials",
            "synthesis_result",
            "citation_list",
            "report_draft",
            "report_metadata",
        ):
            assert field in ann, f"State missing declared field: {field}"

    def test_domain_fields_accessible_by_key(self):
        s: State = {  # type: ignore[assignment]
            "validated_input": "q",
            "research_materials": to_json([{"title": "t"}]),
            "report_draft": "draft",
        }
        assert s["validated_input"] == "q"
        assert s["report_draft"] == "draft"

    def test_no_pydantic_or_dataclass_annotation_strings(self):
        # Defense-in-depth mirror of the PB state-safety scan: the declared
        # field annotations must not reference Pydantic/dataclass/context types.
        ann = typing.get_type_hints(State)
        rendered = " ".join(str(v) for v in ann.values())
        for prohibited in ("BaseModel", "InvocationContext", "dataclass"):
            assert prohibited not in rendered


class TestJsonHelpers:
    def test_to_json_then_from_json_roundtrip_list(self):
        value = [{"title": "Paper", "relevance_score": 0.9}]
        assert from_json(to_json(value)) == value

    def test_to_json_then_from_json_roundtrip_dict(self):
        value = {"language": "ja", "total_sources": 3}
        assert from_json(to_json(value)) == value

    def test_to_json_none_passes_through(self):
        assert to_json(None) is None

    def test_from_json_none_returns_default(self):
        assert from_json(None, []) == []
        assert from_json(None) is None

    def test_from_json_malformed_returns_default(self):
        assert from_json("{not valid json", {"fallback": True}) == {"fallback": True}

    def test_to_json_preserves_non_ascii(self):
        # ensure_ascii=False -> Japanese text survives the round-trip.
        value = {"title": "製造技術レポート"}
        assert from_json(to_json(value)) == value
