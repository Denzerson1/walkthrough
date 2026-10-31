"""
What the windows look out on: the owner's photos out of them, stood in 3D
(view_photo.py), and the sky at each baked time of day (sky.py).

Runs after the bake, or on its own (`pipeline outside`): neither needs
Blender, so the view can be worked on without baking the flat again.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image
from scipy.ndimage import median_filter

from . import glb, rebuild, sky, view_photo
from .manifest import Manifest, ViewPhoto
from .paths import ProjectPaths, repo_root
from .photo_texture import srgb_to_linear

VIEW_FILE = "view_photo.glb"
#: Panorama pixels (of 2048 round) the photo's sky is cleaned over.
SKY_CLEAN = 15


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
        # Only the first photo is stood outside; the second is its stereo
        # partner and lends its sky. Stood outside too, even on the same
        # plane, it went on past the first's edge with the house across
        # again, a few metres along.
        glb.write_textured_meshes(paths.scene / VIEW_FILE, [views[0].mesh], "view-photo")
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
        # The photo's sky without what the glass put on it (a smear read as
        # a pole in the sky), drawn back from the roofs by as much, which
        # the filter dragged into it as a dark rim, and softly edged.
        colour = median_filter(colour, size=(SKY_CLEAN, SKY_CLEAN, 1))
        weight = cv2.erode(weight, np.ones((2 * SKY_CLEAN + 1,) * 2, np.uint8))
        photo = (colour, cv2.GaussianBlur(weight, (0, 0), SKY_CLEAN / 2))
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
