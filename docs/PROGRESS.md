# Progress

Running record of what exists, what was verified, and what was not.

The brief's rule 2.6 governs this file: **if something could not be run or
verified, it says so.** A milestone is not "done" because its code exists.

---

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
