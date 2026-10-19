# Pipeline: capture (+ RoomPlan scan) → scene

One command turns an iPhone capture of a room — a **Stray Scanner** recording
(recommended) or a plain video — plus an optional RoomPlan scan of it, into a scene the viewer loads: Y up, floor at y = 0, real
metres, walls and doors in the manifest, floor split out, SPZ-compressed.

```bash
pnpm run pipeline run-all "C:\captures\living.mov" --project flat-01 --roomplan "C:\captures\living.json"
```

Measured results are in [Measured](#measured); what has and has not been run
is in `docs/PROGRESS.md`.

## Where it runs

WSL2 Ubuntu on the Windows PC, using the RTX 2080 Super (8 GB). `pnpm run
pipeline ...` from a Windows terminal calls into WSL, so captures can stay
anywhere on the Windows disk and be passed with Windows paths.

### Setup (once)

```bash
wsl --install -d Ubuntu          # needs WSL 2.4+; `wsl --update` first if older
pnpm run setup:pipeline              # scripts/setup_pipeline_wsl.sh, idempotent
```

The script installs, inside Ubuntu:

| Thing | Version | Why |
|---|---|---|
| ffmpeg | distro | Frame extraction, video checks |
| CUDA compiler | 12.9 | Builds gsplat's fused-ssim kernel. CUDA 12 to match torch's cu124 build |
| gcc | 14 | The newest CUDA 12.9 accepts |
| torch | 2.4.1 + cu124 | The pair gsplat publishes prebuilt wheels for |
| gsplat | 1.5.3 (Apache-2.0) | Training, via its reference `examples/simple_trainer.py` at the same tag |
| pycolmap-cuda12 | 4.2.1 (BSD) | COLMAP with GPU SIFT, as a Python package |
| usd-core | 25.5+ | Reading RoomPlan USDZ exports |

Two venvs, both outside the repo so the Windows `.venv` is untouched:
`~/.venvs/walkthrough` (the project, `uv sync --extra pipeline`) and
`~/.venvs/gsplat` (the trainer, which pins numpy<2 and a pycolmap fork that
would clash with the API). Override their locations with `GSPLAT_PYTHON` and
`GSPLAT_EXAMPLES`.

**Do not install an NVIDIA driver inside WSL.** The Windows driver provides
the GPU; `nvidia-smi` inside Ubuntu must list the 2080 Super.

#### Setup problems already solved

These cost real time and are handled by the script; recorded so nobody
re-solves them.

- **WSL's networking crawled at ~25 KB/s from some hosts** on this PC (USB
  Wi-Fi + the Hyper-V NAT) while Windows did 20 MB/s; other hosts (PyTorch,
  NVIDIA, PyPI) were fine. Ubuntu's apt mirrors were the bad case. Mirrored
  networking did not help and was reverted. What worked: a temporary HTTP
  proxy on the Windows side for apt only —
  `uv run --no-project --with proxy.py proxy --hostname 127.0.0.1 --port 3128`
  in Windows, and `Acquire::http::Proxy "http://127.0.0.1:3128";` in
  `/etc/apt/apt.conf.d/99proxy` inside Ubuntu. Both were removed after setup;
  repeat them only if `apt-get` crawls again.
- **Ubuntu 26.04's `colmap` package is broken** (`libPoseLib.so` missing).
  COLMAP comes from pycolmap instead, which also brings GPU SIFT.
- **`cuda-toolkit-12-x` will not install** on 26.04 (Nsight needs
  `libtinfo5`). Only the compiler and dev libraries are installed.
- **CUDA 12's math headers clash with glibc 2.41+** (`cospi`, `sinpi`,
  `rsqrt` exception specifications). The script adds the `noexcept` glibc
  expects to CUDA's six declarations.
- **WSL 2.3 could not start the current Ubuntu image** (`E_UNEXPECTED`);
  `wsl --update` to 3.0 fixed it.

## Stages

Each stage reads what the last wrote under `data/projects/<id>/` and writes a
`work/<stage>_report.json`; any stage can be rerun alone. `pnpm run pipeline info
--project <id>` shows what exists and every report.

```bash
pnpm run pipeline ingest <video> --project <id> [--roomplan <file>]
pnpm run pipeline frames  --project <id> [--fps 3] [--long-edge 1600]
pnpm run pipeline poses   --project <id> [--matcher auto] [--camera-model OPENCV]
pnpm run pipeline train   --project <id> [--max-splats 1500000] [--steps 30000]
pnpm run pipeline align   --project <id>
pnpm run pipeline export  --project <id> [--no-crop] [--sh-degree 3]
```

### Two kinds of capture

| | Stray Scanner folder (recommended) | Plain video |
|---|---|---|
| Camera positions | ARKit's, recorded by the phone | Estimated by COLMAP from texture |
| Plain walls | Held by LiDAR depth in training | Often fog |
| Scale | Metres, exact | From the RoomPlan scan, else a 10–20% guess |
| Gravity | Exact | From how the phone was held |

The stage names are the same for both; each stage checks which it has.

### ingest

Copies the capture and scan into `input/` (`input/stray/` or `input/video.*`),
then **checks both before anything slow happens**: a video against
`docs/CAPTURE.md` (HDR, under 4K, under 60 fps, too short), a Stray folder by
reading its poses, and the scan by parsing it and printing walls, openings,
furniture boxes, floor area and ceiling height.

### frames

ffmpeg decodes at `--fps` (default 3), **scaled to `--long-edge` while
decoding** — a 4K PNG is ~12 MB, so a long walk would otherwise write tens of
gigabytes before blur rejection starts. Blurry frames are then dropped by
variance of the Laplacian, relative to this capture's median sharpness (a
plain wall scores low even when sharp). Too few survivors relaxes the
threshold; too many thins evenly across the walk.

**Stray:** decodes every few frames without autorotation (the poses refer to
the sensor's orientation) and keeps the sharpest of each run of 4 candidates,
named by frame number (`frame_000420.png`) so each lines up with its pose and
depth map.

### poses

**Stray:** no estimation. Writes the trainer's dataset straight from ARKit:
one PINHOLE camera per frame (autofocus moves the focal length), poses as
recorded, and LiDAR depth back-projected (confidence 2, 0.15–5 m), fused to
one point per 2 cm voxel with up to 6 sightings each. Those points seed the
splat, and their sightings are what the depth loss supervises.

**Video:** pycolmap: SIFT features on the GPU, matching, incremental mapping, then
undistortion to pinhole images for the trainer.

- `--matcher auto` is exhaustive up to 400 frames (a room) and sequential
  above. Exhaustive closes the loop of a 1 m circle that sequential can miss.
- `--camera-model OPENCV` by default. iOS corrects most of the ultra-wide's
  distortion in video, which leaves a mild residual that OPENCV models well.
  Use `OPENCV_FISHEYE` only if frames look visibly fisheye.
- Reports the registered share. **Below 70% means the capture went wrong.**
  If COLMAP splits into several models, only the largest is used, and it says so.

### train

gsplat's reference trainer, MCMC strategy, because it takes a hard cap on the
Gaussian count — the 8 GB VRAM budget and the download both need one — and
leaves fewer floaters than the default densification.

Every 8th frame is held out and the trainer reports **PSNR, SSIM and LPIPS on
frames it never saw**: the honest quality number. World-space normalisation is
off so the result stays in COLMAP's frame, where `align` can read gravity
from the cameras. Output: `work/trained.ply`, never modified afterwards.

**Stray:** adds `--depth-loss`: the rendered depth is pulled toward the LiDAR
depth at every sighting, which is what keeps a white wall a wall.

### align

**Stray:** the scene is already in metres with Y along gravity, so up is
taken as given and scale is fixed at 1; only the floor height, and with a
scan the yaw and position, are found. The scan fit allows ±2% for the two
ARKit sessions to disagree.

**Video:**

1. **Up from the cameras.** People film upright, so the mean of the images'
   up vectors is gravity. A plane fit alone cannot tell a floor from a wall
   or ceiling in a frame with no fixed orientation.
2. **Floor**: RANSAC for the dominant level plane below the cameras, moved to y = 0.
3. **With a RoomPlan scan**: yaw, position and **metric scale** from fitting
   the splat's wall points (the band between furniture and ceiling) onto the
   scan's walls with every door and window cut out. Scale starts from ceiling
   height ÷ scan wall height; a coarse search over yaw and scale, then ICP.
   Cutting out the openings is what tells a rectangular room from itself
   turned 180°. **Refuses to write a scene** if under 25% of wall points land
   within 5 cm, or the median error exceeds 3 cm — a scan of the wrong room
   fails here rather than producing a subtly wrong scene. Warns when a
   symmetric room barely distinguishes its two orientations.
4. **Without a scan**: scale is guessed from a typical phone height (1.3 m),
   **labelled as an estimate, 10–20% uncertain**, and the room is outlined
   from the floor splats with no doors or windows.

Rotations carry the spherical harmonics with them (view-dependent shading
would otherwise point the wrong way), and positions, scales and orientations
are transformed together. Writes `scene/scene.ply` and the manifest: rooms,
walls, openings, waypoint and furniture boxes from the scan.

### export

1. **Crop** to the rooms plus 0.75 m (`--crop-margin`), which drops the view
   through windows and the floaters around it. `--no-crop` keeps everything.
2. **Cap** the count (`--max-splats`).
3. **Split the floor geometrically**: splats within 3.5 cm of y = 0 inside the
   room outline. No segmentation model is needed for a scanned room. Records
   each room's average floor colour.
4. **SPZ v3**, written by our own encoder (`splat.py`, following Niantic's
   MIT reference). Verified against Spark: a scene renders pixel-identical
   from SPZ and PLY. About 10x smaller than PLY.
5. Warns if the viewer's download exceeds 60 MB; `--sh-degree 2` or lower
   shrinks it.

### floor

Segmentation-based detection (M3) is still unbuilt, by design: the brief
asks for a confirmed model shortlist first. `export` covers a scanned room.

## Measured

First real run, 2026-10-03, on the 2080 Super. **Input: Mip-NeRF 360
"room" (311 photos at 1556x1038), turned into a 3 fps video**, because no
iPhone capture exists yet. It has no RoomPlan scan, and it was shot as an
inward orbit around a table — the opposite of this guide's outward circles —
so it exercises every stage but not the scan-registration path or the
capture style the pipeline is tuned for.

| Stage | Result | Time |
|---|---|---|
| frames | 311 decoded, 276 kept, 35 dropped as blurry | < 1 min |
| poses | **276/276 registered (100%)**, 97,953 points, exhaustive matching | 22 min |
| train | 1.5 M splats (cap reached), 30,000 steps | 35 min |
| | **Held-out PSNR 33.86 dB, SSIM 0.955, LPIPS 0.096** (every 8th frame, never trained on) | |
| | Peak VRAM 3.8 GB by `nvidia-smi` (torch reports 2.0 GB allocated) — half the card | |
| align | Floor fit 22% inliers, level; scale **estimated** (no scan) | < 1 min |
| export | 1.43 M splats after crop, SH 3; **viewer download 36 MB** | < 1 min |

In the viewer: looking toward the filmed side of the room it is photoreal —
piano, shelves, books, door, framed pictures (`docs/screenshots/m1-real-room-*.jpg`).
Looking away from the filmed side it is smeared, as any splat is where no
frame saw: `m1-real-room-unfilmed-side.jpg`. That is the case the outward
circles in `docs/CAPTURE.md` exist to cover.

**Not yet measured:** an iPhone capture, the RoomPlan path on a real scan
(tested only on synthetic scans with known answers), real-device frame rate.

## Known failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `ingest` warns HDR | Recorded in HDR/Dolby Vision | Re-record in SDR Rec. 709 |
| Under 70% frames registered | Walked too fast, or rotated on the spot | Re-shoot slowly, always translating while turning |
| COLMAP splits into several models | Rooms not connected | Walk through the doorways, do not teleport |
| `align`: "does not fit the RoomPlan walls" | Scan of a different room, or the room changed between scan and video | Rescan, same visit, nothing moved |
| `align` warns symmetric | Rectangular room, doors/windows placed symmetrically | Check the plan's orientation in the viewer |
| `align`: "Could not find the floor" | Floor barely filmed | Film more of the floor |
| Blurry, smeared result | Shutter too slow | 1/500 s, or walk slower |
| Colour shifts across the room | White balance not locked | Lock it before filming |
| Out of memory in training | 8 GB VRAM | Lower `--max-splats` or `frames --long-edge` |
| Download over 60 MB | Too many splats or SH bands | `export --sh-degree 2`, or lower `--max-splats` |
| Window views missing | Cropped at the walls on purpose | `export --no-crop` |

## Rebuild: the flat 1:1 with baked light

```bash
pnpm run pipeline rebuild --project flat-01 --roomplan "captures\scan.glb" [--preview-only]
```

Instead of showing the capture's pixels, rebuild the flat from its
floor-plan scan (Polycam Floorplan glTF, RoomPlan JSON or USDZ) in Blender
and bake its light with Cycles on the GPU, the way the X-post apartments
were made, but automatic and viewable on a phone.

**From the scan, exact:**

- The outline is the union of the scan's floor zones (Polycam cuts an open
  plan into Kitchen, Bedroom, Other ...), so a passage between zones stays
  open. Walls between zones that are not on the outline become partitions.
- The ceiling is the scan's own ceiling pieces, merged where they share a
  plane: flat parts and roof slopes. Walls follow it, so gable walls come
  out as gables and a knee wall at its real height. Wall ends are mitred.
- Doors from the scan, each with leaf, architrave, threshold and a backing.

**From the photos, in `input/dressing.json`** (what a floor-plan scan
cannot see; placed by hand from the capture photos):

- `roofWindows`: windows in a slope (Floorplan mode only finds windows in
  upright walls), with plumb and level linings as built. The first one's
  `view` photo (cropped by `viewCrop`) gives the sky dome its colours.
- `site`: the real neighbourhood out of the windows (`site.py`). An
  OpenStreetMap export of the buildings around (`osm`, Overpass JSON,
  ODbL: credit "© OpenStreetMap contributors"), the flat's `origin`
  (lat, lon), its own `building` (way id, left out) and `floorHeight`
  (the flat's floor above the street). Every house within 160 m is raised
  from its footprint to its `building:levels` (4.2 m ground floor, 3.7 m
  upper ones, 3 when untagged) with a hipped tile roof for its
  `roof:levels`, Viennese bays drawn on the facades, the street at street
  level. The side of the house facing `windowsFace` is the window wall;
  its true bearing then also aims the sun. The neighbourhood's light is
  baked into its vertex colours, one set per time of day.
- `floors`: each floor re-laid from its photo (`photo_texture.py`). The A4
  sheet in the shot gives scale and perspective (its corners rectify the
  floor plane) and the exposure; the plank or tile lattice is fitted and
  real planks or tile faces are cut out and laid again, seamlessly.
- `lights`: the flat's own ceiling lights that are on (`at` x, y, z; `size`,
  `power` in W, `colour`): a soft area light under a flush lit panel. A space
  lit only by bounce bakes noisy; its own lamp makes its light clean.
- `viewPhotos`: the owner's photos out of two windows (`photo`, the
  `window` it was taken from, `keep`: the part to use, as fractions, to cut
  the window's own frame away). They are the view: see "The view out" below.
- `windowsFace` (compass bearing), `location`, `sun` (date, `time`, and
  `times` for the viewer's daylight slider): the real sun for each moment,
  warmer and weaker as it sinks, the sky dimming and warming into dusk.
- `surfaces`: materials cut from the capture photos (`photo_patch`): a
  `box` of the photo (fractions), the `size` it spans in metres, and a
  `wall` box of white wall in the same light (85% albedo) or a `colour`
  to set its brightness; light falloff is divided out and the patch made
  seamless. Surfaces without one are generated around their colour.
- `items`: a finish per scanned piece, drawn from parts until the piece's
  own model exists: `kitchen-base` (fronts in `columns`, one tagged `sink`
  gets basin and tap), `kitchen-wall` (`rows`, `columns`), `kitchen-tall`,
  `oven`, `hob`, `island` (oak bar top overhanging `overhangEnd` and
  `overhangBack`), `wardrobe` (`doors`), `open-wardrobe` (`drawers`), `bed`,
  `sofa`, `stool`, `tv`; `min`/`max` correct a scanned box, `front` names
  the side a piece faces. `extras`: what the scanner missed (panels,
  shelves, the air conditioner, a pendant, more stools).
- `hero`: the preview's view, also the walkthrough's start.

**Light:** Cycles bakes diffuse light (direct and bounced) into one 2048
atlas at 2048 samples, with light portals in the roof windows, denoised
with Open Image Denoise, stored with a gamma of 4 (steeper than sRGB: the
dim hall, lit only by bounce, had too few of the 256 levels and showed grey
blotches once brightened), white balanced like a camera (the shade's colour
cast mostly taken out, so walls read white and sun stays warm) and
exposed against the owner's photos (sun spots clip, as they do in a
photo). With `sun.times`, once per time of day: `flat_light-HHMM.jpg`
each, listed in the manifest's `assets.lighting`; a camera's exposure
follows each time halfway, so the evening stays darker. Faces nobody sees from inside stay in for the
bake, so the room's solids block light, but get no atlas. Oak, worktop and
fabric carry generated textures (`photo_texture.surface_texture`).

**Viewer:** shows `flat.glb` unlit (colour × baked light), antialiased,
with neutral tone mapping (AgX greyed colours into a render's look),
reflections of the room on gloss, glass, steel and the floor, haze over
the distant houses, a lens's corner falloff, and auto exposure like a
phone camera (it meters the view and follows it most of the way: the
bounce-lit hall opens up, a view into a window stops down). The daylight slider
blends the two nearest baked times (light maps in the shader, the
neighbourhood's vertex light on the CPU, the sky's tint).
Floor swaps change only the texture under the light. The walk stops where a
slope drops below 1.8 m (the manifest's `ceilings`) and keeps out of every
piece standing on the floor (its `obstacles`, written by the bake).

**The view out** (`outside.py`, also on its own: `pnpm run pipeline outside
--project flat-01`, a few seconds, no bake). Generated houses read as a
render however they are textured, so what the windows show is the owner's
own photos out of them, stood in 3D (`view_photo.py`):

- The two photos, taken out of two windows 2.4 m apart, are a stereo
  pair: SIFT features matched between them (RANSAC on the essential
  matrix) triangulate to metres.
- The camera's tilt comes from where the photo's verticals meet; its turn
  from the street front's horizontal lines (it runs parallel to the flat's
  own facade).
- The houses are walls parallel to the street, at the distances where the
  features cluster (here 12 m for the street front, 30-70 m for the blocks
  behind). Each part of the photo goes on the wall its features vote for.
- The sky is cut away along the roofs and chimneys; the mesh is drawn in
  front of the generated houses (distances drawn in towards the camera, so
  from where the photo was taken it is the photo, pixel for pixel).
- Glass dust and drops are painted out.

The sky (`sky.py`): per baked time, sunlight scattered by air, haze and
ozone along each line of sight (single scattering, Nishita), the low sky
paled for what multiple scattering adds, matched row by row to the photo's
sky, and the photo's own sky (its blue, its contrails) faded in where it
has one, shifted to the time of day by the model's change since the photo
was taken. Equirectangular `sky-HHMM.jpg`, u = 0.5 along -z, zenith on top.

Outside the photos the generated houses remain: OpenStreetMap footprints,
in the photos' colours.

`work/rebuild_preview.png` is a Cycles still from the hero view. About 4
minutes per room on the 2080 Super; `--preview-only` about 1 minute.
