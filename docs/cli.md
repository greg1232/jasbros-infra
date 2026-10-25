# `./jasbros` — the CLI

## Shape

`./jasbros` is a bash script and does three things only:

1. `cd` to the repo root.
2. Make `.venv/` with the system `python3` if it is missing or out of date.
   It checks a hash of `cli/pyproject.toml` stored in `.venv/.jasbros-hash`,
   and reinstalls (`pip install -e cli/`) when the hash changes.
3. `exec .venv/bin/python -m jasbros_cli "$@"`.

All the logic is in Python (`cli/jasbros_cli/`). It uses `argparse` with
subcommands, the same style as hey-claude's laptop tools, and none of it
should feel like a framework.

`cli/pyproject.toml` dependencies: `ansible-core` (pinned), `pyyaml`. Ansible
collections (`kubernetes.core`, `community.general`, `ansible.posix`) are
pinned in `infra/gmk/requirements.yml` and installed into `.venv/` by the
shim on the same hash check.

External tools the CLI expects on the laptop: `ssh`, `helm`, `kubectl`, and
`docker` with `buildx`, logged in to Docker Hub for pushes.
`./jasbros doctor` checks for each one and prints the `brew install` line
for anything missing.

## Commands

```
./jasbros doctor                      check the laptop has what it needs

./jasbros gmk provision [--check] [--tags k3s]   run infra/gmk/site.yml
./jasbros gmk status                  node, pods, CPU/mem allocatable
./jasbros gmk ssh                     ssh to gmk at its inventory address
./jasbros gmk find [--update]         scan for the host called gmk; --update rewrites the inventory
./jasbros gmk kubeconfig              (re)fetch ~/.kube/jasbros-gmk.yaml

./jasbros k8s <kubectl args>          kubectl with the jasbros kubeconfig
./jasbros job run NAME --image I --cpus 4 -- CMD...   launch a k8s Job on gmk
./jasbros job logs NAME | list | rm NAME

./jasbros speaker list                every speaker in infra/inventory.yaml
./jasbros speaker card NAME [--flash] prepare an SD card     (wraps card.py)
./jasbros speaker deploy [NAME|all]   put the code on a Pi   (wraps deploy.py)
./jasbros speaker start|status|stop [NAME]                   (wraps start.py)
./jasbros speaker label|relearn|wishes|evaluate [NAME]
./jasbros speaker scan [--update]     find Pis on 192.168.4.0/22 by hostname; --update rewrites their IPs

./jasbros dashboard build             buildx the fleet dashboard image (amd64), push to Docker Hub
./jasbros dashboard deploy            helm upgrade --install, values from inventory
./jasbros dashboard open              open http://<gmk's address>/speakers
```

Rules:

- **Every command takes `--dry-run`** where it can (`--check --diff` for
  Ansible, `helm diff`/`--dry-run=server` for Helm).
- **Asking for sudo is explicit.** `gmk provision` reads gmk's sudo password
  from `~/.config/jasbros/gmk-become-pass` (or `$JASBROS_BECOME_PASS_FILE`),
  and refuses the file unless it's `chmod 600`. Without the file, it prompts
  (`--ask-become-pass`). Passwordless sudo is never
  turned on behind anyone's back.
- **Long installs show progress.** `gmk provision` follows gmk's
  `/var/log/dpkg.log` over SSH and prints each package as it's unpacked
  and set up (`[gmk apt] …`). `--quiet` turns that off.
- **Find it again when it moves.** If a host doesn't answer at its inventory
  address, the CLI says so and suggests `find`/`scan --update`. It doesn't
  rescan quietly on its own, because a 1,000-address scan shouldn't be a
  surprise.
- **Names, not addresses.** Every speaker and host is named in
  `infra/inventory.yaml`. A unique prefix of a name is enough (`bed` →
  `bedroom`), same as hey-claude's `--pi` today. If a prefix matches more
  than one name, that's an error and the CLI doesn't guess.
- **Wrapping, not rewriting.** The `speaker` subcommands call the existing
  scripts in `infra/claude-speaker/` and pass the resolved `user@host`. Those
  scripts keep working when run directly.

## `infra/inventory.yaml`

One file lists every machine. It's an Ansible YAML inventory, so playbooks
read it as-is, and the CLI and the dashboard chart read it too.

```yaml
all:
  children:
    servers:
      hosts:
        gmk:
          ansible_host: 192.168.4.108
          ansible_user: normal
    speakers:
      vars:
        ansible_user: normal
        dashboard_port: 8080
      hosts:
        bedroom: { ansible_host: 192.168.4.22 }
        pi4:     { ansible_host: 192.168.4.95 }
```

This replaces hey-claude's `.deploy-target`, which was gitignored and only
existed on one laptop. The CLI writes a generated `.deploy-target` from it
before calling hey-claude's scripts, so `speakers.py` doesn't change. See
[claude-speaker-migration.md](claude-speaker-migration.md).

The IPs are **where each machine was last seen**, not promises. Nothing has
a DHCP reservation yet (see [open-questions.md](open-questions.md) Q1).
Rewriting the IPs is one command (`scan --update`), and the diff shows in git.

Private IPs going into git is fine for a home LAN. Secrets don't belong in
this file. See [gmk.md](gmk.md#secrets).
