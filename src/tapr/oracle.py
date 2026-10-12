"""Independent truth evaluator for the synthetic governance contract.

This module intentionally does not import policy rules, proofs, or cache
validation code. It evaluates the contract directly from the current world
snapshot and is used only as a measurement oracle.
"""
from __future__ import annotations

from dataclasses import dataclass

from .world import WorldState


@dataclass(frozen=True)
class OracleResult:
    proposal_verdict: str
    commit_authorized: bool


def evaluate(world: WorldState, action: str, actor: str,
             target: str) -> OracleResult:
    """Recompute expected proposal and commit authorization from current state."""
    get = world.get

    if not get("authorized", actor, False):
        verdict = "DENY"
    elif not get("parent_scope_ok", actor, True):
        verdict = "DENY"
    elif action in {"delete", "export"} and get("protected", target, False):
        verdict = "DENY"
    elif action in {"delete", "export"} and get("retention_hold", target, False):
        verdict = "DENY"
    elif action == "export" and get("prior_sensitive_read", actor, False):
        verdict = "DENY"
    elif action == "retry" and get("prior_effect_succeeded", target, False):
        verdict = "DENY"
    elif (action == "delegate"
          and get("path_tag", actor, "ordinary") == "untrusted_delegation"):
        verdict = "DENY"
    elif (action in {"write", "delete", "export"}
          and (not get("approval", actor, True)
               or not get("evidence_fresh", target, True))):
        verdict = "ESCALATE"
    else:
        verdict = "ALLOW"

    return OracleResult(verdict, verdict == "ALLOW")
