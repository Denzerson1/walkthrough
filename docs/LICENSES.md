# Licences

Every dependency, model, dataset, texture and 3D asset we ship, with its
licence and how we use it. Required by `docs/BRIEF.md` §2.1.

**Posture** (docs/SPEC.md §2): investor demo, pragmatic. Anything on the
shipping path must be CC0, MIT, Apache-2.0, BSD, or a paid licence that
permits commercial use. A `DEMO-ONLY` asset may be used where a style has a
visible gap, but only if it is listed here with a must-replace note and a
`docs/ROADMAP.md` entry.

**Excluded on purpose:** the original Inria/GraphDeco `gaussian-splatting`
implementation. Its licence forbids commercial use. We use gsplat instead.

Run `/licence-check` before adding anything new.

Last updated: 2026-10-02

---

## Software

### Python

| Name | Version | Licence | Source | How we use it |
|---|---|---|---|---|
| `fastapi` | 0.142.2 | MIT | [link](https://github.com/fastapi/fastapi) | HTTP API: manifests, catalog, uploads, chat, analytics |
| `uvicorn` | 0.54.0 | BSD-3-Clause | [link](https://uvicorn.dev/) | ASGI server for the API in dev and production |
| `sqlmodel` | 0.0.47 | MIT | [link](https://github.com/fastapi/sqlmodel) | SQLite models for analytics events and uploads |
| `pydantic` | 2.13.5 | MIT | [link](https://github.com/pydantic/pydantic) | Manifest schema validation, shared with the pipeline |
| `pydantic-settings` | 2.15.0 | MIT | [link](https://github.com/pydantic/pydantic-settings) | Loads .env into typed settings |
| `python-multipart` | 0.0.32 | Apache-2.0 | [link](https://github.com/Kludex/python-multipart) | Multipart parsing for floor-photo uploads (M4) |
| `itsdangerous` | 2.2.0 | BSD License | [link](https://github.com/pallets/itsdangerous/) | Signs the editor session cookie |
| `typer` | 0.27.2 | MIT | [link](https://github.com/fastapi/typer) | Pipeline CLI |
| `rich` | 15.0.0 | MIT | [link](https://github.com/Textualize/rich) | Pipeline CLI output |
| `numpy` | 2.4.6 | see project page | [link](https://numpy.org) | Geometry, plane fitting, splat arrays |
| `pillow` | 12.3.0 | MIT-CMU | [link](https://tidelift.com/subscription/pkg/pypi-pillow?utm_source=pypi-pillow&utm_medium=pypi) | Image I/O |
| `opencv-python-headless` | 5.0.0.93 | Apache 2.0 | [link](https://github.com/opencv/opencv-python) | Homography, seamless tiling, normal maps, hulls |
| `scipy` | 1.17.1 | BSD License | [link](https://scipy.org/) | Numerical helpers for plane fitting |
| `plyfile` | 1.1.5 | GNU General Public License v3 or later (GPLv3+) | [link](https://github.com/dranjan/python-plyfile) | Reference for the splat PLY layout |
| `anthropic` | 1.11.0 | MIT | [link](https://github.com/anthropics/anthropic-sdk-python) | Calls the Messages API for the assistant (M5) |
| `httpx` | 0.28.1 | BSD-3-Clause | [link](https://github.com/encode/httpx) | HTTP client, and the FastAPI test client |
| `pytest` | 9.1.1 | MIT | [link](https://docs.pytest.org/en/latest/) | Python tests |
| `ruff` | 0.16.10 | MIT | [link](https://docs.astral.sh/ruff) | Python lint |

### JavaScript / TypeScript

| Name | Version | Licence | Source | How we use it |
|---|---|---|---|---|
| `@sparkjsdev/spark` | 2.3.1 | MIT | [link](https://github.com/sparkjsdev/spark) | Renders Gaussian splats inside the three.js scene |
| `three` | 0.186.1 | MIT | [link](https://threejs.org/) | 3D scene, camera, replacement floor meshes, furniture |
| `react` | 19.2.0 | MIT | [link](https://react.dev/) | Web app UI |
| `react-dom` | 19.2.0 | MIT | [link](https://react.dev/) | React DOM renderer |
| `react-router-dom` | 7.9.3 | MIT | [link](https://reactrouter.com/) | Routes: landing, /p/:id viewer, /edit/:id editor |
| `zustand` | 5.0.8 | MIT | [link](https://github.com/pmndrs/zustand) | Viewer and manifest state |
| `tailwindcss` | 3.4.18 | MIT | [link](https://tailwindcss.com/) | Styling |
| `vite` | 7.1.9 | MIT | [link](https://vite.dev/) | Dev server and build |
| `typescript` | 5.9.3 | Apache-2.0 | [link](https://www.typescriptlang.org/) | Types across the web app |
| `vitest` | 3.2.4 | MIT | [link](https://vitest.dev/) | Web unit tests |
| `@playwright/test` | 1.56.0 | Apache-2.0 | [link](https://playwright.dev/) | Browser smoke tests and screenshots |
| `pngjs` | 7.0.0 | MIT | [link](https://github.com/pngjs/pngjs) | Reads back screenshots in the splat render check |

### External tools

Invoked as separate processes by the pipeline, never linked into our code. None is installed on the current machine — see `docs/PROGRESS.md`.

| Name | Version | Licence | Source | How we use it |
|---|---|---|---|---|
| `gsplat` | not installed | Apache-2.0 | [link](https://github.com/nerfstudio-project/gsplat) | Planned trainer for M1. Needs CUDA; installed inside WSL2, not a default dep |
| `COLMAP` | not installed | BSD-3-Clause | [link](https://colmap.github.io/) | Feature extraction and matching for camera poses (M1) |
| `GLOMAP` | not installed | BSD-3-Clause | [link](https://github.com/colmap/glomap) | Global mapper, preferred over COLMAP's (M1) |
| `ffmpeg` | not installed | LGPL-2.1+ / GPL-2+ | [link](https://ffmpeg.org/) | Frame extraction. Invoked as a separate binary, never linked |
| `spz` | not installed | MIT | [link](https://github.com/nianticlabs/spz) | SPZ compression for scene export. Absent here, so scenes ship as PLY |

### Fonts

| Name | Version | Licence | Source | How we use it |
|---|---|---|---|---|
| `Archivo` | Google Fonts | OFL-1.1 | [link](https://fonts.google.com/specimen/Archivo) | Interface typeface |
| `IBM Plex Mono` | Google Fonts | OFL-1.1 | [link](https://fonts.google.com/specimen/IBM+Plex+Mono) | Measurements in metres only |

---

## Assets

### Floor materials

16 entries in `catalog/floors/`. All CC0.

| Kind | Count | Licence | Status |
|---|---|---|---|
| Sourced from ambientCG | 11 | CC0 | **Texture maps not downloaded.** Each entry records its source URL; the viewer falls back to a category colour. |
| Generated procedurally | 5 | CC0 | Generated by `scripts/seed_floors.py`, so they are our own work. Thumbnails committed (under 1 MB each). |

ambientCG publishes under CC0 ([licence](https://ambientcg.com/view?type=Terms)). Confirm each asset's page before downloading, and record the exact asset id here.

### Furniture

23 entries in `catalog/furniture/`, every one `assetStatus: "pending"`.

**No GLB file has been downloaded.** Dimensions are real so the layout solver and collision checks work; the viewer draws a correctly sized placeholder box. Intended sources are the Khronos glTF Sample Assets (CC0 / CC-BY per model) and Poly Haven (CC0). Each model's licence must be checked individually and recorded here before the mesh is committed to `data/` or R2.

### Models and datasets

None. The M3 floor segmentation model is **not chosen yet** — the brief requires shortlisting 2–3 permissively licensed candidates and confirming before adding one.

### Capture data

The only scene is synthetic, generated by `scripts/make_test_scene.py`. It is our own work, git-ignored, and regenerated on demand.

---

## Open items before launch

- [ ] Download floor textures and record each ambientCG asset id
- [ ] Choose furniture models, check each licence, record it here
- [ ] Shortlist and approve a floor segmentation model (M3)
- [ ] Confirm the ffmpeg build in use; prefer an LGPL build and keep it a separate binary
- [ ] Re-check every licence before the first public deployment
