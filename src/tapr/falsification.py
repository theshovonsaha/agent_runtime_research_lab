"""Reproducible falsification probes and expanded synthetic workloads."""
from __future__ import annotations

import argparse
import csv
import dataclasses
from datetime import datetime, timezone
import importlib.metadata
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .concurrent_gate import AtomicWorldStore
from .oracle import evaluate as evaluate_oracle
from .policy import DependencyContractError, Policy, PolicyResult
from .proof import CheckCommitGate, NoCommitGate, Proof
from .runtime import RUNTIME_REGISTRY, TAPRRuntime, make_runtime
from .scenarios import (
    CommitAttemptEvent,
    PolicyRolloverEvent,
    ProposalEvent,
    Scenario,
    WorldMutationEvent,
    generate_scenarios,
    replay_scenario,
    scenario_as_dict,
)
from .world import Fact, WorldBuilder, WorldState


def _base_world() -> WorldState:
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


def _world_dict(world: WorldState) -> dict[str, Any]:
    return {
        "facts": [
            {
                "name": fact.name,
                "subject": fact.subject,
                "value": fact.value,
                "version": fact.version,
                "provenance": fact.provenance,
            }
            for _, fact in sorted(world.facts.items())
        ],
        "path": [dataclasses.asdict(effect) for effect in world.path],
        "world_version": world.world_version,
        "policy_version": world.policy_version,
    }


def _record_probe_a(seed: int) -> dict[str, Any]:
    world = _base_world()
    policy = Policy(version=world.policy_version)
    runtime = TAPRRuntime(policy)
    runtime.decide(world, "write", "agent-0", "resource-0", "probe-a", 0)
    proof = runtime._proof_map["probe-a-0"]

    store = AtomicWorldStore(world)
    store.mutate_fact("authorized", "agent-0", False, "probe_revocation")
    blocked, occurred, _, observed = store.commit_effect(
        proof, policy, CheckCommitGate(), timestamp=1
    )
    safe_attempt = dataclasses.asdict(store.traces[-1])

    negative_store = AtomicWorldStore(world)
    negative_store.mutate_fact(
        "authorized", "agent-0", False, "probe_revocation"
    )
    negative, negative_occurred, _, negative_observed = (
        negative_store.commit_effect(
            proof, policy, NoCommitGate(), timestamp=1
        )
    )

    reusable_world = _base_world()
    reusable_policy = Policy(version=reusable_world.policy_version)
    reusable_eval = reusable_policy.evaluate(
        reusable_world, "read", "agent-0", "resource-0"
    )
    reusable_proof = Proof.from_result(
        reusable_eval, "reusable", "read", "agent-0", "resource-0",
        reusable_world,
    )
    reusable_store = AtomicWorldStore(reusable_world)
    first = reusable_store.commit_effect(
        reusable_proof, reusable_policy, CheckCommitGate(), timestamp=1
    )
    second = reusable_store.commit_effect(
        reusable_proof, reusable_policy, CheckCommitGate(), timestamp=2
    )
    failed_store = AtomicWorldStore(reusable_world)
    failed_attempt = failed_store.commit_effect(
        reusable_proof,
        reusable_policy,
        CheckCommitGate(),
        timestamp=3,
        effect_outcome="failed",
    )

    cas_world = _base_world()
    cas_store = AtomicWorldStore(cas_world)
    initial = cas_store.get_snapshot()
    candidate_a = initial.with_fact(
        Fact("metadata_version", "resource-0", 2, 2, "competing-a")
    )
    candidate_b = initial.with_fact(
        Fact("metadata_version", "resource-0", 3, 2, "competing-b")
    )
    cas_results = [
        cas_store.compare_and_swap(initial.world_version, candidate_a),
        cas_store.compare_and_swap(initial.world_version, candidate_b),
    ]

    return {
        "seed": seed,
        "atomicity_scope": "single-process in-memory lock; no distributed guarantee",
        "stale_proof": {
            "proof_world_version": proof.established_at,
            "state_version_after_revocation": store.get_snapshot().world_version,
            "commit_approved": blocked.approved,
            "effect_occurred": occurred,
            "effect_count": store.get_snapshot().count_effects(
                "write", "resource-0"
            ),
            "oracle_verdict": observed,
            "trace": safe_attempt,
            "final_world": _world_dict(store.get_snapshot()),
        },
        "unsafe_negative_control": {
            "commit_approved": negative.approved,
            "effect_occurred": negative_occurred,
            "oracle_verdict": negative_observed,
            "trace": dataclasses.asdict(negative_store.traces[-1]),
            "final_world": _world_dict(negative_store.get_snapshot()),
        },
        "reusable_proof": {
            "first_effect": first[1],
            "second_effect": second[1],
            "effect_count": reusable_store.get_snapshot().count_effects(
                "read", "resource-0"
            ),
            "trace": [dataclasses.asdict(t) for t in reusable_store.traces],
        },
        "failed_effect": {
            "authorization_approved": failed_attempt[0].approved,
            "effect_occurred": failed_attempt[1],
            "trace": dataclasses.asdict(failed_store.traces[-1]),
            "final_world": _world_dict(failed_store.get_snapshot()),
        },
        "competing_updates": {
            "attempts": [
                {"expected_version": initial.world_version, "accepted": result}
                for result in cas_results
            ],
            "accepted_count": sum(cas_results),
            "interleaving": cas_store._interleaving,
            "final_world": _world_dict(cas_store.get_snapshot()),
        },
    }


def _mutate(world: WorldState, name: str, subject: str,
            value: Any) -> WorldState:
    old = world.facts.get((name, subject))
    version = old.version + 1 if old else 1
    return world.with_fact(Fact(name, subject, value, version, "fuzz_mutation"))


def _mutation_cases() -> list[dict[str, Any]]:
    return [
        {"case": "authorization_revocation", "action": "read",
         "mutation": ("authorized", "agent-0", False)},
        {"case": "scope_reduction", "action": "read",
         "mutation": ("parent_scope_ok", "agent-0", False)},
        {"case": "approval_change", "action": "write",
         "mutation": ("approval", "agent-0", False)},
        {"case": "evidence_revision", "action": "write",
         "mutation": ("evidence_fresh", "resource-0", False)},
        {"case": "protected_resource", "action": "delete",
         "mutation": ("protected", "resource-0", True)},
        {"case": "retention_hold", "action": "delete",
         "mutation": ("retention_hold", "resource-0", True)},
        {"case": "sensitive_read_history", "action": "export",
         "mutation": ("prior_sensitive_read", "agent-0", True)},
        {"case": "retry_replay", "action": "retry",
         "mutation": ("prior_effect_succeeded", "resource-0", True)},
        {"case": "delegation_scope", "action": "delegate",
         "mutation": ("path_tag", "agent-0", "untrusted_delegation")},
        {"case": "irrelevant_metadata", "action": "read",
         "mutation": ("metadata_version", "resource-0", 2)},
        {"case": "policy_rollover", "action": "read",
         "mutation": None},
        {"case": "resource_substitution", "action": "delete",
         "mutation": ("protected", "resource-substitution-target", True),
         "proposal_target": "resource-substitution-target"},
    ]


def _record_probe_b(seed: int) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    failures: list[dict] = []
    for index, case in enumerate(_mutation_cases()):
        base = _base_world()
        action = case["action"]
        target = "resource-0"
        proposal_target = case.get("proposal_target", target)
        if proposal_target != target:
            base = base.with_fact(
                Fact("protected", proposal_target, False, 1, "initial")
            ).with_fact(
                Fact("retention_hold", proposal_target, False, 1, "initial")
            ).with_fact(
                Fact("evidence_fresh", proposal_target, True, 1, "initial")
            )
        initial_expected = evaluate_oracle(
            base, action, "agent-0", target
        ).proposal_verdict

        for system in RUNTIME_REGISTRY:
            runtime = make_runtime(
                system, Policy(version=base.policy_version, proof_rounds=1)
            )
            sid = f"probe-b-{case['case']}-{system}"
            initial_record = runtime.decide(
                base, action, "agent-0", target, sid, 0, case["case"]
            )
            if case["mutation"] is None:
                current = base.with_policy_version(base.policy_version + 1)
                runtime.policy = Policy(
                    version=current.policy_version, proof_rounds=1
                )
            else:
                name, subject, value = case["mutation"]
                current = _mutate(base, name, subject, value)

            expected = evaluate_oracle(
                current, action, "agent-0", proposal_target
            )
            changed_record = runtime.decide(
                current, action, "agent-0", proposal_target, sid, 1,
                case["case"]
            )
            commit_record = (
                changed_record
                if case["case"] == "resource_substitution"
                else initial_record
            )
            commit_target = commit_record.target
            commit_started = time.perf_counter_ns()
            runtime.commit(commit_record, None, current)
            commit_latency = time.perf_counter_ns() - commit_started
            commit_expected = evaluate_oracle(
                current, action, "agent-0", commit_target
            ).commit_authorized
            commit_observed = bool(commit_record.commit_approved)
            disagreement = (
                changed_record.verdict != expected.proposal_verdict
                or commit_observed != commit_expected
            )
            row = {
                "seed": seed,
                "case": case["case"],
                "system": system,
                "action": action,
                "initial_expected_proposal": initial_expected,
                "initial_observed_proposal": initial_record.verdict,
                "expected_proposal_decision": expected.proposal_verdict,
                "observed_proposal_decision": changed_record.verdict,
                "expected_commit_outcome": commit_expected,
                "observed_commit_outcome": commit_observed,
                "effect_occurred": commit_observed,
                "invalidation_reason": changed_record.invalidation_reason
                or commit_record.commit_reason,
                "cache_hit": changed_record.cache_hit,
                "commit_latency_ns": commit_latency,
                "oracle_disagreement": disagreement,
                "commit_target": commit_target,
                "initial_world": _world_dict(base),
                "current_world": _world_dict(current),
                "mutation": case["mutation"],
            }
            rows.append(row)
            if disagreement:
                failure = {
                    **row,
                    "minimized_scenario": {
                        "scenario_id": sid,
                        "family": case["case"],
                        "initial_world": _world_dict(base),
                        "events": [
                            {
                                "type": "ProposalEvent",
                                "action": action,
                                "actor": "agent-0",
                                "target": target,
                            },
                            {
                                "type": "WorldMutationEvent",
                                "mutation": case["mutation"],
                                "policy_version": (
                                    current.policy_version
                                    if case["mutation"] is None else None
                                ),
                            },
                            {
                                "type": "ProposalEvent",
                                "action": action,
                                "actor": "agent-0",
                                "target": proposal_target,
                            },
                            {
                                "type": "CommitAttemptEvent",
                                "proposal_index": (
                                    2 if case["case"] == "resource_substitution"
                                    else 0
                                ),
                            },
                        ],
                    },
                }
                failures.append(failure)

    # Fault injection preserves a minimal counterexample while verifying that
    # the runtime rejects the omitted-dependency contract violation.
    base = _base_world()
    faulty_policy = Policy()
    evaluate = faulty_policy.evaluate

    def omit_dependencies(world, action, actor, target):
        result = evaluate(world, action, actor, target)
        return PolicyResult(
            result.verdict, frozenset(), result.work_units, result.rule_name,
            result.policy_identity,
        )

    faulty_policy.evaluate = omit_dependencies
    rejected = False
    try:
        TAPRRuntime(faulty_policy).decide(
            base, "read", "agent-0", "resource-0", "omission", 0
        )
    except DependencyContractError:
        rejected = True
    omission_case = {
        "seed": seed,
        "case": "omitted_authorization_dependencies",
        "system": "tapr_dep_cache",
        "expected_proposal_decision": "DENY after authorization revocation",
        "observed_proposal_decision": (
            "rejected_dependency_contract" if rejected else "ALLOW"
        ),
        "expected_commit_outcome": False,
        "observed_commit_outcome": False,
        "effect_occurred": False,
        "invalidation_reason": "dependency_contract_rejected",
        "oracle_disagreement": not rejected,
        "minimized_scenario": {
            "initial_world": _world_dict(base),
            "events": [
                {"type": "ProposalEvent", "action": "read",
                 "actor": "agent-0", "target": "resource-0"},
                {"type": "WorldMutationEvent",
                 "mutation": ["authorized", "agent-0", False]},
                {"type": "CommitAttemptEvent", "proposal_index": 0},
            ],
        },
    }
    rows.append(omission_case)
    if not rejected:
        failures.append(omission_case)
    return rows, failures


def _workload_profiles() -> dict[str, dict[str, int]]:
    families = [
        "authorization", "path_dependent", "revocation", "evidence_expiry",
        "retry_safety", "irrelevant_noise", "approval_escalation",
        "delegation_chain", "policy_rollover", "dependency_fan_out",
        "concurrent_mutation", "resource_substitution",
    ]

    def weights(**selected):
        return {family: selected.get(family, 0) for family in families}

    return {
        "low_reuse": weights(authorization=8, revocation=2),
        "high_reuse": weights(irrelevant_noise=8, authorization=1),
        "dependency_fanout": weights(dependency_fan_out=8, authorization=1),
        "path_chain": weights(path_dependent=6, retry_safety=4),
        "policy_low": weights(authorization=8, policy_rollover=1),
        "policy_high": weights(policy_rollover=8, authorization=1),
        "mutation_low": weights(authorization=8, concurrent_mutation=1),
        "mutation_high": weights(concurrent_mutation=8, authorization=1),
        "resource_substitution": weights(resource_substitution=8, authorization=1),
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    columns: list[str] = []
    for row in rows:
        for column in row:
            if column not in columns:
                columns.append(column)
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _minimize_failure(scenario: Scenario, record: Any) -> dict[str, Any]:
    world = scenario.initial_world
    proposal_world = None
    commit_world = None
    event_to_step: dict[int, int] = {}
    step = 0
    for event_index, event in enumerate(scenario.events):
        if isinstance(event, WorldMutationEvent):
            for name, subject, value, provenance in event.mutations:
                old = world.facts.get((name, subject))
                version = old.version + 1 if old else 1
                world = world.with_fact(
                    Fact(name, subject, value, version, provenance)
                )
        elif isinstance(event, PolicyRolloverEvent):
            world = world.with_policy_version(event.new_version)
        elif isinstance(event, ProposalEvent):
            event_to_step[event_index] = step
            if step == record.step_index:
                proposal_world = world
            step += 1
        elif (isinstance(event, CommitAttemptEvent)
              and event_to_step.get(event.proposal_index) == record.step_index):
            commit_world = world

    if proposal_world is None:
        return scenario_as_dict(scenario)

    events: list[Any] = [ProposalEvent(
        action=record.action, actor=record.actor, target=record.target
    )]
    if commit_world is not None:
        deltas = [
            (name, subject, fact.value, "minimized_state_delta")
            for (name, subject), fact in commit_world.facts.items()
            if proposal_world.facts.get((name, subject)) != fact
        ]
        if commit_world.policy_version != proposal_world.policy_version:
            events.append(PolicyRolloverEvent(
                new_version=commit_world.policy_version,
                reason="minimized policy delta",
            ))
        if deltas:
            events.append(WorldMutationEvent(
                mutations=deltas, reason="minimized commit-state delta"
            ))
        events.append(CommitAttemptEvent(proposal_index=0))

    minimized = Scenario(
        scenario_id=f"min-{scenario.scenario_id}-{record.system}",
        family=scenario.family,
        events=events,
        initial_world=proposal_world,
        notes="Minimized to one target proposal and the commit-state delta.",
    )
    replayed = replay_scenario(
        minimized, make_runtime(
            record.system, Policy(version=proposal_world.policy_version)
        ),
        Policy(version=proposal_world.policy_version),
    )
    reproduced = any(
        not item.correct or item.commit_correct is False for item in replayed
    )
    return {
        **scenario_as_dict(minimized),
        "minimization_verified": reproduced,
        "fallback_full_scenario": (
            scenario_as_dict(scenario) if not reproduced else None
        ),
    }


def run(seed: int, out_dir: Path) -> dict[str, Any]:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[2]
    historical_checksums = {}
    for directory in ("results", "papers", "figures"):
        root = repo_root / directory
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or out_dir in path.parents:
                continue
            historical_checksums[str(path.relative_to(repo_root))] = (
                hashlib.sha256(path.read_bytes()).hexdigest()
            )

    probe_a = _record_probe_a(seed)
    (out_dir / "probe_a_atomic.json").write_text(
        json.dumps(probe_a, indent=2, sort_keys=True) + "\n"
    )

    probe_b, counterexamples = _record_probe_b(seed)
    scalar_probe_b = [
        {
            key: value for key, value in row.items()
            if not isinstance(value, (dict, list, tuple))
        }
        for row in probe_b
    ]
    _write_csv(out_dir / "probe_b_cases.csv", scalar_probe_b)
    with (out_dir / "probe_b_cases.jsonl").open("w") as output:
        for case in probe_b:
            output.write(json.dumps(case, sort_keys=True) + "\n")
    with (out_dir / "counterexamples.jsonl").open("w") as output:
        for case in counterexamples:
            output.write(json.dumps(case, sort_keys=True) + "\n")

    from benchmark import run_once, summarise

    fuzz_cases: list[dict] = []
    fuzz_metrics: list[dict] = []
    scenario_inputs: list[dict] = []
    profiles = _workload_profiles()
    systems = list(RUNTIME_REGISTRY)
    for profile_index, (profile, weights) in enumerate(profiles.items()):
        for size in (24, 64):
            for repetition in range(2):
                case_seed = seed + profile_index * 100_003 + size * 101 + repetition
                scenarios = generate_scenarios(case_seed, size, weights=weights)
                scenario_inputs.extend(
                    {
                        "seed": case_seed,
                        "profile": profile,
                        **scenario_as_dict(scenario),
                    }
                    for scenario in scenarios
                )
                records = run_once(
                    scenarios, 8, systems, case_seed + 1_000_003
                )
                family_total = max(1, len(scenarios))
                for system in systems:
                    summary = summarise(records, system, repetition)
                    summary.update({
                        "seed": case_seed,
                        "profile": profile,
                        "scenario_size": size,
                        "dependency_shape": (
                            "fanout" if profile == "dependency_fanout"
                            else "path_chain" if profile == "path_chain"
                            else "flat_or_mixed"
                        ),
                        "cache_reuse_profile": profile,
                        "policy_change_rate": sum(
                            scenario.family == "policy_rollover"
                            for scenario in scenarios
                        ) / family_total,
                        "concurrent_mutation_rate": sum(
                            scenario.family == "concurrent_mutation"
                            for scenario in scenarios
                        ) / family_total,
                        "resource_substitution_rate": sum(
                            scenario.family == "resource_substitution"
                            for scenario in scenarios
                        ) / family_total,
                        "dependency_fanout_rate": sum(
                            scenario.family == "dependency_fan_out"
                            for scenario in scenarios
                        ) / family_total,
                    })
                    fuzz_metrics.append(summary)
                scenario_by_id = {
                    scenario.scenario_id: scenario for scenario in scenarios
                }
                for record in records:
                    trace = dataclasses.asdict(record)
                    trace.update({
                        "seed": case_seed,
                        "profile": profile,
                        "scenario_size": size,
                    })
                    fuzz_cases.append(trace)
                    if not record.correct or record.commit_correct is False:
                        scenario = scenario_by_id[record.scenario_id]
                        counterexamples.append({
                            "seed": case_seed,
                            "system": record.system,
                            "oracle_disagreement": True,
                            "record": trace,
                            "minimized_scenario": _minimize_failure(
                                scenario, record
                            ),
                        })

    _write_csv(out_dir / "probe_c_metrics.csv", fuzz_metrics)
    _write_csv(out_dir / "probe_c_cases.csv", fuzz_cases)
    with (out_dir / "probe_c_scenarios.jsonl").open("w") as output:
        for scenario in scenario_inputs:
            output.write(json.dumps(scenario, sort_keys=True) + "\n")
    with (out_dir / "counterexamples.jsonl").open("w") as output:
        for case in counterexamples:
            output.write(json.dumps(case, sort_keys=True) + "\n")

    def git_output(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, text=True,
            capture_output=True, check=False,
        )
        return result.stdout.strip()

    source_files = [
        repo_root / "src" / "benchmark.py",
        repo_root / "src" / "sweep.py",
        repo_root / "src" / "analyze.py",
        repo_root / "src" / "paper.py",
        *sorted((repo_root / "src" / "tapr").glob("*.py")),
        repo_root / "tests" / "test_research_hardening.py",
    ]
    source_checksums = {
        str(path.relative_to(repo_root)): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in source_files if path.is_file()
    }
    dependencies = {}
    for distribution in ("pytest", "numpy", "matplotlib"):
        try:
            dependencies[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            dependencies[distribution] = None

    summary = {
        "seed": seed,
        "probe_a": {
            "safe_stale_effect_occurred": probe_a["stale_proof"]["effect_occurred"],
            "unsafe_control_effect_occurred": probe_a[
                "unsafe_negative_control"
            ]["effect_occurred"],
            "reusable_effect_count": probe_a["reusable_proof"]["effect_count"],
            "competing_cas_successes": probe_a["competing_updates"][
                "accepted_count"
            ],
            "failed_effect_count": probe_a["failed_effect"]["trace"][
                "effect_count"
            ],
        },
        "probe_b_cases": len(probe_b),
        "probe_b_oracle_disagreements": sum(
            bool(row["oracle_disagreement"]) for row in probe_b
        ),
        "probe_c_configurations": len(fuzz_metrics),
        "probe_c_decision_records": len(fuzz_cases),
        "counterexample_count": len(counterexamples),
        "runtime_variants": systems,
    }
    (out_dir / "falsification_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    generated_checksums = {
        str(path.relative_to(out_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(out_dir.rglob("*"))
        if path.is_file() and path.name != "falsification_manifest.json"
    }
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline": {
            "git_revision": "743273347b416059ea2b7abca6ac9bf82552c5a3",
            "worktree_clean": True,
            "test_command": ".venv/bin/python -m pytest -q",
            "test_result": "77 passed",
            "historical_result_artifact_sha256": historical_checksums,
        },
        "source_revision": git_output("rev-parse", "HEAD"),
        "worktree_dirty": bool(git_output("status", "--porcelain")),
        "worktree_status": git_output("status", "--short"),
        "platform": {
            "system": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": sys.version,
        },
        "dependencies": dependencies,
        "run_configuration": {
            "seed": seed,
            "probe_c_sizes": [24, 64],
            "repetitions_per_profile": 2,
            "proof_rounds": 8,
            "profiles": list(profiles),
            "runtime_variants": systems,
            "oracle": "tapr.oracle.evaluate",
        },
        "source_sha256": source_checksums,
        "generated_artifact_sha256": generated_checksums,
        "manifest_checksum_scope": (
            "The manifest intentionally excludes its own checksum."
        ),
    }
    (out_dir / "falsification_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=816202610)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    summary = run(args.seed, args.out)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
