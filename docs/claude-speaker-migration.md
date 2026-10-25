# Migrating hey-claude into `infra/claude-speaker`

**Step 1 is done (2026-10-25).**

Source: <https://github.com/greg1232/hey-claude> (159 tracked files, a single
`main` branch). It's a working product. The migration
moves it without breaking it and changes as little as possible in the
same step.

## What it is, briefly

Code that runs on a Pi (`src/`), laptop tools that drive the Pi over SSH
(`card.py`, `deploy.py`, `start.py`, `label.py`, `relearn.py`, `wishes.py`,
`speakers.py`, each with a `.sh` shim), wake-word training (`train/`),
models (`models/`, ~7 MB, committed on purpose), recordings of the
household's voices (`train/real/`, committed on purpose), and a printed
case (`case/`). Its own `docs/` explain it well and they move with it.

Each speaker already serves a dashboard on `:8080` from inside the speaker
process (`src/dashboard.py`), and has a JSON API the fleet dashboard will use:

| Endpoint | Gives |
|---|---|
| `GET /api/speaker` | state, last 20 turns, wake-word scores, timers, sound, book |
| `GET /api/system` | CPU, temperature, memory, disk, uptime, load, throttling, service health |
| `GET /api/wishes` | pooled wishes |
| `GET /api/wifi` | current network, in range, saved |
| `POST /api/do`, `POST /api/wifi` | actions |

## Step 1 — move it (done)

Copied as a snapshot, **without git history** (decision D16), from the local
checkout at `89fc0f8`, which matched `origin/main` exactly:

```bash
(cd ~/checkout/claude-speaker-tejas && git archive HEAD) | tar -x -C infra/claude-speaker
cp ~/checkout/claude-speaker-tejas/{.env,.deploy-target} infra/claude-speaker/
```

All 159 tracked files are identical. `.env` (the API keys) and `.deploy-target`
came along so the laptop scripts work from here, and hey-claude's own
`.gitignore`, which now sits at `infra/claude-speaker/.gitignore`, keeps both
out of git. Nothing inside `infra/claude-speaker/` was changed.

Checked: `deploy.sh`, `card.sh`, `label.sh`, `relearn.sh`, and `wishes.sh` all
run from the new path, and `speakers.py` resolves both speakers. A real
`./deploy.sh` to a speaker from here hasn't been done yet. It runs the same
code, so the first deploy of the `/api/version` change (step 3) will be that
test.

`~/checkout/claude-speaker-tejas` is now the old copy. Make changes here.
Once this has been deployed from at least once, the GitHub repo gets a README
pointer here and is archived (Q4).

## Step 2 — wire it into the CLI and the inventory

- `jasbros speaker <cmd>` calls the existing scripts (see [cli.md](cli.md)).
- `jasbros speaker scan` reuses `card.py`'s `find()` approach (TCP 22 sweep,
  then `ssh hostname`), since that's what works on this network.
- `speakers.py` reads `../inventory.yaml` (`speakers` group) first and
  falls back to `.deploy-target`. It stays standard-library-only,
  because `start.py` and friends must run before any venv exists. So it
  parses this one simple, known shape of YAML by hand, or the CLI writes a
  generated `.deploy-target` from the inventory before each call. **The
  second is simpler and is the plan**, so `speakers.py` is unchanged.
- `deploy.py` gains a `--name` passthrough so `./card.sh` and deploys
  register a new speaker in `inventory.yaml` instead of `.deploy-target`.

## Step 3 — the fleet dashboard

New code under `infra/claude-speaker/fleet-dashboard/` and
`infra/claude-speaker/charts/`. Nothing in `src/` changes, apart from
the small additions listed in
[speaker-fleet-dashboard.md](speaker-fleet-dashboard.md#changes-to-the-speaker).

## Step 4 — the screen tool

A new tool module, `src/screen.py`, lets a speaker switch what gmk's
monitor shows. `deploy.py` writes `SCREEN_URL`/`SCREEN_TOKEN`. See
[display.md](display.md#switching-by-voice).

## Later, not now

- **Port `deploy.py` to an Ansible role.** "Provisioning should be done
  using Ansible" points here. But `deploy.py` is 950 lines of hard-won Pi
  knowledge (the WirePlumber always-process rule, the captive portal sysctl,
  linger, librespot as a user service). It should be ported one step at a
  time, each checked against a real Pi, *after* the move is done and
  stable. A first role can cover just the apt packages and the systemd
  units, and keep calling `deploy.py` for the rest.
- Nightly retraining as a CronJob on gmk (see [gmk.md](gmk.md#running-jobs-on-the-cores)).
- `card.py` stays a laptop tool. Writing an SD card isn't something
  Ansible does.
