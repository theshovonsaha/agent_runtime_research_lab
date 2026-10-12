"""
benchmark.py – Four-layer agent runtime benchmark.

This benchmark measures governance runtime correctness and efficiency across
stateful, adversarial scenarios.  It is NOT a language benchmark: no LLM is
invoked, no natural-language is parsed.  The measured system is the runtime
governance layer — the part that decides whether a typed proposed action is
admissible given the current world state, execution path, and policy.

Four layers measured
--------------------
Layer A (Intent)       Simulated: typed proposals are generated with known
                       expected verdicts.  Intent extraction accuracy is
                       outside this benchmark's scope.

Layer B (Governance)   Primary measurement: decision accuracy, unsafe allows,
                       false denials, cache hit rate, full evaluations,
                       latency.

Layer C (Effects)      Commit-gate correctness: did the gate correctly
                       block stale proofs at the effect boundary?

Layer D (Accountability) Proof reconstruction: can we determine why a
                       decision was made from the recorded data?

Why stateful scenarios?
-----------------------
Isolated test cases cannot catch the most dangerous failure mode: a runtime
that serves a cached ALLOW decision after the authorization has been revoked.
Stateful event sequences let us measure exactly this: how many proofs are
still accepted when they should have been invalidated?

Usage
-----
    python src/benchmark.py                       # default run
    python src/benchmark.py --quick               # fast smoke test
    python src/benchmark.py --scenarios 500 --repeats 5 --proof-rounds 16
    python src/benchmark.py --systems tapr_dep_cache full_recompute
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import importlib.metadata
import json
import platform
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure src/ is on the path when run from project root.
_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from policy import Policy
from tapr.oracle import evaluate as evaluate_oracle
from runtime import (RUNTIME_REGISTRY, SAFE_SYSTEMS, UNSAFE_SYSTEMS,
                     DecisionRecord, make_runtime)
from scenarios import (Scenario, WorldMutationEvent, ProposalEvent,
                       CommitAttemptEvent, PolicyRolloverEvent,
                       generate_scenarios, EventKind, FAMILIES,
                       scenario_as_dict)
from world import WorldState


# ---------------------------------------------------------------------------
# Scenario replay
# ---------------------------------------------------------------------------

def replay_scenario(scenario: Scenario, runtime, policy: Policy,
                    rep: int = 0) -> List[DecisionRecord]:
    """Replay all events in a scenario against a runtime.

    Returns the list of DecisionRecords produced (one per ProposalEvent).
    """
    runtime.reset()
    runtime.policy = policy
    world = scenario.initial_world
    records: List[DecisionRecord] = []
    # proposal_index -> (DecisionRecord, world_at_proposal_time)
    pending_commits: Dict[int, tuple] = {}
    effects_by_action: Dict[tuple[str, str], int] = {}
    step = 0

    for event_idx, event in enumerate(scenario.events):
        if isinstance(event, WorldMutationEvent):
            for (name, subject, value, prov) in event.mutations:
                from world import Fact
                existing = world.facts.get((name, subject))
                ver = (existing.version + 1) if existing else 1
                new_fact = Fact(name, subject, value, ver, prov)
                world = world.with_fact(new_fact)
            # Policy rollover handled separately.

        elif isinstance(event, PolicyRolloverEvent):
            world = world.with_policy_version(event.new_version)
            policy = Policy(version=event.new_version,
                            proof_rounds=policy.proof_rounds)
            runtime.policy = policy

        elif isinstance(event, ProposalEvent):
            rec = runtime.decide(
                world, event.action, event.actor, event.target,
                scenario_id=scenario.scenario_id,
                step=step,
                family=scenario.family,
            )
            # Record oracle verdict at this world state.
            rec.expected = evaluate_oracle(
                world, event.action, event.actor, event.target
            ).proposal_verdict
            rec.correct = (rec.verdict == rec.expected)
            records.append(rec)
            pending_commits[event_idx] = (rec, world, step)
            step += 1

        elif isinstance(event, CommitAttemptEvent):
            # Find the corresponding proposal.
            commit_info = pending_commits.get(event.proposal_index)
            if commit_info is None:
                continue
            rec, world_at_proposal, proposal_step = commit_info
            # The world may have changed since the proposal.
            commit_started = time.perf_counter_ns()
            rec = runtime.commit(rec, None, world)
            rec.commit_latency_ns = time.perf_counter_ns() - commit_started
            oracle_result = evaluate_oracle(
                world, rec.action, rec.actor, rec.target,
                active_policy_version=runtime.policy.version,
            )
            rec.commit_expected_approved = oracle_result.commit_authorized
            rec.commit_correct = (
                rec.commit_approved == rec.commit_expected_approved
            )
            rec.commit_world_version = world.world_version
            rec.effect_occurred = bool(rec.commit_approved)
            effect_key = (rec.action, rec.target)
            if rec.effect_occurred:
                effects_by_action[effect_key] = effects_by_action.get(effect_key, 0) + 1
            rec.effect_count = effects_by_action.get(effect_key, 0)

    return records


# ---------------------------------------------------------------------------
# Single repetition across all systems
# ---------------------------------------------------------------------------

def run_once(scenarios: List[Scenario], proof_rounds: int,
             systems: List[str], seed: int) -> List[DecisionRecord]:
    """Run all systems over all scenarios in one repetition."""
    rng = random.Random(seed)
    # Randomize system order to reduce ordering effects.
    order = list(systems)
    rng.shuffle(order)

    all_records: List[DecisionRecord] = []
    policy = Policy(version=1, proof_rounds=proof_rounds)

    for system_name in order:
        runtime = make_runtime(system_name, policy)
        shuffled = list(scenarios)
        rng.shuffle(shuffled)

        for sc in shuffled:
            sc_policy = Policy(version=sc.initial_world.policy_version,
                               proof_rounds=proof_rounds)
            recs = replay_scenario(sc, runtime, sc_policy, rep=seed)
            for r in recs:
                r.system = system_name  # ensure system tag is set
            all_records.extend(recs)

    return all_records


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------

def summarise(records: List[DecisionRecord], system: str, rep: int) -> Dict[str, Any]:
    """Compute per-system summary metrics from decision records."""
    recs = [r for r in records if r.system == system]
    if not recs:
        return {}

    n = len(recs)
    correct = sum(r.correct for r in recs)
    unsafe  = sum(r.unsafe_allow for r in recs)
    false_d = sum(r.false_deny for r in recs)
    hits    = sum(r.cache_hit for r in recs)
    work    = sum(r.work_units for r in recs)
    lats    = [r.latency_ns for r in recs]

    # Commit gate metrics (only for records with commit data).
    commit_recs = [r for r in recs if r.commit_approved is not None]
    stale_commits = sum(r.stale_commit_accept for r in commit_recs)
    commit_false_denials = sum(
        r.commit_approved is False and r.commit_expected_approved is True
        for r in commit_recs
    )
    commit_correct = (sum(r.commit_correct for r in commit_recs)
                      if commit_recs else 0)
    effects = [r for r in commit_recs if r.effect_occurred]
    unsafe_effects = sum(
        r.effect_occurred is True and r.commit_expected_approved is False
        for r in commit_recs
    )

    def pct(xs, q):
        if not xs: return 0.0
        ys = sorted(xs)
        pos = (len(ys) - 1) * q
        lo = int(pos); hi = min(lo + 1, len(ys) - 1)
        return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)

    return {
        "system":              system,
        "rep":                 rep,
        "decisions":           n,
        "correct":             correct,
        "accuracy":            correct / n,
        "unsafe_allows":       unsafe,
        "unsafe_allow_rate":   unsafe / n,
        "false_denies":        false_d,
        "false_deny_rate":     false_d / n,
        "cache_hits":          hits,
        "cache_hit_rate":      hits / n,
        "full_evaluations":    n - hits,
        "full_eval_rate":      (n - hits) / n,
        "work_units":          work,
        "mean_latency_ns":     statistics.mean(lats),
        "median_latency_ns":   pct(lats, 0.50),
        "p95_latency_ns":      pct(lats, 0.95),
        "p99_latency_ns":      pct(lats, 0.99),
        "commit_attempts":     len(commit_recs),
        "stale_commit_accepts": stale_commits,
        "commit_false_denies": commit_false_denials,
        "commit_false_deny_rate": (
            commit_false_denials / len(commit_recs) if commit_recs else 0.0
        ),
        "commit_gate_correct": commit_correct,
        "commit_accuracy":     commit_correct / len(commit_recs) if commit_recs else 0.0,
        "effects":             len(effects),
        "unsafe_effects":      unsafe_effects,
        "total_work_units":    work + sum(r.commit_work_units or 0 for r in commit_recs),
        "mean_commit_latency_ns": (
            statistics.mean(r.commit_latency_ns or 0 for r in commit_recs)
            if commit_recs else 0
        ),
        "p50_commit_latency_ns": pct(
            [r.commit_latency_ns or 0 for r in commit_recs], 0.50
        ),
        "p95_commit_latency_ns": pct(
            [r.commit_latency_ns or 0 for r in commit_recs], 0.95
        ),
        "p99_commit_latency_ns": pct(
            [r.commit_latency_ns or 0 for r in commit_recs], 0.99
        ),
    }


def family_accuracy(records: List[DecisionRecord],
                    system: str) -> Dict[str, float]:
    """Per-family accuracy for the heatmap."""
    result: Dict[str, float] = {}
    for fam in FAMILIES:
        recs = [r for r in records
                if r.system == system and r.family == fam]
        if recs:
            result[fam] = sum(r.correct for r in recs) / len(recs)
    return result


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------

def _write_csv(path: Path, records: List[Dict]) -> None:
    if not records:
        return
    keys: list = []
    for row in records:
        for k in row:
            if k not in keys:
                keys.append(k)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--out",          default="results",
                    help="Output directory for CSVs and manifest.")
    ap.add_argument("--scenarios",    type=int, default=400,
                    help="Number of scenarios to generate (default 400).")
    ap.add_argument("--repeats",      type=int, default=5,
                    help="Timing repetitions (default 5).")
    ap.add_argument("--proof-rounds", type=int, default=16,
                    help="Simulated proof-construction cost (default 16).")
    ap.add_argument("--seed",         type=int, default=20261011,
                    help="Random seed for reproducibility.")
    ap.add_argument("--systems",      nargs="+",
                    default=list(RUNTIME_REGISTRY.keys()),
                    help="Runtime modes to benchmark.")
    ap.add_argument("--quick",        action="store_true",
                    help="Small fast run (overrides scenarios/repeats/rounds).")
    a = ap.parse_args()

    if a.quick:
        a.scenarios   = min(a.scenarios,  80)
        a.repeats     = min(a.repeats,     3)
        a.proof_rounds = min(a.proof_rounds, 4)

    # Validate.
    for s in a.systems:
        if s not in RUNTIME_REGISTRY:
            ap.error(f"Unknown system: {s!r}. Valid: {list(RUNTIME_REGISTRY)}")
    if a.scenarios < 10:
        ap.error("--scenarios must be >= 10")
    if a.repeats < 2:
        ap.error("--repeats must be >= 2 for meaningful timing intervals")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Generating {a.scenarios} scenarios (seed={a.seed}) …")
    scenarios = generate_scenarios(a.seed, a.scenarios)

    # Persist generated scenarios for exact replay.
    sc_path = out / "scenarios.jsonl"
    with sc_path.open("w") as f:
        for sc in scenarios:
            f.write(json.dumps(scenario_as_dict(sc), sort_keys=True) + "\n")

    print(f"Running {a.repeats} repetitions × {len(a.systems)} systems …")
    all_records: List[DecisionRecord] = []
    all_summaries: List[Dict] = []

    for rep in range(a.repeats):
        recs = run_once(scenarios, a.proof_rounds, a.systems, a.seed + rep * 997)
        all_records.extend(recs)
        for sys_name in a.systems:
            s = summarise(recs, sys_name, rep)
            if s:
                all_summaries.append(s)
        print(f"  repetition {rep+1}/{a.repeats} done "
              f"({len(recs)} decision records)")

    # Write per-decision traces.
    trace_path = out / "decision_traces.csv"
    _write_csv(trace_path, [dataclasses.asdict(r) for r in all_records])
    print(f"Wrote {len(all_records)} decision records → {trace_path}")

    # Write per-rep summaries.
    metrics_path = out / "run_metrics.csv"
    _write_csv(metrics_path, all_summaries)

    # Write per-family accuracy (averaged across all reps).
    family_rows = []
    for sys_name in a.systems:
        fa = family_accuracy(all_records, sys_name)
        for fam, acc in fa.items():
            family_rows.append({"system": sys_name, "family": fam, "accuracy": acc})
    _write_csv(out / "family_accuracy.csv", family_rows)

    # Write manifest.
    import hashlib as _hl
    def _sha(p: Path) -> str:
        if not p.exists(): return ""
        return _hl.sha256(p.read_bytes()).hexdigest()

    def _version(distribution: str) -> Optional[str]:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            return None

    repo_root = Path(__file__).resolve().parents[1]
    source_checksums = {
        str(path.relative_to(repo_root)): _sha(path)
        for source_root in (repo_root / "src", repo_root / "tests")
        for path in sorted(source_root.rglob("*.py"))
    }
    manifest = {
        "benchmark":     "Agent Runtime Research Lab v2",
        "version":       "2.0.0",
        "command_line":  [sys.executable, *sys.argv],
        "seed":          a.seed,
        "scenarios":     a.scenarios,
        "repeats":       a.repeats,
        "proof_rounds":  a.proof_rounds,
        "systems":       a.systems,
        "safe_systems":  list(SAFE_SYSTEMS & set(a.systems)),
        "unsafe_systems": list(UNSAFE_SYSTEMS & set(a.systems)),
        "mode":          "offline synthetic stateful scenarios",
        "safety": {
            "network_access":        False,
            "external_side_effects": False,
            "credentials_required":  False,
            "subprocesses_spawned":  False,
            "llm_invoked":           False,
        },
        "python_version": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "source_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root,
            text=True, capture_output=True, check=False,
        ).stdout.strip() or None,
        "worktree_status": subprocess.run(
            ["git", "status", "--short"], cwd=repo_root,
            text=True, capture_output=True, check=False,
        ).stdout.strip(),
        "dependencies": {
            package: _version(package)
            for package in ("numpy", "matplotlib", "pytest")
        },
        "source_sha256": source_checksums,
        "timing_note":    (
            "Local microbenchmark only. Do not compare raw latency values "
            "across machines or against published benchmarks."
        ),
        "input_hashes": {
            "scenarios.jsonl":    _sha(sc_path),
            "decision_traces.csv": _sha(trace_path),
            "run_metrics.csv":    _sha(metrics_path),
            "family_accuracy.csv": _sha(out / "family_accuracy.csv"),
        },
    }
    manifest["output_sha256"] = {
        path.name: _sha(path)
        for path in sorted(out.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    # Print quick summary table.
    print("\n── Quick summary (averaged across repetitions) ──")
    header = f"{'System':<26} {'Accuracy':>9} {'Unsafe✗':>8} {'CacheHit':>9} {'WorkUnits':>10} {'Latency μs':>12}"
    print(header)
    print("─" * len(header))
    for sys_name in a.systems:
        reps = [s for s in all_summaries if s["system"] == sys_name]
        if not reps:
            continue
        acc  = statistics.mean(r["accuracy"]        for r in reps)
        uns  = statistics.mean(r["unsafe_allows"]    for r in reps)
        hit  = statistics.mean(r["cache_hit_rate"]   for r in reps)
        work = statistics.mean(r["work_units"]       for r in reps)
        lat  = statistics.mean(r["mean_latency_ns"]  for r in reps) / 1_000
        tag  = "⚠ unsafe" if sys_name in UNSAFE_SYSTEMS else ""
        print(f"{sys_name:<26} {acc:>8.1%} {uns:>8.1f} {hit:>8.1%} "
              f"{work:>10.0f} {lat:>10.2f} μs  {tag}")

    print(f"\nOutputs in {out.resolve()}/")
    print("Run `python src/analyze.py` to generate charts and report.")


if __name__ == "__main__":
    main()
