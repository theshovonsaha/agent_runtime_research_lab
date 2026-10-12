import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tapr.concurrent_gate import AtomicWorldStore
from tapr.policy import Policy, reference_oracle
from tapr.proof import CheckCommitGate, NoCommitGate, Proof


def make_world():
    from tapr.world import WorldBuilder

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
    result = policy.evaluate(world, "read", "agent-0", "res-1")
    return Proof.from_result(
        result, "atomic-test", "read", "agent-0", "res-1", world
    )


def test_atomic_boundary_rejects_proof_after_revocation():
    world = make_world()
    policy = Policy(version=world.policy_version)
    store = AtomicWorldStore(world)
    proof = make_proof(world, policy)

    store.mutate_fact("authorized", "agent-0", False, "test_revocation")

    decision, committed, _, oracle_verdict = store.commit_effect(
        proof, policy, CheckCommitGate()
    )

    assert oracle_verdict == "DENY"
    assert not decision.approved
    assert not committed
    assert store.get_snapshot().count_effects("read", "res-1") == 0
    assert reference_oracle(
        store.get_snapshot(), "read", "agent-0", "res-1"
    ) == "DENY"


def test_atomic_boundary_records_effect_after_valid_approval():
    world = make_world()
    policy = Policy(version=world.policy_version)
    store = AtomicWorldStore(world)
    proof = make_proof(world, policy)

    decision, committed, new_version, oracle_verdict = store.commit_effect(
        proof, policy, CheckCommitGate()
    )

    current = store.get_snapshot()
    assert oracle_verdict == "ALLOW"
    assert decision.approved
    assert committed
    assert current.world_version == new_version
    assert current.count_effects("read", "res-1") == 1


def test_negative_control_accepts_stale_proof_but_records_effect():
    world = make_world()
    policy = Policy(version=world.policy_version)
    store = AtomicWorldStore(world)
    proof = make_proof(world, policy)

    store.mutate_fact("authorized", "agent-0", False, "test_revocation")

    decision, committed, _, oracle_verdict = store.commit_effect(
        proof, policy, NoCommitGate()
    )

    assert oracle_verdict == "DENY"
    assert decision.approved
    assert committed
    current = store.get_snapshot()
    assert current.count_effects("read", "res-1") == 1
    assert reference_oracle(
        current, "read", "agent-0", "res-1"
    ) == "DENY"


def test_deterministic_threaded_revocation_blocks_stale_proof():
    """Force revocation to complete before the worker reaches the effect boundary."""
    from threading import Event, Thread

    world = make_world()
    policy = Policy(version=world.policy_version)
    store = AtomicWorldStore(world)
    proof = make_proof(world, policy)

    proof_ready = Event()
    allow_commit = Event()
    outcome = {}

    def worker():
        proof_ready.set()
        assert allow_commit.wait(timeout=5), "test did not release worker"
        outcome["result"] = store.commit_effect(
            proof, policy, CheckCommitGate()
        )

    thread = Thread(target=worker)
    thread.start()

    try:
        assert proof_ready.wait(timeout=5), "worker did not reach barrier"
        store.mutate_fact(
            "authorized", "agent-0", False, "deterministic_revocation"
        )
    finally:
        allow_commit.set()
        thread.join(timeout=5)

    assert not thread.is_alive(), "worker did not terminate"
    decision, committed, _, oracle_verdict = outcome["result"]
    assert oracle_verdict == "DENY"
    assert not decision.approved
    assert not committed
    assert store.get_snapshot().count_effects("read", "res-1") == 0


def test_deterministic_threaded_negative_control_exposes_stale_allow():
    """The same forced interleaving must expose the unsafe negative control."""
    from threading import Event, Thread

    world = make_world()
    policy = Policy(version=world.policy_version)
    store = AtomicWorldStore(world)
    proof = make_proof(world, policy)

    proof_ready = Event()
    allow_commit = Event()
    outcome = {}

    def worker():
        proof_ready.set()
        assert allow_commit.wait(timeout=5), "test did not release worker"
        outcome["result"] = store.commit_effect(
            proof, policy, NoCommitGate()
        )

    thread = Thread(target=worker)
    thread.start()

    try:
        assert proof_ready.wait(timeout=5), "worker did not reach barrier"
        store.mutate_fact(
            "authorized", "agent-0", False, "deterministic_revocation"
        )
    finally:
        allow_commit.set()
        thread.join(timeout=5)

    assert not thread.is_alive(), "worker did not terminate"
    decision, committed, _, oracle_verdict = outcome["result"]
    assert oracle_verdict == "DENY"
    assert decision.approved
    assert committed
    assert store.get_snapshot().count_effects("read", "res-1") == 1
