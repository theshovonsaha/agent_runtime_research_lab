#!/bin/sh
set -eu

PYTHON=${PYTHON:-python3}
SEED=${1:-20261011}
SCENARIOS=${2:-400}
REPEATS=${3:-5}
PROOF_ROUNDS=${4:-16}
RUN_SWEEP=${5:-1}
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
RUN_DIR="results/hardening-${RUN_ID}"
SUFFIX=0

while ! mkdir "$RUN_DIR" 2>/dev/null; do
    SUFFIX=$((SUFFIX + 1))
    RUN_DIR="results/hardening-${RUN_ID}-${SUFFIX}"
done

"$PYTHON" -m unittest discover -s tests
"$PYTHON" -m pytest

"$PYTHON" src/benchmark.py \
    --out "$RUN_DIR" \
    --scenarios "$SCENARIOS" \
    --repeats "$REPEATS" \
    --proof-rounds "$PROOF_ROUNDS" \
    --seed "$SEED"

if [ "$RUN_SWEEP" = "1" ]; then
    "$PYTHON" src/sweep.py \
        --out "$RUN_DIR/sweep.csv" \
        --repeats 2 \
        --seed "$SEED" \
        --quick
fi

"$PYTHON" src/falsification.py --seed "$SEED" --out "$RUN_DIR"

"$PYTHON" src/analyze.py \
    --input "$RUN_DIR/run_metrics.csv" \
    --family "$RUN_DIR/family_accuracy.csv" \
    --out "$RUN_DIR/figures" \
    --report "$RUN_DIR/REPORT.md"

"$PYTHON" src/update_readme_metrics.py --root . --run-dir "$RUN_DIR"
"$PYTHON" src/paper.py --root . --out papers --run-dir "$RUN_DIR"

printf 'Reproduction complete.\nRun: %s\nPaper index: papers/LATEST.txt\n' "$RUN_DIR"
