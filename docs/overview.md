# Overview

## Goals

1. **One repo, one command.** Everything that sets up or changes a machine on
   the home network lives here and is run with `./jasbros ...`.
2. **gmk is a place to run jobs.** Its 8 CPU threads should be usable by
   launching a Kubernetes Job, not by SSHing in and starting something by hand.
3. **Speakers are cattle, a little.** Making, deploying, and checking on a
   Claude speaker works from this repo, and one page shows how all of them are.
4. **Re-runnable.** Every provisioning step can run again safely. Run it twice
   and the second run changes nothing.

## Non-goals (for now)

- Anything reachable from outside the house. Nothing here gets a port
  forward, and nothing here has a password. See
  [speaker-fleet-dashboard.md](speaker-fleet-dashboard.md#security).
- Multi-node Kubernetes, HA, or cloud. gmk is one machine. The design should
  still let a second node join without a rewrite.
- Running the speakers *in* Kubernetes. A speaker owns a microphone array
  and a speaker on a Pi. It stays a systemd user service on that Pi.

## The machines

| Name | What | Address | Managed by |
|---|---|---|---|
| `gmk` | GMKtec mini PC, i7-1185G7 (4C/8T), 14 GiB, 477 GB NVMe, Ubuntu 26.04 | `192.168.4.108` today, DHCP, no reservation (Wi-Fi `wlp3s0`) | Ansible → k3s |
| speakers | Raspberry Pi 4, 64-bit Pi OS, reSpeaker XVF3800 | `192.168.4.x`, DHCP, no reservation | `infra/claude-speaker` |

**Addresses can move.** The eero hands out every address and nobody can
log into it yet ([Q1](open-questions.md)), and names don't resolve on this
network ([Q5](open-questions.md)). So an IP in the inventory is *where a
machine was last seen*, and every tool can find it again by scanning.
See D11 and D12.
| laptop | where `./jasbros` runs | — | the person |

## Repo layout

```
jasbros-platform/
├── jasbros                      # bash entry point → python CLI
├── cli/                         # the Python CLI (package: jasbros_cli)
│   ├── pyproject.toml
│   └── jasbros_cli/
│       ├── __main__.py
│       ├── gmk.py               # jasbros gmk ...
│       ├── speaker.py           # jasbros speaker ...
│       └── k8s.py               # jasbros k8s / helm helpers
├── infra/
│   ├── inventory.yaml           # every host, in one file (Ansible inventory)
│   ├── gmk/                     # provisioning for gmk
│   │   ├── ansible.cfg
│   │   ├── site.yml
│   │   ├── group_vars/
│   │   └── roles/  base/ k3s/ helm/ display/
│   └── claude-speaker/          # migrated from github.com/greg1232/hey-claude
│       ├── src/ train/ models/ case/ docs/ ...   (as they are today)
│       ├── fleet-dashboard/     # NEW: the dashboard service's code + Dockerfile
│       └── charts/
│           └── speaker-fleet-dashboard/          # NEW: its Helm chart
└── docs/                        # you are here
```

Each project under `infra/` owns its own code, playbooks, and charts. The
CLI ties them together, and none of them import from each other.

## The big picture

```
 laptop                                  gmk (192.168.4.108)
 ──────                                  ─────────────────────────────────────
 ./jasbros gmk provision ──ansible/ssh──► base OS, ufw, swap off, k3s, helm
 ./jasbros dashboard build ──buildx push──► Docker Hub gdiamos/* ──pull──┐
 ./jasbros dashboard deploy ──helm──────► k3s API :6443 ◄──────────────┘
                                           ├─ ns claude-speaker:
                                           │    speaker-fleet-dashboard ──┐
                                           └─ ns jobs: your batch Jobs    │
                                                                          │ polls
 ./jasbros speaker deploy bedroom ──ssh──► Pi "bedroom" :8080 ◄───────────┤ /api/speaker
                                           Pi "pi4"     :8080 ◄───────────┘ /api/system
 browser ───── http://192.168.4.108/speakers ──► Traefik ingress → dashboard
```

## Decisions log

Short records of choices made, so nobody has to work them out again. Add to
the bottom and don't rewrite old entries. Write a new one that replaces it.

| # | Decision | Why | Rejected |
|---|---|---|---|
| D1 | **k3s** for Kubernetes on gmk | One binary, containerd built in, ships Traefik, a load balancer and local-path storage. Right-sized for one 14 GiB box | kubeadm (more parts to own on one node), microk8s (snap-based, less usual with Ansible) |
| D2 | **Ansible** for all host provisioning, run from the laptop | Asked for. Agentless, and SSH already works | cloud-init (only runs once), shell scripts (hard to re-run safely) |
| D3 | **Helm** for everything that runs in the cluster, including infra add-ons | Asked for. One way to install, upgrade, and roll back | raw manifests, kustomize |
| D4 | The CLI is **Python, behind a bash shim** that makes its own venv | Asked for. Matches hey-claude's laptop tools. The shim means no global installs | Make, Poetry scripts |
| D5 | Ansible is **pip-installed into the CLI's venv** (`ansible-core` + pinned collections) | Same version on every laptop, nothing to brew | system Ansible |
| D6 | `helm`/`kubectl` run **on the laptop** against a kubeconfig that Ansible fetches from gmk | Fast iteration, and `helm diff` works locally | running helm over SSH on gmk |
| D7 | *(Replaced by D15.)* Images are built **on gmk** and pushed to a **registry inside the cluster** | The laptop is arm64 and gmk is amd64, and a native build skips cross-compiling. No outside registry account needed | GHCR (needs auth, public by default), `k3s ctr import` (works but isn't a registry) |
| D8 | *(Replaced by D16.)* hey-claude is migrated **with its git history** into `infra/claude-speaker` | Its commit messages are its design record | copying a snapshot |
| D9 | The fleet dashboard is a **new, separate service** that polls each speaker's existing JSON API | Speakers already serve `/api/speaker` and `/api/system`. A browser can't read them across origins, but a server can | putting fleet code in each speaker, push-based telemetry (a later step, see open questions) |
| D10 | The fleet dashboard **does not copy transcripts** | What was said in a room stays on that room's speaker. The fleet page links to it | showing every speaker's last 20 turns in one place |
| D11 | **gmk stays on DHCP.** Its address is read from the host at provision time, never hard-coded. k3s uses its default **SQLite datastore** (no embedded etcd) | No router access ([Q1](open-questions.md)). etcd is tied to the node's IP and breaks when it moves, and SQLite doesn't care | a static IP set in netplan on gmk (done remotely over Wi-Fi, one typo loses the box, and the eero might hand the same address to something else), mDNS (doesn't work here) |
| D12 | **Machines are found by scanning** `192.168.4.0/22` and asking who answers: `hostname` over SSH for the CLI, `/api/version` for the dashboard. The inventory's IPs are hints | Same approach as hey-claude's `card.py --find`, which already works on this network | mDNS, router DNS |
| D13 | gmk's monitor runs a **standard Ubuntu desktop** (GNOME, GDM autologin, Firefox) set up by Ansible, not a pod | Stock, supported, and usable with a keyboard. The apps are still in pods, and the screen is just a browser pointed at them. Details in [display.md](display.md) | — |
| D14 | Speakers switch the screen through a **token-protected display agent** on gmk that drives Firefox over **WebDriver BiDi** | Switches the current tab instead of opening new ones, using Firefox's own standard protocol. It's the one thing on the network with a lock, because it puts things on a screen in the house | `firefox --new-tab` (leaves tabs piling up, kept as the fallback), no token |
| D15 | Images are built **on the laptop** with `docker buildx --platform linux/amd64` and pushed to **public Docker Hub** repos under `gdiamos/`. gmk pulls from there. Replaces D7 | Nothing to run or upgrade. What's in the images is already public (hey-claude is a public repo), and secrets come in through Helm values or the speakers' `.env`, never the image. The images are pure Python, so building under emulation takes seconds | an in-cluster registry (D7: one more service to own, plus containerd config for a registry without TLS), building on gmk with nerdctl |
| D16 | hey-claude is brought in as a **snapshot without its git history**: the files at `89fc0f8`, copied into `infra/claude-speaker`. Replaces D8 | Asked for. Simpler, with no `git-filter-repo` step, and the old history stays on GitHub if it's ever needed | keeping history (D8) |
