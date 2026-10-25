"""./jasbros — infrastructure as code for the jasbros platform.

See docs/cli.md.
"""

import argparse
import sys

from . import dashboard, doctor, gmk, k8s, speaker


def main() -> int:
    if sys.argv[1:2] == ["k8s"]:
        return k8s.kubectl(sys.argv[2:])
    parser = argparse.ArgumentParser(
        prog="jasbros", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for module in (doctor, gmk, k8s, speaker, dashboard):
        module.add_parser(sub)
    args = parser.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
