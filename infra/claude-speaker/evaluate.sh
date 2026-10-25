#!/bin/bash
#
# How good is the wake word, really?  ./evaluate.sh --help
#
# Runs on the Pi, because that is where the data is.

set -euo pipefail
cd "$(dirname "$0")"
TARGET=$(cat .deploy-target 2>/dev/null || true)
if [ -z "$TARGET" ]; then
    echo "I don't know which Pi to ask. Deploy once first:"
    echo "    ./deploy.sh normal@192.168.4.95"
    exit 1
fi
exec ssh "$TARGET" "cd claude-speaker && .venv/bin/python -u train/evaluate.py $*"
