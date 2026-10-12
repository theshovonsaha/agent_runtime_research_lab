"""Benchmark replays must reset active policy state between scenarios."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from benchmark import replay_scenario
from policy import Policy
from runtime import ExactSnapshotRuntime
from scenarios import CommitAttemptEvent, ProposalEvent, Scenario
from world import WorldBuilder


def test_replay_restores_scenario_policy_after_prior_rollover():
    world = (
        WorldBuilder()
        .set("authorized", "agent-0", True)
        .set("parent_scope_ok", "agent-0", True)
        .set("approval", "agent-0", True)
        .set("path_tag", "agent-0", "ordinary")
        .set("protected", "resource-0", False)
        .set("retention_hold", "resource-0", False)
        .set("evidence_fresh", "resource-0", True)
        .build()
    )
    scenario = Scenario(
        "policy-isolation",
        "authorization",
        [
            ProposalEvent("read", "agent-0", "resource-0"),
            CommitAttemptEvent(proposal_index=0),
        ],
        world,
    )
    runtime = ExactSnapshotRuntime(Policy(version=world.policy_version + 1))

    [record] = replay_scenario(
        scenario, runtime, Policy(version=world.policy_version)
    )

    assert runtime.policy.version == world.policy_version
    assert record.commit_expected_approved is True
    assert record.commit_approved is True
    assert record.commit_correct is True


def test_exact_snapshot_fails_closed_on_active_policy_version_mismatch():
    world = (
        WorldBuilder()
        .set("authorized", "agent-0", True)
        .set("parent_scope_ok", "agent-0", True)
        .set("approval", "agent-0", True)
        .set("path_tag", "agent-0", "ordinary")
        .set("protected", "resource-0", False)
        .set("retention_hold", "resource-0", False)
        .set("evidence_fresh", "resource-0", True)
        .build()
    )
    runtime = ExactSnapshotRuntime(Policy(version=world.policy_version))
    record = runtime.decide(world, "read", "agent-0", "resource-0")

    runtime.commit(
        record, None, world.with_policy_version(world.policy_version + 1)
    )

    assert record.commit_approved is False
    assert record.commit_reason == "policy_version_mismatch"
