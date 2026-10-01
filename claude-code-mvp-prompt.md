# Build brief: 3D property walkthrough MVP

You are the lead engineer of a two-person startup. Build the MVP described below in this repository. Work one milestone at a time, keep the code clean and readable, and ask me before any decision that is hard to reverse. Start by reading the whole brief, then follow "Working rules" (section 7).

---

## 1. Product

A web app that shows real apartments as photorealistic 3D walkthroughs (Gaussian splatting), captured with an iPhone 16 Pro video. Buyers and renters:

- move between **one fixed viewpoint per room**, look around 360° and zoom,
- **change the floor** (presets or their own uploaded tile photo),
- **add 3D furniture** in a chosen style, placed by hand or automatically,
- **recolor existing furniture** in furnished apartments,
- talk to **one AI assistant** that drives all of the above by choosing from our own catalog.

MVP audience: investors and the first estate agents. Content: 2–3 real apartments (one furnished) that we capture and process ourselves. **Out of scope for the MVP:** self-service upload, user accounts, payments, swapping/removing existing furniture.

Use the working name `walkthrough` until I give you the brand name.

---

## 2. Non-negotiable constraints

1. **Licences.** Everything must allow commercial use.
   - Do **not** use the original Inria/GraphDeco `gaussian-splatting` code. Its licence forbids commercial use.
   - Use **gsplat** (Apache-2.0) for training, **COLMAP** or **GLOMAP** for camera poses, and **Spark** (`@sparkjsdev/spark`, MIT, by World Labs) with **three.js** for rendering.
   - Before adding any library, ML model, weights, dataset, texture or 3D asset, check its licence. Prefer MIT, Apache-2.0, BSD or CC0. Record every item in `docs/LICENSES.md` (name, version, licence, link, how we use it). If a licence is unclear or non-commercial, stop and ask me.
2. **No large binaries in git.** Videos, frames, splats, GLB models and textures larger than 1 MB live under `data/` (git-ignored) and, when deployed, in S3-compatible storage (Cloudflare R2). Catalog JSON and small thumbnails may be committed.
3. **Secrets** only in `.env` (git-ignored). Provide `.env.example`.
4. **Honest staging.** Any modified view (floor, furniture, recolor, AI preview) shows a visible **"Virtually staged"** badge, and the user can return to the original with one tap.
5. **Privacy.** The editor can blur or delete regions (personal photos, faces, house numbers) before a project is published.
6. **No faked results.** If something can't run or be verified (for example no GPU), say so clearly and use the fallbacks in section 7.

---

## 3. Tech stack and repo layout

Monorepo: **pnpm workspaces** for TypeScript, **uv** for Python.

```
apps/web/          Vite + React + TypeScript + three.js + Spark, Zustand for state, Tailwind. Mobile-first.
                   Public viewer at /p/:projectId, internal editor at /edit/:projectId (protected by EDITOR_PASSWORD).
services/api/      Python 3.11, FastAPI, SQLModel + SQLite for the MVP, storage adapter (local disk / S3-compatible).
                   Serves project manifests, catalog, uploads, chat endpoint, analytics events.
pipeline/          Python CLI (Typer): video -> scene. Linux + NVIDIA CUDA. Dockerfile with a CUDA base image.
catalog/           floors/, furniture/, styles/ (JSON + small thumbnails).
scripts/           Asset seeding scripts (record licence and source for every asset).
docs/              SPEC.md, CAPTURE.md, PIPELINE.md, PROGRESS.md, ROADMAP.md, DEMO.md, LICENSES.md, screenshots/
data/              git-ignored: projects, uploads, cache.
```

Tests: **pytest** (pipeline, api), **Vitest** (web logic), **Playwright** smoke tests on desktop and an iPhone viewport. Add a CI workflow that runs lint and tests (no GPU jobs in CI).

Create a `CLAUDE.md` at the root with conventions, commands (install, dev, test, pipeline usage) and the constraints from section 2.

---

## 4. Data model

Each project lives in `data/projects/<projectId>/`:

```
input/video.mov              iPhone capture
input/roomplan/              optional Apple RoomPlan export (USDZ or JSON)
work/                        frames, poses, training output (intermediate)
scene/scene.spz              full scene
scene/scene_nofloor.spz      scene without floor splats
scene/floor.spz              floor splats only
scene/floor_shading_<roomId>.png
scene/manifest.json
```

`manifest.json` (extend if needed, keep it documented in `docs/SPEC.md`):

```json
{
  "id": "demo-01",
  "name": "Example apartment",
  "units": "m",
  "upAxis": "y",
  "transform": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
  "floor": { "plane": [0, 1, 0, 0] },
  "rooms": [{
    "id": "living",
    "name": "Living room",
    "type": "living",
    "waypoint": { "position": [0, 1.6, 0], "yaw": 0 },
    "floorPolygon": [[0,0],[4.2,0],[4.2,3.8],[0,3.8]],
    "walls": [{ "start": [0,0], "end": [4.2,0], "height": 2.6 }],
    "openings": [{ "type": "door", "wallIndex": 0, "offset": 1.0, "width": 0.9, "height": 2.0 }]
  }],
  "items": [{ "id": "sofa-1", "label": "sofa", "roomId": "living",
              "box": { "center": [1,0.4,1], "size": [2.1,0.8,0.9], "yaw": 90 }, "source": "roomplan" }],
  "privacyMasks": []
}
```

The scene is aligned so that the floor is the plane y = 0, Y points up, and units are metres. The viewer state (floor per room, placed furniture, recolors) is a separate JSON object that can be encoded in a share URL.

---

## 5. Capture guide (write into docs/CAPTURE.md)

**Camera:** iPhone 16 Pro with the Blackmagic Camera app.

- Lens: ultra wide 0.5× (13 mm, 48 MP), selected explicitly so the phone never switches lenses.
- Recording: 4K, 60 fps, HEVC at high bitrate.
- Shutter: 1/500 s; at least 1/250 s in dark rooms (walk slower).
- ISO: as low as the shutter allows, then locked.
- White balance: manual, matched to the lights (about 4000–5000 K), then locked.
- Focus: autofocus off, set once at about 1.5 m.
- Stabilization: Off.
- Colour: Rec. 709 SDR (no HDR, no Apple Log).

**Filming:**

- Switch all lights on and leave them unchanged. Doors fully open or fully closed. No people or pets moving.
- Walk slowly and never rotate on the spot.
- At every room's viewpoint, walk a circle of about 1 m facing outward at three heights: eye, chest and knee.
- Walk through the doorways so all rooms connect into one model. Keep at least 60% overlap between frames.
- Measure one reference distance (for example a door width) for scale.
- Optionally make a RoomPlan scan with an app built on Apple RoomPlan. It provides walls, doors, windows and furniture boxes.

---

## 6. Milestones

Build in this order. Each milestone ends with: tests passing, Playwright screenshots in `docs/screenshots/` where relevant, a short entry in `docs/PROGRESS.md`, and a commit.

### M0: Foundations

- Repo scaffold, `CLAUDE.md`, `docs/SPEC.md` (this brief, kept up to date), CI, `.env.example`, `docs/LICENSES.md`.
- Development data: find a small public indoor dataset or splat with a licence that allows our use, or create a synthetic test scene. Ask me before downloading anything large.

**Done when:** `pnpm dev` starts web and api, the web app shows a test splat, and CI is green.

### M1: Pipeline (video → scene)

Commands:
- `pipeline ingest <video> --project <id>`
- `pipeline frames`: ffmpeg extraction, default 3 fps (configurable). Drop blurry frames using variance of the Laplacian. Target 150–600 frames per apartment, configurable.
- `pipeline poses`: GLOMAP if available, otherwise COLMAP, with a camera model suitable for the ultra-wide lens. Report the share of registered frames.
- `pipeline train`: gsplat with an indoor config. Hold out some frames and report PSNR.
- `pipeline align`: gravity alignment from the dominant floor plane, plus metric scale from a reference distance (`--scale-ref` or set later in the editor).
- `pipeline export`: SPZ compression, keep the PLY.
- `pipeline run-all`.

Write `docs/PIPELINE.md` with timings, GPU requirements and known failure modes.

**Done when:** one command turns a test video into a scene that loads in the viewer.

### M2: Viewer with waypoints, plus basic editor

Viewer:
- Load the scene with Spark.
- One waypoint per room. Look-around with mouse/touch drag and optional device orientation (request permission on iOS).
- **Zoom changes the field of view only** (clamp about 30–90°). It never moves the camera.
- Smooth ~1 s eased transition between waypoints.
- Bottom room strip (scroll, swipe, arrow keys) plus a mini floor plan with room dots.
- Loading progress, and a clear message on unsupported devices.

Editor:
- Place, move and name waypoints. Set room type and starting direction.
- Draw the floor polygon per room. Set the reference distance for scale.
- Box-crop or delete splats. Add privacy blur/delete regions.
- Save everything to the manifest.

Performance: measure the frame rate at waypoints on an iPhone-class device (target 30+ fps) and the download size per apartment. Report both; flag downloads above 60 MB.

**Done when:** a test apartment can be navigated room by room on desktop and mobile viewports.

### M3: Floors (automatic detection, realistic replacement)

Detection (`pipeline floor`):
- Run a semantic segmentation model with a "floor" class on the frames. Shortlist 2–3 candidates with permissive licences and confirm with me before adding one.
- Project each Gaussian centre into the frames with the known poses and vote floor / not floor. Fit the plane with RANSAC, then refine with a height threshold.
- Export `scene_nofloor.spz` and `floor.spz`.
- Floor polygons come from the RoomPlan export if present, otherwise from a concave hull of the floor Gaussians per room. They stay editable in the editor.

Lighting transfer:
- Per room, render a top-down orthographic image of `floor.spz`.
- Compute a shading map from strongly blurred luminance (this removes the old pattern but keeps shadows and light falloff). Normalise it around 1.0 and save it as PNG.

Viewer:
- When a new floor is chosen, hide `floor.spz` and render the room's floor polygon as a mesh on the floor plane with a PBR material.
- Material: albedo, plus normal and roughness when available. UVs in metres from the real tile size. Multiply by the shading map. Optional subtle reflection for glossy materials.
- Verify, and add a test, that splats such as furniture correctly occlude the new floor mesh (Spark combines splats and meshes in one three.js scene).

Floor catalog (`catalog/floors/*.json`):
```json
{ "id": "oak-herringbone-light", "name": "Light oak herringbone", "category": "wood",
  "tags": ["warm", "scandinavian", "classic"], "description": "...", "tileSizeM": [0.6, 0.6],
  "maps": { "albedo": "...", "normal": "...", "roughness": "..." }, "glossy": false,
  "license": "CC0", "source": "https://..." }
```
- Categories: wood, tile, stone, painted, stencilled, vintage.
- Seed about 15 materials with a script from CC0 sources (Poly Haven, ambientCG), recording licence and source. Add a few stencil/painted patterns made procedurally.

**Done when:** the floor in a test scene is detected with little or no manual cleanup, and swapping floors looks plausible from every waypoint (before/after screenshots in docs).

### M4: Floor upload

Flow:
1. The user uploads a photo of one tile or a piece of floor.
2. The API proposes the tile/floor region.
3. The user can adjust four corners.
4. The API rectifies the perspective (OpenCV homography) and makes the image seamless (deterministic offset-and-blend).
5. The user enters the tile size in cm.
6. The API generates a normal map from luminance and a default roughness.
7. The user sees a preview in the room.

Validate type and size, strip EXIF, and limit upload size.

**Done when:** three different test photos produce usable tiled floors.

### M5: AI assistant (one assistant for floors, furniture, recolor)

- Endpoint `POST /api/chat` calls the Anthropic Messages API with tool use. The model comes from `ASSISTANT_MODEL` (default `claude-haiku-4-5-20251001`), the key from `ANTHROPIC_API_KEY`.
- Tools:
  - `get_context()`: current room, room type, size, current state
  - `search_catalog(kind, query, filters)`: tag/keyword scoring over compact summaries; the catalog is small
  - `set_floor(roomIds | "all", floorId)`
  - `apply_style(roomId, styleId)`
  - `add_item(roomId, itemId, position?)`
  - `auto_layout(roomId, styleId)`
  - `recolor_item(itemId, colorHex | materialPreset)`
  - `reset(roomId | "all")`
- System prompt rules:
  - Only choose from the catalog.
  - Translate vague or unusual requests ("Tuscan", "2010 millennial optimism") into concrete attributes (colours, materials, era), pick the closest matches, and explain the choice in one short sentence.
  - If nothing fits, say so and offer the closest option.
  - Never claim an item exists that doesn't.
- The web app executes tool results client-side and shows short replies with quick-reply chips.
- Cost guard: rate limit per session, max tokens, token usage logged.
- Add `docs/assistant-evals.jsonl` with about 30 prompts (normal styles, unusual styles, impossible requests) and expected tool calls, plus a script that reports the pass rate.

**Done when:** the eval pass rate is at least 85% and the assistant drives floors end to end in the viewer.

### M6: Furniture catalog, styles, manual placement

Furniture catalog (`catalog/furniture/*.json`):
```json
{ "id": "sofa-oslo-3", "name": "...", "category": "sofa", "styles": ["modern", "minimalist"],
  "colors": ["#c8c2b8"], "materials": ["linen", "oak"], "dimensionsM": [2.1, 0.8, 0.9],
  "glb": "...", "thumbnail": "...", "license": "CC0", "source": "https://..." }
```
- GLB files compressed with meshopt or Draco, target under 2 MB each.

Styles (`catalog/styles/*.json`):
- Start with 3–4 fully stocked styles: Modern, Minimalist, Mid-Century Modern, Traditional. Prepare entries for Contemporary, Transitional and Eclectic.
- Each style holds a description, palette, preferred materials, picks per room type and matching floor ids.

Assets:
- Seed with CC0 models through a script.
- List gaps that need purchased models with a commercial licence in `docs/ROADMAP.md`.
- Never add an asset without a recorded licence.

Placement:
- Drag on the floor (raycast to the floor plane), rotate with 15° snapping, snap to the nearest wall within 10 cm.
- Collisions against the room polygon and other items (oriented boxes). Delete and duplicate.

Lighting:
- Use an environment map rendered from the splat at the current waypoint (low-resolution cubemap).
- Add soft contact shadows under items so furniture does not look pasted in.

**Done when:** a user can furnish a room in each stocked style by hand, and the assistant can apply a style.

### M7: Automatic layout

Rule-based templates per room type, using the room polygon, walls, doors and windows:
- The bed headboard goes against a wall without the door.
- The sofa goes against the longest free wall, facing the window or TV wall.
- Tables get at least 0.8 m clearance.
- Walkways of 0.8–0.9 m and door swing areas stay free.

The assistant chooses the items and the walls. A deterministic solver places them and checks collisions. If no valid layout exists, fall back to fewer items. Unit-test the solver.

**Done when:** every room in the demo apartments gets a valid layout.

### M8: Furnished apartments, recolor existing furniture

- Items come from RoomPlan object boxes (if present) or boxes drawn in the editor. Splats inside a box are tagged as that item.
- Recolor applies tint/hue/saturation edits only to that item's splats, using Spark's splat colour editing. Offer presets plus a colour picker, and wire it to the assistant's `recolor_item`.
- Swapping or removing existing furniture stays out of scope. Write the design note in `docs/ROADMAP.md`: remove the splats in the box, inpaint the wall behind, place a catalog item using the box as layout.

**Done when:** at least three pieces in the furnished demo apartment can be recolored convincingly.

### M9: AI 360 preview (stretch, ask me before starting)

- At a waypoint, render six cube faces in the browser and send them to the API.
- The API calls an image-generation provider behind an `ImageRestyleProvider` interface (I choose the provider later), guided by depth/edges so walls and windows stay in place.
- Show the result as a separate panorama labelled "AI preview", never mixed into the 3D scene. Cache the results.

### M10: Demo polish

- Landing page with one featured apartment, and share links that carry the viewer state.
- Analytics events (room viewed, time per room, floors and styles tried) stored in SQLite, plus a simple per-project summary page for estate agents.
- Deployment: web on Cloudflare Pages or similar, assets on R2, API on a small VM.
- Write `docs/DEMO.md` with a step-by-step script for the investor demo.

---

## 7. Working rules

1. First write `docs/SPEC.md` and a short plan (milestones, risks, open questions). Wait for my OK before starting M1.
2. One milestone at a time, small commits with clear messages, and `docs/PROGRESS.md` updated after each milestone.
3. Test the logic: geometry, voting, plane fit, homography, solver, tool handling. For visual features, take Playwright screenshots.
4. If no GPU is available, still build and test the pipeline code paths with a small permissively licensed sample or precomputed outputs, and tell me exactly what was and wasn't run.
5. Prefer simple code over clever abstractions. No dead code, no unused dependencies.
6. When a product decision is unclear, ask me. When a technical fact is unclear (API, licence, file format), check the official docs instead of guessing.
7. Never commit secrets or large binaries.

---

## 8. What I will provide

- iPhone 16 Pro videos and optional RoomPlan exports of 2–3 apartments (one furnished).
- A Linux machine or cloud instance with an NVIDIA GPU (ideally 24 GB VRAM) for training.
- `ANTHROPIC_API_KEY`, Cloudflare R2 credentials, a domain.
- The final brand name.

---

## 9. Definition of done for the MVP

- [ ] 2–3 apartments viewable through a share link on iPhone and laptop.
- [ ] One waypoint per room, 360° look-around, zoom, smooth transitions, room strip and mini floor plan.
- [ ] Automatic floor detection and realistic floor replacement with about 15 presets and user upload.
- [ ] Furniture in at least 3 complete styles, with manual placement and automatic layout.
- [ ] Recolor of existing furniture in the furnished apartment.
- [ ] One AI assistant driving floors, furniture and recolor from the catalog (eval pass rate ≥ 85%).
- [ ] "Virtually staged" badge and one-tap reset to the original.
- [ ] `docs/LICENSES.md` complete. No secrets or large binaries in git.
