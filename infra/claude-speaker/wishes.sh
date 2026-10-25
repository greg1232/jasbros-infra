#!/bin/bash
#
# What the speaker has been asked for and can't do.  ./wishes.sh --help
#
# Prefers the project's virtualenv, because the wish parser is shared with
# the code that runs on the Pi and that imports config. Falls back to the
# system Python, which is enough for --help.

set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python wishes.py "$@"
fi
exec python3 wishes.py "$@"
