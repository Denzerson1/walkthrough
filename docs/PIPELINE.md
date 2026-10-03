# Pipeline: video (+ RoomPlan scan) → scene

One command turns an iPhone capture of a room, plus an optional RoomPlan
LiDAR scan of it, into a scene the viewer loads: Y up, floor at y = 0, real
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

### ingest

Copies the video and scan into `input/`, then **checks both before anything
slow happens**: the clip against `docs/CAPTURE.md` (HDR, under 4K, under
60 fps, too short) and the scan by parsing it and printing walls, openings,
furniture boxes, floor area and ceiling height.

### frames

ffmpeg decodes at `--fps` (default 3), **scaled to `--long-edge` while
decoding** — a 4K PNG is ~12 MB, so a long walk would otherwise write tens of
gigabytes before blur rejection starts. Blurry frames are then dropped by
variance of the Laplacian, relative to this capture's median sharpness (a
plain wall scores low even when sharp). Too few survivors relaxes the
threshold; too many thins evenly across the walk.

### poses

pycolmap: SIFT features on the GPU, matching, incremental mapping, then
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

### align

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
