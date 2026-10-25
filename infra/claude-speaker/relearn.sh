#!/bin/bash
#
# Make the speaker learn from today, now rather than at four.  --help
#
# Everything happens in relearn.py, on the system Python and the standard
# library, because all it does is ask the Pi to do the work.

set -euo pipefail
cd "$(dirname "$0")"
exec python3 relearn.py "$@"
