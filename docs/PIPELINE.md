# Pipeline: video → scene

**Nothing in this document has been executed end to end.** The machine this
was built on has no NVIDIA GPU, no ffmpeg, no COLMAP and no GLOMAP. The code
paths are written and the pure-geometry parts are unit-tested on fixtures;
the GPU stages raise a clear error rather than pretending. Timings below are
estimates, labelled as such. See `docs/PROGRESS.md` for exactly what was and
was not run.

## Requirements

| Thing | Version | Why |
|---|---|---|
| Linux (WSL2 Ubuntu is fine) | 22.04+ | COLMAP and gsplat tooling is Linux-first |
| NVIDIA GPU | 8 GB VRAM minimum, 24 GB ideal | Training |
| CUDA | 12.x, matching the driver | gsplat |
| ffmpeg | any recent | Frame extraction |
| COLMAP | 3.9+ | Feature extraction and matching |
| GLOMAP | 1.0+ (optional) | Faster, usually better global mapping |
| spz | optional | SPZ compression; without it scenes ship as PLY |

### Setting up WSL2 with CUDA

The target machine is a Windows PC with an RTX 2080 Super. WSL2 is the
chosen path (docs/SPEC.md §2).

```powershell
# Windows, once
wsl --install -d Ubuntu
```

Install the **Windows** NVIDIA driver only. Do not install a driver inside
WSL — it ships a stub that the Windows driver fills in. Then, inside Ubuntu:

```bash
nvidia-smi            # must list the 2080 Super. If it does not, stop here.
sudo apt update && sudo apt install -y ffmpeg colmap
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
uv pip install gsplat    # needs a matching CUDA toolkit
```

`nvidia-smi` working inside WSL is the gate. Everything downstream depends
on it.

## Stages

```bash
pnpm pipeline -- ingest <video> --project <id> [--roomplan <path>]
pnpm pipeline -- frames  --project <id> [--fps 3] [--min-frames 150] [--max-frames 600]
pnpm pipeline -- poses   --project <id> [--matcher sequential|exhaustive]
pnpm pipeline -- train   --project <id> [--max-splats 1500000] [--long-edge 1600]
pnpm pipeline -- align   --project <id> [--scale-ref 0.82]
pnpm pipeline -- export  --project <id>
pnpm pipeline -- run-all <video> --project <id>
pnpm pipeline -- info    --project <id>
```

### ingest

Copies the capture into `data/projects/<id>/input/` and creates a starter
manifest. Copies the RoomPlan export if given.

### frames

ffmpeg decodes at `--fps` (default 3), then blurry frames are dropped using
the variance of the Laplacian.

The blur threshold is **relative to the median sharpness of this capture**,
not absolute. A plain white wall scores low even when perfectly sharp, so an
absolute threshold would throw away good frames in minimal rooms. If
rejection would leave fewer than `--min-frames`, the threshold relaxes and
keeps the sharpest frames instead — too few frames is worse than slightly
soft ones.

Above `--max-frames` the set is thinned **evenly across the walk** rather
than by sharpness, so coverage of the whole flat is preserved.

Writes `work/frames_report.json` with the counts and the threshold used.

*Estimated: 1–3 minutes for a 10-minute 4K capture.*

### poses

COLMAP extracts features and matches them; GLOMAP maps if available,
otherwise COLMAP's mapper.

Camera model is **OPENCV_FISHEYE**, which suits the iPhone ultra-wide 0.5×
lens the capture guide mandates. A pinhole model will not fit that
distortion.

`--matcher sequential` suits a continuous walk and is much faster than
exhaustive. Use exhaustive only for a small, difficult set.

Reports the share of registered frames. **Below 70% means something went
wrong during capture** — see failure modes.

*Estimated: 10–40 minutes for 300–600 frames.*

### train

gsplat with an indoor configuration. Holds out a fraction of frames and
reports PSNR on them.

On 8 GB VRAM:
- frames are downsampled to `--long-edge` (default 1600 px)
- Gaussian count is capped at `--max-splats` (default 1.5 M)

Both are the deliberate 8 GB trade-off from docs/SPEC.md §2. They also help
keep the download under the 60 MB target.

**This stage has never been run.** It raises `ToolMissing` with the settings
it would have used.

*Estimated: 30–90 minutes on a 2080 Super at these settings.*

### align

Fits the dominant near-horizontal plane in the lower third of the scene with
RANSAC, using an up-hint so a large wall cannot outvote the floor, then
rotates the scene so that plane is **y = 0 with Y up**.

The plane normal is flipped toward the bulk of the points first, so the
scene never ends up upside down.

Metric scale comes from `--scale-ref` plus two reference points marked in
the editor. Marking those points in the editor is **not built yet**, so
`--scale-ref` currently warns and does nothing.

Reports the mean floor residual in millimetres. Tested on synthetic tilted
floors; untested on a real reconstruction.

### export

Caps the splat count (keeping the most opaque and largest splats, sampling
the rest) and compresses to SPZ.

**`spz` is not installed here**, so export keeps the PLY and says so. The
PLY is always kept regardless, as the brief requires. Warns above 60 MB.

### floor (M3)

**Not runnable.** Needs a semantic segmentation model with a cleared licence,
which the brief requires us to shortlist and confirm first. Not done.

The geometry around it *is* implemented and tested: projecting Gaussian
centres into frames and voting, RANSAC refinement that recovers floor hidden
under furniture, concave hulls for room polygons, and shading maps from
heavily blurred luminance.

## Known failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Under 70% frames registered | Walked too fast, or rotated on the spot | Re-shoot slowly, always translating while turning |
| Registration splits into several models | Rooms not connected | Walk through the doorways, do not teleport |
| Floor plane fit lands on a wall | Sparse floor coverage | Film more of the floor; the up-hint helps but cannot invent data |
| Blurry, smeared result | Shutter too slow | 1/500 s, or walk slower |
| Colour shifts between rooms | White balance not locked | Lock it before filming |
| Geometry where a person stood | Someone moved during the capture | Empty the flat |
| Out-of-memory during training | 8 GB VRAM | Lower `--max-splats` or `--long-edge` |
| Scene upside down or on its side | `align` not run, or it picked the wrong plane | Run `align`; check the reported inlier ratio |
| Scene renders but at the wrong size | No metric scale | Measure a reference distance and set it (needs the editor tool, not built) |
| Download over 60 MB | Too many splats, or no SPZ | Lower `--max-splats`; install `spz` |

## What to do first, when a GPU is available

1. Confirm `nvidia-smi` works inside WSL2.
2. Install ffmpeg, COLMAP, and gsplat.
3. Run `frames` on a real capture and check `frames_report.json` is sensible.
4. Run `poses` and look hard at the registered share before training.
5. Only then `train`, and report the real PSNR, VRAM peak, wall-clock time
   and output size. Replace the estimates in this document with measurements.
