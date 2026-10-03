# walkthrough — working notes

3D property walkthrough MVP. Real apartments captured on an iPhone, rebuilt as
Gaussian splats, viewable in the browser with swappable floors and furniture
and one AI assistant driving it all.

Working name `walkthrough` until a brand name is chosen.
Full scope: `docs/BRIEF.md`. Decisions and architecture: `docs/SPEC.md`.
State of play: `docs/PROGRESS.md`.

## Commands

```bash
# install
pnpm install
uv sync

# run both servers (web :5173, api :8000)
pnpm dev

# tests
pnpm test            # vitest + pytest
pnpm test:web        # vitest only
pnpm test:py         # pytest only
pnpm test:e2e        # playwright, needs pnpm dev running

# lint
pnpm lint            # eslint + tsc + ruff

# seed data
pnpm seed:testscene  # synthetic splat apartment -> data/projects/demo-01
pnpm seed:floors     # 16 floor materials into catalog/floors
pnpm seed:assets     # downloads the real textures and meshes, and writes
                     # catalog/furniture + catalog/styles from
                     # scripts/furniture_themes.py

# capture pipeline — runs inside WSL2 Ubuntu on the GPU; see docs/PIPELINE.md.
# `pnpm run` (not `pnpm pipeline`) so every argument reaches the CLI.
pnpm run setup:pipeline        # once: CUDA, torch, gsplat, pycolmap in WSL
pnpm run pipeline run-all "C:\captures\room.mov" --project flat-01 --roomplan "C:\captures\room.json"
pnpm run pipeline ingest|frames|poses|train|align|export|info --project flat-01

# assistant evals (needs ANTHROPIC_API_KEY)
pnpm evals:assistant
uv run python scripts/run_assistant_evals.py --dry-run
```

## Layout

```
apps/web/          Vite + React + TS + three.js + Spark, Zustand, Tailwind
services/api/      FastAPI + SQLModel + SQLite, storage adapter
pipeline/          Typer CLI: video -> scene
catalog/           floors/ furniture/ styles/ (JSON + small thumbnails)
scripts/           seeding and the synthetic test scene
docs/              BRIEF, SPEC, CAPTURE, PIPELINE, PROGRESS, ROADMAP, DEMO, LICENSES
data/              git-ignored: projects, uploads, cache
```

## Constraints that are not negotiable

These come from `docs/BRIEF.md` §2. Breaking one is a bug, not a trade-off.

1. **Licences.** The owner stood this down on 2026-10-02: do not stop work
   for a licence check or keep `docs/LICENSES.md` current unless asked.
   Everything in the project today is CC0, MIT, Apache-2.0 or BSD anyway, so
   nothing shipped depends on the relaxation. Two things still hold, because
   they are cheap and the downside is not: never use the Inria/GraphDeco
   `gaussian-splatting` code (its licence forbids commercial use — gsplat is
   the Apache-2.0 replacement already in use), and say so plainly if an asset
   is added whose licence forbids commercial use.
2. **No large binaries in git.** Anything over 1 MB — videos, frames, splats,
   GLBs, textures — lives under `data/` (git-ignored) or R2. Catalog JSON and
   small thumbnails may be committed.
3. **Secrets only in `.env`**, which is git-ignored. Keep `.env.example`
   current.
4. ~~**Honest staging.** Every modified view shows the "Virtually staged"
   badge~~ — **the owner removed the badge on 2026-10-02.** Do not add it
   back. "Hold to compare" and "Reset" stay: a modified view must still be
   one press from the real capture. Note that virtually staged photography is
   regulated advertising in several markets, so this will need revisiting
   before anything is published to buyers.
5. **Privacy.** The editor can blur or delete regions before publishing.
6. **No faked results.** If something cannot run or be verified, say so and
   use the documented fallback. Never report an unrun step as passing.

## Conventions

- **Scene space is metres, Y up, floor at y = 0.** The pipeline's `align`
  stage guarantees it; the viewer relies on it and applies no orientation
  flip. Raw 3DGS exports are Y-down — convert, do not special-case.
- **Manifest vs viewer state.** `scene/manifest.json` describes the building
  and only the editor writes it. What the user changes (floors, furniture,
  recolors) is a separate object that encodes into a share URL and is never
  written back into the manifest.
- **Splats are flat, not round.** Anything generating splats orients them to
  the surface — thin along the normal, wide in plane. Isotropic splats of
  spacing-scale radius render as fog, which is what the first synthetic scene
  looked like.
- **Furniture categories must be the layout solver's** (`_RULES` in
  `layout.py`). Inventing one means it silently falls to the default rule: a
  side table typed `table` is treated as a dining table and placed mid-room.
- **The manifest schema lives in two places** and must stay in sync:
  `pipeline/src/walkthrough_pipeline/manifest.py` and
  `apps/web/src/lib/manifest.ts`.
- **Geometry is duplicated deliberately** in `layout.py` and `geometry.ts`,
  because the solver runs server-side and placement runs client-side. They
  must agree, including edge cases — a point exactly on a polygon edge counts
  as inside in both.
- **The viewer is free-roam.** The camera walks at eye height (1.6 m) with
  collision against room walls, and passes between rooms through door
  openings only. `canStand` / `moveWithCollision` in `geometry.ts` own that
  rule. Room waypoints are still the entry point and what the room strip
  glides to, but the camera is no longer pinned to them.
  Controls: click or tap the floor to walk there (the primary one, and the
  only one on touch), drag to look, WASD or arrows to walk, Q/E to turn, R/F
  to pitch, wheel to zoom. Keyboard pitch is a discrete nudge per press so a
  test can repeat it.
- **Zoom changes field of view only** (clamped 30–90°), on the wheel or a
  pinch. The camera is moved by walking, never by zooming.
- **The assistant may only name catalog ids.** Every tool call is validated
  against the catalog server-side before it reaches the viewer. Mutating
  tools are applied client-side; read-only tools run on the server.
- Prefer simple code over clever abstractions. No dead code, no unused
  dependencies.
- When a product decision is unclear, ask. When a technical fact is unclear
  (an API, a licence, a file format), check the official docs rather than
  guessing.

## Testing

- Logic gets a unit test: geometry, floor voting, plane fitting, homography,
  the layout solver, assistant tool handling.
- Visual features get a Playwright screenshot in `docs/screenshots/`.
- A green test that cannot fail is worse than no test. The splat render check
  screenshots a centre crop and requires 30% coverage, because an earlier
  version passed against an empty canvas.
- CI runs lint and tests only. No GPU jobs.

## Workflow

Milestones run one at a time via `/milestone <id>`: plan, wait for OK,
implement in small steps with tests after each, show evidence for the
"Done when" check, review the diff with a subagent, update
`docs/PROGRESS.md`, commit.
