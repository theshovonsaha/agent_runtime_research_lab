# Agent Runtime Research Lab

An experimental harness for evaluating typed agent proposals, policy decisions,
reusable proofs, and simulated effect authorization. It is not a production
authorization system or a production-safety certification.

## Research scope

The benchmark compares full recomputation, TAPR dependency caching, exact
snapshot caching, and intentionally unsafe negative controls. Proposal
correctness is measured separately from commit-boundary correctness and
simulated effects. The independent state-only oracle is implemented in
[`src/tapr/oracle.py`](src/tapr/oracle.py); it consumes world state and does
not call the policy evaluator, proof cache, or commit gate. This is
implementation-level independence, not independent validation of the policy
specification.

The authoritative runtime is [`src/tapr/`](src/tapr/). Top-level modules in
[`src/`](src/) remain compatibility import paths and are covered by tests.
The deterministic probes cover precedence dependencies, stale proofs,
policy rollover, resource/evidence changes, declared-dependency omissions,
scripted commit interleavings, and seeded workload families. The separate
threaded harness is stress evidence only; its scheduling-dependent counts are
not test assertions.

The atomicity evidence applies only to the in-process simulated store. It does
not establish atomicity for external adapters, distributed stores, crashes, or
partial external effects. Synthetic workloads and a repository-authored
oracle also leave policy-specification and workload-validity risks.

<!-- TAPR_RESULTS:BEGIN -->
## Data-derived benchmark snapshot

Generated from `results/hardening-20261012T065200Z` (seed `20261011`; source revision `e11585a8ce634252aef8a428f471fe5a20160946`). These are descriptive synthetic-run measurements, not production guarantees.

- Proposal accuracy: full recompute 100.00%; TAPR 100.00%.
- Commit-boundary accuracy: full recompute 100.00%; TAPR 100.00%.
- Mean commit false-denials per repetition: full recompute 0.00; TAPR 0.00.
- Cache-hit rates by reuse regime (weighted by decision count):

| Workload profile | Decisions | Full-recompute cache hit | TAPR cache hit | Difference (percentage points) |
|---|---:|---:|---:|---:|
| high_reuse | 1063 | 0.00% | 83.44% | 83.44 |
| low_reuse | 176 | 0.00% | 0.00% | 0.00 |

Raw metrics: [results/hardening-20261012T065200Z](results/hardening-20261012T065200Z/REPORT.md); integrity data: [results/hardening-20261012T065200Z/README_metrics.json](results/hardening-20261012T065200Z/README_metrics.json).
<!-- TAPR_RESULTS:END -->

## Reproduction

Python 3.14 is the currently exercised environment. Install the declared
dependencies and run both supported test entry points:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests
pytest
```

The reproduction entry point runs both test commands, the standard benchmark,
the optional deterministic sweep, Probes A-C, report/figure generation, and a
paper bundle. Every run creates a new `results/hardening-<UTC timestamp>`
directory and a new timestamped paper bundle. Existing raw result directories
and paper bundles are retained.

```sh
./run_lab.sh
```

Environment options support fixed, repeatable configurations:

```sh
PYTHON_BIN=.venv/bin/python \
SCENARIOS=400 REPEATS=5 PROOF_ROUNDS=16 SEED=20261011 \
RUN_SWEEP=1 ./run_lab.sh
```

Use a second seed and `SCENARIOS=2000` for the larger benchmark. The
reproduction run records its seed, command line, source revision, dirty
worktree status, Python/dependency versions, and SHA-256 checksums in its
manifests. Timing is recorded, not used as a pass/fail assertion.

The dedicated [falsification runner](src/falsification.py) and
[benchmark runner](src/benchmark.py) can also be invoked directly, but the
`run_lab.sh` path is the documented end-to-end workflow. See each runner's
`--help` output for options.

## Evidence and artifacts

- `results/hardening-*/`: per-run CSV/JSONL evidence, counterexamples,
  manifests, reports, and figures. Do not edit historical run outputs.
- `papers/run-*/`: timestamped manuscript draft, copied raw evidence, figures,
  claims ledger, submission checklist, and checksums. These are experimental
  drafts, not publication-ready submissions.
- [`RESEARCH_CHARTER.md`](RESEARCH_CHARTER.md): research principles,
  hypotheses, and known validity constraints.
- [`tests/`](tests/): regression, metamorphic, oracle-independence, and runtime
  tests.

The generated claims ledger maps measured claims to tests, result files, and
calculations. Independent replication, prior-art review, policy-specification
review, and validation against real external effects remain outstanding.

## Interactive tooling

The repository also contains a local interactive CLI and model adapters for
experimentation. Those optional components are outside the deterministic
benchmark and are not included in the claims generated above.
