# gmk display — a standard desktop with a browser

The monitor plugged into gmk shows an ordinary Ubuntu desktop, logged in,
with a web browser open on the app. It's the normal stock stack: GNOME,
GDM, Firefox. Nothing on the screen is custom. The apps run in Kubernetes as
usual, and the browser on gmk's screen is just another client of them, like
a laptop's.

## The hardware, as found (2026-10-25)

| | |
|---|---|
| GPU | Intel Iris Xe (TigerLake GT2), supported by the stock kernel and Mesa |
| Outputs | `HDMI-A-1` **connected**, 3840×2160. `HDMI-A-2`, `DP-1..3` empty |
| Display stack | none. Server install, no desktop or display manager. `graphical.target` is already the default |
| Console | `getty@tty1`, with `normal` logged in on tty1 |

## Goals

- Turn gmk on and the screen comes up on the desktop, logged in, with the
  browser open on the app. Nobody has to type anything.
- It **opens on the [speaker fleet dashboard](speaker-fleet-dashboard.md)** by default.
- What's on screen can be changed in two ways: **by clicking**, since it's a real
  desktop and an ordinary browser, or **by asking a Claude speaker** ("hey
  claude, put the weather on the screen", "go back to the dashboard").
  See [Switching by voice](#switching-by-voice).
- The screen never locks, blanks, or **suspends the machine**. gmk is a
  Kubernetes node first and a screen second.
- Everything is set up by Ansible and safe to re-run, like the rest of gmk.

## What gets installed (Ansible role `display`)

| Piece | Stock choice | Notes |
|---|---|---|
| Desktop | `ubuntu-desktop-minimal` | GNOME on Wayland, the default Ubuntu desktop, without the office suite and games |
| Login | GDM, **automatic login** as user `display` | in `/etc/gdm3/custom.conf` |
| Browser | Firefox, Ubuntu's default (snap) | opened on login by an autostart entry |
| Scaling | GNOME's own, 200 % on the 4K panel | GNOME picks this automatically. Pinned in dconf so it can't drift |

### The `display` user

The desktop logs in as a dedicated user, **`display`**, and not as `normal`:

- Not in `sudo`, and can't read `/etc/rancher/k3s/k3s.yaml` (it stays
  `root:root 0640`). Someone at the keyboard gets a browser and a desktop,
  not the cluster.
- No password login over SSH (`DenyUsers display` in sshd). It exists only
  for the screen.
- `normal` stays the admin account, used over SSH as before.

### The browser on login

`/home/display/.config/autostart/jasbros-browser.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=jasbros
Exec=firefox --remote-debugging-port=9222 --new-window http://localhost/speakers
X-GNOME-Autostart-enabled=true
```

- **`http://localhost/...`**: Traefik listens on the node itself, so the URL
  works whatever LAN address gmk has today (D11).
- An ordinary window, not kiosk mode. Press F11 for full screen, or set
  `display_fullscreen: true` to add `--kiosk` to the line.
- The start page is an Ansible variable, `display_url`, and its default is the
  fleet dashboard. This is only where the browser *starts*. Changing it from
  minute to minute is what clicking and voice are for.
- `--remote-debugging-port=9222` lets the display agent (below) steer the
  browser. Firefox only listens on `127.0.0.1` for this, so it's not
  reachable from the network.
- Firefox's own session restore handles a crash. If someone closes the
  window, logging out and in (or a reboot) opens it again.

### Never lock, never blank, never sleep

GNOME's defaults are made for laptops: lock after 5 minutes, blank the screen,
and **suspend after 15 minutes on AC**. A suspended gmk is a dead
Kubernetes node, so the role turns all of that off in two layers:

1. **System dconf defaults, locked** (`/etc/dconf/db/local.d/00-jasbros` +
   `locks/`), so the user can't turn them back on from Settings:
   - `org/gnome/desktop/session idle-delay=0`
   - `org/gnome/desktop/screensaver lock-enabled=false`
   - `org/gnome/settings-daemon/plugins/power sleep-inactive-ac-type='nothing'`
   - `org/gnome/desktop/lockdown disable-lock-screen=true`
2. **systemd**: mask `sleep.target`, `suspend.target`, `hibernate.target`,
   and `hybrid-sleep.target`, so nothing can suspend gmk, including a
   stray click on the power menu.

Also: `getty@tty1` gets out of the way, because GDM takes over the screen.
The console session on tty1 ends when the role first runs. tty3–6 still have
a text login (Ctrl-Alt-F3), and SSH is unaffected.

## Switching by voice

```
 speaker "kitchen"                          gmk
 ─────────────────                          ───────────────────────────────────────────
 "hey claude, put the                       display-agent  (systemd user service of
  weather on the screen"                     `display`, port 8090, token required)
      │                                          │
 Claude calls show_on_screen ── POST ──────────► │ checks token + URL
   {place: "weather"} or {url: "https://…"}      │ WebDriver BiDi over 127.0.0.1:9222
      │                                          ▼
 "It's on the screen." ◄──── {"showing": …} ─ Firefox navigates the current tab
```

### On gmk: `display-agent`

A small program in the `display` role, `infra/gmk/roles/display/files/display_agent.py`.
It uses Python's standard library plus Ubuntu's `python3-websockets` package,
and runs as a **systemd user service of `display`**, so it lives in the same
desktop session as Firefox and starts with it.

| Endpoint | Token | Does |
|---|---|---|
| `GET /whoami` | no | `{"name": "gmk", "role": "display", "places": [...]}`, used to find gmk when its address moves |
| `GET /api/screen` | yes | the current URL and page title |
| `POST /api/screen` | yes | `{"place": "dashboard"}` or `{"url": "https://…"}` navigates the **current tab** and brings the window forward |

- **It drives the browser with WebDriver BiDi**, Firefox's standard
  remote-control protocol, using `browsingContext.navigate` on the tab
  that's in front. That *switches* what's on screen instead of piling up a new
  tab for every request. The cost: while remote control is on, Firefox shows
  its striped address bar with a robot icon. If that turns out to be
  annoying, the fallback is plain `firefox --new-tab URL`, which looks
  ordinary but leaves old tabs behind.
- **Places** are names for URLs, set in the Ansible variable `display_places`:
  ```yaml
  display_places:
    dashboard: http://localhost/speakers
    weather:   https://www.windy.com/
  ```
  Saying "go back to the dashboard" is `{place: "dashboard"}`. Anything not
  in the list, Claude gives as a URL, which it can find with web search.
- **Only `http`/`https` URLs.** `file:`, `about:`, `javascript:` and the
  rest are refused.
- **A token is required** for anything that changes the screen. Ansible
  generates it once at `/etc/jasbros/display-token` (mode `0640`, group
  `normal`), and it never goes into git. Every other page on this network has no
  password, but this one puts things on a screen in the house, so it's the
  one that gets a lock.
- **No content filtering.** Any `http`/`https` page can be shown, and there's no
  allowlist or parental control (Q12). The screen stays on whatever it was
  switched to and never goes back to the dashboard on its own (Q13).
- Each switch is written to the agent's journal for debugging (when, which
  speaker, what URL). It isn't shown anywhere else.
- ufw allows `8090/tcp` from `192.168.4.0/22`.

### On the speaker: `src/screen.py`

A new tool module in hey-claude, written the way
[docs/tools.md](../infra/claude-speaker/docs/tools.md) says: one decorated
function, and added to `FEATURES`.

```python
@tools.tool(
    "Show something on the big screen: a named place (the dashboard, the "
    "weather) or any web page. Use it when somebody asks to put, show or "
    "switch something on the screen, or to go back to the dashboard.",
    properties={
        "place": {"type": "string", "description": "one of: <places from gmk>"},
        "url":   {"type": "string", "description": "a full https:// address"},
    },
    says="put things on the screen",
)
def show_on_screen(place: str = "", url: str = "") -> str:
    ...
    return "It's on the screen."
```

- Settings: `SCREEN_URL` (for example `http://192.168.4.108:8090`) and
  `SCREEN_TOKEN`. **`./jasbros speaker deploy` fills both in**: the address from
  the inventory, and the token read from gmk over SSH. If they're missing,
  the tool isn't registered, the same way weather needs `LOCATION`.
- The places list is fetched from `/whoami` at startup, so the tool
  description names the real places.
- **When gmk has moved** (D11), it doesn't make anyone wait for a scan, because
  tools must be quick. It answers "I can't reach the screen just now", and a
  background thread scans `192.168.4.0/22` for `:8090/whoami` answering
  `"name": "gmk"`. The new address is saved in `state/` for next time.
- It never raises, and it returns a sentence, per the tool rules.

## Cost

GNOME + Firefox use about **1.5 GiB of memory** and a little CPU at idle.
The `k3s` role's reservations go up to match:
`system-reserved=cpu=1,memory=2Gi`. That leaves **6.5 CPUs / ~11 GiB**
allocatable, and the `jobs` quota drops to `cpu: 6, memory: 10Gi`
(see [gmk.md](gmk.md)).

## Updates

Firefox is a snap and refreshes itself, so it gets security updates without
help. The rest comes through apt and unattended-upgrades like the rest of
gmk. A refresh can close and reopen Firefox, and that's fine.

## Testing

- `display_agent.py` unit tests with a fake BiDi socket: a place, a URL,
  a refused scheme, a missing or wrong token.
- `screen.py` unit tests with a fake agent: it answers, it's down (says so
  without waiting), and it has moved (the background scan finds it).

- `./jasbros gmk provision --tags display --check` before the first real run,
  and a second run reports `changed=0`.
- By hand, once:
  - Reboot gmk. The desktop comes up logged in, with Firefox on the dashboard
    and no keyboard touched.
  - Leave it an hour. It hasn't locked, blanked, or suspended, and
    `kubectl get node` still says Ready the whole time.
  - `ssh display@gmk` is refused, and `sudo -l` as `display` shows nothing.
  - "hey claude, put the weather on the screen" switches the tab, and "go
    back to the dashboard" switches it back. A `curl` to `/api/screen` without
    the token is refused.

## Open questions

| # | Question | Default |
|---|---|---|
| Q9 | **What should the browser open on?** *Answered:* the speaker fleet dashboard, switchable by clicking or by asking a speaker | — |
| Q10 | **Turn the monitor off at night?** A static page all day can burn in on some panels | Not in v1. GNOME's screen blank (without suspend) is one dconf key if you want it |
| Q11 | **OK to end the console session on tty1** when the role first runs? | Yes. SSH and the other ttys still work |
| Q12 | **Can any speaker put any website on the screen?** *Answered (2026-10-25):* yes, with no parental controls and no allowlist | — |
| Q13 | **Go back to the dashboard on its own** after a while? *Answered:* no. It stays on whatever it was switched to until someone switches it | — |
