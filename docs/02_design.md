# Template Design Specification — MFG-C2-063

**Template ID**: MFG-C2-063
**Agent**: ManufacturingEngineeringResearchToTechnicalReportAgent
**Category**: Cat 2 (domain-specific document-generation pipeline)
**Industry**: MFG (Manufacturing)

## Position in AgentCore Architecture

- **Agent Class**: `ManufacturingEngineeringResearchToTechnicalReportAgent` (alias `Graph`)
- **L1 Base**: AgentBaseGraph (outer) + BaseGraph (inner domain workflow)
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution; inner graph wrapped by a `GraphNode` in the `main` slot)

This is a **Cat 2 two-layer nested** template. The outer graph is the fixed
5-node backbone; the `main` slot is a `GraphNode` (`DomainWorkflowGraphNode`)
that delegates to an inner `BaseGraph` (`DomainWorkflowGraph`) holding the
linear research-to-report domain pipeline.

## Architecture Overview

### Node Configuration

| Node | Responsibility | Input State Keys | Output State Keys | Inherits/Overrides |
|------|---------------|------------------|-------------------|--------------------|
| initialize | Session/schema init | — | session_id, schema_version, trust_level | InitializeNode (default) |
| pre_process | Query validation + normalization; caller-contract validation (S-1, S-2 length gate) | user_input, input_context | validated_input, enriched_context, caller_contract, status | PreProcessNode (FunctionNode) |
| main (DomainWorkflowGraphNode) | Inner graph orchestration; bridges caller_contract and forwards the runtime block | validated_input, caller_contract | report_draft, citation_list, report_metadata, status | GraphNode |
| -> research_ingestion | Rank caller / operator materials, or the reference corpus | validated_input, caller_contract, pipeline_settings | research_materials, status | ResearchIngestionNode (FunctionNode) |
| -> research_synthesis | Synthesis into findings + citations (LLM seam) | research_materials | synthesis_result, citation_list, status | ResearchSynthesisNode (FunctionNode) |
| -> technical_report_generation | Japanese report drafting (5 sections, LLM seam) | synthesis_result, citation_list, caller_contract, enriched_context | report_draft, report_metadata, status | TechnicalReportGenerationNode (FunctionNode) |
| post_process | Citation append + report invariant + S-3 output gate | report_draft, citation_list | formatted_output, status | PostProcessNode (FunctionNode) |
| finalize | Response metadata | — | response_metadata, total_time_ms | FinalizeNode (default) |

Every node — the outer backbone nodes, the `GraphNode` wrapper and the three
inner domain nodes — declares `required_trust_level = VERIFIED_EXTERNAL`, the
level the manifest declares for the agent. The framework forwards the caller's
context into the inner graph unchanged, so a caller admitted at the declared
level is served all the way through; an `ANONYMOUS` caller is denied at
`pre_process` and never reaches the domain pipeline.

### Data Flow

```
Outer backbone (fixed — add_edges() NOT overridden):
START -> initialize -> pre_process -> main -> {route} -> post_process -> finalize -> END
                                     | (retry, max 3)
                                  pre_process

Inner DomainWorkflowGraph (BaseGraph, linear):
START -> research_ingestion -> research_synthesis -> technical_report_generation -> END
```

### Caller-data contract (`input_context`)

The framework hands a nested graph only the request string, so the caller's
structured parameters travel as follows:

1. `PreProcessNode` validates `input_context` into the **caller contract**
   (`caller_contract`, JSON string) — every field bounded, every vocabulary
   closed, every number finite and in range, every free-text field screened
   for prompt-injection forms and credential shapes; structured personal-data
   shapes in document text are masked. A malformed field fails CLOSED with
   the field name; the rejected value is never echoed.
2. `DomainWorkflowGraphNode.extract_input()` stashes the validated contract on
   a per-task context variable (`src/graph/context_bridge.py`) one step before
   the inner invoke.
3. `DomainWorkflowGraph._extra_initial_state()` consumes it into the inner
   state, together with the runtime settings (`pipeline_settings`).

| Field | Shape | Bound |
|-------|-------|-------|
| `channel` | inert identifier `[a-z0-9_-]{1,32}` | anchored `\A…\Z` |
| `report_requirements.title` | free text, rendered onto one line | ≤ 120 chars |
| `report_requirements.language` | enum | `ja` |
| `research_materials[]` | list of material objects | ≤ 50 entries; ≤ 256 KB serialized at the entry point |
| `research_materials[].title` | free text, one line | 1–200 chars, required |
| `research_materials[].content` | free text, one line | 1–8000 chars, required |
| `research_materials[].url` | `http(s)://` URL, no whitespace/quotes | ≤ 512 chars, optional |
| `research_materials[].type` | enum | paper / patent / standard / spec / report / document |
| `research_materials[].language` | enum | ja / en |
| `research_materials[].relevance_score` | finite number | 0.0–1.0, optional (computed when absent) |

Unknown keys inside `report_requirements` or a material object are refused;
unknown top-level keys are ignored by the node (the platform may add its own)
and refused by the standalone entry point.

**Why documents travel on `input_context` and not in the query.** The
platform's input filter rewrites personal-data shapes out of the request text
at every node boundary, and its name heuristic reads any two Title Case words
as a person — `Statistical Process Control` arrives as the masking sentinel.
Research literature is title-case by convention, so document titles and text
are carried on `input_context`, which the filter does not rewrite. The
template masks the structured shapes (addresses, phone numbers, national and
card identifiers, labelled Japanese names) in that text itself and leaves the
Latin name heuristic out. A masked query is never rendered as the report title
(the generic title is used) and never scored as a search term.

### Runtime configuration

`config/config.yaml` is loaded by the registry and by the standalone entry
point and passed as `Graph(config=...)`. `max_retry` is consumed by the outer
backbone's retry routing. The `research_report` block is forwarded to the
inner graph, validated at compile time (`ConfigError` on an out-of-range
`max_materials` or a malformed operator document) and seeded into the inner
state, because `FunctionNode.execute()` receives no config argument.

| Key | Reader | Effect |
|-----|--------|--------|
| `max_retry` | AgentBaseGraph.route | retry ceiling of the backbone |
| `timeout_s` | framework-reserved | no external call to bound in this deterministic pipeline |
| `research_report.max_materials` | ResearchIngestionNode | working-set cap (1–50); changes the number of citations |
| `research_report.inline_documents` | ResearchIngestionNode | operator documents ranked with the caller's on every request |

### Output invariant (enforced by PostProcessNode)

The released report always satisfies, for every representation:

1. the five mandatory sections are present (概要 / 主要知見 / 技術的考察 / 製造現場への推奨事項 / 参考文献);
2. it is at least 200 characters long;
3. it contains no credential shape — the union of the framework detector
   (Stripe/OpenAI keys, JWT, AWS key ids, Bearer tokens, connection strings)
   and the local patterns (`pk-`/`ak-` keys, plain `password=`-style assignments);
4. every caller-controlled string — report title, material title, content
   headline, URL — was rendered onto a single line, so caller text cannot
   open a heading, a bullet or a section of its own.

On a violation the node returns ERROR **and** clears every output-bearing
field (`report_draft`, `citation_list`, `report_metadata`, `synthesis_result`,
`research_materials`, `result`); `formatted_output` carries a non-empty
sanitised notice so the framework's `result` fallback stays closed.
`merge_output()` never writes `result` for the same reason.

This template renders no monetary aggregates, so a numeric precision grid does
not apply; the invariant above is what the gate enforces.

### State Definition

All structured fields (dict / list) are stored as **JSON strings** via
`to_json()` / `from_json()` (ADR-005 msgpack safety). Base AgentState fields
(`user_input`, `status`, `session_id`, `node_history`, `error_log`, `hitl_*`)
are inherited and not redeclared.

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| validated_input | Optional[str] | Normalized research query (PreProcessNode) | Yes |
| enriched_context | Optional[str] (JSON) | Request metadata + report_requirements | Yes |
| caller_contract | Optional[str] (JSON) | Validated caller contract (channel, report_requirements, research_materials) | Yes |
| pipeline_settings | Optional[str] (JSON) | Runtime settings seeded into the inner graph (max_materials, inline_documents) | Yes |
| research_materials | Optional[str] (JSON list[dict]) | Ranked papers/patents/standards | Yes |
| synthesis_result | Optional[str] (JSON dict) | key_findings, methodology_notes, citations | Yes |
| citation_list | Optional[str] (JSON list[str]) | Ordered citation strings | Yes |
| report_draft | Optional[str] | Generated Japanese report (5 sections) | Yes |
| report_metadata | Optional[str] (JSON dict) | language, domain, generated_at, total_sources | Yes |
| formatted_output | Optional[str] | Final report w/ citations (after S-3 gate) | Yes |
| trace_id | Optional[str] | Framework-managed trace id | No |
| correlation_id | Optional[str] | Framework-managed correlation id | No |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- InvocationContext is never stored in State
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, caller trust level) — created by the entry point, propagated by the framework into the inner graph
- [x] S-1: `required_trust_level = VERIFIED_EXTERNAL` on every node; the framework's trust gate denies a lower caller before `execute()`
- [x] S-2: the framework's input gate masks personal data and rejects high-confidence injection in the query; `PreProcessNode` additionally refuses chat-template control tokens as a class and validates the caller contract (base `_security_gate_input()` is **not** overridden)
- [x] S-3: `PostProcessNode` enforces the output invariant (module-level `_security_gate_output()` on the final assembled output — the union of the framework credential detector and the local patterns); the framework's own gate runs on every node result afterwards (base `_security_gate_output()` is **not** overridden)
- [x] S-4: `emit_trace_event()` — at least one domain event inside every `execute()` (no `node_start`/`node_complete`/`node_error` emitted from node code)
- [x] Secrets: `provision_secrets()` scoped to the manifest identity (`namespace: mfg`); this template declares no secrets and no extras

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass -> framework `@final` gate always runs automatically;
>   extend via `_extra_security_gate_input()` / `_extra_security_gate_output()` only
> - `GraphNode` (`DomainWorkflowGraphNode`) -> deliberate no-op (the inner FunctionNode gates already applied)

### Composition Pattern

- **Pattern**: GraphNode (subgraph)
- **Composition target**: `DomainWorkflowGraph` (inner `BaseGraph`) at `src/graph/domain_workflow_graph.py`
- **Error propagation strategy**: propagate (inner exceptions and inner error status re-raised as `SubgraphError`; the outer state never receives a merged result on a non-success path)

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework/`, `shared/`, `src/`, `langgraph` only (no `agents/base/` required)

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type (outer) | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | Deterministic fixed multi-step pipeline, no autonomous reasoning loop — Cat 2 not Cat 3 |
| Inner graph parent | BaseGraph | AgentBaseGraph | **BaseGraph** | The inner graph needs a fully custom linear topology with no forced backbone; initialize/finalize are outer concerns, so the lighter BaseGraph fits |
| Runtime values into domain nodes | per-call `execute(state, config)` | state seeding from `config/config.yaml` | **state seeding** | `BaseNode.__call__` invokes `execute(state)` with no config argument, so a per-call config reader is dead code; the graph validates the block at compile time and seeds it into the inner state |
| Caller documents into the inner graph | read `state["input_context"]` inside inner nodes | validated contract over a context bridge | **context bridge** | The framework forwards only the request string into a nested graph; the bridge carries the contract the pre_process node already validated |
| Inner node trust level | INTERNAL | VERIFIED_EXTERNAL | **VERIFIED_EXTERNAL** | The manifest admits VERIFIED_EXTERNAL callers and the framework forwards the caller's level unchanged; INTERNAL inner nodes would deny every admitted caller |
| citation_list / report_metadata transport | bare list/dict in State | JSON string via to_json/from_json | **JSON string** | ADR-005 msgpack safety — bare containers in checkpointed State fields are a CoE gate-state-safety violation |
| S-3 gate location | rely on framework auto-gate | explicit gate in PostProcessNode + framework gate | **both** | The node's gate is the union of both pattern sets and clears the output-bearing fields on a violation; the framework's gate on the node result stays as the floor |
| Personal data in caller documents | framework mask incl. name heuristic | structured shapes only | **structured shapes only** | The Latin name heuristic masks title-case technical titles; document text keeps them and masks addresses, phone numbers, national/card identifiers and labelled Japanese names |
