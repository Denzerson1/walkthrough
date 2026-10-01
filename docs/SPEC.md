# walkthrough — Specification

Working name: `walkthrough` (brand name TBD).
Scope source of truth: `docs/BRIEF.md`. This document records the brief **plus the decisions taken in the kickoff interview**, and is kept up to date as milestones land.

Last updated: 2026-10-01

---

## 1. Product summary

A mobile-first web app showing real apartments as photorealistic Gaussian-splat walkthroughs captured from iPhone 16 Pro video. Users move between one fixed viewpoint per room, look around 360°, zoom (FOV only), change floors, add and recolor furniture, and drive all of it through one AI assistant constrained to our catalog.

MVP audience: investors and first estate agents. Content: 2–3 real apartments, one furnished, captured and processed by us.

**Out of scope:** self-service upload, user accounts, payments, swapping or removing existing furniture.

---

## 2. Kickoff decisions

These answer the open questions in the brief. Where a decision narrows the brief it is marked.

### Hardware and pipeline

| Topic | Decision |
|---|---|
| GPU | NVIDIA RTX 2080 Super, **8 GB VRAM**, on the user's Windows PC |
| OS for pipeline | **WSL2 + Ubuntu with CUDA passthrough.** Keeps the brief's Linux/CUDA target and the CUDA Dockerfile valid. |
| VRAM strategy | **Cap splat count, keep 4K source frames.** Frames downsampled to ~1600 px long edge for training; Gaussian count capped (target 1–2 M). Both are config, not hardcoded. |
| Rationale | 8 GB is well under the brief's ideal 24 GB. Capping splats also serves the <60 MB per-apartment download target. Per-room training with stitching was considered and rejected for now (seam risk at doorways); revisit only if quality is unacceptable. |
| Escalation | Renting a 24 GB box is the first escalation if M1 shows 8 GB is the binding quality constraint. It will be raised with real numbers (PSNR, VRAM, scene size), not a guess. |

### Data and capture

| Topic | Decision |
|---|---|
| M0 test data | **Synthetic scene from a committed generator** (`scripts/make_test_scene.py`): floor plane, walls, a few boxes, a few hundred thousand Gaussians, exported to PLY/SPZ. No download, no licence risk, reproducible. The generated binary is git-ignored; the generator is committed. |
| Real captures | The user's own flat and a friend's flat. |
| Consent / GDPR | Low risk for the MVP because both properties are the user's or a friend's. Privacy masking (M2) is still built as a product feature per the brief. A consent checklist goes into `docs/CAPTURE.md` for the first third-party property. |
| RoomPlan | **Yes — RoomPlan exports will be captured alongside video.** The pipeline prefers RoomPlan for walls, doors, windows, room polygons and furniture boxes. Fallbacks (concave hull of floor Gaussians for polygons, editor-drawn boxes for items) stay first-class, because RoomPlan can fail or be missing for any single room. |

### AI assistant

| Topic | Decision |
|---|---|
| Default model | `ASSISTANT_MODEL=claude-haiku-4-5-20251001` (as in the brief). Picking floor/furniture/style presets from a small catalog through constrained tool use is a narrow task well suited to Haiku. |
| Escalation | If the M5 eval pass rate is below 85% after prompt and tool-schema work, switch `ASSISTANT_MODEL` to `claude-opus-5`. Pass rate is reported per model so the choice is made on evidence. |
| Model note | "Opus 5.5" does not exist. Current lineup: Opus 5 (`claude-opus-5`), Sonnet 5, Haiku 4.5 (`claude-haiku-4-5-20251001`), Fable 5.1. |
| Cost guard | Per-session rate limit, `max_tokens` cap, token usage logged per request. Required before any live key is used. |

### Legal and assets

| Topic | Decision |
|---|---|
| Posture | **Investor demo, pragmatic.** Anything on the shipping path must be CC0 / MIT / Apache-2.0 / BSD, or a paid licence permitting commercial use. |
| Furniture GLBs | **CC0 core plus flagged demo-only extras.** Where a style has a visible gap, a clearly tagged `DEMO-ONLY` asset may be used, provided it is listed in `docs/LICENSES.md` with a must-replace note and a `docs/ROADMAP.md` entry naming the replacement and its cost. |
| Hard stop | Original Inria/GraphDeco `gaussian-splatting` is forbidden (non-commercial licence). Approved stack: gsplat (Apache-2.0), COLMAP/GLOMAP, Spark `@sparkjsdev/spark` (MIT) with three.js. |
| Gate | The `/licence-check` skill runs before adding any dependency, model, dataset, texture or 3D asset. Every item lands in `docs/LICENSES.md`. |

### Delivery

| Topic | Decision |
|---|---|
| Deadline | **None fixed. Build it right** — milestones in order, full test coverage. Unusually expensive milestones are flagged before starting. |
| Editor auth | **Single shared `EDITOR_PASSWORD`** per the brief. HTTP-only session cookie, rate-limited login. No user accounts. |
| Credentials | **None available yet** — no `ANTHROPIC_API_KEY`, no R2, no domain. M0–M4 need none of them. `.env.example` documents every key; code fails with an explicit message rather than degrading silently. |

---

## 3. Architecture

Monorepo: **pnpm workspaces** (TypeScript) + **uv** (Python).

```
apps/web/          Vite + React + TS + three.js + Spark, Zustand, Tailwind. Mobile-first.
                   Public viewer   /p/:projectId
                   Internal editor /edit/:projectId   (EDITOR_PASSWORD)
services/api/      Python 3.11, FastAPI, SQLModel + SQLite, storage adapter (local disk | S3-compatible).
                   Project manifests, catalog, uploads, chat, analytics events.
pipeline/          Python CLI (Typer): video -> scene. WSL2 Ubuntu + CUDA. Dockerfile on a CUDA base image.
catalog/           floors/ furniture/ styles/   (JSON + small thumbnails)
scripts/           Asset seeding and the synthetic test-scene generator.
docs/              BRIEF.md SPEC.md CAPTURE.md PIPELINE.md PROGRESS.md ROADMAP.md DEMO.md LICENSES.md screenshots/
data/              git-ignored: projects, uploads, cache
```

Tests: **pytest** (pipeline, api), **Vitest** (web logic), **Playwright** smoke tests on a desktop and an iPhone viewport. CI runs lint and tests only — **no GPU jobs in CI**.

### Storage rule

No binary over 1 MB in git. Videos, frames, splats, GLBs and textures live under `data/` (git-ignored) and, once deployed, in Cloudflare R2. Catalog JSON and small thumbnails are committed.

---

## 4. Data model

Project layout under `data/projects/<projectId>/`:

```
input/video.mov                   iPhone capture
input/roomplan/                   RoomPlan export (USDZ or JSON) — expected to be present
work/                             frames, poses, training output (intermediate)
scene/scene.spz                   full scene
scene/scene_nofloor.spz           scene without floor splats
scene/floor.spz                   floor splats only
scene/floor_shading_<roomId>.png  per-room shading map
scene/manifest.json
```

`manifest.json` follows the brief §4 exactly: `id`, `name`, `units`, `upAxis`, `transform`, `floor.plane`, `rooms[]` (each with `waypoint`, `floorPolygon`, `walls`, `openings`), `items[]` (oriented `box`, `source`), `privacyMasks[]`. Any extension to this schema is documented here in the same commit that introduces it.

Scene convention: **floor at y = 0, Y up, metres.** Every pipeline stage asserts this after `align`.

Viewer state (floor per room, placed furniture, recolors) is a **separate JSON object**, encodable into a share URL. It is never written back into the manifest.

---

## 5. Non-negotiable product rules

1. **Honest staging.** Every modified view — floor, furniture, recolor, AI preview — shows a visible **"Virtually staged"** badge, with one-tap return to the original.
2. **Zoom is FOV only**, clamped roughly 30–90°. The camera never leaves the waypoint.
3. **Privacy.** The editor can blur or delete regions before a project is published.
4. **No faked results.** If something cannot run or be verified (no GPU, missing asset, no API key), say so plainly and use the documented fallback. An unrun step is never reported as passing.
5. **Secrets only in `.env`** (git-ignored), with `.env.example` committed.

---

## 6. Milestones

Order and "Done when" criteria come from `docs/BRIEF.md` §6 and are unchanged.

| ID | Milestone | Done when |
|---|---|---|
| M0 | Foundations | `pnpm dev` starts web + api, web shows a test splat, CI green |
| M1 | Pipeline (video → scene) | one command turns a test video into a scene that loads in the viewer |
| M2 | Viewer waypoints + basic editor | a test apartment navigable room by room, desktop and mobile |
| M3 | Floors: detection + realistic replacement | floor detected with little cleanup; swaps plausible from every waypoint |
| M4 | Floor upload | three test photos produce usable tiled floors |
| M5 | AI assistant | eval pass rate ≥ 85%, assistant drives floors end to end |
| M6 | Furniture catalog, styles, manual placement | a room furnishable by hand in each stocked style |
| M7 | Automatic layout | every room in the demo apartments gets a valid layout |
| M8 | Furnished apartments, recolor | ≥ 3 pieces recolorable convincingly |
| M9 | AI 360 preview | **stretch — ask before starting** |
| M10 | Demo polish | landing page, share links, analytics, deployment, `docs/DEMO.md` |

Each milestone runs via `/milestone <id>`: plan → OK → implement in small steps → evidence → subagent diff review → `docs/PROGRESS.md` + commit.

---

## 7. Working rules

From the brief §7, in force for every milestone:

1. `docs/SPEC.md` and a plan come first. **No M1 work without an explicit OK.**
2. One milestone at a time. Small commits, clear messages, `docs/PROGRESS.md` updated after each.
3. Test the logic: geometry, floor voting, plane fit, homography, layout solver, tool handling. Visual features get Playwright screenshots.
4. Without a GPU run, still build and test code paths on small fixtures, and state exactly what was and was not executed.
5. Simple code over clever abstractions. No dead code, no unused dependencies.
6. Product uncertainty → ask. Technical uncertainty (API, licence, file format) → check official docs, never guess.
7. Never commit secrets or large binaries.

---

## 8. Definition of done (MVP)

- [ ] 2–3 apartments viewable via share link on iPhone and laptop
- [ ] One waypoint per room, 360° look-around, FOV zoom, smooth transitions, room strip, mini floor plan
- [ ] Automatic floor detection and realistic replacement, ~15 presets plus user upload
- [ ] Furniture in ≥ 3 complete styles, manual placement and automatic layout
- [ ] Recolor of existing furniture in the furnished apartment
- [ ] One AI assistant driving floors, furniture and recolor from the catalog (eval ≥ 85%)
- [ ] "Virtually staged" badge and one-tap reset
- [ ] `docs/LICENSES.md` complete; no secrets or large binaries in git
