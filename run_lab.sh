#!/bin/sh
# run_lab.sh – Agent Runtime Research Lab v2 entry point
#
# Runs the full benchmark pipeline:
#   1. Unit tests
#   2. Benchmark (stateful scenarios, 7 runtime modes)
#   3. Sensitivity sweep (optional)
#   4. Analysis + chart generation
#   5. Paper generation
#
# Environment variables (all optional):
#   SCENARIOS=400      Number of scenarios to generate
#   REPEATS=5          Timing repetitions
#   PROOF_ROUNDS=16    Simulated proof-construction cost
#   SEED=20261011      Random seed
#   RUN_SWEEP=1        Set to 0 to skip sweep
#   SYSTEMS=""         Space-separated system names to benchmark (default: all)
#   PYTHON_BIN=python3 Python executable
#
# Safety: no network, no subprocesses spawned by benchmark code,
# no credentials, no external tools, synthetic data only.
#
# Quick smoke test:
#   SCENARIOS=60 REPEATS=2 PROOF_ROUNDS=4 ./run_lab.sh
#
# Publication-quality run:
#   SCENARIOS=2000 REPEATS=10 PROOF_ROUNDS=40 ./run_lab.sh
set -eu
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
SCENARIOS="${SCENARIOS:-400}"
REPEATS="${REPEATS:-5}"
PROOF_ROUNDS="${PROOF_ROUNDS:-16}"
SEED="${SEED:-20261011}"
RUN_SWEEP="${RUN_SWEEP:-1}"

mkdir -p results figures

echo "═══════════════════════════════════════════════════════"
echo "  Agent Runtime Research Lab v2"
echo "  Scenarios: ${SCENARIOS}  Repeats: ${REPEATS}  Proof rounds: ${PROOF_ROUNDS}"
echo "═══════════════════════════════════════════════════════"

echo ""
echo "▶  Step 1/5 — Unit tests"
"$PYTHON_BIN" -m unittest discover -s tests -v 2>&1

echo ""
echo "▶  Step 2/5 — Benchmark"
SYSTEMS_ARG=""
if [ -n "${SYSTEMS:-}" ]; then
  SYSTEMS_ARG="--systems ${SYSTEMS}"
fi
"$PYTHON_BIN" src/benchmark.py \
  --out results \
  --scenarios "${SCENARIOS}" \
  --repeats   "${REPEATS}" \
  --proof-rounds "${PROOF_ROUNDS}" \
  --seed      "${SEED}" \
  ${SYSTEMS_ARG}

echo ""
echo "▶  Step 3/5 — Sensitivity sweep"
if [ "${RUN_SWEEP}" = "1" ]; then
  SWEEP_QUICK=""
  if [ "${SCENARIOS}" -lt 200 ]; then SWEEP_QUICK="--quick"; fi
  "$PYTHON_BIN" src/sweep.py \
    --out results/sweep.csv \
    --repeats 3 \
    --seed "${SEED}" \
    ${SWEEP_QUICK}
else
  echo "  (skipped — set RUN_SWEEP=1 to enable)"
fi

echo ""
echo "▶  Step 4/5 — Analysis, charts + PDF Report"
"$PYTHON_BIN" src/analyze.py \
  --input  results/run_metrics.csv \
  --family results/family_accuracy.csv \
  --out    figures \
  --report results/REPORT.md

"$PYTHON_BIN" src/pdf_report.py || true

echo ""
echo "▶  Step 5/5 — Research paper generation (Markdown, TeX, PDF)"
"$PYTHON_BIN" src/paper.py --root . --out papers 2>/dev/null || \
  echo "  (paper.py skipped — not available or errored)"

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Done."
echo "  PDF Report: results/REPORT.pdf"
echo "  Paper PDF:  papers/<latest-run>/paper.pdf"
echo "  Report MD:  results/REPORT.md"
echo "  Charts:     figures/*.png, figures/*.svg"
echo "  Data:       results/decision_traces.csv"
echo "              results/run_metrics.csv"
echo "              results/family_accuracy.csv"
echo "═══════════════════════════════════════════════════════"
