"""
What the windows look out on: the owner's photos out of them, stood in 3D
(view_photo.py), and the sky at each baked time of day (sky.py).

Runs after the bake, or on its own (`pipeline outside`): neither needs
Blender, so the view can be worked on without baking the flat again.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from . import glb, rebuild, sky, view_photo
from .manifest import Manifest, ViewPhoto
from .paths import ProjectPaths, repo_root
from .photo_texture import srgb_to_linear

VIEW_FILE = "view_photo.glb"


def build(paths: ProjectPaths, spec: dict, dressing: dict, times: list[str]) -> dict:
    """
    The view meshes (`VIEW_FILE`) and a sky panorama per time in `times`,
    written to the scene folder. Returns the manifest's part:
    {"viewPhoto": {"model", "time"} | None, "skies": {time: file}}.
    """
    entries = dressing.get("viewPhotos") or []
    centres = [np.array(sk["centre"], float) for sk in spec["skylights"]]
    facing = spec.get("facing") or {"dir": [0.0, 1.0], "bearing": None}
    outward = np.array([facing["dir"][0], 0.0, facing["dir"][1]])
    views: list[view_photo.View] = []
    if len(entries) >= 2:
        for i, entry in enumerate(entries):
            partner = entries[1 if i == 0 else 0]
            views.append(view_photo.build(
                repo_root() / entry["photo"], repo_root() / partner["photo"],
                centres[entry["window"]], centres[partner["window"]], outward,
                entry.get("keep")))
        glb.write_textured_meshes(paths.scene / VIEW_FILE, [v.mesh for v in views], "view-photo")
    when = view_photo.taken(repo_root() / entries[0]["photo"]) if views else None

    photo, photo_sun = None, None
    width, height = 2048, 1024
    if views and when:
        colour = np.zeros((height, width, 3))
        weight = np.zeros((height, width))
        for v in views:
            c, w = sky.project_photo(width, height, srgb_to_linear(v.texture / 255.0), v.sky,
                                     v.camera.right, v.camera.up, v.camera.forward, v.camera.f)
            better = w > weight
            colour[better], weight[better] = c[better], w[better]
        photo = (colour, weight)
        on_the_day = {**dressing, "sun": {**dressing["sun"], "date": when[0]}}
        photo_sun = np.array(rebuild.sun(on_the_day, tuple(facing["dir"]), facing["bearing"],
                                         when[1])["direction"])
    suns = {s["time"]: s for s in [spec.get("sun"), *(spec.get("suns") or [])] if s}
    skies, reference = {}, None
    for time in times:
        if time not in suns:
            continue
        image, bright = sky.panorama(np.array(suns[time]["direction"]), photo_sun, photo,
                                     width, height, reference=reference)
        reference = reference or bright
        name = f"sky-{time.replace(':', '')}.jpg"
        Image.fromarray(image).save(paths.scene / name, quality=88)
        skies[time] = name
    return {"viewPhoto": {"model": VIEW_FILE, "time": when[1]} if views and when else None,
            "skies": skies, "views": [v.info for v in views]}


def apply(manifest: Manifest, result: dict) -> None:
    """Put `build`'s result into the manifest's assets."""
    manifest.assets.viewPhoto = ViewPhoto(**result["viewPhoto"]) if result["viewPhoto"] else None
    for entry in manifest.assets.lighting:
        entry.sky = result["skies"].get(entry.time)
