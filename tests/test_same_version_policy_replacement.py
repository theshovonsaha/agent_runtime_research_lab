import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tapr.world import WorldBuilder
from tapr.policy import Policy, PolicyResult
from tapr.proof import Proof, CheckCommitGate


def test_same_version_different_policy_rules_do_not_reuse_old_allow_proof():
    world = (
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
    result = policy_a.evaluate(world, "read", "alice", "account-1")
    assert result.verdict == "ALLOW"

    proof = Proof.from_result(
        result, "same-version-test", "read", "alice", "account-1", world
    )

    # Deliberately adversarial replacement: same version, different rules.
    policy_b = Policy(version=7)
    policy_b._rules = [
        lambda world, action, actor, target: PolicyResult(
            "DENY", frozenset(), 0, "replacement_policy_deny"
        )
    ]

    decision = CheckCommitGate().check_and_commit(proof, world, policy_b)

    print(
        f"approved={decision.approved}, "
        f"reason={decision.reason}, "
        f"proof_verdict={decision.proof.verdict}"
    )

    # The replacement policy denies, so accepting the old ALLOW is unsafe.
    assert decision.approved is False
    assert decision.reason == "policy_identity_mismatch"
    assert decision.proof.verdict == "ALLOW"
