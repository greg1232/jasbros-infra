#!/bin/bash
#
# Prepare an SD card for a new speaker.  ./card.sh --help
#
# Runs here, not on a Pi — the whole point is that there isn't one yet.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 card.py "$@"
