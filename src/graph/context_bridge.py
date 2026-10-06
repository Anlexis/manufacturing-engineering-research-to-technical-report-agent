"""AgentCore Platform v1.0"""

# MFG-C2-063 -- carries the validated caller contract across the outer/inner
# graph boundary.
#
# The framework invokes a nested graph as
# `subgraph.invoke(user_input, session_id=..., ctx=...)`: only the request
# STRING crosses. Neither the outer state nor the caller's structured
# invocation parameters (`input_context`) are forwarded, so the research
# materials and report requirements the pre_process node validated would never
# reach the domain pipeline -- an inner node reading `state["input_context"]`
# sees an empty mapping on every real invocation, while a unit test that seeds
# the inner state directly keeps passing.
#
# Two sanctioned subclass hooks bridge it without touching the framework:
#
#   DomainWorkflowGraphNode.extract_input(state)   [runs BEFORE subgraph.invoke]
#       -> set_caller_contract(<validated contract>)
#   DomainWorkflowGraph._extra_initial_state()     [runs INSIDE subgraph.invoke]
#       -> seeds the contract into the inner state, consuming it
#
# Only the VALIDATED contract crosses: every field has already passed its
# bounded shape check, every number its finite-range check, every free-text
# field its injection and credential screens. The raw request body never
# travels.
#
# A ContextVar rather than a module global: it is per-thread and per-task, so
# two invocations running concurrently in one process cannot read each other's
# contract. The inner graph consumes the value when it reads it, so a later
# direct inner invoke in the same task starts from an empty contract rather
# than a stale one.

from contextvars import ContextVar
from typing import Any, Dict, Optional

_CALLER_CONTRACT: ContextVar[Optional[Dict[str, Any]]] = ContextVar("mfg_c2_063_caller_contract", default=None)


def set_caller_contract(contract: Optional[Dict[str, Any]]) -> None:
    """Stash the validated caller contract for the imminent inner-graph invoke."""
    _CALLER_CONTRACT.set(dict(contract) if contract else {})


def take_caller_contract() -> Dict[str, Any]:
    """Read AND clear the stashed contract; {} when none was set."""
    contract = _CALLER_CONTRACT.get() or {}
    _CALLER_CONTRACT.set(None)
    return contract
