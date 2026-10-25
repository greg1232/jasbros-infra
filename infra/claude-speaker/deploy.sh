#!/bin/bash
#
# Put the Claude Speaker on a Raspberry Pi.  ./deploy.sh --help
#
# Everything happens in deploy.py. This exists so the command is the same
# shape as ./start.sh, and so it works before you've thought about which
# python is on your path.

set -euo pipefail
cd "$(dirname "$0")"
exec python3 deploy.py "$@"
