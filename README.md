# Engineering Research to Technical Report Agent

AI agent for turning manufacturing engineering research into sourced technical reports, built with Agentic Star.

> **Category**: Cat 2 (a domain-specific pipeline for one job-to-be-done)
> **Industry**: Manufacturing
> **Template ID**: MFG-C2-063

## Overview

Manufacturing engineers who need a sourced technical write-up on a topic —
predictive maintenance for a machining line, statistical process control for
a yield ramp, the tolerance standard a new part must meet — usually assemble
it by hand from papers, patents and standards.

This agent takes a research question together with the research materials to
draw on, ranks the materials against the question, synthesizes them into key
findings and methodology notes, and returns a **Japanese technical report**
with five fixed sections (概要 / 主要知見 / 技術的考察 / 製造現場への推奨事項 /
参考文献) and a citation for every source used. The shipped pipeline is
deterministic and runs offline; the synthesis and drafting steps are marked
seams where a language model can be wired in.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails at graph
compile / start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Sending a request

The research question goes in `input`; the documents to draw on go in
`input_context.research_materials`. The platform rewrites personal-data
shapes out of the request string at every node boundary, and its name
heuristic reads title-case phrases such as *Statistical Process Control* as
personal names — so document titles and text belong in `input_context`, which
is not rewritten.

```json
{
  "input": "Survey CNC predictive maintenance and SPC yield methods for the shop floor.",
  "input_context": {
    "report_requirements": {"title": "CNC予知保全とSPC歩留まり改善 技術調査レポート"},
    "research_materials": [
      {
        "title": "Vibration-based predictive maintenance for CNC spindles",
        "url": "https://papers.example.org/cnc-spindle-pdm",
        "content": "Spindle vibration signatures predict bearing wear 40 hours before failure.",
        "type": "paper",
        "language": "en"
      }
    ]
  }
}
```

Material types and languages are closed sets; every text field is bounded and
rendered onto a single line; `relevance_score`, when supplied, must be a finite
number between 0 and 1. A refused request names the field that failed and never
repeats the value. A request with no materials is answered from a small
built-in reference corpus. The design (`docs/02_design.md`) carries the full
contract and the output invariant the report always satisfies.

## Project Structure

```
src/          agent implementation (graphs, nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent manifest and runtime parameters
docs/         design and test specification
```

`docs/` holds the design (`02_design.md`) and the test specification
(`03_test_spec.md`).

## Customising

1. Adjust `config/config.yaml` — `research_report.max_materials` caps how many
   sources are synthesized and cited; `research_report.inline_documents`
   supplies documents ranked on every request.
2. Wire your retrieval source and language model at the marked seams in
   `src/nodes/research_ingestion_node.py`, `research_synthesis_node.py` and
   `technical_report_generation_node.py`, and declare their secrets in
   `config/agent.yaml`.
3. Review the caller contract in `src/services/service.py` if your materials
   carry other fields.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
