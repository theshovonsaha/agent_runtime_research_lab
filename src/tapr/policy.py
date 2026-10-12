"""
policy.py – Declarative policy rules with explicit dependency tracking.

Every policy rule declares which world-state facts it reads.  This
declaration is the foundation of TAPR (Typed Action Proof Reuse): the
runtime can safely reuse a cached proof whenever none of the declared
dependencies have changed.

The dependency declaration is a first-class object — not inferred by
watching memory access, but explicitly named in the rule body.  This makes
dependency completeness auditable and testable.

Key types
---------
Dependency      Reference to a specific (name, subject, version, value)
                fact that was used when establishing a proof.
PolicyResult    Verdict plus the set of dependencies that determined it.
PolicyRule      A single testable condition that returns a PolicyResult or None.
Policy          Versioned, ordered list of rules evaluated top-down.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, FrozenSet, List, Optional, Tuple

try:
    from .world import Fact, WorldState, absent_fact
except ImportError:
    from world import Fact, WorldState, absent_fact


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Dependency:
    """A fact version pinned at the time a proof was established.

    A proof is still valid iff every dependency's (version, value) still
    matches the current world state.

    Attributes
    ----------
    name            Fact name, e.g. ``"authorized"``.
    subject         Entity the fact is about.
    pinned_version  The fact's version when the proof was established.
    pinned_value    The fact's value when the proof was established.
    """
    name: str
    subject: str
    pinned_version: int
    pinned_value: Any

    @classmethod
    def from_fact(cls, f: Fact) -> "Dependency":
        return cls(f.name, f.subject, f.version, f.value)

    @classmethod
    def absent(cls, name: str, subject: str) -> "Dependency":
        """Dependency on a fact that was absent (version=0, value=None)."""
        return cls(name, subject, 0, None)

    def still_valid(self, world: WorldState) -> bool:
        """Return True if the pinned (version, value) still matches."""
        f = world.facts.get((self.name, self.subject))
        if f is None:
            return self.pinned_version == 0 and self.pinned_value is None
        return f.version == self.pinned_version and f.value == self.pinned_value

    def cache_key_contribution(self) -> str:
        return json.dumps(
            [self.name, self.subject, self.pinned_version, self.pinned_value],
            sort_keys=True, separators=(",", ":"))


def deps_cache_key(
    deps: FrozenSet[Dependency],
    policy_version: int,
    policy_identity: str = "",
) -> str:
    """Cache key over dependencies, policy version, and policy identity."""
    parts = sorted(d.cache_key_contribution() for d in deps)
    blob = json.dumps(
        {
            "policy_version": policy_version,
            "policy_identity": policy_identity,
            "deps": parts,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Policy result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PolicyResult:
    """Outcome of evaluating a policy against a world state.

    Attributes
    ----------
    verdict         ``"ALLOW"``, ``"DENY"``, or ``"ESCALATE"``.
    dependencies    Facts that determined the verdict.  A sound proof must
                    not be reused when any dependency has changed.
    work_units      Simulated cost of establishing this verdict (proof rounds).
    rule_name       Which rule fired (for audit / debugging).
    """
    verdict: str
    dependencies: FrozenSet[Dependency]
    work_units: int
    rule_name: str
    policy_identity: str = ""

    def is_valid_in(self, world: WorldState) -> bool:
        """Return True iff all dependencies are still satisfied."""
        return all(d.still_valid(world) for d in self.dependencies)

    def cache_key(self, policy_version: int) -> str:
        return deps_cache_key(
            self.dependencies,
            policy_version,
            self.policy_identity,
        )


class DependencyContractError(ValueError):
    """Raised when a result omits a semantic input required for safe reuse."""



# ---------------------------------------------------------------------------
# Policy rules
# ---------------------------------------------------------------------------

def _require_fact(world: WorldState, name: str, subject: str) -> Fact:
    """Return the fact, or an absent-sentinel if missing."""
    f = world.facts.get((name, subject))
    if f is None:
        from world import absent_fact
        return absent_fact(name, subject)
    return f


def _policy_rules() -> List[Any]:
    """
    Return ordered list of rule callables.

    Each rule has signature:
        rule(world, action, actor, target) -> Optional[PolicyResult]

    Rules return None when they do not fire (evaluation continues to the
    next rule).  The first rule that returns a PolicyResult wins.

    Dependency declarations are explicit: each rule reads facts through
    ``_require_fact`` and collects Dependency objects.  This makes the
    dependency set auditable and testable independently of the verdict.
    """

    def r_authorization(world, action, actor, target):
        """Deny if actor is not authorized or parent scope is not OK."""
        auth = _require_fact(world, "authorized", actor)
        scope = _require_fact(world, "parent_scope_ok", actor)
        deps = frozenset([
            Dependency.from_fact(auth),
            Dependency.from_fact(scope),
        ])
        if not auth.value or not scope.value:
            return PolicyResult("DENY", deps, 0, "authorization")
        return None

    def r_protected_resource(world, action, actor, target):
        """Deny destructive actions on protected or retention-held resources."""
        if action not in ("delete", "export"):
            return None
        prot = _require_fact(world, "protected", target)
        hold = _require_fact(world, "retention_hold", target)
        deps = frozenset([Dependency.from_fact(prot), Dependency.from_fact(hold)])
        if prot.value or hold.value:
            return PolicyResult("DENY", deps, 0, "protected_resource")
        return None

    def r_export_after_sensitive_read(world, action, actor, target):
        """Deny export if the session already read a sensitive resource."""
        if action != "export":
            return None
        # Path-dependent: depends on execution history, not just current state.
        # We model this as a special "path_sensitive_read" session fact.
        sens = _require_fact(world, "prior_sensitive_read", actor)
        deps = frozenset([Dependency.from_fact(sens)])
        if sens.value:
            return PolicyResult("DENY", deps, 0, "export_after_sensitive_read")
        return None

    def r_retry_idempotency(world, action, actor, target):
        """Deny retry if the prior attempt already succeeded."""
        if action != "retry":
            return None
        prev = _require_fact(world, "prior_effect_succeeded", target)
        deps = frozenset([Dependency.from_fact(prev)])
        if prev.value:
            return PolicyResult("DENY", deps, 0, "retry_idempotency")
        return None

    def r_untrusted_delegation(world, action, actor, target):
        """Deny delegation if the path tag marks it untrusted."""
        if action != "delegate":
            return None
        tag = _require_fact(world, "path_tag", actor)
        deps = frozenset([Dependency.from_fact(tag)])
        if tag.value == "untrusted_delegation":
            return PolicyResult("DENY", deps, 0, "untrusted_delegation")
        return None

    def r_write_escalation(world, action, actor, target):
        """Escalate write/delete/export when approval or evidence is missing."""
        if action not in ("write", "delete", "export"):
            return None
        appr = _require_fact(world, "approval", actor)
        evid = _require_fact(world, "evidence_fresh", target)
        deps = frozenset([Dependency.from_fact(appr), Dependency.from_fact(evid)])
        if not appr.value or not evid.value:
            return PolicyResult("ESCALATE", deps, 0, "write_escalation")
        return None

    def r_allow(world, action, actor, target):
        """Default allow, pinning only conditions relevant to this action."""
        deps = frozenset(
            Dependency.from_fact(_require_fact(world, name, subject))
            for name, subject in Policy.required_fact_keys(
                action, actor, target, "allow"
            )
        )
        return PolicyResult("ALLOW", deps, 0, "allow")

    return [
        r_authorization,
        r_protected_resource,
        r_export_after_sensitive_read,
        r_retry_idempotency,
        r_untrusted_delegation,
        r_write_escalation,
        r_allow,
    ]


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

class Policy:
    """Versioned, ordered collection of rules evaluated top-down.

    The policy is the *semantic contract* for the benchmark.  The oracle
    and the evaluator are separate implementations of the same contract
    (see below).  Dependency completeness can be tested by verifying that
    every fact mutation that changes the verdict also changes the cache key.
    """

    def __init__(self, version: int = 1, proof_rounds: int = 0):
        self.version = version
        self.proof_rounds = proof_rounds
        self._rules = _policy_rules()

    @property
    def identity(self) -> str:
        """Identity of this active in-process rule set.

        This detects replacement of the rule list or its callable objects.
        It is intentionally process-local, not a portable semantic digest.
        """
        payload = {
            "version": self.version,
            "rules_list_id": id(self._rules),
            "rule_ids": [id(rule) for rule in self._rules],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()

    @staticmethod
    def required_fact_keys(
        action: str, actor: str, target: str, rule_name: str
    ) -> FrozenSet[Tuple[str, str]]:
        """Return facts that must be pinned for the fired policy branch.

        The branch dependencies include the conditions that produced a
        denial/escalation. An allow pins every condition that could change
        that action's verdict, but excludes facts irrelevant to that action.
        """
        branches = {
            "authorization": {("authorized", actor), ("parent_scope_ok", actor)},
            "protected_resource": {("protected", target), ("retention_hold", target)},
            "export_after_sensitive_read": {("prior_sensitive_read", actor)},
            "retry_idempotency": {("prior_effect_succeeded", target)},
            "untrusted_delegation": {("path_tag", actor)},
            "write_escalation": {("approval", actor), ("evidence_fresh", target)},
        }
        if rule_name != "allow":
            return frozenset(branches.get(rule_name, {
                ("authorized", actor), ("parent_scope_ok", actor)
            }))

        keys = {("authorized", actor), ("parent_scope_ok", actor)}
        if action in {"delete", "export"}:
            keys.update({("protected", target), ("retention_hold", target)})
        if action == "export":
            keys.add(("prior_sensitive_read", actor))
        if action == "retry":
            keys.add(("prior_effect_succeeded", target))
        if action == "delegate":
            keys.add(("path_tag", actor))
        if action in {"write", "delete", "export"}:
            keys.update({("approval", actor), ("evidence_fresh", target)})
        return frozenset(keys)

    def validate_dependencies(
        self, result: PolicyResult, action: str, actor: str, target: str
    ) -> None:
        required = self.required_fact_keys(
            action, actor, target, result.rule_name
        )
        declared = {(d.name, d.subject) for d in result.dependencies}
        missing = required - declared
        if missing:
            details = ", ".join(f"{name}:{subject}" for name, subject in sorted(missing))
            raise DependencyContractError(
                f"Policy result {result.rule_name!r} omitted required dependencies: "
                f"{details}"
            )

    def evaluate(self, world: WorldState, action: str, actor: str,
                 target: str) -> PolicyResult:
        """Evaluate policy top-down; accumulate dependencies from all fired rules."""
        all_deps: set[Dependency] = set()
        work = 0

        # Simulate proof-construction cost (deterministic CPU work).
        if self.proof_rounds > 0:
            import hashlib as _hl
            h = json.dumps(
                {"action": action, "actor": actor, "target": target,
                 "wv": world.world_version},
                sort_keys=True).encode()
            for i in range(self.proof_rounds):
                h = _hl.sha256(h + i.to_bytes(4, "little")).digest()
            work = self.proof_rounds

        for rule in self._rules:
            result = rule(world, action, actor, target)
            if result is not None:
                all_deps.update(result.dependencies)
                return PolicyResult(
                    result.verdict,
                    frozenset(all_deps),
                    work,
                    result.rule_name,
                    self.identity,
                )
        # Should never reach here (r_allow always fires).
        raise RuntimeError("Policy exhausted without verdict")


# ---------------------------------------------------------------------------
# Reference oracle (separate implementation of the same policy contract)
# ---------------------------------------------------------------------------

def reference_oracle(world: WorldState, action: str, actor: str,
                     target: str) -> str:
    """Conservative reference implementation.

    Deliberately written without using the Policy class or _policy_rules(),
    to serve as an independent oracle for correctness testing.  If oracle
    and Policy ever disagree, one of them has a bug or they have drifted.
    """
    def g(name: str, subject: str, default: Any = None) -> Any:
        return world.get(name, subject, default)

    if not g("authorized", actor, False) or not g("parent_scope_ok", actor, True):
        return "DENY"
    if action in ("delete", "export") and (
            g("protected", target, False) or g("retention_hold", target, False)):
        return "DENY"
    if action == "export" and g("prior_sensitive_read", actor, False):
        return "DENY"
    if action == "retry" and g("prior_effect_succeeded", target, False):
        return "DENY"
    if action == "delegate" and g("path_tag", actor, "ordinary") == "untrusted_delegation":
        return "DENY"
    if action in ("write", "delete", "export") and (
            not g("approval", actor, True) or not g("evidence_fresh", target, True)):
        return "ESCALATE"
    return "ALLOW"
