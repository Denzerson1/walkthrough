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
from collections import defaultdict
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
    is_flat,
    join_partitions,
    mitres,
    opening_spans,
    polygon_union,
    slope_frame,
    slope_point,
    wall_cells,
    wall_frame,
    wall_pieces,
    wall_profile,
)

SLAB_T = 0.2  # floor and ceiling slab thickness, so no light leaks at edges
PARTITION_T = 0.1
SKIRTING_H, SKIRTING_D = 0.08, 0.012
TRIM_W, TRIM_D = 0.07, 0.015  # door architrave
FRAME_W, FRAME_D = 0.05, 0.07  # window frame
SASH_W = 0.065  # roof window sash, seen from inside
LINING_T = 0.012
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


def floor_material(name: str, spec: dict) -> bpy.types.Material:
    mat = material(name, spec["colour"], 0.6)
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
        """8 corners: one end quad, then the matching quad of the other end.
        A sloping top may bring corners together; `finish` drops what that collapses."""
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
            bmesh.ops.dissolve_degenerate(bm, dist=1e-5, edges=bm.edges)
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            mesh = bpy.data.meshes.new(key)
            bm.to_mesh(mesh)
            bm.free()
            mesh.materials.append(materials.get(key) or materials[key.split("-")[0]])
            obj = bpy.data.objects.new(key, mesh)
            bpy.context.collection.objects.link(obj)
            objects.append(obj)
        return objects


def wall_prism(b: Builder, key: str, wall: dict, u0: float, u1: float, y0: float,
               top0: float, top1: float, v0: float, v1: float,
               reach0: float = 0.0, reach1: float = 0.0) -> None:
    """A wall piece in the wall's frame (u along, v outward, y up), its top
    running straight from `top0` at u0 to `top1` at u1, its ends moved by
    `reach0` and `reach1` on the outer face (mitres). Empty ones are skipped."""
    if u1 - u0 < 1e-4 or max(top0, top1) - y0 < 1e-4:
        return
    (dx, dz), (nx, nz), _ = wall_frame(wall["start"], wall["end"])
    ax, az = wall["start"]

    def at(u: float, v: float, y: float) -> Vector:
        return P(ax + dx * u + nx * v, y, az + dz * u + nz * v)

    o0, o1 = u0 + reach0, u1 + reach1
    corners = [at(u0, v0, y0), at(u1, v0, y0), at(o1, v1, y0), at(o0, v1, y0)]
    corners += [at(u0, v0, top0), at(u1, v0, top1), at(o1, v1, top1), at(o0, v1, top0)]
    b.box(key, corners)


def wall_box(b: Builder, key: str, wall: dict, u0: float, u1: float, y0: float, y1: float,
             v0: float, v1: float) -> None:
    wall_prism(b, key, wall, u0, u1, y0, y1, y1, v0, v1)


def slope_box(b: Builder, key: str, frame: dict, u0: float, u1: float, v0: float, v1: float,
              n0: float, n1: float) -> None:
    """A box in a sloped ceiling's frame: u level along it, v up it, n out of the room."""
    def q(u, v, n):
        return P(*slope_point(frame, u, v, n))

    b.box(key, [q(u0, v0, n0), q(u1, v0, n0), q(u1, v1, n0), q(u0, v1, n0),
                q(u0, v0, n1), q(u1, v0, n1), q(u1, v1, n1), q(u0, v1, n1)])


def build_room(b: Builder, room: dict, skylights: list[dict]) -> list[dict]:
    """Walls with openings, slabs, ceilings, partitions, skirting, frames.
    Returns the windows, for the sun."""
    h = room["height"]
    ceilings = room.get("ceilings") or []
    # The slabs reach a hair under the walls: where two surfaces only meet at
    # an edge, the renderer shows cracks of background between them. Only a
    # hair, under a light texel: the part inside the wall sees the sky, and a
    # wider strip bled into the visible ceiling as a bright line.
    outline = grow(room["polygon"], 0.002)
    # One floor per room, so the viewer can swap each room's floor on its own.
    b.slab(f"floor-{room['id']}", outline, -SLAB_T, 0.0)
    # A zone with a floor of its own (the tiled hall of an open plan) lies on
    # top, 2 mm up; the room's floor stays whole beneath it.
    for zone in room.get("zones", []):
        b.slab(f"floorzone-{zone['label']}", zone["polygon"], 0.0, 0.002)
    build_ceilings(b, room, skylights)

    windows = []
    ends = mitres(room["polygon"], WALL_T)
    for index, wall in enumerate(room["walls"]):
        _, _, length = wall_frame(wall["start"], wall["end"])
        openings = sorted(wall["openings"], key=lambda o: o["offset"])
        spans = opening_spans(wall, h)
        for o, (lo, hi, bottom, top) in zip(openings, spans, strict=True):
            if o["type"] == "door":
                door(b, wall, lo, hi, top)
            else:
                window(b, wall, lo, hi, bottom, top)
                (dx, dz), (nx, nz), _ = wall_frame(wall["start"], wall["end"])
                mid = (lo + hi) / 2
                windows.append({
                    "area": (hi - lo) * (top - bottom),
                    "centre": (wall["start"][0] + dx * mid, (bottom + top) / 2,
                               wall["start"][1] + dz * mid),
                    "normal": (nx, 0.0, nz),
                })
        # Floor to ceiling exactly, following a slope where one comes down to
        # the wall; the slabs reach under the walls to seal the edges.
        profile = (wall_profile(wall["start"], wall["end"], ceilings, h) if ceilings
                   else [(0.0, h), (length, h)])
        start, end = ends[index]
        for u0, u1, y0, t0, t1 in wall_pieces(length, profile, spans):
            wall_prism(b, "wall", wall, u0, u1, y0, t0, t1, 0, WALL_T,
                       start if u0 < 1e-6 else 0.0, end if u1 > length - 1e-6 else 0.0)
        skirting(b, wall, [o for o in openings if o["type"] == "door"], length)

    for part in join_partitions(room.get("partitions", []), PARTITION_T):
        wall = {"start": part["start"], "end": part["end"], "openings": []}
        _, _, length = wall_frame(wall["start"], wall["end"])
        profile = (wall_profile(wall["start"], wall["end"], ceilings, h) if ceilings
                   else [(0.0, h), (length, h)])
        for u0, u1, y0, t0, t1 in wall_pieces(length, profile, []):
            wall_prism(b, "wall", wall, u0, u1, y0, t0, t1, -PARTITION_T / 2, PARTITION_T / 2)

    for sk in skylights:
        roof_window(b, sk)
        windows.append({"area": sk["area"], "centre": tuple(sk["centre"]),
                        "normal": tuple(sk["frame"]["n"])})
    return windows


def build_ceilings(b: Builder, room: dict, skylights: list[dict]) -> None:
    """
    The scanned ceiling: flat pieces at one height merged into one slab, so
    no two overlap on the same plane; each slope its own slab, cut around
    the roof windows in it. Without scanned pieces, one flat slab.
    """
    ceilings = room.get("ceilings") or []
    if not ceilings:
        h = room["height"]
        b.slab("ceiling", grow(room["polygon"], 0.002), h, h + SLAB_T)
        return
    levels = defaultdict(list)
    for c in ceilings:
        if is_flat(c):
            levels[round(sum(p[1] for p in c) / len(c), 3)].append([[p[0], p[2]] for p in c])
    for y, plans in levels.items():
        outline = polygon_union(plans) if len(plans) > 1 else plans[0]
        b.slab("ceiling", grow(outline, 0.002), y, y + SLAB_T)
    for index, c in enumerate(ceilings):
        if is_flat(c):
            continue
        frame = next((sk["frame"] for sk in skylights if sk["ceiling"] == index), None)
        if frame is None:
            frame = slope_frame(c)
        holes = [sk["hole"] for sk in skylights if sk["ceiling"] == index]
        lu, lv, g = frame["lu"], frame["lv"], 0.002
        for u0, u1, v0, v1 in wall_cells(lu, lv, holes):
            # Grown a hair past the piece's own edges, like the flat slabs.
            slope_box(b, "ceiling", frame,
                      u0 - g if u0 < 1e-6 else u0, u1 + g if u1 > lu - 1e-6 else u1,
                      v0 - g if v0 < 1e-6 else v0, v1 + g if v1 > lv - 1e-6 else v1,
                      0.0, SLAB_T)


def roof_window(b: Builder, sk: dict) -> None:
    """The lining boards, the sash and the pane of a roof window (see flat_geometry.roof_window)."""
    frame, depth = sk["frame"], sk["depth"]
    hu0, hu1, hv0, hv1 = sk["hole"]
    axis = Vector(slope_point(frame, (hu0 + hu1) / 2, (hv0 + hv1) / 2, depth / 2))
    for quad in sk["lining"]:
        pts = [Vector(p) for p in quad]
        normal = (pts[1] - pts[0]).cross(pts[3] - pts[0]).normalized()
        middle = sum(pts, Vector()) / 4
        if normal.dot(axis - middle) < 0:
            normal = -normal
        # Thickened into the shaft, so its outer face lies on the slab's cut.
        inner = [p + normal * LINING_T for p in pts]
        b.box("skylight", [P(*p) for p in pts] + [P(*p) for p in inner])
    u0, u1, v0, v1 = sk["sash"]
    n0, n1 = depth - 0.07, depth + 0.005
    slope_box(b, "skylight", frame, u0, u0 + SASH_W, v0, v1, n0, n1)
    slope_box(b, "skylight", frame, u1 - SASH_W, u1, v0, v1, n0, n1)
    slope_box(b, "skylight", frame, u0 + SASH_W, u1 - SASH_W, v0, v0 + SASH_W, n0, n1)
    slope_box(b, "skylight", frame, u0 + SASH_W, u1 - SASH_W, v1 - SASH_W, v1, n0, n1)
    slope_box(b, "glass", frame, u0 + SASH_W, u1 - SASH_W, v0 + SASH_W, v1 - SASH_W,
              depth - 0.035, depth - 0.027)


VIEW_DISTANCE = 4.0  # how far out the view photo stands


def window_view(sk: dict, index: int) -> bpy.types.Object:
    """
    The customer's photo out of a roof window, on a plane parallel to its
    glass a few metres out. The plane spans the photo's field of view (a
    phone's 1x lens, 69 degrees along its long side) and a third more, so a
    glance in at an angle still lands on it. Shown unlit, and hidden while
    baking, so it never shades the room.
    """
    frame = sk["frame"]
    u0, u1, v0, v1 = sk["sash"]
    uc, vc = (u0 + u1) / 2, (v0 + v1) / 2
    image = bpy.data.images.load(sk["view"])
    long_side = 2 * VIEW_DISTANCE * math.tan(math.radians(69 / 2)) * 1.3
    w, h = image.size
    across, up = (long_side * w / h, long_side) if h >= w else (long_side, long_side * h / w)
    n = sk["depth"] + VIEW_DISTANCE
    # Looking out, the photo's right is the slope's -u and its up is +v.
    corners = [(uc + across / 2, vc - up / 2), (uc - across / 2, vc - up / 2),
               (uc - across / 2, vc + up / 2), (uc + across / 2, vc + up / 2)]
    mesh = bpy.data.meshes.new(f"view-{index + 1}")
    mesh.from_pydata([P(*slope_point(frame, a, b_, n)) for a, b_ in corners], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    for loop, coord in zip(mesh.loops, ((0, 0), (1, 0), (1, 1), (0, 1)), strict=True):
        uv.data[loop.index].uv = coord
    mat = bpy.data.materials.new(f"view-{index + 1}")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 1.0
    mesh.materials.append(mat)
    obj = bpy.data.objects.new(f"view-{index + 1}", mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def door(b: Builder, wall: dict, lo: float, hi: float, top: float) -> None:
    mid = WALL_T / 2
    wall_box(b, "trim", wall, lo + 0.005, hi - 0.005, 0, top - 0.005, mid - 0.02, mid + 0.02)
    # What lies behind a door (a bathroom, the stairwell) is not modelled, so
    # back the doorway: its leaf's 5 mm gaps otherwise let the sky shine in.
    wall_box(b, "wall", wall, lo, hi, 0, top, WALL_T - 0.01, WALL_T)
    # And a threshold: the floor stops at the wall line, so under the leaf
    # one looked down past the floor's edge as a bright line. 3 mm proud,
    # not flush, so its top is not in the floor's plane.
    wall_box(b, "trim", wall, lo, hi, -SLAB_T, 0.003, 0, WALL_T)
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


def mark_unseen(obj: bpy.types.Object, rooms: list[dict]) -> None:
    """
    Flag the faces that face no air: outer wall faces, slab tops, caps
    buried in other boxes. Nobody sees them from inside, so they get no
    room in the light atlas (layout_lightmap). They stay in the mesh to keep
    the solids solid while baking: deleted, the 2 mm by which the slabs
    reach under the walls looked out at the sky, and that light bled into
    the room's edges as glowing lines along every crease.
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    unseen = bm.faces.layers.int.new("unseen")
    for face in bm.faces:
        c, n = face.calc_center_median(), face.normal
        probe = c + n * 0.01  # Blender (x, -z, y) back to scene below
        face[unseen] = 0 if is_air(probe.x, probe.z, -probe.y, rooms) else 1
    # Weld: a wall's cells then form one connected surface, so
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

#: Surfaces the finishes are made of: colour (sRGB) and roughness.
SURFACES = {
    "gloss": ("#f3f3f1", 0.12), "stone": ("#6f6d69", 0.45), "plinth": ("#3d3d3c", 0.6),
    "oak": ("#c4ab8c", 0.55), "wenge": ("#3a2a23", 0.5), "linen": ("#f1eee8", 0.9),
    "boucle": ("#e6e1d7", 1.0), "black": ("#121314", 0.2), "fabric": ("#a29e98", 0.9),
    "white": ("#efeeea", 0.6), "coats": ("#2b2a2c", 0.95), "shade": ("#f4f0e6", 0.8),
}


def finish_parts(finish: str, w: float, h: float, d: float) -> list[tuple[str, tuple]]:
    """
    A piece drawn from boxes, in its own frame: x across, y depth with the
    front at -d/2, z up from 0. The built-ins the scan boxed (a kitchen run,
    an island, a wardrobe) are architecture more than furniture, and parts
    in the right materials read as them where a single block reads as a crate.
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    base = [("plinth", (x0, x1, y0 + 0.06, y1, 0, 0.1)),
            ("gloss", (x0, x1, y0 + 0.02, y1, 0.1, h - 0.04)),
            ("stone", (x0, x1, y0, y1, h - 0.04, h))]
    legs = [("oak", (x, x + 0.03, y, y + 0.03, 0, h * 0.7))
            for x in (x0 + 0.04, x1 - 0.07) for y in (y0 + 0.04, y1 - 0.07)]
    parts = {
        "kitchen-base": base,
        "oven": [*base, ("black", (x0 + 0.03, x1 - 0.03, y0 + 0.015, y0 + 0.021, 0.4, h - 0.1))],
        "kitchen-tall": [base[0], ("gloss", (x0, x1, y0, y1, 0.1, h))],
        "kitchen-wall": [("gloss", (x0, x1, y0, y1, 0, h))],
        "hob": [("black", (x0, x1, y0, y1, 0, h))],
        "island": [("plinth", (x0 + 0.04, x1 - 0.04, y0 + 0.06, y1 - 0.06, 0, 0.1)),
                   ("gloss", (x0 + 0.02, x1 - 0.02, y0 + 0.02, y1 - 0.02, 0.1, h - 0.04)),
                   ("oak", (x0, x1, y0, y1, h - 0.04, h))],
        "shelves": [("oak", (x0, x1, y1 - 0.02, y1, 0, h))] + [
            ("oak", (x0, x1, y0, y1 - 0.02, z, z + 0.025)) for z in (0.0, h / 2, h - 0.025)],
        "wardrobe": [("oak", (x0, x1, y0, y1, 0, h))],
        "open-wardrobe": [("oak", (x0, x0 + 0.02, y0, y1, 0, h)),
                          ("oak", (x1 - 0.02, x1, y0, y1, 0, h)),
                          ("oak", (x0 + 0.02, x1 - 0.02, y0, y1, h - 0.02, h)),
                          ("oak", (x0 + 0.02, x1 - 0.02, y0, y1, 0, 0.1)),
                          ("oak", (x0 + 0.02, x1 - 0.02, y1 - 0.015, y1, 0.1, h - 0.02)),
                          ("oak", (x0 + 0.02, x1 - 0.02, y0, y1 - 0.015, h - 0.36, h - 0.34)),
                          ("coats", (x0 + 0.08, x1 - 0.08, y0 + 0.1, y1 - 0.08, 0.7, h - 0.42))],
        "bed": [("wenge", (x0, x1, y0, y1, 0, 0.3)),
                ("linen", (x0 + 0.03, x1 - 0.03, y0 + 0.03, y1 - 0.03, 0.3, h))],
        "sofa": [("boucle", (x0, x1, y0, y1, 0, 0.42)),
                 ("boucle", (x0, x1, y1 - 0.24, y1, 0.42, h)),
                 ("boucle", (x0, x0 + 0.22, y0, y1 - 0.24, 0.42, h * 0.82)),
                 ("boucle", (x1 - 0.22, x1, y0, y1 - 0.24, 0.42, h * 0.82))],
        "tv": [("black", (x0, x1, y0, y1, 0, h))],
        "stool": [*legs, ("fabric", (x0, x1, y0, y1, h * 0.7, h * 0.7 + 0.07)),
                  ("fabric", (x0 + 0.03, x1 - 0.03, y1 - 0.05, y1, h * 0.7 + 0.07, h))],
        "panel": [("oak", (x0, x1, y0, y1, 0, h))],
        "air-con": [("white", (x0, x1, y0, y1, 0, h))],
    }
    if finish not in parts:
        raise ValueError(f"Unknown finish {finish!r}")
    return parts[finish]


def finished(item: dict) -> bpy.types.Object:
    """A piece built from its finish's parts, at its true size; one object, one material each."""
    w, h, d = item["size"]
    mesh = bpy.data.meshes.new(item["id"])
    bm = bmesh.new()
    slots: list[str] = []
    if item["finish"] == "pendant":
        # A drum shade on a cable from the ceiling.
        radius, shade = min(w, d) / 2, 0.28
        cone = bmesh.ops.create_cone(bm, cap_ends=True, segments=40, radius1=radius,
                                     radius2=radius, depth=shade)
        bmesh.ops.translate(bm, verts=cone["verts"], vec=(0, 0, shade / 2))
        cable = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, verts=cable["verts"], vec=(0.008, 0.008, h - shade))
        bmesh.ops.translate(bm, verts=cable["verts"], vec=(0, 0, shade + (h - shade) / 2))
        for face in bm.faces:
            face.material_index = 0
        slots = ["shade"]
    else:
        for surface, (px0, px1, py0, py1, pz0, pz1) in finish_parts(item["finish"], w, h, d):
            if surface not in slots:
                slots.append(surface)
            cube = bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.scale(bm, verts=cube["verts"], vec=(px1 - px0, py1 - py0, pz1 - pz0))
            bmesh.ops.translate(bm, verts=cube["verts"],
                                vec=((px0 + px1) / 2, (py0 + py1) / 2, (pz0 + pz1) / 2))
            for face in {f for v in cube["verts"] for f in v.link_faces}:
                face.material_index = slots.index(surface)
    bm.to_mesh(mesh)
    bm.free()
    for surface in slots:
        mat = bpy.data.materials.get(f"surface-{surface}")
        if mat is None:
            mat = material(f"surface-{surface}", *SURFACES[surface])
        mesh.materials.append(mat)
    obj = bpy.data.objects.new(item["id"], mesh)
    bpy.context.collection.objects.link(obj)
    if item["finish"] == "sofa":
        bevel = obj.modifiers.new("soft", "BEVEL")  # boucle is all rounded edges
        bevel.width, bevel.segments = 0.05, 4
    plain_uvs(obj)
    return obj


def furniture(item: dict) -> bpy.types.Object:
    """The piece's model (or a block), fitted to its scanned box and turned to face `front`."""
    w, h, d = item["size"]
    if item.get("finish") and not item["model"]:
        obj = finished(item)
    elif item["model"]:
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

    if not item.get("finish") or item["model"]:
        # Normalise: footprint centred on the origin, standing on z = 0,
        # stretched to the scanned box. Finished pieces are built to size.
        xs = [v.co.x for v in obj.data.vertices]
        ys = [v.co.y for v in obj.data.vertices]
        zs = [v.co.z for v in obj.data.vertices]
        centre = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)))
        extent = Vector((max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)))
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


def light(spec: dict, windows: list[dict]) -> tuple[float, float] | None:
    """Sky, sun and fallback lamps. Returns where the sun patch lands (x, z), if known."""
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
    # A clear sky is deep blue; through a window it tints white walls cold.
    # Interior photographs are white-balanced to the room, so tone it down.
    tame = nodes.new("ShaderNodeHueSaturation")
    tame.inputs["Saturation"].default_value = 0.2
    links.new(sky.outputs["Color"], tame.inputs["Color"])
    links.new(tame.outputs["Color"], nodes["Background"].inputs["Color"])
    nodes["Background"].inputs["Strength"].default_value = 0.5

    if not windows:
        # No window: warm ceiling light, as the room would be lit in the evening.
        for room in spec["rooms"]:
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

    main = max(windows, key=lambda w: w["area"])
    patch = None
    if spec.get("sun"):
        # The real sun for the flat's place, date and time.
        tx, ty, tz = spec["sun"]["direction"]
        elevation = math.asin(ty)
    else:
        # Through the largest window, a little sideways: a sun patch across
        # the floor is what makes an interior read as a photograph.
        nx, _, nz = main["normal"]
        across = math.hypot(nx, nz) or 1.0
        nx, nz = nx / across, nz / across
        c, s = math.cos(SUN_SIDEWAYS), math.sin(SUN_SIDEWAYS)
        elevation = SUN_ELEVATION
        tx, tz = (nx * c - nz * s) * math.cos(elevation), (nx * s + nz * c) * math.cos(elevation)
        ty = math.sin(elevation)
        wx, wy, wz = main["centre"]
        reach = wy / math.tan(elevation)
        across_t = math.hypot(tx, tz)
        patch = (wx - tx / across_t * reach, wz - tz / across_t * reach)
    travel = P(-tx, -ty, -tz)
    sun = bpy.data.lights.new("sun", "SUN")
    sun.energy, sun.angle = 5.5, math.radians(1.2)
    sun.color = (1.0, 0.87, 0.72)
    obj = bpy.data.objects.new("sun", sun)
    bpy.context.collection.objects.link(obj)
    obj.rotation_euler = travel.to_track_quat("-Z", "Y").to_euler()
    sky.sun_elevation = elevation
    sky.sun_rotation = math.atan2(-travel.x, -travel.y)
    return patch


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
    scene.view_settings.exposure = 0.0  # set from the bake by `expose`


# --------------------------------------------------------------------------
# Preview, bake, export
# --------------------------------------------------------------------------


def hero_view(spec: dict, windows: list[dict],
              patch: tuple[float, float] | None) -> tuple[Vector, Vector]:
    """
    Where an interior photographer would stand: the dressing's view when it
    names one. Otherwise across the light, not into it: from the corner
    furthest from the sun patch, looking between the main window and the
    patch. Without either, from the waypoint along its yaw.
    """
    room = spec["rooms"][0]
    if spec.get("hero"):
        eye, target = Vector(spec["hero"]["eye"]), Vector(spec["hero"]["target"])
        look = (target - eye).normalized()
        return P(*eye), P(*look)
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


def expose(exposure: float, views: list[bpy.types.Object]) -> None:
    """Grade the preview as the viewer will show it: the room exposed, the views as photographed."""
    bpy.context.scene.view_settings.exposure = math.log2(exposure)
    for obj in views:
        bsdf = obj.data.materials[0].node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Emission Strength"].default_value = 1.0 / exposure


def preview(spec: dict, windows: list[dict], patch: tuple[float, float] | None,
            path: str) -> None:
    position, look = hero_view(spec, windows, patch)
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
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    areas = []
    for obj in objects:
        layer = obj.data.uv_layers.new(name="lightmap")
        obj.data.uv_layers.active = layer
        flag = obj.data.attributes.get("unseen")
        count = len(obj.data.polygons)
        hidden = [bool(flag.data[i].value) for i in range(count)] if flag else [False] * count
        for poly, is_hidden in zip(obj.data.polygons, hidden, strict=True):
            poly.select = not is_hidden
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")
        # Unseen faces shrink to a point: in the bake, but on no texels.
        uv = obj.data.uv_layers["lightmap"].data
        for poly, is_hidden in zip(obj.data.polygons, hidden, strict=True):
            if is_hidden:
                for i in poly.loop_indices:
                    uv[i].uv = (0.0, 0.0)
        areas.append(sum(p.area for p, h in zip(obj.data.polygons, hidden, strict=True) if not h))
    corners, sides = atlas_cells(areas, margin)
    for obj, side, (cx, cy) in zip(objects, sides, corners, strict=True):
        for loop in obj.data.uv_layers["lightmap"].data:
            loop.uv = (cx + loop.uv[0] * side, cy + loop.uv[1] * side)


def denoise(baked: bpy.types.Image, scratch: Path):
    """
    The bake through Open Image Denoise, the denoiser Cycles uses for its
    renders. Light in a room with two small roof windows is nearly all
    bounce, the noisiest kind; at the exposure such a room needs, a blur
    left it blotchy. Run by the compositor of an empty scene, so nothing is
    rendered but the denoise.
    """
    import numpy as np

    size = baked.size[0]
    scene = bpy.data.scenes.new("denoise")
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.compositor_device = "CPU"
    tree = bpy.data.node_groups.new("denoise", "CompositorNodeTree")
    scene.compositing_node_group = tree
    tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    source = tree.nodes.new("CompositorNodeImage")
    source.image = baked
    node = tree.nodes.new("CompositorNodeDenoise")
    node.inputs["HDR"].default_value = True
    out = tree.nodes.new("NodeGroupOutput")
    tree.links.new(source.outputs["Image"], node.inputs["Image"])
    tree.links.new(node.outputs["Image"], out.inputs[0])
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"
    bpy.ops.render.render(scene=scene.name)
    path = scratch / "lightmap-denoised.exr"
    bpy.data.images["Render Result"].save_render(str(path), scene=scene)
    result = bpy.data.images.load(str(path))
    pixels = np.empty(size * size * 4, dtype=np.float32)
    result.pixels.foreach_get(pixels)
    bpy.data.images.remove(result)
    bpy.data.scenes.remove(scene)
    path.unlink()
    return pixels.reshape(size, size, 4)


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


#: Exposure, like a camera's: the room's typical surface lands at this
#: (linear, before tone mapping), and what is brighter than CLIP times it
#: clips — sun on a window lining, which a photograph blows out too.
EXPOSE_TO, CLIP = 0.38, 16.0


def save_lightmap(baked: bpy.types.Image, path: str, scratch: Path) -> tuple[float, float]:
    """
    The float bake as an 8-bit sRGB JPEG, plus the scale that undoes it and
    the exposure the room wants.

    A float EXR of a 2048 atlas is ~50 MB; light is smooth, so 8 bits in a
    perceptual curve lose nothing visible and come to a megabyte or two. The
    range is set by the room, not its brightest texel: normalised to the
    sunlit window linings, the walls got a few dozen of the 256 levels and
    JPEG turned them into blotches once exposed.
    """
    import numpy as np

    size = baked.size[0]
    rgba = np.empty(size * size * 4, dtype=np.float32)
    baked.pixels.foreach_get(rgba)
    rgba = rgba.reshape(size, size, 4)
    # What the bake wrote, margins included. Not the alpha: a new image is
    # opaque black throughout, so alpha marks every texel as baked.
    covered = rgba[..., :3].max(axis=-1) > 0
    clean = denoise(baked, scratch)[..., :3]
    # A light smoothing after, kept inside each island, takes the denoiser's
    # last grain off; outside the islands stays empty.
    rgb = smooth_within_islands(clean, covered, sigma=1.0).reshape(-1, 3)
    covered = covered.ravel()
    typical = float(np.median(rgb.max(axis=1)[covered])) if covered.any() else 1.0
    scale = float(max(min(np.percentile(rgb.max(axis=1), 99.9), CLIP * typical), 1e-6))
    # Mostly white walls and ceilings: albedo ~0.85 under the typical light.
    exposure = min(max(EXPOSE_TO / (0.85 * max(typical, 1e-6)), 1.0), 64.0)
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
    bpy.context.scene.render.image_settings.quality = 95
    out.save()
    return scale, exposure


def bake(objects: list[bpy.types.Object], size: int, path: str,
         scratch: Path) -> tuple[float, float]:
    """
    All objects' diffuse light (no colour) into one atlas on a second UV set.
    Returns the scale save_lightmap stored it at and the room's exposure.
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
    # A wide margin, so the denoiser blends island edges with island, not black.
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"DIRECT", "INDIRECT"}, uv_layer="lightmap",
                        margin=6, use_clear=True)
    scale, exposure = save_lightmap(image, path, scratch)
    for mat, node in targets:
        mat.node_tree.nodes.remove(node)
    for obj in objects:
        obj.data.uv_layers.active = obj.data.uv_layers[0]
    return scale, exposure


def main() -> None:
    args = sys.argv[sys.argv.index("--") + 1 :]
    spec = json.loads(Path(args[0]).read_text())
    preview_only = "--preview-only" in args

    bpy.ops.wm.read_factory_settings(use_empty=True)
    setup_cycles(spec["samples"])

    b = Builder()
    windows = []
    for i, room in enumerate(spec["rooms"]):
        windows += build_room(b, room, spec["skylights"] if i == 0 else [])
    trim = material("trim", "#f1efea", 0.45)
    mats = {
        "wall": material("wall", spec["wall_colour"], 0.92),
        "ceiling": material("ceiling", "#f4f2ee", 0.95),
        "trim": trim,
        "skylight": trim,
        "floor": floor_material("floor", spec["floors"]["main"]),
        "glass": glass_material(),
    }
    for label, floor in spec["floors"].items():
        if label != "main":
            mats[f"floorzone-{label}"] = floor_material(f"floorzone-{label}", floor)
    shell = b.finish(mats)
    for obj in shell:
        # The roof window's shaft is not room air, so its lining would count as unseen.
        if obj.name not in ("glass", "skylight"):
            mark_unseen(obj, spec["rooms"])
    for obj in shell:
        if obj.name.startswith("floor-"):
            floor_uvs(obj, spec["floors"]["main"]["tile"])
            # Exported as glTF extras: the viewer scales a swapped floor's
            # texture by the ratio of tile sizes, as these UVs are in tiles.
            obj["tileM"] = list(spec["floors"]["main"]["tile"])
            obj["roomId"] = obj.name.removeprefix("floor-")
        elif obj.name.startswith("floorzone-"):
            floor_uvs(obj, spec["floors"][obj.name.removeprefix("floorzone-")]["tile"])
        else:
            plain_uvs(obj)
    pieces = [furniture(item) for item in spec["items"]]
    views = [window_view(sk, i) for i, sk in enumerate(spec["skylights"]) if sk.get("view")]
    patch = light(spec, windows)

    out = spec["out"]
    if preview_only:
        # The exposure is the bake's; reuse the last one, or a typical room's.
        meta = Path(out["meta"])
        exposure = json.loads(meta.read_text()).get("exposure", 6.0) if meta.is_file() else 6.0
        expose(exposure, views)
        preview(spec, windows, patch, out["preview"])
        return
    glass = [o for o in shell if o.name == "glass"]
    for obj in glass + views:
        obj.hide_render = True  # let the sun and sky through the openings while baking
    scale, exposure = bake([o for o in shell if o not in glass] + pieces,
                           spec["lightmap_size"], out["lightmap"], Path(out["meta"]).parent)
    # The viewer shows colour x light x lightmapScale, so the exposure rides
    # on the scale; the views out of the windows carry no baked light and
    # so stay as photographed.
    Path(out["meta"]).write_text(json.dumps({"lightmapScale": scale * exposure,
                                             "exposure": exposure}))
    for obj in glass + views:
        obj.hide_render = False
    expose(exposure, views)
    preview(spec, windows, patch, out["preview"])
    bpy.ops.export_scene.gltf(filepath=out["glb"], export_format="GLB", export_apply=True,
                              export_lights=False, export_cameras=False, export_extras=True)


main()
