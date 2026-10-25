#!/bin/bash
#
# Start the Claude Speaker.  ./start.sh --help
#
# Everything happens in start.py, which runs on the system Python and only
# the standard library — its first job is to build the virtualenv that the
# rest of the project needs.

set -euo pipefail
cd "$(dirname "$0")"
exec python3 start.py "$@"
