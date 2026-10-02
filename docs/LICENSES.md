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

16 entries in `catalog/floors/`, all backed by a downloaded ambientCG material.

Fetched by `pnpm seed:assets` into `data/assets/floors/<id>/` (git-ignored) as
`color.jpg`, `normal.jpg` and `roughness.jpg`, from the 1K-JPG archive. The
NormalGL variant is taken, not NormalDX — three.js expects the OpenGL
convention. The exact source id for each entry is in its catalog JSON under
`source`, and the mapping lives in `scripts/fetch_assets.py`.

ambientCG publishes under CC0 ([licence](https://ambientcg.com/view?type=Terms)).

| Catalog id | ambientCG id |
|---|---|
| ash-pale-wide | WoodFloor048 |
| concrete-polished | Concrete034 |
| limestone-sand | Rock030 |
| marble-carrara | Marble016 |
| oak-herringbone-light | WoodFloor041 |
| oak-plank-natural | WoodFloor043 |
| painted-board-sage | Planks028 |
| painted-board-white | Planks005 |
| slate-charcoal | Tiles093 |
| stencil-moroccan-blue | Tiles103 |
| stencil-victorian-geometric | Tiles100 |
| terracotta-rustic | Tiles074 |
| terrazzo-grey | Terrazzo009 |
| tile-square-white | Tiles101 |
| vintage-parquet-chevron | WoodFloor037 |
| walnut-dark-plank | WoodFloor051 |

The four painted and stencilled entries carry `tintAlbedo: true`: their scan is
neutral and the catalog colour is the paint. The other twelve render the scan's
own colour, untinted.

### Furniture

42 entries in `catalog/furniture/`, across four themes.

| Source | Licence | Link |
|---|---|---|
| Poly Haven (photogrammetry models) | CC0-1.0 | [polyhaven.com](https://polyhaven.com) |
| Built in the viewer (sofas and beds) | ours, CC0 | `buildProceduralItem` in `SplatScene.tsx` |

Fetched by `pnpm seed:assets` into `data/assets/furniture/<id>/` (git-ignored)
as glTF plus its `.bin` and 1K textures. The mapping, and each piece's stated
real-world height, live in `scripts/furniture_themes.py`; that script is also
what writes `catalog/furniture/` and `catalog/styles/`.

**Models are picked by looking at them, not by their names.** An earlier pass
chose on names alone and filled a theme called "Modern" with a buttoned
chesterfield, a carved wing chair and a floral day bed.

**Two pieces are built in code rather than downloaded.** Poly Haven's
furniture library is almost entirely antique, rustic or industrial: across
roughly 190 furniture assets there is no contemporary sofa and no
contemporary bed. Those are the two pieces a flat most needs, so the
Contemporary, Mid-Century and Industrial themes draw them as plain slab forms
instead. They are ours and carry no third-party licence.

**Scale is not taken on trust.** Each mesh is measured from its glTF
accessors and scaled to the height stated in the theme table. Measured raw,
`steel_frame_shelves_01` is 21 m tall and `WoodenChair_01` 2.3 m.

**Open:** a photoreal contemporary sofa and bed would have to be bought — no
CC0 source has them. Roughly €40–80 each on the usual marketplaces. Until
then the built ones stand, and they do not pretend to be photographs.

### Models and datasets

None. The M3 floor segmentation model is **not chosen yet** — the brief requires shortlisting 2–3 permissively licensed candidates and confirming before adding one.

### Capture data

The only scene is synthetic, generated by `scripts/make_test_scene.py`. It is our own work, git-ignored, and regenerated on demand.

---

## Open items before launch

- [x] Download floor textures and record each ambientCG asset id — done
      2026-10-02, 16 materials, ids recorded above
- [x] Choose furniture models, check each licence, record it here — done
      2026-10-02, Poly Haven, CC0-1.0, one source for all 36 downloaded pieces
- [ ] Buy a photoreal contemporary sofa and bed, or keep the built ones.
      A quality and budget decision, not a licensing one.
- [ ] Shortlist and approve a floor segmentation model (M3)
- [ ] Confirm the ffmpeg build in use; prefer an LGPL build and keep it a separate binary
- [ ] Re-check every licence before the first public deployment
