import random
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tapr.concurrent_gate import AtomicWorldStore
from tapr.oracle import evaluate as evaluate_oracle
from tapr.policy import (
    Dependency,
    DependencyContractError,
    Policy,
    PolicyResult,
)
from tapr.proof import CheckCommitGate, Proof
from tapr.runtime import ExactSnapshotRuntime, TAPRRuntime
from tapr.world import Fact, WorldBuilder


def make_world():
    return (
        WorldBuilder()
        .set("authorized", "agent-0", True)
        .set("parent_scope_ok", "agent-0", True)
        .set("approval", "agent-0", True)
        .set("path_tag", "agent-0", "ordinary")
        .set("prior_sensitive_read", "agent-0", False)
        .set("prior_effect_succeeded", "resource-0", False)
        .set("protected", "resource-0", False)
        .set("retention_hold", "resource-0", False)
        .set("evidence_fresh", "resource-0", True)
        .set("metadata_version", "resource-0", 1)
        .build()
    )


@pytest.mark.parametrize(
    ("action", "fact", "subject", "value", "expected"),
    [
        ("read", "authorized", "agent-0", False, "DENY"),
        ("read", "parent_scope_ok", "agent-0", False, "DENY"),
        ("write", "approval", "agent-0", False, "ESCALATE"),
        ("write", "evidence_fresh", "resource-0", False, "ESCALATE"),
        ("delete", "protected", "resource-0", True, "DENY"),
        ("delete", "retention_hold", "resource-0", True, "DENY"),
        ("export", "prior_sensitive_read", "agent-0", True, "DENY"),
        ("retry", "prior_effect_succeeded", "resource-0", True, "DENY"),
        ("delegate", "path_tag", "agent-0", "untrusted_delegation", "DENY"),
    ],
)
def test_semantically_relevant_fact_mutation_invalidates_tapr_cache(
    action, fact, subject, value, expected
):
    world = make_world()
    runtime = TAPRRuntime(Policy(version=world.policy_version))
    assert runtime.decide(world, action, "agent-0", "resource-0").verdict == "ALLOW"

    prior = world.facts[(fact, subject)]
    changed = world.with_fact(
        Fact(fact, subject, value, prior.version + 1, "falsification")
    )
    record = runtime.decide(changed, action, "agent-0", "resource-0")

    assert record.cache_hit is False
    assert record.verdict == expected
    assert record.verdict == evaluate_oracle(
        changed, action, "agent-0", "resource-0"
    ).proposal_verdict


def test_irrelevant_fact_change_preserves_reuse():
    world = make_world()
    runtime = TAPRRuntime(Policy(version=world.policy_version))
    runtime.decide(world, "read", "agent-0", "resource-0")

    changed = world.with_fact(
        Fact("metadata_version", "resource-0", 2, 2, "irrelevant")
    )
    record = runtime.decide(changed, "read", "agent-0", "resource-0")

    assert record.cache_hit is True
    assert record.verdict == evaluate_oracle(
        changed, "read", "agent-0", "resource-0"
    ).proposal_verdict


def test_dependency_omission_is_rejected_before_caching_or_commit():
    policy = Policy()
    original_evaluate = policy.evaluate

    def omit_dependencies(world, action, actor, target):
        result = original_evaluate(world, action, actor, target)
        return PolicyResult(
            result.verdict, frozenset(), result.work_units, result.rule_name,
            result.policy_identity,
        )

    policy.evaluate = omit_dependencies
    runtime = TAPRRuntime(policy)
    with pytest.raises(DependencyContractError, match="omitted required"):
        runtime.decide(make_world(), "read", "agent-0", "resource-0")

    valid_policy = Policy()
    world = make_world()
    result = valid_policy.evaluate(world, "read", "agent-0", "resource-0")
    incomplete = Proof.from_result(
        PolicyResult(
            result.verdict, frozenset(), result.work_units, result.rule_name,
            result.policy_identity,
        ),
        "omitted-dependency",
        "read",
        "agent-0",
        "resource-0",
        world,
    )
    with pytest.raises(DependencyContractError, match="omitted required"):
        CheckCommitGate().check_and_commit(incomplete, world, valid_policy)


def test_same_version_policy_replacement_invalidates_proposal_cache():
    policy = Policy(version=4)
    world = make_world().with_policy_version(4)
    runtime = TAPRRuntime(policy)
    assert runtime.decide(world, "read", "agent-0", "resource-0").verdict == "ALLOW"

    policy._rules = [
        lambda current, action, actor, target: PolicyResult(
            "DENY",
            frozenset({
                Dependency.from_fact(current.facts[("authorized", actor)]),
                Dependency.from_fact(
                    current.facts[("parent_scope_ok", actor)]
                ),
            }),
            0,
            "authorization",
        )
    ]
    record = runtime.decide(world, "read", "agent-0", "resource-0")

    assert record.cache_hit is False
    assert record.verdict == "DENY"


def test_exact_snapshot_rechecks_state_at_commit_boundary():
    world = make_world()
    runtime = ExactSnapshotRuntime(Policy(version=world.policy_version))
    record = runtime.decide(
        world, "delete", "agent-0", "resource-0", "exact-stale", 0
    )
    assert record.verdict == "ALLOW"
    changed = world.with_fact(
        Fact("protected", "resource-0", True, 2, "concurrent_change")
    )

    runtime.commit(record, None, changed)

    assert record.commit_approved is False
    assert record.commit_reason == "recomputed"


def test_concurrent_competing_cas_updates_serialize_and_reusable_proof_commits_twice():
    world = make_world()
    store = AtomicWorldStore(world)
    gate = CheckCommitGate()
    policy = Policy(version=world.policy_version)
    result = policy.evaluate(world, "read", "agent-0", "resource-0")
    proof = Proof.from_result(
        result, "reusable-proof", "read", "agent-0", "resource-0", world
    )

    start = threading.Barrier(3)
    cas_results = []

    def update(value):
        candidate = world.with_fact(
            Fact("metadata_version", "resource-0", value, 2, "competing_update")
        )
        start.wait(timeout=5)
        cas_results.append(store.compare_and_swap(world.world_version, candidate))

    workers = [
        threading.Thread(target=update, args=(value,))
        for value in (2, 3)
    ]
    for worker in workers:
        worker.start()
    start.wait(timeout=5)
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()

    assert sorted(cas_results) == [False, True]
    first, committed_first, _, _ = store.commit_effect(proof, policy, gate)
    second, committed_second, _, _ = store.commit_effect(proof, policy, gate)

    assert first.approved and second.approved
    assert committed_first and committed_second
    snapshot = store.get_snapshot()
    assert snapshot.count_effects("read", "resource-0") == 2
    assert [trace.effect_count for trace in store.traces] == [1, 2]
    assert all(trace.proof_state_version == world.world_version
               for trace in store.traces)
    assert store.traces[0].state_version_after <= store.traces[1].state_version_before


def test_failed_effect_attempt_is_not_counted_as_a_committed_effect():
    world = make_world()
    policy = Policy(version=world.policy_version)
    result = policy.evaluate(world, "write", "agent-0", "resource-0")
    proof = Proof.from_result(
        result, "failed-effect", "write", "agent-0", "resource-0", world
    )
    store = AtomicWorldStore(world)

    decision, committed, _, _ = store.commit_effect(
        proof, policy, CheckCommitGate(), effect_outcome="failed"
    )

    assert decision.approved is True
    assert committed is False
    assert store.traces[0].effect_outcome == "failed"
    assert store.traces[0].effect_occurred is False
    assert store.traces[0].effect_count == 0
    assert store.get_snapshot().path[-1].outcome == "failed"


def test_seeded_policy_mutations_match_independent_oracle():
    rng = random.Random(271828)
    policy = Policy()
    actions = ("read", "write", "delete", "export", "delegate", "retry")

    for _ in range(500):
        action = rng.choice(actions)
        world = (
            WorldBuilder()
            .set("authorized", "agent-0", rng.choice((True, False)))
            .set("parent_scope_ok", "agent-0", rng.choice((True, False)))
            .set("approval", "agent-0", rng.choice((True, False)))
            .set("path_tag", "agent-0", rng.choice(
                ("ordinary", "untrusted_delegation")
            ))
            .set("prior_sensitive_read", "agent-0", rng.choice((True, False)))
            .set("prior_effect_succeeded", "resource-0", rng.choice((True, False)))
            .set("protected", "resource-0", rng.choice((True, False)))
            .set("retention_hold", "resource-0", rng.choice((True, False)))
            .set("evidence_fresh", "resource-0", rng.choice((True, False)))
            .build()
        )

        evaluated = policy.evaluate(world, action, "agent-0", "resource-0")
        policy.validate_dependencies(
            evaluated, action, "agent-0", "resource-0"
        )
        expected = evaluate_oracle(
            world, action, "agent-0", "resource-0"
        ).proposal_verdict
        assert evaluated.verdict == expected


def test_legacy_import_paths_resolve_to_authoritative_package():
    import runtime as legacy_runtime
    import policy as legacy_policy

    assert legacy_runtime.TAPRRuntime is TAPRRuntime
    assert legacy_policy.Policy is Policy
