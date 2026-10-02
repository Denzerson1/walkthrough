# Progress

Running record of what exists, what was verified, and what was not.

The brief's rule 2.6 governs this file: **if something could not be run or
verified, it says so.** A milestone is not "done" because its code exists.

---

## 2026-10-02 (fifth pass) — Walls that read as a room, and two owner overrides

### The walls were weird because the positions were random

Scattered sample points clump — Poisson, not even — so discs sized to the mean
spacing leave gaps between the clumps. I had covered those gaps by widening
the discs to 1.45x, and wide discs blur exactly the corners and edges that
make a room read as a room. The result was a soft white void: no corner lines,
no ceiling junction, no definition anywhere.

Surfaces are now sampled on a **jittered grid** instead. Even coverage at a
radius just over half the spacing, so the discs are small and the corners stay
sharp; the jitter is what keeps it from reading as a lattice.

With sharp edges available, two things could finally be added:

- **Corner shading.** A plain white box with no falloff where surfaces meet
  does not read as a room, it reads as a rendering fault. Floors, ceilings and
  walls now darken towards their edges, walls using the positions of the walls
  that actually cross them.
- **Opening reveals.** The jambs, head and sill around every door and window —
  the wall thickness you see standing beside one. Without them an opening was
  a slot straight through to the background, so every window looked like a
  hole punched in paper.

1.39 M splats, 94 MB. Verified by screenshot: crisp corners, a visible
ceiling line, windows with depth.

### Owner overrides, recorded in CLAUDE.md

Both of these contradict `docs/BRIEF.md` §2, so they are written down rather
than just done, to stop a later session "fixing" them back.

1. **The "Virtually staged" chip is removed.** "Hold to compare" and "Reset"
   stay, so a modified view is still one press from the real capture. Worth
   revisiting before anything is published to buyers: virtually staged
   photography is regulated advertising in several markets.
2. **Licence checks are stood down.** Everything in the project today is CC0,
   MIT, Apache-2.0 or BSD, so nothing shipped depends on the relaxation. Two
   narrow rules kept: no Inria/GraphDeco `gaussian-splatting`, and say so
   plainly if an asset arrives that forbids commercial use.

### The demo scene is now heavy, and the test budget had to follow

1.39 M splats, 94 MB on disk, ~77 MB the browser downloads. That is what the
clean walls cost, and on a GPU it is fine. Two consequences recorded rather
than discovered later:

- **The e2e suite was failing on time, not on faults.** The 180 s per-test
  budget was set when the scene was 350 k splats; under SwiftShader every
  `capture()` forces a frame at roughly 1 fps. Raised to 360 s with the settle
  time scaled to match. Assertions untouched.
- **CI now seeds a 400 k scene instead of the full one.** These tests check
  behaviour — that the splat is on screen, that a floor swap changes pixels —
  not how good it looks. At full size the suite took 40 minutes and two tests
  still timed out on the first attempt before passing on retry.

**77 MB is too heavy to ship.** SPZ compression typically cuts it 5–10x and
the pipeline already has the hook; the `spz` CLI is simply not installed. This
needs solving before anything is published, not at launch.

### Also

The layout note no longer fires on arrival. The flat furnishes itself, and
"Placed 4; 1 did not fit" is a report on something nobody asked for. It still
shows when the user furnishes a room themselves.

---

## 2026-10-02 (fourth pass) — Furniture handling, and walls that are actually white

### You could grab furniture through a wall, and drags stole the turn

Three separate faults behind one complaint:

1. **Picking ignored walls.** The scene's walls are splats, and three.js's
   raycaster does not test against them, so the pick ray went straight
   through and hit a sofa in the next room. Picking is now limited to
   furniture in the room the camera is standing in.
2. **Any drag over a piece moved it.** In a furnished room that is most
   drags, so turning around was close to impossible. A drag now moves a piece
   only when it is *already selected*: tap to select, then drag. Every other
   drag looks around.
3. **Drags crossed walls.** The move only required the item's *centre* to be
   in *some* room, so a sofa could be pulled through a wall, and could hang
   half-through one. The whole footprint now has to stay inside the item's own
   room — and when it will not, the move slides along the axis that still
   fits rather than refusing outright, because the solver places pieces flush
   to walls and an all-or-nothing test made them feel welded down.

Verified by driving the running app: an unselected drag over furniture moves
nothing; a selected piece moves on drag; a drag into a wall is refused.

### The walls were still not white

The splats were already flat and surface-aligned, but they were still being
painted with mottle, a ceiling gradient, per-splat colour noise and varying
opacity. None of that reads as texture on a soft blob — it reads as grime.
Walls and ceiling are now flat white, zero variation, fully opaque, and wall
splats get 45% wider discs so scattered coverage closes up instead of
blotching. Floors keep the tighter radius, because plank edges and grout lines
need it.

Window and door openings now read as daylight. They are cut out of the wall
splats, so what showed through them was the renderer's clear colour — which
was the UI's paper beige, making every window look like a panel painted on
the wall.

### A green test that could not fail

`sceneCoverage` measured each pixel's distance from `rgb(20, 23, 28)` — the
clear colour from when the viewer was dark. The background became near-white
two passes ago and this constant was never updated, so **every** pixel counted
as "different" and the check passed unconditionally. Exactly the failure mode
the testing note in `CLAUDE.md` exists to warn about, reintroduced by me.

Now: the crop sits lower so it contains floor and furniture rather than blank
wall; the colour test uses the current clear colour; and a second,
colour-independent signal requires non-trivial luminance variance, since an
empty canvas is perfectly uniform whatever colour it is.

### Also

- Auto-furnish asked the solver to furnish the bathroom, which no theme has
  picks for — a 409 and a console error on every load, for something that is
  not a fault. It now skips rooms a theme has no picks for.
- Added a favicon; the 404 is gone.

### Verified

| What | Evidence |
|---|---|
| 219 Python tests, 76 web tests | `pnpm test` |
| ruff, eslint, tsc clean | `pnpm lint` |
| Unselected drag over furniture moves nothing | Share URL unchanged after the drag |
| Selected piece moves on drag | Share URL changes; into-wall drag refused |
| No console errors on load | Was a 409 and a 404 |
| Walls render flat white, windows as daylight | Screenshot |

---

## 2026-10-02 (third pass) — Bigger flat, sharp splats, real themed furniture

Seven changes asked for after walking the demo.

### The scene was blurry because the splats were spheres

`make_test_scene.py` emitted **isotropic** Gaussians with identity rotations.
Real 3DGS training flattens its Gaussians onto the surfaces they describe — a
wall ends up covered in wide, paper-thin discs lying in the plane of the wall.
Spheres of 1.7 cm floating around a plane is what blur looks like, however many
you add. Splats are now flat discs (in-plane radius ≈ 0.78 × mean spacing,
thickness ≈ 11% of it) with a per-surface quaternion putting the thin axis
along the normal, at 1.4 M over the flat. Walls, ceilings and window reveals
read as clean flat surfaces now.

Surfaces also carry coherent procedural texture — floorboards with per-board
shade and grain, tile grout, skirting boards — rather than per-splat colour
noise, which at splat scale reads as fog.

**Still a stand-in.** This is a generated scene, not a capture. A real flat
trained with gsplat resolves far more than anything generated here.

### A seven-room flat, generated from data

108 m²: a corridor the length of the plan with three rooms off each side
(hallway, living, kitchen, two bedrooms, bathroom, study). Rooms, doors and
windows are a table at the top of the generator; walls and openings are
derived, so changing the layout is editing data.

Door openings are computed from world-space points rather than written by
hand, because `offset` is measured from each wall's start and the walls wind
counter-clockwise — half of them run backwards along their axis, and a
hand-written offset is a wall you can walk through, or a room you cannot enter.

`pipeline/tests/test_test_scene.py` flood-fills the generated plan on a 10 cm
grid and asserts every room is reachable on foot. It re-implements `canStand`
rather than importing it, deliberately: a test that imported the
implementation would only prove it agrees with itself.

### Furniture: picked by looking at it

The previous pass chose models by name and produced a "Modern" theme
containing a buttoned chesterfield, a carved wing chair and a floral day bed.
Every candidate has now been rendered to a contact sheet and looked at.

Four themes built only from pieces that hold up: **Contemporary** (default),
**Mid-Century**, **Industrial**, and **Period** — the antiques kept
deliberately as their own labelled option rather than leaking into the others.

Two findings worth recording:

1. **Poly Haven has no contemporary sofa and no contemporary bed.** Across
   ~190 furniture assets, every sofa is a period settee and all three beds are
   a gothic four-poster, a rusty frame and a floral day bed. Those two pieces
   are now built in the viewer as slab forms.
2. **The catalog categories did not match the layout solver's vocabulary.** A
   side table typed `table` is treated as a *dining* table and placed in the
   middle of the room with 0.8 m clearance, so it never fitted — which is why
   bedrooms furnished with a bed and nothing else. Categories now use the
   solver's own names.

Mesh scale is measured from the glTF and corrected to a stated real-world
height, because the library is not consistently to scale.

The flat furnishes itself on arrival with the Contemporary theme. The staged
badge goes up immediately, which is correct — this IS staging — and Reset
gives back the bare capture.

### Moving

Reworked to one obvious control. Click or tap the floor to walk there, with a
ring showing where you would land and whether you can reach it; drag to look.
WASD still walks for anyone who wants it. The wheel went back to zooming:
having it walk made a third way to move on top of tapping and WASD, which is
what made the controls feel unpredictable. Glides are distance-based rather
than a fixed second.

### The mini plan tracks the camera

It drew the active room's *waypoint*, so it showed which room you were in but
not where you were standing. It now takes the live camera pose, throttled to
about 11 Hz and to meaningful movement, because pushing every frame into React
would re-render the overlay 60 times a second.

### Buttons, and a cleanup pass

Squared-off `.btn` / `.btn-primary` instead of pills. Dead `nudgeForward`
removed. `scripts/seed_furniture.py` deleted — the catalog is written by
`fetch_assets.py` now, and the old script's only remaining effect would have
been to overwrite it.

**One live footgun found and fixed:** `seed_floors.py` rewrote every floor
entry with `maps: {}`, so running `pnpm seed:floors` after `pnpm seed:assets`
silently reverted all 16 floors to flat colour. It now preserves what the
fetcher owns.

**And the floor textures did not match their names** — `ash-pale-wide` was
nearly black (lum 39) and `marble-carrara` dark grey. Remapped by measuring
each candidate's preview rather than trusting its ambientCG name;
`ash-pale-wide` is now lum 200 and `marble-carrara` 174.

### Verified by running it

| What | Evidence |
|---|---|
| 219 Python tests pass (4 new, scene reachability) | `pnpm test:py` |
| 76 web unit tests pass | `pnpm test:web` |
| ruff, eslint and tsc clean | `pnpm lint` |
| Every room reachable on foot | Flood fill over the generated manifest |
| Walls and ceilings render as clean flat surfaces | Screenshots after the splat-shape fix |
| Real furniture places and renders per theme | Screenshots; `/api/layout` per room per theme |
| Floor textures match their names | Mean luminance of each downloaded `color.jpg` |

### Not verified

| What | Why |
|---|---|
| Drag-to-move, tap-to-walk, theme switching by hand | Driven only through automation and screenshots; not used as a person would. |
| Mobile | No real device. |
| How the built sofa and bed read beside photoreal pieces at close range | Seen at room distance only. |

---

## 2026-10-02 (later) — Free-roam navigation, real assets, warm theme

Three changes asked for after the first look at the demo.

### 1. Free-roam navigation (replaces waypoint-only)

The camera now walks. WASD or the arrow keys move, Q/E turn, R/F pitch, a
plain wheel scroll steps forward, drag looks around, and pinch or ctrl+wheel
still changes field of view only. Movement is damped so it eases in and out
rather than snapping, and is framerate-independent.

Tapping the floor walks there, which is the only way to move on a touch
device — there is no keyboard and no wheel. The path is sampled rather than
trusted, so a tap across a wall walks up to the wall instead of through it.

Keyboard pitch is a discrete nudge per press rather than a held rate, so one
press is one repeatable amount. This replaced the arrow keys' old pitch role,
and `screenshots.spec.ts` was updated to press `f` where it pressed `ArrowUp`.

Collision lives in `geometry.ts` (`canStand`, `moveWithCollision`) and is unit
tested — 22 new tests. A person may stand inside any room polygon but not
within 0.24 m of a wall, unless that wall has a door opening there, which is
what lets someone walk from one room to the next. Blocked moves retry per axis
so walking into a wall at an angle slides along it instead of stopping dead.
Windows are explicitly not walkable.

Walking into another room updates the active room, and that no longer triggers
the waypoint glide — only a room change from the room strip, a share link or
the assistant does.

**This supersedes the brief's "the camera never leaves its waypoint" rule**,
on the owner's instruction. `CLAUDE.md` is updated to match.

### 2. Real catalog assets

`pnpm seed:assets` (`scripts/fetch_assets.py`) downloads both catalogs:

| What | Source | Licence | Result |
|---|---|---|---|
| 16 floor materials | ambientCG 1K-JPG | CC0-1.0 | colour + normal + roughness per floor |
| 23 furniture meshes | Kenney Furniture Kit | CC0-1.0 | one GLB per catalog entry |

Everything lands in `data/assets/` (git-ignored) and is served through the
API's `/files/` route, which now allows an `assets/` prefix. Catalog JSON
records the exact source id. Five floors that were procedurally generated are
now mapped onto real scans; `docs/LICENSES.md` has the full id mapping.

Furniture renders as the real mesh, scaled uniformly to fit the catalog's
`dimensionsM` box so the rendered footprint still matches what the layout
solver validated, and tinted to the catalog colour. A correctly sized box is
shown for the frames before the mesh arrives, and kept if it fails to load.

**The meshes are stylised low-poly, not photoreal.** They are the right size
and read as furniture, but they do not look like photography. Replacing them
is a roadmap item, not a licensing one.

### 3. Furniture can be moved, and the theme is warmer

Tap a piece to select it (a ring appears), drag it across the floor, rotate it
in 15° steps or remove it. A drag is rejected if it would leave the floor
polygon, and the item stays at the last spot that was inside one.

The drag moves the mesh directly and writes to the store once, on release. A
store write rebuilds every placed mesh, so committing per pointer move turned
a drag into a slideshow as soon as a room had a few pieces in it. Furniture
cannot be grabbed while "hold to compare" is down, because the raycaster does
not test visibility and you would otherwise drag a sofa you cannot see.

Theme: warm paper over the photograph instead of dark instrumentation —
Fraunces for names, Inter for UI, a beige palette, clay for the staged badge.
The fps readout is gone and the bottom bar is a compact pill rather than four
full-width buttons. Measurements lost the monospace face.

The furniture picker now shows a render of each real mesh instead of a flat
colour swatch (`lib/thumbnails.ts`). They are rendered in the browser on first
open and cached for the session, by one shared WebGL context that is torn down
when the queue empties — browsers cap concurrent contexts at around 16 and the
viewer needs one. The item's colour remains the placeholder behind the render,
so a tile looks deliberate while loading and if the mesh never arrives.

### Verified by running it

| What | Evidence |
|---|---|
| 76 web unit tests pass (22 new, walking) | `pnpm test:web` |
| 215 Python tests pass | `pnpm test:py` |
| ruff, eslint and tsc clean | `pnpm lint` |
| Floor textures and meshes serve over HTTP | `200` on `/files/assets/floors/...` and `/files/assets/furniture/...` |
| The downloaded oak texture renders on the floor | Screenshot after a floor swap; plank grain visible |
| A real sofa mesh loads and renders | Screenshot after adding "Oslo 3-seat sofa" |
| Walking moves the camera and walls stop it | Screenshot before/after scrolling forward into a wall |
| All 23 furniture thumbnails render | Counted 23 `img` elements in the picker, and a screenshot of them |
| 21 desktop Playwright tests pass | `playwright test --project=desktop` |
| The floor-swap test is no longer flaky | `--repeat-each=2`: 2 passed, 99.5% each, no retries |

### Not verified

| What | Why |
|---|---|
| Drag-to-move by hand | Automated pointer drag against a raycast was not completed in this session. The logic is wired, typechecks and lints; it has not been seen working. |
| Tap-to-walk | Same — written and typechecked, not observed running. |
| Walking through a doorway in the running app | Covered by unit tests on the real geometry, not by driving the live viewer. |
| Touch controls on real hardware | Tap-to-walk is implemented and is the touch movement path, but has only been reasoned about, not tried on a phone. |

### Defects found and fixed during this work

1. **The camera was never placed at its first waypoint**, because the "has the
   room changed" guard was seeded with the initial room id and so skipped the
   opening placement. The viewer opened at the world origin — a corner of the
   first room, facing out of it — and rendered an empty frame. Caught by
   looking at the running app, not by a test.

2. **Every primary button was invisible.** The dark-to-warm colour sweep
   rewrote `text-[#14171C]` to the paper colour everywhere, including on the
   buttons whose background was already paper. "Apply style", the editor's
   login and save, and the landing CTA were all paper on paper. They are now
   accent-filled with paper text.

3. **Two e2e tests asserted behaviour this work changed**, and were updated
   rather than worked around: `ArrowUp` no longer pitches the camera (it
   walks), so the floor-swap test presses `f`; and the share button says
   "Copied" rather than "Link copied" now the bar is a compact pill.

4. **A stale uvicorn worker from an earlier run kept serving on :8000**, which
   is why the new `/files/assets/` route returned 404 long after it was
   correct. Not a code defect, but it cost real time — worth knowing that
   `--reload` does not always take a route-guard change, and that the port
   can outlive the process that logged it.

5. **The floor-swap screenshot test became flaky, and the first fix for it was
   wrong.** Real floor textures (~2.4 MB a set) replaced what used to be an
   instantly applied flat colour, so the test's fixed 8 s wait passed on a warm
   HTTP cache and failed on a cold one (4.5% of floor pixels changed, against a
   40% bar).

   The first attempt waited on the three map responses before sampling. That
   made it worse: a texture served from the browser cache emits no network
   response, so on a warm run the wait hung until the test's own 180 s budget
   expired. A cold-cache flake had been traded for a warm-cache hang.

   It now polls the pixels themselves — the thing the test is actually about —
   with the per-test budget raised to 300 s, because every screenshot in that
   loop costs roughly a frame at SwiftShader's ~1 fps. **The assertion and its
   40% threshold are unchanged**: the test still fails if the floor is not
   drawn. Confirmed with `--repeat-each=2`, covering both the cold and warm
   paths: 2 passed, 99.5% both times, no retries.

---

## 2026-10-02 — Initial build across M0–M8 and M10

Built in one session on the user's instruction to build the whole MVP rather
than one milestone at a time. Everything buildable without a GPU, a real
capture, or API credentials was built and tested; everything else is wired up
and fails loudly.

### Verified by running it

| What | Evidence |
|---|---|
| 212 Python tests pass | `uv run pytest -q` |
| 51 web unit tests pass | `pnpm --filter @walkthrough/web test` |
| 21 Playwright tests pass on desktop Chromium | `playwright test --project=desktop` |
| 19 Playwright tests pass on iPhone 13 / WebKit | `playwright test --project=iphone` (3 needed one retry) |
| Swapping the floor visibly changes the room | Pixel comparison of the floor area reports **100%** changed |
| The analytics DB and raw captures are not downloadable | `test_security.py`, and confirmed live against the running API |
| ruff and tsc clean | `pnpm lint` |
| `pnpm dev` starts both servers | API health 200 on :8000, Vite on :5173 |
| The viewer actually renders the splat | Centre-crop screenshot shows **99.7%** coverage; `docs/screenshots/m2-viewer-desktop.png` |
| Room navigation, floor swap, staged badge, reset, share round-trip | Playwright, plus screenshots in `docs/screenshots/` |
| 32 fps at an iPhone 13 viewport | On-screen counter in `m2-viewer-iphone.png`. **Desktop hardware emulating a phone viewport — not real iPhone silicon.** |
| Synthetic scene generates | 350k splats, 23.8 MB PLY, 2 rooms |
| Catalogs seed | 16 floors, 23 furniture, 7 styles |

### Built but never executed

| What | Why not | What happens if you run it |
|---|---|---|
| `pipeline train` (M1) | No NVIDIA GPU on this machine | Raises with the exact settings it would use |
| `pipeline poses` (M1) | No COLMAP or GLOMAP installed | Raises with an install hint |
| `pipeline frames` (M1) | No ffmpeg installed | Raises with an install hint |
| `pipeline floor` (M3) | No segmentation model chosen — the brief requires shortlisting and confirming first | Raises, and explains that the geometry half is implemented and tested |
| SPZ export | No `spz` CLI | Keeps the PLY and says so |
| Assistant pass rate (M5) | No `ANTHROPIC_API_KEY` | 32 eval cases written; `--dry-run` validates the harness |
| S3/R2 storage (M10) | No credentials | Raises rather than pretending |

### Hardware finding

**This machine has no NVIDIA GPU.** `Get-CimInstance Win32_VideoController`
reports only `AMD Radeon(TM) Graphics` (integrated); `nvidia-smi` is not
installed. WSL2 exists but has only the `docker-desktop` distro — no Ubuntu.

The kickoff interview recorded an RTX 2080 Super as the training GPU. Either
this is a different machine, or the card is not visible to Windows. Nothing
in M1 can be validated until that is resolved. Setup steps are in
`docs/PIPELINE.md`; `nvidia-smi` working inside WSL2 is the gate.

### Milestone status

| ID | Status | Notes |
|---|---|---|
| M0 | **Done** | `pnpm dev` runs both servers, the viewer shows a test splat, CI workflow written. CI itself is unverified — no GitHub remote exists (not created without asking). |
| M1 | **Code complete, unrun** | Every stage written and the pure-geometry parts unit-tested on fixtures. No stage has touched a real video. |
| M2 | **Mostly done** | Waypoints, FOV-only zoom, eased transitions, room strip, mini floor plan, loading and unsupported-device states all work. Editor edits the manifest but cannot yet draw floor polygons or privacy boxes on the splat, and device-orientation look-around is not implemented. |
| M3 | **Half done** | Floor replacement now demonstrably works: the test scene emits `scene_nofloor` and `floor` separately, the viewer drops the floor splats and draws the room polygon as a mesh in metres, and a pixel test confirms **100%** of the floor area changes. Splats correctly occlude the new mesh. Detection still needs the segmentation model, and shading maps are generated but not yet multiplied into the floor material. |
| M4 | **Done, on synthetic input** | Corner proposal, rectification, seamless tiling, normal and roughness maps, EXIF stripped by re-encoding. Tested on generated checkerboards, **not on three real photos** as the brief requires. |
| M5 | **Code complete, unmeasured** | Tools, system prompt, server-side read tools, client-applied mutations, and a guard that rejects invented catalog ids before they reach the viewer. 32 evals written. Pass rate unknown. |
| M6 | **Partial** | Catalog, panels and placement state work. Items render as placeholder boxes at their real catalog dimensions because no GLB is sourced. No environment map, no contact shadows. |
| M7 | **Done** | Deterministic solver honouring the brief's wall, clearance and door-swing rules, falling back to fewer items. Wired to the viewer and the assistant through `POST /api/layout`, with an end-to-end test. |
| M8 | **Partial** | Recolour plumbing exists end to end in state and the assistant tool. Applying a tint to a specific item's splats via Spark is not implemented. |
| M9 | **Not started** | Stretch; the brief says ask first. |
| M10 | **Partial** | Landing page, share links carrying viewer state, analytics events and a per-project summary endpoint all work. No deployment — no R2, no domain. |

### Code review pass

A subagent reviewed the whole diff against the brief. It found 25 issues; the
critical, high and medium ones are fixed, each with a regression test that
fails without the fix.

**Critical**

1. **The replacement floor was never visible.** `ShapeGeometry` is laid out in
   XY with its normal along +Z, and `rotateX(+90°)` sends that normal to
   (0,−1,0) — so the new floor faced downward and was back-face culled from
   every waypoint. The staged badge appeared, so the test passed. Fixed to
   `rotateX(-90°)` with the polygon's z negated to compensate.
2. **Room polygons from the no-RoomPlan fallback were bowties.**
   `cv2.convexHull(returnPoints=False)` returns indices already ordered around
   the hull; sorting them scrambled the winding into a self-intersecting shape
   of zero area. Every downstream consumer — area display, point-in-polygon,
   the layout solver, the floor plan — would have been silently wrong.

**High**

3. Yaw handedness disagreed between the 2D solver and three.js, so rendered
   furniture was mirrored against the footprint the solver had validated.
4. The solver ignored the inward wall normal when choosing yaw, so in any
   clockwise-wound room every bed and sofa faced into the wall.
5. `SplatCloud.transformed()` rotated positions but not the Gaussian
   orientations, which would have streaked every surface after alignment.
6. `pipeline align` assumed the scene was already Y-up and would have written
   an unaligned scene while printing "Aligned". It now refits without the hint
   and fails loudly rather than reporting a false pass.
7. `/files` served the entire data root — the analytics database and the raw
   unmasked captures included. Now restricted to published scene files and
   upload results, with the database moved outside the served root.
8. `SESSION_SECRET` defaulted to the published `change-me`, so anyone could
   mint their own editor cookie. The API now refuses to issue or accept a
   session until it is changed.

**Medium:** recolours did not mark a room as staged and survived a per-room
reset; placeholder furniture ignored its real dimensions; the tiling
quality warning measured the blended output instead of the source photo, so it
could never fire; `Opening.offset` was documented as an edge but read as a
centre; uploads buffered the whole body before enforcing the size cap and did
not validate `project_id`; floor textures leaked on every swap; and six
committed screenshots exceeded the brief's 1 MB limit.

**Also found and fixed while chasing the floor bug:** every wood floor
rendered as the same generic brown, because the fallback colour was per
category rather than per item. The picker looked broken. Each catalog floor
now carries its own `baseColor`.

**Not accepted:** the review flagged `claude-haiku-4-5-20251001` as an invalid
model id. The installed SDK lists both it and `claude-haiku-4-5`, so the
brief's pinned snapshot is valid and was kept — pinning is better for
reproducible evals.

### Bugs found and fixed during the build

Two were found only by looking at the rendered output rather than trusting a
green test, which is worth recording:

1. **The scene rendered blank.** The viewer applied Spark's 180°-about-X
   flip, which is right for raw 3DGS exports (Y-down) but wrong for ours —
   `align` guarantees Y-up. Worse, the first pixel assertion *passed anyway*,
   because it read back a WebGL drawing buffer that is not preserved after
   compositing. Replaced with a centre-crop screenshot requiring 30%
   coverage; it now reports 99.7%.
2. **WebGL contexts leaked.** Unmount disposed the renderer but never called
   `forceContextLoss()`, so every navigation leaked a context against a
   browser cap of ~16. Surfaced as WebKit test failures.
3. **The layout solver skipped every wall-hugging item.** Python
   `point_in_polygon` rejected points exactly on an edge, and a sofa pushed
   flush against a wall has corners exactly on the polygon boundary. The
   TypeScript twin already treated the boundary as inside. Now consistent.

### Open issues

- Playwright needs one retry on this machine. Headless WebGL is SwiftShader
  at roughly 1 fps and degrades across a long run, so a pixel test can time
  out waiting for a frame. Every test passes on a fresh run; a GPU runner
  should not need the retry.
- A room whose floor was *not* replaced is drawn with a flat average colour
  once another room's floor is swapped, because hiding one room's floor
  splats needs per-splat masking. Noted in `docs/ROADMAP.md`.
- No GitHub remote, so CI has never run. The workflow is written but
  unproven. Creating a repo under the user's account was not done without
  asking.
- `scene.spz` is never produced, so downloads are 3–5× larger than they
  should be. Threatens the 60 MB target.
- Metric scale cannot be set: `align --scale-ref` warns and does nothing,
  because marking the two reference points needs an editor tool that is not
  built.
- The assistant's `apply_style` and `auto_layout` apply the style's floor and
  drop its picks at the room centre, rather than calling the real solver.
  The solver exists and is tested; it is not wired to the API.
- iPhone frame rate was measured in an emulated viewport on desktop
  hardware. Real-device performance is unknown and is the number that
  matters.
- Floor textures and furniture meshes are not downloaded, so floors show as
  flat category colours and furniture as boxes.

### What to do next

1. Resolve the GPU situation and get `nvidia-smi` working inside WSL2.
2. Capture one real flat following `docs/CAPTURE.md`, with a RoomPlan scan
   and a measured reference distance.
3. Run `frames` → `poses` and inspect the registered-frame share before
   spending time on training.
4. Add `ANTHROPIC_API_KEY` and run `pnpm evals:assistant` to get a real
   number for M5.
5. Decide on the GitHub remote so CI can actually run.
