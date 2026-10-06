"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only -- no business logic here.
# On the hosted platform the gateway calls agent.invoke() directly instead.

import json
import os
import re
import secrets
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from framework.security.credential_detector import detect_credentials_in_value
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel
from shared.secrets import factory as secrets_factory
from shared.utils.audit_logger import emit_trace_event

from src.graph.graph import ManufacturingEngineeringResearchToTechnicalReportAgent, runtime_config

app = FastAPI(title="Agent")

# The registry loads config/config.yaml and passes it as Graph(config=...);
# this standalone server mirrors that exactly, so the declared runtime
# parameters are live in both deployments instead of only one.
agent = ManufacturingEngineeringResearchToTechnicalReportAgent(config=runtime_config())

# Mirror the registry's conditional checkpointer -- hitl.enabled or
# memory_enabled needs one, or interrupt()/memory silently no-ops on this path.
_hitl_enabled = bool(agent.config.get("hitl", {}).get("enabled", False))
_needs_checkpointer = bool(agent.config.get("memory_enabled")) or _hitl_enabled
agent.compile(checkpointer=MemorySaver() if _needs_checkpointer else None)

# The secret provider is scoped by the identity the manifest declares, so a
# secret resolves from the same place here and under the registry. The provider
# reads `env/namespaces/{namespace}/...` and `env/agents/{namespace}/{name}/...`
# (shared/secrets/dotenv_provider.py), so a namespace that disagrees with
# config/agent.yaml silently splits one agent's secrets across two stores.
# `namespace` is lower(industry) -- "mfg" -- not the lowercased Template ID.
# tests/integration/test_manifest_identity_alignment.py holds these two values
# to the manifest; it reads both sides rather than restating them.
agent.provision_secrets(
    secrets_factory(namespace="mfg", agent_name="ManufacturingEngineeringResearchToTechnicalReportAgent")
)

# Upper bound on the serialized structured parameters (bytes). The graph
# enforces per-field bounds -- entry caps, text caps, closed vocabularies,
# finite numeric ranges; this is the coarse guard that keeps an oversized
# payload from reaching the graph at all.
_MAX_INPUT_CONTEXT_BYTES = 262_144

# The structured parameters this agent accepts. Anything else at the top level
# is refused here, so a misspelt field is an error the caller can act on
# rather than a silently ignored one.
_ACCEPTED_CONTEXT_FIELDS = ("channel", "report_requirements", "research_materials")

# Caller identifiers are anchored with \A and \Z, not ^ and $: in Python `$`
# also matches just before a trailing newline, so "abc\n" would satisfy a
# `^...$` pattern and the newline would travel into the audit record and the
# error message. \Z has no such exception.
_SESSION_ID_RE = re.compile(r"\A[A-Za-z0-9_.-]{1,64}\Z")
_SAFE_FIELD_NAME_RE = re.compile(r"\A[A-Za-z0-9_.-]{1,64}\Z")


def _field_reference(name: object, index: int) -> str:
    """Render a caller-supplied field name safe to put in a message."""
    if isinstance(name, str) and _SAFE_FIELD_NAME_RE.fullmatch(name) and not detect_credentials_in_value(name):
        return f"input_context.{name}"
    return f"input_context field #{index}"


def unknown_context_field(input_context: Dict[str, Any]) -> Optional[str]:
    """Return a reference to the first top-level field outside the contract, else None."""
    for index, name in enumerate(input_context, start=1):
        if name not in _ACCEPTED_CONTEXT_FIELDS:
            return _field_reference(name, index)
    return None


# ── Structured-parameter credential screen ───────────────────────────────────
# Why this runs before invoke() rather than inside a node:
#
# The framework's mandatory output gate scans every value of every node result
# for credential patterns, and the backbone's first node copies the structured
# parameters verbatim into its own result. So a credential-shaped string
# anywhere in them makes the FIRST node of the graph fail, before any template
# code runs. What the caller receives is an error status with the domain result
# withheld and no explanation -- nothing names the parameter, the field, or the
# reason. On a hosted conversation the same context is replayed every turn, so
# the session never recovers on its own.
#
# The request cannot succeed either way. Refusing it here changes nothing about
# what is accepted; it changes an opaque failure into an actionable one.
#
# The screen calls the SAME detector the gate calls, on the SAME assembled
# object, so what this adapter refuses and what the gate blocks are one set by
# construction. Scanning field by field composes exactly to scanning the whole
# mapping (the detector on a mapping is the union over its values), which is
# what lets the refusal name the offending field without widening or narrowing
# the match. The rejected value and the matched text are never echoed.


def screen_input_context(input_context: Dict[str, Any]) -> Optional[str]:
    """Return a reference to the first credential-bearing field, else None."""
    for index, (name, value) in enumerate(input_context.items(), start=1):
        if detect_credentials_in_value(value):
            return _field_reference(name, index)
    return None


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""
    # Structured invocation parameters: the research materials to draw on, the
    # report requirements and the channel -- validated field by field inside
    # the graph. This is the channel documents belong on: the platform rewrites
    # personal-data shapes out of `input` at every node boundary, and its name
    # heuristic reads title-case technical titles as personal names, so a
    # document title arrives masked there and intact here.
    input_context: Optional[Dict[str, Any]] = None


def _bearer_matches(supplied: str, expected: str) -> bool:
    """Constant-time bearer comparison that is safe for non-ASCII header input."""
    return secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode())


def _resolve_standalone_trust(
    current: TrustLevel,
    authorization: str,
    invoke_auth_token: Optional[str],
    internal_runner_token: Optional[str],
) -> TrustLevel:
    """Authenticate standalone callers without allowing external-token elevation.

    This adapter is the entry-point auth boundary (the standalone equivalent of
    the platform's authentication middleware). Both values are deployment-level
    caller credentials, not agent secrets: no invocation context exists before
    this boundary, so the secrets provider cannot apply.

    STG_INTERNAL_RUNNER_TOKEN is a distinct deployment credential considered
    only for an anonymous caller and mapping exactly to INTERNAL;
    INVOKE_AUTH_TOKEN maps to VERIFIED_EXTERNAL -- the level every node of this
    agent declares, and the reason this boundary is required: nothing else sets
    request.state.trust_level in a standalone deployment, so without it every
    request would arrive ANONYMOUS and the trust gate would deny it.
    Middleware-established trust is never changed.
    """
    if current is not TrustLevel.ANONYMOUS:
        return current
    if internal_runner_token and _bearer_matches(authorization, internal_runner_token):
        return TrustLevel.INTERNAL
    if invoke_auth_token and _bearer_matches(authorization, invoke_auth_token):
        return TrustLevel.VERIFIED_EXTERNAL
    if internal_runner_token or invoke_auth_token:
        # Generic body on purpose -- do not leak whether the token was absent,
        # malformed, or wrong.
        raise HTTPException(status_code=401, detail="Token is invalid or expired.")
    return TrustLevel.ANONYMOUS


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> Any:
    trust = _resolve_standalone_trust(
        getattr(request.state, "trust_level", TrustLevel.ANONYMOUS),
        request.headers.get("authorization", ""),
        os.environ.get("INVOKE_AUTH_TOKEN"),
        os.environ.get("STG_INTERNAL_RUNNER_TOKEN"),
    )

    if req.session_id and not _SESSION_ID_RE.fullmatch(req.session_id):
        raise HTTPException(status_code=400, detail="session_id must match [A-Za-z0-9_.-]{1,64}.")

    input_context = req.input_context or {}
    if input_context and len(json.dumps(input_context, default=str)) > _MAX_INPUT_CONTEXT_BYTES:
        raise HTTPException(status_code=413, detail="input_context exceeds the maximum allowed size.")
    unknown = unknown_context_field(input_context)
    if unknown is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{unknown} is not part of this agent's contract " f"(accepted: {', '.join(_ACCEPTED_CONTEXT_FIELDS)})."
            ),
        )
    offending_field = screen_input_context(input_context)
    if offending_field is not None:
        emit_trace_event(
            "input_context_credential_refused",
            {"field": offending_field},
            {"session_id": req.session_id},
        )
        # 400, not 422: pydantic owns 422 and answers there with a list of
        # error objects, so reusing it would make client handling ambiguous.
        raise HTTPException(
            status_code=400,
            detail=(
                f"{offending_field} contains a credential-shaped value. Remove API keys, "
                "tokens and connection strings from input_context and retry."
            ),
        )

    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        return agent.invoke(req.input, ctx=ctx, input_context=input_context)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "agent": "ManufacturingEngineeringResearchToTechnicalReportAgent"}
