#!/bin/bash
#
# Listen to what woke the speaker, and say whether it was right.  --help
#
# Everything happens in label.py, on the system Python and the standard
# library — it fetches the clips from the Pi and opens a page in your
# browser. Nothing to install.

set -euo pipefail
cd "$(dirname "$0")"
exec python3 label.py "$@"
