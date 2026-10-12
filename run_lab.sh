#!/bin/sh
# Compatibility entry point for a non-overwriting timestamped reproduction run.
#
# Environment:
#   PYTHON_BIN=python3 Python executable (use .venv/bin/python when installed)
#   SCENARIOS=400      Benchmark scenario count
#   REPEATS=5          Independent benchmark repetitions
#   PROOF_ROUNDS=16    Deterministic simulated proof-work rounds
#   SEED=20261011      Reproducible random seed
#   RUN_SWEEP=1        Set to 0 to skip parameter sweep
#
# Example:
#   PYTHON_BIN=.venv/bin/python SCENARIOS=60 REPEATS=2 \
#     PROOF_ROUNDS=4 ./run_lab.sh
#
# Results are written to a new results/hardening-<UTC timestamp> directory.
# Existing results and paper bundles are never overwritten.
set -eu
cd "$(dirname "$0")"

PYTHON_BIN=${PYTHON_BIN:-python3}
SCENARIOS=${SCENARIOS:-400}
REPEATS=${REPEATS:-5}
PROOF_ROUNDS=${PROOF_ROUNDS:-16}
SEED=${SEED:-20261011}
RUN_SWEEP=${RUN_SWEEP:-1}

PYTHON=$PYTHON_BIN ./reproduce_research.sh \
    "$SEED" "$SCENARIOS" "$REPEATS" "$PROOF_ROUNDS" "$RUN_SWEEP"
