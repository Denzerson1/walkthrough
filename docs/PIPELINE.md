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
--project <id>` shows what exists, which stages are current, and every report.

```bash
pnpm run pipeline ingest <capture> --project <id> [--roomplan <file> | --keep-roomplan]
pnpm run pipeline frames  --project <id> [--fps 3] [--long-edge 1600]
pnpm run pipeline poses   --project <id> [--matcher auto] [--camera-model OPENCV]
pnpm run pipeline check   --project <id>          # preflight: before training
pnpm run pipeline train   --project <id> [--steps 30000] [--max-splats 1500000] [--depth auto] [--pose-opt auto]
pnpm run pipeline align   --project <id> [--scale-tolerance 0]
pnpm run pipeline export  --project <id> [--no-crop] [--sh-degree 3]
pnpm run pipeline verify  --project <id>          # against splat-transform
pnpm run pipeline compare --project <id>          # the same views at every stage
```

### Reruns: records, staleness, atomic outputs

Every stage leaves `work/runs/<stage>.json` (and a copy per run under
`work/runs/history/`): the input fingerprints (a SHA-256 of the capture and
the scan), the run id of each stage it built on, its settings, the git
commit and a hash of the pipeline source, tool versions (Python, numpy,
OpenCV, pycolmap, ffmpeg; for `train` also gsplat, torch, CUDA, the GPU and
the gsplat examples' tag) and the trainer's seed (42 + rank, fixed in
gsplat's `simple_trainer.py` at the pinned tag).

- **No silent mixing.** A stage refuses to run when what it builds on was
  rerun since — frames cut from an older video, poses for older frames, a
  splat trained on older poses: `StaleInput`, naming the stage to rerun.
  `info` lists every stage as current or stale.
- **The scan goes with its capture.** `ingest` without `--roomplan` sets an
  existing scan aside (`input/roomplan.set-aside-<run>`); reusing it is
  explicit, `--keep-roomplan`, and recorded. `align` refuses a scan that
  changed since `ingest`.
- **Nothing good is deleted before its replacement exists.** Frames, the
  COLMAP model, the dataset and training are built beside the old output
  and swapped in when complete. A failed run leaves its partial output, its
  record and its log under `work/failed/<stage>-<run>/` and `work/logs/`;
  the last good result and record stay current.
- **Publishing is last.** `align` writes `work/aligned/` (levelled splat,
  draft manifest). `export` builds every published file in a staging folder,
  reads each SPZ back and checks it, and only then moves the files into
  `scene/` and writes the manifest that points at them. Until then the
  viewer shows the last export. Manifests are written atomically.
- **Experiments beside the baseline.** `train --experiment <name>` trains
  into `work/experiments/<name>/` with its own record, leaving `work/train`
  and `trained.ply` alone. `--displaced 0.45:0.55` (Stray) also leaves that
  stretch of the walk out of training, so `compare --experiment` scores
  views away from every training camera, not just between them.

### Two kinds of capture

| | Stray Scanner folder (recommended) | Plain video |
|---|---|---|
| Camera positions | ARKit's, recorded by the phone | Estimated by COLMAP from texture |
| Plain walls | Held by LiDAR depth in training | Often fog |
| Scale | Metres, kept exactly | From the RoomPlan scan, else a 10–20% guess |
| Gravity | ARKit's, kept | From how the phone was held, refined by the floor (≤ 10°) |

The stage names are the same for both; each stage checks which it has.

### ingest

Copies the capture and scan into `input/` (`input/stray/` or `input/video.*`),
fingerprints them, then **checks both before anything slow happens**: a video
against `docs/CAPTURE.md` (HDR, under 4K, under 60 fps, too short), a Stray
folder by reading its poses, and the scan by parsing it and printing walls,
openings, furniture boxes, floor area and ceiling height. A capture already in
the project (`input/stray`) is re-registered in place.

### frames

ffmpeg decodes at `--fps` (default 3), **scaled to `--long-edge` while
decoding** — a 4K PNG is ~12 MB, so a long walk would otherwise write tens of
gigabytes before selection starts. Then, per window of candidates, the
sharpest; then (`frames.select_by_coverage`):

- **duplicates go**: a frame that barely moved from the last kept one
  (Stray: under 2 cm and 2°, from the poses; video: under 2/255 mean change
  between small greyscale copies) adds weight, not views;
- **blur is judged locally**, against the median of its neighbours, not the
  capture's: a plain wall is never sharp and still has to be in;
- **no stretch of the walk goes missing**: after 3 windows in a row dropped
  as blurry, the next is kept anyway (`kept_for_coverage`) — that is where
  the person turned, into a corner or through a door, the views a global
  threshold loses in clumps.

A video still left under `--min-frames` gets its least blurred frames back;
over `--max-frames` it is thinned evenly. **Stray:** decoded without
autorotation (the poses refer to the sensor's orientation), named by frame
number (`frame_000420.png`) so each lines up with its pose and depth map.

### poses

**Stray:** no estimation. Writes the trainer's dataset straight from ARKit:
one PINHOLE camera per frame (autofocus moves the focal length), poses as
recorded, LiDAR depth back-projected (confidence 2, 0.15–5 m). The video's
offset from its poses is measured (depth edges against video edges) and
applied. Fusion (`stray.VoxelFusion`), frame by frame, never holding the
whole capture:

- one point per 2 cm voxel; **a point is seen at most once per image** (a
  valid COLMAP track — the old fusion let several pixels of one frame fill
  a voxel's slots: 18% of room3's sightings were such duplicates);
- each point keeps up to 6 sightings (`--max-track`) **spread along the
  walk**, not the first six frames to see it;
- every frame keeps at least 300 of its own (`--min-frame-obs`), and a
  frame whose LiDAR saw under 50 usable points is **left out and named**:
  gsplat's depth loss averages over a frame's points, and an empty set is
  NaN for the whole run. Frames at tracking jumps are left out and named too.

`poses_report.json` and `depth_observations.json` give sightings per frame.

**Video:** pycolmap: SIFT features on the GPU, matching, incremental mapping,
then undistortion to pinhole images for the trainer.

- `--matcher auto` is exhaustive up to 400 frames (a room) and sequential
  above. Exhaustive closes the loop of a 1 m circle that sequential can miss.
- `--camera-model OPENCV` by default; `OPENCV_FISHEYE` only if frames look
  visibly fisheye.
- Reports more than the registered share, which alone says little: mean
  reprojection error, track length, observations per image, the calibrated
  camera (parameters, field of view), the size of every model COLMAP split
  the walk into, and **which stretches of frames did not register**.

### check (preflight)

`pipeline check` before training, into `work/preflight/`:

- **Stray**: frame numbering gaps; depth and confidence beside every pose;
  timestamps (monotonic, frame rate, hitches); depth map size against the
  video's aspect; depth units (median depth must be an indoor distance in
  millimetres); confidence and usable share; intrinsics (principal point,
  fx/fy, field of view, per-frame spread); pose convention (proper
  rotations; the LiDAR floor level under ARKit's +Y; the camera mostly
  looking across the room, which also catches a frame turned 90° in a box
  room where a wall passes for a floor); tracking jumps; yaw coverage and
  pitch range; **depth against video** at the measured offset over the frame,
  its centre and its corners. Corners agreeing much worse than the centre
  hint at lens distortion the pinhole model ignores (Stray records no
  distortion model) — or at LiDAR thinner at the frame's edges: ARKit
  registers depth to the image, so this cannot tell the two apart, and the
  overlay is there to look at.
- **Video**: exposure jumps (auto-exposure), share of motion-blurred frames,
  stabilisation (not readable from the file: a reminder), and once COLMAP
  has run, its numbers above plus loop consistency (3D points seen both in
  the first and the last tenth of the walk).
- **Pictures**: `contact_sheet.jpg` (every Nth kept frame, with sharpness),
  `trajectory.png` (the path from blue to red with a frustum every few
  frames, over the LiDAR or COLMAP points), `depth_overlay.jpg` (depth over
  video, depth edges in white), `observations.png` (depth sightings per frame).

No number here is a pass/fail gate; the warnings say what to look at.

### train

gsplat's reference trainer, MCMC strategy, because it takes a hard cap on the
Gaussian count — the 8 GB VRAM budget and the download both need one — and
leaves fewer floaters than the default densification. Faint splats are kept:
MCMC leaves most of them faint on purpose, and no opacity cut is applied.

Every 8th frame is held out and the trainer reports **PSNR, SSIM and LPIPS on
frames it never saw** — but those sit between training frames, so the number
mostly measures interpolation; `--displaced` experiments measure new
viewpoints. World-space normalisation is off so the result stays in the
capture's frame. Output: `work/trained.ply`, never modified afterwards, and
the trainer's folder `work/train/` with its log in `work/logs/`.

Settings, validated: `--steps`, `--max-splats`, `--sh-degree`,
`--holdout-every`, `--depth auto|on|off` and `--depth-lambda`,
`--pose-opt auto|on|off`, `--pose-opt-lr`, `--pose-opt-reg`, `--antialiased`,
`--data-factor`. `auto` turns depth supervision and pose refinement on for
a Stray capture (LiDAR depth; ARKit poses good to a centimetre, not a pixel)
and off for COLMAP's.

**Pose refinement is kept**: the corrections the trainer learned are read
from its checkpoint and written as `work/train/refined_poses.json` and a
COLMAP model `work/train/sparse_refined/`, with how far the poses moved.
`compare` renders training views from the refined poses. Alignment uses the
original ARKit poses: the refinement is centimetres and degrees around them,
in the same world frame, and gravity comes from ARKit, not from the cameras.

### align

**Stray:** the scene is in metres with Y along ARKit's gravity. **Gravity is
kept**; the floor gives only its height — the lowest level layer of the
LiDAR points under the cameras that covers at least 1 m² and 15% of the
area under them, with the cameras standing 0.4–2.2 m above it. Candidates
that fail are listed with the reason. The floor's own lean from ARKit's up
is measured and reported (> 2° warns), never applied: the old plane fit
could tilt the scene up to 35° to lie on a bed. With a scan, the fit is
**rigid** (yaw and position only); `--scale-tolerance 0.01` allows a bounded
refinement. A free-scale fit is run alongside as a probe, and if it fits
much better at a scale over 3% away, the scan and the capture disagree on
size and nothing is written.

**Video:**

1. **Up from the cameras.** People film without rolling the phone, so every
   image's right vector is level: up is perpendicular to all of them.
2. **Floor**: RANSAC planes within 10° of that up (not 35°), each checked
   for area under the cameras and for the cameras standing above it; the
   floor's normal refines up.
3. **With a RoomPlan scan**: yaw, position and **metric scale** from fitting
   the splat's wall points onto the scan's walls with every door and window
   cut out, from the best few coarse yaws and from shifted starts (a wall
   hidden behind a wardrobe moves the splat's centre by half a room). The
   scale bounds (0.85–1.15 around ceiling height ÷ scan wall height) hold
   **through every ICP step and the result**: an unbounded ICP once turned a
   0.98–1.02 search into 1.12.
4. **Without a scan**: scale is guessed from a typical phone height (1.3 m),
   **labelled as an estimate, 10–20% uncertain**, and the room is outlined
   from the floor splats with no doors or windows.

With a scan, every fit reports inlier share, median, RMS and 90th-percentile
error, the share of the plan's solid wall length the capture saw, and **per
wall**: sightings, residuals, coverage, and how far it leans from vertical.
Walls leaning in step mean the scene is not level: over 5° refuses, over
1.5° warns. It **refuses to write a scene** under 25% inliers, over 3 cm
median error, or under 20% of the wall length seen.

Rotations carry the spherical harmonics with them; positions, scales and
orientations are transformed together (checked against splat-transform by
`verify`). Writes `work/aligned/scene.ply` and a draft manifest; nothing is
published.

### export

1. **Crop** to the rooms plus 0.75 m (`--crop-margin`); `--no-crop` keeps
   everything. It **warns** when the crop takes over half of the scene's
   opaque coverage (opacity x area): the room's surfaces are see-through and
   lean on a backdrop outside (room3 at 30k steps; cropped, its ceiling came
   out stained). A backdrop beyond 1 km is **pulled in** along its direction,
   its size scaled to keep its angle from the room, because SPZ holds ±2 km.
2. **Cap** the count (`--max-splats`).
3. **Privacy masks** from the editor are applied to the splats themselves,
   before anything is written: `delete` drops every splat reaching into the
   box (its centre within the box grown by its own 3σ, up to 15 cm); `blur`
   averages colour over 8 cm cells and drops the view-dependent colour.
   Every published file, the comparison original included, is cut from the
   masked cloud.
4. **Each room's floor on its own** (`floor-<room>.spz`): splats within
   3.5 cm of y = 0 inside the room (grown 5 cm), and not standing upright —
   a splat with a clear thin axis must have it within 35° of vertical, so
   skirting boards and furniture legs stay. A floor layer lopsided upward
   (15 points more of its splats 1–3 cm above its level than below) is
   reported as a likely rug.
5. **Floor shading maps** (`floor-shading-<room>.png`): how light fell on
   each room's floor, from its splats' linear luminance, blurred 10 cm so the
   old planks do not print through; a replacement floor is multiplied by it.
6. **SPZ v3**, our encoder, **read back** and compared before publishing.
7. Then the files move into `scene/`, the manifest is written, and files
   nothing points at any more (an old single `floor.spz`, an unmasked
   `scene.ply`) are deleted.

In the viewer, replacing one room's floor takes only that room's floor
splats away; the other rooms keep their captured floors. An older export's
single floor file still works the old way (every room at once).

### verify, compare, rerun (diagnostics)

- **`verify`** checks our splat code against PlayCanvas's
  [splat-transform](https://github.com/playcanvas/splat-transform) 3.10.3
  (MIT, run with npx, pinned): every published SPZ decoded by it must match
  our reader exactly; random degree-3 splats rotated about each axis by it
  and by `SplatCloud.transformed` must agree (centres exactly, SH to 1e-7).
  Its rotations are in PlayCanvas's frame, the PLY's turned 180° about Z, so
  a turn about X or Y there is the opposite turn here.
- **`compare`** renders the same held-out views from the trained splat, the
  aligned one and the exported SPZ with gsplat (`pipeline/gsplat_tools/
  render_eval.py`, in the trainer's venv), at the source resolution, and
  scores each against the photo: `work/compare/<label>/grid.jpg` and
  `report.json`. Where the score drops is where the defect starts.
  **`compare-browser`** does the same for the last step: the viewer's own
  canvas (`scripts/browser-compare.mjs`, dev server, Chrome with the GPU) at
  upright cameras where the photos were taken, against gsplat's render of
  the published SPZ from the same camera.
- **`rerun`** saves a local [Rerun](https://rerun.io) recording
  (`work/diagnostics/<id>.rrd`, `uv sync --extra diagnostics`, rerun-sdk
  0.39.0): every few frames with its camera and image, the LiDAR or COLMAP
  points, the trained splat's centres and the scan's walls where `align`
  put them. Open with `rerun <file>`; nothing leaves the machine.
- **SuperSplat** (PlayCanvas, MIT) for looking at and cleaning a splat by
  hand. To keep a private flat entirely on this machine, build it from
  source and serve it on localhost only:
  `git clone https://github.com/playcanvas/supersplat`, `npm ci`,
  `npm run build`, `npx serve dist -l tcp://127.0.0.1:3000`, then open
  `http://127.0.0.1:3000` in desktop Chrome or Edge and drop in
  `work/aligned/scene.ply` or `scene/scene.spz`. Built here from master
  (3.7.0, 2026-11-08); it now **requires WebGPU**, which headless Chrome on
  this PC lacks, so loading our file in it was not verified automatically.

Considered and not integrated: **Open3D** (the registration here is 2D wall
points onto plan segments, where its 3D ICP adds nothing; the room path has
no problem it was shown to solve), **Brush** (an independent trainer worth a
benchmark, not run), **SAM 2** (no floor or object mask has yet failed by
geometry), COLMAP's global mapper (the room captures register fully with the
incremental one; standalone GLOMAP has moved into COLMAP).

### floor

Segmentation-based detection (M3) is still unbuilt, by design: the brief
asks for a confirmed model shortlist first. `export` covers a scanned room.

## Measured

### room3, the first Stray capture, rebaked 2026-11-08

One room (no RoomPlan scan), 9,563 posed frames, 160 s at 60 fps. `check`:
numbering, timestamps (59.98 fps, no hitches), depth units and aspect,
intrinsics and pose convention all clean; LiDAR floor 0.36° from ARKit's up,
cameras 1.44–1.47 m above it; every compass direction faced; depth and video
edges agree at a −1 frame offset, steady along the walk (the samples where
the phone turned enough to tell), corners 58% as well as the centre.

**What the old pipeline had published** (2026-10-17, kept in
`work/baseline/`): the floor fit had lain on a sloped surface, **tilting the
scene 6.9° off ARKit's gravity and putting the floor 0.7 m too high**, and
export's crop (from 0.5 m under that floor) had **cut the real parquet away**:
every view looking down showed white smears. The LiDAR fusion had 510,159
same-image duplicate sightings (18%).

**Short experiments** (7,000 steps, 1.5 M splats, ~10 min each, one setting
changed from the defaults; held-out = every 8th frame, gsplat's own eval;
8 views = `compare` on the same 8 held-out frames):

| Experiment | Held-out PSNR / SSIM / LPIPS | 8 views PSNR | Verdict |
|---|---|---|---|
| defaults (depth on, pose refinement on) | 23.86 / 0.896 / 0.286 | 24.76 | baseline |
| depth supervision off | 23.79 / 0.895 / 0.290 | 24.59 | no measurable gain either way at 7k |
| pose refinement off | 23.96 / 0.897 / 0.298 | 24.23 | refinement moved poses 0.08 cm, 0.05°: nothing to correct |
| pose refinement lr 1e-4 (10x) | 23.12 / 0.880 / 0.275 | 23.92 | moved 0.55 cm (max 1.1 cm); PSNR 0.7 dB down, LPIPS slightly better: mixed, no clear gain |
| appearance embeddings (`--app-opt`) | 21.52 / 0.885 / 0.286 | 20.59 (exported) | worse, and the export loses view-dependent colour |
| **displaced**: 10% of the walk left out | 24.52 on its own held-out | **18.02** (SSIM 0.748) on the left-out stretch | the honest novel-view number |

The defaults stay. What limits the room is coverage (18 dB a stretch away
from the filmed path against 25 dB between filmed frames) and soft, doubled
edges on fast turns (crown moulding twice) that neither sync (steady),
poses (refining them harder gave no clear gain) nor depth supervision explains —
most likely rolling shutter and motion blur in turns, which gsplat 1.5.3's
pinhole trainer does not model. Both are capture limits: `docs/CAPTURE.md`.

**The bake** (defaults, 30,000 steps, 52.7 min, peak 2.3 GB VRAM; records in
`work/runs/`, trainer patch and seed recorded):

| Stage | Result |
|---|---|
| poses | 450 frames (1 left out at a tracking jump), 832,128 LiDAR points, 2.42 M sightings, every frame 478–10,788 (median 5,209) |
| train | held-out **25.66 dB / 0.901 / 0.222** (the old pipeline's: 25.65 / 0.902 / 0.220 — interpolation between frames was never what failed); poses refined 0.27 cm, 0.14° median |
| align | ARKit gravity kept; floor from LiDAR, levelled +2.1 cm to the splats on it; cameras 1.44 m above it; ceiling 3.29 m; 4 furniture obstacles |
| export | 1.5 M splats, **38.3 MB** download, SH 3; every file read back, and splat-transform agrees exactly |

Same 12 held-out views rendered at every stage (`compare`): trained **24.59 dB**,
aligned **24.59** (identical: the transform carries positions, orientations
and SH intact), exported **24.22** uncropped — and **21.68** with the default
crop. The crop is where quality was lost: at 30k steps the walls and ceiling
are partly see-through (median opacity 0.003) and lean on big backdrop
splats outside the room (6% of splats, 98.8% of the opacity x area); cropped,
the ceiling comes out stained (one view 24.7 → 13.7 dB). Export now warns
when its crop takes over half of that coverage, and pulls a backdrop beyond
1 km in along its direction (SPZ holds ±2 km; uncropped, 862 splats wrapped
round and the read-back refused the file). room3 is published with
`--no-crop`. That the room's own surfaces are see-through is a training
result, not fixed here.

Before and after from the same viewer cameras (gsplat renders of each
published SPZ, `var/evidence/before-after/sheet.jpg`): the old export shows a
black void wherever it looks down — the floor cropped away — and the new one
the herringbone parquet, level.

Reproduce:

```bash
pnpm run pipeline ingest data/projects/room3/input/stray --project room3   # in place
pnpm run pipeline frames  --project room3
pnpm run pipeline poses   --project room3
pnpm run pipeline check   --project room3
pnpm run pipeline train   --project room3
pnpm run pipeline align   --project room3
pnpm run pipeline export  --project room3 --no-crop
pnpm run pipeline verify  --project room3
pnpm run pipeline compare --project room3 --label final --views 12
```

**Not measured here**: room3 in the browser after the final bake (the
session's sandbox stopped the dev servers starting; the 7k dry run was
walked, floored and checked in the browser the day before), its load time and
frame times, and any phone. flat-02 has no capture at all.

### Mip-NeRF 360 "room", 2026-10-03

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
- `strips`: LED strips under the kitchen's wall units and lowest shelf
  (`from`, `to` at the underside; `power` in W per metre; `colour`): a long
  narrow area light, out of sight behind the front edge, a warm pool on the
  worktop. On in the evening, off by day: full once the sun is below 10
  degrees, fading in over the 20 above, so the slider fades them.
- `viewPhotos`: the owner's photos out of two windows (`photo`, the
  `window` it was taken from, `keep`: the part to use, as fractions, to cut
  the window's own frame away). They are the view: see "The view out" below.
- `windowsFace` (compass bearing), `location`, `sun` (date, `time`, and
  `times` for the viewer's daylight slider, which spans them): the real sun
  for each moment, warmer and weaker as it sinks, the sky dimming and
  warming into dusk.
- `surfaces`: materials cut from the capture photos (`photo_patch`): a
  `box` of the photo (fractions), the `size` it spans in metres, and a
  `wall` box of white wall in the same light (85% albedo) or a `colour`
  to set its brightness; light falloff is divided out and the patch made
  seamless. Surfaces without one are generated around their colour.
- `items`: a finish per scanned piece, drawn from parts until the piece's
  own model exists: `kitchen-base` (fronts in `columns`, one tagged `sink`
  gets basin and tap), `kitchen-wall` (`rows`, `columns`), `kitchen-tall`,
  `oven`, `hob`, `island` (oak bar top overhanging `overhangEnd` and
  `overhangBack`, one piece with the stone inset: cells of one slab that
  share their edges, since two boards side by side baked a dark seam; the
  stone lies 3.5 cm below the oak, which stands round it as a step),
  `wardrobe` (`doors`, or door widths as `columns`;
  `handles` as fractions across the front, `[]` for none), `open-wardrobe`
  (`drawers`), `coat-niche` (both with coats and jackets on the rail,
  facing out and fanned as in the owner's photos, `CLOTHES`: each body its
  front-view outline inflated, a V the lining shows through with lapels
  along it, a collar band, a placket or zip, pocket flaps, folds growing
  to the hem, sleeves hanging from rounded shoulders, on a hook; hung
  sideways, a rail of tubes read as rolled-up sacks), `bed`, `sofa`,
  `stool` (an upholstered shell
  smoothed by subdivision from a cage of sections, on splayed tapering
  round legs with stretchers, meeting the seat well inside its outline),
  `tv`; `min`/`max`
  correct a scanned box, `front` names the side a piece faces. `extras`:
  what the scanner missed (panels, shelves, the air conditioner, a
  pendant, more stools, the corner wardrobe's second wing). A piece or
  extra with a `model` (a .glb in the project's folder, e.g. the owner's
  generated ones in `input/models/`) is drawn by it instead of its finish,
  stretched to its box, front along glTF +Z; delete the `model` to go back
  to the parts. A generated model (~200k triangles, split along every UV
  seam, 2048 px maps) is welded, decimated to 8k triangles per metre of
  its largest side, unwrapped afresh with its colour baked across from the
  original into a 1024 map (decimating smeared its UVs, and decimating it
  unwelded tore holes in the duvet; texels no ray reached, which left
  black flecks, start magenta and are filled from their neighbours),
  smooth shaded below 40 degrees, and
  stripped of emission, normal and roughness maps the viewer does not
  draw; `options.colour` brings its colour map's median to a colour read
  off a photo (the generated sofa came grey; the real one is cream).
- `doors`: how each door is hung, by the index of its wall: `latch`
  (`start` or `end` of the wall: the handle's side) and `frame` (false: a
  concealed frame, the leaf flush with the wall in a 4 mm dark gap).
- **A flat of rooms** (flat-02) rather than an open plan: walls inside the
  outline are partitions, 10 cm, built both sides with skirting. A scanned
  door more than 20 cm off the outline hangs in the partition it stands in
  (within 30 cm), as a doorway: lined reveals, an architrave on both faces,
  the leaf standing open square to the wall. The manifest's room gets the
  partitions as walls with their doors, so the walk stops at them but for
  the doorways, and the dressing's `rooms` each keep the ones along their
  edges (draw their outlines on the partitions' centre lines).
  - `dropZones`: floor zones scanned with the flat that are not part of it
    (a balcony): their walls, windows and ceiling go, the wall to them
    stays as outline, and the pieces standing in them are left out. The
    pieces keep the numbers the whole scan gives them.
  - `partitions`: inner walls the scan missed, `start` to `end`.
  - `openings`: doors and windows the scan missed, centred `at` a plan
    point, `width`, `height` (a window's `sill`). A door off the outline
    hangs in its partition, `hinge` (`start`/`end`) and `swing` (1/-1: the
    side its leaf opens to); one where the scan has a door already only
    hangs that one.
- `palette`: surfaces of the flat's own (`name: [colour, roughness,
  texture kind or null]`); a piece's `options.surfaces` maps its finish's
  surfaces onto them (`{"gloss": "kitchen-green", "stone": "oak-top"}`).
- `floors` may name a catalog floor (`{"catalog": "oak-plank-natural"}`)
  where no photo can be re-laid, or have one generated:
  `{"photo", "parquet": 0.068, "colour"}` a 3-strip parquet of strips that
  wide, each strip's tone drawn from the photo's (`strip_colours`: the floor
  rectified on the A4 sheet and sampled in 4 cm squares), their median
  moved to `colour` (under a night's lamps the sheet is blown out and cannot
  balance them), oak's fibres, rings and pores along each strip
  (`parquet_texture`, 1200 px/m); `{"stone": colour, "tiles": [0.6, 0.3]}`
  large stone-look tiles, half-offset rows, grey joints (`tile_surface`).
  flat-02's plank fit failed on the slatted wall and on the parquet itself.
- An item's `"drop": true` leaves out what the scan took for furniture
  (a step stool, a basin counted twice). Finishes for flat-02: `curtain`
  (cloth in folds every `options.pitch` m on a rod, in `options.surface`;
  lit through, per vertex, and casting no shadow in the bake, so drawn
  sheers do not darken the room), `wc` (wall-hung: a bowl lofted round at
  the front, square to the wall, its lid up with `open`) and `washer`.
  flat-02's own pieces (`build_flat.PIECES`, each from its photos):
  `hall-wardrobe` (slatted oak end, coat niche with seat, drawers, rail and
  its jackets, an open oak box among cupboards), `shoe-cabinet` (hung
  `raised` m off a mat), `sideboard` (an oak `case`, `columns` of fronts
  set in, one surface each in `fronts`), `dining-table`, `coffee-table`,
  `bath-vanity` (drawers, tall cupboard, open shelves, basin: `vanity_basin`,
  mirror), `washer-column`, `roll-shelf`, `niche-wall` (the WC's pre-wall,
  tiled to `tiles`, its lit `niche`), `box-bed`, `folding-bed`,
  `standing-desk` (with `monitors`), `nightstand`, `sliding-wardrobe`,
  `slat-wall`, `floating-shelf`, `step-stool`, `towels`, `box-cushion`,
  `barrel-chair` (a velvet tub upholstered to the floor), `mirror`
  (`shape` "pebble" with a brass `frame`, or a rectangle) and `picture`.
  Kitchen fronts take a `style`: `frame` (shaker), `glass` (frosted panes
  behind `panes` bars), `handle` (black bars). A mirror is its face only;
  the viewer gives each its own reflection from half a metre in front.
- `pictures`: paintings, prints and the clock cut from the owner's photos
  (`photo_picture`): the four `corners` (fractions, clockwise from top
  left) straightened to `size` in metres at 1600 px/m, white-balanced on a
  `wall` box beside it; `cutout` keeps what is darker than the wall as
  alpha (the clock's metal). A `picture` piece shows one by `image`.
- `glass-door`: a pivoting glass leaf (the shower's), hung at its `hinge`
  end and built standing `open` degrees out. It and every inner door's
  leaf are their own objects on their hinges with a `door` glTF extra:
  the viewer opens and closes them on a tap (`doors.ts`), and a closed
  door shuts its doorway to walking.
- `hero`: the preview's view, also the walkthrough's start.
- `widen`: a strip `by` metres deep let into the scanned plan at z = `at`,
  where the owner knows the flat is roomier than the scan: walls, slopes
  and pieces past it move on, the walls across it grow (`rebuild.widen`).
  The other dressing positions are as after it.

**Light:** Cycles bakes diffuse light (direct and bounced) into one 2048
atlas, baked twice as fine (4096, a quarter of the samples per texel) and
averaged down: the small parts (the sink, the shelves under the air
conditioner) had come out speckled. A 4096 atlas (`--lightmap-size 4096`,
flat-02: a flat of rooms spread one atlas thin, and its light looked
pixelated) is baked as published, as 8192 would not fit an 8 GB card.
Single-texel speckles go first (a 3x3
median: the denoiser's leftovers along the joints showed as dots), then it
is smoothed edge-preserving, widely (σ 6) where the light is dim (nooks
behind the shelves kept blotches), and dithered to 8 bits (the hall lamp's
smooth falloff on a wall showed its steps as rings) (a
neighbour counts less the further its light is from a texel's: a plain
blur spread the dark behind the wardrobe onto the wall as a grey band),
and its empty texels are filled from the islands round them (left black,
filtering pulled black into the islands' edges: dark lines along the wall
tops and door frames). Before the denoiser, texels no light reaches
(where a face runs on past another it touches: the ceiling's underside over
the walls' tops, a wall behind a wardrobe) take the light of the visible
surface beside them: mixed with black, the joints showed dashed dark lines
and the wall beside the wardrobe a stepped band. The bake's margins extend each island's own edge
(by default they took the light of the face beside it in 3D: for a wall's
top, its face hidden in the ceiling slab, a dashed dark joint), and the
JPEG keeps colour at full resolution (4:4:4, written with OpenImageIO;
Blender's halves it, and neighbouring islands' colour showed as specks
along edges). Baked with light portals in the roof windows, denoised
with Open Image Denoise, stored with a gamma of 4 (steeper than sRGB: the
dim hall, lit only by bounce, had too few of the 256 levels and showed grey
blotches once brightened), white balanced like a camera (the shade's colour
cast mostly taken out, so walls read white and sun stays warm; then a
warm cast over it all, `WARMTH`), saved at JPEG quality 95 (Blender's
`Image.save` takes its own quality; the scene's render setting never reached
it, and 90 left blocks on white fronts once exposed) and
exposed against the owner's photos (sun spots clip, as they do in a
photo). With `sun.times`, once per time of day: `flat_light-HHMM.jpg`
each, listed in the manifest's `assets.lighting`; a camera's exposure
follows each time halfway, so the evening stays darker. The sun's direct
light is not in them: it is baked alone (128 samples, the sky off, the
other lamps hidden) and taken away from the full bake, so the light maps
hold the sky's light and every bounce, the sun's included, and the viewer
draws the sun itself. The manifest's `assets.sunPath` gives it every 10
minutes from the first time to the last: the direction towards it, and
its light on a surface facing it in the light maps' units (a diffuse bake
holds a sun of strength E as E cos θ / π, checked against a plane in
Cycles; times the bakes' colour balance and exposure, blended between
them as the maps are). The sky panoramas have no sun disc; the viewer
draws it where the sun stands. Faces nobody sees from inside stay in for the
bake, so the room's solids block light, but get no atlas: each shrinks to
the nearest point of a seen face, so one that shows after all (the slope's
cut beside a roof window's lining) carries the light beside it, not black.
So do the lining boards' 12 mm edges, which on so few texels baked blotchy,
and the boards' backs on the slab's cut, so an edge takes its board's lit
face and not a back's black.
A roof window's sash is one closed ring: four bars meeting end to end hid
faces whose dark the bake spread into its corners. Each object is
unwrapped on its own without aspect correction (with it, a photo
texture's aspect squashed a piece's islands up to 16x and thin parts
baked black), its islands packed tight (`pack_islands`, concave, rotated)
but further apart than its smoothing reaches (8 texels at least): as the
unwrap left them, half of each cell was gaps. Furniture gets 1.5x its
share of the atlas and its light a smoothing (σ 2 texels), as the trim
does: on few texels the kitchen's white fronts came out blotchy. Walls and
ceilings get 0.8x and σ 3.5: their light is smooth now the sun's patches
are drawn live, and the hall, lit only by bounce, showed the denoiser's
blotches as grey bands.

**Light in vertices:** the generated models, the clothes, the pendant and
the sink (`build_sink`: a white composite inset sink as photographed, its
bowl lofted through rounded sections, a ribbed drainboard, a round steel
tap) are lit per vertex, not from the atlas: the models' folds unwrap into ~800
islands whatever the angle, and the bed's triangles got half a texel each
and showed black; at 4x the share, packed tight, a few still did and the
stools' rims came out in patches. Each time of day is baked into a colour
attribute (`_light_HHMM`, as the neighbourhood's), without the sun's
direct light, in the light map's units over its scale; a vertex far darker
than those round it (inside a fold cutting through another) takes theirs,
and each is blended with them a little. The pendant's shade is fabric:
baked as half diffuse, half translucent (COMBINED, with what passes
through), it is lit through from its bright side as the real one is, not
a dark drum lit from the kitchen; its sun stays baked. The sets go out as
glTF custom attributes (`_LIGHT_1730`): as vertex colours the exporter
wrote every set but the last as white, and the neighbourhood's houses had
stood unshaded at all times but one. They are RGB (a colour attribute's
fourth channel was a quarter of the model) and square rooted, so the site
export's 8 bits (`--quantize-generic 8`) keep the dark; the viewer squares
them. Seven sets on 120,000 vertices: the published model is 5.2 MB. Oak, worktop and fabric carry
generated textures (`photo_texture.surface_texture`); the worktop's is a
fine sandy grain over faint clouds (a soft mottling alone read as a blur
up close), in the grey of the owner's photos.

**Viewer:** shows `flat.glb` unlit (colour × baked light), antialiased,
with neutral tone mapping (AgX greyed colours into a render's look),
reflections of the room on gloss, glass, steel and the floor (a 1024 cube
looked up a mip level down, its faces being drawn without antialiasing), haze over
the distant houses, a lens's corner falloff, and auto exposure like a
phone camera (it meters the view and follows it most of the way: the
bounce-lit hall opens up, a view into a window stops down). The daylight slider
blends the two nearest baked times (light maps in the shader, the
neighbourhood's vertex light on the CPU, the sky's tint) and draws the
sun live (`sun.ts`): its direct light from where it stands at that
minute, shadowed by a 2048 depth map of the flat and the houses round it
(an orthographic view along the sunlight framed on the flat, reaching
150 m back, drawn again only when the time changes; twelve hardware
comparisons over a 2.5 cm penumbra, as Cycles' 1.2 degree sun throws from
a roof window to the floor), and its disc on the sky. So its patch walks
across the floor and up the walls as the time is dragged, and any time
can be rested on; before, between two bakes the floor had two
half-strength suns on it, and letting go glided to the nearest bake.
It shows the flat as soon as the time it opens at has its light map and
sky; the other times' (some 750 KB each) arrive after, the nearest one
standing in until then. It renders at the device's full pixel ratio (up
to 3), half a step lower at a time while it cannot keep 45 fps.
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
- Glass dust and drops are painted out, up to 1/140 of the photo across,
  before the sky panorama is made from it too.
- The second photo shows only what the first does not: each is stood at its
  own pair's depths, and where both covered a house it showed twice, one
  copy torn across the other, from anywhere but the window it was taken
  from.
- Near its outline the mesh is four times finer, so the roofs' and the
  photo's edges are not cut into teeth.

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
