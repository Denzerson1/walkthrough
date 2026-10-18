"""
Runs inside Blender: builds the flat from a spec written by
walkthrough_pipeline.rebuild, bakes its light and exports it.

    blender -b --factory-startup -P build_flat.py -- spec.json [--preview-only]

Scene space is ours (metres, Y up); Blender is Z up, so a scene point
(x, y, z) is Blender (x, -z, y) — the same mapping the glTF exporter undoes.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "walkthrough_pipeline"))
from flat_geometry import (  # noqa: E402  (standard library only, so importable here)
    WALL_T,
    atlas_cells,
    grow,
    is_air,
    opening_spans,
    wall_cells,
    wall_frame,
)

SLAB_T = 0.2  # floor and ceiling slab thickness, so no light leaks at edges
SKIRTING_H, SKIRTING_D = 0.08, 0.012
TRIM_W, TRIM_D = 0.07, 0.015  # door architrave
FRAME_W, FRAME_D = 0.05, 0.07  # window frame
SUN_ELEVATION = math.radians(35)
SUN_SIDEWAYS = math.radians(32)  # so the sun patch falls across the floor, not straight in


def P(x: float, y: float, z: float) -> Vector:
    return Vector((x, -z, y))


def hex_rgba(colour: str) -> tuple[float, float, float, float]:
    c = colour.lstrip("#")
    srgb = [int(c[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in srgb]
    return (*lin, 1.0)


# --------------------------------------------------------------------------
# Materials
# --------------------------------------------------------------------------


def material(name: str, colour: str, roughness: float) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = hex_rgba(colour)
    bsdf.inputs["Roughness"].default_value = roughness
    return mat


def floor_material(spec: dict) -> bpy.types.Material:
    mat = material("floor", spec["colour"], 0.6)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    if spec.get("albedo"):
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(spec["albedo"])
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    if spec.get("roughness"):
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(spec["roughness"])
        tex.image.colorspace_settings.name = "Non-Color"
        links.new(tex.outputs["Color"], bsdf.inputs["Roughness"])
    if spec.get("normal"):
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(spec["normal"])
        tex.image.colorspace_settings.name = "Non-Color"
        normal = nodes.new("ShaderNodeNormalMap")
        links.new(tex.outputs["Color"], normal.inputs["Color"])
        links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def glass_material() -> bpy.types.Material:
    """
    Clear glass as a mostly transparent surface rather than a refractive one:
    Cycles treats sun through refractive glass as a caustic and drops it, which
    would leave the room without its sun patch. Transparent surfaces pass
    shadow rays, and the same alpha exports to glTF as blended glass.
    """
    mat = bpy.data.materials.new("glass")
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.9, 0.95, 0.95, 1)
    bsdf.inputs["Roughness"].default_value = 0.0
    bsdf.inputs["Alpha"].default_value = 0.08
    mat.surface_render_method = "BLENDED"
    return mat


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


class Builder:
    """Accumulates boxes and slabs into one bmesh per material."""

    def __init__(self) -> None:
        self.meshes: dict[str, bmesh.types.BMesh] = {}

    def bm(self, key: str) -> bmesh.types.BMesh:
        return self.meshes.setdefault(key, bmesh.new())

    def box(self, key: str, corners: list[Vector]) -> None:
        """8 corners: bottom quad then top quad, both counter-clockwise from above."""
        bm = self.bm(key)
        v = [bm.verts.new(c) for c in corners]
        for face in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6),
                     (3, 0, 4, 7)):
            bm.faces.new([v[i] for i in face])

    def slab(self, key: str, polygon: list[list[float]], y0: float, y1: float) -> None:
        """A polygon extruded from y0 to y1. Face winding is fixed up in `finish`."""
        bm = self.bm(key)
        bottom = [bm.verts.new(P(x, y0, z)) for x, z in polygon]
        top = [bm.verts.new(P(x, y1, z)) for x, z in polygon]
        bm.faces.new(top)
        bm.faces.new(list(reversed(bottom)))
        n = len(polygon)
        for i in range(n):
            j = (i + 1) % n
            bm.faces.new([bottom[i], top[i], top[j], bottom[j]])

    def finish(self, materials: dict[str, bpy.types.Material]) -> list[bpy.types.Object]:
        objects = []
        for key, bm in self.meshes.items():
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            mesh = bpy.data.meshes.new(key)
            bm.to_mesh(mesh)
            bm.free()
            mesh.materials.append(materials.get(key) or materials[key.split("-")[0]])
            obj = bpy.data.objects.new(key, mesh)
            bpy.context.collection.objects.link(obj)
            objects.append(obj)
        return objects


def wall_box(b: Builder, key: str, wall: dict, u0: float, u1: float, y0: float, y1: float,
             v0: float, v1: float) -> None:
    """A box in the wall's frame: u along it, v outward, y up. Empty ones are skipped."""
    if u1 - u0 < 1e-4 or y1 - y0 < 1e-4:
        return
    (dx, dz), (nx, nz), _ = wall_frame(wall["start"], wall["end"])
    ax, az = wall["start"]

    def at(u: float, v: float, y: float) -> Vector:
        return P(ax + dx * u + nx * v, y, az + dz * u + nz * v)

    corners = [at(u0, v0, y0), at(u1, v0, y0), at(u1, v1, y0), at(u0, v1, y0)]
    corners += [at(u0, v0, y1), at(u1, v0, y1), at(u1, v1, y1), at(u0, v1, y1)]
    b.box(key, corners)


def build_room(b: Builder, room: dict) -> list[dict]:
    """Walls with openings, slabs, skirting, frames. Returns the windows, for the sun."""
    h = room["height"]
    # The slabs reach a hair under the walls: where two surfaces only meet at
    # an edge, the renderer shows cracks of background between them. Only a
    # hair, under a light texel: the part inside the wall sees the sky, and a
    # wider strip bled into the visible ceiling as a bright line.
    slab = grow(room["polygon"], 0.002)
    # One floor per room, so the viewer can swap each room's floor on its own.
    b.slab(f"floor-{room['id']}", slab, -SLAB_T, 0.0)
    b.slab("ceiling", slab, h, h + SLAB_T)
    windows = []
    for wall in room["walls"]:
        _, _, length = wall_frame(wall["start"], wall["end"])
        openings = sorted(wall["openings"], key=lambda o: o["offset"])
        spans = opening_spans(wall, h)
        for o, (lo, hi, bottom, top) in zip(openings, spans, strict=True):
            if o["type"] == "door":
                door(b, wall, lo, hi, top)
            else:
                window(b, wall, lo, hi, bottom, top)
                (dx, dz), _, _ = wall_frame(wall["start"], wall["end"])
                mid = (lo + hi) / 2
                windows.append({
                    "wall": wall,
                    "area": (hi - lo) * (top - bottom),
                    "centre": (wall["start"][0] + dx * mid, (bottom + top) / 2,
                               wall["start"][1] + dz * mid),
                })
        # Floor to ceiling exactly; the slabs reach under the walls to seal the
        # edges. A wall running past the ceiling had a strip open to the sky
        # whose bright bake bled into the visible wall as a white band.
        for u0, u1, y0, y1 in wall_cells(length, h, spans):
            wall_box(b, "wall", wall, u0, u1, y0, y1, 0, WALL_T)
        skirting(b, wall, [o for o in openings if o["type"] == "door"], length)
    return windows


def door(b: Builder, wall: dict, lo: float, hi: float, top: float) -> None:
    mid = WALL_T / 2
    wall_box(b, "trim", wall, lo + 0.005, hi - 0.005, 0, top - 0.005, mid - 0.02, mid + 0.02)
    # Sides stop where the head starts: overlapping coplanar boxes render black.
    wall_box(b, "trim", wall, lo - TRIM_W, lo, 0, top, -TRIM_D, 0)
    wall_box(b, "trim", wall, hi, hi + TRIM_W, 0, top, -TRIM_D, 0)
    wall_box(b, "trim", wall, lo - TRIM_W, hi + TRIM_W, top, top + TRIM_W, -TRIM_D, 0)


def window(b: Builder, wall: dict, lo: float, hi: float, bottom: float, top: float) -> None:
    v0, v1 = WALL_T / 2 - FRAME_D / 2, WALL_T / 2 + FRAME_D / 2
    wall_box(b, "trim", wall, lo, lo + FRAME_W, bottom, top, v0, v1)
    wall_box(b, "trim", wall, hi - FRAME_W, hi, bottom, top, v0, v1)
    wall_box(b, "trim", wall, lo, hi, bottom, bottom + FRAME_W, v0, v1)
    wall_box(b, "trim", wall, lo, hi, top - FRAME_W, top, v0, v1)
    wall_box(b, "glass", wall, lo + FRAME_W, hi - FRAME_W, bottom + FRAME_W, top - FRAME_W,
             WALL_T / 2 - 0.004, WALL_T / 2 + 0.004)
    # An interior sill board, the detail that makes a window read as built.
    if bottom > 0.1:
        wall_box(b, "trim", wall, lo - 0.03, hi + 0.03, bottom - 0.03, bottom, -0.04, WALL_T / 2)


def skirting(b: Builder, wall: dict, doors: list[dict], length: float) -> None:
    u = 0.0
    for o in doors:
        lo = max(0.0, o["offset"] - o["width"] / 2 - TRIM_W)
        if lo > u:
            wall_box(b, "trim", wall, u, lo, 0, SKIRTING_H, -SKIRTING_D, 0)
        u = o["offset"] + o["width"] / 2 + TRIM_W
    if u < length:
        wall_box(b, "trim", wall, u, length, 0, SKIRTING_H, -SKIRTING_D, 0)


def cull_unseen(obj: bpy.types.Object, rooms: list[dict]) -> None:
    """
    Drop faces that face no air: outer wall faces, slab undersides, caps
    buried in other boxes. Nobody can see them from inside, and baking them
    spent most of the light atlas on surfaces no camera reaches. A single
    face still blocks light from either side, so the room stays sealed.
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    hidden = []
    for face in bm.faces:
        c, n = face.calc_center_median(), face.normal
        probe = c + n * 0.01  # Blender (x, -z, y) back to scene below
        if not is_air(probe.x, probe.z, -probe.y, rooms):
            hidden.append(face)
    bmesh.ops.delete(bm, geom=hidden, context="FACES")
    # Weld what is left: a wall's cells then form one connected surface, so
    # the lightmap unwraps it as one island instead of a seam at every cell.
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    bm.to_mesh(obj.data)
    bm.free()


def floor_uvs(obj: bpy.types.Object, tile: list[float]) -> None:
    """Texture coordinates in metres, so the floor's real tile size holds."""
    mesh = obj.data
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        co = mesh.vertices[loop.vertex_index].co  # Blender (x, -z, y)
        uv.data[loop.index].uv = (co.x / tile[0], -co.y / tile[1])


def plain_uvs(obj: bpy.types.Object) -> None:
    """Walls and trim are flat colours; they still need a first UV set so the
    lightmap lands on the second, as the viewer expects."""
    obj.data.uv_layers.new(name="UVMap")


# --------------------------------------------------------------------------
# Furniture
# --------------------------------------------------------------------------


BLOCK_COLOURS = {"bed": "#e9e4dc", "sofa": "#8d9390", "tv": "#1b1c1e", "appliance": "#e8e8e6"}


def furniture(item: dict) -> bpy.types.Object:
    """The piece's model (or a block), fitted to its scanned box and turned to face `front`."""
    if item["model"]:
        before = {o.name for o in bpy.data.objects}
        bpy.ops.import_scene.gltf(filepath=item["model"])
        new = [o.name for o in bpy.data.objects if o.name not in before]
        meshes = [bpy.data.objects[n] for n in new if bpy.data.objects[n].type == "MESH"]
        bpy.ops.object.select_all(action="DESELECT")
        for o in meshes:
            o.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        if len(meshes) > 1:
            bpy.ops.object.join()
        obj = bpy.context.view_layer.objects.active
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        # Joining deleted the other meshes; what is left besides `obj` are
        # the importer's empties.
        for name in new:
            leftover = bpy.data.objects.get(name)
            if leftover is not None and leftover is not obj:
                bpy.data.objects.remove(leftover, do_unlink=True)
    else:
        mesh = bpy.data.meshes.new(item["id"])
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(item["id"], mesh)
        bpy.context.collection.objects.link(obj)
        obj.data.materials.append(
            material(f"block-{item['label']}", BLOCK_COLOURS.get(item["label"], "#d9d6d0"), 0.8)
        )
        bevel = obj.modifiers.new("soft", "BEVEL")
        bevel.width, bevel.segments = 0.03, 3
        plain_uvs(obj)
    obj.name = item["id"]

    # Normalise: footprint centred on the origin, standing on z = 0.
    xs = [v.co.x for v in obj.data.vertices]
    ys = [v.co.y for v in obj.data.vertices]
    zs = [v.co.z for v in obj.data.vertices]
    centre = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)))
    extent = Vector((max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)))
    w, h, d = item["size"]
    scale = Matrix.Diagonal((w / max(extent.x, 1e-6), d / max(extent.y, 1e-6),
                             h / max(extent.z, 1e-6), 1.0))
    obj.data.transform(scale @ Matrix.Translation(-centre))

    # A model's front is glTF +Z, which is Blender -Y; turn it to `front`.
    fx, fz = item["front"]
    cx, cy, cz = item["center"]
    obj.matrix_world = Matrix.Translation(P(cx, cy - h / 2, cz)) @ Matrix.Rotation(
        math.atan2(fx, fz), 4, "Z"
    )
    return obj


# --------------------------------------------------------------------------
# Light
# --------------------------------------------------------------------------


def light(windows: list[dict], rooms: list[dict]) -> tuple[float, float] | None:
    """Sky, sun and fallback lamps. Returns where the sun patch lands (x, z), if any."""
    world = bpy.context.scene.world or bpy.data.worlds.new("world")
    bpy.context.scene.world = world
    nodes, links = world.node_tree.nodes, world.node_tree.links
    sky = nodes.new("ShaderNodeTexSky")
    for kind in ("MULTIPLE_SCATTERING", "NISHITA"):
        try:
            sky.sky_type = kind
            break
        except TypeError:
            continue
    sky.sun_disc = False
    sky.sun_elevation = SUN_ELEVATION
    # A clear sky is deep blue; through a window it tints white walls cold.
    # Interior photographs are white-balanced to the room, so tone it down.
    tame = nodes.new("ShaderNodeHueSaturation")
    tame.inputs["Saturation"].default_value = 0.35
    links.new(sky.outputs["Color"], tame.inputs["Color"])
    links.new(tame.outputs["Color"], nodes["Background"].inputs["Color"])
    nodes["Background"].inputs["Strength"].default_value = 0.6

    if windows:
        # Through the largest window, a little sideways: a sun patch across
        # the floor is what makes an interior read as a photograph.
        wall = max(windows, key=lambda w: w["area"])["wall"]
        _, (nx, nz), _ = wall_frame(wall["start"], wall["end"])
        c, s = math.cos(SUN_SIDEWAYS), math.sin(SUN_SIDEWAYS)
        ix, iz = -(nx * c - nz * s), -(nx * s + nz * c)  # travelling into the room
        travel = P(ix * math.cos(SUN_ELEVATION), -math.sin(SUN_ELEVATION),
                   iz * math.cos(SUN_ELEVATION))
        sun = bpy.data.lights.new("sun", "SUN")
        sun.energy, sun.angle = 4.5, math.radians(1.2)
        sun.color = (1.0, 0.87, 0.72)
        obj = bpy.data.objects.new("sun", sun)
        bpy.context.collection.objects.link(obj)
        obj.rotation_euler = travel.to_track_quat("-Z", "Y").to_euler()
        sky.sun_rotation = math.atan2(-travel.x, -travel.y)
        main = max(windows, key=lambda w: w["area"])
        wx, wy, wz = main["centre"]
        reach = wy / math.tan(SUN_ELEVATION)
        return wx + ix * reach, wz + iz * reach
    else:
        # No window: warm ceiling light, as the room would be lit in the evening.
        for room in rooms:
            xs = [p[0] for p in room["polygon"]]
            zs = [p[1] for p in room["polygon"]]
            area = bpy.data.lights.new(f"ceiling-{room['id']}", "AREA")
            area.shape, area.size = "DISK", 0.4
            area.energy = 60 * (max(xs) - min(xs)) * (max(zs) - min(zs))
            area.color = (1.0, 0.86, 0.7)
            obj = bpy.data.objects.new(area.name, area)
            bpy.context.collection.objects.link(obj)
            obj.location = P((min(xs) + max(xs)) / 2, room["height"] - 0.05,
                             (min(zs) + max(zs)) / 2)
    return None


def setup_cycles(samples: int) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "CUDA"
    prefs.get_devices()
    for device in prefs.devices:
        device.use = device.type == "CUDA"
    scene.cycles.device = "GPU"
    scene.cycles.samples = samples
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.exposure = 1.0  # interiors are exposed for the room, not the window


# --------------------------------------------------------------------------
# Preview, bake, export
# --------------------------------------------------------------------------


def hero_view(room: dict, windows: list[dict],
              patch: tuple[float, float] | None) -> tuple[Vector, Vector]:
    """
    Where an interior photographer would stand: across the light, not into
    it. From the corner furthest from the sun patch, looking between the
    main window and the patch, so the window, the light on the floor and
    the room all show. Without a window, from the waypoint along its yaw.
    """
    if not windows or patch is None:
        x, _, z = room["waypoint"]["position"]
        yaw = math.radians(room["waypoint"]["yaw"])
        return P(x, 1.45, z), P(-math.sin(yaw), -0.08, -math.cos(yaw))
    wx, wy, wz = max(windows, key=lambda w: w["area"])["centre"]
    target = Vector(((wx + patch[0]) / 2, wy / 2, (wz + patch[1]) / 2))
    corner = max(room["polygon"], key=lambda p: math.hypot(p[0] - target.x, p[1] - target.z))
    cx = sum(p[0] for p in room["polygon"]) / len(room["polygon"])
    cz = sum(p[1] for p in room["polygon"]) / len(room["polygon"])
    # A step in from the corner, towards the middle of the room.
    eye = Vector((corner[0] + (cx - corner[0]) * 0.15, 1.45, corner[1] + (cz - corner[1]) * 0.15))
    look = (target - eye).normalized()
    return P(eye.x, eye.y, eye.z), P(look.x, look.y, look.z)


def preview(spec: dict, windows: list[dict], patch: tuple[float, float] | None,
            path: str) -> None:
    position, look = hero_view(spec["rooms"][0], windows, patch)
    cam = bpy.data.cameras.new("preview")
    cam.lens_unit, cam.angle = "FOV", math.radians(80)
    obj = bpy.data.objects.new("preview", cam)
    bpy.context.collection.objects.link(obj)
    obj.location = position
    obj.rotation_euler = look.to_track_quat("-Z", "Y").to_euler()
    scene = bpy.context.scene
    scene.camera = obj
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720
    scene.cycles.use_denoising = True
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def layout_lightmap(objects: list[bpy.types.Object], margin: float = 0.006) -> None:
    """
    A second UV set laid out so no two objects share atlas space.

    Each object is unwrapped on its own into the unit square, then shrunk
    into its own square cell, sized by the root of its surface area so
    texel density stays roughly even. Unwrapping several objects at once
    and packing them together left their islands on top of each other, and
    each object's bake overwrote the last — walls came out near black.
    """
    for obj in objects:
        layer = obj.data.uv_layers.new(name="lightmap")
        obj.data.uv_layers.active = layer
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")

    areas = [sum(p.area for p in o.data.polygons) for o in objects]
    corners, sides = atlas_cells(areas, margin)
    for obj, side, (cx, cy) in zip(objects, sides, corners, strict=True):
        for loop in obj.data.uv_layers["lightmap"].data:
            loop.uv = (cx + loop.uv[0] * side, cy + loop.uv[1] * side)


def smooth_within_islands(rgb, covered, sigma: float = 1.6):
    """
    Bake noise, blurred away without bleeding between surfaces: a Gaussian
    over covered texels only, renormalised by how much of the kernel was
    covered. Light is smooth at texel scale, so this costs nothing visible;
    islands sit wider apart than the kernel, so neighbours never mix.
    """
    import numpy as np

    radius = int(3 * sigma)
    x = np.arange(-radius, radius + 1)
    kernel = np.exp(-(x**2) / (2 * sigma**2))
    kernel /= kernel.sum()

    def blur(a):
        for axis in (0, 1):
            a = sum(np.roll(a, k, axis=axis) * w for k, w in zip(x, kernel, strict=True))
        return a

    weight = covered.astype(np.float32)
    total = blur(rgb * weight[..., None])
    norm = blur(weight)[..., None]
    return np.where(covered[..., None], total / np.maximum(norm, 1e-6), 0.0).astype(np.float32)


def save_lightmap(baked: bpy.types.Image, path: str) -> float:
    """
    The float bake as an 8-bit sRGB JPEG plus the scale that undoes it.

    A float EXR of a 2048 atlas is ~50 MB; light is smooth, so 8 bits in a
    perceptual curve, normalised to the brightest 0.1%, lose nothing visible
    and come to a megabyte or two. The brightest sun spots clip, which is
    what a photograph of them does too.
    """
    import numpy as np

    size = baked.size[0]
    rgba = np.empty(size * size * 4, dtype=np.float32)
    baked.pixels.foreach_get(rgba)
    rgba = rgba.reshape(size, size, 4)
    rgb = smooth_within_islands(rgba[..., :3], rgba[..., 3] > 0).reshape(-1, 3)
    scale = float(max(np.percentile(rgb.max(axis=1), 99.9), 1e-6))
    out = bpy.data.images.new("lightmap-8bit", size, size)
    out.colorspace_settings.name = "sRGB"
    linear = np.clip(rgb / scale, 0.0, 1.0)
    encoded = np.ones((size * size, 4), dtype=np.float32)
    # An 8-bit image's pixels are stored already encoded, so apply the sRGB
    # curve here: handing it linear values made the light ~15x too dark.
    encoded[:, :3] = np.where(linear <= 0.0031308, linear * 12.92,
                              1.055 * np.power(linear, 1 / 2.4) - 0.055)
    out.pixels.foreach_set(encoded.ravel())
    out.filepath_raw = path
    out.file_format = "JPEG"
    bpy.context.scene.render.image_settings.quality = 92
    out.save()
    return scale


def bake(objects: list[bpy.types.Object], size: int, path: str) -> float:
    """
    All objects' diffuse light (no colour) into one atlas on a second UV set.
    Returns the scale save_lightmap stored it at.
    """
    layout_lightmap(objects)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]

    image = bpy.data.images.new("lightmap", size, size, float_buffer=True)
    targets = []
    for mat in {m for obj in objects for m in obj.data.materials if m}:
        node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        mat.node_tree.nodes.active = node
        targets.append((mat, node))
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"DIRECT", "INDIRECT"}, uv_layer="lightmap",
                        margin=3, use_clear=True)
    scale = save_lightmap(image, path)
    for mat, node in targets:
        mat.node_tree.nodes.remove(node)
    for obj in objects:
        obj.data.uv_layers.active = obj.data.uv_layers[0]
    return scale


def main() -> None:
    args = sys.argv[sys.argv.index("--") + 1 :]
    spec = json.loads(Path(args[0]).read_text())
    preview_only = "--preview-only" in args

    bpy.ops.wm.read_factory_settings(use_empty=True)
    setup_cycles(spec["samples"])

    b = Builder()
    windows = []
    for room in spec["rooms"]:
        windows += build_room(b, room)
    mats = {
        "wall": material("wall", spec["wall_colour"], 0.92),
        "ceiling": material("ceiling", "#f4f2ee", 0.95),
        "trim": material("trim", "#f1efea", 0.45),
        "floor": floor_material(spec["floor"]),
        "glass": glass_material(),
    }
    shell = b.finish(mats)
    for obj in shell:
        if obj.name != "glass":
            cull_unseen(obj, spec["rooms"])
    for obj in shell:
        if obj.name.startswith("floor"):
            floor_uvs(obj, spec["floor"]["tile"])
            # Exported as glTF extras: the viewer scales a swapped floor's
            # texture by the ratio of tile sizes, as these UVs are in tiles.
            obj["tileM"] = list(spec["floor"]["tile"])
            obj["roomId"] = obj.name.removeprefix("floor-")
        else:
            plain_uvs(obj)
    pieces = [furniture(item) for item in spec["items"]]
    patch = light(windows, spec["rooms"])

    out = spec["out"]
    preview(spec, windows, patch, out["preview"])
    if preview_only:
        return
    glass = [o for o in shell if o.name == "glass"]
    for obj in glass:
        obj.hide_render = True  # let the sun through the openings while baking
    scale = bake([o for o in shell if o not in glass] + pieces, spec["lightmap_size"],
                 out["lightmap"])
    Path(out["meta"]).write_text(json.dumps({"lightmapScale": scale}))
    for obj in glass:
        obj.hide_render = False
    bpy.ops.export_scene.gltf(filepath=out["glb"], export_format="GLB", export_apply=True,
                              export_lights=False, export_cameras=False, export_extras=True)


main()
