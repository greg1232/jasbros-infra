# Candy watch

The **JAS Brothers Halloween candy computer**. A camera watches the candy box.
When a trick-or-treater walks up, it welcomes them and names their costume
(*"I see you're a scary witch!"*). When a kid takes **more than two pieces**,
a Claude speaker calls them out and asks them to put the extra back. If
they don't, it plays the gangster's line from *Home Alone* ("…before I pump
your guts full of lead!").

It's an app like the fleet dashboard: Python in a pod on gmk, its own Helm
chart, with source in `infra/candy-watch/`.

**Status (2026-10-25):** the dashboard is live at `http://192.168.4.108/candy`
and **on gmk's screen**: the camera's live view (every 2 s while someone is
looking), the rule, what's happening, and test controls. The camera is the
**Reolink E1 Outdoor Pro "Front Yard" at `192.168.4.107`** (4K, pan-tilt-zoom,
wall powered), reached over its HTTP API. It isn't aimed at the candy box yet.
Step 1 is done. The pod runs on gmk at
`http://192.168.4.108/candy`, and `./jasbros candy say …` / `--gangster`
makes pi4 talk through it. `./jasbros candy demo scary witch` runs the
whole interaction on pi4 (welcome with the costume → call-out → wait →
"You didn't put them back. All right..." → the clip), with nobody putting
anything back. Each line waits for the last to finish, using the speaker's
`talking` status. `--gangster` plays **the film clip** on pi4
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

### Seeing: a small model on gmk decides when Claude looks (built)

```
 camera ──RTSP 640×360──► YOLOX-nano on gmk ──► tracker ──► groups
        (10 fps; detector   (people, ~20 ms,       (IDs)
         takes ~4/s)          1 thread)
                          (people, ~13 ms,       (IDs)       │
                           1 thread)                         │ only at the moments that matter
                                                             ▼
                                   Claude (claude-opus-5-5, effort low), a 4K still:
                                     · a person is confirmed → costume (a crop of just them, found
                                       in the 4K still itself), then a parting joke, prefetched
                                     · the group has gone → the joke, said as they walk away
                                     · every 5 s while anyone's there → count only (≤6 per group)
                                     · box alone 30 min → count (fresh baseline)
```

- **The gate is YOLOX-nano** (Apache-2.0, Megvii; 0.9M parameters) on ONNX
  Runtime with **one thread**. It's downloaded into the image by the
  Dockerfile, pinned by checksum, and never committed. In the pod it takes
  **~13 ms a frame**. The whole pod runs at **~5% of one CPU and ~70 MiB**,
  held to **1 CPU / 1 GiB**. Picked over YOLOv8/11 because those are AGPL.
  Tested on two public-domain trick-or-treat photos (`tests/fixtures/`):
  2 and 3 people found, and none in the empty yard.
- **The tracker** (`watch.Tracker`) gives each person an ID: boxes matched
  frame to frame by overlap, or, since a frame a second is a long time for a
  walking child, by a nearby centre and similar size. Two sightings make an
  ID real, and 4 s unseen ends it.
- **Speed:** pi4 starts talking **~5 s** after someone appears (it was
  10–13 s): ~0.75 s to confirm (3 sightings at ~4 frames a second), ~1 s for
  the 4K still, ~2–3 s for Claude, then the speaker. The costume look starts
  **the moment a person is confirmed**.
- **Frames come from the camera's RTSP stream** (`stream.py`): its 640×360
  sub-stream at 10 fps, decoded with PyAV in one thread that keeps only the
  newest frame. The detector takes the newest ~4 times a second. Snapshots
  over HTTP took ~0.6 s each and held it to ~1.6. If the stream drops, the
  loop falls back to snapshots while it reconnects (backing off, and 10 min
  after a refused login, since that counts towards the lockout). RTSP has to
  be on in the Reolink app. The camera takes 4–7 s to start a stream.
- **Cost of all that:** ~15% of one CPU and ~210 MiB, within 1 CPU / 1 GiB.
- **Claude with nobody there:** a box-alone count every 30 min, or every
  10 min while no box is in view. (A bug asked ~20 times a minute when the
  box couldn't be seen. Fixed.)
- **The parting joke:** as soon as the costume is known, a second Claude call
  (text only, ~2–3 s) writes a short, funny, slightly spooky joke about it
  while the group is still at the box. Groups that overlap in time are one
  **visit**, and when the yard is empty again the speaker says **one** joke:
  the biggest group's (most people, then the fullest description).
- **The jokes are scary:** funny, then an eerie twist ("…so does the thing
  breathing behind you. And it isn't Vader."), campfire-scary and still for
  young children: no gore, no real dangers, nothing threatening them. Kept
  under 30 words and 180 characters, since the speaker cuts off at 200.
- **Conversation scrollback:** every line the speaker says (welcome,
  call-out, thanks, warning, film clip, joke, typed by hand) is logged with
  its time and kind, and shown newest first on the dashboard. The last 500
  are kept in `/data/turns.jsonl` on the pod's volume, so a restart keeps
  them.
- **Costumes are described** with a vivid visible detail or two, up to 12
  words ("Darth Vader in cozy plaid pajamas", "a spooky grim reaper with
  skeleton hands and a pointy-hatted witch"), still only what's actually
  there. The welcome stays under the speaker's 200 characters.
- **False positives are learned:** when the costume look finds **no real
  people** (Claude reports `people: 0` for a birdhouse, a post, a
  scarecrow), there's no welcome and no joke, and that spot is remembered as
  "not a person". Detections overlapping it (IoU ≥ 0.4) are dropped before
  tracking from then on. So each false positive costs one Claude look, once.
  The spots are kept with the on/off switch on the pod's volume, drawn
  dashed on the live view, and **Test it → Forget ignored spots** clears
  them. The front yard's squirrel house was learned 26 s after deploy.
- **One group, not three:** someone confirmed right beside a group already at
  the box joins it, with no second welcome. Claude looks again so the
  costume and joke cover everyone. A group that has gone by the time
  Claude answers isn't welcomed. If it arrives after they've left it's still said, but only within
  15 s. No costume, no joke.
- **Groups:** a new ID starts a group, which is "forming" for 2 s, so kids a
  step behind join it. Any new ID after that is a **new group, even while the
  last one is still at the box**. **One costume look per group**, on a
  crop of just its members. The welcome waits for the speaker if a
  call-out is playing.
- **The candy sums:** each group's starting count is the box *before they
  arrived* (so grabbing before Claude first looks still counts). "Taken"
  runs from the earliest group still there. The allowance is 2 × everyone
  there. Over the limit **twice running** → a call-out naming the costumes
  present. A count Claude marks `blocked` (an arm in the box) isn't
  believed. **The same groups are called out once**, and a new group
  resets that.
- **No key, no Claude:** without the `candy-watch-claude` Secret
  (`./jasbros candy key`), the pod still tracks people and shows them on
  the dashboard. It never asks Claude.
- Claude answers as structured JSON (`output_config.format`), with the
  server-side refusal fallback on (`fallbacks: "default"`). Tested live on
  the public-domain photo: *"a spooky grim reaper and a little witch"*,
  2 people, in 6.7 s. So a welcome comes ~10 s after a group walks up
  (3 s forming + the look).
- **What it can't do:** count a big bowl exactly. Fewer, separated pieces
  (a tray rather than a heap) make the counts, and so the call-outs,
  trustworthy. Adults near the box count as people, and raise the allowance.

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

### The camera: Reolink (connected)

What finding it took, for next time:

- Current Reolink firmware ships with **HTTP, HTTPS and RTSP off**, leaving
  only its own port 9000. A scan for camera ports finds nothing, and
  several Google/Nest devices also listen on 9000 (they answer with a TLS
  alert). It was `.107`, MAC from a contract manufacturer (GSD Viet Nam),
  not Reolink's own `ec:71:db`. Turning **HTTP** on in the app
  (⚙️ → Network → Advanced → Server Settings) made it answer
  `"please login first"` on `/api.cgi`.
- The HTTP API only takes **users on the camera** (⚙️ → User Management),
  not the Reolink cloud account. `rspCode -7` means a wrong login.
  **Five failures lock it**, so nothing here retries a refused login. The
  pod waits 10 minutes after one.
- `Snap` with `&width=640&height=360` gives the sub stream: 40 KB in 0.6 s,
  against 1.3 MB for 4K. Other sizes are ignored. The live view uses the
  small one, and counting will use full 4K stills.

The login is in `~/.config/jasbros/reolink.env` (mode 600) on the laptop.
`./jasbros candy camera` checks it, saves a snapshot to the gitignored
`clip/` folder, and puts it in the cluster as the Secret `candy-watch-camera`,
built in Python and piped in so the password is never on a command line.
The chart reads it with `envFrom` (`optional`). The camera's address is in
`infra/inventory.yaml` under `cameras`.

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
| C4 | Should it say who? It can't tell the kids apart | No names. "Hey, you", or by costume: "Hey, witch!" |
| C5 | Costume: from the first frame when someone walks up, before any candy is taken | Yes, step 3: Claude names it, and the demo takes it as an argument until then |
