
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tapr.world import WorldBuilder
from tapr.policy import Policy, PolicyResult
from tapr.proof import Proof, CheckCommitGate


def test_old_allow_proof_rejected_after_policy_replacement():
    # Policy A: the original policy allows the action.
    world_v7 = (
        WorldBuilder(policy_version=7)
        .set("authorized", "alice", True)
        .set("parent_scope_ok", "alice", True)
        .set("approval", "alice", True)
        .set("path_tag", "alice", "ordinary")
        .set("prior_sensitive_read", "alice", False)
        .set("prior_effect_succeeded", "account-1", False)
        .set("protected", "account-1", False)
        .set("retention_hold", "account-1", False)
        .set("evidence_fresh", "account-1", True)
        .build()
    )

    policy_a = Policy(version=7)
    result = policy_a.evaluate(
        world_v7, "read", "alice", "account-1"
    )
    assert result.verdict == "ALLOW"

    proof = Proof.from_result(
        result, "proof-original", "read", "alice",
        "account-1", world_v7
    )

    # Simulate policy replacement: Policy B denies every action.
    policy_b = Policy(version=8)
    policy_b._rules = [
        lambda world, action, actor, target: PolicyResult(
            "DENY", frozenset(), 0, "replacement_policy_deny"
        )
    ]

    # Simulate a policy-version rollback in the world metadata.
    world_v8 = world_v7.with_policy_version(8)
    world_rolled_back = world_v8.with_policy_version(7)

    # The commit gate must not trust Policy A's proof under Policy B.
    decision = CheckCommitGate().check_and_commit(
        proof, world_rolled_back, policy_b
    )

    assert decision.approved is False, (
        "SECURITY FAILURE: old ALLOW proof accepted after policy replacement"
    )
   # The original proof remains historical evidence; it must not authorize commit.
    assert decision.proof.verdict == "ALLOW"
    assert decision.approved is False
    assert decision.reason == "policy_version_mismatch"