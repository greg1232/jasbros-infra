# jasbros platform — design docs

Infrastructure as code for the machines in the house: the `gmk` server, the
Kubernetes cluster on it, and the Claude speakers on the shelves.

| Doc | What it decides |
|---|---|
| [overview.md](overview.md) | Goals, repo layout, the big picture, and the decisions log |
| [cli.md](cli.md) | `./jasbros` — the one command everything goes through |
| [gmk.md](gmk.md) | Provisioning gmk with Ansible, and the single-node k3s cluster on it |
| [claude-speaker-migration.md](claude-speaker-migration.md) | Moving `hey-claude` into `infra/claude-speaker` (step 1 done, without history) |
| [speaker-fleet-dashboard.md](speaker-fleet-dashboard.md) | A new dashboard, in a pod on gmk, showing every speaker at once |
| [display.md](display.md) | gmk's monitor: a standard Ubuntu desktop, logged in, with a browser on the app |
| [open-questions.md](open-questions.md) | What still needs a decision from a person |

Status: **draft, nothing built yet.** These docs come first; code follows
them, and when the two disagree the docs get fixed in the same change.
