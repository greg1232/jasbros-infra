#!/usr/bin/env bash
# Render the case. Needs OpenSCAD:  brew install --cask openscad
#
#     ./case/build.sh            base.3mf and lid.3mf, ready to open in Bambu Studio
#     ./case/build.sh --preview  ...and PNGs of each part, the assembly and a section
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
scad="$here/speaker-case.scad"
openscad="${OPENSCAD:-openscad}"

if ! command -v "$openscad" >/dev/null; then
    echo "No openscad on the PATH. brew install --cask openscad" >&2
    exit 1
fi

for part in base lid; do
    echo "==> $part.3mf"
    "$openscad" -o "$here/$part.3mf" -D "part=\"$part\"" "$scad" 2>&1 |
        grep -E "Status|WARNING|ERROR" || true
done

if [ "${1:-}" = "--preview" ]; then
    shot() {  # name part camera [projection]
        "$openscad" -o "$here/preview-$1.png" -D "part=\"$2\"" \
            --camera="$3" --imgsize=1000,850 --colorscheme=Tomorrow \
            --projection="${4:-p}" "$scad" >/dev/null 2>&1
        echo "==> preview-$1.png"
    }
    shot base     base     0,0,20,58,0,25,340
    shot lid      lid      0,0,41,0,0,0,290 o
    shot assembly assembly 0,0,21,66,0,200,340
    shot section  section  0,0,21,78,0,180,300 o
fi

echo
echo "Open the 3MFs in Bambu Studio. Print settings are in case/README.md."
