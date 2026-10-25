# Candy watch

A camera watches the candy box. When a kid takes **more than two pieces**,
a Claude speaker calls them out and asks them to put the extra back. If
they don't, it plays the gangster's line from *Home Alone* ("…before I pump
your guts full of lead!").

It's an app like the fleet dashboard: Python in a pod on gmk, its own Helm
chart, with source in `infra/candy-watch/`.

**Status (2026-10-25):** step 1 is done. The pod runs on gmk at
`http://192.168.4.108/candy`, and `./jasbros candy say …` / `--gangster`
makes pi4 talk through it. `--gangster` plays **the film clip** on pi4
(35 s, 1:21–1:56), or says the line if the clip isn't there. Steps 2–5 are design.

## How it works

```
 camera ──snapshot every 2 s──► candy-watch pod (gmk)
 (Reolink, later)                 │
                                  │ 1. count: how many pieces are in the box?
                                  │    (Claude, looking at the frame)
                                  │ 2. the count dropped by more than 2 since
                                  │    the box was last left alone → TAKEN
                                  ▼
                       Claude writes the call-out ──► POST /api/do say ──► pi4
                       "Hey! That's three. Put one back."
                                  │
                       wait 60 s, count again
                                  │
                   back to ≤ 2 taken? ── yes ──► "Thank you." (done)
                                  │ no
                                  ▼
                       the Home Alone line ──► pi4
```

### States

| State | Means | Leaves when |
|---|---|---|
| `watching` | box is settled; `baseline` = its count | count drops (someone's taking) |
| `taking` | count is falling; wait until it's been stable for 10 s | stable → compare to baseline |
| `called_out` | more than 2 taken; speaker has asked for them back | count recovers within 2 of baseline → `thanked`; 60 s pass → `gangster` |
| `gangster` | the line has played | box settles → new baseline, `watching` |

"Taken" is measured as **baseline minus settled count**, not by watching
hands, so the counting only has to be right when nobody is reaching in. A
hand in the frame makes a count unreliable, so frames where Claude says
something is blocking the box aren't counted.

Refilling the box raises the count, and that simply sets a new baseline.

### Seeing: Claude, not a trained model

Each frame goes to Claude with a fixed question: *"How many pieces of candy
are in the box? Is anything (a hand, an arm) blocking the view? Answer as
JSON."* Claude's image understanding counts small objects well enough for a
box on a counter, there's nothing to train, and the household's speakers
already use the same API key.

Cost and pace: one frame every 2 s *only while there's motion*. Reolink
cameras report motion themselves, and until then a cheap local frame
difference works. A quiet kitchen costs nothing.

**Privacy:** frames are of the kids, and they go to Anthropic's API. Frames
are never stored, only the count. The pod's log says what was decided,
never what was seen.

### Talking: the speakers' existing API

`POST http://<speaker>:8080/api/do {"what": "say", "value": "<≤200 chars>"}`
is what each speaker already accepts (see `src/dashboard.py`). The speaker is
named in values (`speaker: pi4`), and its address comes from
`infra/inventory.yaml` like the fleet dashboard's.

The call-out is written by Claude each time, so it isn't the same sentence
every time, and so it can use what the speaker knows (`HOUSEHOLD` names):
*"Three pieces? Nice try. Put one back before I get creative."*

### The *Home Alone* line

The audio is from a copyrighted film, and this repo is public, so the clip
is **never committed or downloaded by the project**. Two ways to play it:

1. **Speaker voice (built, the fallback):** the speaker *says* the line in
   its own Piper voice. It's used when there's no clip, or the speaker is on
   code too old to play one.
2. **The real clip (later):** `./jasbros candy clip` downloads it from the
   scene on YouTube (`hj_XtWtOOzA`, 1:21–1:56, the defaults in
   `cli/jasbros_cli/candy.py`; another URL and `--start/--end` can be given)
   with `yt-dlp` and `ffmpeg`. It writes a 16 kHz mono WAV to
   `infra/candy-watch/clip/home-alone.wav`, which is gitignored. **Built.**
   Then it puts the clip in the cluster: FLAC (584 KB, lossless) in a
   ConfigMap `candy-watch-clip` made from the laptop with server-side apply,
   since the client-side kind copies it into a 256 KB annotation. The chart
   only mounts it (`optional`), so the clip never goes through git.
   The pod serves it at `/candy/clip/home-alone.flac`, and tells the speaker
   to fetch it from gmk's address, which it reads from Kubernetes
   (`status.hostIP`).
3. **The speaker's `play` action (built):** `{"what": "play", "value": url}`
   plays a recording only from a *numbered private* address (no names, no
   internet, `http` only), up to 8 MB. "Stop" stops it: `tts.play_clip`
   now plays a quarter-second at a time. The pod
   serves it on the LAN, and the speaker gets one new action,
   `{"what": "play", "value": "<url>"}`, which plays a WAV from an address
   on `192.168.4.0/22`. That's a small change to `src/dashboard.py`, deployed
   like `/api/version` was.

### The camera: Reolink

Reolink cameras serve stills over HTTP
(`/cgi-bin/api.cgi?cmd=Snap&channel=0&user=…&password=…`) and video over
RTSP. Snapshots are all this needs, with no video decoding in the pod. The
camera's username and password go in a Kubernetes Secret, created from the
laptop (`./jasbros candy camera --host … --user …`), never in git. Until the
Reolink is connected, a test mode takes frames from files, so the whole
pipeline can be tested without it.

## Plan

1. **Pod, and making pi4 talk** (now): `infra/candy-watch/` with a stdlib
   Python service, Dockerfile, Helm chart, and `./jasbros candy build|deploy|say`.
   `POST /api/say` on the pod makes pi4 speak. This tests the image, the
   deploy, and pod → speaker over the LAN.
2. Claude writes the call-outs (API key as a Secret from the laptop).
3. Counting from frames, the state machine, and tests from still photos.
4. The Reolink, with motion gating.
5. The real clip, if wanted (speaker `play` action).

## Open questions

| # | Question | Default |
|---|---|---|
| C1 | Which speaker talks? | pi4 for now; one value to change |
| C2 | How long to put it back? | 60 s |
| C3 | "More than 2" per what: per visit to the box, or per hour/day? | Per visit: the count settles, then compare to before |
| C4 | Should it say who? It can't tell the kids apart | No names. "Hey, you" |
