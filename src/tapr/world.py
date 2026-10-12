"""
world.py – Typed, versioned world state for the Agent Runtime Research Lab.

An AI agent is not a text generator.  It is an execution system that turns
natural-language intent into typed proposals, evaluates them against a
runtime policy, commits effects to the world, and must account for what it
did and why.

This module defines the *world model*: a structured collection of versioned
facts about entities, authorization, evidence, and execution history.
Unlike a raw dictionary, every fact carries its version and provenance, so
the governance runtime can track exactly which facts a proof depended on and
whether those facts have since changed.

Key types
---------
Fact            Named, typed, versioned assertion about a subject entity.
EffectRecord    A committed effect in the session execution path.
WorldState      Immutable snapshot: facts mapping + path + policy version.
WorldBuilder    Fluent builder for constructing WorldState in tests/generators.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Fact:
    """A single named fact about a subject entity.

    Attributes
    ----------
    name        Logical fact name, e.g. ``"authorized"``, ``"evidence_fresh"``.
    subject     Entity this fact is about (user id, resource id, ...).
    value       The fact's current typed value.
    version     Monotonically increasing per ``(name, subject)`` pair.
                Version 0 means "never explicitly set" (default / absent).
    provenance  Human-readable tag indicating how this fact was established:
                ``"initial"``, ``"delegation"``, ``"effect"``, ``"expiry"``, etc.
    """
    name: str
    subject: str
    value: Any
    version: int
    provenance: str = "initial"

    def key(self) -> Tuple[str, str]:
        return (self.name, self.subject)

    def mutated(self, new_value: Any, provenance: str = "mutation") -> "Fact":
        """Return a new Fact with incremented version and updated value."""
        return Fact(self.name, self.subject, new_value, self.version + 1, provenance)

    def bumped(self) -> "Fact":
        """Return a new Fact with version incremented but value unchanged.

        Used when we need to signal that the fact was re-evaluated or
        re-confirmed, which should still invalidate caches that tracked the
        old version.
        """
        return Fact(self.name, self.subject, self.value, self.version + 1, "re-confirmed")


@dataclass(frozen=True)
class EffectRecord:
    """An effect that was committed (or attempted) within this session.

    The execution *path* is the ordered history of EffectRecords for the
    current session.  Path-dependent policies read this history to determine
    whether an action is now admissible.

    Attributes
    ----------
    action      What the agent attempted, e.g. ``"read"``, ``"export"``.
    actor       Entity (agent id) that performed the action.
    target      Entity the action was applied to.
    outcome     ``"success"``, ``"partial"``, ``"failed"``, ``"unknown"``.
    timestamp   Logical (not wall-clock) timestamp within the session.
    """
    action: str
    actor: str
    target: str
    outcome: str   # success | partial | failed | unknown
    timestamp: int


@dataclass(frozen=True)
class WorldState:
    """Immutable snapshot of the world at a single point in time.

    WorldState is the input to the policy engine.  Every governance decision
    is made against a specific WorldState.  Mutations produce a *new*
    WorldState with an incremented ``world_version``.

    Attributes
    ----------
    facts           ``(name, subject) -> Fact`` mapping.
    path            Ordered committed effect history for this session.
    world_version   Monotonically increasing across all mutations.
    policy_version  The policy version active when this state was created.
    """
    facts: Mapping[Tuple[str, str], Fact]
    path: Tuple[EffectRecord, ...]
    world_version: int
    policy_version: int

    # ------------------------------------------------------------------
    # Fact access helpers
    # ------------------------------------------------------------------

    def get(self, name: str, subject: str, default: Any = None) -> Any:
        """Return the value of a fact, or *default* if absent."""
        f = self.facts.get((name, subject))
        return f.value if f is not None else default

    def get_fact(self, name: str, subject: str) -> Optional[Fact]:
        return self.facts.get((name, subject))

    def fact_version(self, name: str, subject: str) -> int:
        """Return the version of a fact, or 0 if the fact is absent."""
        f = self.facts.get((name, subject))
        return f.version if f is not None else 0

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def has_prior_effect(self, action: str, target: str = "",
                         outcome: str = "") -> bool:
        """True if the path contains a matching effect record."""
        for e in self.path:
            if e.action != action:
                continue
            if target and e.target != target:
                continue
            if outcome and e.outcome != outcome:
                continue
            return True
        return False

    def count_effects(self, action: str, target: str = "") -> int:
        return sum(
            1 for e in self.path
            if e.action == action and (not target or e.target == target)
        )

    def last_outcome(self, action: str, target: str = "") -> Optional[str]:
        for e in reversed(self.path):
            if e.action == action and (not target or e.target == target):
                return e.outcome
        return None

    # ------------------------------------------------------------------
    # Fingerprinting (for cache keys)
    # ------------------------------------------------------------------

    def fingerprint_all(self) -> str:
        """Hash of the complete world state (exact-snapshot cache key)."""
        items = {
            f"{k[0]}:{k[1]}": (v.value, v.version)
            for k, v in sorted(self.facts.items())
        }
        blob = json.dumps({
            "facts": items,
            "path": [(e.action, e.actor, e.target, e.outcome, e.timestamp)
                     for e in self.path],
            "policy_version": self.policy_version,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def fingerprint_keys(self, keys: Sequence[Tuple[str, str]]) -> str:
        """Hash of a specific subset of facts + path + policy version.

        Used by the TAPR cache: the key covers only declared dependencies,
        so irrelevant metadata changes do not invalidate the proof.
        """
        items = {}
        for k in keys:
            f = self.facts.get(k)
            if f is not None:
                items[f"{k[0]}:{k[1]}"] = (f.value, f.version)
        blob = json.dumps({
            "facts": items,
            "path": [(e.action, e.actor, e.target, e.outcome, e.timestamp)
                     for e in self.path],
            "policy_version": self.policy_version,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def fingerprint_coarse(self, action: str, target: str) -> str:
        """Coarse cache key: action + target only.

        This is the *intentionally unsafe* negative-control cache key.
        It ignores authorization, evidence, path history, and policy version.
        """
        blob = json.dumps({"action": action, "target": target},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Mutation helpers (return new WorldState)
    # ------------------------------------------------------------------

    def with_fact(self, fact: Fact) -> "WorldState":
        new_facts = dict(self.facts)
        new_facts[fact.key()] = fact
        return WorldState(new_facts, self.path, self.world_version + 1,
                          self.policy_version)

    def with_facts(self, facts: Sequence[Fact]) -> "WorldState":
        new_facts = dict(self.facts)
        for f in facts:
            new_facts[f.key()] = f
        return WorldState(new_facts, self.path, self.world_version + 1,
                          self.policy_version)

    def with_effect(self, effect: EffectRecord) -> "WorldState":
        return WorldState(self.facts, self.path + (effect,),
                          self.world_version + 1, self.policy_version)

    def with_policy_version(self, pv: int) -> "WorldState":
        return WorldState(self.facts, self.path, self.world_version + 1, pv)


# ---------------------------------------------------------------------------
# Fluent builder
# ---------------------------------------------------------------------------

class WorldBuilder:
    """Fluent builder for constructing WorldState in tests and generators."""

    def __init__(self, policy_version: int = 1, world_version: int = 0):
        self._facts: dict[Tuple[str, str], Fact] = {}
        self._path: list[EffectRecord] = []
        self._world_version = world_version
        self._policy_version = policy_version
        self._ts = 0

    def set(self, name: str, subject: str, value: Any,
            provenance: str = "initial") -> "WorldBuilder":
        key = (name, subject)
        existing = self._facts.get(key)
        ver = (existing.version + 1) if existing else 1
        self._facts[key] = Fact(name, subject, value, ver, provenance)
        return self

    def push_effect(self, action: str, actor: str, target: str,
                    outcome: str = "success") -> "WorldBuilder":
        self._path.append(
            EffectRecord(action, actor, target, outcome, self._ts))
        self._ts += 1
        return self

    def build(self) -> WorldState:
        return WorldState(
            dict(self._facts),
            tuple(self._path),
            self._world_version,
            self._policy_version,
        )


# ---------------------------------------------------------------------------
# Helpers for tests and generators
# ---------------------------------------------------------------------------

def absent_fact(name: str, subject: str) -> Fact:
    """Sentinel for a fact that is explicitly absent (version=0, value=None)."""
    return Fact(name, subject, None, 0, "absent")
