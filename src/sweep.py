"""
sweep.py – Parameter sensitivity sweep for the v2 benchmark.

Varies four dimensions simultaneously:
  - scenarios:     workload size
  - proof_rounds:  simulated proof-construction cost
  - scenario mix:  family weights (adversarial-heavy vs ordinary-heavy)

Measures how TAPR and comparison systems respond as these parameters change.
The key scientific questions:

1. At what proof cost does TAPR's reuse advantage appear?
2. Does TAPR's accuracy hold under heavier adversarial pressure?
3. Does the exact snapshot cache perform better or worse than TAPR as
   irrelevant metadata churn increases?

Usage:
    python src/sweep.py
    python src/sweep.py --out results/sweep.csv --quick
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import importlib.metadata
import json
import platform
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from benchmark import run_once, summarise
from scenarios import generate_scenarios


# ---------------------------------------------------------------------------
# Sweep configuration
# ---------------------------------------------------------------------------

SWEEP_SYSTEMS = [
    "full_recompute",
    "tapr_dep_cache",
    "exact_snapshot_cache",
    "coarse_cache",          # negative control (shows up as corner case)
    "stale_proof_accept",    # negative control
]

# Workload mixes: (name, weights dict)
MIXES = {
    "uniform":     None,   # equal weight per family
    "adversarial": {
        "revocation":       5,
        "evidence_expiry":  5,
        "path_dependent":   4,
        "retry_safety":     4,
        "irrelevant_noise": 2,
        "authorization":    1,
    },
    "noise_heavy": {
        "irrelevant_noise": 8,
        "authorization":    2,
        "approval_escalation": 1,
    },
    "mutation_heavy": {
        "concurrent_mutation": 8,
        "revocation": 4,
        "evidence_expiry": 4,
        "authorization": 1,
    },
    "fanout_heavy": {
        "dependency_fan_out": 8,
        "authorization": 1,
    },
    "policy_heavy": {
        "policy_rollover": 8,
        "authorization": 1,
    },
    "substitution_heavy": {
        "resource_substitution": 8,
        "authorization": 1,
    },
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out",     default="results/sweep.csv")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--seed",    type=int, default=20260101)
    ap.add_argument("--quick",   action="store_true")
    a = ap.parse_args()

    sizes  = [100, 300]        if a.quick else [200, 600, 1200]
    costs  = [0, 8]            if a.quick else [0, 4, 16, 40]
    repeats = min(a.repeats, 2) if a.quick else a.repeats
    mixes  = (
        ["uniform", "adversarial", "mutation_heavy", "noise_heavy"]
        if a.quick else list(MIXES)
    )

    rows: List[Dict[str, Any]] = []
    total = len(sizes) * len(costs) * len(mixes) * repeats
    done  = 0

    for n in sizes:
        for cost in costs:
            for mix_name in mixes:
                weights = MIXES[mix_name]
                for rep in range(repeats):
                    mix_offset = int.from_bytes(
                        hashlib.sha256(mix_name.encode("utf-8")).digest()[:4],
                        "big",
                    ) % 1000
                    seed = (
                        a.seed + n * 3 + cost * 31 + rep * 997 + mix_offset
                    )
                    scenarios = generate_scenarios(seed, n, weights=weights)
                    recs = run_once(scenarios, cost, SWEEP_SYSTEMS, seed + 1)
                    for sys_name in SWEEP_SYSTEMS:
                        s = summarise(recs, sys_name, rep)
                        if s:
                            rows.append({
                                "scenarios":   n,
                                "proof_rounds": cost,
                                "mix":         mix_name,
                                "seed":        seed,
                                "rep":         rep,
                                "system":      sys_name,
                                "accuracy":    s["accuracy"],
                                "unsafe_allows": s["unsafe_allows"],
                                "cache_hit_rate": s["cache_hit_rate"],
                                "full_evaluations": s["full_evaluations"],
                                "work_units":  s["work_units"],
                                "mean_latency_ns": s["mean_latency_ns"],
                                "stale_commit_accepts": s.get("stale_commit_accepts", 0),
                                "commit_attempts": s["commit_attempts"],
                                "commit_accuracy": s["commit_accuracy"],
                                "effects": s["effects"],
                                "unsafe_effects": s["unsafe_effects"],
                                "total_work_units": s["total_work_units"],
                                "mean_commit_latency_ns": s[
                                    "mean_commit_latency_ns"
                                ],
                                "p95_latency_ns": s["p95_latency_ns"],
                                "p99_latency_ns": s["p99_latency_ns"],
                            })
                    done += 1
                    print(f"  sweep {done}/{total}: n={n} cost={cost} "
                          f"mix={mix_name} rep={rep}", flush=True)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        keys = list(rows[0].keys())
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
    repo_root = Path(__file__).resolve().parents[1]
    source_checksums = {
        str(path.relative_to(repo_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for source_root in (repo_root / "src", repo_root / "tests")
        for path in sorted(source_root.rglob("*.py"))
        if path.is_file()
    }
    dependencies = {}
    for package in ("pytest", "numpy", "matplotlib"):
        try:
            dependencies[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            dependencies[package] = None
    out_sha256 = hashlib.sha256(out.read_bytes()).hexdigest() if out.exists() else None
    manifest = {
        "generated_at_utc": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        "command_line": [sys.executable, *sys.argv],
        "seed": a.seed,
        "configuration": {
            "sizes": sizes,
            "proof_costs": costs,
            "repeats": repeats,
            "mixes": mixes,
            "systems": SWEEP_SYSTEMS,
            "quick": a.quick,
        },
        "source_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root,
            text=True, capture_output=True, check=False,
        ).stdout.strip() or None,
        "worktree_dirty": bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root,
            text=True, capture_output=True, check=False,
        ).stdout.strip()),
        "python_version": sys.version,
        "platform": platform.platform(),
        "dependencies": dependencies,
        "source_sha256": source_checksums,
        "output_sha256": {out.name: out_sha256},
        "manifest_checksum_scope": "sweep_manifest.json excludes its own checksum",
    }
    (out.parent / "sweep_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(f"Wrote {len(rows)} sweep rows → {out.resolve()}")

    # Quick aggregate print.
    print("\n── Sweep summary (mean across all configs, for reference) ──")
    for sys_name in SWEEP_SYSTEMS:
        sr = [r for r in rows if r["system"] == sys_name]
        if not sr:
            continue
        acc  = statistics.mean(r["accuracy"]        for r in sr)
        hit  = statistics.mean(r["cache_hit_rate"]  for r in sr)
        work = statistics.mean(r["work_units"]      for r in sr)
        print(f"  {sys_name:<26} acc={acc:.1%}  hit={hit:.1%}  work={work:.0f}")


if __name__ == "__main__":
    main()
