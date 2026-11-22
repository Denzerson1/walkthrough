"""
A flat's surroundings from its owner's photos of them, stood in 3D.

One photo out of a window, laid on the street front across (view_photo.py),
is the view from that window. A flat with a balcony is looked out of from
everywhere, in every direction; its owner photographs it by turning on the
spot, leaning over the rail, and walking round the house. `pipeline
surround` reconstructs those photos (structure from motion, multi-view
stereo, a single-image depth network pinned to what was measured), sets
the reconstruction in the flat's own coordinates by the balcony the photos
show and the flat's plan has, and stands each photo out there as a mesh at
its measured depth: `scene/view_photo.glb`, which the viewer shows as it
shows a view photo. Each direction is one photo's, so nothing is blended.

This module holds what the rest of the pipeline needs of it: the panoramas
the surround leaves for the skies (outside.py), in sky.py's convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .paths import ProjectPaths
from .photo_texture import linear_to_srgb, srgb_to_linear

VIEW_FILE = "view_photo.glb"
PANORAMAS = "surround_pano.npz"
#: Degrees above the horizon the ground fill reaches at most.
HORIZON_DEG = 2.0
#: Metres from where the photos were taken within which the viewer shows all
#: of them, and metres from there that count as near: from farther off (a
#: room at the flat's other end) what stands near was only seen side-on, and
#: is left to the ground fill.
REACH, NEAR_FIELD = 8.0, 30.0


@dataclass
class Panoramas:
    """What the photos show at infinity, equirectangular, linear RGB."""

    sky: np.ndarray  # (h, w, 3) the photos' own sky
    sky_weight: np.ndarray  # (h, w) how surely each spot of it is sky, 0..1
    ground: np.ndarray  # (h, w, 3) the houses and gardens, per direction from `centre`
    ground_known: np.ndarray  # (h, w) where a photo shows any
    centre: np.ndarray  # scene point the directions are seen from


def folder(paths: ProjectPaths):
    return paths.work / "surround"


def load(paths: ProjectPaths) -> Panoramas | None:
    """The surround's panoramas, if `pipeline surround` has built it."""
    file = folder(paths) / PANORAMAS
    if not file.is_file() or not (paths.scene / VIEW_FILE).is_file():
        return None
    data = np.load(file)
    return Panoramas(data["sky"].astype(np.float32), data["sky_weight"].astype(np.float32),
                     data["ground"].astype(np.float32), data["ground_known"],
                     data["centre"])


def sky_photo(panoramas: Panoramas, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """The photos' sky and its weight at a panorama's size (sky.panorama's `photo`)."""
    colour = cv2.resize(panoramas.sky, (width, height), interpolation=cv2.INTER_AREA)
    weight = cv2.resize(panoramas.sky_weight, (width, height), interpolation=cv2.INTER_AREA)
    return colour.astype(np.float64), weight.astype(np.float64)


def ground_fill(panoramas: Panoramas, width: int, height: int, soft_deg: float = 1.5
                ) -> tuple[np.ndarray, np.ndarray]:
    """
    What stands round the flat as a soft panorama (linear RGB) and where it
    covers (0..1). Standing where the photos were taken, their meshes hide
    it; from across the flat a mesh no longer covers all it did, and what
    shows in the gap is this, the right colours in about the right
    direction, rather than a strip of sky under the houses. Soft, as a
    sharp copy would read as the houses seen twice; and only below the
    skyline, taken low (the lowest roof within two degrees either side):
    above it the gaps are sky, and a fill there hung over the roofs as fog.
    """
    known = cv2.morphologyEx(panoramas.ground_known.astype(np.uint8), cv2.MORPH_CLOSE,
                             np.ones((9, 9), np.uint8))
    rows, cols = known.shape
    any_known = known.any(axis=0)
    top = np.where(any_known, known.argmax(axis=0), rows).astype(np.float32)
    reach = max(1, round(2.0 * cols / 360))
    low = cv2.dilate(top[None, :], np.ones((1, 2 * reach + 1), np.uint8))[0]  # the lowest nearby
    # Where no photo looks at all, there is nothing to fill with.
    have = cv2.erode(any_known.astype(np.uint8)[None, :], np.ones((1, 2 * reach + 1), np.uint8))[0]
    below = (np.arange(rows)[:, None] >= low[None, :] + 0.5 * rows / 180) & (have[None, :] > 0)
    # And not above eye level: what stands taller than that is a tree or a
    # house, drawn by its mesh or not at all.
    below &= np.arange(rows)[:, None] >= rows * (90 - HORIZON_DEG) / 180
    srgb = (linear_to_srgb(np.clip(panoramas.ground, 0, 1)) * 255).astype(np.uint8)
    filled = cv2.inpaint(srgb, (below & (panoramas.ground_known == 0)).astype(np.uint8), 4,
                         cv2.INPAINT_TELEA)
    filled = cv2.resize(filled, (width, height), interpolation=cv2.INTER_AREA)
    cover = cv2.resize(below.astype(np.float32), (width, height), interpolation=cv2.INTER_AREA)
    sigma = soft_deg * width / 360
    # Blurred within the fill only: the black above the skyline would darken its edge.
    inside = (cover > 0.5).astype(np.float32)
    linear = srgb_to_linear(filled.astype(np.float32) / 255) * inside[..., None]
    colour = cv2.GaussianBlur(linear, (0, 0), sigma) / np.maximum(
        cv2.GaussianBlur(inside, (0, 0), sigma), 1e-3)[..., None]
    cover = cv2.GaussianBlur(cover, (0, 0), sigma / 4)
    return colour, cover


def over_sky(sky_srgb: np.ndarray, ground: np.ndarray, cover: np.ndarray,
             tint: np.ndarray) -> np.ndarray:
    """A time's sky (uint8 sRGB) with the ground fill laid over it, in that time's light."""
    sky = srgb_to_linear(sky_srgb.astype(np.float32) / 255)
    out = sky * (1 - cover[..., None]) + ground * np.asarray(tint, np.float32) * cover[..., None]
    return (linear_to_srgb(np.clip(out, 0, 1)) * 255).astype(np.uint8)


def daylight_tints(skies: dict[str, np.ndarray], taken: str) -> dict[str, list[float]]:
    """
    Per time of day, what multiplies the photos (taken in one light) to
    stand in that time's: the sky's brightness and colour then over the
    sky's at the time nearest when they were taken. The skies are exposed
    as a camera would (sky.panorama), so the view dims with them and no
    further. Brightness in full, the colour's shift by a third: a low sun
    reddens the sky more than it does a lawn.
    """
    def minutes(time: str) -> int:
        hours, mins = time.split(":")
        return int(hours) * 60 + int(mins)

    def mean(image: np.ndarray) -> np.ndarray:
        h = image.shape[0]
        band = image[int(h * 0.28): int(h * 0.47)]  # 5 to 40 degrees up
        return srgb_to_linear(band.astype(np.float32) / 255).reshape(-1, 3).mean(axis=0)

    means = {time: mean(image) for time, image in skies.items()}
    reference = means[min(means, key=lambda t: abs(minutes(t) - minutes(taken)))]
    luma = np.array([0.2126, 0.7152, 0.0722])
    tints = {}
    for time, value in means.items():
        ratio = value / np.maximum(reference, 1e-6)
        level = float(ratio @ luma)
        colour = 0.7 + 0.3 * ratio / max(level, 1e-6)
        # After sunset the land is lit by the whole sky, which is blue, not
        # by the glow left on the horizon.
        dusk = float(np.clip((0.35 - level) / 0.3, 0, 1))
        colour = colour * (1 - dusk) + np.array([0.8, 0.92, 1.12]) * dusk
        tint = np.clip(level, 0.03, 1.25) * colour
        tints[time] = [round(float(v), 4) for v in tint]
    return tints


# --------------------------------------------------------------------------
# `pipeline surround`: from the photos to the meshes and the panoramas
# --------------------------------------------------------------------------


def _taken(photo: Path) -> tuple[int, int, int, float] | None:
    """Year, month, day and hour of day (with its minutes and seconds) a photo was taken (EXIF)."""
    from PIL import Image

    stamp = Image.open(photo).getexif().get_ifd(0x8769).get(36867)  # DateTimeOriginal
    if not stamp:
        return None
    day, clock = str(stamp).split(" ")
    year, month, date = (int(v) for v in day.split(":"))
    hours, minutes, seconds = (int(v) for v in clock.split(":"))
    return year, month, date, hours + minutes / 60 + seconds / 3600


def build(paths: ProjectPaths, dressing: dict, pycolmap, say=print, fresh: bool = False) -> dict:
    """
    The surround of the dressing's `surround`, written as `scene/view_photo.glb`
    and `work/surround/` (the reconstruction, the panoramas for the skies, a
    report, and `overlay.jpg`: the plan's balcony drawn on the photos, to
    check by eye that the reconstruction sits on the flat). Steps whose
    results are there are not run again, unless `fresh`. Returns the report.

    The dressing's `surround`:
        photos    folder of the owner's photos, under the project
        taken     "HH:MM" they were taken (the light they show)
        balcony   {photos: [stems taken on it], railZ, wallX, wallTowards,
                   railHeight}: the plan's balcony, which sets the scale
        use       {photo stem: how gladly it is used, 0..1}
        veil      {photo stem: share of the sun's veil taken off it}
        centre    [stems]: the photos compete as seen from these' middle
        own, lean, air, lay: see surround_mesh.Layout
    """
    import json
    import shutil

    from . import flat_geometry, glb, rebuild, sky
    from . import surround_mesh as mesh
    from . import surround_recon as recon
    from . import surround_register as register

    spec = dressing["surround"]
    work = folder(paths)
    if fresh:
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    originals = {p.stem: p for p in sorted((paths.root / spec["photos"]).iterdir())
                 if p.suffix.lower() in (".jpg", ".jpeg")}
    frame_dir, dense = work / "frames", work / "dense"
    recon.frames(list(originals.values()), frame_dir)
    if not (work / "sparse" / "images.bin").is_file():
        say(f"Structure from motion over {len(originals)} photos (about an hour) ...")
        recon.structure(pycolmap, frame_dir, work)
    depth_maps = dense / "stereo" / "depth_maps"
    if not depth_maps.is_dir() or not any(depth_maps.glob("*.geometric.bin")):
        say("Multi-view stereo ...")
        recon.stereo(pycolmap, work / "sparse", frame_dir, dense)
        # Only the geometric depth is read again: the rest is gigabytes.
        for spare in depth_maps.glob("*.photometric.bin"):
            spare.unlink()
        shutil.rmtree(dense / "stereo" / "normal_maps", ignore_errors=True)
    views = recon.views(pycolmap, dense)
    if not all((dense / "mono" / (Path(name).stem + ".npy")).is_file() for name in views):
        say("The depth network ...")
        recon.network_depth(dense)
    images = {name: cv2.imread(str(dense / "images" / name)) for name in views}
    mono = {name: np.load(dense / "mono" / (Path(name).stem + ".npy")) for name in views}

    # Up, north, and the flat's own coordinates.
    names = sorted(views)
    up, spread = register.up_direction([views[n] for n in names], [images[n] for n in names])
    place = dressing["location"]
    suns = []
    for name in names:
        at, when = register.sun_in_frame(images[name]), _taken(originals[Path(name).stem])
        if at is None or when is None:
            continue
        view = views[name]
        ray = np.linalg.inv(view.k) @ np.array([at[0], at[1], 1.0])
        bearing, elevation = flat_geometry.sun_position(*when, place["utcOffset"],
                                                        place["latitude"], place["longitude"])
        suns.append((view.rotation.T @ (ray / np.linalg.norm(ray)), bearing, elevation))
    north, said = register.north_direction(up, suns)
    enu = np.array([np.cross(north, up), north, up])
    plan = spec["balcony"]
    on_balcony = [f"{stem}.jpg" for stem in plan["photos"] if f"{stem}.jpg" in views]
    stereo = {n: recon.measured(dense, views[n]) for n in on_balcony}
    measured = np.concatenate([views[n].world(stereo[n])[stereo[n] > 0] for n in on_balcony])
    outline = dressing["outdoors"][0]
    height = outline["height"]
    fit = register.fit_balcony(
        measured, np.array([views[n].centre for n in on_balcony]), enu,
        register.Balcony(plan["railZ"], plan["wallX"], plan["wallTowards"], height,
                         plan["railHeight"]), dressing["windowsFace"])
    say(f"  the balcony faces {fit.facing:.1f} degrees; {fit.scale:.3f} m to the model's unit; "
        f"up agreed to {spread:.1f} degrees, north from {sum(s['used'] for s in said)} suns")
    if abs((fit.facing - dressing["windowsFace"] + 180) % 360 - 180) > 0.3:
        say(f"  [the dressing's windowsFace is {dressing['windowsFace']}: set it to "
            f"{fit.facing:.1f} and bake again for the sun to stand where it did]")
    xs, zs = [p[0] for p in outline["polygon"]], [p[1] for p in outline["polygon"]]
    corners = [(x, y, z) for x in (min(xs), max(xs)) for y in (0.0, height)
               for z in (min(zs), max(zs))]
    edges = [(a, b) for i, a in enumerate(corners) for b in corners[i + 1:]
             if sum(u != v for u, v in zip(a, b, strict=True)) == 1]
    edges.append(((min(xs), plan["railHeight"], plan["railZ"]),
                  (max(xs), plan["railHeight"], plan["railZ"])))
    tiles = [cv2.resize(register.overlay(images[n], views[n], fit, edges), (450, 600))
             for n in on_balcony[:8]]
    while len(tiles) % 4:
        tiles.append(np.zeros_like(tiles[0]))
    cv2.imwrite(str(work / "overlay.jpg"),
                np.vstack([np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)]))

    # A depth for every pixel, then the photos stood in the scene.
    everything = recon.cloud(dense, views)
    used = [f"{stem}.jpg" for stem in spec["use"] if f"{stem}.jpg" in views]
    on_the_day = {**dressing, "windowsFace": fit.facing}
    sun = np.array(rebuild.sun(on_the_day, tuple(dressing["windowsDir"]), None,
                               spec["taken"])["direction"])
    layout = mesh.Layout(
        own=[tuple(b) for b in spec["own"]], lean=list(spec.get("lean", [])),
        air=[tuple(b) for b in spec["air"]], sun=sun,
        quality={f"{stem}.jpg": q for stem, q in spec["use"].items()},
        veil={f"{stem}.jpg": v for stem, v in spec.get("veil", {}).items()},
        centre=[f"{stem}.jpg" for stem in spec.get("centre", [])],
        lay=tuple(spec["lay"]) if spec.get("lay") else None)
    to_model = np.linalg.inv(fit.matrix)

    def photo(name: str, depth: np.ndarray) -> mesh.Photo:
        return mesh.Photo(views[name], images[name], mono[name], depth, fit.matrix, fit.origin,
                          layout)

    def redo(p: mesh.Photo, cloud: np.ndarray) -> mesh.Photo | None:
        depth = recon.complete(p.view, mono[p.name], None, (cloud - fit.origin) @ to_model.T)
        return photo(p.name, depth) if depth is not None else None

    photos = []
    for name in used:
        depth = recon.complete(views[name], mono[name], recon.measured(dense, views[name]),
                               everything)
        if depth is not None:
            photos.append(photo(name, depth))
    camera = next(iter(pycolmap.Reconstruction(str(work / "sparse")).cameras.values()))
    built = mesh.build(photos, layout, {n: frame_dir / n for n in views},
                       tuple(float(v) for v in camera.params), redo)
    paths.scene.mkdir(parents=True, exist_ok=True)
    glb.write_textured_meshes(paths.scene / VIEW_FILE, built.meshes, "view-photo")
    sky_colour, sky_weight = mesh.sky_panorama(built.photos, sun, sky.directions(2048, 1024))
    np.savez_compressed(work / PANORAMAS, sky=sky_colour.astype(np.float16),
                        sky_weight=sky_weight.astype(np.float16),
                        ground=built.ground.astype(np.float16), ground_known=built.ground_known,
                        centre=built.centre)
    report = {
        "photos": len(originals), "registered": len(views), "used": len(built.meshes),
        "upSpreadDeg": round(spread, 2), "suns": said, "facing": round(fit.facing, 2),
        "scale": round(fit.scale, 4), "balconyFit": fit.found,
        "centre": [round(float(v), 3) for v in built.centre],
        "modelKB": (paths.scene / VIEW_FILE).stat().st_size // 1024, **built.report}
    (work / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report
