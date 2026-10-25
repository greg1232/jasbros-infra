# Speaker fleet dashboard

One page, served from gmk, that shows every Claude speaker in the house:
is it up, what is it doing, is the Pi healthy, and which network is it on.

Each speaker's own page (`http://<pi>:8080`) stays as it is. That page is
for *one* speaker in detail and for changing its Wi-Fi. This one is for the
whole house at a glance, and for noticing the speaker that has gone quiet.

## Why it has to be a server

A page in a browser can't just fetch from every Pi. The speaker's API
sends no CORS headers, so a page on `gmk` can't read `bedroom:8080/api/system`.
And a speaker that is off shows up as a hung request, not an answer. A small
server that polls each speaker and keeps the last result handles both. It
also remembers *when* each speaker was last seen, which no single speaker
can report about itself.

## What it shows

One card per speaker in `infra/inventory.yaml`, plus one row per problem
across the top:

| Field | From |
|---|---|
| **Up / down / stale**, last seen | whether the poll worked, and when |
| What it's doing: talking, music, sound, book, timers | `/api/speaker` (`talking`, `music`, …) |
| **Is the mic hearing?** (the array goes deaf silently, see hey-claude's raspberry-pi.md) | `/api/speaker` (`hearing`, `gone_deaf`) |
| Timers running, sound playing, book being read | `/api/speaker` |
| Wake-word firings in the last 24 h, and how many were "woke, nobody spoke" | `/api/speaker` wake summary |
| CPU, temperature, memory, disk, uptime, **throttling** | `/api/system` |
| Services: `claude-speaker`, `librespot`, `claude-relearn.timer` | `/api/system` |
| Wi-Fi network and signal | `/api/wifi` (current network only) |
| Code version deployed | new `/api/version` (see below) |
| Link to the speaker's own page | the speaker's current address + port |
| **Moved**: was `.22`, now `.31` | discovery (below) |

A 24-hour sparkline of temperature and up/down per speaker comes from the
dashboard's own history (see Storage).

Problems listed at the top: **down for more than 5 minutes**, **throttling**,
**mic not hearing**, **over 75 °C**, **disk over 90 %**, **a service not active**, **a deployed
version that differs from the others**.

### What it deliberately leaves out

- **Transcripts.** `/api/speaker` includes the last twenty things said in
  each room. The fleet dashboard drops `turns[].asked`/`answered` text
  **at poll time, before anything is stored**, and keeps only counts and
  how each turn ended. Seeing what was said means going to that speaker's
  own page, in that room's context (decision D10).
- **Wi-Fi changes and `POST /api/do`.** No actions in v1. Read-only means a
  bug in the fleet page can't strand a speaker off the network. A "stop
  everything, everywhere" button is the one action worth adding later.
- **Wishes.** Every speaker already shows the pooled wishes list. Repeating it
  here adds nothing.

## Architecture

```
            ┌──────────────── pod: speaker-fleet-dashboard ───────────────┐
 inventory  │  poller (one thread per speaker, every 15 s, 3 s timeout)  │
 ConfigMap ─┼─►  GET /api/speaker, /api/system, /api/wifi, /api/version  │──► Pis :8080
            │       │ strip transcripts                                 │
            │       ▼                                                   │
            │  state: latest per speaker (memory) + history (SQLite)    │
            │       │                                                   │
            │  http.server :8000  /  /api/fleet  /healthz               │
            └───────┼───────────────────────────────────────────────────┘
                    ▼
     Traefik ingress  http://<gmk's address>/speakers   (today http://192.168.4.108/speakers)
```

- **Python, standard library only**, like `src/dashboard.py`: `http.server`
  (threaded), `urllib.request`, `sqlite3`, `json`, and one HTML page with
  inline JS that polls `/api/fleet` every 5 s. It should look like the
  speaker's own dashboard and reuse that page's CSS. No framework, no build step.
- **Polling**: every 15 s per speaker with a 3 s timeout, and back off to
  60 s after 3 failures in a row so a speaker that is off doesn't eat a
  thread. Down for 5 minutes is the alert line, because a Pi
  rebooting takes about a minute and a router blink about ninety seconds.
- **Storage**: SQLite on a 1 Gi local-path PVC. One row per speaker per
  minute (up, temperature, CPU, throttled), kept 7 days. If the PVC is
  lost, all that's lost is sparklines.
- **Config**: the speaker list comes from Helm values, which the CLI
  renders from `infra/inventory.yaml`'s `speakers` group, so there's one list
  and not two. It's mounted as a ConfigMap, and changing it rolls the pod
  through a checksum annotation.

### Networking from the pod

Pods reach the LAN through k3s's flannel + NAT out of `wlp3s0`. The speakers
see requests coming from gmk's address.

### Finding speakers that moved

No speaker has a DHCP reservation and names don't resolve here (D11, D12),
so the inventory's IP for each speaker is only a **hint**. A speaker is
known by its **name**, and the poller checks it on every poll:

1. Poll the last address that worked (at first, the inventory's).
2. If `/api/version` there answers with the right `name`, carry on.
3. If it doesn't answer, or answers with a *different* name (the eero gave
   that address to another speaker), mark it **looking** and put it on the
   rescan list.
4. **Rescan** runs at most once every 5 minutes, and only if something is on
   the list. It tries TCP `:8080` on all of `192.168.4.0/22` (1,022
   addresses, 64 at a time, 0.5 s timeout, about 8 s), then asks each one
   that answers for `/api/version`. One scan finds every missing speaker.
5. A new address is kept in the SQLite DB (so a pod restart remembers it) and
   shown on the card as **moved**. The page also lists anything that
   answers like a speaker but isn't in the inventory, as **unknown speaker
   `kitchen` at .57**.

The dashboard never writes the inventory. `./jasbros speaker scan --update`
does that from the laptop, so the change shows up in git.

**Down** therefore means *not found anywhere* for 5 minutes, not just *not
at its old address*. Right after a router reboot most speakers will show
**looking** for up to 5 minutes before they're found again, and that's
expected.

## Changes to the speaker

Small, additive, and in the same style as the code around them:

1. **`GET /api/version`** in `src/dashboard.py`, returning
   `{"name": <hostname>, "commit": …, "deployed_at": …}`. The name is what
   discovery matches on (`card.py` already sets the hostname to the speaker's
   name). The commit and time come from a `VERSION` file that `deploy.py`
   writes next to the code. A speaker on old code answers 404 here, so
   discovery falls back to matching `/api/system`'s `address` against the
   inventory, and the version shows as "unknown".
2. Nothing else. If `/api/speaker` turns out to be too heavy to poll every
   15 s on a Pi 4, add `GET /api/summary` there instead of polling less.

## The Helm chart

`infra/claude-speaker/charts/speaker-fleet-dashboard/`

```
Chart.yaml            appVersion = image tag
values.yaml
templates/
  deployment.yaml     1 replica, strategy Recreate (SQLite + RWO PVC)
  service.yaml        ClusterIP :80 → 8000
  ingress.yaml        Traefik, host-less, path /speakers (stripPrefix middleware)
  middleware.yaml     traefik.io/v1alpha1 StripPrefix
  configmap.yaml      speakers.json from .Values.speakers
  pvc.yaml            1Gi local-path
```

`values.yaml`:

```yaml
image:
  repository: docker.io/gdiamos/speaker-fleet-dashboard
  tag: ""                 # set by ./jasbros dashboard deploy to the git sha
pollSeconds: 15
timeoutSeconds: 3
downAfterMinutes: 5
rescan:
  enabled: true
  subnet: 192.168.4.0/22
  port: 8080
  everyMinutes: 5
historyDays: 7
speakers: []              # [{name: bedroom, url: http://192.168.4.22:8080}]
ingress:
  enabled: true
  path: /speakers
resources:
  requests: { cpu: 50m, memory: 64Mi }
  limits:   { cpu: 500m, memory: 256Mi }
persistence:
  size: 1Gi
  storageClass: local-path
```

Pod basics: runs as non-root uid 10001, read-only root filesystem (SQLite
lives on the PVC), liveness probe on `/healthz`, readiness probe once the
first poll round is done.

Deploying:

```
./jasbros dashboard build    # docker buildx --platform linux/amd64 --push :<git-sha>
./jasbros dashboard deploy   # helm upgrade --install speaker-fleet-dashboard \
                             #   -n claude-speaker --create-namespace \
                             #   --set image.tag=<sha> -f <rendered speakers>
```

## Testing

- Unit tests (stdlib `unittest`, run by `./jasbros test`): parsing real
  saved `/api/speaker` and `/api/system` responses (recorded from a live
  Pi into `fleet-dashboard/tests/fixtures/`), checking transcripts are
  stripped, and the up / looking / moved / down state machine against a
  fake clock and a fake network. That includes two speakers swapping
  addresses, which is the case that's easy to get wrong.
- `helm lint` and `helm template` in the same test run.
- By hand, once: turn one speaker off, check it shows down after 5 minutes
  and recovers on its own. Then force a new lease on one Pi (`sudo nmcli
  con down/up` on it) and check it shows **moved** within 5 minutes.

## Security

It has no password, like the speakers' own pages, and the same rule
applies: **home network only, no port forward.** It's less exposed
than a speaker page, since it's read-only and carries no transcripts, but it
still lists every speaker and where it is. Traefik only listens on the LAN
because ufw only allows 80/443 from `192.168.4.0/22`.

The rescan is a port scan of the home network, on purpose and only for
`:8080`. It's rate-limited (once every 5 min, only when a speaker is
missing), so it doesn't flood the Wi-Fi. `rescan.enabled: false` in values turns
it off, and then a moved speaker just shows as down.
