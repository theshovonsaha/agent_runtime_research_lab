"""
proof.py – Proof objects, proof store, and check/commit gate.

A *proof* is a first-class object representing a governance decision and its
justification.  It is not just a cached verdict — it carries the dependency
set that the verdict rested on, the policy version in force at the time, and
a validity bound (the world version through which the runtime is confident
the proof is still sound).

The check/commit gate re-validates all dependencies immediately before an
effect is committed to the world.  This prevents TOCTOU (time-of-check /
time-of-use) vulnerabilities: a proof established while authorization was
valid cannot be silently reused after authorization was revoked.

Key types
---------
Proof           Verdict + dependency set + validity metadata.
ProofStore      In-memory cache keyed by dependency fingerprint.
CheckCommitGate Validates a proof at the effect boundary.
StaleProofAcceptor  Unsafe negative control: never re-validates.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from policy import Dependency, PolicyResult, deps_cache_key
from world import WorldState


# ---------------------------------------------------------------------------
# Proof
# ---------------------------------------------------------------------------

@dataclass
class Proof:
    """A governance decision with its full justification.

    Attributes
    ----------
    proof_id            Unique identifier for audit / tracing.
    verdict             ``"ALLOW"``, ``"DENY"``, or ``"ESCALATE"``.
    action              The action that was evaluated.
    actor               The agent that proposed the action.
    target              The resource/entity targeted.
    dependencies        Facts (name, subject, version, value) used to reach
                        the verdict.  Changing any of these invalidates this
                        proof.
    policy_version      Policy version in force when the proof was made.
    established_at      World version when the proof was established.
    valid_through       Optional upper bound on world version through which
                        reuse is safe.  None means no bound was declared.
    work_units          Simulated proof-construction cost (proof rounds).
    rule_name           Which policy rule fired last.
    established_ns      Wall-clock nanosecond timestamp (for latency tracking).
    """
    proof_id: str
    verdict: str
    action: str
    actor: str
    target: str
    dependencies: FrozenSet[Dependency]
    policy_version: int
    established_at: int         # world_version at establishment
    valid_through: Optional[int]  # world_version upper bound (None = unbounded)
    work_units: int
    rule_name: str
    established_ns: int = field(default_factory=time.perf_counter_ns)
    policy_identity: str = ""

    def cache_key(self) -> str:
        return deps_cache_key(
            self.dependencies, self.policy_version, self.policy_identity
        )

    def is_valid_in(self, world: WorldState) -> bool:
        """True iff all dependencies still match and the world version is in range."""
        if (self.valid_through is not None
                and world.world_version > self.valid_through):
            return False
        if self.policy_version != world.policy_version:
            return False
        return all(d.still_valid(world) for d in self.dependencies)

    @classmethod
    def from_result(cls, result: PolicyResult, proof_id: str,
                    action: str, actor: str, target: str,
                    world: WorldState,
                    valid_through: Optional[int] = None) -> "Proof":
        return cls(
            proof_id=proof_id,
            verdict=result.verdict,
            action=action,
            actor=actor,
            target=target,
            dependencies=result.dependencies,
            policy_version=world.policy_version,
            established_at=world.world_version,
            valid_through=valid_through,
            work_units=result.work_units,
            rule_name=result.rule_name,
            policy_identity=result.policy_identity,
        )


# ---------------------------------------------------------------------------
# ProofStore
# ---------------------------------------------------------------------------

@dataclass
class ProofStoreStats:
    hits: int = 0
    misses: int = 0
    invalidations: int = 0

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0


class ProofStore:
    """In-memory cache keyed by dependency fingerprint + policy version.

    This is the TAPR cache.  The key is the hash of the declared dependency
    set plus the policy version — not the full world state.  Irrelevant
    metadata changes therefore do not invalidate cached proofs.

    On a cache hit, the store re-validates all dependencies against the
    *current* world state before returning the cached proof.  If any
    dependency has changed, the entry is evicted and a miss is reported.
    """

    def __init__(self):
        self._store: Dict[str, Proof] = {}
        self.stats = ProofStoreStats()

    def lookup(self, world: WorldState, action: str, actor: str,
               target: str) -> Optional[Proof]:
        """Return a valid cached proof, or None (cache miss).

        A cached proof is only returned if:
        1. The dependency-set cache key exists in the store.
        2. All dependencies are still valid in ``world``.
        3. The proof's validity bound has not been exceeded.
        4. The active policy version matches.

        Any violation results in eviction and a miss.
        """
        # We need to find an entry whose key matches the current dependency vector.
        # Since we don't know the key without evaluating, we look up by a
        # *prospective* key: this requires the caller to supply a key hint, OR
        # we scan entries for a matching action/actor/target that is still valid.
        #
        # Design note: In a production system you would compute the dependency
        # fingerprint *before* evaluation using a static dependency declaration.
        # Here we iterate valid entries (bounded by number of distinct actions).
        # The O(n) scan is acceptable for the benchmark's entry counts.
        for key, proof in list(self._store.items()):
            if (proof.action == action and proof.actor == actor
                    and proof.target == target):
                if proof.is_valid_in(world):
                    self.stats.hits += 1
                    return proof
                else:
                    # Evict stale entry.
                    del self._store[key]
                    self.stats.invalidations += 1
        self.stats.misses += 1
        return None

    def store(self, proof: Proof) -> None:
        self._store[proof.cache_key()] = proof

    def invalidate_all(self) -> None:
        self._store.clear()

    def size(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# Exact-snapshot cache (for comparison)
# ---------------------------------------------------------------------------

class ExactSnapshotStore:
    """Cache keyed by the full world-state fingerprint.

    Correctness: always sound (no irrelevant metadata can alias).
    Efficiency: low hit rate when irrelevant metadata changes frequently.
    """

    def __init__(self):
        self._store: Dict[str, Tuple[str, int]] = {}  # key -> (verdict, work_units)
        self.stats = ProofStoreStats()

    def lookup(self, world: WorldState, action: str, actor: str,
               target: str) -> Optional[Tuple[str, int]]:
        key = world.fingerprint_all() + action + actor + target
        if key in self._store:
            self.stats.hits += 1
            return self._store[key]
        self.stats.misses += 1
        return None

    def store(self, world: WorldState, action: str, actor: str,
              target: str, verdict: str, work_units: int) -> None:
        key = world.fingerprint_all() + action + actor + target
        self._store[key] = (verdict, work_units)


# ---------------------------------------------------------------------------
# Coarse cache (negative control)
# ---------------------------------------------------------------------------

class CoarseCache:
    """Cache keyed by action + target only (intentionally unsafe).

    This is the *negative-control* coarse cache.  It will serve stale
    verdicts whenever the same action/target pair recurs but the authorization
    or evidence has changed.

    Expected behaviour: very high cache-hit rate, but incorrect decisions
    when authorization is revoked or evidence goes stale.
    """

    def __init__(self):
        self._store: Dict[str, str] = {}
        self.stats = ProofStoreStats()

    def lookup(self, action: str, target: str) -> Optional[str]:
        key = f"{action}:{target}"
        if key in self._store:
            self.stats.hits += 1
            return self._store[key]
        self.stats.misses += 1
        return None

    def store(self, action: str, target: str, verdict: str) -> None:
        self._store[f"{action}:{target}"] = verdict


# ---------------------------------------------------------------------------
# Check/commit gate
# ---------------------------------------------------------------------------

@dataclass
class CommitDecision:
    """Result of the check/commit gate evaluation."""
    approved: bool
    proof: Optional[Proof]
    reason: str  # "proof_valid", "proof_stale", "recomputed"
    work_units: int


class CheckCommitGate:
    """Validates a proof at the effect boundary before committing.

    The gate re-verifies that every dependency still holds at the moment
    the effect is about to be committed.  This prevents TOCTOU: an ALLOW
    decision made when authorization was valid cannot be silently reused
    after authorization was revoked.

    In a real system this would be an atomic compare-and-swap or
    optimistic-lock check.  Here it is a deterministic in-process check.
    """

    def check_and_commit(self, proof: Proof, current_world: WorldState,
                         policy: "Policy") -> CommitDecision:
        """Validate proof against current_world.

        If the proof is still valid, return approved=True.
        If any dependency has changed, recompute and return the fresh verdict.
        """
        if (proof.policy_identity == policy.identity
                and proof.is_valid_in(current_world)):
            return CommitDecision(
                approved=(proof.verdict == "ALLOW"),
                proof=proof,
                reason="proof_valid",
                work_units=0,
            )
        # Recompute.
        result = policy.evaluate(current_world, proof.action, proof.actor,
                                 proof.target)
        new_proof = Proof.from_result(
            result, proof.proof_id + "_recheck",
            proof.action, proof.actor, proof.target,
            current_world)
        return CommitDecision(
            approved=(new_proof.verdict == "ALLOW"),
            proof=new_proof,
            reason="recomputed",
            work_units=result.work_units,
        )


class NoCommitGate:
    """Unsafe negative control: skips the check/commit validation.

    Simulates a runtime that never re-validates at the effect boundary.
    Expected behaviour: stale proofs will be accepted after state changes.
    """

    def check_and_commit(self, proof: Proof, current_world: WorldState,
                         policy: "Policy") -> CommitDecision:
        return CommitDecision(
            approved=(proof.verdict == "ALLOW"),
            proof=proof,
            reason="no_gate_accepted",
            work_units=0,
        )
