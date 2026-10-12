import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from policy import Dependency, Policy, PolicyResult
from proof import CheckCommitGate, Proof
from world import WorldBuilder


def test_same_version_policy_replacement_rejects_old_allow_proof():
    world = (
        WorldBuilder()
        .set("authorized", "alice", True)
        .set("parent_scope_ok", "alice", True)
        .build()
    )

    policy_a = Policy(version=world.policy_version)
    result = policy_a.evaluate(world, "read", "alice", "res-1")
    assert result.verdict == "ALLOW"

    proof = Proof.from_result(
        result, "identity-regression", "read", "alice", "res-1", world
    )

    policy_b = Policy(version=policy_a.version)

    def deny_replacement(w, action, actor, target):
        return PolicyResult(
            verdict="DENY",
            dependencies=frozenset({
                Dependency.from_fact(w.facts[("authorized", actor)]),
                Dependency.from_fact(w.facts[("parent_scope_ok", actor)]),
            }),
            work_units=0,
            rule_name="authorization",
        )

    policy_b._rules = [deny_replacement]

    assert policy_a.version == policy_b.version
    assert policy_a.identity != policy_b.identity

    decision = CheckCommitGate().check_and_commit(proof, world, policy_b)

    assert decision.approved is False
    assert decision.reason == "recomputed"
    assert decision.proof is not None
    assert decision.proof.verdict == "DENY"
    assert decision.proof.policy_identity == policy_b.identity
