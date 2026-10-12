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
    policy_version_matches: bool


def evaluate(world: WorldState, action: str, actor: str,
             target: str, *,
             active_policy_version: int | None = None) -> OracleResult:
    """Recompute verdicts from state and the active policy version.

    An absent active version means the snapshot's version is active, which is
    useful for proposal-only checks. Commit adjudication should pass the
    runtime's active version so mismatched snapshots are fail-closed.
    """
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

    active_version = (
        world.policy_version
        if active_policy_version is None else active_policy_version
    )
    version_matches = world.policy_version == active_version
    return OracleResult(
        verdict,
        verdict == "ALLOW" and version_matches,
        version_matches,
    )
