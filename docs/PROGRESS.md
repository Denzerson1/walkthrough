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
