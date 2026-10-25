// A case for the speaker, in two printed parts.
//
//     ./case/build.sh            base.3mf and lid.3mf, ready for Bambu Studio
//     ./case/build.sh --preview  PNGs of each part and of the assembly
//
// What it holds
// -------------
// A Raspberry Pi 4 lying flat in the bottom, and the reSpeaker XVF3800
// 4-mic array as a ceiling above it, microphones and LEDs facing up. The
// whole thing is a puck that stands on the speaker: 140 mm across and
// 43 mm tall. The lid drops on and turns fifteen degrees to lock, and the
// Pi clips onto four sprung posts, so there is no fastener anywhere in it
// — nothing to buy but the plastic and the leads. PI_MOUNT = "screw" puts
// the M2.5 pilots back for anyone who would rather.
//
// Every number below that describes the array was taken from Seeed's own
// 2D mechanical drawing rather than from a ruler, except where a comment
// says otherwise. The board is 100 mm across; the four microphones sit on
// a 66 mm square whose centre is 1 mm below the centre of the board, which
// is the sort of detail that is invisible until four holes don't line up.
//
// What it deliberately does not do
// --------------------------------
// It does not screw to the array's mounting holes. The drawing dimensions
// them in a way I could not read without guessing, and a guess here costs
// a print. Instead the board rests on four shelves and is clamped at its
// rim by the lid, which needs only the outside diameter — a number the
// drawing states outright.
//
// It does not sit flat on the speaker either. It stands on four rubber
// feet, and that gap is not decoration: it is where the cooling air gets
// in, and it is what stops the cabinet's own vibration being conducted
// straight into the microphones. The echo canceller on the XVF3800 is
// good at sound that arrives through the air and has nothing at all to
// say about sound that arrives through the furniture.

part = "assembly";          // "base", "lid", "assembly"
$fn  = 96;

/* ---- the printer and the plastic ------------------------------------ */

FIT   = 0.4;   // clearance between parts, PLA on a P1S
WALL  = 2.4;   // six perimeters at 0.4 mm
FLOOR = 2.0;

/* ---- reSpeaker XVF3800 4-Mic Array v1.1 ----------------------------- */
// Seeed 2D drawing: respeaker_xvf3800_2d_mechanical_drawing.pdf

BOARD_D  = 100.0;   // stated on the drawing as the board outline
BOARD_T  = 1.2;     // stated
LED_R    = 32.0;    // measured off the drawing at 600 dpi, 10 LEDs agreeing
LED_N    = 12;
LED_A0   = 15;      // first LED at 15 degrees, then every 30
MIC_DX   = 33.0;    // half of the stated 66.00 mm mic square
MIC_TOP  = 32.0;    // stated: 66.00 overall, 34.00 below the board centre
MIC_BOT  = -34.0;

// Height of whatever stands on each face of the array. The drawing gives
// 5.66 on one side and about 6.2 overall on the other. The tall things on
// the microphone face are all at the very edge — the USB-C socket and the
// headphone jack — so the lid can come down close over the middle, which
// is what keeps the microphone ports short.
UNDER_ARRAY = 6.5;
LID_GAP     = 4.0;  // lid underside above the array's top face

/* ---- Raspberry Pi 4B ------------------------------------------------ */

PI        = [85, 56];
PI_T      = 1.4;
PI_TALL   = 17.5;   // USB-A shells, the tallest thing on the board
PI_HOLES  = [58, 49];
// The mounting holes are not centred on the board: they sit 3.5 mm in
// from three edges, which puts their centre 10 mm off the board's.
PI_HOLE_OFF = -10;
PI_STANDOFF = 5.0;
PI_PILOT    = 2.4;  // M2.5 self-tapping, if you'd rather use screws

// How the Pi is held down: "clip" snaps it onto four posts and needs no
// screws at all, "screw" keeps the M2.5 pilots.
PI_MOUNT = "clip";

// The snap itself. The Pi's mounting holes are 2.7 mm, so a 2.4 mm post
// drops through with a little to spare and a 0.6 mm hook has to bend
// 0.45 mm to follow it.
//
// That bend is the whole design. Each half is a cantilever 1.2 mm thick
// and PI_CLIP_DEEP + the stem long, and the strain at the root is
// 3.t.d / 2.L^2 — 1.4% here, against PLA yielding somewhere around 2 to
// 3%. Shortening the split is what breaks it: at 4 mm the same hook is
// 2.6% and cracks on the first push. The split runs well down into the
// standoff for that reason, and stops a millimetre above the underside so
// it never becomes a hole in the floor.
PI_CLIP_D    = 2.4;   // the post, through the board
PI_CLIP_BARB = 0.6;   // how far the hook stands out, per side
PI_CLIP_LEAD = 1.8;   // the cone above it, which does the pushing-apart
PI_CLIP_SLOT = 0.9;   // the split, two extrusions wide
PI_CLIP_DEEP = 6.0;   // how far that split runs down

/* ---- the case ------------------------------------------------------- */

// 140, not 122. At 122 the case was a right-angle-plug case and I had not
// said so: a Pi's USB-C power socket sits 34.5 mm off the centre line, so
// its plug had 19.4 mm before it met the wall and needed 26, and the USB-A
// carrying the array's lead had 14.1 and needed 20. Nine more millimetres
// of radius buys 30.1 and 23.1, and every plug goes in straight.
CASE_D  = 140;
LID_T   = 2.5;
FOOT_D  = 10;       // stick-on silicone bumpers
FOOT_H  = 1.2;
// The opening is centred on where the plugs actually come out, which is
// not where the case's own axis is. The power plug meets the wall at 239
// degrees, micro-HDMI at 252, and the array's own sockets sit between 250
// and 295. So: 230 to 302.
REAR      = 266;
REAR_WIDE = 72;

// And a second doorway over the Pi's short edge, where Ethernet and the four
// USB ports face. Nothing in the build needs it — the array's lead lives
// inside — but it is the difference between a Pi you can put a network cable
// or a keyboard into and one you have to unscrew first. Ethernet had 20.7 mm
// against the 22 a plug wants; with the wall gone it simply passes through.
//
// Centred on 0 degrees, because that is where those plugs cross the wall:
// Ethernet at 338-351, the USB 3.0 pair at 354-6, the USB 2.0 pair at 10-21.
PORTS      = 0;
PORTS_WIDE = 50;
/* ---- the bayonet ---------------------------------------------------- */
// The lid drops on and turns fifteen degrees. No screws, no inserts,
// nothing in the box but plastic.
//
// The catch is where it can live. Both doorways run from 5 mm to the rim,
// so above the Pi the wall is simply not there between 230 and 302 degrees
// or between 335 and 25 — most of one side. The four columns are there
// regardless: they stand clear of the wall, they already reach the lid and
// already carry its weight. So the columns grow a head, and the lid's
// skirt grows four lugs that turn in underneath it.
//
// Which way round matters, and is easy to get backwards. The lid's lug
// must finish UNDER the base's head. Lift the lid and the lug drives up
// into the head, which is what stops it. A head on the base sitting under
// a roof on the lid looks the same in a drawing and holds nothing at all:
// lifting the lid takes the roof away with it.
//
// The lid sits on the tops of the columns from the moment it goes on, so
// there is nothing for a long ramp to pull down — it would only rub. The
// head's underside is flat at BAY_GAP above the lug for the whole turn and
// dips by BAY_NIP over the last BAY_NIP_A degrees. So it turns freely and
// then tightens, and that last dip is what preloads the joint and keeps it
// from rattling. Turning stops against a block at the far end.
BAY_TRAVEL = 15;     // degrees from dropped-on to shut
BAY_LUG_W  = 10;     // degrees of lug
BAY_LUG_Z0 = 0.5;    // lug underside, above the seam
BAY_LUG_H  = 1.5;
BAY_GAP    = 0.15;   // running clearance under the head, while it turns
BAY_NIP    = 0.05;   // and how far past the lug the last bit of it comes,
BAY_NIP_A  = 3.0;    // over this many degrees, so it tightens at the end
BAY_CLR    = 0.5;    // degrees of slack behind a lug at the open end
BAY_MARK   = 90;     // where the pips go, on a clear stretch of wall

// Radii. The lug reaches in from the skirt, the head reaches out from the
// column, and they overlap by 1.6 mm — that overlap is the whole catch.
BAY_STEM   = 65.2;   // the column, above the seam, turned down to this
BAY_LUG_R  = 65.6;   // how far in the lid's lug reaches
BAY_HEAD_R = 67.2;   // how far out the head reaches, a FIT inside the skirt

/* ---- the logo on the lid -------------------------------------------- */
// "arcs", "loop", "wordmark" or "none".
LOGO       = "arcs";
LOGO_FONT  = "Avenir Next Condensed:style=Bold";
// Cut into the top face, not raised off it. The lid prints face down, so a
// recess is simply a gap in the first three layers — crisp, no supports,
// and nothing proud to catch a child's fingernail. Raised lettering would
// have to print into the plate, which is not a thing.
LOGO_DEPTH = 0.6;
// Everything must live inside the LED ring. The windows sit at r = 32 and
// open Ø5 at the top face, so their inner edge is at 29.5; 26 leaves a
// clear ring of daylight between the mark and the lights.
LOGO_R     = 28;

// Where the columns stand. Not evenly spaced, and the odd one took two
// tries. It began at 225, where it lay across the path the power plug takes
// to the wall. Moved to 200, it cleared the plug and ran straight through
// the Raspberry Pi instead — at 200 degrees the Pi's own corner reaches
// r=45.2 and the shelf starts at 44. At 180 the Pi reaches only 42.5, so
// the shelf clears it by 1.5 mm, and 180 is clear of the opening and of the
// plug as well. The other three were right the first time.
COLS = [45, 135, 180, 315];

// Heights, stacked from the bench up.
Z_PI      = FLOOR + PI_STANDOFF;
Z_PI_TOP  = Z_PI + PI_T + PI_TALL;
Z_BOARD   = Z_PI_TOP + 2.5 + UNDER_ARRAY;
Z_TOP     = Z_BOARD + BOARD_T;          // top face of the array
Z_SEAM    = Z_TOP;                      // base ends, lid begins
Z_LID     = Z_TOP + LID_GAP;            // lid underside
H         = Z_LID + LID_T;              // overall

// The bayonet, now that the seam has a height. The lug sits just above the
// seam, inside the skirt; the roof of the race is what stops the lid lifting.
BAY_Z0   = Z_SEAM + BAY_LUG_Z0;         // the lid's lug
BAY_Z1   = BAY_Z0 + BAY_LUG_H;
BAY_RUN  = BAY_Z1 + BAY_GAP;            // head underside, while it turns
BAY_SHUT = BAY_Z1 - BAY_NIP;            // head underside, at the stop

R_OUT = CASE_D / 2;
R_IN  = R_OUT - WALL;

/* ---- the logo ------------------------------------------------------- */

// A 2D arc: a ring, trimmed to a wedge.
module arc2d(r, w, from, to, steps = 48) {
    intersection() {
        difference() {
            circle(r = r + w / 2);
            circle(r = r - w / 2);
        }
        polygon(concat([[0, 0]],
            [for (i = [0 : steps])
                let (a = from + (to - from) * i / steps)
                [(r + w) * cos(a), (r + w) * sin(a)]]));
    }
}

// Sound leaving a point. Three arcs and the thing they come from — the
// same gesture the speaker makes with its ring, drawn at a different size
// and cut short, so it reads as a mark rather than a second ring.
module logo_arcs() {
    translate([0, 5.5]) {
        circle(r = 2.3, $fn = 48);
        for (r = [5.8, 9.6, 13.4]) arc2d(r, 2.0, 32, 148);
    }
    translate([0, -12])
        text("JASBROS", size = 8.2, font = LOGO_FONT,
             halign = "center", valign = "center", spacing = 1.04);
}

// A loop with one break in it. The speaker keeps almost everything in the
// house; exactly one step of the loop leaves. That is the whole idea of
// the thing, and it happens to draw well.
module logo_loop() {
    translate([0, 6]) arc2d(11.5, 3.6, -55, 235);
    translate([0, -12])
        text("JASBROS", size = 8.2, font = LOGO_FONT,
             halign = "center", valign = "center", spacing = 1.04);
}

// Just the name, as large as the ring allows.
module logo_wordmark() {
    text("JASBROS", size = 9.5, font = LOGO_FONT,
         halign = "center", valign = "center", spacing = 1.02);
}

module logo_2d() {
    if (LOGO == "arcs") logo_arcs();
    else if (LOGO == "loop") logo_loop();
    else if (LOGO == "wordmark") logo_wordmark();
}

/* ---- helpers -------------------------------------------------------- */

// A pie slice, for windows and for the gaps in rings.
module sector(r, h, from, to) {
    step = 15;
    n = max(1, ceil((to - from) / step));
    for (i = [0 : n - 1])
        rotate([0, 0, from + i * (to - from) / n])
            linear_extrude(h)
                polygon([[0, 0],
                         [r, 0],
                         [r * cos((to - from) / n), r * sin((to - from) / n)]]);
}

module led_positions()
    for (i = [0 : LED_N - 1])
        rotate([0, 0, LED_A0 + i * 360 / LED_N]) translate([LED_R, 0, 0]) children();

module mic_positions()
    for (p = [[MIC_DX, MIC_TOP], [-MIC_DX, MIC_TOP],
              [MIC_DX, MIC_BOT], [-MIC_DX, MIC_BOT]])
        translate([p[0], p[1], 0]) children();

// One snap post, standing on a standoff. The hook's underside is flat and
// faces down — a 0.6 mm overhang, which the printer bridges without
// noticing — and the cone above it is what the board rides up as it goes
// on. The base prints floor-down, so all of this prints the right way up.
module pi_clip_post() {
    stem = PI_T + 0.2;          // the board, and a little daylight
    translate([0, 0, Z_PI]) {
        cylinder(d = PI_CLIP_D, h = stem + 0.01);
        translate([0, 0, stem])
            cylinder(d1 = PI_CLIP_D + 2 * PI_CLIP_BARB,
                     d2 = PI_CLIP_D - 0.6, h = PI_CLIP_LEAD);
    }
}

// The split that lets it flex, cut after the post is made.
module pi_clip_slot() {
    wide = PI_CLIP_D + 2 * PI_CLIP_BARB + 2;
    translate([-PI_CLIP_SLOT / 2, -wide / 2, Z_PI - PI_CLIP_DEEP])
        cube([PI_CLIP_SLOT, wide,
              PI_CLIP_DEEP + PI_T + 0.2 + PI_CLIP_LEAD + 1]);
}

module pi_hole_positions()
    for (x = [-1, 1], y = [-1, 1])
        translate([PI_HOLE_OFF + x * PI_HOLES[0] / 2, y * PI_HOLES[1] / 2, 0])
            children();

// Is angle `a` inside the arc from `lo` to `hi`? Written to survive an arc
// that crosses zero, which the port doorway does.
function in_arc(a, lo, hi) =
    (lo <= hi) ? (a >= lo && a <= hi) : (a >= lo || a <= hi);

// A ring of vertical slots through the wall, skipping the two doorways. One
// slot per angle, cut from the outside in — a bar through the middle would
// quietly cut the far side of the case as well, including the parts meant to
// be left whole.
module wall_slots(z0, z1, count, width, skip = []) {
    for (i = [0 : count - 1]) {
        a = i * 360 / count;
        if (!in_arc(a, REAR - REAR_WIDE / 2 - 4, REAR + REAR_WIDE / 2 + 4) &&
            !in_arc(a, (PORTS - PORTS_WIDE / 2 - 4 + 360) % 360,
                       (PORTS + PORTS_WIDE / 2 + 4) % 360) &&
            len([for (s = skip) if (in_arc(a, (s[0] + 360) % 360,
                                              (s[1] + 360) % 360)) 1]) == 0)
            rotate([0, 0, a])
                translate([R_IN - 1, -width / 2, z0])
                    cube([WALL + 2, width, z1 - z0]);
    }
}

// A slice of a ring: r0 to r1, z0 to z1, between two angles. Every part
// of the bayonet is made of these.
module ring_wedge(r0, r1, z0, z1, a0, a1) {
    rotate([0, 0, a0])
        rotate_extrude(angle = a1 - a0, $fn = 240)
            translate([r0, z0]) square([r1 - r0, z1 - z0]);
}

// Where a lug sits when the lid is shut, and the arc of head it turns
// under. The head starts a clearance past where the lug drops in, so the
// lug has somewhere to go straight down.
function bay_lug_lo(a)  = a - BAY_LUG_W / 2;
function bay_lug_hi(a)  = a + BAY_LUG_W / 2;
// The head runs from just behind the lug's resting place to just short of
// where the lug drops in — any further and the lug could not get down.
function bay_head_lo(a) = a - BAY_LUG_W / 2 - 0.3;
function bay_head_hi(a) = a - BAY_LUG_W / 2 + BAY_TRAVEL - BAY_CLR;

// The head on one column: flat for most of its length, dipping over the
// last few degrees onto the lug. The dip is at the low end, because the
// lug turns that way and only its trailing edge ever reaches there — put
// it at the other end and the lug would ride over it the whole way round.
// Cut as a staircase of twenty wedges, each riser well under a layer.
module bay_head(a, steps = 20) {
    lo = bay_head_lo(a);
    for (i = [0 : steps - 1])
        ring_wedge(BAY_STEM, BAY_HEAD_R,
                   BAY_SHUT + (BAY_RUN - BAY_SHUT) * i / steps, Z_LID,
                   lo + BAY_NIP_A * i / steps,
                   lo + BAY_NIP_A * (i + 1) / steps + 0.15);
    ring_wedge(BAY_STEM, BAY_HEAD_R, BAY_RUN, Z_LID,
               lo + BAY_NIP_A, bay_head_hi(a));
}

// What the lug runs into at the end. Turning stops here, and that is how
// you know it is shut.
function bay_stop_lo(a) = bay_lug_lo(a) - 3.5;
module bay_stop(a)
    ring_wedge(BAY_STEM, BAY_HEAD_R, Z_SEAM, Z_LID,
               bay_stop_lo(a), bay_lug_lo(a) - 0.4);

// What the head and the stop stand on. A column is a 9 mm rib — about
// eight degrees where the head is — and the head and stop together want
// eighteen. Printed off the rib alone, most of both would start in mid
// air. So the rib flares into a wide shelf below the seam, and the head
// and stop are then a 2 mm step off that, which needs nothing to hold it
// up. The flare is 41 degrees off vertical, and it all happens inboard of
// where the lug turns.
module bay_prop(a) {
    lo = bay_stop_lo(a);
    hi = bay_head_hi(a);
    hull() {
        ring_wedge(58, BAY_STEM, Z_SEAM - 7, Z_SEAM - 6.9, a - 3.5, a + 3.5);
        ring_wedge(58, BAY_STEM, Z_SEAM - 0.1, Z_SEAM, lo, hi);
    }
    ring_wedge(58, BAY_STEM, Z_SEAM, Z_LID, lo, hi);
}

// The lugs, standing in from the skirt. These are on the lid.
module bay_lugs()
    for (a = COLS)
        ring_wedge(BAY_LUG_R, R_IN + 0.01, BAY_Z0, BAY_Z1,
                   bay_lug_lo(a), bay_lug_hi(a));

// A pip on the outside, so you can see where to start and where it stops.
module bay_pip(a, z0, z1)
    rotate([0, 0, a]) translate([R_OUT - 0.35, 0, (z0 + z1) / 2])
        rotate([0, 0, 45]) cube([1.6, 1.6, z1 - z0], center = true);

// Cool air comes in under the feet and up through here. The pattern is
// held clear of the Pi's standoffs: a standoff printed over the edge of a
// slot has nothing to stand on.
module floor_vents() {
    difference() {
        for (i = [0 : 11]) rotate([0, 0, i * 30])
            for (r = [14 : 9 : R_IN - 10])
                translate([r, 0, FLOOR / 2])
                    cube([5, 3, FLOOR + 2], center = true);
        pi_hole_positions() translate([0, 0, -1]) cylinder(d = 13, h = FLOOR + 3);
    }
}

/* ---- the base ------------------------------------------------------- */

module base() {
  union() {
    difference() {
        union() {
            // Shell.
            difference() {
                cylinder(r = R_OUT, h = Z_SEAM);
                translate([0, 0, FLOOR]) cylinder(r = R_IN, h = H);
            }
            // Four columns against the wall. The inner step carries the
            // array; the outer part carries the lid and its bayonet.
            // Both start well outboard of the Pi, which occupies the middle
            // of the case up to Z_PI_TOP.
            for (a = COLS) rotate([0, 0, a]) {
                translate([BOARD_D / 2 - 6, -3, 0])
                    cube([R_IN - BOARD_D / 2 + 6, 6, Z_BOARD]);
                translate([BOARD_D / 2 + 0.5, -4.5, 0])
                    cube([R_IN - BOARD_D / 2 - 0.5, 9, Z_LID]);
            }
            // Standoffs for the Pi, and the posts it snaps onto.
            pi_hole_positions()
                cylinder(d = 6, h = Z_PI);
            if (PI_MOUNT == "clip") pi_hole_positions() pi_clip_post();
        }

        // The columns stop at the lid.
        translate([0, 0, Z_LID]) cylinder(r = R_OUT + 1, h = H);

        // The array drops into a shallow recess so it cannot slide.
        translate([0, 0, Z_BOARD]) cylinder(r = BOARD_D / 2 + FIT, h = H);

        // Above the seam, turn the columns down to the stem so the lid's
        // lugs have somewhere to sweep. Their corners used to reach R_IN
        // exactly, which was fine for a lid that only ever went straight
        // down and is not fine for one that turns.
        difference() {
            translate([0, 0, Z_SEAM]) cylinder(r = R_OUT + 5, h = H);
            translate([0, 0, Z_SEAM - 1]) cylinder(r = BAY_STEM, h = H);
        }

        // Either the split that makes the posts springy, or the pilots for
        // screws — not both.
        if (PI_MOUNT == "clip")
            pi_hole_positions() pi_clip_slot();
        else
            pi_hole_positions() translate([0, 0, FLOOR])
                cylinder(d = PI_PILOT, h = Z_PI);

        // The rear opening. It serves the Pi's port edge low down and the
        // array's own sockets at the top, which stand at the very rim of
        // the board and have to get out somehow.
        //
        // It runs right up to the rim of the base rather than stopping
        // short of it. A window with a wall above it would need the printer
        // to bridge fifty millimetres across its top, unsupported, and a
        // sagging lintel is a worse thing to own than a taller doorway.
        translate([0, 0, 5]) rotate([0, 0, REAR - REAR_WIDE / 2])
            sector(R_OUT + 1, Z_SEAM - 5 + 0.01, 0, REAR_WIDE);
        translate([0, 0, 5]) rotate([0, 0, PORTS - PORTS_WIDE / 2])
            sector(R_OUT + 1, Z_SEAM - 5 + 0.01, 0, PORTS_WIDE);

        // Side and floor venting. A Pi 4 running the wake word all day is
        // a 5 W heater, and PLA gives up at about 60 C.
        wall_slots(8, 24, 28, 3.5);
        wall_slots(Z_PI_TOP + 1, Z_SEAM - 1.5, 28, 3.5);
        floor_vents();

        // Feet.
        for (a = [45, 135, 225, 315])
            rotate([0, 0, a]) translate([R_OUT - 14, 0, -0.01])
                cylinder(d = FOOT_D, h = FOOT_H);
    }

    // A head on each column for the lid's lugs to turn under, the block
    // that stops them, and two pips below the seam: the lid's own pip
    // starts at one and finishes at the other.
    for (a = COLS) { bay_prop(a); bay_head(a); bay_stop(a); }
    bay_pip(BAY_MARK, Z_SEAM - 4.5, Z_SEAM - 0.5);
    bay_pip(BAY_MARK + BAY_TRAVEL, Z_SEAM - 4.5, Z_SEAM - 0.5);
  }
}

/* ---- the lid -------------------------------------------------------- */

module lid() {
  union() {
    difference() {
        union() {
            translate([0, 0, Z_LID]) cylinder(r = R_OUT, h = LID_T);
            // Skirt, down to meet the base.
            difference() {
                translate([0, 0, Z_SEAM]) cylinder(r = R_OUT, h = LID_GAP);
                translate([0, 0, Z_SEAM - 1]) cylinder(r = R_IN, h = LID_GAP + 2);
            }
            // Nothing hangs down onto the board. The first draft had a ring
            // here to clamp the rim, until the mic positions came out of the
            // drawing at a radius of 46 to 47 mm — which is where that ring
            // would have been, sitting squarely on all four microphones. The
            // board is located by the four columns and held down by its own
            // weight; if it ever rattles, a pad of foam on the lid is a
            // better answer than a ring drawn over parts I cannot see.
        }

        // Twelve windows, one per WS2812. Wider underneath than on top, so
        // each one gathers a wide cone of light and still prints without
        // support: the hole closes by 1 mm over 2.5 mm of height.
        led_positions() translate([0, 0, Z_LID - 0.01])
            cylinder(d1 = 7, d2 = 5, h = LID_T + 0.02);

        // Four microphone ports, kept wide and short. A narrow tube over a
        // MEMS microphone is a resonator; an open window is not.
        mic_positions() translate([0, 0, Z_LID - 0.01])
            cylinder(d1 = 5.5, d2 = 4.5, h = LID_T + 0.02);

        // The logo, cut into the top face.
        if (LOGO != "none")
            translate([0, 0, H - LOGO_DEPTH])
                linear_extrude(LOGO_DEPTH + 0.01)
                    intersection() {
                        logo_2d();
                        circle(r = LOGO_R, $fn = 96);
                    }


        // The rear notch, continuing the base's opening up through the
        // skirt so a right-angle plug can get out.
        translate([0, 0, Z_SEAM - 0.01]) rotate([0, 0, REAR - REAR_WIDE / 2])
            sector(R_OUT + 1, LID_GAP + 0.02, 0, REAR_WIDE);

        // Warm air leaves at the top, but not through a lug.
        wall_slots(Z_SEAM + 1, Z_LID - 0.6, 28, 3,
                   [for (a = COLS) [a - 8, a + 8]]);
    }

    // The four lugs that turn in under the heads, and the pip that lines
    // up with the base's two.
    bay_lugs();
    bay_pip(BAY_MARK, Z_SEAM + 0.5, Z_SEAM + 3.5);
  }
}

/* ---- what to render ------------------------------------------------- */

module array_board() {
    color("#1a1a1a") difference() {
        translate([0, 0, Z_BOARD]) cylinder(d = BOARD_D, h = BOARD_T);
        led_positions() translate([0, 0, Z_BOARD - 1]) cylinder(d = 3, h = 4);
    }
}

module pi_board()
    color("#0a6b3d") translate([0, 0, Z_PI]) cube([PI[0], PI[1], PI_T], center = true);

module everything() {
    base();
    color("#c8c8c8", 0.55) lid();
    array_board();
    pi_board();
}

// Each part is exported standing on z = 0 the way it should be printed.
// The base goes down as it sits. The lid goes on its face: printed the
// right way up, the whole underside of its top disc would be printing over
// thin air. Upside down, the top surface is laid straight onto the plate —
// the best surface the printer can make, on the one face anybody looks at —
// and every window widens as it rises, which is an overhang of 22 degrees
// and needs nothing to hold it up.
if (part == "base") base();
else if (part == "lid") translate([0, 0, H]) rotate([180, 0, 0]) lid();
else if (part == "section") {
    // Half the puck, so the stack of heights can be checked by eye.
    difference() {
        everything();
        translate([-200, 0, -1]) cube([400, 200, 300]);
    }
}
else {
    base();
    color("#c8c8c8", 0.55) lid();
    array_board();
    pi_board();
}
