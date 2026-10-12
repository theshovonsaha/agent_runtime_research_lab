
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tapr.world import WorldBuilder, Fact
from tapr.policy import Policy, reference_oracle
from tapr.proof import Proof, CheckCommitGate, NoCommitGate


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


def test_revoked_proof_cannot_authorize_commit():
    policy = Policy(version=1)
    gate = CheckCommitGate()
    world_before = make_world()

    result = policy.evaluate(
        world_before, "write", "agent-0", "res-1"
    )
    proof = Proof.from_result(
        result, "proof-before-revocation",
        "write", "agent-0", "res-1", world_before
    )
    assert proof.verdict == "ALLOW"

    world_after = world_before.with_fact(
        Fact("authorized", "agent-0", False, 2, "revocation")
    )
    assert reference_oracle(
        world_after, "write", "agent-0", "res-1"
    ) != "ALLOW"

    safe_decision = gate.check_and_commit(
        proof, world_after, policy
    )
    unsafe_decision = NoCommitGate().check_and_commit(
        proof, world_after, policy
    )

    # The real safety assertion is the effect boundary's decision.
    assert safe_decision.approved is False
    assert unsafe_decision.approved is True
