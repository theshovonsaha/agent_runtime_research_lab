#!/bin/sh
set -eu
cd "$(dirname "$0")"

# Forward to the v2 lab runner
exec ./run_lab.sh "$@"
