# gmk — provisioning and the cluster

## The machine, as found (2026-10-25)

| | |
|---|---|
| CPU | Intel i7-1185G7, 4 cores / 8 threads |
| Memory | 14 GiB, plus 4 GiB `/swap.img` (on) |
| Disk | 477 GB NVMe. LVM `ubuntu-vg/ubuntu-lv` mounted at `/`, **98 GB**, so ~375 GB of the VG is unallocated |
| OS | Ubuntu 26.04.1 LTS, kernel 7.0 |
| Network | Wi-Fi only (`wlp3s0`, 192.168.4.108/22, from DHCP on the eero, **no reservation**). `enp2s0` is down. `gmk`/`gmk.local` don't resolve, and avahi isn't running |
| Firewall | `ufw` active |
| Access | `ssh normal@192.168.4.108`, key auth works, sudo needs a password. The system sudo is **sudo-rs**, and Ansible uses the classic `sudo.ws` installed beside it (see `group_vars/servers.yml`) |
| Wi-Fi chip | Realtek RTL8852BE, `rtw89` driver |
| Installed | python3. No Kubernetes, container runtime, or helm |

## What provisioning does

`./jasbros gmk provision` runs `infra/gmk/site.yml` against the `servers`
group. The roles run in order, and each can be run alone with `--tags`.

### `base`
- apt update/upgrade (35 updates pending today), unattended-upgrades for security only.
- Packages: `curl`, `jq`, `htop`, `iw`, `lvm2`, `unattended-upgrades`.
- If an upgrade needs a reboot, it says so. It **never reboots** gmk itself.
- **Swap off.** `swapoff -a` and remove `/swap.img` from fstab. kubelet
  refuses to start with swap on by default, and with 14 GiB there's no
  reason to fight it.
- **Disk.** Grow `ubuntu-lv` and the filesystem to a set size
  (`gmk_root_size`, default `200G`). The rest of the VG stays free for a
  later volume dedicated to `/var/lib/rancher`. Growing is safe online, and
  the role only grows, never shrinks. *(Open question: how big.)*
- sysctls Kubernetes wants: `net.ipv4.ip_forward=1`,
  `net.bridge.bridge-nf-call-iptables=1`, `fs.inotify.max_user_instances=512`.
- Kernel modules `br_netfilter`, `overlay`, loaded now and at boot.
- **systemd-networkd runs the network**, not NetworkManager:
  `/etc/netplan/90-jasbros-networkd.yaml` sets `renderer: networkd`, which
  overrides the desktop's netplan file. It also marks `enp2s0` (no cable)
  `optional`, so boot doesn't wait 2 minutes for it. It takes effect at the
  next boot, and is never applied live over the only link.
- **Wi-Fi power save off** on `wlp3s0`. A server that naps its radio drops
  kubectl connections and dashboard polls. gmk uses systemd-networkd, not
  NetworkManager, so this is done two ways: a oneshot unit runs `iw ... set
  power_save off` at boot, and `options rtw89_core disable_ps_mode=y` in
  `/etc/modprobe.d` keeps the driver from turning it back on after a
  reconnect (that takes effect from the next boot).
- **ufw**: allow 22/tcp, 80/tcp, 443/tcp, 6443/tcp (and 8090/tcp for the
  display agent) from `192.168.4.0/22`.
  Allow the k3s pod and service CIDRs (`10.42.0.0/16`, `10.43.0.0/16`) so
  traffic inside the cluster isn't dropped. Default deny incoming.

### `k3s`
- `/etc/NetworkManager/conf.d/90-jasbros-k3s.conf` keeps NetworkManager
  (which comes with the desktop) off `cni0`, `flannel*`, and `veth*`. Without
  it, every pod lost its network. See [display.md](display.md).
- Installs a **pinned** k3s version (`k3s_version` in `group_vars`) using the
  official install script with `INSTALL_K3S_VERSION`. Upgrades only happen
  when that variable changes.
- `/etc/rancher/k3s/config.yaml`, templated:
  ```yaml
  node-ip: "{{ ansible_default_ipv4.address }}"   # read from the host every run
  flannel-iface: wlp3s0
  write-kubeconfig-mode: "0640"
  tls-san: [gmk, "{{ ansible_default_ipv4.address }}"]
  # no cluster-init: the default SQLite datastore survives an IP change
  kubelet-arg:
    - "system-reserved=cpu=1,memory=2Gi"     # includes the desktop, see display.md
    - "kube-reserved=cpu=500m,memory=1Gi"
  ```
  That leaves **6.5 CPUs / ~11 GiB allocatable** for jobs.
- Keeps the bundled **Traefik** (ingress), **ServiceLB**, and
  **local-path-provisioner** (PVCs on gmk's disk).
- Fetches the kubeconfig to the laptop as `~/.kube/jasbros-gmk.yaml`,
  rewrites `server:` to gmk's current address, and names the context
  `jasbros-gmk`. The CLI always passes `--kubeconfig` explicitly and never
  touches `~/.kube/config`.

### When gmk's address changes

With no DHCP reservation this can happen after a long power cut or a router
reset. Recovering takes three commands, and all of them are safe to re-run:

```
./jasbros gmk find --update        # scan, find hostname gmk, rewrite inventory
./jasbros gmk provision --tags k3s # new node-ip / tls-san, restart k3s
./jasbros gmk kubeconfig           # point the laptop at the new address
```

The `k3s` role also checks at the start of each run whether the node's
current address matches what k3s is configured with, and **says so in
big letters** if it doesn't, so a move doesn't look like some other bug.
The Traefik ingress listens on every address, so the dashboard follows the
move on its own, but the URL you open changes.

### `helm`
- Installs a pinned `helm` on gmk as well. That's not the usual path, but it
  means a deploy can still be done from gmk itself if the laptop is away.

### `display`
A standard Ubuntu desktop on the HDMI monitor: `ubuntu-desktop-minimal`,
GDM autologin as an unprivileged `display` user, and Firefox opened on the
app. Lock, blank, and suspend are all turned off, and the sleep targets are
masked so the node never suspends. Also runs `display-agent` on port 8090,
so speakers can switch what the browser shows. See [display.md](display.md).

### Images
Nothing on gmk builds or stores images. They're built on the laptop and pulled
from Docker Hub (`gdiamos/*`, public), see decision D15.

## Running jobs on the cores

Cluster layout:

| Namespace | For |
|---|---|
| `jasbros-system` | cluster add-ons, later (monitoring) |
| `claude-speaker` | the fleet dashboard |
| `jobs` | batch work. Has a `ResourceQuota` of `cpu: 6, memory: 10Gi`, and a `LimitRange` default of 1 CPU / 1 Gi so a Job with no requests can't take the whole box |

`./jasbros job run train-42 --image python:3.13-slim --cpus 4 -- python -c ...`
renders a `batch/v1 Job` (`backoffLimit: 0`, `ttlSecondsAfterFinished: 86400`,
requests = limits = what you asked for) and applies it to `jobs`. This is a
convenience. Anything long-lived or repeated should get its own Helm chart.

Example use: hey-claude's nightly wake-word retraining currently runs on
each Pi at 04:00. Moving it to a CronJob on gmk is a natural later step,
but **out of scope** here.

## Secrets

Nothing secret goes into git. For now:

- Ansible's sudo password is typed in at a prompt (`--ask-become-pass`).
- Secrets for the cluster (none needed yet) would go in as `kubectl create
  secret` from the CLI, read from the laptop's environment, never from a
  values file in the repo.
- Speaker secrets (`ANTHROPIC_API_KEY`, Spotify, HF) keep living in
  `infra/claude-speaker/.env` on the laptop, gitignored, sent over SSH by
  `deploy.py` the same way they are today.

If that stops being enough, the next step is `ansible-vault` for host
secrets and SOPS for chart values. Neither is needed yet.

## Testing provisioning

- `./jasbros gmk provision --check` (Ansible check + diff) before any real run.
- Idempotency test: run twice and expect `changed=0` on the second run.
  The CLI prints the recap and exits non-zero if anything changed.
- `infra/gmk/molecule/` with a Docker Ubuntu 26.04 image tests `base` and
  the k3s config templating. It can't test k3s itself, and that's fine.
