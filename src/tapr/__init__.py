"""
tapr – Typed Action Proof Reuse (TAPR) Governance Engine & Research Harness.

A formal runtime governance package for autonomous AI agents that turns
probabilistic actions into verifiable, auditable, and dependency-sound state effects.
"""

from .world import Fact, EffectRecord, WorldState, WorldBuilder
from .policy import Dependency, PolicyResult, Policy, reference_oracle
from .oracle import OracleResult, evaluate as evaluate_oracle
from .proof import Proof, ProofStore, CheckCommitGate, NoCommitGate
from .runtime import (
    RuntimeBase,
    DecisionRecord,
    FullRecomputeRuntime,
    TAPRRuntime,
    ExactSnapshotRuntime,
    CoarseCacheRuntime,
    StaleProofAcceptRuntime,
    NoCommitGateRuntime,
    PathlessPolicyRuntime,
    make_runtime,
    SAFE_SYSTEMS,
    UNSAFE_SYSTEMS,
)
from .audit import AuditEntry, AuditExplanation, audit_decision
from .concurrent_gate import (
    AtomicWorldStore,
    CommitTrace,
    RaceResult,
    run_concurrency_race,
)
from .intent import IntentCase, ExtractionResult, DeterministicIntentExtractor, evaluate_intent_layer

__version__ = "2.1.0"
__all__ = [
    "Fact",
    "EffectRecord",
    "WorldState",
    "WorldBuilder",
    "Dependency",
    "PolicyResult",
    "Policy",
    "reference_oracle",
    "OracleResult",
    "evaluate_oracle",
    "Proof",
    "ProofStore",
    "CheckCommitGate",
    "NoCommitGate",
    "RuntimeBase",
    "DecisionRecord",
    "FullRecomputeRuntime",
    "TAPRRuntime",
    "ExactSnapshotRuntime",
    "CoarseCacheRuntime",
    "StaleProofAcceptRuntime",
    "NoCommitGateRuntime",
    "PathlessPolicyRuntime",
    "make_runtime",
    "SAFE_SYSTEMS",
    "UNSAFE_SYSTEMS",
    "AuditEntry",
    "AuditExplanation",
    "audit_decision",
    "AtomicWorldStore",
    "CommitTrace",
    "RaceResult",
    "run_concurrency_race",
    "IntentCase",
    "ExtractionResult",
    "DeterministicIntentExtractor",
    "evaluate_intent_layer",
]
