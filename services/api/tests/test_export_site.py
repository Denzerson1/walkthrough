"""
The static site must carry what the viewer loads, and nothing else.

A project's scene/ also holds the owner's photos the rebuild worked from.
They are not shown anywhere, and on a public site they would be published
all the same, so the export copies only the files the manifest names.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from export_site import catalog_files, scene_files, site_head  # noqa: E402


def test_scene_files_are_what_the_manifest_names():
    manifest = {
        "assets": {
            "scene": None,
            "floorShading": {"room-1": "shade-1.png"},
            "model": "flat.glb",
            "lightmap": "flat_light-1730.jpg",
            "lighting": [
                {"time": "17:30", "lightmap": "flat_light-1730.jpg", "sky": "sky-1730.jpg"},
                {"time": "08:00", "lightmap": "flat_light-0800.jpg", "sky": None},
            ],
            "viewPhoto": {"model": "view_photo.glb", "time": "15:38"},
        },
        "heroView": {"position": [0, 1.6, 0], "yaw": 0, "pitch": 0, "fov": 60,
                     "image": "hero.webp"},
    }
    assert scene_files(manifest) == {
        "shade-1.png", "flat.glb", "flat_light-1730.jpg", "sky-1730.jpg",
        "flat_light-0800.jpg", "view_photo.glb", "hero.webp",
    }


def test_a_splat_manifest_names_its_splats():
    manifest = {"assets": {"scene": "scene.ply", "sceneNoFloor": "body.ply", "floor": "floor.ply"}}
    assert scene_files(manifest) == {"scene.ply", "body.ply", "floor.ply"}


def test_catalog_files_finds_every_served_path_and_nothing_else():
    items = [
        {"glb": "/files/assets/furniture/sofa/sofa.gltf", "thumbnail": None,
         "source": "https://polyhaven.com/a/sofa"},
        {"maps": {"albedo": "/files/assets/floors/ash/color.jpg"},
         "thumbnail": "/catalog/floors/thumbnails/ash.png", "tags": ["pale", "files"]},
    ]
    assert catalog_files(items) == {
        "/files/assets/furniture/sofa/sofa.gltf",
        "/files/assets/floors/ash/color.jpg",
        "/catalog/floors/thumbnails/ash.png",
    }


def test_site_head_keeps_the_page_out_of_search_and_escapes_the_name():
    page = "<html><head><title>x</title></head><body></body></html>"
    out = site_head(page, 'Flat "A" & B', "preview.jpg")
    assert '<meta name="robots" content="noindex" />' in out
    assert 'content="Flat &quot;A&quot; &amp; B"' in out
    assert 'content="https://denzerson1.github.io/walkthrough/preview.jpg"' in out
    assert out.index("og:image") < out.index("</head>")
    assert "og:image" not in site_head(page, "Flat", None)
