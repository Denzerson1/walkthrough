# Progress

Running record of what exists, what was verified, and what was not.

The brief's rule 2.6 governs this file: **if something could not be run or
verified, it says so.** A milestone is not "done" because its code exists.

---

## 2026-10-02 — Initial build across M0–M8 and M10

Built in one session on the user's instruction to build the whole MVP rather
than one milestone at a time. Everything buildable without a GPU, a real
capture, or API credentials was built and tested; everything else is wired up
and fails loudly.

### Verified by running it

| What | Evidence |
|---|---|
| 167 Python tests pass | `uv run pytest -q` |
| 51 web unit tests pass | `pnpm --filter @walkthrough/web test` |
| 19 Playwright tests pass on desktop Chromium | `playwright test --project=desktop` |
| 18 Playwright tests pass on iPhone 13 / WebKit | `playwright test --project=iphone` (3 needed one retry) |
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
| M3 | **Half done** | Viewer-side floor replacement works: polygon mesh on y=0, UVs in metres, correctly occluded by splats. Detection needs the segmentation model. Shading maps implemented and tested but never applied to a real scene. |
| M4 | **Done, on synthetic input** | Corner proposal, rectification, seamless tiling, normal and roughness maps, EXIF stripped by re-encoding. Tested on generated checkerboards, **not on three real photos** as the brief requires. |
| M5 | **Code complete, unmeasured** | Tools, system prompt, server-side read tools, client-applied mutations, and a guard that rejects invented catalog ids before they reach the viewer. 32 evals written. Pass rate unknown. |
| M6 | **Partial** | Catalog, panels and placement state work. Items render as correctly sized placeholder boxes because no GLB is sourced. No environment map, no contact shadows. |
| M7 | **Done** | Deterministic solver honouring the brief's wall, clearance and door-swing rules, falling back to fewer items. 26 unit tests. Not yet wired to the API. |
| M8 | **Partial** | Recolour plumbing exists end to end in state and the assistant tool. Applying a tint to a specific item's splats via Spark is not implemented. |
| M9 | **Not started** | Stretch; the brief says ask first. |
| M10 | **Partial** | Landing page, share links carrying viewer state, analytics events and a per-project summary endpoint all work. No deployment — no R2, no domain. |

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
