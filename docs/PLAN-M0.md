# M0 — Foundations: plan

**Done when** (from the brief): `pnpm dev` starts web and api, the web app shows a test splat, and CI is green.

Status: **planned, not started.** Run `/milestone M0` to execute.

---

## 1. What gets built

### Repo scaffold
- `pnpm-workspace.yaml`, root `package.json` with `dev`, `build`, `test`, `lint` scripts.
- `apps/web/` — Vite + React + TypeScript + Tailwind + Zustand, three.js and `@sparkjsdev/spark`. Routes `/p/:projectId` and `/edit/:projectId` exist as stubs; only the viewer does anything in M0.
- `services/api/` — FastAPI + SQLModel + SQLite, `uv` project. Endpoints in M0: `GET /api/health`, `GET /api/projects/:id/manifest`. Storage adapter interface with a local-disk implementation (S3 implementation deferred to M10).
- `pipeline/` — Typer CLI skeleton with every subcommand from M1 registered and raising a clear "not implemented" error. No pipeline logic in M0.
- `catalog/`, `scripts/`, `data/` (git-ignored) created with `.gitkeep` where needed.

### Root `CLAUDE.md`
Conventions, the commands (install, dev, test, pipeline usage), and the §2 constraints from the brief: licences, no large binaries, secrets in `.env` only, honest staging, privacy, no faked results.

### Synthetic test splat
- `scripts/make_test_scene.py` generates a small room: floor plane at y=0, four walls, two box "furniture" shapes, a couple of hundred thousand Gaussians with plausible colours and scales.
- Writes `data/projects/demo-01/scene/scene.ply`, converts to `scene.spz`, and writes a `manifest.json` with two rooms and waypoints matching the brief schema.
- The output is git-ignored; the generator is committed. Run via `pnpm seed:testscene`.

### Docs and config
- `docs/LICENSES.md` — table seeded with the stack chosen in SPEC §2 (gsplat, Spark, three.js, COLMAP/GLOMAP, FastAPI, React, Vite, Tailwind, Zustand), each with version, licence, link and how we use it.
- `docs/CAPTURE.md` — the brief's §5 capture guide verbatim in structure, plus the RoomPlan step and the consent checklist from SPEC §2.
- `docs/PROGRESS.md`, `docs/ROADMAP.md` created with their M0 entries.
- `.env.example` — `EDITOR_PASSWORD`, `ANTHROPIC_API_KEY`, `ASSISTANT_MODEL`, `STORAGE_BACKEND`, R2 keys, `DATABASE_URL`. All documented, none populated.
- `.gitignore` — `data/`, `.env`, `node_modules/`, `__pycache__/`, `.venv/`, build output, `*.ply`, `*.spz`, `*.glb` over the size rule.

### CI
`.github/workflows/ci.yml` on push and PR: pnpm install + `tsc --noEmit` + eslint + Vitest; uv sync + ruff + pytest; Playwright smoke test on desktop and iPhone 13 viewports. **No GPU jobs.**

---

## 2. Order of work

1. Workspace scaffold + `.gitignore` + `.env.example` — commit.
2. `services/api` health endpoint + pytest — commit.
3. `apps/web` shell, routing, Vitest — commit.
4. `scripts/make_test_scene.py` + manifest + pytest on the geometry — commit.
5. Spark viewer component loading the synthetic splat — commit.
6. Playwright smoke test + screenshots to `docs/screenshots/` — commit.
7. CI workflow, run it green — commit.
8. `CLAUDE.md`, `LICENSES.md`, `CAPTURE.md`, `PROGRESS.md` — commit.

Tests run after every step, per the milestone skill.

---

## 3. Evidence for "Done when"

| Criterion | Proof |
|---|---|
| `pnpm dev` starts web and api | Terminal output of both servers plus `curl /api/health` returning 200 |
| Web app shows a test splat | Playwright screenshot of `/p/demo-01` with the synthetic room rendered, saved to `docs/screenshots/m0-viewer-desktop.png` and `-iphone.png` |
| CI is green | GitHub Actions run URL and status |

---

## 4. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **Spark + Vite + three.js version friction** — Spark is young and pins specific three.js versions | Blocks the M0 "Done when" directly | Pin exact versions on day one, record them in `LICENSES.md`, verify the import path against Spark's official docs rather than assuming. First thing to try, so failure surfaces early. |
| **Synthetic splat looks unconvincing** and hides real rendering bugs | Low — it only has to prove the render path | Keep it deliberately simple. It proves "a splat loads and renders", nothing more. Real validation starts at M1. |
| **SPZ writer availability from Python** | Medium — may force PLY-only in M0 | Fall back to serving PLY in M0 and defer SPZ compression to M1's `export` stage, where it belongs anyway. Would be reported, not hidden. |
| **WSL2 CUDA setup is untested** | Blocks M1, not M0 | Verify the WSL2 + CUDA + driver chain during M0 as a side task so M1 does not start on a broken toolchain. `nvidia-smi` inside WSL2 is the check. |
| **8 GB VRAM** | Quality ceiling from M1 onward | Already decided in SPEC §2: cap splats, downsample frames. Re-raise with numbers after the first real training run. |
| **Playwright WebGL in CI** — headless GPU rendering is unreliable on CI runners | Could make CI flaky | Use `--use-gl=swiftshader` software rendering for the CI smoke test; accept lower visual fidelity in CI and take the real screenshots locally. If software rendering cannot load the splat, the CI test asserts canvas presence and no console errors instead, and this limitation is written into `PROGRESS.md`. |

---

## 5. What I need from you

**Nothing blocks the start of M0.** The following are needed later, in this order:

1. **Confirm WSL2 is installed** (or that you want me to write the setup steps into `docs/PIPELINE.md`) — needed before M1.
2. **A test video** — any short indoor clip from the iPhone, even unpolished, so M1 has real input. Not needed to start M0.
3. **`ANTHROPIC_API_KEY`** — needed at M5, not before.
4. **Approval if any download is proposed.** Per the brief, I ask before downloading anything large. M0 as planned downloads nothing beyond npm/PyPI packages.
5. **The brand name**, whenever you have it — until then everything says `walkthrough`.

---

## 6. Explicitly not in M0

No pipeline logic, no floor detection, no furniture, no assistant, no deployment. The pipeline CLI exists only as a command surface that errors clearly.
