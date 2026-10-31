"""
Export the static site for GitHub Pages.

The Pages build of the viewer (`vite build --mode pages`) has no server, so
this writes beside it what the API would have served for the projects named:
the manifest and catalog responses as JSON under api/, the scene and catalog
files under files/, and a copy of the page at every route, so a deep link
loads with a 200 rather than through a 404 page.

Only what the viewer asks for is copied. A project's scene/ also holds the
owner's photos the rebuild worked from, which nothing in the viewer shows.

The rebuilt flat's glTF files are meshopt-compressed on the way (the flat goes
from 6.5 MB to 2.4 MB), which is what a phone on a mobile connection waits for.

The page gets the card a messenger shows for a shared link, with the first
project's preview.jpg (data/projects/<id>/preview.jpg) when there is one, and
is kept out of search engines: it is someone's home.

Run:  pnpm build:pages   (builds the viewer, then runs this)
"""

from __future__ import annotations

import html
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "services" / "api" / "src"))

from walkthrough_api import catalog as catalog_mod  # noqa: E402
from walkthrough_api import projects  # noqa: E402
from walkthrough_api.config import get_settings  # noqa: E402

DIST = REPO / "apps" / "web" / "dist"
#: Where Pages serves the site; a link preview's image must be an absolute URL.
SITE_URL = "https://denzerson1.github.io/walkthrough/"

#: Lossless for what shows: 16-bit positions over a mesh's own bounds are
#: well under a millimetre for a room, 16-bit UVs are exact to a light-map
#: texel, and 12-bit colours keep the neighbourhood's shadows from banding.
MESHOPT = ["@gltf-transform/cli@4.2.1", "meshopt"]
MESHOPT_OPTIONS = [
    "--level", "medium",
    "--quantize-position", "16", "--quantize-texcoord", "16", "--quantize-color", "12",
    # The light in vertices, per time of day (_LIGHT_*), square rooted.
    "--quantize-generic", "8",
]


def scene_files(manifest: dict[str, Any]) -> set[str]:
    """Every file under scene/ that the viewer may load for this manifest."""
    assets = manifest.get("assets") or {}
    names = {assets.get(k) for k in ("scene", "sceneNoFloor", "floor", "model", "lightmap")}
    names |= set((assets.get("floorShading") or {}).values())
    for light in assets.get("lighting") or []:
        names |= {light.get("lightmap"), light.get("sky")}
    if assets.get("viewPhoto"):
        names.add(assets["viewPhoto"]["model"])
    names.add((manifest.get("heroView") or {}).get("image"))
    return {n for n in names if n}


def catalog_files(items: list[dict[str, Any]]) -> set[str]:
    """Every /files/ or /catalog/ path a catalog entry points at."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str) and value.startswith(("/files/", "/catalog/")):
            found.add(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(items)
    return found


def site_head(page: str, name: str, preview: str | None) -> str:
    """The page with the static site's head tags: no indexing, and a link preview."""
    tags = [
        '<meta name="robots" content="noindex" />',
        f'<meta property="og:title" content="{html.escape(name)}" />',
        '<meta property="og:description" content="Walk through it in 3D, in the browser." />',
        '<meta property="og:type" content="website" />',
    ]
    if preview:
        tags += [
            f'<meta property="og:image" content="{SITE_URL}{preview}" />',
            '<meta name="twitter:card" content="summary_large_image" />',
        ]
    return page.replace("</head>", "  " + "\n    ".join(tags) + "\n  </head>", 1)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    elif src.suffix == ".glb":
        npx = shutil.which("npx")
        if not npx:
            raise SystemExit("npx is needed to compress the glTF files")
        subprocess.run([npx, "--yes", *MESHOPT, str(src), str(dst), *MESHOPT_OPTIONS],
                       check=True, capture_output=True)
    else:
        shutil.copy2(src, dst)


def main(project_ids: list[str]) -> None:
    if not (DIST / "index.html").is_file():
        raise SystemExit("Build the viewer first: pnpm --filter @walkthrough/web build:pages")
    settings = get_settings()
    data = settings.data_root

    listed = [p for p in projects.list_projects(settings) if p["id"] in project_ids]
    if len(listed) != len(project_ids):
        raise SystemExit(f"Unknown project in {project_ids}")
    write_json(DIST / "api" / "projects.json", listed)

    index = DIST / "index.html"
    preview = data / "projects" / project_ids[0] / "preview.jpg"
    if preview.is_file():
        copy(preview, DIST / "preview.jpg")
    page = index.read_text(encoding="utf-8")
    if 'name="robots"' not in page:
        page = site_head(page, listed[0]["name"], "preview.jpg" if preview.is_file() else None)
        index.write_text(page, encoding="utf-8")

    for pid in project_ids:
        manifest = projects.load_manifest(settings, pid).model_dump(mode="json")
        write_json(DIST / "api" / "projects" / pid / "manifest.json", manifest)
        for name in sorted(scene_files(manifest)):
            copy(data / "projects" / pid / "scene" / name,
                 DIST / "files" / "projects" / pid / "scene" / name)
        copy(index, DIST / "p" / pid / "index.html")

    # The API's default page size, so the lists match what the viewer gets there.
    cat = catalog_mod.load_catalog(settings.catalog_root)
    for kind in catalog_mod.KINDS:
        items = catalog_mod.search(cat, kind, limit=50)
        write_json(DIST / "api" / "catalog" / f"{kind}.json", items)
        for url in sorted(catalog_files(items)):
            if url.startswith("/catalog/"):
                src, dst = REPO / url.lstrip("/"), DIST / url.lstrip("/")
            else:
                key = url.removeprefix("/files/")
                src, dst = data / key, DIST / "files" / key
            # A glTF names its buffers and textures beside it: take the folder.
            if src.suffix == ".gltf":
                src, dst = src.parent, dst.parent
            if src.exists():
                copy(src, dst)

    copy(index, DIST / "404.html")
    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"Exported {', '.join(project_ids)} to {DIST} ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(sys.argv[1:] or ["flat-01"])
