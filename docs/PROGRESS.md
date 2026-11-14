# Progress

Running record of what exists, what was verified, and what was not.

The brief's rule 2.6 governs this file: **if something could not be run or
verified, it says so.** A milestone is not "done" because its code exists.

---

## 2026-11-14 — flat-02: mirrors on the live site; where the flat stands; the Unreal plan

- **Mirrors (viewer, tested):** on the published flat the reflecting copy of
  every mirror was drawn metres from its wall: the entry mirror's low across
  the hall–living passage, where it hid the wall ends and the floor (the
  owner's two screenshots). The Pages build quantizes `flat.glb` to 16-bit
  positions and `addMirrors` moved the glass into place on those integers,
  which wrapped. The dev viewer loads the unquantized file, so no check
  there ever showed it. The glass is copied to floats first
  (`mirror.ts` `mirrorGlass`, `mirror.test.ts` on a quantized quad, which
  fails by exactly the 4 m seen). Verified in a dev build served the Pages
  data: the four mirrors' boxes equal their walls', and both screenshots'
  views are whole (`var/evidence/hall/`).
- **Where the flat stands:** the owner gave the address. The building is in
  OpenStreetMap; the plan's outline fits its west end, so the balcony side
  faces 191.5° (was assumed 180°), ±2°. Measured facts and the address are in
  `data/projects/flat-02/input/site.json`, not in git. `dressing.json` is
  unchanged until a bake is wanted.
- **Unreal:** researched and decided in `docs/FLAT02_UNREAL.md`. **Nothing
  is built**: the engine is not installed, and the floor, the date and time,
  a Cesium ion token and a daylight photo to compare with are missing.
- **Not done:** the fix is committed but not deployed; the live site still
  shows the displaced mirrors. The window views in the browser flat are
  still an invented facade. Thin door gaps on the small bedroom's wardrobe
  alias into dotted lines at a grazing angle (seen while checking; in both
  the quantized and the plain model).

## 2026-11-08 — the capture pipeline, audited and fixed; room3 rebaked

The audit's list (defects A–D, reruns, preflight, helpers, experiments,
floors, structure, output, the bake). **flat-02 has no capture** — it is
the Blender rebuild from a Polycam scan and photos — so the corrected
capture pipeline was run on **room3**, the only Stray (ARKit + LiDAR)
capture on this machine, one room.

**Defects**

- **A. run-all**: already calling `ingest(capture=...)` on this branch; now
  tested through Typer with the slow stages stubbed (order and every
  option). The same test caught the same class of bug again during this
  work (`train --displaced` not passed by `run-all`).
- **B. LiDAR fusion**: one sighting per voxel per image (valid COLMAP
  tracks), up to 6 spread along the walk, at least 300 per frame, frames
  without depth left out and named, streamed frame by frame. Synthetic
  reproduction [6, 0, 0] → [10, 10, 10]. On room3's 450 frames: 510,159
  duplicate same-image sightings (18%) → 0; sightings per point from 2.62
  to 2.91 distinct frames, spanning 38.9 frames of the walk on average
  instead of 28.3. room3 never had a frame without depth.
- **C. Metric scale**: bounds held through every ICP step and the result
  (the 1.12-from-0.98–1.02 reproduction stays at ≤ 1.02 and reports a bad
  fit); ARKit captures fitted rigidly, with a free-scale probe that refuses
  a scan of another size; multi-start (one wall hidden no longer sends the
  fit half a room off); RMS/p90, coverage and lean per wall.
- **D. Gravity**: ARKit's kept; the floor gives height only, and floor
  candidates are checked (area, coverage, camera clearance); a video's
  floor may correct up by 10°, not 35°. **room3's old export was tilted
  6.9° off ARKit's gravity with the floor 0.7 m too high, and export's crop
  had cut the real parquet floor away** (`var/evidence/browser-before`).

**Reruns** — run records with input fingerprints, code and tool versions,
settings, seed and upstream run ids; stale inputs refused; scan reuse
explicit; atomic outputs; failed runs keep logs and partial output; align
drafts and export publishes only after reading every file back.

**Preflight, helpers, experiments, floors, output** — see
`docs/PIPELINE.md` (check, verify, compare, compare-browser, rerun;
splat-transform agrees exactly with our SPZ and SH rotation; floors per
room with shading maps; privacy masks applied to every published file;
catalog floors at their published physical size).

**room3 rebaked** (details and the experiment table: `docs/PIPELINE.md`
"Measured"): held-out 25.66 dB, 38.3 MB; trained, aligned and exported
renders of the same 12 views 24.59 / 24.59 / 24.22 dB. Six short experiments
changed one setting each; none beat the defaults (depth and pose refinement
make no measurable difference on this capture, appearance embeddings cost
4 dB); views from a stretch of the walk never trained on score 18.0 dB
against ~25 between frames — coverage is the limit. Found on the way and
fixed: the default crop removed the backdrop see-through walls lean on
(−2.9 dB, stained ceilings; now warned, and room3 exported uncropped), SPZ's
±2 km range (backdrop pulled in), a 2.6 m guessed wall height that cropped
a 3.3 m ceiling, the camera walking into furniture (LiDAR obstacles), floor
textures laid at up to 7x the wrong scale (now ambientCG's published sizes).

**Verified**: 439 Python tests in WSL (pipeline and API), 166 web, eslint,
tsc and ruff green; the desktop e2e suite 22 of 23 (the landing test expects
demo-01 to be the featured flat, which on this machine it is not; CI is
unaffected), including the new per-room floor test; `verify` against
splat-transform on every published file; in the browser on the 7k dry run:
floor replaced and shaded, walk stopped by walls and furniture.
**Not verified**: room3 in the browser after the final bake, its load time
and frame times (the sandbox in the last session refused to start the dev
servers: Windows error 448 on uv's and pnpm's junctions), any phone, Brush,
SuperSplat opening our file (it needs WebGPU; headless Chrome here has none),
multi-room assembly (not built; there is no multi-room capture), and
anything for flat-02, which has no capture.

## 2026-11-08 — flat-02, the owner's review of the live flat

Items 110–116 of `docs/flat-02-punchlist.md`.

- **Light:** one bake, a quarter to seven in the evening, lamps on and the
  sky still lit; no time slider (it shows only with more than one bake).
- **Mirrors (viewer):** every mirror draws into one half-float buffer at the
  screen's size (`lib/mirror.ts`, three's Reflector maths): a buffer each,
  five of them at up to 2048 px and 4x multisampled, ran out of GPU memory
  (black mirrors), and capped smaller they were pixelated. The hall's pebble
  mirrors have wider rims standing off the wall.
- **Kitchen:** two worktops lay in one plane with a corner unit's front
  overhang (the flicker): cut back to it (`worktopTrim`).
- **Pieces:** the study's guest bed towards the west wall; the bathroom's open
  shelves narrower with an oak side, the towels clear of the shower glass, the
  roll shelf open; the small bedroom's bed plain and clear of the wardrobe,
  a bedside table with a lit lamp (`side-table`, a `pendant` shade over a
  `bulb`), the desk closed at its right end, both office chairs a black
  that shows their form (`match_colour(flat=True)`); the shower's glass
  shut by default, so the towels hang clear of it.
- **Verified:** one bake at 320 samples, 4096 px (25.8 min, the owner
  wanted it quick), manifest with the one lighting entry; every item
  screenshotted in the dev viewer from where the owner looks (the hall
  mirror at their screenshot's angle) before deploying; `pnpm test`
  (162 web, 367 py) and `pnpm lint` green. Not verified: a phone's GPU
  (the shared mirror buffer is sized for it, but no phone was tried).

## 2026-11-07 — flat-02, every detail against the photos; the time of day

The owner went through the live flat and listed what was still wrong (items
68–104 of `docs/flat-02-punchlist.md`), then looked again at the renders.

- **Viewer (tested):** a door shut on someone standing in its doorway steps
  them out of the wall (`nearestStandable`, `walking.test.ts`); a step from
  where one may not stand steps out first, so the walk is never stuck. Keys
  are read by position (`KeyboardEvent.code`): a W let go after Shift no
  longer stayed held. A button that kept the focus after a click no longer
  swallows WASD. Q/E turn during a glide. Mirrors: their buffers a quarter
  the size and without multisampling on a phone (the bathroom's and
  study's came out black: out of memory), no tint, clipped at the glass.
  Exposure lower (key 0.15, was 0.33; at most 1.6x, was 2.2x); lamp discs
  warm, not blue-white.
- **Geometry:** a wall piece shorter than its corner's mitre is a wedge (the
  13 cm by the front door was a twisted box, its diagonal a black strip).
  The coats hang side on, inside the niche. The sink's worktop edge is one
  face; the splashback reaches the corner.
- **Pieces, as photographed:** the hall dresser (two drawers over two doors);
  the kitchen re-laid (fridge 72, dishwasher 60, sink 80, drawers, a narrow
  pull-out each side of the oven, a plain corner door then four narrow glass
  doors in dim frosted grey, an 86 cm hob with its extractor, fine even oak,
  a light switch by the fridge); the bathroom's vanity flush, its shelves
  lower and open to the basin, the WC's pre-wall straight to the shaft, tiles
  to the ceiling where photographed; beds with draped duvets and plump
  pillows (`bedding`), the small bedroom's plain white desk in the corner,
  its bed's head on the west wall; tub chairs
  rebuilt as seats; a new clock; no cushions on the sofa; bigger bedside
  chests.
- **Models:** the two office chairs are IKEA's own 3D models (MARKUS,
  JÄRVFJÄLLET), fetched from ikea.com's product viewer at their measured
  size. **IKEA's models are not licensed for reuse; the owner asked for them
  and said the use is not commercial. They ship inside flat.glb on Pages.**
- **Pictures and rugs:** artwork made for the flat in its colours replaces
  every picture cut from the photos (`pictures.image`); rugs plain.
- **Time of day:** upright windows get a sun now (`scene_spec`, tested): the
  dressing names the windows' scene direction and compass bearing. flat-02
  bakes 08:30, 12:30, 16:30, 18:45 and 20:30 (Vienna, equinox; the plan has
  no north arrow, so the balcony side was taken as south) and opens at
  20:30, lamps only, as photographed. The other times bake at half samples.
- **After the full bake** every room was screenshotted against the photos
  again (items 105–109): the small bedroom's wardrobe now two gloss doors
  over three rows of drawers (`wardrobe` `drawers`), MARKUS's black lifted so
  it shows its form, the study's curtains end at the sill above the guest bed
  (they cut into it), its duvet the photographed dark green floral; the
  duvet prints are seamless tiles.
- **Verified:** pytest and vitest pass, lint clean; a quick build and a full
  bake checked in screenshots room by room, then the second full bake with
  items 105–109 checked the same way before deploying.

## 2026-11-06 — flat-02 from its floor plan; doors, mirrors, no pixels

The owner sent the architect's floor plan (TOP 4, WNF 79.92 m²) and asked
for every dimension to it, the scuffed balcony wall fixed, the doors hung
right, nothing pixelated, working mirrors and perfect shadows; then the
bathroom's shelves and basin, the fridge, and the barn door behind the TV.
Items 56–67 of `docs/flat-02-punchlist.md`.

- **Floor plan** (`rebuild.apply_plan`, tested; dressing `plan`): the outline,
  its windows and doors, and the inner walls with their thickness (25, 13,
  32 cm, a 29 cm shaft) replace the scan's. The scan had the living room's
  balcony wall 52 cm too far north (41 cm too deep); the rest was within
  2–11 cm. Outer walls are 43 cm (`wallT`), so reveals are as deep as built.
  Rooms carry the plan's net areas (`areaM2`), which the room strip shows.
  A plan's walls are not "joined" (that skewed two of them by 3.5 cm).
- **Openings:** at the plan's axes and shell widths; sills and heights as
  photographed. Two openings that only touch (balcony door and fixed pane)
  both stay (`add_openings`, tested).
- **Doors** as drawn: small bedroom hinged east (was west), front door at
  its north jamb; the hall–living passage keeps the photos' oak casing and
  gets the barn door (0073, 0074): slats on felt on an oak rail, standing
  open behind the TV, sliding shut on a click (`doors.ts` `slide`, tested).
  It is baked shut and unseen by the rest of the bake, so the wall it opens
  in front of carries no shadow and both its faces are lit.
- **Furniture** moved with its walls (dressing); the kitchen's fridge unit is
  the run's first cabinet; two wall cupboards and the shelves keep clear of
  the south window; the sofa ends short of the east pane.
- **Light map** (`build_flat.layout_lightmap`): all objects' islands packed
  as one atlas instead of a square cell each. The old atlas was 27 % used;
  the packer also squashed islands to the active image's aspect, the
  parquet's 1:9, so the floor's light was smeared along the boards. Now
  88 % used: walls 2.4 → 1.2 cm per texel, floor 3.7 → 1.0, furniture
  ~1.0, trim 0.5. Smoothing stays inside each island (a rasterised island
  map), so islands sit 4 texels apart; the balcony takes a third of a
  piece's share. JPEG at quality 97.
- **Mirrors** (`SplatScene.addMirrors`): planar reflections (three's
  Reflector) re-drawn from the viewer's eye, instead of a cube map taken
  once in front of each mirror. The TV screen reflects 8 %, not 25 %.
- **Bathroom:** the basin 50 × 38 × 11 cm (was 60 × 42 × 14); the open shelves
  four compartments up to 1.86 m. Canvas sides wrap the image's edge.

### Verified
- Full build at 640 samples / 4096; 40 fixed views checked against the
  photos and the plan; the hinges read back from the viewer's door states;
  walked through the passage and onto the balcony; the barn door slid shut.
- pytest 366 passed, 4 skipped; vitest 159; lint clean.

### Not done / known limits
- The plan draws a hinged door at the hall–living passage; the photos show
  an open passage with the barn door, which is what is built.
- Window heights follow the photos, not the plan's shell heights.
- A door's baked light is that of its built pose (hinged doors open, the
  barn door shut).

## 2026-11-06 — flat-02, a third pass: the balcony, warm light, the floor

The owner asked for the balcony, everything checked 1:1 against the photos,
realistic warm light in the right colours and the right floor. Items 49–55
of `docs/flat-02-punchlist.md`.

- **Balcony** (dressing, new `build_flat.py` parts): the scan's loggia with a
  tiled floor, rendered walls and soffit, a frosted glass balustrade on
  anthracite posts, an acacia table with two chairs and a lounge chair; house
  fronts and the street beyond (new `facade` palette kind in `rebuild.py`,
  tested). No photo shows it by day, so its finishes are a common new build's.
- **Balcony door:** a glazed white-framed leaf that opens and closes; open,
  the balcony is a room walked onto. The house's walls facing it are baked
  (the dressing's `outdoors`, so they are no longer flagged unseen and dark).
- **Walking:** what lies on the floor (rugs, mats, the balcony floor) and a
  doorway's swinging leaf are no longer obstacles. Before, the rugs could
  not be walked onto.
- **Light:** every room's ceiling lamp as photographed, warm white, round or
  square; the overall warm cast held back (`WARMTH` 1.08/1.0/0.86) so white
  walls read white beside the lamps, as in 0092, not beige.
- **Floor:** oak grain drawn per strip (cathedral and rift, latewood, pores,
  knots), strips varying in lightness only; colour honey-amber as measured
  from 0092 and 0140.

### Verified
- Full build at 1024 samples / 4096 (57.4 min); 36 fixed views screenshotted
  and checked against the photos, including four on the balcony.
- In the browser: walked from the living room through the balcony door onto
  the balcony and back; the balustrade and the balcony table stop the walk.
- pytest 365 passed, 4 skipped; vitest 158; lint clean.

### Not done / known limits
- The balcony and the view from it are not photographed; both are generic.
- The house fronts across the street are a flat painted backdrop.

## 2026-11-06 — flat-02, a second pass for realism

The owner asked for everything very realistic, the dining plant's pot shorter
and the plant taller, and the paintings over the sofa placed as in the photo.
Items 40–48 of `docs/flat-02-punchlist.md`.

- **New pieces** (`build_flat.py`): a drawn yucca (low red pot, three canes,
  192 arching leaves, lit per vertex), external blinds, felt place mats; the
  basin rebuilt with a bowl in front of a rear deck, the mixer on the deck
  and a drain, its hollow in enough rings for the per-vertex light to shade
  it; beds rounded more (3.5 cm) so duvets and pillows read soft.
- **Textures** (`photo_texture.py`, tested): `linen` is a fine weave with a
  few broad creases (the ridged folds read as sand on the mattresses); new
  `paisley` (the duvet, drawn: the photo crop read as granite) and `rug`
  (the dining rug: the photo crop had the floor and a chair foot in it).
- **Plan** (`rebuild.py`, tested): a dressing opening that overlaps a scanned
  one replaces it. The scan had the balcony door beside the sofa as a window
  on a sill; it is a door to the floor now, its blinds down outside, no
  curtain (0084, 0092, 0093).
- **Dressing:** the sofa's arm ends short of that door's frame; the two
  paintings over it larger and higher, the lake's right edge just short of
  the arm (0092); the small bedroom's pillows blue (0054).
- **Pages:** `pnpm build:pages` exports flat-02 beside flat-01.

### Verified
- Full build at 1024 samples / 4096 (55.8 min), checked in 31 screenshots
  from fixed views against the photos, no regressions in the 26 earlier
  views; all 48 punch-list items ticked.
- The new pieces smoke-tested and rendered in Blender before the build.
- pytest 364 passed, 4 skipped; vitest 158; lint clean.

### Not done / known limits
- Mirrors still reflect a cube probe without parallax correction.
- The paintings' size is judged from one photo; the scan's east wall comes
  out longer than the photo suggests, so their spacing is a compromise.

## 2026-11-05 — flat-02 after the owner's review

The owner walked flat-02 and listed what was off; the list, checked item by
item against the 89 photos, is `docs/flat-02-punchlist.md` (39 items).

- **Viewer** (`doors.ts`, tested in `doors.test.ts`; `SplatScene.tsx`): the
  walk starts at the entry looking down the hall. Doors open and close on a
  tap (open by default, a 0.7 s eased swing about the hinge); a closed door
  is wall to the walk, and the room strip's glide opens doors in its way.
  The shower's glass door starts half open. Each mirror gets its own
  reflection probe in front of it.
- **Pipeline** (`build_flat.py`, `rebuild.py`, `photo_texture.py`, tested in
  `test_photo_texture.py`): door leaves exported with their hinge as origin
  and a `door` extra; a generated 3-strip oak parquet (the photos' strip
  tones, 1200 px/m) and large-format stone tiles; pictures cut from the
  photos by four measured corners, white-balanced on the wall beside them,
  1600 px/m, the clock and the metal flowers cut out of the wall; shaker and
  glazed kitchen fronts; new pieces (the hall wardrobe with its slatted oak
  edge, seat and coat rail, wall-hung shoe cabinet, TV sideboard, pedestal
  dining table, wall-hung WC with its lid up, vanity, washer column, beds,
  desk with monitors, mirrors in brass pebble frames, towels); frosted glass
  lets light through. Colour maps of large models at 2048. Light map 4096,
  1024 samples.
- **Bake fixes:** reflective caustics off (the brass frames' and mirrors'
  bounce onto the wall came out as a ring of bright blotches); walls' and
  ceilings' light smoothed more (sigma 6) for the mottle left on the
  hall's bounce-lit walls.

### Verified
- Four full builds at 1024 samples / 4096 (about 57 min each), each checked
  in 23–26 screenshots from fixed views against the photos; 38 of 39
  punch-list items ticked (the yucca is the one left).
- In the browser: the start at the entry; the bathroom, study and bedroom
  doors close and reopen on a click; the shower door goes half open → closed
  → open.
- vitest 158, pytest all green, lint clean.

### Not done / known limits
- The plant by the dining table is the closest free model (a spiky plant in a
  tall white pot); the real one is a yucca in a red pot.
- Mirrors reflect a cube probe without parallax correction, so a reflection
  near the mirror looks a little too large.
- The header's room name is updated by walking; the screenshot script moves
  the camera directly, so its shots all say "Hall".

## 2026-11-04 — flat-02: a flat of rooms

A second flat, from the owner's Polycam scan (`3.11.2026.glb`) and 89 HEIC
photos taken that night (in `data/projects/flat-02/input`, git-ignored; the
photos converted to JPEG without their metadata, which held no location).
The owner's word: no people, fitting furniture, nothing lying on the floor.

- **Inner walls with doors** (`roomplan.py`, `rebuild.py`, `build_flat.py`,
  tested in `test_inner_walls.py`): flat-01 was an open plan; flat-02 has
  rooms. A scanned door off the outline used to be pulled onto the nearest
  outer wall (the bathroom's door came out as a second front door); now it
  hangs in the partition it stands in, built as a doorway with lined
  reveals, architraves both sides and the leaf standing open. Partitions
  get skirting both sides. The manifest's room gets the partitions as walls
  with their doors, so the walk stops at inner walls but for the doorways,
  and each room the dressing draws keeps the walls along its edges.
- **What the scan missed, from the photos** (dressing): the balcony behind
  the living room's glass door left out (`dropZones`: its walls and ceiling
  go, the pieces on it too; pieces keep their scan numbers); the study's
  wall to the hall (`partitions`); four doors, the balcony door's glass and
  the window behind the sofa (`openings`).
- **Colours of its own** (`palette`, a piece's `options.surfaces`): the
  kitchen's dark green fronts on an oak worktop, the hall's blue built-ins,
  the bathroom's sage. **Furniture**: ABO models (CC BY 4.0, credit
  "Amazon.com") picked by eye against the photos: the grey corner sofa, the
  black faux-leather dining chairs, the oak pedestal table, green velvet
  tub chairs, the oak and black-metal coffee table, an upholstered bed,
  white three-drawer nightstands, desks and office chairs. New finishes:
  `curtain` (drawn as photographed; lit through, casting no shadow in the
  bake), a wall-hung `wc`, a `washer`.
- **Not like flat-01:** the floors are catalog floors (oak plank, grey
  stone in the bathroom): re-laying from the photos failed, the slatted
  oak screen in every shot read as planks. The photos are at night and
  carry no location, so there is no view out (the curtains are drawn),
  no real sun or neighbourhood, and one daylight bake, so no time slider.
- Tested: pipeline pytest 210 (4 skipped; 5 new in `test_inner_walls.py`),
  vitest 152, ruff and eslint clean. Two full bakes (2048 samples, 36 and
  38 min); in the exported site every room was looked at, and walking was
  tried at each door: through the bathroom, bedroom, small bedroom and study
  doors and the passage to the living room, stopped at the walls beside
  them. The first bake showed the window sills through the curtains' folds
  as a row of dots; the curtains now hang 5 cm off the wall. Not checked
  on a phone.

## 2026-11-02 — The sofa as photographed; one floor in the hall

- **The sofa** (`sofa()` in `build_flat.py`, from sofa1-3): it was boxes
  with 6 cm bevels, the arms lower than the back. Now the back and arms are
  one band of bouclé at one height, round in plan where the back turns into
  the arms (15 cm) and at the arms' fronts (11 cm), its top rolled over
  (11.5 cm); a deep seat cushion on a base set back under it; two flat
  lumbar cushions leaning on the back. Shaded smooth where round.
- **One floor in the hall:** a floor chosen for the hall showed two
  shades. The tiled part is a zone 2 mm over the floor, and was taken up
  for a new floor; the floor beneath it had baked in its shadow, darker.
  The new floor is now laid on the zone too, at the same scale (the zone's
  tile read off its coordinates, `tileOf`), its edge faces left out; and
  the rebuild bakes the floor under the zone as lit (the zone casts no
  shadow or bounce), so the two meet without a line.
- Tested: vitest 152 (the zone's tile, zone pieces without edge faces),
  pipeline pytest 205 (4 skipped), ruff and eslint clean. Full rebuild at
  4096 samples (148 min); in the exported site the sofa is round as
  photographed, and oak and marble chosen for the hall each lie in one
  shade end to end.

## 2026-11-02 — A floor for each room; a phone layout that fits

The owner could not change the hall's floor, and the floor changed with
the room they stood in. The rebuilt flat has one floor mesh for the scan's
single room (an open plan cut into rooms afterwards) and the hall's tiles
as a separate zone on top: the floor took whatever was chosen where you
stood, and the tiles took nothing.

- **One floor per room** (`floorSplit.ts`, tested): at load the floor is
  cut along the rooms' outlines into one mesh per room, every triangle
  into convex pieces, each to the room it lies in or the nearest, so the
  floor is covered once with no gap where outlines do not quite meet.
  Texture and light-map coordinates are carried over by interpolation, so
  a floor laid in two rooms runs on without a seam. The hall's tiles are
  cut the same way and are taken up where a room gets a new floor. The
  model's positions are quantized (KHR_mesh_quantization); they are copied
  into floats before being cut, or they clamp.
- **Floor › where:** "Whole flat" or any room by name, whichever room you
  stand in; a room walks you there. The floor marked is the one that room
  has (for the whole flat, the one every room shares). Laying a floor
  leaves a toast with Undo, as every other change does.
- **Floor pictures:** eleven of sixteen floors were a flat colour in the
  menu, several nothing like the floor laid (a "light oak" tile over a dark
  scan). Each now has a 192 px picture of its texture, tinted as the viewer
  tints it, written beside its maps by `pnpm seed:assets`
  (`data/assets/floors/<id>/thumb.jpg`, some 10 KB each; served from
  `/files/` like the furniture's, as the API does not serve `/catalog/`).
  The floor's colour shows under it until it loads.
- **A phone's layout:** one column of cards at the foot, all one width,
  one corner radius and one gap: the rooms as one segmented card (name over
  area), the daylight, then the design menu. Share moved from its own bar
  to an icon beside the flat's name, and the copied link is a toast.
- **Seeing what you choose on a phone:** the open menu covered the foot of
  the view, where the floor and the furniture are. The view's centre now
  moves up into what the menu leaves (a lens offset at the same scale, not
  a resize), and the gaze dips to what is being chosen: 28° down for
  floors, 12° for styles and furniture. Closing the menu brings the gaze
  back, unless you have looked up or down yourself.
- **The menu:** a segmented tab bar with a dot on each tab that holds a
  change; tiles with the chosen one ringed and ticked, every label two
  lines high so the rows line up; "Reset all" with its icon.
- Tested: the cut per room, including a quantized floor (`floorSplit`,
  7 tests), floors in a room or the whole flat with Undo (store); vitest
  151, pytest 149. Playwright, desktop and iPhone: 30 of 32 viewer tests,
  among them a new one for a floor in one room and then the whole flat;
  the landing page's link fails as before (the uncommitted landing work
  links to flat-01). The Reset-all test now clicks Undo without waiting
  for still frames: under software rendering a frame takes some 5.5 s
  here, longer than the toast stays. On flat-01 in Chrome: the hall takes
  a floor of its own, the whole flat one floor without a seam, the gaze
  dips and comes back. Not verified on a real phone (`DEVICE-CHECK.md`).
  Screenshots: `m11-floor-hall`, `m11-floor-whole-flat`, `m11-furniture`,
  `m11-furniture-choice`, and the menu shots retaken.

## 2026-10-31 — A menu nothing goes missing from

The owner changed the sofa and could not find the way back to the rest.
On a phone the "Back" row scrolled away with the sofas; and the tabs
changed from room to room (a hall had no Style or Furniture), so a tab
could vanish just by walking.

- The same tabs everywhere: Style, Floor, Furniture, Kitchen, Wardrobe.
- **Style** is for the whole flat; "None" takes the kit's pieces and the
  floors it laid out again, and keeps other changes.
- **Furniture** lists every room's pieces under the room's name; a piece
  opens its choices inside the tab (the tabs stay), with "‹ All
  furniture" pinned at the top however far the list scrolls, and
  "Original" (or "None") first to empty the place. A piece in another
  room walks you there. A piece tapped in 3D opens the same choices.
- **Floor** says where it goes: the room's name or "Whole flat".
- **Reset all** at the foot of the menu (greyed out while nothing has
  changed), and the one at the top: both offer Undo now.
- Tested: the store's reset, Undo, removing a style and emptying a slot
  (vitest 143); the menu's reset and Undo in Playwright (desktop: 14 of
  15 viewer tests, the landing link failing as before). Screenshots:
  `m11-furniture-desktop`, `m11-furniture-choice-iphone`.

## 2026-10-31 — No lag; the kitchen and the wardrobe; a simpler menu

- **The lag:** every frame the view moved woke the render loop from inside
  itself, starting a second loop beside the first; they doubled until one
  key held down drew 16,483 frames in 2 s and the page froze for seconds
  at a time on any browser. One loop now (`schedule()` in `SplatScene`),
  measured in Chrome at 1280×720, ratio 1.5: frames every 17.8 ms at the
  median and 18.6 ms at the 95th percentile while turning, walking and
  gliding, no long tasks (was 1–5 s each), 4 ms of GPU per frame on an
  RTX 2080 SUPER. A laptop's integrated GPU is several times slower and
  was not measured on one.
- **Quality that reacts:** the frame time is averaged, as a 60 Hz display
  makes single frames jump between 16.7 and 33.3 ms (40 fps never stepped
  down before); over 20 ms for a second takes the pixel ratio straight to
  the one that should fit, and it never steps back up to one found too
  slow (`quality.ts`, tested).
- **Room glides:** a tap on a room walks there through the openings
  between (fewest rooms first, at the place in each that makes the
  shortest way, square through a narrow door) on a centripetal
  Catmull-Rom curve at an even speed, eased in and out (sine: top speed
  1.6× the average, the cubic's was 3×), 1.1–2.6 s by length; the gaze
  levels and turns from view to view, looking ahead on a long walk unless
  that means turning right round (`camera.ts`, `rooms.ts`, tested).
  Sampled in Chrome: at most 0.9° of turn and 5.7 cm per frame, every
  point standable. Openings under 0.6 m (two corners touching) are no
  longer ways in, for glides or labels.
- **Kitchen and wardrobe:** a new catalog kind, `catalog/finishes` (five
  for the kitchen: fronts and worktop; five for the wardrobe: stains and
  lacquers), served and exported like the others. A finish names the
  flat's own materials and gives each a colour in place of its texture or
  a tint over it, under the same baked light; "hold to compare" shows the
  original. Offered only for fittings whose materials the flat has. In the
  share link (`k`), with Undo.
- **Menu:** 440 px on a big screen; tabs Style, Floor, Furniture, Kitchen,
  Wardrobe (a room without furniture places shows no Style or Furniture);
  every tab is a grid of pictures with one word under each, an "As it is"
  or "Original" tile first, "All rooms" a checkbox. The descriptions, the
  sizes and the "This room" list are gone. Restoring a room's floor no
  longer clears the rest of the room.
- **Two more fixes the browser tests found:** a splat scene stayed awake
  only 2 s after a change, so a floor swap could stay unseen until Spark's
  sort, finishing later on a slow machine, was drawn; the view now stays
  awake while Spark sorts. And every visitor's console showed a 401 from
  the viewer asking whether they are an editor: `GET /api/editor/me`
  answers 200 either way (tested).
- **Evidence:** vitest 139, pytest 149, eslint, tsc and ruff green.
  Playwright desktop: 19 of 20; the one failing is the landing page link,
  whose uncommitted rework is not part of this. Memory check (phone
  settings, CPU 4× slower): the same kit after 20 switches has the same
  GPU geometries and textures (104/45), Reset brings them back (92/26),
  and once the downloads in flight have landed (9 s) nothing is drawn
  while idle. Firefox was not measured: Playwright's Firefox for this
  version is not installed; the cause was not browser-specific.
  Screenshots: `docs/screenshots/m11-*` (menu, styles, `finish-kitchen`,
  `finish-wardrobe`).

## 2026-10-31 — Style kits on real furniture

- **Models and scale:** 36 real products from Amazon Berkeley Objects
  (CC BY 4.0) for four kits, chosen from the metadata by product photo
  (`scripts/shortlist_abo.py`) and converted by `pnpm prepare:models`:
  bottom-centre pivot, front +Z, KTX2 textures, meshopt, a phone file
  (≤1024 px textures, ≤50k triangles; all 36 under 0.92 MB) and a desktop
  one. Each is scale-checked against the product's listed size (3%, uniform
  fixes only) and its category's sane range (`catalog/sizes.json`); 10 of
  46 picks failed and were replaced. `docs/scale-sheet.png` shows all 36
  beside a 2.0 m door and a 1.75 m person. The old Poly Haven themes and
  the drawn sofa and bed are gone.
- **Styles and swapping:** rooms have furniture slots (category, place,
  facing, most floor); a kit puts its piece in each, else its next smaller
  piece, else leaves the slot empty, moving a piece out from under the roof
  slope as far as it must (`styles.ts`, tested). flat-01 is cut into living
  room, bedroom and hall (`pipeline rooms`, walls only along each part's own
  edges, so walking is unchanged); the kit's sofa and bed hide the flat's
  own. One store action for the menu, 3D taps and the assistant; pieces
  fade in and out, a toast offers Undo; lamps glow from dusk; models load
  per device (phone/desktop), within a memory budget, and are freed.
- **Shadows:** placed pieces cast the sun's shadow on the baked floor and
  walls (the sun's shadow map is drawn again when a piece arrives, moves or
  goes), the flat's own sofa and bed stop casting theirs once hidden, and
  placed pieces are lit as the hour is (by the light maps' brightness, the
  key from the sun's direction): dim at dusk, not lit like noon.
- **Last fixes:** the duvet's hem kept (no holes; the drawn frame as wide
  as the bed), the TV right of the island, the drawn sofa back, the window
  photo on one plane with its sky cleaned and stronger dust removal, dim
  light smoothed wider. Checked in the new bake; the anteroom's dark line
  could not be reproduced.
- **Design menu:** a bottom sheet on a phone (collapsed: room and style),
  a panel at the right from 1024 px; tabs Style (cards with swatches,
  "Apply to all rooms"), Floor, Furniture & lamps, This room (floor, each
  piece with its size in cm, the flat's own pieces as "Original", other
  rooms with their style and floor). A row or a tap on a piece in 3D lists
  what fits there first. Esc closes it. The models' CC BY credit is in it.
  Playwright desktop: 13 of 14 viewer tests pass; the one failing is the
  landing page link, whose uncommitted rework is not part of this.
- **Room labels:** a pill (icon and name) over the way into each
  neighbouring room, where you would walk through it; the room you are in
  has none, and a tap takes you there. Arriving shows "Bedroom · 13 m²"
  briefly. Rooms carry `areaM2` (`pipeline rooms`). The room strip and the
  plan already marked the current room. ←/→ still walk, as the owner asked.
- **Entry view and title:** the manifest has `listing` (level, bedrooms,
  bathrooms, area) and `heroView` (pose and its still). A signed-in editor
  gets "Set current view as entry view" in the viewer, which saves the
  pose and a WebP still (≤200 KB; flat-01's is 73 KB) through
  `PUT /api/projects/<id>/entry`. A visitor sees the still at once, with
  `formatListingTitle` in English or German (tested, singular and plural)
  and "Start tour"; the view then fades in at the entry view with a 2.6 s
  move, none with reduced motion, a tap ends it. flat-01's listing gives
  only its area (36 m²) until its level and room counts are known.

## 2026-10-24 — The landing page is a doorway

The landing page featured whichever project sorted first (a test room
called "bedroom") and showed no picture of anything. It now features the
first flat with a `preview.jpg`, the still a finished flat already has for
link cards, and opens on that still: clicking it walks in, with the
viewer's own hint chip on it. The headline is in the display serif the
viewer already uses for names. `/api/projects` reports `preview`, `/files`
serves `projects/<id>/preview.jpg` (nothing else beside `scene/`), and the
static export copies it. Tests for both; lint, vitest and pytest green.
Screenshots in `docs/screenshots/m10-landing-*`.

Not verified: Playwright's WebKit on Windows draws the variable fonts at the
wrong weights (the old screenshot shows it too), so the iPhone shot's heavy
headline is not what Safari shows; untested on a real iPhone. The still is
1200 px wide and looks soft at full desktop width.

## 2026-10-24 — Under-cabinet lights; the last blotches

- LED strips under the wall units and the lowest shelf: a warm, soft pool
  on the worktop by the sink and the hob, on in the evening (fading in as
  the sun drops below 30 degrees), off by day.
- Dim nooks (behind the shelves) smoothed widely; light maps dithered to 8
  bits (rings on the bright hall wall). The sink 24 cm deep, its cabinet cut
  away under the bowl: solid, its top had shown 4 cm down as the floor.
  Matte, as a composite sink is (it had glinted like chrome).
- The kitchen as photographed (kitchen3): every unit's back on the wall
  panel, the base fronts (oven and hob included) in one line, the small
  wall unit as deep as the others; the oak back continuous behind the
  worktop and up behind the open shelves to the air conditioner, and oak
  end panels at the kitchen's open end. The open shelves' light at three
  times a piece's share (it was pixelated).
- The bed's frame drawn: the generated model's own decimated into a lumpy
  black mass under the duvet. Its dark patches are dropped (welded first,
  as the model comes split at every seam) and a wenge platform stands
  under the duvet as photographed (bed1-3).
- The air conditioner as photographed: flat front and top, the front
  curving into the underside (bevelled by edge weight: its thin outlet had
  clamped the bevel to nothing). The flat exposed a little brighter
  (EXPOSE_TO 0.25).
- Jackets subdivided once, their light smoothed twice: hems and folds had
  zig-zagged, their light was patchy.

## 2026-10-23 — Light without dark edges or speckle

The owner: dark lines along wall tops and door frames, a grey band on the
wall by the wardrobe, speckled light on the sink, the shelves under the air
conditioner and the wardrobe's glass shelf. The atlas's empty texels were
black and filtering pulled them into edges: now filled from the islands.
The wall smoothing blurred across the wardrobe's shadow: now edge-
preserving. The atlas is baked at 4096 and averaged down to 2048. The
shelves under the air conditioner and the unit itself came out black or
flat: the gap kept between islands, 9% of a small piece's square per
island, made the packer shrink all of its islands to a point (every face
one texel); it is capped at 2%, and each piece's UVs are fitted to its
cell. The sink is rebuilt as photographed (kitchen2): a white composite
inset sink, a rounded bowl with its flange, a drain, a ribbed drainboard,
a round steel tap, lit per vertex; it was a square steel tray. The dashed
joints at the ceiling were not the gaps: the ceiling's underside runs on
over the walls' tops, where no light reaches, and the texels along the
joint mixed in its black (so did a wall behind the wardrobe): such texels
now take the light of the visible surface beside them before denoising.
Checked on the light map (black texels 0.5%, the dark interiors), rebuilt
and deployed.

## 2026-10-23 — Jackets that read as jackets

The owner: the clothes did not look like jackets (hung sideways, a row of
rounded tubes). Now three per rail, facing out and fanned as in the
owner's photo: bodies inflated from a front-view outline, a V with lapels
and the lining showing, a collar band, a placket (a zip on the leather
jacket), pocket flaps, folds, sleeves from rounded shoulders. The grid's
middle columns follow the V, so its edges run straight. Checked in a
Blender render of the clothes alone, then rebuilt and deployed.

## 2026-10-23 — Smooth light everywhere, jackets on the rails, a fabric lamp

From the owner's phone: pixelated light above the kitchen and on the
island, black triangles on the bed, patchy stools, blotchy hall walls, a
dull lamp, and jackets wanted in both wardrobes.

- **Light in vertices** for the generated models, the clothes and the
  pendant: no atlas islands, so no black triangles or patches; a vertex far
  darker than its neighbours takes theirs.
- **Sharper atlas:** freed of the models; each object's islands packed tight
  (concave, rotated) but further apart than its smoothing; walls and
  ceilings 0.8x the share with stronger smoothing (σ 3.5), the hall's grey
  bands gone; 4096 samples.
- **The neighbourhood was unshaded:** exported as vertex colours, every
  light set but the last came out white. All sets now go out as custom
  attributes (`_LIGHT_HHMM`), RGB, square rooted, 8 bits when published.
- **Jackets:** five coats in the hall niche, four in the open wardrobe
  (the burgundy leather jacket facing out, as photographed), lofted with
  collar, shoulders, sleeves, folds, a placket or zip, on hangers.
- **Pendant:** baked as fabric, half translucent: an even cream drum, not a
  dark one with a seam.
- **Island:** a fine concrete grain in the photos' grey. **Stools:** their
  fabric brought to the photos' mid grey (the legs left as they are).

Verified: rebuilt in 52.3 min at 4096 samples; the owner's spots
(kitchen wall units, island, both wardrobes, bed, stools, pendant, hall)
re-shot at iPhone resolution on the dev server, no console errors; the
built site ready in 1.5 s on an iPhone profile; 340 Python and 92 web
tests, lint clean. Published model 5.0 MB (was 3.2; the houses' light now
carries real data).

Not verified: on a real phone. The views out of the windows are as before
apart from the houses now being shaded; more photos from other spots are
still what would help most.

## 2026-10-22 — The owner's generated sofa, bed and stools; a sun that moves

- **Models:** the owner's Higgsfield (Forge 3D) sofa, bed and bar stool
  draw `sofa-1`, `bed-1`, `chair-1` and the two extra stools (dressing
  `model`, files in `input/models/`; delete the key to go back to the
  parts). Each came at ~200k triangles with 2048 px maps; now welded (it
  came split along every UV seam and decimating it unwelded tore holes in
  the duvet), decimated to 8k triangles per metre (bed 16.7k, sofa 15.3k,
  stool 8.1k), its colour baked onto a fresh unwrap (decimating smeared its
  UVs; texels no ray reached are filled from their neighbours, they had
  left black flecks), stripped of emission, normal and roughness maps. The
  sofa came grey; its colour map is brought to the cream read off the
  owner's photo. Its folds unwrap into ~800 islands: at the furniture's
  share of the light map the bed's triangles had half a texel each and
  showed black; packed tight at 4x the share they have about six.
  Not for commercial use: the owner's test run.
- **The sun moves:** the bakes no longer hold the sun's direct light (it is
  baked alone and taken away); the viewer draws it live from where the sun
  stands at that minute (`sunPath`, every 10 minutes), shadowed by a depth
  map of the flat and the houses round it, and puts its disc on the sky.
  The patch walks across the floor and up the lining as the time is
  dragged; the slider rests anywhere, 08:00 to 20:45 (two more bakes, 19:00
  and 20:45). Before, between two bakes the light faded from one patch to
  another, and letting go glided to a bake.
- The flat shows once the opening time's light has arrived; the other six
  times' (some 770 KB each) load after.

Verified: rebuilt in 41.9 min; the live sun set beside Cycles' own render
from the same camera at 17:30 (the dressing's hero view): the patch on the
floor lands in the same place with the same shape. The units were checked
first: a Cycles diffuse bake of a sun of strength 1 is 1/π at normal
incidence, cos θ/π at θ. Close-ups of the bed and sofa on the dev server
(light map alone, colour map alone, both). The day from 08:00 to 20:30 at
iPhone resolution. The built Pages site on an iPhone profile: ready in
1.5 s locally, no console errors. 340 Python and 92 web tests, lint clean.

Not verified: on a real phone (the live sun is a shadow map and twelve
lookups per lit pixel; checked in desktop Chrome only). A few flecks a few
pixels across are left on the duvet up close, in its light map. The views
out of the windows are unchanged: from where the owner's photos were taken
they are the photos, from elsewhere the generated houses show; more photos
of each window from other spots would let them stand in 3D better.

## 2026-10-21 — From the owner's phone: sharper, warmer, the island and stools refined

The owner walked the live site on an iPhone (Firefox) and sent screenshots.

- **Pixelated** close up: roof windows reflected in the gloss fronts in
  steps (a 512 cube drawn without antialiasing, magnified 6x), the view
  drawn at 2x on a 3x screen, and blotches in the fronts' light. Now a 1024
  cube read a mip level down, the device's full pixel ratio (stepping down
  half a ratio at a time below 45 fps), light maps filtered anisotropically,
  saved at the quality 95 always intended (`Image.save` ignores the scene's
  setting; it was 90), and furniture given more atlas and smoother light.
- **Night mode** in the owner's browser painted the panels dark brown:
  `color-scheme: light only` and `darkreader-lock`. Chrome's forced dark
  mode leaves them light; Firefox on iOS is not verified here.
- **Island:** the stone lies 3.5 cm below the oak, a step all round
  (kitchentray1-3). **Stools:** the legs meet the seat well inside its
  outline; at the back their tops stood out behind it (chair1-3).
- **Warmer light** (`WARMTH` 1.08, 1.0, 0.86).
- **Daylight control:** a sky-coloured track with the baked times as stops;
  let go, it glides to the nearest bake, since between two the floor had
  two half-strength suns.
- **The view out** looked scuffed: both photos showed the house across, one
  copy torn across the other, from anywhere but the window a photo was taken
  from. The second photo now shows only what the first does not, what stands
  within 1.6x of the street front lies on it (its roofs, a side wall),
  the outline is meshed four times finer, and dust is painted out before the
  sky is made from the photo.
- **Black edges at the roof windows:** the slope's cut beside a lining was
  marked unseen, and unseen faces sampled the atlas's empty corner: black.
  They now carry the light of the nearest point of a seen face. The sash,
  four bars meeting end to end, baked dark notches into its corners; it is
  one ring. The lining boards' 12 mm edges, on too few texels beside the
  slab, baked in dark blotches; they are unseen too and take their board's
  light (the boards' backs as well, or an edge took a back's black).

Verified on the dev server as an iPhone 14 Pro Max (DPR 3) at the owner's
viewpoints, before and after. The island, stool and view checks are
renders; the black edges are measured in the model (faces round both
windows with near-black light) and checked again after the bake.

## 2026-10-21 — On GitHub Pages, made for phones

The flat is public at https://denzerson1.github.io/walkthrough/ (the owner's
call, to show it around), kept out of search engines.

- **A static site.** `vite build --mode pages` (`IS_STATIC` in `api.ts`)
  reads JSON files where it asked the API, under the repository's path, and
  posts nothing. The assistant and "Furnish automatically" need the server
  and are not offered; the editor route is not there. `scripts/export_site.py`
  writes what the API would have served, only the scene files the manifest
  names (not the owner's photos beside them), a copy of the page at every
  route and a link preview (`data/projects/<id>/preview.jpg`).
- **Deployed without git.** The site is 114 MB, mostly the catalog's meshes
  and floor textures, which only load when picked. `pnpm publish:pages`
  uploads it as the asset of the `site` release and runs
  `.github/workflows/pages.yml`, which deploys that archive.
- **Smaller.** The flat's glTF files are meshopt-compressed in the export
  (6.5 MB to 2.4 MB, the view 1.0 to 0.9 MB); renders of the same views
  from both differ on under 0.04% of pixels. Spark is loaded only for a
  splat scene: the page's script went from 1.15 MB to 0.27 MB gzipped. The
  model, the view and the daylight maps are fetched at once rather than one
  after another, and the loading bar shows how much of the model is in,
  measured against the length in the .glb's header: Pages gzips it (2.4 to
  1.6 MB on the wire), so Content-Length is not the size that arrives.
- **Phones.** Pinching no longer spins the view (each finger's moves turned
  it by the gap between them). A finger drags the room along with it, as
  photo spheres do; the mouse is unchanged. Buttons are 44 px under a
  finger. On a phone the plan is smaller, the daylight slider sits at the
  bottom by the thumb, both make way for an open sheet, Share opens the
  phone's share sheet, and a one-room flat shows no room strip. Landscape
  sheets are taller with more columns. A long press selects nothing.
- **Battery.** The view draws at full rate while it moves or was touched in
  the last 1.5 s, and at 4 frames a second otherwise. Auto exposure reads its
  meter back without stalling the GPU after the first reading.

Verified: the built site served locally under `/walkthrough/` in Chrome as
an iPhone 13 (portrait and landscape) and a Pixel 7, touch taps and the
panels included, no console errors or failed requests; the dev app with the
API still loads. Live, as an iPhone 13 throttled to 16 Mbit/s: ready in
4.5 s, the bar counting 5% to 95%; routes, the share link's redirect and
the 404 page work, and the owner's photos are not served. Not verified:
Safari on a real iPhone (Playwright's WebKit is not installed here).

## 2026-10-20 — The island's top in one piece, more room, the real stools

- **Island top:** the oak bar, the oak end and the stone inset were three
  boxes; where they touched, the bake spread the hidden faces' darkness
  over the joint and a dark cut showed between the boards. The top is now
  one slab of cells sharing their edges.
- **More room towards the windows** (the owner's call): dressing `widen`
  lets 0.3 m into the plan at z = 2.05, between the stools and the sofa.
  The slope, roof windows, sofa and bed move with it; the island, TV and
  kitchen stay.
- **Stools** as photographed (chair1-3): a soft upholstered shell, seat
  rolling into a curved back that widens to its top, on four splayed
  tapering round legs with steel glides and round stretchers. Plain grey
  suede: the generated linen texture's crumples read as dents on them.

## 2026-10-19 — Layout 1:1 with the photos, a warmer light

Every capture photo was set beside a render from its own viewpoint (24 mm,
71.6° vertical, from the EXIF) and the differences fixed:

- **Bed** turned 90°: its head is at the knee wall under the slope, its
  side along the gable wall (bed1-3). The scan's box ran along the slope.
- **Wardrobe** is an L (garderobe1-3): along the gable wall the open
  section (1.0 m) and one door (0.5 m), along the hall wall a second wing
  whose door has the handle at the inner corner and whose oak side faces
  the room. Measured off the frontal photo garderobe3.
- **Hall doors** are concealed-frame doors, flush with the wall in a thin
  dark gap, no architrave; only the entrance has a white frame. Every
  handle is on the left seen from the hall (dressing `doors`).
- **Hall niche**: rustic oak, one shelf, a rail of coats, a white
  two-drawer unit (wall4). The open wardrobe's clothes are a jacket and a
  coat at the right, as photographed, not a dark block.
- **Light-map unwrap without aspect correction:** photo-textured pieces
  (wardrobe, sofa, stools) had islands squashed up to 16x; a drawer front
  baked black, the sofa striped. Furniture now gets the walls' density.
- **Warmer:** a slight warm cast on the baked light (`WARMTH`).

Not checked against a photo: the bathroom side of the hall doors (not
modelled), and the electrical boxes and intercom on the hall walls (not
built). Sofa, bed and stools are still drawn from parts.

## 2026-10-19 — The hall clean, the ceiling joints sealed

- **Grey spots on the door frames and hall walls:** two causes. The trim
  had ~4 texels across a 7 cm frame at the walls' light-map density: it
  now gets 6x the atlas share and smoothing of its own. And the hall is
  lit only by bounce, the noisiest light to bake: its flush ceiling light
  (in the owner's photos) is on now, soft and low (dressing `lights`), so
  its light is direct and clean; the camera still keeps it darker than
  the main room.
- **White line at the wall-ceiling joint:** the scanned ceiling's outline
  missed the walls by a few millimetres and the slab reached only 2 mm
  past it: a crack the bake saw the sky through. Ceiling slabs now reach
  over the walls' full thickness.
- The bake's exposure is set on the walls and ceilings, not the whole
  atlas (the trim's bigger share had darkened the room).
- Forge 3D sheet for the owner (`forge3d-furniture-sheet-v2.pdf`): ten
  models incl. kitchen and island, all photos per piece cropped, prompts.

## 2026-10-19 — The view is the owner's photos; a real sky; a clean hall

- **View:** the photos out of the two windows, stood in 3D from their own
  stereo (street front 12 m, blocks behind 30-70 m), sky cut away along
  the roofs. From where they were taken they are the photo; walking about,
  near and far move against each other.
- **Sky:** physically scattered sunlight per time of day, matched to the
  photo's sky, which is kept (contrails and all) where it has one.
- **Hall:** clean and darker: light maps stored with a gamma of 4 (the
  grey spots were the hall's light squeezed into a few 8-bit levels), and
  the auto exposure opens up to x2.2 at most.
- Generated houses at the edges in the photos' colours, darker tiles.

Not verified: the stereo distances against a measurement (the street
front came out 12.0 m from one photo, 10.6 m from the other).

---

## 2026-10-19 — Real views, a daylight slider, the photos' look

- **Views:** the houses really outside: 76 buildings from OpenStreetMap
  raised to their floor counts, Viennese facades and tile roofs, the
  flat's own roof below the windows. The windows face Malzgasse (324°),
  as the owner corrected; across it at 16 m a 2+2 house, a 4+2 and a 5+1
  behind.
- **Daylight slider:** 08:00 to 20:00, from five bakes (08, 12, 15, 17:30,
  20). The sun warms and weakens as it sinks; the sky dims into dusk.
- **Less like a render:** materials cut from the owner's photos (the
  wardrobe's and bar's oak, the sofa's bouclé); a camera's exposure and
  white balance set against the photos (it was too bright and cream);
  neutral tone mapping; auto exposure as a phone camera does it (the hall
  is lit only by bounce and went near black at one exposure for the flat);
  reflections on gloss fronts, glass and steel;
  haze; a crumpled duvet and dark base on the bed as photographed.

Verified: baked in 26.8 min (five times of day, 2048 samples each);
screenshots at 08:00, 12:00, 17:30 and 20:00 from the same spots in
Chrome, no console errors; a 60 s walkthrough filmed with
`apps/web/scripts/record-tour.mjs`; site, sun, photo-patch and daylight
blending tests.

Not verified: how the blend between two bakes looks halfway on a phone
(checked on desktop Chrome only).

---

## 2026-10-19 — The flat made true to the photos

The owner's review of the first rebuild, worked through:

- **Entry hall:** clean, no grey specks: light portals in the roof windows
  and 2048 bake samples (the hall is lit only by bounce, the noisiest kind).
  Doors have lever handles.
- **Views:** the photo out of the window has its sky cut out and stands
  12 m out, in front of a sky dome in the photo's own colours with its
  facade colour below the horizon: no blank background from an angle. The
  viewer now antialiases the rebuilt flat, which stopped sashes shimmering.
- **Bar:** oak L-top overhanging the free end and the sofa side, end and
  back panels in oak, two more stools facing it from the sofa side, the
  first stool turned to it.
- **Walking:** the walk keeps a shoulder's width out of every piece on the
  floor (footprints from the bake, `obstacles` in the manifest).
- **Wardrobe:** against the wall (the scan had it 35 cm off), the open
  section full height with side panels both ends, shelves, rail, clothes,
  glass shelf, drawers and basket; the door section with bar handles.
- **Kitchen:** handleless high-gloss fronts with aluminium grip channels,
  worktop with inset sink and tap, black glass hob and oven, two rows of
  wall units, tall unit, oak end panel, open shelves with frames, the air
  conditioner. Oak, concrete-look worktop and fabrics textured.

Verified: rebuilt in 7.7 min; screenshots from eight fixed spots in Chrome,
no console errors; 316 Python and 81 web tests, lint clean.

Not right yet: gloss fronts show no reflections (the viewer is unlit); the
movable pieces are parts until the owner's Higgsfield models arrive; the
bed's head is assumed at the knee wall, as the scan's box runs.

---

## 2026-10-18 — The owner's flat rebuilt 1:1 in Blender

Direction changed after comparing with an X post of an Airbnb modelled in
Blender: instead of training splats on footage, the flat is **rebuilt** from
its Polycam Floorplan scan and the photos, lit with Cycles, and the light is
baked so any phone shows it (`pipeline rebuild`, see PIPELINE.md).

### Verified by running it

- The owner's attic flat (`flat-01`, 34 m², Vienna, roof windows facing
  147°): scan read into an 8-corner outline with the passage open, 3 doors,
  4 floor zones, 7 ceiling pieces merged to a flat ceiling and one slope,
  2 partitions, 13 furniture boxes. Rebuilt and baked in 4.3 min on the
  2080 Super; `flat.glb` 1.3 MB; loads in Chrome with no console errors.
- Floors re-laid from the owner's photos: oak planks measured at 19.5 cm
  (a lattice fit across the rectified shot lands on every seam), the hall's
  tiles at 60 × 40 cm.
- Walking under the slope stops where the ceiling drops to 1.8 m (camera
  held at z = 2.863 against a knee wall at 3.71).
- Tests: 311 Python, 79 web, all lint clean.

### Bugs found and fixed on the way

- Light map saved linear into an 8-bit image: ~15× too dark in the browser.
- Light map normalised to the sunlit window linings: the walls got a few
  dozen levels and JPEG blotches; now normalised to the room, sun clipped.
- Blotchy bake noise at interior exposure: denoised with OIDN.
- "Baked" texels marked by alpha, which a new image has everywhere.
- A gable missed its bend because the scanned ceiling stops 1.3 mm short of
  the wall line; the crossing test now reaches 2 cm.
- Union of floor zones left a spike where corners rounded 1 cm apart.
- Coplanar overlaps at the passage's inside corner (mitred wall ends now),
  glowing slope edges (hidden faces kept for the bake), light under doors.
- The A4 sheet is no white reference: optical brighteners read it blue and
  turned the oak orange; used for scale, perspective and exposure only.

### Not right yet

- Roof window size (78 × 118 cm) and positions are read off photos, not
  measured. Built-ins the scan missed (niche wardrobe, air conditioner,
  pendant) are placed by eye.
- Movable pieces are drawn from parts (blocks) until their own models
  exist; the bed's box runs head-to-foot along the slope, which the photos
  do not obviously confirm.
- The dressing is written by hand; nothing yet writes it from photos.

---

## 2026-10-04 — First real captures, and the move to Stray Scanner

### The owner's bedroom, filmed twice

| Capture | Registered | Held-out PSNR | Result |
|---|---|---|---|
| 1: dim lamp light, mostly close-ups | 87% (after re-running exhaustive) | 14.5 dB | Fog |
| 2: brighter, wider, flicker fixed (1/100 s) | 99% | 21.7 dB | Recognisable room, much smear |

Both trained correctly (no stray processes, correct 108° intrinsics found by
COLMAP); the footage was the limit. On the second, the floor fit was weak, no
ceiling was found and the scan fit refused (16% inliers), so the scene is
tilted and scaled by the camera-height guess (21 m² against a real 12.5 m²).
The two failure modes are the ones the research below targets: COLMAP
guessing poses from texture, and plain walls training as fog.

### Polycam Floorplan glTF reads

`roomplan.load_glb` reads Polycam's Floorplan export: walls, doors, floor
type, eight furniture boxes, 2.53 m ceiling. Tested on the real 128 KB file,
committed as `pipeline/tests/fixtures/polycam-bedroom.glb`.

### Owner decisions

- **Furniture appears only when the user adds it.** The viewer no longer
  furnishes the flat on arrival; real furniture is never replaced.
- **Capture with Stray Scanner** (free, MIT): ARKit poses and LiDAR depth per
  frame, so the pipeline trains on our GPU without COLMAP and with depth
  supervision. Scaniverse (finished splats, closed terms) is the benchmark to
  compare against on the same visit.

### Stray Scanner support

| Piece | What |
|---|---|
| `stray.py` | Reads the dataset (both odometry formats), back-projects LiDAR depth, fuses it to one point per 2 cm voxel with up to 6 sightings, writes the trainer's dataset |
| `colmap.write_model` | COLMAP binary model, one PINHOLE camera per frame (autofocus moves the focal length) |
| `frames` | For Stray: sharpest of every 4 candidates, named by video frame number |
| `poses` | For Stray: ARKit poses as recorded, no estimation |
| `train` | `--depth-loss` for Stray: rendered depth is pulled toward LiDAR |
| `align` | For Stray: gravity and scale taken as given; scan fit allowed ±2% |

Pose convention checked against the app's source (`OdometryEncoder.swift`
turns ARKit's camera 180° about X), and by test: on a synthetic room with
rendered LiDAR, depth points land within 0.5 mm of the walls; with ARKit's
unconverted axes they miss by 446 mm, so the test fails if the convention
regresses. Every depth sighting projects back to its stored pixel.

### Not verified

| What | Why |
|---|---|
| A real Stray Scanner recording through the pipeline | None captured yet |
| Whether Stray beats Scaniverse on the same room | To be compared on the next visit |
| ARKit lens distortion | Ignored: the wide camera's is small, and Stray's per-frame lookup tables are not used yet |

---

## 2026-10-03 — The capture pipeline runs, on the GPU, end to end

The owner asked for everything needed to take real captures: an iPhone video
of one room plus a RoomPlan (LiDAR) scan. Before this, `train` raised
unconditionally, RoomPlan files were copied and never read, scale could not
be set, and SPZ needed a CLI nobody had.

### The GPU was there all along

The 2080 Super is visible to Windows now (`nvidia-smi`: 8 GB, driver 610.88);
the "no NVIDIA GPU" finding of 2026-10-02 no longer holds. WSL2 Ubuntu 26.04
was installed and `scripts/setup_pipeline_wsl.sh` (`pnpm run setup:pipeline`)
builds the toolchain in it. Five environment problems were solved on the way
and are recorded in `docs/PIPELINE.md` so nobody re-solves them: WSL 2.3
could not start the image, WSL's networking crawled for apt, Ubuntu's
`colmap` package is broken, the CUDA metapackage will not install, and CUDA
12's headers clash with glibc 2.43.

### What was built

| Piece | What it does |
|---|---|
| `train` | Runs gsplat 1.5.3's reference trainer (MCMC, capped count, every 8th frame held out) in its own venv, and reports PSNR/SSIM/LPIPS on frames it never saw |
| `poses` | pycolmap with GPU SIFT replaces the broken COLMAP binary; exhaustive matching for a room; undistorts for the trainer |
| `roomplan.py` | Reads RoomPlan JSON (`CapturedRoom`) and USDZ into manifest rooms: outline, walls, doors, windows, furniture boxes, room type |
| `register.py` | Fits the splat's walls onto the scan's: yaw, position and **metric scale**. Openings are cut out of the walls, which is what tells a rectangle from itself turned 180°. Refuses to write a scene that does not fit |
| `align` | Gravity from the cameras' right vectors, floor by RANSAC, then the scan fit (or a labelled scale estimate without one). Never overwrites the trainer's output, so it can be rerun |
| `export` | Crops beyond the walls, splits the floor geometrically (no segmentation model needed for a scanned room), writes SPZ |
| SPZ writer | Our own, in numpy, following Niantic's MIT reference. The demo scene went from 60 MB of PLY to 4.8 MB, and Spark renders it pixel-identical (mean difference 1/255) |
| SH | Higher-order spherical harmonics survive read, write, rotation and SPZ. Rotations rotate them, solved numerically in gsplat's own basis |
| `ingest` | Checks the clip against the capture guide (HDR, resolution, fps, length) and parses the scan before anything slow starts |
| `pnpm run pipeline ...` | Calls into WSL from Windows and accepts Windows paths |

### Verified by running it on real images

No iPhone capture exists yet, so the Mip-NeRF 360 "room" photos (311, CC-BY,
fetched by range request — 150 MB instead of the 12 GB archive) were turned
into a 3 fps video and pushed through every stage on the 2080 Super:

| Stage | Result |
|---|---|
| poses | 276/276 frames registered, 22 min |
| train | **Held-out PSNR 33.86 dB, SSIM 0.955, LPIPS 0.096**, 1.5 M splats, 35 min, 3.8 GB VRAM |
| export | 36 MB viewer download at SH degree 3 |
| viewer | Photoreal toward the filmed side; smeared where nothing was filmed (`docs/screenshots/m1-real-room-*.jpg`) |

### Found by running it, not by the tests

1. **gsplat's MCMC leaves most splats faint** (median opacity 0.035). A fixed
   0.3 opacity cut kept 4% of them and the floor fit landed on a 19° tilt;
   `align` refused to continue, correctly. Solid splats are now the more
   opaque 40%, and a regression test fails under the old cut.
2. **A hull over floor-height splats outlined 740 m² for one room**, because
   strays sit metres outside it. The outline is now the largest connected
   region of a 10 cm density grid.
3. **The viewer opened among floaters**, at the outline's centroid. It now
   starts where the camera actually stood, facing the side that was filmed.
4. My first guess at (1) blamed gravity. Measuring showed the camera-up
   estimate within 2° of the robust one — the wrong diagnosis, caught before
   it shipped. The right-vector estimate was kept anyway, because pitch
   cannot bias it, which a test now proves.

### Not verified

| What | Why |
|---|---|
| An iPhone 16 Pro capture | None exists yet. The test video is photos, not walking footage, and was not shot with the guide's settings |
| RoomPlan on a real scan | No public sample exists. JSON and USDZ readers follow Apple's documented `CapturedRoom` layout, accept both encodings Swift produces, and are tested on synthetic scans; the registration is tested with known transforms. The first real scan is the real test |
| USDZ reading | Written to RoomPlan's prim naming; never run on a real file |
| Camera model choice | `OPENCV` assumes iOS's lens correction is on, as it is by default; if real frames look fisheye, `--camera-model OPENCV_FISHEYE` |
| Viewer frame rate with a 1.4 M-splat SH-3 real scene on a phone | Not measured |

### Also

- The viewer no longer auto-furnishes a capture whose scan found furniture —
  staging over a real sofa would stand a virtual one inside it.
- `SPEC.md` and the landing page caught up with the owner's 2026-10-02
  decisions (badge, licences, free-roam) and the current model list.
- The trainer and pipeline venvs live in WSL (`~/.venvs`), never touching the
  Windows `.venv`. `pyproject.toml` gains a `pipeline` extra; the API and CI
  install exactly what they did before.

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
