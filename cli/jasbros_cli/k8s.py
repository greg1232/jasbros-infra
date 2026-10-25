"""./jasbros k8s — kubectl, always pointed at gmk's cluster."""

import os

from .common import KUBECONFIG, fail, need


def kubectl(arguments: list[str]) -> int:
    if not need("kubectl"):
        return 1
    if not KUBECONFIG.is_file():
        return fail("no kubeconfig yet — ./jasbros gmk kubeconfig")
    os.execvp("kubectl", ["kubectl", "--kubeconfig", str(KUBECONFIG),
                          *arguments])


def add_parser(sub) -> None:
    # Only for --help. main() hands `k8s ...` straight to kubectl before
    # argparse sees it, so kubectl's own flags pass through untouched.
    sub.add_parser("k8s", help="kubectl against gmk's cluster, e.g. "
                   "./jasbros k8s get pods -A")
