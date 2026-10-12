import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from concurrent_harness import AtomicWorldStore
from policy import Policy, reference_oracle
from proof import CheckCommitGate
from runtime import TAPRRuntime
from world import WorldBuilder


def make_world():
    return (
        WorldBuilder()
        .set("authorized", "agent-0", True)
        .set("parent_scope_ok", "agent-0", True)
        .set("approval", "agent-0", True)
        .set("path_tag", "agent-0", "ordinary")
        .set("prior_sensitive_read", "agent-0", False)
        .set("prior_effect_succeeded", "res-1", False)
        .set("protected", "res-1", False)
        .set("retention_hold", "res-1", False)
        .set("evidence_fresh", "res-1", True)
        .build()
    )


def make_proof(world, policy):
    runtime = TAPRRuntime(policy)
    runtime.decide(
        world, "read", "agent-0", "res-1",
        "deterministic-race", 0, "test",
    )
    proof = runtime._proof_map.get("deterministic-race-0")
    assert proof is not None
    return proof


def test_snapshot_check_then_act_has_deterministic_toctou():
    world = make_world()
    policy = Policy(version=world.policy_version)
    store = AtomicWorldStore(world)
    proof = make_proof(world, policy)
    gate = CheckCommitGate()

    validated = threading.Event()
    resume_effect = threading.Event()
    effects = []
    decisions = []

    def vulnerable_worker():
        # The authorization check happens against the pre-revocation snapshot.
        snapshot = store.get_snapshot()
        decision = gate.check_and_commit(proof, snapshot, policy)
        decisions.append(decision)
        assert decision.approved

        validated.set()
        assert resume_effect.wait(timeout=5)

        # Deliberately vulnerable: effect occurs without revalidation.
        effects.append("read:res-1")

    worker = threading.Thread(target=vulnerable_worker)
    worker.start()

    assert validated.wait(timeout=5)

    # Force revocation after validation and before the effect.
    store.mutate_fact("authorized", "agent-0", False, "test_revocation")
    revoked_world = store.get_snapshot()
    assert reference_oracle(
        revoked_world, "read", "agent-0", "res-1"
    ) == "DENY"

    resume_effect.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    # This is the deliberately vulnerable negative control.
    assert decisions[0].approved
    assert effects == ["read:res-1"]


def test_gate_rejects_proof_after_revocation_before_effect():
    world = make_world()
    policy = Policy(version=world.policy_version)
    store = AtomicWorldStore(world)
    proof = make_proof(world, policy)
    gate = CheckCommitGate()
    effects = []

    # Deterministically revoke before the effect-boundary check.
    store.mutate_fact("authorized", "agent-0", False, "test_revocation")
    current = store.get_snapshot()

    decision = gate.check_and_commit(proof, current, policy)
    if decision.approved:
        effects.append("read:res-1")

    assert decision.approved is False
    assert decision.reason == "recomputed"
    assert effects == []
    assert reference_oracle(
        current, "read", "agent-0", "res-1"
    ) == "DENY"
