import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tapr.world import WorldBuilder
from tapr.policy import Policy
from tapr.proof import Proof, CheckCommitGate
from tapr.oracle import evaluate as evaluate_oracle


def test_commit_gate_rejects_proof_when_active_policy_version_differs():
    world = (
        WorldBuilder(policy_version=7)
        .set("authorized", "alice", True)
        .set("parent_scope_ok", "alice", True)
        .set("approval", "alice", True)
        .set("path_tag", "alice", "ordinary")
        .set("prior_sensitive_read", "alice", False)
        .set("prior_effect_succeeded", "resource-1", False)
        .set("protected", "resource-1", False)
        .set("retention_hold", "resource-1", False)
        .set("evidence_fresh", "resource-1", True)
        .build()
    )

    old_policy = Policy(version=7)
    result = old_policy.evaluate(world, "read", "alice", "resource-1")
    proof = Proof.from_result(
        result, "policy-mismatch-test", "read", "alice", "resource-1", world
    )

    new_policy = Policy(version=8)
    decision = CheckCommitGate().check_and_commit(proof, world, new_policy)
    expected = evaluate_oracle(
        world, "read", "alice", "resource-1",
        active_policy_version=new_policy.version,
    )

    assert decision.approved is False
    assert decision.reason == "policy_version_mismatch"
    assert expected.proposal_verdict == "ALLOW"
    assert expected.policy_version_matches is False
    assert expected.commit_authorized is False
