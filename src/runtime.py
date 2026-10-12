"""
runtime.py – Governance runtime: six decision modes for comparison.

The runtime wraps a Policy and one of several caching/proof-reuse strategies.
Each mode is self-contained and produces the same output shape, making
cross-mode comparison straightforward.

Runtime modes
-------------
full_recompute          Always evaluate from scratch.  Zero false reuse;
                        maximum work.  The safety baseline.

tapr_dep_cache          TAPR: cache keyed by explicit dependency fingerprint.
                        Reuses proofs when no dependency has changed; evicts
                        when any dependency changes.  Validates at commit gate.

exact_snapshot_cache    Cache keyed by full world-state fingerprint.  Sound
                        but low hit rate when irrelevant metadata changes.

coarse_cache            Cache keyed by action + target only.  Unsafe: ignores
                        authorization and evidence.  Negative control.

stale_proof_accept      TAPR store but skips dependency re-validation on
                        lookup.  Unsafe: serves stale proofs.  Negative
                        control showing what happens without invalidation.

no_commit_gate          TAPR store with valid invalidation, but commits
                        without re-checking.  Unsafe negative control for
                        TOCTOU: catches stale-proof-at-commit failures.

pathless_policy         Full recompute but path-history facts are zeroed out.
                        Unsafe: cannot enforce path-dependent rules.

Each mode's ``decide()`` returns a DecisionRecord capturing verdict, proof,
cache-hit status, latency, and work units.  After a proposal, if the scenario
calls for a commit attempt, ``commit()`` runs the gate check.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from policy import Dependency, Policy, PolicyResult, reference_oracle
from proof import (CheckCommitGate, CoarseCache, CommitDecision,
                   ExactSnapshotStore, NoCommitGate, Proof, ProofStore)
from world import Fact, WorldState


# ---------------------------------------------------------------------------
# Decision record
# ---------------------------------------------------------------------------

@dataclass
class DecisionRecord:
    """Full record of a single governance decision."""
    scenario_id:    str
    step_index:     int
    system:         str
    action:         str
    actor:          str
    target:         str
    family:         str

    verdict:        str          # ALLOW / DENY / ESCALATE
    expected:       str          # from reference oracle
    correct:        bool

    cache_hit:      bool
    work_units:     int          # proof-construction rounds expended
    latency_ns:     int          # wall-clock ns for decide()

    rule_name:      str          # which rule fired
    dep_count:      int          # number of tracked dependencies
    world_version:  int

    # Commit gate results (populated when CommitAttemptEvent is processed)
    commit_approved:        Optional[bool]  = None
    commit_reason:          Optional[str]   = None
    commit_work_units:      Optional[int]   = None
    commit_expected_approved: Optional[bool] = None
    commit_correct:         Optional[bool]  = None

    @property
    def unsafe_allow(self) -> bool:
        """True if we said ALLOW but oracle says DENY/ESCALATE."""
        return self.verdict == "ALLOW" and self.expected != "ALLOW"

    @property
    def false_deny(self) -> bool:
        """True if we said DENY but oracle says ALLOW/ESCALATE."""
        return self.verdict == "DENY" and self.expected != "DENY"

    @property
    def stale_commit_accept(self) -> bool:
        """Commit gate approved a proof that should have been denied."""
        return (self.commit_approved is True
                and self.commit_expected_approved is False)


# ---------------------------------------------------------------------------
# Base runtime class
# ---------------------------------------------------------------------------

class RuntimeBase:
    """Base class for all runtime modes."""

    name: str = "base"

    def __init__(self, policy: Policy):
        self.policy = policy
        self._records: List[DecisionRecord] = []

    def decide(self, world: WorldState, action: str, actor: str,
               target: str, scenario_id: str = "", step: int = 0,
               family: str = "") -> DecisionRecord:
        raise NotImplementedError

    def commit(self, record: DecisionRecord, proof: Optional[Proof],
               world: WorldState) -> DecisionRecord:
        raise NotImplementedError

    def reset(self) -> None:
        """Reset per-session state (caches, proof stores)."""

    @property
    def records(self) -> List[DecisionRecord]:
        return self._records


# ---------------------------------------------------------------------------
# Mode 1: Full recompute (safety baseline)
# ---------------------------------------------------------------------------

class FullRecomputeRuntime(RuntimeBase):
    name = "full_recompute"

    def decide(self, world, action, actor, target,
               scenario_id="", step=0, family="") -> DecisionRecord:
        t0 = time.perf_counter_ns()
        result = self.policy.evaluate(world, action, actor, target)
        lat = time.perf_counter_ns() - t0
        expected = reference_oracle(world, action, actor, target)
        rec = DecisionRecord(
            scenario_id=scenario_id, step_index=step, system=self.name,
            action=action, actor=actor, target=target, family=family,
            verdict=result.verdict, expected=expected,
            correct=(result.verdict == expected),
            cache_hit=False, work_units=result.work_units, latency_ns=lat,
            rule_name=result.rule_name, dep_count=len(result.dependencies),
            world_version=world.world_version,
        )
        self._records.append(rec)
        return rec

    def commit(self, record, proof, world):
        # Full recompute has no caching, so commit just re-evaluates.
        result = self.policy.evaluate(world, record.action, record.actor,
                                      record.target)
        approved = result.verdict == "ALLOW"
        record.commit_approved = approved
        record.commit_reason = "recomputed"
        record.commit_work_units = result.work_units
        return record


# ---------------------------------------------------------------------------
# Mode 2: TAPR dependency cache (main hypothesis)
# ---------------------------------------------------------------------------

class TAPRRuntime(RuntimeBase):
    name = "tapr_dep_cache"

    def __init__(self, policy: Policy):
        super().__init__(policy)
        self._store = ProofStore()
        self._gate  = CheckCommitGate()
        self._proof_map: Dict[str, Proof] = {}  # step key -> Proof

    def reset(self):
        self._store = ProofStore()
        self._proof_map = {}

    def decide(self, world, action, actor, target,
               scenario_id="", step=0, family="") -> DecisionRecord:
        t0 = time.perf_counter_ns()
        cached = self._store.lookup(world, action, actor, target)
        if cached is not None:
            lat = time.perf_counter_ns() - t0
            expected = reference_oracle(world, action, actor, target)
            rec = DecisionRecord(
                scenario_id=scenario_id, step_index=step, system=self.name,
                action=action, actor=actor, target=target, family=family,
                verdict=cached.verdict, expected=expected,
                correct=(cached.verdict == expected),
                cache_hit=True, work_units=0, latency_ns=lat,
                rule_name=cached.rule_name,
                dep_count=len(cached.dependencies),
                world_version=world.world_version,
            )
            self._proof_map[f"{scenario_id}-{step}"] = cached
            self._records.append(rec)
            return rec

        # Cache miss: evaluate and store.
        result = self.policy.evaluate(world, action, actor, target)
        lat = time.perf_counter_ns() - t0
        proof = Proof.from_result(result, str(uuid.uuid4())[:8],
                                  action, actor, target, world)
        self._store.store(proof)
        self._proof_map[f"{scenario_id}-{step}"] = proof

        expected = reference_oracle(world, action, actor, target)
        rec = DecisionRecord(
            scenario_id=scenario_id, step_index=step, system=self.name,
            action=action, actor=actor, target=target, family=family,
            verdict=result.verdict, expected=expected,
            correct=(result.verdict == expected),
            cache_hit=False, work_units=result.work_units, latency_ns=lat,
            rule_name=result.rule_name, dep_count=len(result.dependencies),
            world_version=world.world_version,
        )
        self._records.append(rec)
        return rec

    def commit(self, record, proof, world):
        key = f"{record.scenario_id}-{record.step_index}"
        stored_proof = self._proof_map.get(key)
        if stored_proof is None:
            record.commit_approved = False
            record.commit_reason = "no_proof"
            record.commit_work_units = 0
            return record
        decision = self._gate.check_and_commit(stored_proof, world, self.policy)
        record.commit_approved = decision.approved
        record.commit_reason = decision.reason
        record.commit_work_units = decision.work_units
        return record


# ---------------------------------------------------------------------------
# Mode 3: Exact snapshot cache
# ---------------------------------------------------------------------------

class ExactSnapshotRuntime(RuntimeBase):
    name = "exact_snapshot_cache"

    def __init__(self, policy: Policy):
        super().__init__(policy)
        self._store = ExactSnapshotStore()

    def reset(self):
        self._store = ExactSnapshotStore()

    def decide(self, world, action, actor, target,
               scenario_id="", step=0, family="") -> DecisionRecord:
        t0 = time.perf_counter_ns()
        hit = self._store.lookup(world, action, actor, target)
        expected = reference_oracle(world, action, actor, target)
        if hit is not None:
            verdict, work = hit
            lat = time.perf_counter_ns() - t0
            rec = DecisionRecord(
                scenario_id=scenario_id, step_index=step, system=self.name,
                action=action, actor=actor, target=target, family=family,
                verdict=verdict, expected=expected,
                correct=(verdict == expected),
                cache_hit=True, work_units=0, latency_ns=lat,
                rule_name="cached", dep_count=0,
                world_version=world.world_version,
            )
            self._records.append(rec)
            return rec

        result = self.policy.evaluate(world, action, actor, target)
        lat = time.perf_counter_ns() - t0
        self._store.store(world, action, actor, target,
                          result.verdict, result.work_units)
        rec = DecisionRecord(
            scenario_id=scenario_id, step_index=step, system=self.name,
            action=action, actor=actor, target=target, family=family,
            verdict=result.verdict, expected=expected,
            correct=(result.verdict == expected),
            cache_hit=False, work_units=result.work_units, latency_ns=lat,
            rule_name=result.rule_name, dep_count=len(result.dependencies),
            world_version=world.world_version,
        )
        self._records.append(rec)
        return rec

    def commit(self, record, proof, world):
        # Exact snapshot cache is always safe at commit (full state checked on lookup).
        record.commit_approved = record.verdict == "ALLOW"
        record.commit_reason = "snapshot_match"
        record.commit_work_units = 0
        return record


# ---------------------------------------------------------------------------
# Mode 4: Coarse cache — negative control (unsafe)
# ---------------------------------------------------------------------------

class CoarseCacheRuntime(RuntimeBase):
    name = "coarse_cache"

    def __init__(self, policy: Policy):
        super().__init__(policy)
        self._cache = CoarseCache()

    def reset(self):
        self._cache = CoarseCache()

    def decide(self, world, action, actor, target,
               scenario_id="", step=0, family="") -> DecisionRecord:
        t0 = time.perf_counter_ns()
        expected = reference_oracle(world, action, actor, target)
        hit = self._cache.lookup(action, target)
        if hit is not None:
            lat = time.perf_counter_ns() - t0
            rec = DecisionRecord(
                scenario_id=scenario_id, step_index=step, system=self.name,
                action=action, actor=actor, target=target, family=family,
                verdict=hit, expected=expected, correct=(hit == expected),
                cache_hit=True, work_units=0, latency_ns=lat,
                rule_name="coarse_cached", dep_count=0,
                world_version=world.world_version,
            )
            self._records.append(rec)
            return rec

        result = self.policy.evaluate(world, action, actor, target)
        lat = time.perf_counter_ns() - t0
        self._cache.store(action, target, result.verdict)
        rec = DecisionRecord(
            scenario_id=scenario_id, step_index=step, system=self.name,
            action=action, actor=actor, target=target, family=family,
            verdict=result.verdict, expected=expected,
            correct=(result.verdict == expected),
            cache_hit=False, work_units=result.work_units, latency_ns=lat,
            rule_name=result.rule_name, dep_count=len(result.dependencies),
            world_version=world.world_version,
        )
        self._records.append(rec)
        return rec

    def commit(self, record, proof, world):
        record.commit_approved = record.verdict == "ALLOW"
        record.commit_reason = "no_gate"
        record.commit_work_units = 0
        return record


# ---------------------------------------------------------------------------
# Mode 5: Stale proof accept — negative control (unsafe)
# ---------------------------------------------------------------------------

class StaleProofAcceptRuntime(RuntimeBase):
    """TAPR store but never evicts on dependency change.

    Demonstrates the failure mode of caching without dependency tracking.
    """
    name = "stale_proof_accept"

    def __init__(self, policy: Policy):
        super().__init__(policy)
        # We store (action, actor, target) → verdict, never invalidating.
        self._naive: Dict[str, str] = {}

    def reset(self):
        self._naive = {}

    def _key(self, action, actor, target):
        return f"{action}:{actor}:{target}"

    def decide(self, world, action, actor, target,
               scenario_id="", step=0, family="") -> DecisionRecord:
        t0 = time.perf_counter_ns()
        expected = reference_oracle(world, action, actor, target)
        k = self._key(action, actor, target)
        if k in self._naive:
            verdict = self._naive[k]
            lat = time.perf_counter_ns() - t0
            rec = DecisionRecord(
                scenario_id=scenario_id, step_index=step, system=self.name,
                action=action, actor=actor, target=target, family=family,
                verdict=verdict, expected=expected, correct=(verdict == expected),
                cache_hit=True, work_units=0, latency_ns=lat,
                rule_name="stale_cached", dep_count=0,
                world_version=world.world_version,
            )
            self._records.append(rec)
            return rec

        result = self.policy.evaluate(world, action, actor, target)
        lat = time.perf_counter_ns() - t0
        self._naive[k] = result.verdict
        rec = DecisionRecord(
            scenario_id=scenario_id, step_index=step, system=self.name,
            action=action, actor=actor, target=target, family=family,
            verdict=result.verdict, expected=expected,
            correct=(result.verdict == expected),
            cache_hit=False, work_units=result.work_units, latency_ns=lat,
            rule_name=result.rule_name, dep_count=len(result.dependencies),
            world_version=world.world_version,
        )
        self._records.append(rec)
        return rec

    def commit(self, record, proof, world):
        record.commit_approved = record.verdict == "ALLOW"
        record.commit_reason = "stale_no_check"
        record.commit_work_units = 0
        return record


# ---------------------------------------------------------------------------
# Mode 6: No commit gate — negative control (unsafe TOCTOU)
# ---------------------------------------------------------------------------

class NoCommitGateRuntime(TAPRRuntime):
    """TAPR with valid cache invalidation, but no check/commit gate.

    The dependency cache correctly evicts stale entries at decision time,
    but the commit gate is bypassed.  This tests TOCTOU: if the world
    changes *between* the decision call and the commit call, the old proof
    is accepted without re-checking.
    """
    name = "no_commit_gate"

    def __init__(self, policy: Policy):
        super().__init__(policy)
        self._no_gate = NoCommitGate()

    def commit(self, record, proof, world):
        key = f"{record.scenario_id}-{record.step_index}"
        stored_proof = self._proof_map.get(key)
        if stored_proof is None:
            record.commit_approved = False
            record.commit_reason = "no_proof"
            record.commit_work_units = 0
            return record
        decision = self._no_gate.check_and_commit(stored_proof, world, self.policy)
        record.commit_approved = decision.approved
        record.commit_reason = decision.reason
        record.commit_work_units = 0
        return record


# ---------------------------------------------------------------------------
# Mode 7: Pathless policy — negative control
# ---------------------------------------------------------------------------

class PathlessPolicyRuntime(RuntimeBase):
    """Full recompute, but path-dependent facts are zeroed before evaluation.

    Simulates a runtime that cannot see session history.  Path-dependent
    rules (e.g., export after sensitive read, retry after success) will fire
    incorrectly.
    """
    name = "pathless_policy"

    _PATH_FACTS = {
        "prior_sensitive_read", "prior_effect_succeeded", "path_tag",
    }

    def _blank_path(self, world: WorldState) -> WorldState:
        """Return a world state with path-dependent facts zeroed."""
        new_facts = dict(world.facts)
        for (name, subject), fact in list(world.facts.items()):
            if name in self._PATH_FACTS:
                new_facts[(name, subject)] = Fact(
                    name, subject,
                    False if fact.value is not False else False,
                    fact.version, "blanked")
        return WorldState(new_facts, (), world.world_version, world.policy_version)

    def decide(self, world, action, actor, target,
               scenario_id="", step=0, family="") -> DecisionRecord:
        blind_world = self._blank_path(world)
        t0 = time.perf_counter_ns()
        result = self.policy.evaluate(blind_world, action, actor, target)
        lat = time.perf_counter_ns() - t0
        expected = reference_oracle(world, action, actor, target)
        rec = DecisionRecord(
            scenario_id=scenario_id, step_index=step, system=self.name,
            action=action, actor=actor, target=target, family=family,
            verdict=result.verdict, expected=expected,
            correct=(result.verdict == expected),
            cache_hit=False, work_units=result.work_units, latency_ns=lat,
            rule_name=result.rule_name, dep_count=len(result.dependencies),
            world_version=world.world_version,
        )
        self._records.append(rec)
        return rec

    def commit(self, record, proof, world):
        record.commit_approved = record.verdict == "ALLOW"
        record.commit_reason = "pathless_no_gate"
        record.commit_work_units = 0
        return record


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

RUNTIME_REGISTRY = {
    "full_recompute":       FullRecomputeRuntime,
    "tapr_dep_cache":       TAPRRuntime,
    "exact_snapshot_cache": ExactSnapshotRuntime,
    "coarse_cache":         CoarseCacheRuntime,
    "stale_proof_accept":   StaleProofAcceptRuntime,
    "no_commit_gate":       NoCommitGateRuntime,
    "pathless_policy":      PathlessPolicyRuntime,
}

SAFE_SYSTEMS  = {"full_recompute", "tapr_dep_cache", "exact_snapshot_cache"}
UNSAFE_SYSTEMS = {
    "coarse_cache", "stale_proof_accept", "no_commit_gate", "pathless_policy"
}


def make_runtime(name: str, policy: Policy) -> RuntimeBase:
    cls = RUNTIME_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown runtime: {name!r}. "
                         f"Valid: {list(RUNTIME_REGISTRY)}")
    return cls(policy)
