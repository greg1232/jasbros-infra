# A case for the speaker

A printed puck: the Raspberry Pi lying in the bottom, the reSpeaker XVF3800
array as a ceiling above it with its microphones and lights facing up.
**140 mm across, 43 mm tall.**

    ./case/build.sh              base.3mf and lid.3mf
    ./case/build.sh --preview    ...and PNGs to look at first

Two parts, no supports, no AMS, no adapters and **no fasteners of any
kind**: the lid turns fifteen degrees to lock and the Pi clips onto four
posts. Nothing to buy but the plastic and the leads. Open
`case/speaker-case.scad` to change anything; every dimension is named at
the top of the file.

## Where the numbers came from

The array's dimensions are from Seeed's own 2D mechanical drawing, not from
a ruler:

| | |
|---|---|
| Board | Ø100.0 mm, 1.2 mm thick |
| Microphones | on a 66 × 66 mm square, whose centre is **1 mm below** the centre of the board |
| LEDs | 12 × WS2812 at r = 32.0 mm, one every 30° starting at 15° |

The LED ring is the one number the drawing does not state. It was measured
off a 600 dpi render, scaling from the stated Ø100 outline: ten of the
twelve footprints agreed on 32.0 mm to within 0.3 mm.

The mic square being 1 mm off centre is the sort of thing that is invisible
until four holes don't line up.

## Why it is this big

A plug's clearance is not the case's radius. It is the distance from the
socket face to the wall **along the plug's own axis**, and the Pi's sockets
do not sit on the centre line — the USB-C power socket is 34.5 mm off it.

| along the plug's axis | at Ø140 | a plug needs |
|---|---|---|
| USB-C power | 30.1 | ~26 |
| USB-A, the array's lead | 23.1 | ~20 |
| USB-A, outer pair | 20.5 | ~20 |
| micro-HDMI | 36.3 | ~22 |
| Ethernet | 20.7 | ~22 |

140 is the diameter at which straight plugs fit. Nine millimetres less of
radius is the difference between a case that takes the leads already on the
desk and one that needs a bag of right-angle adapters.

The array's lead goes into one of the **blue USB 3.0 ports** — those are the
pair with 23.1 mm of clearance; the outer pair has 20.5 and is tighter.

Ethernet is the one measurement still short, by about 1.3 mm, which stops
mattering because there is a doorway on that side for it to pass through.

## Where the wires go

Three leads. Two leave through the rear opening and one never leaves at all.

    rear doorway, 230 to 302 deg, floor to rim
        in    Pi power, USB-C, straight from the adapter
        out   the array's 3.5 mm lead, to the speaker

    port doorway, 335 to 25 deg, floor to rim
        nothing, in normal use — it is there so Ethernet and the
        four USB ports stay reachable without taking the lid off

    inside, never leaves
        the array's USB-C down to a Pi USB 3.0 port

Put the array in with **its sockets over the rear opening**, the same side
the Pi's power lead comes in.

The rear doorway is centred on 266°, not on the case's own 270° axis,
because that is where the plugs actually arrive: the power plug crosses the
wall between 236° and 243°, micro-HDMI between 249° and 255°, the A/V jack
between 276° and 282°, and the array's sockets sit between 250° and 295°.
All of them are inside 230–302.

The port doorway is centred on 0° for the same reason: Ethernet crosses at
338–351°, the USB 3.0 pair at 354–6°, the USB 2.0 pair at 10–21°.

Both doorways run from 5 mm up to the rim rather than stopping short of it.
A window with wall above it would need the printer to bridge seventy
millimetres unsupported, and a sagging lintel is a worse thing to own than a
taller doorway.

The four columns are at **45, 135, 180 and 315°** — not evenly spaced. 180
is the awkward one: it has to clear both the path the power plug takes to
the wall and the Raspberry Pi itself. At 200° the Pi's corner reaches
r = 45.2 mm and the shelf starts at 44; at 180° the Pi reaches only 42.5, so
the shelf clears it by 1.5 mm.

## The board can go in four ways round

The LED ring is at 15 + 30k degrees, and 90 is three times 30, so turning
the array by a quarter turn leaves all twelve windows exactly where they
were. The microphones are not so tidy — that 1 mm offset means each one
drifts 1.41 mm from its port — but the ports are 4.5 mm across at their
narrowest, so a microphone still has 0.34 mm of clearance.

Sockets over the opening is the orientation to prefer. Everything lines up
exactly.

**Which way up matters more than which way round.** The face carrying the
XMOS chip, the 2 × 12 header field and the white JST connector is the
*bottom*. The face with the twelve LEDs and the four microphone apertures
goes up, towards the lid. Upside down, the lid stands proud on the JST and
the lights and microphones are aimed into the Pi.

## What you need besides the plastic

- 4 × Ø10 mm stick-on silicone feet, about 2 mm thick
- A USB-C to USB-A lead for the array, 20 cm or so. Straight plugs are fine.
- A 3.5 mm lead from the array to the speaker

## The logo

Cut into the middle of the lid, inside the ring of lights.

    LOGO = "arcs"       sound leaving a point, JASBROS beneath  (default)
    LOGO = "loop"       a ring with one break in it, JASBROS beneath
    LOGO = "wordmark"   just the name, as large as the ring allows
    LOGO = "none"       a plain lid

Change the line at the top of `speaker-case.scad` and run `./case/build.sh`.
`case/logo-options.png` shows all four.

### Why it is cut in, not raised

The lid prints face down, so its top surface is laid straight onto the
plate. A recess is then simply a gap in the first three layers: crisp edges,
no supports, and nothing standing proud for a child's fingernail to catch.
Raised lettering would have to print *into* the plate, which is not a thing.

It is 0.6 mm deep — three layers at 0.2 — which gives it away free in a
second colour, with no AMS:

    layer 4   swap to the accent colour
    layer 5   swap back

Layers 1–3 carry the top face, so the logo is the only place the accent
shows through. Everything above layer 5 is buried.

### If you change the font

`JASBROS` in Avenir Next Condensed Bold is **50.7 mm wide at size 10** — a
third wider than it looks. The sizes in the file come from measuring the
rendered text and solving for the corner radius, because text that overruns
`LOGO_R` is sliced clean off at the intersection rather than scaled to fit,
and you get `ASBRO`. Measure a new font rather than trusting the shape of
the letters.

## Printing

P1S, PLA, 0.4 mm nozzle, 0.2 mm layers. Both parts are exported already
standing the way they should print, so drop them on the plate as they come.

- **base** — 3 walls, 15% gyroid, no supports. Prints floor-down.
- **lid** — 3 walls, 15%, no supports. Prints **face down**: the top surface
  is laid straight onto the plate, which is the best finish the printer can
  give it, and every window widens as it rises at 22° from vertical.

Nothing in either part bridges more than 4 mm.

## Assembly

1. Four silicone feet into the recesses in the underside of the base.
2. Pi onto the four posts, ports facing the rear opening. Line the
   mounting holes up over the cones and press it straight down until all
   four click.
3. Plug the array's lead into a blue USB 3.0 port and into the array. Plug
   the 3.5 mm lead in and feed it out of the rear opening.
4. Drop the array into the base, **microphones and LEDs facing up**, sockets
   towards the rear opening.
5. Lid on, with its pip against the **anticlockwise** one of the two pips
   on the base. Then turn it **clockwise**, about fifteen degrees, until it
   stops. Shut, the two pips line up.

## The Pi clips on

Four posts stand on the standoffs, one through each of the Pi's mounting
holes. Each is split down the middle so its two halves can squeeze
together, with a hook on top that springs out again over the board.

    press down                       held
      ___                          _/   \_
     /   \   <- cone spreads      |  hook | <- 0.45 mm of grip
    |     |     the halves        ===========  the board
    |  |  |                        |  |  |
    |  |  | <- the split           |  |  | <- 6 mm of flexure
    ===============================================

**The split is the design, not a detail.** Each half is a cantilever, and
the strain where it meets the standoff is `3·t·δ / 2·L²` — thickness,
deflection, and length squared. Length squared is what matters:

| hook | must bend | grips | strain at the root |
|---|---|---|---|
| 0.4 mm | 0.25 mm | 0.25 mm | 0.8% |
| **0.6 mm** | **0.45 mm** | **0.45 mm** | **1.4%** |
| 0.9 mm | 0.75 mm | 0.75 mm | 2.3% — too much |

PLA yields somewhere around 2 to 3%, so 0.6 mm leaves real margin. The same
hook on a 4 mm split would be 2.6% and would crack on the first push, which
is why the split runs 6 mm down into the standoff — and why it stops a
millimetre short of the underside, so it never becomes a hole in the floor.

A Pi 4 weighs 46 grams. 0.45 mm of hook is plenty.

**To get it out again**, squeeze the two halves of each post together with
a fingernail or tweezers and lift that corner. Prefer screws? Set
`PI_MOUNT = "screw"` at the top of `speaker-case.scad` and the posts become
2.4 mm pilots for four M2.5 × 6 again.

## The lid is a bayonet

Four lugs stand in from the skirt. Four heads stand out from the tops of
the columns. The lugs drop past the heads, you turn, and they finish
underneath them.

    dropped on                        turned clockwise to shut
      lug ──┐   ┌── head                   ┌─── head ───┐
            │   │                          │   ┌────┐   │
            └───┘   clear of each other    └───┤ lug│───┘   trapped
                                                └────┘

**Which way round is the whole thing, and it is easy to get backwards.**
The lid's lug has to finish *under* the base's head. Lift the lid and the
lug drives up into the head, which is what stops it. A lug on the base
sitting under a roof on the lid draws identically and holds nothing at all
— lifting the lid simply takes the roof away with it.

| | |
|---|---|
| turn, open to shut | 15° |
| lug | 10° wide, 1.5 mm tall, reaching in to r = 65.6 |
| head | reaching out to r = 67.2, so it overlaps the lug by 1.6 mm |
| running clearance under the head | 0.15 mm |
| nip over the last 3° | 0.05 mm |

The lid rests on the tops of the columns from the moment it goes on, so
there is nothing for a long ramp to pull down — it would only rub. The
head's underside is flat for the whole turn and dips by 0.05 mm over the
last three degrees, which takes up the running clearance and preloads the
joint so it cannot rattle. Then the lug runs into a block and stops.

**What you feel is the stop, not the nip.** Five hundredths of a millimetre
is inside what the printer varies by anyway; the grip is a light preload,
and the thing that tells you it is shut is that it will not turn any
further.

### The props under the heads

A column is a 9 mm rib — about eight degrees of arc where the head sits —
and the head and its stop together want eighteen. Printed off the rib
alone, most of both would begin in mid-air. So the rib flares out into a
wide shelf below the seam, at 41° off vertical, and the head and stop are
then a 2 mm step off that shelf, which needs nothing to hold it up. All of
it happens inboard of r = 65.2, where the lug never goes.

### What it gives up against screws

A screwed lid clamps hard; a bayonet holds. If the puck ends up standing on
a loudspeaker, the screwed version would rattle less — the four silicone
feet are doing that job here, and if the wake word gets worse when music is
loud, softer feet are still the first thing to try.

## The feet are not decoration

The recesses in the underside are 1.2 mm deep, so a 2 mm foot leaves the
case standing a little under a millimetre off the surface below it. That gap
does two jobs.

It is where the cooling air gets in — the floor is a grid of slots, and if
the case sits flat they are all blocked. A Pi 4 running the wake word all
day is roughly a five watt heater in a closed box, and PLA starts to give up
around 60 °C. It is a narrow gap, so if yours runs hot, thicker feet are the
cheapest fix — they need no reprint — and a stick-on heatsink on the SoC is
the next one.

The other job is mechanical. If this puck stands on a loudspeaker, it is
standing on a box built to vibrate. The XVF3800's echo canceller is very
good at sound that reaches the microphones through the air and has nothing
whatever to say about sound that reaches them through the furniture. Rubber
feet break that path. If the wake word gets noticeably worse when music is
loud, that is the first thing to suspect, and softer feet are the first
thing to try.

## Two things this deliberately does not do

**It does not use the array's mounting holes.** The drawing dimensions them
in a way that cannot be read without guessing, and a guess there costs a
print. The board rests on four shelves and is located by four columns, which
needs only the outside diameter — a number the drawing states outright.

**Nothing presses down on the board.** The microphones sit at a radius of 46
to 47 mm, which is exactly where a clamping ring on the lid would land. If
the board rattles, a pad of foam on the underside of the lid is a better
answer than a ring drawn over parts that cannot be seen.
