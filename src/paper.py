#!/usr/bin/env python3
"""Create a versioned manuscript bundle from benchmark outputs; no results are fabricated."""
import argparse, csv, json, hashlib, importlib.metadata, platform, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev
from math import sqrt

VERSION = "2.0.0"
LABELS = {
    "full_recompute": "Full recompute",
    "tapr_dep_cache": "TAPR dep-cache",
    "exact_snapshot_cache": "Exact snapshot cache",
    "coarse_cache": "Coarse cache (unsafe control)",
    "stale_proof_accept": "Stale proof accept (unsafe control)",
    "no_commit_gate": "No commit gate (unsafe control)",
    "pathless_policy": "Pathless policy (unsafe control)",
}

def rows(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))

def sha(path):
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()

def fmt(x, digits=3):
    return f"{x:.{digits}f}"

def mean_ci(values):
    if len(values) < 2:
        return mean(values) if values else 0.0, 0.0
    t95 = (0.0, 12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365,
           2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
           2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069,
           2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042)
    df = len(values) - 1
    critical = t95[df] if df < len(t95) else 1.96
    return mean(values), critical * stdev(values) / sqrt(len(values))

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', default='.')
    p.add_argument('--out', default='papers')
    p.add_argument('--run-dir', default='results', help='Benchmark run directory relative to --root, or absolute path')
    a = p.parse_args()

    root = Path(a.root).resolve()
    outroot = root / a.out
    outroot.mkdir(parents=True, exist_ok=True)

    run_dir = (root / a.run_dir).resolve()
    metrics_path = run_dir / 'run_metrics.csv'
    trace_path = run_dir / 'decision_traces.csv'
    sweep_path = run_dir / 'sweep.csv'

    if not metrics_path.exists():
        raise SystemExit(f"Missing {metrics_path}. Run the benchmark first.")

    metrics = rows(metrics_path)
    if not metrics:
        raise SystemExit('No metric rows found. Run the benchmark first.')

    run_id = datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S%fZ')
    out = outroot / run_id
    out.mkdir(parents=True, exist_ok=False)

    manifest_path = run_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    falsification_manifest_path = run_dir / 'falsification_manifest.json'
    falsification_manifest = (
        json.loads(falsification_manifest_path.read_text())
        if falsification_manifest_path.exists() else {}
    )
    git_revision = subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=root, text=True,
        capture_output=True, check=False
    ).stdout.strip() or None
    worktree_status = subprocess.run(
        ['git', 'status', '--short'], cwd=root, text=True,
        capture_output=True, check=False
    ).stdout.strip()
    input_hashes = {
        str(q.relative_to(run_dir)): sha(q)
        for q in sorted(run_dir.rglob('*'))
        if q.is_file() and q.name not in {
            'manifest.json', 'falsification_manifest.json'
        }
    }
    manifest.update({
        'lab_version': VERSION,
        'paper_run_id': run_id,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'command_line': [sys.executable, *sys.argv],
        'python_runtime': sys.version,
        'platform': platform.platform(),
        'benchmark_run_dir': str(run_dir),
        'git_revision': git_revision,
        'git_worktree_dirty': bool(worktree_status),
        'git_worktree_status': worktree_status,
        'dependencies': {},
        'input_sha256': input_hashes,
        'falsification_manifest': falsification_manifest,
    })
    manifest['dependencies'] = {}
    for distribution in ('pytest', 'numpy', 'matplotlib'):
        try:
            manifest['dependencies'][distribution] = (
                importlib.metadata.version(distribution)
            )
        except importlib.metadata.PackageNotFoundError:
            manifest['dependencies'][distribution] = None
    (out / 'run_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')

    # Copy exact inputs and generated figures for an auditable, self-contained run
    (out / 'data').mkdir()
    for q in sorted(run_dir.iterdir()):
        if q.is_file() and q.suffix in {'.csv', '.json', '.jsonl'}:
            shutil.copy2(q, out / 'data' / q.name)

    (out / 'figures').mkdir()
    if (run_dir / 'figures').exists():
        for q in (run_dir / 'figures').iterdir():
            if q.is_file() and q.suffix in {'.png', '.svg'}:
                shutil.copy2(q, out / 'figures' / q.name)

    # Calculate average across repetitions per system
    systems = []
    for r in metrics:
        if r['system'] not in systems:
            systems.append(r['system'])

    sys_aggs = {}
    for s in systems:
        s_rows = [r for r in metrics if r['system'] == s]
        sys_aggs[s] = {
            'accuracy': mean(float(r['accuracy']) for r in s_rows),
            'unsafe_allow': mean(float(r['unsafe_allows']) for r in s_rows),
            'false_deny': mean(float(r['false_denies']) for r in s_rows),
            'cache_hit': mean(float(r['cache_hit_rate']) for r in s_rows),
            'full_evaluations': mean(float(r['full_evaluations']) for r in s_rows),
            'latency_ns': mean(float(r['mean_latency_ns']) for r in s_rows),
            'commit_accuracy': mean(
                float(r.get('commit_accuracy', 0)) for r in s_rows
            ),
            'stale_commit_accepts': mean(
                float(r.get('stale_commit_accepts', 0)) for r in s_rows
            ),
            'commit_false_denies': mean(
                float(r.get('commit_false_denies', 0)) for r in s_rows
            ),
            'effects': mean(float(r.get('effects', 0)) for r in s_rows),
            'unsafe_effects': mean(
                float(r.get('unsafe_effects', 0)) for r in s_rows
            ),
            'total_work_units': mean(
                float(r.get('total_work_units', r.get('work_units', 0)))
                for r in s_rows
            ),
            'commit_p95_ns': mean(
                float(r.get('p95_commit_latency_ns', 0)) for r in s_rows
            ),
            'accuracy_ci': mean_ci(
                [float(r['accuracy']) for r in s_rows]
            ),
            'commit_ci': mean_ci(
                [float(r.get('commit_accuracy', 0)) for r in s_rows]
            ),
        }

    table = [
        '| System | Proposal accuracy (95% t CI) | Commit accuracy (95% t CI) | Unsafe ALLOW | Commit false denies | Stale accepts | Effects | Unsafe effects | Cache hit | Full evals | Total work | Proposal mean μs | Commit p95 μs |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|'
    ]
    for s in systems:
        r = sys_aggs[s]
        table.append(
            f"| {LABELS.get(s, s)} | {100*r['accuracy']:.2f}% "
            f"[{100*(r['accuracy']-r['accuracy_ci'][1]):.2f}, "
            f"{100*(r['accuracy']+r['accuracy_ci'][1]):.2f}] | "
            f"{100*r['commit_accuracy']:.2f}% "
            f"[{100*(r['commit_accuracy']-r['commit_ci'][1]):.2f}, "
            f"{100*(r['commit_accuracy']+r['commit_ci'][1]):.2f}] | "
            f"{r['unsafe_allow']:.1f} | {r['commit_false_denies']:.1f} | "
            f"{r['stale_commit_accepts']:.1f} | "
            f"{r['effects']:.1f} | {r['unsafe_effects']:.1f} | "
            f"{100*r['cache_hit']:.1f}% | {r['full_evaluations']:.0f} | "
            f"{r['total_work_units']:.1f} | {r['latency_ns']/1e3:.2f} | "
            f"{r['commit_p95_ns']/1e3:.2f} |"
        )

    tapr = sys_aggs.get('tapr_dep_cache', {})
    full = sys_aggs.get('full_recompute', {})
    full_eval_baseline = max(1.0, float(full.get('full_evaluations', 1.0)))
    reduction = (1.0 - float(tapr.get('full_evaluations', 0.0)) / full_eval_baseline) * 100.0
    fuzz_metrics_path = run_dir / 'probe_c_metrics.csv'
    fuzz_metrics = rows(fuzz_metrics_path) if fuzz_metrics_path.exists() else []
    probe_summary_path = run_dir / 'falsification_summary.json'
    probe_summary = (
        json.loads(probe_summary_path.read_text())
        if probe_summary_path.exists() else {}
    )
    probe_a_path = run_dir / 'probe_a_atomic.json'
    probe_a = json.loads(probe_a_path.read_text()) if probe_a_path.exists() else {}

    profile_names = list(dict.fromkeys(row['profile'] for row in fuzz_metrics))
    profile_table = [
        '| Workload profile | Full accuracy | TAPR accuracy | Full commit accuracy | TAPR commit accuracy | Full/TAPR cache hit | Full/TAPR total work | Full/TAPR mean proposal μs |',
        '|---|---:|---:|---:|---:|---:|---:|---:|',
    ]
    break_even_profiles = []
    profile_system_means = {}
    for profile in profile_names:
        profile_system_means[profile] = {}
        for system in ('full_recompute', 'tapr_dep_cache'):
            selected = [
                row for row in fuzz_metrics
                if row['profile'] == profile and row['system'] == system
            ]
            if selected:
                decisions = sum(int(row['decisions']) for row in selected)
                commit_attempts = sum(
                    int(row['commit_attempts']) for row in selected
                )
                profile_system_means[profile][system] = {
                    'accuracy': sum(
                        int(row['correct']) for row in selected
                    ) / decisions,
                    'commit_accuracy': (
                        sum(int(row['commit_gate_correct']) for row in selected)
                        / commit_attempts if commit_attempts else 0.0
                    ),
                    'total_work_units': sum(
                        float(row['total_work_units']) for row in selected
                    ),
                    'mean_latency_ns': sum(
                        float(row['mean_latency_ns']) * int(row['decisions'])
                        for row in selected
                    ) / decisions,
                    'cache_hit_rate': sum(
                        int(row['cache_hits']) for row in selected
                    ) / decisions,
                }
        full_profile = profile_system_means[profile].get('full_recompute')
        tapr_profile = profile_system_means[profile].get('tapr_dep_cache')
        if full_profile and tapr_profile:
            if tapr_profile['mean_latency_ns'] < full_profile['mean_latency_ns']:
                break_even_profiles.append(profile)
            profile_table.append(
                f"| {profile} | {full_profile['accuracy']:.1%} | "
                f"{tapr_profile['accuracy']:.1%} | "
                f"{full_profile['commit_accuracy']:.1%} | "
                f"{tapr_profile['commit_accuracy']:.1%} | "
                f"{full_profile['cache_hit_rate']:.1%}/"
                f"{tapr_profile['cache_hit_rate']:.1%} | "
                f"{full_profile['total_work_units']:.1f}/"
                f"{tapr_profile['total_work_units']:.1f} | "
                f"{full_profile['mean_latency_ns']/1e3:.2f}/"
                f"{tapr_profile['mean_latency_ns']/1e3:.2f} |"
            )

    per_seed_table = [
        '| Seed | Profile | Size | Replicate | Full proposal acc. | TAPR proposal acc. | Full commit acc. | TAPR commit acc. | Full/TAPR cache hit |',
        '|---:|---|---:|---:|---:|---:|---:|---:|---:|',
    ]
    replicate_keys = sorted({
        (row['seed'], row['profile'], row['scenario_size'], row['rep'])
        for row in fuzz_metrics
    })
    for seed, profile, size, rep in replicate_keys:
        paired = {
            row['system']: row for row in fuzz_metrics
            if (row['seed'], row['profile'], row['scenario_size'], row['rep'])
            == (seed, profile, size, rep)
            and row['system'] in {'full_recompute', 'tapr_dep_cache'}
        }
        if {'full_recompute', 'tapr_dep_cache'} <= paired.keys():
            full_row, tapr_row = paired['full_recompute'], paired['tapr_dep_cache']
            per_seed_table.append(
                f"| {seed} | {profile} | {size} | {rep} | "
                f"{float(full_row['accuracy']):.1%} | "
                f"{float(tapr_row['accuracy']):.1%} | "
                f"{float(full_row['commit_accuracy']):.1%} | "
                f"{float(tapr_row['commit_accuracy']):.1%} | "
                f"{float(full_row['cache_hit_rate']):.1%}/"
                f"{float(tapr_row['cache_hit_rate']):.1%} |"
            )

    family_path = run_dir / 'family_accuracy.csv'
    family_results = rows(family_path) if family_path.exists() else []
    family_table = [
        '| Scenario family | System | Proposal accuracy |',
        '|---|---|---:|',
    ]
    for result in family_results:
        family_table.append(
            f"| {result['family']} | {LABELS.get(result['system'], result['system'])} | "
            f"{float(result['accuracy']):.1%} |"
        )

    runtime_lines = [
        f"- Source revision: `{manifest.get('git_revision')}`; "
        f"worktree dirty: `{manifest.get('git_worktree_dirty')}`.",
        f"- Runtime: `{manifest.get('python_runtime', 'not recorded').splitlines()[0]}`; "
        f"platform: `{manifest.get('platform', 'not recorded')}`.",
        f"- Hardware identifiers: machine `{manifest.get('machine', 'not recorded')}`; "
        f"processor `{manifest.get('processor', 'not recorded')}`.",
        f"- Dependencies: `{json.dumps(manifest.get('dependencies', {}), sort_keys=True)}`.",
        f"- Benchmark configuration: `{json.dumps({k: manifest.get(k) for k in ('seed', 'scenarios', 'repeats', 'proof_rounds', 'systems')}, sort_keys=True)}`.",
        f"- Probe seed/configuration: `{json.dumps(probe_summary, sort_keys=True)}`.",
    ]
    image_names = [
        'decision_accuracy.png', 'unsafe_allows.png', 'cache_hit_rate.png',
        'latency_ci.png', 'work_savings.png', 'commit_gate.png',
        'family_heatmap.png', 'safety_efficiency.png',
    ]
    figure_lines = []
    for name in image_names:
        if (out / 'figures' / name).exists():
            figure_lines.extend([f'![{name}](figures/{name})', ''])

    md = f'''# Dependency-aware proof reuse for agent runtime governance: a stateful execution benchmark

**Manuscript status:** automatically generated experimental draft; not submission-certified.  
**Lab version:** {VERSION} · **Run ID:** {run_id} · **UTC:** {manifest['generated_at_utc']}  
**Study type:** offline synthetic stateful systems experiment.

## Abstract

**Research question.** Under identical typed event histories and policy semantics, can dependency-aware proof reuse preserve proposal decisions and commit authorization while reducing computational work relative to full recomputation? The study compares full recomputation, TAPR dependency caching, exact-snapshot caching, and intentionally faulty negative controls. Results below are measured only for this run and synthetic simulator. They do not establish production safety, general superiority, or novelty.

## 1. Falsifiable hypotheses

- **H1, proposal equivalence:** TAPR proposal verdicts match the independent state-only evaluator for all generated cases.
- **H2, commit safety:** a proof established before a relevant state change cannot authorize a later effect; the check/effect transaction serializes with state mutation.
- **H3, selective efficiency:** proof reuse reduces work in reusable-dependency regimes, but validation costs or low reuse can make TAPR slower than recomputation.

## 2. Methods

### 2.1 Runtime variants and operational definitions

- **Full recompute:** evaluate current typed policy conditions for every proposal; recompute at commit.
- **TAPR dependency cache:** reuse a proof only when its declared facts, policy version, and in-process policy identity still match; revalidate/recompute at commit.
- **Exact snapshot cache:** use a hash of the complete world snapshot and action identity.
- **Negative controls:** coarse action/target cache, stale-proof acceptance, no commit gate, and pathless policy. These are intentionally unsafe comparators, not candidate implementations.
- The authoritative implementation is `src/tapr/`; legacy `src/*.py` import-compatible modules re-export it. Both import paths are covered by tests.

### 2.2 Dependency contract

The ordered policy evaluator accumulates facts actually inspected by each
nonmatching rule and the matching rule. A cached verdict therefore pins the
matched rule's inputs plus inputs read while establishing that earlier rules
did not fire. The rule functions supply those reads; `required_fact_keys`
replays that evaluator contract rather than maintaining a second handwritten
precedence table. Irrelevant metadata is intentionally excluded when it is not
read by any applicable rule. This contract is authored in the same project as
the evaluator and requires external policy-specification review.

### 2.3 Independent oracle and its limits

`tapr.oracle.evaluate` computes expected proposal verdict and commit authorization directly from current `WorldState` facts. It imports the state model only; it does not call the policy evaluator, dependency validator, proof cache, or commit gate. A structural regression test checks those imports and a runtime test replaces `Policy.evaluate` with a failing stub while the oracle continues to evaluate. Independence is implementation-level, not specification-level: both evaluator and oracle encode a human-authored contract in this repository, so shared misunderstanding remains possible.

### 2.4 Scenario and mutation procedure

Stateful scenarios are event streams of proposals, commits, policy rollovers, and versioned world mutations. Probe B changes authorization, scope, approvals, evidence revisions, protected/held resources, sensitive-read/retry/delegation history, policy version, irrelevant metadata, and resource identity. Omitted dependencies are fault-injected. Probe C uses recorded seeds across nine workload profiles, two scenario sizes (24, 64), two repetitions, and all seven registered runtime variants. Profiles vary reuse/noise, dependency fan-out, path behavior, policy rollovers, concurrent-mutation scenarios, and resource substitution. Complete serialized inputs and per-decision records are included in `data/`.

### 2.5 Atomic effect model

`AtomicWorldStore` holds a single-process mutex from reading current state through gate validation and recording a simulated effect. A deterministic barrier test pauses after authorization and attempts revocation before effect recording; the mutation must block until the effect is serialized. Competing compare-and-swap updates are tested with the same version and exactly one may succeed. Probe A also compares both scripted serial histories—revocation-before-commit and effect-before-deferred-revocation—with full recomputation. These results demonstrate only in-process simulation behavior, not database transactionality, distributed consensus, crash recovery, or external side-effect atomicity. Repeated proof use is permitted while dependencies remain valid; a failed effect is recorded as a failed attempt and is not counted as an occurred effect.

### 2.6 Environment, configuration, and reproducibility

{chr(10).join(runtime_lines)}

Reproduce with `PYTHON=.venv/bin/python ./reproduce_research.sh <seed> 400 5 16 1`. The positional arguments are seed, scenario count, repetitions, proof-work rounds, and whether to run the quick sensitivity sweep. The script runs both `unittest` and `pytest`, then the benchmark, deterministic parameter sweep, three probes, figures, README metric refresh, and paper bundle. It creates a new timestamped result directory. The proof-work measure is a deterministic SHA-256 CPU-work proxy, not formal verification. Per-run manifests contain command lines, source revision, dirty-worktree status, dependency versions, configuration, and checksums.

## 3. Results

### 3.1 Benchmark summary

Proposal accuracy and commit-boundary accuracy are reported separately. `Effects` counts simulated in-memory effects actually recorded after runtime approval; `Unsafe effects` counts effects where the independent oracle says commit authorization was false. They are not measurements of a real external adapter.

{chr(10).join(table)}

The TAPR full-evaluation reduction relative to recomputation is {reduction:.2f}% in this run, computed as `1 - mean(TAPR full_evaluations) / mean(full_recompute full_evaluations)`. This work-count proxy excludes the engineering cost of discovering and maintaining dependency declarations; latency is reported separately.

### 3.2 Workload-profile and break-even outcomes

Each profile row aggregates the per-seed/per-repetition observations in the raw CSV. A profile counts as a latency break-even only when mean TAPR proposal latency is lower than mean full-recompute latency in this measured environment. Profiles below are not pooled into a universal estimate.

{chr(10).join(profile_table)}

Break-even by this criterion was observed in {len(break_even_profiles)} of {len(profile_names)} profiles: {', '.join(break_even_profiles) if break_even_profiles else 'none'}. This is descriptive and machine-specific. The exact per-seed observations are tabulated in the raw data and summarized here:

{chr(10).join(per_seed_table)}

### 3.3 Scenario-family results

{chr(10).join(family_table)}

### 3.4 Probe outcomes and mutation sensitivity

Probe summary: `{json.dumps(probe_summary, sort_keys=True)}`.

Probe B/C disagreement totals include intentionally unsafe negative controls.
The summary separates those from full recomputation, TAPR, and exact-snapshot
candidate variants. Probe B/C case-level outcomes and minimized
counterexamples—including expected negative-control failures—are preserved in
the CSV/JSONL files. A zero candidate disagreement count is not proof of
completeness; targeted fault injection and mutation testing show sensitivity to
specific faults, not that the oracle or scenario grammar is exhaustive.

The deterministic commit/mutation interleaving record is
`{json.dumps(probe_a.get('commit_mutation_race', {}), sort_keys=True)}`.
The separate threaded stress harness is not used as a pass/fail test.

## 4. Figures

Every included chart is generated by `src/analyze.py` from this run's raw CSVs. Figure files are copied into the bundle; their SHA-256 hashes appear in `paper_manifest.json`.

{chr(10).join(figure_lines)}

## 5. Uncertainty and sample units

The main table's two-sided 95% Student-t intervals are computed over independent benchmark repetition aggregates per system, not over individual decisions. The independent sample size is the number of repetitions recorded for that system; small replication counts make intervals unstable and do not support broad population claims. Fuzz/probe seeds and scenario-level outcomes are retained individually; the profile table is descriptive and no inferential interval is asserted for it. Proposal and commit outcomes are paired within the same generated scenario histories.

## 6. Failure analysis, threats, and limitations

1. The input is typed and synthetic. No LLM, intent extraction, real policy engine, network, or external effect adapter participates.
2. The evaluator and oracle are separately implemented but share a repository-authored policy specification; semantic completeness has not received independent domain review.
3. The dependency contract is manually authored. The omission fault test verifies rejection of the tested omission; it cannot prove no relevant fact was omitted from all rules.
4. The atomic store is one-process/in-memory. It cannot establish safety under distributed stores, process crashes, adapter timeouts, partial external writes, or cross-service transactions.
5. Work units proxy deterministic hash rounds. Cache-hit and work-count reductions omit dependency discovery, storage, synchronization, audit, network, and maintenance costs.
6. Wall-clock latency is a local Python microbenchmark subject to runtime and machine noise; no external baseline or cross-machine comparison is claimed.
7. Generated cases have bounded seeds, sizes, graph shapes, mutation rates, and event types. No claim of exhaustive falsification or production safety follows.
8. No independent replication or completed prior-art/novelty review has been conducted.

## 7. Artifact inventory and integrity

The bundle includes the raw benchmark/probe CSV, JSONL scenarios and counterexamples, manifests, figures, claims ledger, and incomplete submission checklist. `run_manifest.json` records the source revision, worktree status, configuration, environment, and run-input checksums; `paper_manifest.json` hashes all files in this bundle other than itself. The falsification manifest also records pre-existing historical artifact checksums; historical result directories are not overwritten.

## 8. Submission readiness

This is an engineering research artifact, **not** a submission-ready or safety certification. Independent replication, policy-specification review, prior-art analysis, venue formatting, and broader/realistic adapter experiments remain outstanding.
'''
    (out / 'paper.md').write_text(md)

    checklist = {
        'status': 'NOT_CERTIFIED_FOR_SUBMISSION',
        'checks': [
            {'item': 'venue template and requirements reviewed', 'status': 'not_done'},
            {'item': 'all claims independently audited against raw outputs', 'status': 'partial_local_calculations_only'},
            {'item': 'policy contract independently reviewed', 'status': 'not_done'},
            {'item': 'prior-art and novelty review complete', 'status': 'not_done'},
            {'item': 'independent replication completed', 'status': 'not_done'},
            {'item': 'distributed/external effect adapter validation', 'status': 'not_done'},
            {'item': 'all three probes reproduced from documented clean checkout', 'status': 'local_run_pending_external_replication'},
        ]
    }
    (out / 'submission_checklist.json').write_text(json.dumps(checklist, indent=2) + '\n')

    tapr_accuracy = tapr.get('accuracy', 0.0)
    tapr_commit_accuracy = tapr.get('commit_accuracy', 0.0)
    safe_variants = ('full_recompute', 'tapr_dep_cache', 'exact_snapshot_cache')
    probe_b_candidate_disagreements = sum(
        probe_summary.get('probe_b_disagreements_by_system', {}).get(system, 0)
        for system in safe_variants
    )
    probe_c_candidate_disagreements = sum(
        probe_summary.get('probe_c_disagreements_by_system', {}).get(system, 0)
        for system in safe_variants
    )
    claims = {
        'run_id': run_id,
        'claims': [
            {
                'claim': f'TAPR proposal accuracy was {tapr_accuracy:.6f} on this benchmark run',
                'status': 'calculated_from_run',
                'evidence': 'data/run_metrics.csv',
                'calculation': 'arithmetic mean of per-repetition tapr_dep_cache accuracy',
                'tests': ['tests/test_research_hardening.py'],
                'scope': 'typed synthetic scenarios only',
            },
            {
                'claim': f'TAPR commit accuracy was {tapr_commit_accuracy:.6f} on this benchmark run',
                'status': 'calculated_from_run',
                'evidence': 'data/run_metrics.csv',
                'calculation': 'arithmetic mean of per-repetition tapr_dep_cache commit_accuracy',
                'tests': [
                    'tests/test_research_hardening.py',
                    'tests/test_atomic_effect_boundary.py',
                ],
                'scope': 'in-memory simulated effect boundary only',
            },
            {
                'claim': (
                    'Mean TAPR commit false-denials per repetition were '
                    f"{tapr.get('commit_false_denies', 0.0):.6f}"
                ),
                'status': 'calculated_from_run',
                'evidence': 'data/run_metrics.csv',
                'calculation': (
                    'arithmetic mean of per-repetition tapr_dep_cache '
                    'commit_false_denies'
                ),
                'tests': [
                    'tests/test_policy_mismatch_package.py',
                    'tests/test_benchmark_runtime_isolation.py',
                ],
                'scope': 'in-memory simulated commit boundary only',
            },
            {
                'claim': (
                    f'TAPR full evaluations were reduced by '
                    f'{reduction:.6f}% relative to full recomputation'
                ),
                'status': 'calculated_from_run',
                'evidence': 'data/run_metrics.csv',
                'calculation': '100 * (1 - mean(TAPR full_evaluations) / mean(full_recompute full_evaluations))',
                'tests': [
                    'tests/test_benchmark.py::BenchmarkRunnerTests::test_run_once_produces_records',
                    'tests/test_research_hardening.py::test_irrelevant_fact_change_preserves_reuse',
                ],
                'scope': 'does not include dependency discovery or maintenance work',
            },
            {
                'claim': (
                    f'Probe B recorded {probe_b_candidate_disagreements} '
                    'candidate-variant oracle disagreements'
                ),
                'status': 'counted_from_run',
                'evidence': 'data/probe_b_cases.jsonl and data/falsification_summary.json',
                'calculation': (
                    'sum per-system disagreements for full_recompute, '
                    'tapr_dep_cache, and exact_snapshot_cache'
                ),
                'tests': [
                    'tests/test_research_hardening.py',
                    'tests/test_precedence_dependencies.py',
                ],
            },
            {
                'claim': (
                    f'Probe C recorded {probe_c_candidate_disagreements} '
                    'candidate-variant proposal/commit oracle disagreements'
                ),
                'status': 'counted_from_run',
                'evidence': 'data/probe_c_cases.csv',
                'calculation': (
                    'sum per-system proposal/commit disagreements for '
                    'full_recompute, tapr_dep_cache, and exact_snapshot_cache'
                ),
                'tests': [
                    'tests/test_precedence_dependencies.py',
                    'tests/test_benchmark_runtime_isolation.py',
                ],
            },
            {
                'claim': f'TAPR was faster than full recomputation in {len(break_even_profiles)} of {len(profile_names)} measured profiles',
                'status': 'calculated_from_run',
                'evidence': 'data/probe_c_metrics.csv',
                'calculation': (
                    'compare decision-weighted per-profile mean_latency_ns '
                    'for tapr_dep_cache vs full_recompute'
                ),
                'tests': [
                    'tests/test_benchmark.py::BenchmarkRunnerTests::test_run_once_produces_records',
                ],
                'scope': 'descriptive for this machine and generated workloads',
            },
            *[
                {
                    'claim': (
                        f"TAPR cache-hit rate in {profile} was "
                        f"{profile_system_means[profile]['tapr_dep_cache']['cache_hit_rate']:.6f}"
                    ),
                    'status': 'calculated_from_run',
                    'evidence': 'data/probe_c_metrics.csv',
                    'calculation': (
                        'sum(cache_hits) / sum(decisions) over matching '
                        'profile/system rows'
                    ),
                    'tests': ['tests/test_precedence_dependencies.py'],
                    'scope': 'descriptive for the recorded synthetic workload',
                }
                for profile in ('high_reuse', 'low_reuse')
            ],
        ],
    }
    (out / 'claims_ledger.json').write_text(json.dumps(claims, indent=2) + '\n')
    (outroot / 'LATEST.txt').write_text(run_id + '\n')

    bundle_checksums = {
        str(path.relative_to(out)): sha(path)
        for path in sorted(out.rglob('*'))
        if path.is_file() and path.name != 'paper_manifest.json'
    }
    paper_manifest = {
        'run_id': run_id,
        'seed': manifest.get('seed'),
        'command_line': [sys.executable, *sys.argv],
        'python_runtime': sys.version,
        'source_revision': manifest.get('git_revision'),
        'worktree_dirty': manifest.get('git_worktree_dirty'),
        'run_manifest_sha256': sha(out / 'run_manifest.json'),
        'bundle_artifact_sha256': bundle_checksums,
        'manifest_checksum_scope': 'paper_manifest.json intentionally excludes itself',
    }
    (out / 'paper_manifest.json').write_text(
        json.dumps(paper_manifest, indent=2, sort_keys=True) + '\n'
    )

    print(f"Paper artifacts written to {out}")

if __name__ == '__main__':
    main()
