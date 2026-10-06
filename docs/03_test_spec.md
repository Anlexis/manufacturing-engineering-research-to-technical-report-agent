# Test Specification — MFG-C2-063

**Template ID**: MFG-C2-063
**Agent**: ManufacturingEngineeringResearchToTechnicalReportAgent

## Test Strategy
- Coverage target: **>= 80%** on `src/nodes/`, `src/graph/` and `src/services/`
- Test types: Unit / Integration / Proof-of-Boundary
- Every suite runs deterministically offline (no LLM, no network) against the
  released framework wheel; the integration suite drives the real ASGI entry
  point (`src/api/server.py`) through the real compiled graph.

### Shipped test files

| File | Scope |
|------|-------|
| `tests/unit/test_service.py` | Anchored identifier grammars, finite-range parser, single-line rendering, injection and credential screens (both directions), structured personal-data masking, ranking |
| `tests/unit/test_pre_process_node.py` | Query validation, injection refusal in the query, caller-contract rejection matrix (every field, every bound, non-finite numbers), normalisation |
| `tests/unit/test_research_ingestion_node.py` | Caller / operator / reference sources, ranking, `max_materials` cap, masked-query fallback |
| `tests/unit/test_research_synthesis_node.py` | Synthesis contract, single-line findings and citations, fail-soft error path |
| `tests/unit/test_technical_report_generation_node.py` | Five-section report, title policy (caller title, one-line query, masked query, bound) |
| `tests/unit/test_post_process_node.py` | Output invariant: sections, length, credential union, field clearing on violation |
| `tests/unit/test_graph_composition.py` | Nested graph end to end at the declared trust level, config liveness, contract bridging, `result` fallback closed |
| `tests/unit/test_state.py` | State schema and JSON helpers |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | TC-06 / TC-07 |
| `tests/integration/test_invoke_contract.py` | Real `/invoke`: bearer auth, adapter screens, contract rejection, containment; the STG payload equals the test fixture |
| `tests/integration/test_manifest_identity_alignment.py` | Entry-point secret scope and class path read from the manifest |
| `tests/proof_of_boundary/test_pb_mfg_c2_063.py` | PB-1, PB-3, PB-6 (domain) |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | PB-6 gate order for every node under `src/nodes/` |
| `tests/proof_of_boundary/test_import_isolation.py` | PB-4 |
| `tests/proof_of_boundary/test_state_safety.py` | PB-2 / PB-5 (static) |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | PB-7 (skipped: HITL not enabled) |

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass | PASS — `State(AgentState)` TypedDict, structured fields are JSON strings |
| TC-02 | Invalid input refused | ERROR status, nothing carried forward | PASS — PreProcessNode returns ERROR on empty/oversized/injection input and on every malformed caller field (`test_pre_process_node.py`) |
| TC-03 | No JWT/Credential in State | CI `gate-credential-scan`: 0 violations | PASS — no credential field names/values in state.py |
| TC-04 | InvocationContext never stored in State | State holds no context object | PASS — `test_state_safety.py`, `test_state.py` |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start`/`node_complete`/`node_error` absent from `execute()` body | 0 duplicates — only domain events emitted |
| TC-06 | S-2: `_security_gate_input()` not overridden (FunctionNode) | `TypeError` if overridden (`@final`) | PASS — `test_framework_compliance_tc06_tc07.py` |
| TC-07 | S-3: `_security_gate_output()` not overridden (FunctionNode) | `TypeError` if overridden (`@final`) | PASS — `test_framework_compliance_tc06_tc07.py` |
| TC-08 | `required_trust_level` enforced | Insufficient trust -> refused; declared level served | PASS — every node VERIFIED_EXTERNAL; ANONYMOUS denied before `execute()`, VERIFIED_EXTERNAL served end to end (`test_pb_mfg_c2_063.py`, `test_graph_composition.py`) |
| TC-09 | S-2: domain input check non-trivial | Domain input check executes | PASS — 2000-char query limit, control-token screen, caller-contract validation |
| TC-10 | S-3: domain output check non-trivial | Domain output check executes | PASS — five-section / length / credential-union invariant, fields cleared on violation (`test_post_process_node.py`) |
| TC-11 | S-4: >=1 domain `emit_trace_event()` inside each `execute()` | Domain event on every path | PASS — `test_pb_mfg_c2_063.py::TestPB1TraceEmission` |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test method | Expected Result | Result |
|-------|----------|-------------|----------------|--------|
| PB-1 | BaseNode -> EventEmitter | Spy on `emit_trace_event`; call each node `execute()` and assert >=1 call | No silent failures | PASS — `test_pb_mfg_c2_063.py` |
| PB-2 | State serialization | Static scan of `src/schemas/state.py`; JSON-string round trip through the inner chain | No Pydantic/dataclass | PASS — `test_state_safety.py`, `test_graph_composition.py::TestStateRoundTrip` |
| PB-3 | External service | ResearchIngestionNode returns non-empty `research_materials`; TechnicalReportGenerationNode returns non-empty `report_draft` | Data retrieved / report produced | PASS — `test_pb_mfg_c2_063.py` |
| PB-4 | Import isolation | AST scan all `src/*.py`: zero `agenticstar`/`platform` imports | AST scan: 0 violations | PASS — `test_import_isolation.py` |
| PB-5 | Checkpoint safety | Regex-scan State definition: no credential-like field, no prohibited types | Inspection pass | PASS — `test_state_safety.py` |
| PB-6 | Invoke execution order | S-1 trust gate -> S-4 node_start -> S-2 `_security_gate_input` -> `execute()` -> S-3 `_security_gate_output` -> S-4 node_complete, for every node under `src/nodes/`; denial before `execute()` | Order verified | PASS — `test_pb_invoke_order.py`, `test_pb_mfg_c2_063.py` |
| PB-7 | HITL interrupt propagation | Conditional on `hitl.enabled: true` | Not applicable | SKIPPED — HITL not enabled in `config/config.yaml` |

## Business Logic Tests

| TC-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | Valid research query -> report generated | Query at the declared trust level | report contains the 5 sections (概要/主要知見/技術的考察/製造現場への推奨事項/参考文献) | PASS — `test_graph_composition.py`, `test_invoke_contract.py` |
| BL-02 | Empty research query -> PreProcess rejects | "" (empty) | status=ERROR, error_log non-empty | PASS |
| BL-03 | Oversized query -> PreProcess rejects | >2000-char string | status=ERROR (length gate) | PASS |
| BL-04 | Synthesis error -> ERROR propagated | Valid materials, synthesis raises | status=ERROR; exception text not copied into error_log | PASS |
| BL-05 | Citations rendered once | Full pipeline run | one 参考文献 section, one bullet per material | PASS |
| BL-06 | report_metadata fields all present | Full pipeline run | language=ja, domain=manufacturing, generated_at ISO8601, total_sources>=1 | PASS |
| BL-07 | No research materials -> ERROR | Query with zero corpus overlap and no caller materials | status=ERROR at ingestion, propagated to outer | PASS |
| BL-08 | Output gate blocks credential leak | report seeded with an API key / `password=` / AWS key id | formatted_output replaced with sanitised notice, status=ERROR, fields cleared | PASS |
| BL-09 | Caller materials drive the report | `input_context.research_materials` with 1 vs 5 documents | 1 vs 5 citations; titles cited | PASS |
| BL-10 | Runtime config is live | `research_report.max_materials` 1 vs 3 | 1 vs 3 citations | PASS |
| BL-11 | Contract fails closed | non-finite `relevance_score`, bad URL, unknown key, injection token | status=ERROR naming the field, no report, value never echoed | PASS |
| BL-12 | Title-case query survives masking | "Line Balancing Study" | report produced; sentinel not rendered as the title | PASS |
| BL-13 | Query cannot manufacture a section | query containing `\n## 参考文献` | exactly one 参考文献 heading | PASS |
| BL-14 | Blocked report is contained end to end | credential injected at the synthesis seam | envelope carries the notice only — no report text, no traceback, no path | PASS |

## Test Execution Summary
- Framework: released `agenticstar-agentcore` wheel (the version CI installs)
- Total tests: see the run recorded on the merge request
- Coverage target: >= 80% on `src/nodes/`, `src/graph/` and `src/services/`
