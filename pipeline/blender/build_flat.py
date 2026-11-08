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
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "walkthrough_pipeline"))
import flat_geometry  # noqa: E402
from flat_geometry import (  # noqa: E402  (standard library only, so importable here)
    WALL_T,
    convex_hull,
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
LEAF_T, DOOR_LINING = 0.04, 0.02  # an inner door's leaf; its reveals' lining
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


#: A box's six faces by its corners' indices (see Builder.box).
BOX_FACES = ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))


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
        for face in BOX_FACES:
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


class Recorder:
    """Stands in for a Builder where a part is its own object: keeps its boxes."""

    def __init__(self) -> None:
        self.boxes: list[tuple[str, list[Vector]]] = []

    def box(self, key: str, corners: list[Vector]) -> None:
        self.boxes.append((key, corners))


def three_turn(x: float, z: float, phi: float) -> tuple[float, float]:
    """(x, z) turned by `phi` about +Y as three.js turns a node (rotation.y)."""
    c, s = math.cos(phi), math.sin(phi)
    return x * c + z * s, -x * s + z * c


def swing_pivot(a_hinge, a_far, b_hinge, b_far) -> tuple[tuple[float, float], float]:
    """
    The pivot (x, z) and the quarter turn (radians, three.js's sense about +Y)
    that take a leaf lying from `a_hinge` to `a_far` onto `b_hinge` to `b_far`.
    """
    for phi in (math.pi / 2, -math.pi / 2):
        rx, rz = three_turn(a_far[0] - a_hinge[0], a_far[1] - a_hinge[1], phi)
        if rx * (b_far[0] - b_hinge[0]) + rz * (b_far[1] - b_hinge[1]) <= 0:
            continue
        # p = b - R (a - p), so (I - R) p = b - R a.
        c, s = math.cos(phi), math.sin(phi)
        ra = three_turn(*a_hinge, phi)
        tx, tz = b_hinge[0] - ra[0], b_hinge[1] - ra[1]
        m00, m01, m10, m11 = 1 - c, -s, s, 1 - c
        det = m00 * m11 - m01 * m10
        return ((m11 * tx - m01 * tz) / det, (m00 * tz - m10 * tx) / det), phi
    raise ValueError("the leaf does not turn onto its frame")


#: The inner doors' leaves, each built as its own object turning about its
#: hinge, so the viewer can open and close it: built standing open.
DOOR_LEAVES: list[dict] = []


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
    if o1 < o0:
        # A piece shorter than its inside corner's mitre (the 13 cm between
        # the front door and the hall's corner): a wedge up to the corner's
        # diagonal. As a box its outer face crossed over, and the twisted
        # piece showed its diagonal into the hall as a black strip.
        if reach0 > 0:
            o0 = o1
        else:
            o1 = o0
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
            if o["type"] == "door" and o.get("glazed"):
                # A balcony door: its frame in the wall, a threshold, and the
                # glazed leaf a piece of its own (glass-door) that opens.
                window(b, wall, lo, hi, bottom, top, glass=False)
                wall_box(b, "trim", wall, lo, hi, -SLAB_T, 0.003, 0, WALL_T)
            elif o["type"] == "door":
                door(b, wall, lo, hi, top, o.get("frame", True), o.get("latch", "end"),
                     o.get("depth"))
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

    parts = room.get("partitions", [])
    # A floor plan's walls (each with its thickness) already meet exactly;
    # joined, the small bedroom's 32 cm wall was pulled onto the 25 cm one
    # beside it and both ran 3.5 cm askew.
    if not all("thickness" in p for p in parts):
        parts = join_partitions(parts, PARTITION_T)
    for part in parts:
        # As thick as drawn on a floor plan (25 cm of brick, 13 of plasterboard).
        t = part.get("thickness", PARTITION_T) / 2
        wall = {"start": part["start"], "end": part["end"], "openings": part.get("openings", []),
                "thickness": 2 * t}
        _, _, length = wall_frame(wall["start"], wall["end"])
        profile = (wall_profile(wall["start"], wall["end"], ceilings, h) if ceilings
                   else [(0.0, h), (length, h)])
        spans = opening_spans(wall, h)
        for u0, u1, y0, t0, t1 in wall_pieces(length, profile, spans):
            wall_prism(b, "wall", wall, u0, u1, y0, t0, t1, -t, t)
        doors = sorted(wall["openings"], key=lambda o: o["offset"])
        for o, (lo, hi, _, top) in zip(doors, spans, strict=True):
            doorway(b, wall, lo, hi, top, o.get("hinge", "start"), o.get("swing", 1),
                    o.get("leaf", True), o.get("frame", True))
        # A flat's inner walls are seen from both sides.
        skirting(b, wall, doors, length, -t - SKIRTING_D, -t)
        skirting(b, wall, doors, length, t, t + SKIRTING_D)

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
    # Each slab reaches over the walls' full thickness. The scanned ceiling's
    # outline misses the walls by millimetres to centimetres; reaching 2 mm
    # past it left a crack along the hall's walls that lit up as a white line.
    # Over the walls, the slab rests on their tops, out of sight.
    for y, plans in levels.items():
        outline = polygon_union(plans) if len(plans) > 1 else plans[0]
        b.slab("ceiling", grow(outline, WALL_T), y, y + SLAB_T)
    for index, c in enumerate(ceilings):
        if is_flat(c):
            continue
        frame = next((sk["frame"] for sk in skylights if sk["ceiling"] == index), None)
        if frame is None:
            frame = slope_frame(c)
        holes = [sk["hole"] for sk in skylights if sk["ceiling"] == index]
        lu, lv, g = frame["lu"], frame["lv"], WALL_T
        for u0, u1, v0, v1 in wall_cells(lu, lv, holes):
            # Grown over the walls past the piece's own edges, like the flat slabs.
            slope_box(b, "ceiling", frame,
                      u0 - g if u0 < 1e-6 else u0, u1 + g if u1 > lu - 1e-6 else u1,
                      v0 - g if v0 < 1e-6 else v0, v1 + g if v1 > lv - 1e-6 else v1,
                      0.0, SLAB_T)


def slope_ring(b: Builder, key: str, frame: dict, outer: tuple[float, float, float, float],
               width: float, n0: float, n1: float) -> None:
    """
    A frame `width` wide round the rectangle `outer` (u0, u1, v0, v1) in a
    sloped ceiling's frame, as one closed piece. Four bars meeting end to end
    hid faces between them, and the bake spread their dark into the corners.
    """
    u0, u1, v0, v1 = outer
    rings = [[(u0, v0), (u1, v0), (u1, v1), (u0, v1)],
             [(u0 + width, v0 + width), (u1 - width, v0 + width),
              (u1 - width, v1 - width), (u0 + width, v1 - width)]]
    bm = b.bm(key)
    vs = [[[bm.verts.new(P(*slope_point(frame, u, v, n))) for u, v in ring] for ring in rings]
          for n in (n0, n1)]
    for i in range(4):
        j = (i + 1) % 4
        for side in (0, 1):  # the faces towards the room and away from it
            bm.faces.new([vs[side][0][i], vs[side][0][j], vs[side][1][j], vs[side][1][i]])
        for ring in (0, 1):  # round the outside and the inside
            bm.faces.new([vs[0][ring][i], vs[0][ring][j], vs[1][ring][j], vs[1][ring][i]])


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
    slope_ring(b, "skylight", frame, (u0, u1, v0, v1), SASH_W, n0, n1)
    slope_box(b, "glass", frame, u0 + SASH_W, u1 - SASH_W, v0 + SASH_W, v1 - SASH_W,
              depth - 0.035, depth - 0.027)


DOME_RADIUS = 80.0
VIEW_DISTANCE = 12.0  # how far out the buildings across the courtyard stand


def outside(spec: dict) -> list[bpy.types.Object]:
    """
    What the windows look out on: a sky dome in the photo's sky colours, and
    the customer's photo out of a window, its sky cut away, standing a street
    width out from the roof windows. A photo on a plane just behind the glass
    showed its edges and blank background from an angle; the dome has none.
    Both are shown unlit, and hidden while baking (the bake has its own sky).
    """
    view = spec.get("outside")
    if not view:
        return []
    room = spec["rooms"][0]
    cx = sum(p[0] for p in room["polygon"]) / len(room["polygon"])
    cz = sum(p[1] for p in room["polygon"]) / len(room["polygon"])
    objects = [sky_dome(view, (cx, cz))]
    # Without the real buildings around, the photo out of the window stands
    # in for them; with them, it would sit in front of them.
    if spec["skylights"] and not spec.get("site"):
        objects.append(view_card(view, spec["skylights"]))
    for obj in objects:
        # Seen by the camera only: to light, the dome would be a shell
        # around the flat shutting out the sun and the sky.
        obj.visible_diffuse = obj.visible_glossy = obj.visible_shadow = False
        obj.visible_transmission = obj.visible_volume_scatter = False
    return objects


def unlit_material(name: str, image: bpy.types.Image, alpha: bool) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 1.0
    if alpha:
        links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
        mat.surface_render_method = "BLENDED"
    return mat


def sky_dome(view: dict, centre: tuple[float, float]) -> bpy.types.Object:
    """A sphere around the flat, painted from the ground up through the horizon to the zenith."""
    import numpy as np

    def lin(colour: str) -> np.ndarray:
        return np.array(hex_rgba(colour)[:3])

    # Below the photo's bottom edge lie the lower floors of the buildings it
    # shows, not more sky: the dome takes their colour up to a little above
    # the horizon, where the photo starts.
    ground, horizon, zenith = lin(view["below"]), lin(view["horizon"]), lin(view["zenith"])
    rows = 256
    elevation = np.linspace(-90, 90, rows)
    up = np.clip((elevation - 4) / 86, 0, 1)[:, None] ** 0.6
    down = np.clip((6 - elevation) / 6, 0, 1)[:, None]
    colour = horizon * (1 - up) + zenith * up
    colour = colour * (1 - down) + ground * down
    pixels = np.ones((rows, 4, 4), np.float32)
    pixels[..., :3] = colour[:, None, :]
    image = bpy.data.images.new("sky", 4, rows, float_buffer=False)
    image.colorspace_settings.name = "sRGB"
    srgb = np.where(pixels[..., :3] <= 0.0031308, pixels[..., :3] * 12.92,
                    1.055 * np.power(pixels[..., :3], 1 / 2.4) - 0.055)
    pixels[..., :3] = srgb
    image.pixels.foreach_set(pixels.ravel())
    image.pack()

    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=DOME_RADIUS)
    bmesh.ops.reverse_faces(bm, faces=bm.faces)  # seen from inside
    layer = bm.loops.layers.uv.verify()
    for face in bm.faces:
        for loop in face.loops:
            z = max(-1.0, min(1.0, loop.vert.co.z / DOME_RADIUS))
            loop[layer].uv = (0.5, 0.5 + math.asin(z) / math.pi)
    mesh = bpy.data.meshes.new("sky")
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.append(unlit_material("sky", image, alpha=False))
    obj = bpy.data.objects.new("sky", mesh)
    bpy.context.collection.objects.link(obj)
    obj.location = P(centre[0], 0.0, centre[1])
    return obj


def view_card(view: dict, skylights: list[dict]) -> bpy.types.Object:
    """
    The photo out of the window, sky cut away, on a plane facing back in
    through the roof windows: centred on them, VIEW_DISTANCE out along their
    slope, as large as the photo's field of view (a phone's 1x lens, 69
    degrees along its long side) at that distance.
    """
    frame = skylights[0]["frame"]
    centres = [Vector(sk["centre"]) for sk in skylights]
    centre = sum(centres, Vector()) / len(centres)
    o = Vector(frame["origin"])
    uc = (centre - o).dot(Vector(frame["u"]))
    vc = (centre - o).dot(Vector(frame["v"]))
    n = (centre - o).dot(Vector(frame["n"])) + VIEW_DISTANCE
    # The photo was shot along the window's tilt, so its foot lies a few
    # degrees above the horizon; lowered so it meets the facade colour below.
    vc -= VIEW_DISTANCE * math.tan(math.radians(6))
    image = bpy.data.images.load(view["view"])
    long_side = 2 * VIEW_DISTANCE * math.tan(math.radians(69 / 2))
    w, h = image.size
    across, up = (long_side * w / h, long_side) if h >= w else (long_side, long_side * h / w)
    # Looking out, the photo's right is the slope's -u and its up is +v.
    corners = [(uc + across / 2, vc - up / 2), (uc - across / 2, vc - up / 2),
               (uc - across / 2, vc + up / 2), (uc + across / 2, vc + up / 2)]
    mesh = bpy.data.meshes.new("view")
    mesh.from_pydata([P(*slope_point(frame, a, b_, n)) for a, b_ in corners], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    for loop, coord in zip(mesh.loops, ((0, 0), (1, 0), (1, 1), (0, 1)), strict=True):
        uv.data[loop.index].uv = coord
    mesh.materials.append(unlit_material("view", image, alpha=True))
    obj = bpy.data.objects.new("view", mesh)
    bpy.context.collection.objects.link(obj)
    return obj



# --------------------------------------------------------------------------
# The neighbourhood
# --------------------------------------------------------------------------


ROOF_INSET = 2.5  # how far a roof steps in from the eaves to its top (a steep Viennese roof)


class SiteMesh:
    """One mesh of the neighbourhood: faces placed with a direction they should face, so
    an open shell (walls without floors) still gets its normals right."""

    def __init__(self, name: str) -> None:
        self.name, self.bm = name, bmesh.new()
        self.uv = self.bm.loops.layers.uv.verify()

    def quad(self, corners: list, uvs: list, outward) -> None:
        verts = [self.bm.verts.new(P(*c)) for c in corners]
        face = self.bm.faces.new(verts)
        face.normal_update()
        if face.normal.dot(P(*outward)) < 0:
            face.normal_flip()
        uv_of = dict(zip(verts, uvs, strict=True))  # by vertex: a flip reorders the loops
        for loop in face.loops:
            loop[self.uv].uv = uv_of[loop.vert]

    def finish(self, mat: bpy.types.Material) -> bpy.types.Object:
        mesh = bpy.data.meshes.new(self.name)
        self.bm.to_mesh(mesh)
        self.bm.free()
        mesh.materials.append(mat)
        obj = bpy.data.objects.new(self.name, mesh)
        bpy.context.collection.objects.link(obj)
        return obj


def textured(name: str, image_path: str | None, colour: str, roughness: float = 0.8):
    mat = material(name, colour, roughness)
    if image_path:
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(image_path)
        links.new(tex.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
    return mat


def neighbourhood(spec: dict) -> list[bpy.types.Object]:
    """
    The buildings around the flat (site.py), as walls with Viennese facades
    and steep tiled roofs with a tin top, the street they stand on, and a
    tiled roof over the flat's own slope as seen out of its windows. They
    shade the room in the bake like the real ones do; their own light is
    baked into their vertices afterwards (bake_neighbourhood).
    """
    s = spec.get("site")
    if not s:
        return []
    bay, storey = s["bay"]
    walls = [SiteMesh(f"site-facade-{i}") for i in range(len(s["facades"]))]
    roofs, tops = SiteMesh("site-roof"), SiteMesh("site-tin")
    for b in s["buildings"]:
        poly = b["polygon"]
        base, eaves, top = b["base"], b["eaves"], b["top"]
        mesh = walls[b["colour"] % len(walls)]
        rows = max(1, math.ceil((eaves - base) / storey))
        u = (b["id"] % 7) * 0.37  # each house starts its window rhythm elsewhere
        for i in range(len(poly)):
            a, c = poly[i], poly[(i + 1) % len(poly)]
            length = math.hypot(c[0] - a[0], c[1] - a[1])
            if length < 0.05:
                continue
            cols = max(1, math.ceil(length / bay))
            out = (c[1] - a[1], 0.0, -(c[0] - a[0]))  # outward of a counter-clockwise outline
            for j in range(cols):
                t0, t1 = j / cols, (j + 1) / cols
                p0 = (a[0] + (c[0] - a[0]) * t0, a[1] + (c[1] - a[1]) * t0)
                p1 = (a[0] + (c[0] - a[0]) * t1, a[1] + (c[1] - a[1]) * t1)
                for k in range(rows):
                    y0 = base + (eaves - base) * k / rows
                    y1 = base + (eaves - base) * (k + 1) / rows
                    # Floors line up with the texture: a floor level every
                    # `storey` above the tall ground floor.
                    v0 = (y0 - base - s["groundFloor"]) / storey
                    v1 = (y1 - base - s["groundFloor"]) / storey
                    ua, ub = (u + length * t0) / bay, (u + length * t1) / bay
                    mesh.quad([(p0[0], y0, p0[1]), (p1[0], y0, p1[1]), (p1[0], y1, p1[1]),
                               (p0[0], y1, p0[1])],
                              [(ua, v0), (ub, v0), (ub, v1), (ua, v1)], out)
            u += length
        area = flat_area(poly)
        perimeter = sum(math.hypot(poly[i][0] - poly[i - 1][0], poly[i][1] - poly[i - 1][1])
                        for i in range(len(poly)))
        inner = None
        if not b["flat"] and top - eaves > 0.7:
            inset = min(ROOF_INSET, 0.8 * area / perimeter)
            candidate = grow(poly, -inset)
            if flat_area(candidate) > 0.15 * area:
                inner = candidate
        if inner is None:  # a flat roof, or one too narrow to step in
            cap(tops, poly, eaves)
            continue
        for i in range(len(poly)):
            a, c = poly[i], poly[(i + 1) % len(poly)]
            ai, ci = inner[i], inner[(i + 1) % len(inner)]
            run = math.hypot(c[0] - a[0], c[1] - a[1])
            slope = math.hypot(math.hypot(ai[0] - a[0], ai[1] - a[1]), top - eaves)
            out = (c[1] - a[1], (run * 0.8), -(c[0] - a[0]))
            roofs.quad([(a[0], eaves, a[1]), (c[0], eaves, c[1]), (ci[0], top, ci[1]),
                        (ai[0], top, ai[1])],
                       [(0, 0), (run, 0), (run, slope), (0, slope)], out)
        cap(tops, inner, top)
    ground = SiteMesh("site-ground")
    room = spec["rooms"][0]
    gx = sum(p[0] for p in room["polygon"]) / len(room["polygon"])
    gz = sum(p[1] for p in room["polygon"]) / len(room["polygon"])
    ring = [(gx + 220 * math.cos(a), gz + 220 * math.sin(a))
            for a in (2 * math.pi * i / 48 for i in range(48))]
    cap(ground, ring, s["street"] - 0.02)
    skin = SiteMesh("site-roof-own")
    own_roof(skin, spec)

    objects = [m.finish(textured(m.name, path, "#d8c8a6"))
               for m, path in zip(walls, s["facades"], strict=True)]
    objects.append(roofs.finish(textured("site-roof", s["tiles"], "#8c4c3c")))
    objects.append(skin.finish(bpy.data.materials["site-roof"]))
    objects.append(tops.finish(material("site-tin", "#80848a", 0.5)))
    objects.append(ground.finish(material("site-ground", "#7c7a76", 0.9)))
    return objects


def flat_area(poly) -> float:
    return 0.5 * sum(poly[i - 1][0] * poly[i][1] - poly[i][0] * poly[i - 1][1]
                     for i in range(len(poly)))


def cap(mesh: SiteMesh, poly, y: float) -> None:
    """A level polygon at height y facing up, textured in metres."""
    verts = [mesh.bm.verts.new(P(x, y, z)) for x, z in poly]
    face = mesh.bm.faces.new(verts)
    face.normal_update()
    if face.normal.dot(P(0, 1, 0)) < 0:
        face.normal_flip()
    for loop in face.loops:
        loop[mesh.uv].uv = (loop.vert.co.x, loop.vert.co.y)


def own_roof(mesh: SiteMesh, spec: dict) -> None:
    """Tiles over the flat's own slope, down past the knee wall to the eaves, open at its
    roof windows: what one sees of one's own roof looking out of them."""
    room = spec["rooms"][0]
    for index, c in enumerate(room.get("ceilings") or []):
        if is_flat(c):
            continue
        frame = next((sk["frame"] for sk in spec["skylights"] if sk["ceiling"] == index), None)
        frame = frame or slope_frame(c)
        holes = [sk["hole"] for sk in spec["skylights"] if sk["ceiling"] == index]
        eaves = 0.6
        n = SLAB_T + 0.03
        for u0, u1, v0, v1 in wall_cells(frame["lu"], frame["lv"] + eaves,
                                          [(a, b_, lo + eaves, hi + eaves)
                                           for a, b_, lo, hi in holes]):
            corners = [slope_point(frame, u, v - eaves, n) for u, v in
                       ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
            mesh.quad(corners, [(u0, v0), (u1, v0), (u1, v1), (u0, v1)], frame["n"])


def light_set(label: str) -> str:
    """
    The attribute that holds a time of day's light in vertices. Its leading
    underscore exports it as a glTF custom attribute (`_LIGHT_1730`):
    exported as vertex colours (COLOR_n), every set but the last came out
    white, so the houses outside stood unshaded at all but one time.
    """
    return f"_light_{label}"


#: The colour attribute the vertex bakes write into, before store_light.
BAKING = "baking"


def store_light(mesh: bpy.types.Mesh, label: str, rgb, domain: str) -> None:
    """
    Keep a time of day's light (RGB, 0-1) in `mesh` as its light set, square
    rooted (the viewer squares it): published at 8 bits, the dark keeps
    enough levels. Three channels, not a colour attribute's four: seven sets
    on 120,000 vertices were most of the model.
    """
    import numpy as np

    attr = mesh.attributes.new(light_set(label), "FLOAT_VECTOR", domain)
    attr.data.foreach_set("vector", np.sqrt(np.clip(rgb, 0.0, 1.0)).astype(np.float32).ravel())
    mesh.color_attributes.remove(mesh.color_attributes[BAKING])


def bake_neighbourhood(objects: list[bpy.types.Object], label: str,
                       scale: float | None = None) -> float:
    """
    The neighbourhood's light (sun, sky, shadows, bounce) into its vertices,
    scaled so a sunlit wall shows its plaster at full strength: the viewer
    shows it unlit as texture x vertex colour, at an outdoor exposure, so
    the view stays visible from a room exposed for its inside.
    """
    import numpy as np

    for obj in objects:
        attr = obj.data.color_attributes.new(BAKING, "FLOAT_COLOR", "CORNER")
        obj.data.color_attributes.active_color = attr
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"DIRECT", "INDIRECT"},
                        target="VERTEX_COLORS")
    values = []
    for obj in objects:
        data = np.empty(len(obj.data.color_attributes[BAKING].data) * 4, np.float32)
        obj.data.color_attributes[BAKING].data.foreach_get("color", data)
        values.append(data.reshape(-1, 4)[:, :3])
    if scale is None:  # the first time of day sets the scale for all of them
        every = np.concatenate([v.max(axis=1) for v in values])
        scale = 0.95 / max(float(np.percentile(every, 95)), 1e-6)
    for obj, rgb in zip(objects, values, strict=True):
        store_light(obj.data, label, rgb * scale, "CORNER")
    return scale


def door(b: Builder, wall: dict, lo: float, hi: float, top: float, framed: bool = True,
         latch: str = "end", depth: float | None = None) -> None:
    """
    A door in the wall from lo to hi: its leaf, the handle at the `latch`
    end of the wall ("start" or "end"), and either an architrave round it
    (`framed`) or, for a concealed-frame door, the leaf flush with the wall
    and only a 4 mm dark shadow gap round it. The leaf stands `depth` into
    the wall (default its middle): a flat's front door hangs near the hall's
    face, not halfway into a 43 cm outer wall.
    """
    mid = WALL_T / 2 if depth is None else depth
    if framed:
        face = mid - 0.02
        wall_box(b, "trim", wall, lo + 0.005, hi - 0.005, 0, top - 0.005, face, mid + 0.02)
    else:
        # The gap's dark back sits behind the leaf and a hair inside the
        # opening's reveals, so no faces are coplanar.
        face = 0.0
        wall_box(b, "trim", wall, lo + 0.004, hi - 0.004, 0.006, top - 0.004, face, 0.04)
        wall_box(b, "gap", wall, lo + 0.001, hi - 0.001, 0, top - 0.001, 0.006, 0.05)
    # What lies behind a door (a bathroom, the stairwell) is not modelled, so
    # back the doorway: its leaf's 5 mm gaps otherwise let the sky shine in.
    wall_box(b, "wall", wall, lo, hi, 0, top, WALL_T - 0.01, WALL_T)
    # And a threshold: the floor stops at the wall line, so under the leaf
    # one looked down past the floor's edge as a bright line. 3 mm proud,
    # not flush, so its top is not in the floor's plane.
    wall_box(b, "trim", wall, lo, hi, -SLAB_T, 0.003, 0, WALL_T)
    # A lever handle on the room side, at the latch edge: rose, neck, lever
    # pointing back towards the hinges.
    hu, back = (hi - 0.075, -1) if latch == "end" else (lo + 0.075, 1)
    wall_box(b, "steel", wall, hu - 0.025, hu + 0.025, 1.025, 1.075, face - 0.008, face)
    wall_box(b, "steel", wall, hu - 0.01, hu + 0.01, 1.04, 1.06, face - 0.058, face - 0.008)
    wall_box(b, "steel", wall, min(hu - 0.01 * back, hu + 0.14 * back),
             max(hu - 0.01 * back, hu + 0.14 * back), 1.04, 1.06, face - 0.075, face - 0.058)
    if not framed:
        return
    # Sides stop where the head starts: overlapping coplanar boxes render black.
    wall_box(b, "trim", wall, lo - TRIM_W, lo, 0, top, -TRIM_D, 0)
    wall_box(b, "trim", wall, hi, hi + TRIM_W, 0, top, -TRIM_D, 0)
    wall_box(b, "trim", wall, lo - TRIM_W, hi + TRIM_W, top, top + TRIM_W, -TRIM_D, 0)


def doorway(b: Builder, wall: dict, lo: float, hi: float, top: float, hinge: str = "start",
            swing: int = 1, leaf: bool = True, framed: bool = True) -> None:
    """
    A doorway in a partition (an inner wall, centred on its line), as the
    flat's are photographed: lined reveals, an architrave on both faces, and
    its leaf standing open square to the wall on the `swing` side (1: the
    side the wall's normal points to), hinged at its `hinge` end ("start" or
    "end" of the wall), a lever handle on each face by its free edge. A
    passage without a door has no `leaf`; one cased in oak above has no
    architrave (`framed`).
    """
    t, g = wall.get("thickness", PARTITION_T) / 2, DOOR_LINING
    wall_box(b, "trim", wall, lo, lo + g, 0, top, -t, t)
    wall_box(b, "trim", wall, hi - g, hi, 0, top, -t, t)
    wall_box(b, "trim", wall, lo + g, hi - g, top - g, top, -t, t)
    # Sides stop where the head starts: overlapping coplanar boxes render black.
    for v0, v1 in ((-t - TRIM_D, -t), (t, t + TRIM_D)) if framed else ():
        wall_box(b, "trim", wall, lo - TRIM_W, lo, 0, top, v0, v1)
        wall_box(b, "trim", wall, hi, hi + TRIM_W, 0, top, v0, v1)
        wall_box(b, "trim", wall, lo - TRIM_W, hi + TRIM_W, top, top + TRIM_W, v0, v1)
    if not leaf:
        return
    width = hi - lo - 2 * g
    u = lo + g + 0.002 if hinge == "start" else hi - g - 0.002 - LEAF_T
    # Clear of the architrave on its face, so no two faces share a plane.
    near = t + TRIM_D + 0.002
    v0, v1 = (near, near + width) if swing > 0 else (-near - width, -near)
    # The leaf and its handles are their own object (DOOR_LEAVES), which the
    # viewer turns shut about the hinge.
    leaf = Recorder()
    wall_box(leaf, "trim", wall, u, u + LEAF_T, 0.008, top - g - 0.004, v0, v1)
    # The lever points back towards the hinges, along the leaf.
    free, back = (v1 - 0.07, -1) if swing > 0 else (v0 + 0.07, 1)
    for out in (-1, 1):
        face = u if out < 0 else u + LEAF_T
        neck = sorted((face, face + out * 0.05))
        wall_box(leaf, "steel", wall, *neck, 1.04, 1.06, free - 0.01, free + 0.01)
        tip = sorted((neck[0], neck[0] - 0.016) if out < 0 else (neck[1], neck[1] + 0.016))
        wall_box(leaf, "steel", wall, *tip, 1.04, 1.06,
                 *sorted((free - 0.01 * back, free + 0.14 * back)))
    (dx, dz), (nx, nz), _ = wall_frame(wall["start"], wall["end"])
    ax, az = wall["start"]

    def at(uu: float, vv: float) -> tuple[float, float]:
        return ax + dx * uu + nx * vv, az + dz * uu + nz * vv

    mid = u + LEAF_T / 2
    hinge_v, far_v = (v0, v1) if swing > 0 else (v1, v0)
    shut = (lo + g, hi - g) if hinge == "start" else (hi - g, lo + g)
    pivot, turn = swing_pivot(at(mid, hinge_v), at(mid, far_v), at(shut[0], 0), at(shut[1], 0))
    DOOR_LEAVES.append({"boxes": leaf.boxes, "pivot": pivot, "open": 0.0, "close": turn,
                        "at": at((lo + hi) / 2, 0), "width": hi - lo})


def leaf_objects(leaves: list[dict], mats: dict[str, bpy.types.Material]) -> list[bpy.types.Object]:
    """
    Each leaf as its own object, its origin on the pivot, tagged with a `door`
    extra (glTF): the turns from its built pose to open and to closed, and the
    doorway's centre and width when it stands in one (doors.ts).
    """
    objects = []
    for i, leaf in enumerate(leaves):
        px, pz = leaf["pivot"]
        origin = P(px, 0.0, pz)
        bm = bmesh.new()
        keys: list[str] = []
        for key, corners in leaf["boxes"]:
            if key not in keys:
                keys.append(key)
            v = [bm.verts.new(c - origin) for c in corners]
            for face in BOX_FACES:
                bm.faces.new([v[j] for j in face]).material_index = keys.index(key)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        mesh = bpy.data.meshes.new(f"door-{i}")
        bm.to_mesh(mesh)
        bm.free()
        for key in keys:
            mesh.materials.append(mats[key])
        obj = bpy.data.objects.new(f"door-{i}", mesh)
        bpy.context.collection.objects.link(obj)
        obj.location = origin
        tag = {"open": round(leaf["open"], 5), "close": round(leaf["close"], 5)}
        if leaf.get("at"):
            tag.update(at=[round(c, 4) for c in leaf["at"]], width=round(leaf["width"], 4))
        obj["door"] = tag
        plain_uvs(obj)
        objects.append(obj)
    return objects


def window(b: Builder, wall: dict, lo: float, hi: float, bottom: float, top: float,
           glass: bool = True) -> None:
    v0, v1 = WALL_T / 2 - FRAME_D / 2, WALL_T / 2 + FRAME_D / 2
    wall_box(b, "trim", wall, lo, lo + FRAME_W, bottom, top, v0, v1)
    wall_box(b, "trim", wall, hi - FRAME_W, hi, bottom, top, v0, v1)
    wall_box(b, "trim", wall, lo, hi, bottom, bottom + FRAME_W, v0, v1)
    wall_box(b, "trim", wall, lo, hi, top - FRAME_W, top, v0, v1)
    if glass:
        wall_box(b, "glass", wall, lo + FRAME_W, hi - FRAME_W, bottom + FRAME_W, top - FRAME_W,
                 WALL_T / 2 - 0.004, WALL_T / 2 + 0.004)
    # An interior sill board, the detail that makes a window read as built.
    if bottom > 0.1:
        wall_box(b, "trim", wall, lo - 0.03, hi + 0.03, bottom - 0.03, bottom, -0.04, WALL_T / 2)


def skirting(b: Builder, wall: dict, doors: list[dict], length: float,
             v0: float = -SKIRTING_D, v1: float = 0.0) -> None:
    """Skirting along the wall's face from `v0` to `v1` (the room side), broken at its doors."""
    u = 0.0
    for o in doors:
        trim = TRIM_W if o.get("frame", True) else 0.0
        lo = max(0.0, o["offset"] - o["width"] / 2 - trim)
        if lo > u:
            wall_box(b, "trim", wall, u, lo, 0, SKIRTING_H, v0, v1)
        u = o["offset"] + o["width"] / 2 + trim
    if u < length:
        wall_box(b, "trim", wall, u, length, 0, SKIRTING_H, v0, v1)


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


def mark_lining(obj: bpy.types.Object, skylights: list[dict], width: float) -> None:
    """
    Flag as unseen the roof windows' faces that face away from their shaft
    (the boards' backs on the slab's cut, nobody sees them) and those no
    wider than `width` (the boards' edges: on so few texels, beside the slab,
    their light baked in dark blotches). An edge then takes the light of the
    board's face beside it; with the backs still seen, some took a back's
    black instead.
    """
    axes = [(P(*sk["centre"]), P(*sk["frame"]["n"]).normalized()) for sk in skylights]
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    unseen = bm.faces.layers.int.new("unseen")
    for face in bm.faces:
        c = face.calc_center_median()
        at, axis = min(axes, key=lambda a: (c - a[0]).length)
        radial = (c - at) - axis * (c - at).dot(axis)
        away = radial.length > 1e-6 and face.normal.dot(radial.normalized()) > 0.5
        thin = min(e.calc_length() for e in face.edges) <= width + 1e-4
        face[unseen] = int(away or thin)
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

FRONT_T, GAP, GRIP = 0.019, 0.003, 0.03  # front thickness, gap between fronts, grip channel

#: Rounded edges, for the pieces that have them: (bevel width, segments). A
#: bed's at 2 cm left its duvet and pillows looking sawn; at 3.5 they are soft.
SOFTNESS = {"bed": (0.025, 3), "air-con": (0.09, 10), "tv": (0.003, 1),
            "bed-frame": (0.004, 1), "washer": (0.012, 2),
            "box-bed": (0.035, 4), "towels": (0.012, 2), "folding-bed": (0.018, 3),
            "box-cushion": (0.07, 5), "lounge-chair": (0.02, 3)}

Part = tuple[str, tuple[float, float, float, float, float, float]]


def column_widths(x0: float, x1: float, columns: list) -> list[float]:
    """The widths of a run of `columns` (see `fronts`), a width of 0 sharing what is left."""
    fixed = sum(c[0] for c in columns if c[0])
    free = sum(1 for c in columns if not c[0])
    share = (x1 - x0 - fixed) / free if free else 0.0
    return [c[0] or share for c in columns]


def fronts(x0: float, x1: float, z0: float, z1: float, y: float, columns: list,
           surface: str = "gloss", grips: bool = True, style: dict | None = None) -> list[Part]:
    """
    Door and drawer fronts standing on a carcass face, their front at y.
    `columns` are (width, count) left to right, a width of 0 sharing what is
    left; each column's fronts are stacked with a recessed aluminium grip
    channel between them (`grips`), as on a handleless kitchen, or a plain gap.
    `style` dresses each front (front_style): a shaker frame or glazing, and
    a bar handle.
    """
    parts: list[Part] = []
    x = x0
    for k, (column, width) in enumerate(zip(columns, column_widths(x0, x1, columns), strict=True)):
        count = column[1]
        between = GRIP if grips else GAP
        height = (z1 - z0 - between * (count - 1)) / count
        for i in range(count):
            za = z0 + i * (height + between)
            box = (x + GAP / 2, x + width - GAP / 2, y, y + FRONT_T, za, za + height)
            parts += front_style(surface, box, style, k) if style else [(surface, box)]
            if grips and i < count - 1:
                parts.append(("alu", (x, x + width, y + 0.004, y + FRONT_T + 0.01,
                                      za + height + 0.001, za + height + GRIP - 0.001)))
        x += width
    return parts


#: A shaker front's frame and a glazed door's bars, in metres across.
STILE, MULLION, PROUD = 0.06, 0.022, 0.005


def front_style(surface: str, box: tuple, style: dict, column: int) -> list[Part]:
    """
    One front dressed as photographed (kitchen 0076-0079): `frame` a shaker
    frame proud of a flat panel, or `glass` frosted panes behind a frame and
    bars (`panes`: across, up); `handle` a black bar, upright near the edge
    the door opens at (even columns their right, odd their left; at the
    bottom of a wall unit's door, `handleLow`), across the top of a drawer.
    """
    x0, x1, y0, y1, z0, z1 = box
    parts: list[Part] = []
    if style.get("glass"):
        across, up = style.get("panes", (2, 3))
        parts.append(("glass-frost", (x0 + STILE, x1 - STILE, y0 + 0.008, y1,
                                      z0 + STILE, z1 - STILE)))
        parts += [(surface, (x0, x0 + STILE, y0, y1, z0, z1)),
                  (surface, (x1 - STILE, x1, y0, y1, z0, z1)),
                  (surface, (x0 + STILE, x1 - STILE, y0, y1, z0, z0 + STILE)),
                  (surface, (x0 + STILE, x1 - STILE, y0, y1, z1 - STILE, z1))]
        inner_w, inner_h = x1 - x0 - 2 * STILE, z1 - z0 - 2 * STILE
        for j in range(1, across):
            xm = x0 + STILE + inner_w * j / across
            parts.append((surface, (xm - MULLION / 2, xm + MULLION / 2, y0 + 0.004, y1 - 0.004,
                                    z0 + STILE, z1 - STILE)))
        for j in range(1, up):
            zm = z0 + STILE + inner_h * j / up
            parts.append((surface, (x0 + STILE, x1 - STILE, y0 + 0.004, y1 - 0.004,
                                    zm - MULLION / 2, zm + MULLION / 2)))
    elif style.get("frame") and min(x1 - x0, z1 - z0) > 3 * STILE:
        parts.append((surface, box))
        parts += [(surface, (x0, x0 + STILE, y0 - PROUD, y0, z0, z1)),
                  (surface, (x1 - STILE, x1, y0 - PROUD, y0, z0, z1)),
                  (surface, (x0 + STILE, x1 - STILE, y0 - PROUD, y0, z0, z0 + STILE)),
                  (surface, (x0 + STILE, x1 - STILE, y0 - PROUD, y0, z1 - STILE, z1))]
    else:
        parts.append((surface, box))
    if style.get("handle"):
        face = y0 - (PROUD if style.get("frame") else 0.0)
        if z1 - z0 < 0.35 or style.get("handleTop"):  # a drawer, a dishwasher: across its top
            xm, zm = (x0 + x1) / 2, z1 - 0.045
            parts.append(("handle", (xm - 0.08, xm + 0.08, face - 0.02, face - 0.008,
                                     zm - 0.006, zm + 0.006)))
            parts += [("handle", (xa - 0.005, xa + 0.005, face - 0.012, face,
                                  zm - 0.005, zm + 0.005))
                      for xa in (xm - 0.07, xm + 0.07)]
        else:
            xh = x1 - 0.035 if column % 2 == 0 else x0 + 0.035
            low = style.get("handleLow")
            za, zb = (z0 + 0.05, z0 + 0.25) if low else (z1 - 0.25, z1 - 0.05)
            parts.append(("handle", (xh - 0.006, xh + 0.006, face - 0.02, face - 0.008, za, zb)))
            parts += [("handle", (xh - 0.005, xh + 0.005, face - 0.012, face,
                                  zc - 0.005, zc + 0.005))
                      for zc in (za + 0.01, zb - 0.01)]
    return parts


def column_span(x0: float, x1: float, columns: list, tag: str) -> tuple[float, float] | None:
    """Where along a run of `columns` (see `fronts`) the one tagged `tag` lies."""
    x = x0
    for column, width in zip(columns, column_widths(x0, x1, columns), strict=True):
        if tag in column[2:]:
            return x, x + width
        x += width
    return None


#: The sink as photographed (kitchen2): a white composite inset sink, its
#: rounded bowl over the sink cabinet and a ribbed drainboard to its right,
#: a steel tap behind. Its bowl's opening across and deep, its depth, the
#: bowl's corner radius, the flange round it, and the drainboard's width.
BOWL_W, BOWL_D, BOWL_DEPTH, BOWL_R, FLANGE, DRAINBOARD = 0.38, 0.36, 0.24, 0.055, 0.035, 0.42


def sink_outline(y0: float, lo: float,
                 hi: float) -> tuple[float, float, float, float, float, float]:
    """The bowl's centre (x, y) and the sink's outer edge (x0, x1, y0, y1), in the base's frame."""
    cx, cy = (lo + hi) / 2, y0 + 0.06 + FLANGE + BOWL_D / 2
    return (cx, cy, cx - BOWL_W / 2 - FLANGE, cx + BOWL_W / 2 + FLANGE + DRAINBOARD,
            cy - BOWL_D / 2 - FLANGE, cy + BOWL_D / 2 + FLANGE)


def sink(x0: float, x1: float, y0: float, y1: float, h: float, lo: float,
         hi: float) -> list[Part]:
    """The worktop round the sink's cut-out; the sink itself is build_sink's."""
    _, _, hx0, hx1, hy0, hy1 = sink_outline(y0, lo, hi)
    # Inside the flange's rounded corners, so the cut never shows past them.
    hx0, hx1, hy0, hy1 = hx0 + 0.03, hx1 - 0.03, hy0 + 0.03, hy1 - 0.03
    top = h - 0.04
    # The strip in front of the sink the worktop's whole width, so its front
    # edge is one face as its neighbours' are: in three pieces, the short
    # middle one under the sink baked dark (0080 has the worktop's edge even).
    return [("stone", (x0, x1, y0 - 0.02, hy0, top, h)),
            ("stone", (x0, hx0, hy0, y1, top, h)),
            ("stone", (hx1, x1, hy0, y1, top, h)),
            ("stone", (hx0, hx1, hy1, y1, top, h))]


def sink_carcass(carcass: Part, y0: float, h: float, lo: float, hi: float) -> list[Part]:
    """
    The sink cabinet's carcass with a well cut down round the bowl: whole,
    its top showed 4 cm under the worktop as the bowl's floor.
    """
    material, (x0, x1, cy0, cy1, z0, z1) = carcass
    cx, cy, *_ = sink_outline(y0, lo, hi)
    hx0, hx1 = cx - BOWL_W / 2 - 0.01, cx + BOWL_W / 2 + 0.01
    hy0, hy1 = cy - BOWL_D / 2 - 0.01, cy + BOWL_D / 2 + 0.01
    low = h - BOWL_DEPTH - 0.02
    return [(material, (x0, x1, cy0, cy1, z0, low)),
            (material, (x0, hx0, cy0, cy1, low, z1)),
            (material, (hx1, x1, cy0, cy1, low, z1)),
            (material, (hx0, hx1, cy0, hy0, low, z1)),
            (material, (hx0, hx1, hy1, cy1, low, z1))]


def rounded_rect(cx: float, cy: float, half_w: float, half_d: float, r: float,
                 n: int) -> list[tuple[float, float]]:
    """`n` points round a rounded rectangle, evenly along its edge, from its right side's middle."""
    r = max(min(r, half_w, half_d), 1e-4)
    straight_w, straight_d = 2 * (half_w - r), 2 * (half_d - r)
    arc = math.pi * r / 2
    length = 2 * (straight_w + straight_d) + 4 * arc
    corners = [(half_w - r, half_d - r), (-(half_w - r), half_d - r),
               (-(half_w - r), -(half_d - r)), (half_w - r, -(half_d - r))]
    # The edge as runs: (start point, direction) of the straights, arcs round the corners.
    pts = []
    for k in range(n):
        t = length * k / n
        t = (t + straight_d / 2) % length  # start at the right side's middle
        for side in range(4):
            run = straight_d if side % 2 == 0 else straight_w
            if t < run:
                if side == 0:
                    x, y = half_w, -(half_d - r) + t
                elif side == 1:
                    x, y = (half_w - r) - t, half_d
                elif side == 2:
                    x, y = -half_w, (half_d - r) - t
                else:
                    x, y = -(half_w - r) + t, -half_d
                break
            t -= run
            if t < arc:
                ccx, ccy = corners[side]
                angle = (side * math.pi / 2) + t / r
                x, y = ccx + r * math.cos(angle), ccy + r * math.sin(angle)
                break
            t -= arc
        pts.append((cx + x, cy + y))
    return pts


def build_sink(item: dict, piece: bpy.types.Object, surfaces: dict) -> bpy.types.Object | None:
    """
    The kitchen base's sink, as its own object lit per vertex (its curves
    smooth): the bowl lofted through rounded sections from its flange down
    its walls to a floor falling to the drain, a ribbed drainboard, and a
    round steel tap, an L with its spout over the bowl.
    """
    if item.get("finish") != "kitchen-base":
        return None
    w, h, d = item["size"]
    x0, x1, y0 = -w / 2, w / 2, -d / 2
    span = column_span(x0, x1, (item.get("options") or {}).get("columns", [[0, 1]]), "sink")
    if not span:
        return None
    cx, cy, sx0, sx1, sy0, sy1 = sink_outline(y0, *span)
    white, steel = 0, 1
    bm = bmesh.new()
    n = 64
    hw, hd = BOWL_W / 2, BOWL_D / 2
    # The flange's outer edge square (barely rounded), so the drainboard
    # meets it along a straight edge: rounded, it left a crack to the cut-out.
    rings = [(hw + FLANGE, hd + FLANGE, 0.002, h),
             (hw + FLANGE, hd + FLANGE, 0.002, h + 0.006),
             (hw + 0.006, hd + 0.006, BOWL_R + 0.006, h + 0.006),
             (hw, hd, BOWL_R, h - 0.004),
             # Walls near plumb, so the depth shows from above.
             (hw - 0.002, hd - 0.002, BOWL_R - 0.002, h - 0.08),
             (hw - 0.004, hd - 0.004, BOWL_R - 0.004, h - 0.16),
             (hw - 0.008, hd - 0.008, BOWL_R - 0.008, h - BOWL_DEPTH + 0.025),
             (hw - 0.022, hd - 0.022, BOWL_R - 0.02, h - BOWL_DEPTH)]
    loops = [[bm.verts.new((px, py, z))
              for px, py in rounded_rect(cx, cy, a, b, r, n)] for a, b, r, z in rings]
    for upper, lower in zip(loops, loops[1:], strict=False):
        for k in range(n):
            bm.faces.new((upper[k], upper[(k + 1) % n], lower[(k + 1) % n], lower[k]))
    drain = [bm.verts.new((cx + 0.04 * math.cos(2 * math.pi * k / n),
                           cy + 0.04 * math.sin(2 * math.pi * k / n), h - BOWL_DEPTH - 0.004))
             for k in range(n)]
    for k in range(n):  # the floor, falling to the drain
        bm.faces.new((loops[-1][k], loops[-1][(k + 1) % n], drain[(k + 1) % n], drain[k]))
    hub = bm.verts.new((cx, cy, h - BOWL_DEPTH - 0.006))
    for k in range(n):
        bm.faces.new((drain[k], drain[(k + 1) % n], hub)).material_index = steel
    # Facing up out of the bowl: the floor's normal points up.
    floor = next(f for f in hub.link_faces)
    floor.normal_update()
    if floor.normal.z < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    # The drainboard beside it, its ribs running along it.
    board_x0 = cx + hw + FLANGE - 0.001
    boxes = [(white, (board_x0, sx1, sy0, sy1, h, h + 0.0059))]
    for k in range(7):
        yy = sy0 + 0.07 + k * (sy1 - sy0 - 0.14) / 6
        boxes.append((white, (board_x0 + 0.04, sx1 - 0.04, yy - 0.004, yy + 0.004,
                              h + 0.006, h + 0.01)))
    for material, (bx0, bx1, by0, by1, bz0, bz1) in boxes:
        cube = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, verts=cube["verts"], vec=(bx1 - bx0, by1 - by0, bz1 - bz0))
        bmesh.ops.translate(bm, verts=cube["verts"],
                            vec=((bx0 + bx1) / 2, (by0 + by1) / 2, (bz0 + bz1) / 2))
        for face in {f for v in cube["verts"] for f in v.link_faces}:
            face.material_index = material
    # The tap: a round base, a column, a spout reaching out over the bowl, a lever.
    tx, ty = cx + hw - 0.02, sy1 + 0.03
    for a, b, r0, r1 in (((tx, ty, h), (tx, ty, h + 0.06), 0.026, 0.024),
                         ((tx, ty, h + 0.06), (tx, ty, h + 0.30), 0.0145, 0.0145),
                         ((tx, ty + 0.006, h + 0.29), (tx, ty - 0.2, h + 0.29), 0.012, 0.011),
                         ((tx, ty - 0.2, h + 0.296), (tx, ty - 0.2, h + 0.255), 0.013, 0.012),
                         ((tx, ty, h + 0.11), (tx + 0.08, ty, h + 0.135), 0.006, 0.005)):
        for face in tube(bm, a, b, r0, r1, segments=20):
            face.material_index = steel
    mesh = bpy.data.meshes.new(f"{item['id']}-sink")
    bm.to_mesh(mesh)
    bm.free()
    # The sink and tap in the piece's own surfaces, where it names them (a black sink).
    remap = (item.get("options") or {}).get("surfaces", {})
    for name in (remap.get("basin", "basin"), remap.get("steel", "steel")):
        mesh.materials.append(surface_material(name, surfaces[name]))
    mesh.shade_smooth()
    mesh.set_sharp_from_angle(angle=math.radians(50))
    obj = bpy.data.objects.new(mesh.name, mesh)
    bpy.context.collection.objects.link(obj)
    plain_uvs(obj)
    obj.matrix_world = piece.matrix_world.copy()
    VERTEX_LIT.add(obj.name)
    return obj


def finish_parts(finish: str, w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    A piece drawn from boxes, in its own frame: x across, y depth with the
    front at -d/2, z up from 0. Built as the photos show it: handleless high
    gloss fronts with grip channels, a worktop with its sink, an open
    wardrobe with its shelves, rail and drawers. Parts may reach outside the
    scanned box (an overhang). The island's top and the stools are not boxes:
    island_top, stool.
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    plinth = ("plinth", (x0, x1, y0 + 0.06, y1, 0, 0.1))
    carcass = ("white", (x0, x1, y0 + FRONT_T, y1, 0.1, h - 0.04))
    top_grip = ("alu", (x0, x1, y0 + 0.004, y0 + FRONT_T + 0.01,
                        h - 0.04 - GRIP + 0.001, h - 0.041))
    # `worktopTrim`: the worktop cut back at its ends (x0, x1) where a corner
    # unit's front overhang lies on it: two tops in one plane flickered.
    trim0, trim1 = opts.get("worktopTrim", (0.0, 0.0))
    worktop = ("stone", (x0 + trim0, x1 - trim1, y0 - 0.02, y1, h - 0.04, h))
    xi0, xi1 = x0 + 0.018, x1 - 0.018  # inside a carcass's sides

    if finish == "kitchen-base":
        columns = opts.get("columns", [[0, 1]])
        span = column_span(x0, x1, columns, "sink")
        style = opts.get("style")
        parts = [plinth, *(sink_carcass(carcass, y0, h, *span) if span else [carcass]),
                 *([] if style else [top_grip]),
                 *fronts(x0, x1, 0.1 + GAP, h - 0.04 - (GAP if style else GRIP), y0,
                         [c[:2] for c in columns], grips=not style, style=style)]
        return parts + (sink(x0, x1, y0, y1, h, *span) if span else [worktop])
    if finish == "oven":
        zt = h - 0.04 - GRIP - GAP
        return [plinth, carcass, top_grip, worktop,
                ("gloss", (x0 + GAP / 2, x1 - GAP / 2, y0, y0 + FRONT_T, 0.1 + GAP, 0.3)),
                ("black", (x0 + 0.004, x1 - 0.004, y0, y0 + FRONT_T, 0.31, zt)),
                ("steel", (x0 + 0.004, x1 - 0.004, y0 - 0.002, y0 + 0.005, zt - 0.075, zt)),
                ("steel", (x0 + 0.07, x1 - 0.07, y0 - 0.05, y0 - 0.03, zt - 0.13, zt - 0.112)),
                ("steel", (x0 + 0.09, x0 + 0.105, y0 - 0.03, y0, zt - 0.13, zt - 0.112)),
                ("steel", (x1 - 0.105, x1 - 0.09, y0 - 0.03, y0, zt - 0.13, zt - 0.112))]
    if finish == "hob":
        # Induction with its extractor (0080): black glass framed in steel,
        # the extractor's slot down its middle, its controls along the front.
        return [("steel", (x0, x1, y0, y1, 0, h)),
                ("granite", (x0 + 0.004, x1 - 0.004, y0 + 0.004, y1 - 0.004, h, h + 0.001)),
                ("black", (x0 + 0.05, x1 - 0.05, -0.022, 0.022, h + 0.001, h + 0.002)),
                ("vent", (-0.12, 0.12, y0 + 0.025, y0 + 0.05, h + 0.001, h + 0.0015))]
    if finish == "kitchen-tall" and opts.get("style"):
        # Framed doors and black bars (0075): a tall door over a short one.
        return [plinth, ("white", (x0, x1, y0 + FRONT_T, y1, 0.1, h)),
                *fronts(x0, x1, 0.1 + GAP, h - 0.004, y0, [[0, 2]], grips=False,
                        style=opts["style"])]
    if finish == "kitchen-tall":
        return [plinth, ("white", (x0, x1, y0 + FRONT_T, y1, 0.1, h)),
                *fronts(x0, x1, 0.1 + GAP, 0.86, y0, [[0, 2]]),
                ("alu", (x0, x1, y0 + 0.004, y0 + FRONT_T + 0.01, 0.861, 0.889)),
                ("gloss", (x0 + GAP / 2, x1 - GAP / 2, y0, y0 + FRONT_T, 0.89, 1.955)),
                ("gloss", (x0 + GAP / 2, x1 - GAP / 2, y0, y0 + FRONT_T, 1.958, h - 0.002))]
    if finish == "kitchen-wall":
        rows, columns = opts.get("rows", 1), opts.get("columns", 1)
        # A count of equal doors, or their widths left to right (0: a share of the rest).
        columns = [[0, 1]] * columns if isinstance(columns, int) else [[c, 1] for c in columns]
        parts = [("white", (x0, x1, y0 + FRONT_T, y1, 0, h))]
        height = h / rows
        style = {**opts.get("style", {}), "handleLow": True} if opts.get("style") else None
        for r in range(rows):
            parts += fronts(x0, x1, r * height + GAP / 2, (r + 1) * height - GAP / 2, y0,
                            columns, grips=False, style=style)
        return parts
    if finish == "switch":
        # A light switch (0075, 0076): a white frame, its rockers a hair proud,
        # one over the other.
        rock = (h - 0.02) / max(1, opts.get("gangs", 2))
        parts = [("white", (x0, x1, y0 + 0.004, y1, 0, h))]
        for k in range(opts.get("gangs", 2)):
            z = 0.01 + k * rock
            parts.append(("white", (x0 + 0.012, x1 - 0.012, y0, y0 + 0.004,
                                    z + 0.004, z + rock - 0.004)))
        return parts
    if finish == "shelves":
        parts = []
        for i, z in enumerate((0.0, (h - 0.035) / 2, h - 0.035)):
            parts.append(("oak-warm", (x0, x1, y0, y1, z, z + 0.035)))
            if i < 2:  # a picture frame stood on each of the lower two
                f0 = x0 + 0.05 + 0.06 * i
                parts += [("frame", (f0, f0 + 0.2, y1 - 0.06, y1 - 0.045, z + 0.035, z + 0.29)),
                          ("picture", (f0 + 0.02, f0 + 0.18, y1 - 0.062, y1 - 0.06,
                                       z + 0.055, z + 0.27))]
        return parts
    if finish == "panel":
        return [(opts.get("surface", "oak-warm"), (x0, x1, y0, y1, 0, h))]
    if finish == "desk":
        # A plain desk (0056): a white top on slim legs, nothing under it;
        # `panel`: its right end (+x) closed by a side panel to the floor.
        top, leg, legs = 0.025, 0.04, opts.get("legs", "white-matt")
        panel = opts.get("panel", False)
        parts = [(opts.get("top", "white-matt"), (x0, x1, y0, y1, h - top, h)),
                 (legs, (x0 + 0.03, x1 - 0.03, y1 - 0.05, y1 - 0.03, h - top - 0.08, h - top))]
        if panel:
            parts.append((legs, (x1 - 0.025, x1, y0, y1, 0, h - top)))
        for xa in (x0 + 0.03,) if panel else (x0 + 0.03, x1 - 0.03 - leg):
            for ya in (y0 + 0.03, y1 - 0.03 - leg):
                parts.append((legs, (xa, xa + leg, ya, ya + leg, 0, h - top)))
        return parts
    if finish == "side-table":
        # The small bedroom's bedside table (0054): black steel legs, a dark
        # top and a shelf under it, a white lamp base standing on it.
        leg, top = 0.02, 0.02
        parts = [("walnut", (x0, x1, y0, y1, h - top, h)),
                 ("walnut", (x0 + leg, x1 - leg, y0 + leg, y1 - leg, 0.16, 0.16 + top))]
        for xa in (x0, x1 - leg):
            for ya in (y0, y1 - leg):
                parts.append(("black-metal", (xa, xa + leg, ya, ya + leg, 0, h - top)))
        return parts + [("box-white", (-0.05, 0.05, -0.05, 0.05, h, h + 0.08))]
    if finish == "air-con":
        # A split unit as photographed: flat front and top, the front
        # curving round into the underside, and the outlet underneath
        # behind the curve, as it is seen from the room below.
        return [("white", (x0, x1, y0, y1, 0, h)),
                ("vent", (x0 + 0.09, x1 - 0.09, y0 + 0.095, y1 - 0.03, -0.002, 0.001))]
    if finish == "island":
        # Drawers face the kitchen (-y); the free end is +x, the bar side +y.
        # The top is one piece: island_top. The drawers and their grip sit
        # under its stone, lower than its oak; under the oak end, a fascia
        # closes the front up to it.
        under = h - 0.04 - STONE_DROP
        end = x1 - 0.25
        parts = [("plinth", (x0 + 0.05, x1 - 0.05, y0 + 0.06, y1 - 0.06, 0, 0.1)),
                 ("white", (x0, x1 - 0.025, y0 + FRONT_T, y1 - 0.02, 0.1, h - 0.04)),
                 ("alu", (x0, x1 - 0.025, y0 + 0.004, y0 + FRONT_T + 0.01,
                          under - GRIP + 0.001, under - 0.001)),
                 ("white", (end, x1 - 0.025, y0, y0 + FRONT_T, under, h - 0.04)),
                 *fronts(x0, x1 - 0.025, 0.1 + GAP, under - GRIP, y0, [[0, 3]] * 3),
                 ("oak-warm", (x1 - 0.025, x1, y0, y1 - 0.02, 0, h - 0.04)),
                 ("oak-warm", (x0, x1, y1 - 0.02, y1, 0, h - 0.04))]
        if opts.get("backPanel", True):
            parts += [("oak-warm", (x0, x0 + 0.03, y0, y1, h - STONE_DROP, h + 0.75)),
                      ("glass-shelf", (x0 + 0.03, x0 + 0.2, y0 + 0.08, y1 - 0.08,
                                       h + 0.45, h + 0.462))]
        return parts
    if finish == "wardrobe":
        # Doors left to right as `columns` (see `fronts`), else `doors` equal
        # ones. Bar handles beside each meeting edge, or where `handles` puts
        # them: fractions across the front from its left. A corner unit's
        # door hidden behind the other wing gets none. `drawers` rows of
        # drawers under the doors, up to `drawerTop` (the small bedroom's, 0055).
        columns = opts.get("columns") or [[0, 1]] * opts.get("doors", 2)
        doors_from = 0.06
        parts = [("oak", (x0, x1, y0 + FRONT_T, y1, 0, h))]
        if opts.get("drawers"):
            doors_from = opts.get("drawerTop", 0.8)
            parts += fronts(x0, x1, 0.06, doors_from - GAP, y0,
                            [[c[0], opts["drawers"]] for c in columns], surface="oak", grips=False)
        parts += fronts(x0, x1, doors_from, h - 0.004, y0, columns, surface="oak", grips=False)
        widths = column_widths(x0, x1, columns)
        edges = [x0 + sum(widths[: i + 1]) for i in range(len(widths) - 1)]
        spots = ([x0 + f * w for f in opts["handles"]] if "handles" in opts
                 else [hx for edge in edges for hx in (edge - 0.05, edge + 0.035)])
        for hx in spots:
            parts += [("steel", (hx, hx + 0.015, y0 - 0.035, y0 - 0.02, 0.95, 1.35)),
                      ("steel", (hx, hx + 0.015, y0 - 0.02, y0, 0.96, 0.98)),
                      ("steel", (hx, hx + 0.015, y0 - 0.02, y0, 1.32, 1.34))]
        return parts
    if finish == "open-wardrobe":
        # An open wardrobe section: both sides full height, top, plinth and
        # back, two shelves at the top, a rail (its clothes: hang_clothes), a
        # glass shelf with boxes, drawers and a wire basket below.
        ym = (y0 + y1) / 2
        drawers = opts.get("drawers", 3)
        parts = [("oak", (x0, xi0, y0, y1, 0, h)), ("oak", (xi1, x1, y0, y1, 0, h)),
                 ("oak", (xi0, xi1, y0, y1, h - 0.018, h)),
                 ("oak", (xi0, xi1, y0 + 0.02, y1, 0, 0.06)),
                 ("oak", (xi0, xi1, y1 - 0.008, y1, 0.06, h - 0.018)),
                 ("oak", (xi0, xi1, y0 + 0.01, y1 - 0.008, h - 0.38, h - 0.362)),
                 ("oak", (xi0, xi1, y0 + 0.01, y1 - 0.008, h - 0.2, h - 0.182)),
                 ("steel", (xi0, xi1, ym - 0.01, ym + 0.01, h - 0.46, h - 0.44))]
        if drawers:
            parts += [("glass-shelf", (xi0, xi1, y0 + 0.02, y1 - 0.01, 0.98, 0.99)),
                      ("boxes", (xi0 + 0.03, xi0 + 0.3, y0 + 0.05, y0 + 0.36, 0.99, 1.13)),
                      ("boxes", (xi0 + 0.33, xi1 - 0.03, y0 + 0.05, y0 + 0.36, 0.99, 1.13)),
                      ("basket", (xi0 + 0.01, xi1 - 0.01, y0 + 0.03, y1 - 0.03, 0.07, 0.27))]
            height = (0.95 - 0.30 - GAP * (drawers - 1)) / drawers
            for i in range(drawers):
                za = 0.30 + i * (height + GAP)
                parts += [("oak", (xi0 + GAP, xi1 - GAP, y0, y0 + FRONT_T, za, za + height)),
                          ("oak", (xi0 + 0.01, xi1 - 0.01, y0 + FRONT_T, y1 - 0.02,
                                   za + 0.02, za + height - 0.02))]
        return parts
    if finish == "coat-niche":
        # The hall's coat niche (photo wall4), a wall on its left and a
        # partition on its right, both outside the box: rustic oak lining and
        # a shelf near the top, a rail under it (its coats: hang_clothes)
        # above a white two-drawer unit with an oak top, on a plinth.
        ym, top = (y0 + y1) / 2, h - 0.40
        parts = [("oak-warm", (x0, xi0, y0, y1, 0, h)),
                 ("oak-warm", (xi0, x1, y1 - 0.018, y1, 0, h)),
                 ("oak-warm", (xi0, x1, y0 + 0.01, y1 - 0.018, h - 0.32, h - 0.30)),
                 ("steel", (xi0, x1, ym - 0.01, ym + 0.01, top, top + 0.02)),
                 ("plinth", (xi0, x1, y0 + 0.05, y1 - 0.018, 0, 0.08)),
                 ("white", (xi0, x1, y0 + FRONT_T, y1 - 0.018, 0.08, 0.5)),
                 ("oak-warm", (xi0, x1, y0, y1 - 0.018, 0.5, 0.518)),
                 *fronts(xi0, x1, 0.08 + GAP, 0.5 - GAP, y0, [[0, 2]], surface="white")]
        return parts
    if finish == "bed":
        base = min(0.26, h * 0.4)
        return [("wenge", (x0, x1, y0, y1, 0.02, base)),
                ("wenge", (x0 + 0.04, x1 - 0.04, y0 + 0.04, y1 - 0.04, 0, 0.02)),
                ("linen", (x0 + 0.03, x1 - 0.03, y0 + 0.03, y1 - 0.03, base, h - 0.08)),
                ("linen", (x0 - 0.02, x1 + 0.01, y0 - 0.02, y1 + 0.02, h - 0.14, h))]
    if finish == "bed-frame":
        # Under a generated bed whose own frame is dropped (dropDarker): the
        # platform as photographed (bed1-3), dark wood, its plinth set back.
        return [("wenge", (x0, x1, y0, y1, 0.03, h)),
                ("wenge", (x0 + 0.05, x1 - 0.05, y0 + 0.05, y1 - 0.05, 0, 0.03))]
    if finish == "tv" and opts.get("feet"):
        # Standing on its sideboard (0073): a slim panel on two splayed feet.
        foot = opts["feet"]
        ym = (y0 + y1) / 2
        parts = [("screen", (x0, x1, ym - 0.012, ym - 0.004, foot, h)),
                 ("black", (x0, x1, ym - 0.004, ym + 0.02, foot, h)),
                 ("black", (-0.2, 0.2, ym + 0.02, ym + 0.05, foot + 0.1, foot + 0.4))]
        for xf in (x0 + 0.12, x1 - 0.14):
            parts += [("black", (xf, xf + 0.02, ym - 0.004, ym + 0.006, foot - 0.04, foot + 0.05)),
                      ("black", (xf - 0.04, xf + 0.06, y0, y1, 0, 0.012))]
        return parts
    if finish == "tv":
        return [("black", (x0, x1, y0, y0 + 0.012, 0, h)),
                ("vent", (x0 + 0.08, x1 - 0.08, y0 + 0.012, y1, 0.08, h - 0.08))]
    if finish == "washer":
        # A front loader: white body, the round door seen as its dark glass
        # behind a steel rim, the control strip along the top.
        cz, r = h * 0.45, min(w, h) * 0.25
        return [("white", (x0, x1, y0 + 0.01, y1, 0, h)),
                ("steel", (-r - 0.03, r + 0.03, y0, y0 + 0.012, cz - r - 0.03, cz + r + 0.03)),
                ("black", (-r, r, y0 - 0.006, y0 + 0.01, cz - r, cz + r)),
                ("alu", (x0 + 0.02, x1 - 0.02, y0, y0 + 0.012, h - 0.12, h - 0.02))]
    if finish in PIECES:
        return PIECES[finish](w, h, d, opts)
    raise ValueError(f"Unknown finish {finish!r}")


# --------------------------------------------------------------------------
# flat-02's own pieces, each as its photos show it (the photo numbers are
# data/projects/flat-02/input/photos IMG_0xxx). Built in the same frame as
# finish_parts: x across, y depth with the front at -d/2, z up from 0.
# --------------------------------------------------------------------------


def hall_wardrobe(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    The hall's built-in (0061, 0062, 0106): at its left end a screen of oak
    slats the depth of it, then the coat niche, lined in oak, with drawers
    under an oak seat, a rail under its oak top and a cupboard over it; then
    cupboards below and above an open oak box. Fronts plain and handleless.
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    slats, niche = opts.get("slats", 0.075), opts.get("niche", 0.62)
    seat, top = opts.get("seat", 0.47), opts.get("nicheTop", 1.98)
    box_lo, box_hi = opts.get("open", (0.92, 1.32))
    ym = (y0 + y1) / 2
    parts: list[Part] = []
    count = max(3, round(d / 0.085))
    pitch = d / count
    for i in range(count):  # the slats, gaps between them
        ya = y0 + i * pitch + 0.0175
        parts.append(("oak-warm", (x0, x0 + slats, ya, ya + pitch - 0.035, 0, h)))
    a, b = x0 + slats, x0 + slats + niche
    parts += [("plinth", (a, b, y0 + 0.05, y1, 0, 0.06)),
              ("white", (a, b, y0 + FRONT_T, y1, 0.06, seat - 0.03)),
              *fronts(a, b, 0.06 + GAP, seat - 0.03 - GAP, y0, [[0, 2]], grips=False),
              ("oak-warm", (a, b, y0 - 0.012, y1, seat - 0.03, seat)),
              ("oak-warm", (a, b - 0.019, y1 - 0.018, y1, seat, top - 0.025)),
              ("oak-warm", (b - 0.019, b, y0, y1, seat, top - 0.025)),
              ("oak-warm", (a, b, y0, y1, top - 0.025, top)),
              ("steel", (a + 0.02, b - 0.039, ym - 0.01, ym + 0.01, top - 0.11, top - 0.09)),
              ("white", (a, b, y0 + FRONT_T, y1, top, h)),
              *fronts(a, b, top + GAP, h - 0.004, y0, [[0, 1]], grips=False)]
    t = 0.019
    parts += [("plinth", (b, x1, y0 + 0.05, y1, 0, 0.06)),
              ("white", (b, x1, y0 + FRONT_T, y1, 0.06, box_lo)),
              *fronts(b, x1, 0.06 + GAP, box_lo - GAP, y0, [[0, 1], [0, 1]], grips=False),
              ("oak-warm", (b, x1, y0 + 0.004, y1, box_lo, box_lo + t)),
              ("oak-warm", (b, b + t, y0 + 0.004, y1, box_lo + t, box_hi - t)),
              ("oak-warm", (x1 - t, x1, y0 + 0.004, y1, box_lo + t, box_hi - t)),
              ("oak-warm", (b + t, x1 - t, y1 - t, y1, box_lo + t, box_hi - t)),
              ("oak-warm", (b, x1, y0 + 0.004, y1, box_hi - t, box_hi)),
              ("white", (b, x1, y0 + FRONT_T, y1, box_hi, h)),
              *fronts(b, x1, box_hi + GAP, h - 0.004, y0, [[0, 1], [0, 1]], grips=False)]
    return parts


def shoe_cabinet(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    The entry's shoe cabinet (0059, 0107): hung on the wall with the shoes'
    mat under it, two plain doors, an oak top. (The owner had the black bar
    on its end taken off.)
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    low, top = opts.get("raised", 0.25), h - 0.022
    return [("white", (x0, x1, y0 + FRONT_T, y1, low, top)),
            *fronts(x0, x1, low + 0.002, top - 0.003, y0, [[0, 1], [0, 1]], grips=False),
            ("oak-warm", (x0 - 0.004, x1 + 0.004, y0 - 0.012, y1, top, h)),
            ("mat", (x0 + 0.03, x1 + 0.08, y0 - 0.08, y1 - 0.01, 0.0, 0.006))]


def sideboard(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    An oak case with its fronts set in between its sides: the TV's (0073:
    olive, pink and cream doors, `fronts`, one surface a column) and the
    hall's by the study (0063: two drawers the full width over two doors,
    `drawers` of `drawerHeight`). `case` names the case's surface.
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    t, base, case = 0.019, opts.get("plinth", 0.0), opts.get("case", "oak-warm")
    columns = opts.get("columns") or [[0, 1]] * 3
    colours = opts.get("fronts") or ["gloss"] * len(columns)
    top = h - t - GAP
    parts = []
    if opts.get("drawers"):
        split = h - t - opts["drawers"] * opts.get("drawerHeight", 0.17)
        parts += fronts(x0 + t, x1 - t, split + GAP / 2, top, y0 + 0.002,
                        [[0, opts["drawers"]]], surface=colours[0], grips=False)
        top = split - GAP / 2
    parts += [(case, (x0, x0 + t, y0, y1, 0, h)), (case, (x1 - t, x1, y0, y1, 0, h)),
             (case, (x0 + t, x1 - t, y0, y1, h - t, h)),
             (case, (x0 + t, x1 - t, y0 + 0.004, y1, base, base + t)),
             (case, (x0 + t, x1 - t, y1 - 0.008, y1, base + t, h - t))]
    if base:
        parts.append((case, (x0 + t, x1 - t, y0 + 0.03, y1, 0, base)))
    x = x0 + t
    for column, width, surface in zip(columns, column_widths(x0 + t, x1 - t, columns), colours,
                                      strict=True):
        parts += fronts(x, x + width, base + t + GAP, top, y0 + 0.002, [column],
                        surface=surface, grips=False)
        x += width
    return parts


def dining_table(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """The dining table (0084): a rectangular oak top on one oak pedestal, its foot a dark plate."""
    top = 0.04
    return [("oak-top", (-w / 2, w / 2, -d / 2, d / 2, h - top, h)),
            ("black", (-w / 2 + 0.15, w / 2 - 0.15, -d / 2 + 0.12, d / 2 - 0.12, h - top - 0.04,
                       h - top)),
            ("oak-top", (-0.27, 0.27, -0.07, 0.07, 0.12, h - top - 0.04)),
            ("oak-top", (-0.4, 0.4, -0.11, 0.11, 0.025, 0.12)),
            ("black", (-0.44, 0.44, -0.25, 0.25, 0.0, 0.025))]


def coffee_table(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """The coffee table (0095-0099): an oak top on a black steel frame, a black shelf under it."""
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    leg, top, i = 0.03, 0.04, 0.02
    parts: list[Part] = [("oak-top", (x0, x1, y0, y1, h - top, h))]
    for xa in (x0 + i, x1 - i - leg):
        for ya in (y0 + i, y1 - i - leg):
            parts.append(("black", (xa, xa + leg, ya, ya + leg, 0, h - top)))
    xa, xb, ya, yb = x0 + i + leg, x1 - i - leg, y0 + i + leg, y1 - i - leg
    for z0, z1 in ((h - top - leg, h - top), (0.1, 0.1 + leg)):
        parts += [("black", (xa, xb, y0 + i, y0 + i + leg, z0, z1)),
                  ("black", (xa, xb, y1 - i - leg, y1 - i, z0, z1)),
                  ("black", (x0 + i, x0 + i + leg, ya, yb, z0, z1)),
                  ("black", (x1 - i - leg, x1 - i, ya, yb, z0, z1))]
    parts.append(("black", (xa, xb, ya, yb, 0.1 + leg - 0.006, 0.1 + leg)))
    return parts


def bath_vanity(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    The bathroom's vanity wall (0117, 0120, 0122, 0134): sage drawers hung
    clear of the floor under an oak top its whole length; on it from the
    left a tall sage cupboard, an oak upright to the ceiling, a narrow column
    of open oak shelves under a sage door, an oak crown along the top; the
    basin (vanity_basin) and a frameless mirror over it to the right. All of
    it flush in front, as photographed (0134, 0136): set back, the basin's
    top stood out in front of the shelves. The shelves stop a hand over the
    tiles, an oak side between them and the basin (the owner).
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    low, top = 0.14, 0.86
    tall, upright, shelf_w = opts.get("tall", 0.6), 0.05, opts.get("shelves", 0.27)
    yu = y1 - opts.get("upperDepth", d)
    crown = h - 0.05
    a, b, c = x0 + tall, x0 + tall + upright, x0 + tall + upright + shelf_w
    t = 0.018
    # Three open compartments up to about the mirror's middle (0117, 0136).
    roof = opts.get("roof", 1.55)
    parts: list[Part] = [
        ("white", (x0, x1, y0 + FRONT_T, y1, low, top - 0.03)),
        *fronts(x0, x1, low + GAP, top - 0.03 - GAP, y0, [[c - x0, 2], [0, 2]], grips=False),
        ("oak-warm", (x0, x1, y0 - 0.015, y1, top - 0.03, top)),
        ("white", (x0, a, yu + FRONT_T, y1, top, crown)),
        *fronts(x0, a, top + GAP, crown - GAP, yu, [[0, 1]], grips=False),
        ("oak-warm", (a, b, yu, y1, top, h)),
        ("oak-warm", (b, c, y1 - 0.01, y1, top, roof)),
        *[("oak-warm", (b, c, yu + 0.01, y1 - 0.01, z, z + t))
          for z in (top + (roof - top) / 3, top + 2 * (roof - top) / 3)],
        ("oak-warm", (b, c, yu, y1, roof, roof + t)),
        ("white", (b, c, yu + FRONT_T, y1, roof + t, crown)),
        *fronts(b, c, roof + t + GAP, crown - GAP, yu, [[0, 1]], grips=False),
        ("oak-warm", (c, c + t, yu, y1, top, h)),
        ("oak-warm", (x0, a, yu - 0.01, y1, crown, h)),
        ("oak-warm", (b, c, yu - 0.01, y1, crown, h))]
    mx = (c + t + x1) / 2
    parts.append(("mirror", (mx - 0.3, mx + 0.3, y1 - 0.01, y1 - 0.003, 1.22, 2.06)))
    return parts


def washer_column(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    The washer at eye level (0126-0129, 0137): oak sides and top round a
    column, a sage cupboard under the washer's oak shelf, its oak back, and
    a two-door sage cupboard over it. The washer: a white front loader, its
    round door's dark glass in a steel ring, the control strip along the top.
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    t = 0.019
    base, upper = opts.get("shelf", 0.86), opts.get("upper", 1.8)
    xa, xb = x0 + t, x1 - t
    parts: list[Part] = [
        ("oak-warm", (x0, xa, y0, y1, 0, h)), ("oak-warm", (xb, x1, y0, y1, 0, h)),
        ("oak-warm", (xa, xb, y0, y1, h - t, h)),
        ("white", (xa, xb, y0 + FRONT_T, y1, 0.0, base)),
        *fronts(xa, xb, 0.02, base - GAP, y0, [[0, 1]], grips=False),
        ("oak-warm", (xa, xb, y0 + 0.004, y1, base, base + t)),
        ("oak-warm", (xa, xb, y1 - 0.012, y1, base + t, upper - t)),
        ("oak-warm", (xa, xb, y0 + 0.004, y1, upper - t, upper)),
        ("white", (xa, xb, y0 + FRONT_T, y1, upper, h - t)),
        *fronts(xa, xb, upper + GAP, h - t - GAP, y0, [[0, 1], [0, 1]], grips=False)]
    # The washer, 85 cm tall, on the shelf.
    wz, ww = base + t, xb - xa - 0.01
    cz, r = wz + 0.47, 0.17
    parts += [("white", (-ww / 2, ww / 2, y0 + 0.03, y1 - 0.02, wz, wz + 0.85)),
              ("steel", (-r - 0.03, r + 0.03, y0 + 0.018, y0 + 0.03, cz - r - 0.03, cz + r + 0.03)),
              ("black", (-r, r, y0 + 0.012, y0 + 0.018, cz - r, cz + r)),
              ("vent", (-ww / 2 + 0.02, ww / 2 - 0.02, y0 + 0.025, y0 + 0.03,
                        wz + 0.74, wz + 0.82))]
    return parts


def roll_shelf(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    The narrow open oak shelf beside the washer column (0126, 0137), rolls on
    its shelves: open at the sides (the owner), the shelves on an oak back.
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    t = 0.018
    parts: list[Part] = [("oak-warm", (x0, x1, y1 - t, y1, 0, h))]
    levels = [0.0, 0.25, 0.5, 0.75, h - t]
    for z in levels:
        parts.append(("oak-warm", (x0, x1, y0, y1 - t, z, z + t)))
    for z in levels[:-1]:  # a roll on each shelf
        parts.append(("paper", (-0.055, 0.055, -0.055, 0.055, z + t, z + t + 0.1)))
    parts.append(("box-white", (x0 - 0.01, x1 + 0.01, y0 + 0.01, y1 - 0.01, h, h + 0.1)))
    return parts


def niche_wall(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    The WC's pre-wall (0124, 0125): tiled to the tiles' top, white above,
    the flush plate on it and the lit niche let into it over the WC, its
    floor the tiles' top. What stands in the niche is a picture of it.
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    tiles, (n0, n1) = opts.get("tiles", 1.2), opts.get("niche", (1.2, 1.66))
    nw = opts.get("width", 0.44)
    depth = opts.get("depth", 0.13)
    a, b = -nw / 2, nw / 2
    return [("tile", (x0, x1, y0, y1, 0, tiles)),
            ("paint", (x0, a, y0, y1, tiles, h)), ("paint", (b, x1, y0, y1, tiles, h)),
            ("paint", (a, b, y0, y1, n1, h)),
            ("paint", (a, b, y0 + depth, y1, n0, n1)),
            ("box-white", (-0.12, 0.12, y0 - 0.006, y0, 0.98, 1.13))]


def box_bed(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    A box-spring bed (0109, 0140): its fabric base on low oak feet, the
    mattress on it and an upholstered headboard `h` high at the back
    (`head`: 0 for none). Its duvet and pillows are soft shapes (bedding).
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    head = opts.get("head", 0.08)
    feet, base, mattress = opts.get("feet", 0.07), opts.get("base", 0.36), opts.get("mattress", 0.2)
    yb = y1 - head
    deck = base + mattress
    parts: list[Part] = []
    for xa in (x0 + 0.06, x1 - 0.1):
        for ya in (y0 + 0.06, yb - 0.1):
            parts.append(("feet", (xa, xa + 0.04, ya, ya + 0.04, 0, feet)))
    parts += [("upholstery", (x0, x1, y0, yb, feet, base)),
              ("mattress", (x0 + 0.01, x1 - 0.01, y0 + 0.01, yb - 0.01, base, deck))]
    if head:
        parts.append(("upholstery", (x0 - 0.02, x1 + 0.02, yb, y1, feet, h)))
    return parts


def folding_bed(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """The study's guest bed (0067, 0068): a mattress on a black steel folding frame."""
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    rail, top = 0.025, h - 0.12
    parts: list[Part] = [("black", (x0, x1, y0, y0 + rail, top - rail, top)),
                         ("black", (x0, x1, y1 - rail, y1, top - rail, top)),
                         ("black", (x0, x0 + rail, y0 + rail, y1 - rail, top - rail, top)),
                         ("black", (x1 - rail, x1, y0 + rail, y1 - rail, top - rail, top))]
    for xa in (x0 + 0.08, -0.02, x1 - 0.11):
        for ya in (y0, y1 - rail):
            parts.append(("black", (xa, xa + rail, ya, ya + rail, 0.0, top - rail)))
    for i in range(12):  # the slats
        xa = x0 + 0.06 + i * (w - 0.15) / 11
        parts.append(("black", (xa, xa + 0.02, y0 + rail, y1 - rail, top - 0.012, top)))
    parts.append(("mattress", (x0 + 0.01, x1 - 0.01, y0 + 0.01, y1 - 0.01, top, h - 0.01)))
    return parts


def standing_desk(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    The study's desk (0069, 0070): a rectangular light oak top on a white
    two-legged frame, two monitors on their stands at the back, a keyboard.
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    top = 0.025
    parts: list[Part] = [("birch", (x0, x1, y0, y1, h - top, h)),
                         ("frame-white", (x0 + 0.1, x1 - 0.1, -0.03, 0.03,
                                          h - top - 0.05, h - top))]
    for xl in (x0 + 0.12, x1 - 0.19):
        parts += [("frame-white", (xl, xl + 0.07, -0.025, 0.025, 0.03, h - top - 0.05)),
                  ("frame-white", (xl - 0.01, xl + 0.08, y0 + 0.06, y1 - 0.06, 0.0, 0.03))]
    monitors = opts.get("monitors", 2)
    mw, mh = 0.615, 0.365
    span = monitors * mw + (monitors - 1) * 0.01
    for i in range(monitors):
        xa = -span / 2 + i * (mw + 0.01)
        xm = xa + mw / 2
        parts += [("screen", (xa, xa + mw, y1 - 0.24, y1 - 0.232, h + 0.13, h + 0.13 + mh)),
                  ("black", (xa, xa + mw, y1 - 0.232, y1 - 0.21, h + 0.13, h + 0.13 + mh)),
                  ("black", (xm - 0.025, xm + 0.025, y1 - 0.2, y1 - 0.17, h, h + 0.3)),
                  ("black", (xm - 0.1, xm + 0.1, y1 - 0.28, y1 - 0.1, h, h + 0.01))]
    parts.append(("black", (-0.22, 0.22, y0 + 0.1, y0 + 0.24, h, h + 0.02)))
    return parts


def nightstand(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """A white three-drawer bedside chest (0109, 0140), handleless fronts."""
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    return [("white", (x0, x1, y0 + FRONT_T, y1, 0.0, h - 0.02)),
            ("white", (x0, x1, y0, y1, h - 0.02, h)),
            *fronts(x0, x1, 0.01, h - 0.02 - GAP, y0, [[0, opts.get("drawers", 3)]], grips=False)]


def sliding_wardrobe(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    The study's wardrobe (0066): two sliding doors in white aluminium frames,
    each four panels high, the left beige glass, the right a mirror; the
    right runs on the track in front.
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    bar = 0.022
    parts: list[Part] = [("white", (x0, x1, y0 + 0.07, y1, 0, h)),
                         ("frame-white", (x0, x1, y0, y0 + 0.07, h - 0.05, h)),
                         ("frame-white", (x0, x1, y0 + 0.01, y0 + 0.07, 0, 0.02))]
    half = w / 2 + 0.02
    for xa, ya, panel in ((x0, y0 + 0.04, opts.get("left", "glass-beige")),
                          (x1 - half, y0 + 0.012, opts.get("right", "mirror"))):
        xb = xa + half
        parts += [("frame-white", (xa, xa + bar, ya, ya + 0.024, 0.02, h - 0.05)),
                  ("frame-white", (xb - bar, xb, ya, ya + 0.024, 0.02, h - 0.05))]
        rows = 4
        step = (h - 0.07 - bar) / rows
        for r in range(rows + 1):
            z = 0.02 + r * step
            parts.append(("frame-white", (xa + bar, xb - bar, ya, ya + 0.024, z, z + bar)))
            if r < rows:
                parts.append((panel, (xa + bar, xb - bar, ya + 0.008, ya + 0.014,
                                      z + bar, z + step)))
    return parts


def slat_wall(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    The TV wall's acoustic panel (0073): narrow dark oak slats on black felt,
    floor to `h`. As a barn door, a `back` board behind the felt: seen from
    the hall when it is shut.
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    behind = y1 - 0.006
    parts: list[Part] = [("felt", (x0, x1, behind, y1, 0, h))]
    if opts.get("back"):
        behind = y1 - 0.012
        parts = [("felt", (x0, x1, behind, y1 - 0.006, 0, h)),
                 (opts["back"], (x0, x1, y1 - 0.006, y1, 0, h))]
    pitch, slat = opts.get("pitch", 0.045), opts.get("slat", 0.027)
    count = int((w + pitch - slat) / pitch)
    start = -(count - 1) * pitch / 2
    for i in range(count):
        xm = start + i * pitch
        parts.append(("walnut", (xm - slat / 2, xm + slat / 2, y0, behind, 0, h)))
    return parts


def floating_shelf(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """An oak shelf on hidden brackets (the kitchen's, 0079, 0081), framed photos on it."""
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    t = 0.025
    parts: list[Part] = [("oak-top", (x0, x1, y0, y1, 0, t))]
    for i, surface in enumerate(opts.get("frames", [])):
        fa = x0 + 0.03 + i * 0.14
        parts += [("frame-white", (fa, fa + 0.12, y1 - 0.06, y1 - 0.045, t, t + 0.16)),
                  (surface, (fa + 0.012, fa + 0.108, y1 - 0.062, y1 - 0.06, t + 0.012, t + 0.148))]
    return parts


def step_stool(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """The bathroom's two-step stool (0121, 0138): slatted bamboo treads on two sides."""
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    t = 0.02
    ym = (y0 + y1) / 2
    parts: list[Part] = [("bamboo", (x0, x0 + t, y0, ym, 0, h / 2)),
                         ("bamboo", (x1 - t, x1, y0, ym, 0, h / 2)),
                         ("bamboo", (x0, x0 + t, ym, y1, 0, h)),
                         ("bamboo", (x1 - t, x1, ym, y1, 0, h))]
    for (ya, yb, z) in ((y0, ym, h / 2), (ym, y1, h)):
        for i in range(4):
            yy = ya + 0.01 + i * (yb - ya - 0.02) / 4
            parts.append(("bamboo", (x0 + t, x1 - t, yy, yy + (yb - ya) / 4 - 0.015, z - t, z)))
    return parts


def towels(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """Towels and a robe on their hooks (0123, 0124): `hung`, each a surface, width and length."""
    x0, y1 = -w / 2, d / 2
    parts: list[Part] = []
    x = x0
    hung = opts.get("hung", [("towel-grey", 0.32, 0.85), ("robe", 0.38, 1.05)])
    for surface, width, length in hung:
        xm = x + width / 2
        parts += [("steel", (xm - 0.01, xm + 0.01, y1 - 0.04, y1, h - 0.02, h)),
                  (surface, (x, x + width, y1 - 0.06, y1 - 0.015, h - length, h - 0.03))]
        x += width + 0.03
    return parts


def picture(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    A picture on the wall: its `image` (a surface cut from the photos, laid
    once across by picture_uvs) as a canvas, or behind a white `mat` in a
    `frame` (metres; `frameSurface`).
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    f, m, image = opts.get("frame", 0.0), opts.get("mat", 0.0), opts["image"]
    if not f:
        return [(image, (x0, x1, y0, y1, 0, h))]
    fs = opts.get("frameSurface", "frame")
    parts: list[Part] = [(fs, (x0, x0 + f, y0, y1, 0, h)), (fs, (x1 - f, x1, y0, y1, 0, h)),
                         (fs, (x0 + f, x1 - f, y0, y1, 0, f)),
                         (fs, (x0 + f, x1 - f, y0, y1, h - f, h))]
    if m:
        parts += [("mat-white", (x0 + f, x1 - f, y0 + 0.008, y1, f, f + m)),
                  ("mat-white", (x0 + f, x1 - f, y0 + 0.008, y1, h - f - m, h - f)),
                  ("mat-white", (x0 + f, x0 + f + m, y0 + 0.008, y1, f + m, h - f - m)),
                  ("mat-white", (x1 - f - m, x1 - f, y0 + 0.008, y1, f + m, h - f - m))]
    parts.append((image, (x0 + f + m, x1 - f - m, y0 + 0.011, y1, f + m, h - f - m)))
    return parts


def box_cushion(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """A scatter cushion (the sofa's teal ones, 0094-0097): one block, softened (SOFTNESS)."""
    return [(opts.get("surface", "linen"), (-w / 2, w / 2, -d / 2, d / 2, 0, h))]


def blinds(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    External venetian blinds let down outside a window (0084, 0092, 0093):
    anthracite slats with a strip of daylight between each, in side rails
    under a head box. Its front (y0) faces the room.
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    rail, head = 0.025, 0.1
    parts: list[Part] = [("blind", (x0, x0 + rail, y0, y1, 0, h - head)),
                         ("blind", (x1 - rail, x1, y0, y1, 0, h - head)),
                         ("blind", (x0, x1, y0, y1, h - head, h))]
    pitch, slat = opts.get("pitch", 0.08), opts.get("slat", 0.062)
    for i in range(int((h - head) / pitch)):
        z = h - head - (i + 1) * pitch
        parts.append(("blind", (x0 + rail, x1 - rail, y0 + 0.01, y0 + 0.022, z, z + slat)))
    return parts


def placemat(bm: bmesh.types.BMesh, w: float, h: float, d: float, slot) -> None:
    """A felt place mat (0091, 0093): a flat pebble, fuller at one end, `h` thick."""
    n, outline = 48, []
    for k in range(n):
        c, s = math.cos(2 * math.pi * k / n), math.sin(2 * math.pi * k / n)
        outline.append((w / 2 * math.copysign(abs(c) ** 0.7, c),
                        d / 2 * math.copysign(abs(s) ** 0.8, s) * (1 + 0.12 * c) / 1.12))
    low = [bm.verts.new((x, y, 0.0)) for x, y in outline]
    high = [bm.verts.new((x, y, h)) for x, y in outline]
    faces = [bm.faces.new(high), bm.faces.new(list(reversed(low)))]
    faces += [bm.faces.new((low[k], low[(k + 1) % n], high[(k + 1) % n], high[k]))
              for k in range(n)]
    for face in faces:
        face.material_index = slot("felt")


def railing(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """
    The balcony's balustrade: frosted glass panes between anthracite posts
    about every 1.5 m, a flat top rail over them. Its front (y0) faces the
    balcony.
    """
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    post, rail = 0.05, 0.05
    bays = max(1, round(w / 1.5))
    parts: list[Part] = [("anthracite", (x0, x1, y0, y1, h - rail, h))]
    for i in range(bays + 1):
        xa = x0 + i * (w - post) / bays
        parts.append(("anthracite", (xa, xa + post, y0 + 0.005, y1 - 0.005, 0, h - rail)))
    for i in range(bays):
        xa = x0 + post + i * (w - post) / bays
        parts.append(("glass-frost", (xa + 0.01, xa + (w - post) / bays - post - 0.01,
                                      -0.005, 0.005, 0.08, h - rail - 0.04)))
    return parts


def garden_table(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """A balcony table, as the scan has it: a slatted acacia top on an anthracite frame."""
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    leg, t = 0.04, 0.025
    parts: list[Part] = []
    for xa in (x0 + 0.03, x1 - 0.03 - leg):
        for ya in (y0 + 0.03, y1 - 0.03 - leg):
            parts.append(("anthracite", (xa, xa + leg, ya, ya + leg, 0, h - t)))
    parts += [("anthracite", (x0 + 0.03, x1 - 0.03, y0 + 0.03, y0 + 0.05, h - t - 0.06, h - t)),
              ("anthracite", (x0 + 0.03, x1 - 0.03, y1 - 0.05, y1 - 0.03, h - t - 0.06, h - t))]
    count = 7
    for i in range(count):
        ya = y0 + i * d / count
        parts.append(("acacia", (x0, x1, ya + 0.004, ya + d / count - 0.004, h - t, h)))
    return parts


def garden_chair(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """A balcony chair: anthracite frame, acacia slats in its seat and back (front y0)."""
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    leg, seat = 0.03, 0.45
    parts: list[Part] = []
    for xa in (x0 + 0.04, x1 - 0.04 - leg):
        parts += [("anthracite", (xa, xa + leg, y0 + 0.04, y0 + 0.04 + leg, 0, seat)),
                  ("anthracite", (xa, xa + leg, y1 - 0.06, y1 - 0.06 + leg, 0, h)),
                  ("anthracite", (xa, xa + leg, y0 + 0.04, y1 - 0.06, seat - 0.03, seat))]
    for i in range(5):
        ya = y0 + 0.04 + i * (d - 0.12) / 5
        parts.append(("acacia", (x0 + 0.05, x1 - 0.05, ya + 0.004, ya + (d - 0.12) / 5 - 0.004,
                                 seat, seat + 0.02)))
    for i in range(3):
        za = seat + 0.12 + i * 0.1
        parts.append(("acacia", (x0 + 0.05, x1 - 0.05, y1 - 0.06, y1 - 0.04, za, za + 0.075)))
    return parts


def lounge_chair(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """A low balcony armchair: an anthracite frame, grey seat and back cushions (front y0)."""
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    arm, base = 0.07, 0.18
    return [("anthracite", (x0, x0 + arm, y0, y1, 0.0, 0.58)),
            ("anthracite", (x1 - arm, x1, y0, y1, 0.0, 0.58)),
            ("anthracite", (x0 + arm, x1 - arm, y1 - 0.08, y1, 0.0, h - 0.1)),
            ("anthracite", (x0 + arm, x1 - arm, y0, y1 - 0.08, base - 0.05, base)),
            ("cushion-grey", (x0 + arm, x1 - arm, y0 + 0.02, y1 - 0.08, base, base + 0.16)),
            ("cushion-grey", (x0 + arm + 0.01, x1 - arm - 0.01, y1 - 0.24, y1 - 0.08,
                              base + 0.16, h))]


def backdrop(w: float, h: float, d: float, opts: dict) -> list[Part]:
    """What is across the street, seen from the balcony: a row of house fronts or the ground."""
    return [(opts.get("surface", "facade"), (-w / 2, w / 2, -d / 2, d / 2, 0, h))]


def yucca(bm: bmesh.types.BMesh, w: float, h: float, d: float, slot) -> None:
    """
    The yucca beside the dining table (0084, 0092, 0093): a low red pot,
    three canes of different heights, each topped by a burst of long narrow
    leaves, the young ones rising, the old ones arching out and down. The
    free models were a spiky succulent in a tall white pot; the owner wanted
    the pot shorter and the plant taller.
    """
    import random

    rng = random.Random(7)
    pot_r, pot_h, n = min(w, d) * 0.36, 0.34, 40
    lathe = [(0.74, 0.0), (0.8, 0.03), (0.93, 0.55), (1.0, 0.9), (1.05, 0.94), (1.05, 1.0),
             (0.97, 1.0), (0.95, 0.9)]
    rings = [[bm.verts.new((pot_r * f * math.cos(a), pot_r * f * math.sin(a), pot_h * z))
              for a in (2 * math.pi * k / n for k in range(n))] for f, z in lathe]
    for lower, upper in zip(rings, rings[1:], strict=False):
        for k in range(n):
            face = bm.faces.new((lower[k], lower[(k + 1) % n], upper[(k + 1) % n], upper[k]))
            face.material_index, face.smooth = slot("terracotta"), True
    bm.faces.new(list(reversed(rings[0]))).material_index = slot("terracotta")
    bm.faces.new(rings[-1]).material_index = slot("soil")
    tops = []
    for k, (rise, lean) in enumerate(((h - 0.42, 0.05), (h - 0.68, 0.11), (h - 0.95, 0.13))):
        a = 2 * math.pi * k / 3 + 0.4
        base = Vector((0.04 * math.cos(a), 0.04 * math.sin(a), pot_h * 0.85))
        top = Vector((lean * math.cos(a), lean * math.sin(a), rise))
        for face in tube(bm, base, top, 0.032, 0.024, segments=10):
            face.material_index = slot("cane")
        tops.append(top)
    for top in tops:
        count = 64
        for i in range(count):
            age = i / (count - 1)  # 0: the youngest, upright; 1: the oldest, hanging
            azimuth = i * 2.39996 + rng.uniform(-0.2, 0.2)
            rise = math.radians(82 - 120 * age ** 0.85 + rng.uniform(-6, 6))
            length = rng.uniform(0.34, 0.5) * (0.75 + 0.35 * math.sin(math.pi * min(age * 1.3, 1)))
            half = rng.uniform(0.018, 0.024)
            out = Vector((math.cos(azimuth), math.sin(azimuth), 0.0))
            side = Vector((-out.y, out.x, 0.0))
            point = top + Vector((0, 0, -0.1 * age))
            heading, droop = rise, (0.5 + 0.6 * age) * (1 - math.sin(max(rise, 0)) * 0.6)
            rows = []
            for s in range(8):
                t = s / 7
                width = max(0.0015, half * (min(1.0, 4 * t + 0.35) if t < 0.25
                                            else 1.15 * (1 - t) / 0.75))
                rib = 0.25 * width
                rows.append([bm.verts.new(point + side * width),
                             bm.verts.new(point + Vector((0, 0, rib))),
                             bm.verts.new(point - side * width)])
                step = length / 7
                point = point + (out * math.cos(heading) + Vector((0, 0, math.sin(heading)))) * step
                heading = max(heading - droop * 0.18, -1.4)
            leaf = slot("leaf-old" if age > 0.8 else "leaf")
            for r0, r1 in zip(rows, rows[1:], strict=False):
                for j in range(2):
                    face = bm.faces.new((r0[j], r0[j + 1], r1[j + 1], r1[j]))
                    face.material_index, face.smooth = leaf, True


#: flat-02's pieces by finish name (finish_parts).
PIECES = {
    "hall-wardrobe": hall_wardrobe, "shoe-cabinet": shoe_cabinet, "sideboard": sideboard,
    "dining-table": dining_table, "coffee-table": coffee_table, "bath-vanity": bath_vanity,
    "washer-column": washer_column, "roll-shelf": roll_shelf, "niche-wall": niche_wall,
    "box-bed": box_bed, "folding-bed": folding_bed, "standing-desk": standing_desk,
    "nightstand": nightstand, "sliding-wardrobe": sliding_wardrobe, "slat-wall": slat_wall,
    # The living room's barn door (0073, 0074): the slat panel on its rail.
    "sliding-door": slat_wall,
    "floating-shelf": floating_shelf, "step-stool": step_stool, "towels": towels,
    "picture": picture, "box-cushion": box_cushion, "blinds": blinds, "railing": railing,
    "garden-table": garden_table, "garden-chair": garden_chair, "lounge-chair": lounge_chair,
    "backdrop": backdrop,
}


#: A top in one piece: cut lines `xs` and `ys` across the plan, each cell of
#: the grid its own surface between its own z0 and z1 (None: no cell). The
#: cells share their edges, so no hidden faces lie between them: two boards
#: laid side by side baked a dark seam, the bake spreading the hidden faces'
#: darkness over the joint. Where a neighbour lies lower, the step shows.
Cell = tuple[str, float, float] | None
Slab = tuple[list[float], list[float], list[list[Cell]]]

#: The island's stone lies this far below its oak, which stands round it as a
#: raised bar, its edge a step (kitchentray1-3).
STONE_DROP = 0.035


def island_top(w: float, h: float, d: float, opts: dict) -> Slab:
    """The island's top as photographed: oak round the stone inset, the bar
    along the back and the end overhanging where the stools go."""
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    out_end, out_back = opts.get("overhangEnd", 0.42), opts.get("overhangBack", 0.27)
    bar = opts.get("barWidth", 0.3)
    oak, stone = ("oak-warm", h - 0.04, h), ("stone", h - 0.04 - STONE_DROP, h - STONE_DROP)
    return ([x0 - 0.01, x1 - 0.25, x1 + out_end], [y0 - 0.02, y1 - bar, y1 + out_back],
            [[stone, oak], [oak, oak]])


def add_slab(bm: bmesh.types.BMesh, slab: Slab, slot, surfaces: dict) -> None:
    """A Slab into `bm`, its faces given their surface's material by `slot`."""
    xs, ys, cells = slab
    verts: dict[tuple[int, int, float], bmesh.types.BMVert] = {}

    def vert(i: int, j: int, z: float) -> bmesh.types.BMVert:
        key = (i, j, round(z, 6))
        if key not in verts:
            verts[key] = bm.verts.new((xs[i], ys[j], z))
        return verts[key]

    def cell(i: int, j: int) -> Cell:
        return cells[j][i] if 0 <= j < len(cells) and 0 <= i < len(cells[j]) else None

    heights = [z for row in cells for c in row if c for z in c[1:]]
    size = (xs[-1] - xs[0], ys[-1] - ys[0], max(heights) - min(heights))
    for j, row in enumerate(cells):
        for i, c in enumerate(row):
            if c is None:
                continue
            surface, z0, z1 = c
            corners = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
            faces = [bm.faces.new([vert(a, b, z1) for a, b in corners]),
                     bm.faces.new([vert(a, b, z0) for a, b in reversed(corners)])]
            # Sides wherever no neighbour covers them: the whole height on the
            # outline, the step above a lower neighbour, the band below a
            # higher one. Counter-clockwise from the front.
            for n, (di, dj) in enumerate(((0, -1), (1, 0), (0, 1), (-1, 0))):
                other = cell(i + di, j + dj)
                bands = ([(z0, z1)] if other is None else
                         [(max(z0, other[2]), z1), (z0, min(z1, other[1]))])
                a, b = corners[n], corners[(n + 1) % 4]
                for lo, hi in bands:
                    if hi - lo > 1e-6:
                        faces.append(bm.faces.new([vert(*a, lo), vert(*b, lo), vert(*b, hi),
                                                   vert(*a, hi)]))
            for face in faces:
                face.material_index = slot(surface)
                face.normal_update()  # a new face has none, and box_uvs goes by it
            box_uvs(bm, faces, size, surfaces[surface].get("tile", (1.0, 1.0)))


def tube(bm: bmesh.types.BMesh, a, b, r0: float, r1: float,
         segments: int = 16) -> list[bmesh.types.BMFace]:
    """A round rod from a (radius r0) to b (radius r1), shaded smooth round its side."""
    a, b = Vector(a), Vector(b)
    axis = b - a
    cone = bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=r0,
                                 radius2=r1, depth=axis.length)
    turn = Vector((0, 0, 1)).rotation_difference(axis.normalized()).to_matrix().to_4x4()
    bmesh.ops.transform(bm, matrix=Matrix.Translation((a + b) / 2) @ turn, verts=cone["verts"])
    faces = list({f for v in cone["verts"] for f in v.link_faces})
    for face in faces:
        face.normal_update()  # still the upright cone's
        face.smooth = abs(face.normal.dot(axis.normalized())) < 0.9
    return faces


def stool_shell(w: float, h: float, d: float) -> bpy.types.Mesh:
    """
    The bar stools' upholstered shell, as photographed: a thin seat with a
    soft rolled front, rising at the back into a curved back that widens to
    its rounded top. A coarse cage of sections along the shell's profile,
    smoothed by subdivision into one rounded piece.
    """
    k, seat = min(w, d) / 0.46, h * 0.64
    # Along the profile, front lip to the top of the back: y, z, width, thickness.
    sections = [(-0.20, seat - 0.03, 0.34, 0.035), (-0.17, seat - 0.005, 0.41, 0.05),
                (-0.04, seat + 0.005, 0.44, 0.055), (0.09, seat, 0.43, 0.055),
                (0.155, seat + 0.05, 0.34, 0.05), (0.175, seat + 0.17, 0.32, 0.045),
                (0.195, seat + 0.28, 0.37, 0.045), (0.21, h + 0.01, 0.39, 0.04)]
    across = (-1.0, -0.6, 0.0, 0.6, 1.0)
    points = []
    for i, (y, z, width, _) in enumerate(sections):
        # The seat's sides lift a little; the back wraps round the sitter.
        lift, wrap = (0.012, 0.0) if i < 4 else (0.0, 0.05 if i > 4 else 0.03)
        points.append([Vector((u * width * k / 2, y * k - wrap * u * u, z + lift * u * u))
                       for u in across])
    bm = bmesh.new()
    top: list[list[bmesh.types.BMVert]] = []
    under: list[list[bmesh.types.BMVert]] = []
    rows, cols = len(points), len(across)
    for i, row in enumerate(points):
        t = sections[i][3] / 2
        top.append([])
        under.append([])
        for j, p in enumerate(row):
            along = points[min(i + 1, rows - 1)][j] - points[max(i - 1, 0)][j]
            side = row[min(j + 1, cols - 1)] - row[max(j - 1, 0)]
            n = side.cross(along).normalized()
            top[i].append(bm.verts.new(p + n * t))
            under[i].append(bm.verts.new(p - n * t))
    for i in range(rows - 1):
        for j in range(cols - 1):
            bm.faces.new([top[i][j], top[i][j + 1], top[i + 1][j + 1], top[i + 1][j]])
            bm.faces.new([under[i][j], under[i + 1][j], under[i + 1][j + 1], under[i][j + 1]])
        bm.faces.new([top[i][0], top[i + 1][0], under[i + 1][0], under[i][0]])
        bm.faces.new([top[i][-1], under[i][-1], under[i + 1][-1], top[i + 1][-1]])
    for j in range(cols - 1):
        bm.faces.new([top[0][j], under[0][j], under[0][j + 1], top[0][j + 1]])
        bm.faces.new([top[-1][j], top[-1][j + 1], under[-1][j + 1], under[-1][j]])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    cage = bpy.data.meshes.new("shell-cage")
    bm.to_mesh(cage)
    bm.free()
    obj = bpy.data.objects.new("shell-cage", cage)
    bpy.context.collection.objects.link(obj)
    smooth = obj.modifiers.new("smooth", "SUBSURF")
    smooth.levels = smooth.render_levels = 2
    depsgraph = bpy.context.evaluated_depsgraph_get()
    shell = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(cage)
    return shell


def stool(bm: bmesh.types.BMesh, w: float, h: float, d: float, slot, surfaces: dict) -> None:
    """A bar stool as photographed: the shell (stool_shell) on four splayed,
    tapering round legs in a wood finish with steel glides, and round
    stretchers, the front and back ones lower than the sides'."""
    k, seat = min(w, d) / 0.46, h * 0.64
    before = set(bm.faces)
    shell = stool_shell(w, h, d)
    bm.from_mesh(shell)
    bpy.data.meshes.remove(shell)
    faces = [f for f in bm.faces if f not in before]
    for face in faces:
        face.material_index, face.smooth = slot("fabric"), True
    box_uvs(bm, faces, (w, d, h - seat), surfaces["fabric"].get("tile", (1.0, 1.0)))
    glide, under = 0.012, seat - 0.004  # the legs' tops sunk into the shell
    feet = [Vector((sx * 0.195 * k, sy * 0.185 * k, 0)) for sy in (-1, 1) for sx in (-1, 1)]
    # Under the seat, well inside its outline (chair1-3): at the back, where
    # the shell starts to rise, the tops stood out behind it.
    tops = [Vector((sx * 0.12 * k, (-0.09 if sy < 0 else 0.04) * k, under))
            for sy in (-1, 1) for sx in (-1, 1)]

    def on_leg(n: int, z: float) -> Vector:
        return feet[n].lerp(tops[n], z / under)

    wood = []
    for n in range(4):
        steel = tube(bm, feet[n], on_leg(n, glide), 0.009, 0.010)
        for face in steel:
            face.material_index = slot("steel")
        wood += tube(bm, on_leg(n, glide), tops[n], 0.010, 0.017)
    # Front and back stretchers, then the sides', between the legs' axes.
    for a, b, z in ((0, 1, 0.27), (2, 3, 0.27), (0, 2, 0.37), (1, 3, 0.37)):
        wood += tube(bm, on_leg(a, z), on_leg(b, z), 0.008, 0.008, segments=10)
    for face in wood:
        face.material_index = slot("beech")
    box_uvs(bm, wood, (0.03, 0.03, seat), surfaces["beech"].get("tile", (1.0, 1.0)))


#: The sofa as photographed (sofa1-3): its back and arms one thick band of
#: boucle at one height, round in plan where the back turns into the arms
#: and at the arms' fronts, its top rolled over; a deep seat cushion on a
#: base set back under it; two cushions leaning on the back. Metres: the
#: arms' and the back's thickness, the radius of their top, of the corners
#: in plan (the arms' fronts, where back meets arm, the back's outer
#: corners), the base's and the cushion's top.
SOFA_ARM, SOFA_BACK, SOFA_ROLL = 0.27, 0.25, 0.115
SOFA_CORNERS = {"front": 0.11, "inner": 0.15, "back": 0.1}
SOFA_BASE, SOFA_SEAT = 0.24, 0.45


def rounded_prism(outline: list[tuple[float, float]], z0: float, z1: float,
                  corners: list[float], top: float, bottom: float) -> bmesh.types.BMesh:
    """
    A prism on `outline` (counter-clockwise in plan) from z0 to z1, each
    upright corner rounded to its radius in `corners` (0: left sharp), then
    its top edge rolled over by `top` and its foot by `bottom`: a soft,
    upholstered block. Round where it is round, flat where it is flat.
    """
    bm = bmesh.new()
    low = [bm.verts.new((x, y, z0)) for x, y in outline]
    high = [bm.verts.new((x, y, z1)) for x, y in outline]
    bm.faces.new(high)
    bm.faces.new(list(reversed(low)))
    n = len(outline)
    uprights = []
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new([low[i], low[j], high[j], high[i]])
        uprights.append(bm.edges.get((low[i], high[i])))
    # Largest first: a corner rounded small beside a large one is not clamped.
    for radius in sorted({r for r in corners if r > 0}, reverse=True):
        edges = [e for e, r in zip(uprights, corners, strict=True) if r == radius]
        bmesh.ops.bevel(bm, geom=edges, offset=radius, segments=8, profile=0.5,
                        affect="EDGES", clamp_overlap=True)
    for z, radius, segments in ((z1, top, 8), (z0, bottom, 3)):
        if radius <= 0:
            continue
        rim = [e for e in bm.edges
               if all(abs(v.co.z - z) < 1e-6 for v in e.verts) and len(e.link_faces) == 2
               and any(abs(f.normal.z) < 0.5 for f in e.link_faces)]
        bmesh.ops.bevel(bm, geom=rim, offset=radius, segments=segments, profile=0.5,
                        affect="EDGES", clamp_overlap=True)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def sofa(bm: bmesh.types.BMesh, w: float, h: float, d: float, slot, surfaces: dict) -> None:
    """The sofa as photographed (SOFA_*): the wrap of back and arms, the
    base and seat cushion between the arms, two cushions on the back."""
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    a, b, c = SOFA_ARM, SOFA_BACK, SOFA_CORNERS
    inside = y1 - b  # the back's face, where the seat meets it
    wrap = rounded_prism(
        [(x0, y0), (x0 + a, y0), (x0 + a, inside), (x1 - a, inside),
         (x1 - a, y0), (x1, y0), (x1, y1), (x0, y1)],
        0.0, h,
        [c["front"], c["front"], c["inner"], c["inner"], c["front"], c["front"],
         c["back"], c["back"]],
        SOFA_ROLL, 0.02)
    # Between the arms, tucked a little into them and into the back.
    base = rounded_prism(
        [(x0 + a - 0.02, y0 + 0.05), (x1 - a + 0.02, y0 + 0.05),
         (x1 - a + 0.02, inside + 0.02), (x0 + a - 0.02, inside + 0.02)],
        0.02, SOFA_BASE, [0.03] * 4, 0.03, 0.01)
    seat = rounded_prism(
        [(x0 + a + 0.004, y0 + 0.015), (x1 - a - 0.004, y0 + 0.015),
         (x1 - a - 0.004, inside + 0.03), (x0 + a + 0.004, inside + 0.03)],
        SOFA_BASE, SOFA_SEAT, [0.05] * 4, 0.07, 0.03)
    parts = [wrap, base, seat]
    # The two cushions: lying back against it, a little turned, as left.
    for x, turn in ((-0.27, 7), (0.27, -5)):
        # Flat lumbar cushions, as the photos have them.
        pillow = rounded_prism([(-0.25, -0.05), (0.25, -0.05), (0.25, 0.05), (-0.25, 0.05)],
                               -0.13, 0.13, [0.045] * 4, 0.045, 0.045)
        bmesh.ops.transform(
            pillow, verts=pillow.verts,
            matrix=Matrix.Translation((x, inside - 0.1, SOFA_SEAT + 0.13))
            @ Matrix.Rotation(math.radians(turn), 4, "Z")
            @ Matrix.Rotation(math.radians(-18), 4, "X"))
        parts.append(pillow)
    tile = surfaces["boucle"].get("tile", (1.0, 1.0))
    for part in parts:
        mesh = bpy.data.meshes.new("sofa-part")
        part.to_mesh(mesh)
        part.free()
        before = set(bm.faces)
        bm.from_mesh(mesh)
        bpy.data.meshes.remove(mesh)
        faces = [f for f in bm.faces if f not in before]
        for face in faces:
            face.material_index, face.smooth = slot("boucle"), True
        box_uvs(bm, faces, (w, d, h), tile)


def curtain(bm: bmesh.types.BMesh, w: float, h: float, d: float, slot, surface: str,
            pitch: float) -> None:
    """
    A curtain on its rail, drawn across `w`: the cloth in soft vertical
    folds every `pitch` metres, deeper towards the hem, 5 mm thick (one
    sheet bakes black on its back), hanging from just under the rail to a
    centimetre off the floor; the rail a steel rod across the top.
    """
    amp = max(0.01, min(d / 2 - 0.012, 0.04))
    across = max(2, round(w / pitch)) * 8
    rows = 10
    grid = []
    for j in range(rows + 1):
        z = 0.01 + (h - 0.06) * j / rows
        deep = amp * (1.25 - 0.35 * j / rows)
        grid.append([bm.verts.new((-w / 2 + w * i / across,
                                   deep * math.sin(2 * math.pi * (w * i / across) / pitch), z))
                     for i in range(across + 1)])
    faces = [bm.faces.new((grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i]))
             for j in range(rows) for i in range(across)]
    bmesh.ops.solidify(bm, geom=faces, thickness=0.005)
    for face in bm.faces:
        face.material_index = slot(surface)
    rod = bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.009, radius2=0.009,
                                depth=w)
    bmesh.ops.rotate(bm, verts=rod["verts"], cent=(0, 0, 0),
                     matrix=Matrix.Rotation(math.radians(90), 3, "Y"))
    bmesh.ops.translate(bm, verts=rod["verts"], vec=(0, 0, h - 0.02))
    for face in {f for v in rod["verts"] for f in v.link_faces}:
        face.material_index = slot("steel")
        face.smooth = True


#: What hangs on a rail, from the left: (surface, length, turn in degrees,
#: shift along the rail as a share of it). Facing out and fanned a little,
#: as the owner's open wardrobe has them (garderobe1-3: the burgundy leather
#: jacket in front, a pale one beside it); hung sideways, edge on, a rail of
#: them read as rolled-up sacks.
CLOTHES = {
    "open-wardrobe": [("coat-navy", 0.74, -28, 0.22), ("coat-light", 0.72, -14, 0.5),
                      ("leather", 0.58, 8, 0.76)],
    "coat-niche": [("coat-camel", 0.98, -24, 0.2), ("coat-navy", 1.02, -10, 0.5),
                   ("denim", 0.6, 12, 0.8)],
    # The hall's niche (0061, 0062): long black puffers and a navy coat on
    # hangers on its rail, so side on: facing out, a coat is 50 cm across
    # and the niche 62, and they came through its slats and its cupboard.
    "hall-wardrobe": [("puffer", 1.05, 84, 0.08), ("puffer", 1.0, 95, 0.36),
                      ("coat-navy", 0.95, 88, 0.64), ("puffer", 0.9, 93, 0.92)],
}
#: Zipped rather than buttoned: their placket is a steel zip.
ZIPPED = {"leather"}


def loft(bm: bmesh.types.BMesh, sections: list[tuple], place, material: int,
         n: int = 16, dome: bool = False) -> None:
    """
    A closed tube through round `sections` (centre across, along and up the
    garment, radii across and along), capped at its foot and, with `dome`,
    rounded over at its head: a sleeve. `place` maps the garment's (across
    its front, along its shoulders, height) to the piece's frame.
    """
    loops = []
    for cx, cy, z, rx, ry in sections:
        loops.append([bm.verts.new(place(cx + rx * math.cos(2 * math.pi * k / n),
                                         cy + ry * math.sin(2 * math.pi * k / n), z))
                      for k in range(n)])
    for upper, lower in zip(loops, loops[1:], strict=False):
        for k in range(n):
            bm.faces.new((upper[k], upper[(k + 1) % n], lower[(k + 1) % n],
                          lower[k])).material_index = material
    head, foot = sections[0], sections[-1]
    top = bm.verts.new(place(head[0], head[1], head[2] + (head[4] if dome else 0.0)))
    end = bm.verts.new(place(foot[0], foot[1], foot[2]))
    for k in range(n):
        bm.faces.new((loops[0][(k + 1) % n], loops[0][k], top)).material_index = material
        bm.faces.new((loops[-1][k], loops[-1][(k + 1) % n], end)).material_index = material


def garment(bm: bmesh.types.BMesh, x: float, ym: float, rail: float, length: float,
            turn: float, seed: int, cloth: int, lining: int, closure: int, steel: int) -> None:
    """
    A jacket or coat on a hanger hooked over a rail along x at depth `ym`,
    height `rail`, facing out (-y) when `turn` is 0, swung by `turn` about
    its hook.

    Its body is its outline inflated: a grid over the front-view outline
    (collar band at the back of the neck, shoulders sloping down from it,
    the chest, a slightly flared hem) whose front bulges up to 4 cm and back
    3 cm, rounding off over the last 5 cm to the outline, where they meet.
    On the front: a V opening the lining shows through, lapels standing up
    along it, a placket (or a zip) below, two pocket flaps, and soft folds
    growing towards the hem. Sleeves are tubes hanging from rounded
    shoulders just in front of the body's sides, to cuffs above the hem.
    """
    import random

    rng = random.Random(seed)
    c, s_ = math.cos(turn), math.sin(turn)
    top = rail - 0.06

    def place(front: float, across: float, z: float) -> tuple[float, float, float]:
        # The garment's front (+front) faces -y at turn 0.
        lx, ly = across, -front
        return (x + lx * c - ly * s_, ym + lx * s_ + ly * c, z)

    def half_width(v: float) -> float:
        # Half the body's width `v` metres below the collar's top.
        if v < 0.09:
            return 0.075 + (0.205 - 0.075) * min(max((v - 0.01) / 0.08, 0.0), 1.0) ** 0.6
        f = (v - 0.09) / max(length - 0.09, 0.1)
        return 0.205 - 0.02 * math.sin(math.pi * min(f, 1.0)) + 0.012 * f

    def shoulder(s: float) -> float:
        # Where the body starts, down from the collar's top, across it (-1..1).
        return 0.085 * max(0.0, abs(s) - 0.35) / 0.65

    # The grid's middle columns follow the V (and below it the placket), so
    # their edges run straight: across a plain grid they stepped.
    outer, inner, rows = 10, 6, 44
    cols = 2 * outer + inner
    collar, depth_v, placket = 0.025, min(0.3, 0.42 * length), 0.045
    phase = rng.uniform(0, 6.3)

    def opening(v: float) -> float:
        # The V's (or below it the placket's) half-width, as a share across.
        f = min(max((v - collar) / (depth_v - collar), 0.0), 1.0)
        return max(0.5 * (1 - f), placket)

    def across(k: int, v: float) -> float:
        w = opening(v)
        if k <= outer:
            return -1 + (1 - w) * k / outer
        if k <= outer + inner:
            return -w + 2 * w * (k - outer) / inner
        return 1 - (1 - w) * (cols - k) / outer

    front_v, back_v, rows_v = {}, {}, []
    for j in range(rows + 1):
        t = j / rows
        v = t * length
        rows_v.append(v)
        for i in range(cols + 1):
            s = across(i, v)
            v_here = max(v, shoulder(s)) if j == 0 else shoulder(s) + t * (length - shoulder(s))
            hw = half_width(v_here)
            u = s * hw
            edge = min((1 - abs(s)) * hw, (v_here - shoulder(s)) * 0.8, length - v_here)
            puff = min(edge / 0.06, 1.0)
            puff = math.sqrt(max(0.0, puff * (2 - puff)))
            drape = min(max(v_here / length, 0.0), 1.0) ** 1.5
            fold = 0.008 * drape * (math.sin(9 * s + phase) + 0.35 * math.sin(17 * s - 2 * phase))
            front = 0.04 * puff + fold * puff
            back = -0.03 * puff
            if i in (0, cols) or j in (0, rows):
                vert = bm.verts.new(place(0.0, u, top - v_here))
                front_v[i, j] = back_v[i, j] = vert
                continue
            in_v = outer < i < outer + inner and collar < v_here < depth_v
            lapel = i in (outer, outer + inner) and v_here < depth_v + 0.02
            if in_v:
                front = 0.55 * front - 0.006  # the lining, a little way back inside
            elif lapel:
                front += 0.007  # the lapels stand up along the V
            front_v[i, j] = bm.verts.new(place(front, u, top - v_here))
            back_v[i, j] = bm.verts.new(place(back, u, top - v_here))
    for j in range(rows):
        v = (rows_v[j] + rows_v[j + 1]) / 2
        for i in range(cols):
            quad = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
            face = bm.faces.new([front_v[k] for k in quad])
            middle = outer <= i < outer + inner
            face.material_index = (lining if middle and collar < v < depth_v
                                   else closure if middle and v >= depth_v else cloth)
            bm.faces.new([back_v[k] for k in reversed(quad)]).material_index = cloth
    # Pocket flaps, a little proud of the front at the hips.
    for side in (-1, 1):
        v = 0.62 * length
        for z0, z1, front in ((v, v + 0.035, 0.044),):
            loft(bm, [(front, side * 0.11, top - z0, 0.004, 0.065),
                      (front, side * 0.11, top - z1, 0.004, 0.065)], place, cloth, n=8)
    # Sleeves from rounded shoulders, in front of the body's sides.
    sleeve = min(0.6, length - 0.06)
    for side in (-1, 1):
        rings = []
        for f in (0.0, 0.15, 0.35, 0.55, 0.75, 1.0):
            v = 0.07 + f * (sleeve - 0.07)
            rings.append((0.006, side * (half_width(0.1) + 0.01 - 0.012 * f), top - v,
                          0.042 - 0.006 * f, 0.05 - 0.01 * f))
        loft(bm, rings, place, cloth, n=16, dome=True)
    # The hanger's hook, up from the collar and over the rail.
    for box in ((x - 0.002, x + 0.002, ym - 0.002, ym + 0.002, top, rail + 0.016),
                (x - 0.002, x + 0.002, ym - 0.022, ym + 0.002, rail + 0.012, rail + 0.016),
                (x - 0.002, x + 0.002, ym - 0.022, ym - 0.018, rail - 0.012, rail + 0.014)):
        cube = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, verts=cube["verts"],
                        vec=(box[1] - box[0], box[3] - box[2], box[5] - box[4]))
        bmesh.ops.translate(bm, verts=cube["verts"], vec=((box[0] + box[1]) / 2,
                                                          (box[2] + box[3]) / 2,
                                                          (box[4] + box[5]) / 2))
        for face in {f for v in cube["verts"] for f in v.link_faces}:
            face.material_index = steel


def superquadric(bm: bmesh.types.BMesh, centre, radii, round_side: float, round_top: float,
                 n: int = 32, m: int = 16) -> list:
    """A closed superquadric: `round_side` < 1 squares it off in plan, `round_top` its profile."""
    def pw(c: float, e: float) -> float:
        return math.copysign(abs(c) ** e, c)

    cx, cy, cz = centre
    rx, ry, rz = radii
    rows = []
    for j in range(1, m):
        v = -math.pi / 2 + math.pi * j / m
        ring = pw(math.cos(v), round_top)
        rows.append([bm.verts.new((cx + rx * ring * pw(math.cos(u), round_side),
                                   cy + ry * ring * pw(math.sin(u), round_side),
                                   cz + rz * pw(math.sin(v), round_top)))
                     for u in (2 * math.pi * i / n for i in range(n))])
    low, high = bm.verts.new((cx, cy, cz - rz)), bm.verts.new((cx, cy, cz + rz))
    faces = []
    for a, b in zip(rows, rows[1:], strict=False):
        for i in range(n):
            faces.append(bm.faces.new((a[i], a[(i + 1) % n], b[(i + 1) % n], b[i])))
    for i in range(n):
        faces.append(bm.faces.new((rows[0][(i + 1) % n], rows[0][i], low)))
        faces.append(bm.faces.new((rows[-1][i], rows[-1][(i + 1) % n], high)))
    # Facing out: the top's normal points up.
    probe = faces[-1]
    probe.normal_update()
    if probe.normal.z < 0:
        bmesh.ops.reverse_faces(bm, faces=faces)
    return faces


def drape(t: float, half: float, r: float) -> tuple[float, float]:
    """
    A sheet laid over an edge `half` from the middle, rounding over it with
    radius `r` and then hanging: where its flat coordinate `t` ends up
    across, and how far it has dropped.
    """
    excess = abs(t) - half
    if excess <= 0:
        return t, 0.0
    arc = math.pi / 2 * r
    if excess < arc:
        angle = excess / r
        return math.copysign(half + r * math.sin(angle), t), r * (1 - math.cos(angle))
    return math.copysign(half + r, t), r + excess - arc


def bedding(item: dict, piece: bpy.types.Object, surfaces: dict) -> bpy.types.Object | None:
    """
    A bed's duvet and pillows (0054, 0067, 0109, 0140) as soft shapes, their
    own object lit per vertex, placed as the bed is. The duvet is a sheet
    laid over the mattress: puffed up in its middle, soft folds across it,
    rounding over the mattress's edges and hanging down its sides and foot,
    its top turned down; the pillows plump and squared off. As boxes they
    read as a render, not a bed.
    """
    finish = item.get("finish")
    if finish not in ("box-bed", "folding-bed"):
        return None
    import random

    opts = item.get("options") or {}
    w, h, d = item["size"]
    rng = random.Random(len(item["id"]))
    if finish == "box-bed":
        # Width along x, the head at +y.
        head = opts.get("head", 0.08)
        base, mattress = opts.get("base", 0.36), opts.get("mattress", 0.2)
        width, foot, top_end = w, -d / 2, d / 2 - head
        deck, hang_to = base + mattress, base + 0.04

        def place(a: float, b: float, z: float) -> tuple[float, float, float]:
            return (a, b, z)
    else:
        # The folding bed lies along x, its head at +x.
        width, foot, top_end = d, -w / 2, w / 2
        deck, hang_to = h - 0.01, h - 0.13

        def place(a: float, b: float, z: float) -> tuple[float, float, float]:
            return (b, -a, z)

    half, r = width / 2 - 0.01, 0.045
    pillow_d = 0.42 if finish == "box-bed" else 0.45
    end = top_end - pillow_d - 0.02  # the duvet's top edge, turned down, below the pillows
    if finish == "folding-bed":
        end = foot + 0.62 * (top_end - foot)  # pushed down the bed, as photographed (0067)
    hang = max(0.05, deck - hang_to - r)
    side = half + math.pi / 2 * r + hang
    foot_hang = math.pi / 2 * r + hang
    remap = opts.get("surfaces", {})
    duvet_name, pillow_name = remap.get("duvet", "duvet"), remap.get("pillow", "pillow")
    tile = surfaces[duvet_name].get("tile", (1.0, 1.0))
    wave = 0.012 if finish == "box-bed" else 0.03
    phase = [rng.uniform(0, 6.3) for _ in range(4)]

    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    nu, nv = 56, 64
    grid, flat = {}, {}
    q0, q1 = foot - foot_hang, end
    for j in range(nv + 1):
        q = q0 + (q1 - q0) * j / nv
        for i in range(nu + 1):
            p = -side + 2 * side * i / nu
            a, drop_a = drape(p, half, r)
            if q < foot:
                b, drop_b = drape(q - foot, 0.0, r)
                b += foot
            else:
                b, drop_b = q, 0.0
            # Puffed up in the middle, thinning to its edges; a turned-down top.
            inside = min(half - abs(p), q - foot, end - q) if drop_a == 0 and drop_b == 0 else 0.0
            puff = 0.012 + 0.05 * min(max(inside / 0.25, 0.0), 1.0) ** 0.6
            cuff = 0.02 * min(max(1 - (end - q) / 0.28, 0.0), 1.0)
            gap = end - q
            roll = (0.0 if gap > 0.035
                    else 0.035 - math.sqrt(max(0.0, 0.035 ** 2 - (0.035 - gap) ** 2)))
            fold = wave * (math.sin(7.5 * p + phase[0]) * math.sin(3.1 * q + phase[1])
                           + 0.5 * math.sin(13 * p + 5 * q + phase[2]))
            z = deck + puff + cuff - roll + fold * (1.0 if inside > 0 else 0.4) - drop_a - drop_b
            # What hangs sways a little in and out.
            sway = 0.008 * math.sin(9 * q + phase[3]) * min(drop_a / 0.1, 1.0)
            a += math.copysign(sway, a)
            grid[i, j] = bm.verts.new(place(a, b, z))
            flat[i, j] = (p / tile[0], q / tile[1])
    faces = []
    for j in range(nv):
        for i in range(nu):
            quad = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
            face = bm.faces.new([grid[k] for k in quad])
            for loop, k in zip(face.loops, quad, strict=True):
                loop[uv].uv = flat[k]
            faces.append(face)
    middle = faces[len(faces) // 2 + nu // 2]
    middle.normal_update()
    if middle.normal.z < 0:
        bmesh.ops.reverse_faces(bm, faces=faces)
    bmesh.ops.solidify(bm, geom=faces, thickness=0.018)
    duvet_faces = set(bm.faces)
    # The pillows: plump, squared off in plan, resting on the mattress.
    count = 2 if width > 1.2 else 1
    pw = (width - 0.1) / 2 - 0.02 if count == 2 else min(0.62, width - 0.16)
    for k in range(count):
        ca = (-width / 2 + 0.05 + pw / 2 + k * (pw + 0.04)) if count == 2 else 0.0
        cb = top_end - pillow_d / 2 - 0.03
        radii = (pw / 2, pillow_d / 2, 0.075)
        if finish == "folding-bed":
            radii = (pillow_d / 2, pw / 2, 0.075)
        superquadric(bm, place(ca, cb, deck + 0.055), radii, 0.32, 0.7)
    for f in bm.faces:
        f.material_index = 0 if f in duvet_faces else 1
    mesh = bpy.data.meshes.new(f"{item['id']}-bedding")
    bm.to_mesh(mesh)
    bm.free()
    for name in (duvet_name, pillow_name):
        mesh.materials.append(surface_material(name, surfaces[name]))
    mesh.shade_smooth()
    obj = bpy.data.objects.new(mesh.name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.matrix_world = piece.matrix_world.copy()
    VERTEX_LIT.add(obj.name)
    return obj


def hang_clothes(item: dict, piece: bpy.types.Object, surfaces: dict) -> bpy.types.Object | None:
    """
    The clothes on an open wardrobe's or the coat niche's rail, as their own
    object (lit per vertex: folded cloth unwrapped into many small islands,
    as the generated models did), placed as the piece is.
    """
    hung = CLOTHES.get(item.get("finish") or "")
    if not hung:
        return None
    w, h, d = item["size"]
    x0, x1, y0, y1 = -w / 2, w / 2, -d / 2, d / 2
    ym = (y0 + y1) / 2
    rail = h - 0.45 if item["finish"] == "open-wardrobe" else h - 0.39
    xi0, xi1 = x0 + 0.04, x1 - 0.04
    if item["finish"] == "hall-wardrobe":  # the rail across the niche only
        opts = item.get("options") or {}
        xi0 = x0 + opts.get("slats", 0.075) + 0.04
        xi1 = xi0 + opts.get("niche", 0.62) - 0.1
        rail = opts.get("nicheTop", 1.98) - 0.1
    surfaces_used = sorted({name for name, *_ in hung}) + ["lining", "steel"]
    mesh = bpy.data.meshes.new(f"{item['id']}-clothes")
    bm = bmesh.new()
    for i, (name, length, turn, along) in enumerate(hung):
        material = surfaces_used.index(name)
        closure = surfaces_used.index("steel") if name in ZIPPED else material
        garment(bm, xi0 + along * (xi1 - xi0), ym, rail, length, math.radians(turn), seed=i,
                cloth=material, lining=surfaces_used.index("lining"), closure=closure,
                steel=surfaces_used.index("steel"))
    # Some faces come out wound inwards; the bake lit those from inside: black.
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    for name in surfaces_used:
        mesh.materials.append(surface_material(name, surfaces[name]))
    obj = bpy.data.objects.new(mesh.name, mesh)
    bpy.context.collection.objects.link(obj)
    # Subdivided once: the grid's hems and edges zig-zagged, its folds creased.
    smooth = obj.modifiers.new("smooth", "SUBSURF")
    smooth.levels = smooth.render_levels = 1
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=smooth.name)
    mesh.shade_smooth()
    mesh.set_sharp_from_angle(angle=math.radians(60))
    plain_uvs(obj)
    obj.matrix_world = piece.matrix_world.copy()
    VERTEX_LIT.add(obj.name)
    return obj


#: Glass that lets a little light through: the shower's frosted door and the
#: balcony door's pane, by surface name (alpha). The kitchen's glazed doors
#: are lit from inside (0078): opaque, their frosting white, not the dark
#: carcass behind it showing through blue-green.
TRANSLUCENT_GLASS = {"frosted": 0.55, "door-glass": 0.12}


def surface_material(name: str, surface: dict) -> bpy.types.Material:
    """A surface from the spec: its colour or its texture, and its roughness."""
    mat = bpy.data.materials.get(f"surface-{name}")
    if mat is not None:
        return mat
    mat = material(f"surface-{name}", surface["colour"], surface["roughness"])
    if name in TRANSLUCENT_GLASS:
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Alpha"].default_value = TRANSLUCENT_GLASS[name]
    if surface.get("texture"):
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(surface["texture"])
        links.new(tex.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
        if surface.get("cutout"):
            # A shape cut out of its photo (the clock): alpha clipped at a half,
            # which the glTF exporter writes as alphaMode MASK.
            clip = nodes.new("ShaderNodeMath")
            clip.operation = "GREATER_THAN"
            clip.inputs[1].default_value = 0.5
            links.new(tex.outputs["Alpha"], clip.inputs[0])
            links.new(clip.outputs["Value"], nodes["Principled BSDF"].inputs["Alpha"])
    return mat


def box_uvs(bm: bmesh.types.BMesh, faces, size: tuple[float, float, float],
            tile: tuple[float, float] = (1.0, 1.0)) -> None:
    """Texture coordinates on a box's faces, one repeat per `tile` metres, running along
    each face's longer side, so a veneer's grain follows a board's length."""
    layer = bm.loops.layers.uv.verify()
    for face in faces:
        axis = max(range(3), key=lambda i: abs(face.normal[i]))
        a, b = [i for i in range(3) if i != axis]
        if size[b] > size[a]:
            a, b = b, a
        for loop in face.loops:
            loop[layer].uv = (loop.vert.co[a] / tile[0], loop.vert.co[b] / tile[1])


def finished(item: dict, surfaces: dict) -> bpy.types.Object:
    """A piece built from its finish's parts, at its true size; one object, one material each."""
    w, h, d = item["size"]
    mesh = bpy.data.meshes.new(item["id"])
    bm = bmesh.new()
    slots: list[str] = []

    def slot(surface: str) -> int:
        if surface not in slots:
            slots.append(surface)
        return slots.index(surface)

    opts = item.get("options") or {}
    if item["finish"] == "barrel-chair":
        barrel_chair(bm, w, h, d, slot, opts.get("surface", "velvet"))
    elif item["finish"] == "mirror":
        mirror(bm, w, h, d, slot, opts)
    elif item["finish"] == "clock":
        wall_clock(bm, w, h, d, slot, opts)
    elif item["finish"] == "glass-door":
        glass_leaf(bm, w, h, slot, opts)
    elif item["finish"] == "wc":
        wall_wc(bm, w, h, d, slot, opts)
    elif item["finish"] == "yucca":
        yucca(bm, w, h, d, slot)
    elif item["finish"] == "placemat":
        placemat(bm, w, h, d, slot)
    elif item["finish"] == "curtain":
        curtain(bm, w, h, d, slot, opts.get("surface", "sheer"), opts.get("pitch", 0.11))
    elif item["finish"] == "stool":
        stool(bm, w, h, d, slot, surfaces)
    elif item["finish"] == "sofa":
        sofa(bm, w, h, d, slot, surfaces)
    elif item["finish"] == "pendant":
        # A drum shade on a cable from the ceiling. Its light is baked per
        # vertex through the fabric (vertex_light), so its side is cut into
        # rings for the light to vary down it.
        # A table lamp's shade is the same drum, as tall as its box, no cable.
        radius, shade = min(w, d) / 2, min(0.28, h)
        cone = bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=radius,
                                     radius2=radius, depth=shade)
        bmesh.ops.translate(bm, verts=cone["verts"], vec=(0, 0, shade / 2))
        sides = [e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > shade / 2]
        bmesh.ops.subdivide_edges(bm, edges=sides, cuts=6, use_grid_fill=True)
        if h - shade > 0.005:
            cable = bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.scale(bm, verts=cable["verts"], vec=(0.008, 0.008, h - shade))
            bmesh.ops.translate(bm, verts=cable["verts"], vec=(0, 0, shade + (h - shade) / 2))
        for face in bm.faces:
            face.material_index = slot(opts.get("surface", "shade"))
    else:
        # A piece in other colours than the finish's own: `surfaces` names
        # the dressing's palette entry each of its surfaces is drawn in.
        remap = opts.get("surfaces", {})
        for surface, (px0, px1, py0, py1, pz0, pz1) in finish_parts(item["finish"], w, h, d, opts):
            surface = remap.get(surface, surface)
            cube = bmesh.ops.create_cube(bm, size=1.0)
            size = (px1 - px0, py1 - py0, pz1 - pz0)
            bmesh.ops.scale(bm, verts=cube["verts"], vec=size)
            bmesh.ops.translate(bm, verts=cube["verts"],
                                vec=((px0 + px1) / 2, (py0 + py1) / 2, (pz0 + pz1) / 2))
            faces = {f for v in cube["verts"] for f in v.link_faces}
            if surface == "mirror":
                # Only its face: the viewer aims a mirror's reflection along its normal.
                back = [f for f in faces if f.normal.y > -0.5]
                bmesh.ops.delete(bm, geom=back, context="FACES_ONLY")
                faces = {f for f in faces if f.is_valid}
            for face in faces:
                face.material_index = slot(surface)
            box_uvs(bm, faces, size, surfaces[surface].get("tile", (1.0, 1.0)))
            if item["finish"] == "picture" and surface == opts.get("image"):
                picture_uvs(bm, faces, (px0, px1, pz0, pz1), py0)
        if item["finish"] == "island":
            add_slab(bm, island_top(w, h, d, opts), slot, surfaces)
    bm.to_mesh(mesh)
    bm.free()
    for surface in slots:
        mesh.materials.append(surface_material(surface, surfaces[surface]))
    obj = bpy.data.objects.new(item["id"], mesh)
    bpy.context.collection.objects.link(obj)
    if item["finish"] == "sofa":
        mesh.set_sharp_from_angle(angle=math.radians(40))
    if item["finish"] == "curtain":
        # Lit per vertex through the fabric, as the pendant's shade. It lets
        # the window's light into the room: in the bake it casts no shadow.
        mesh.shade_smooth()
        plain_uvs(obj)
        VERTEX_LIT.add(obj.name)
        TRANSLUCENT.add(obj.name)
        obj.visible_shadow = False
    if item["finish"] == "pendant":
        mesh.shade_smooth()
        mesh.set_sharp_from_angle(angle=math.radians(40))
        VERTEX_LIT.add(obj.name)
        TRANSLUCENT.add(obj.name)
        # `bulb`: a bulb (a `lights` entry) inside it, whose light it lets out.
        obj.visible_shadow = not opts.get("bulb")
    if item["finish"] in ("yucca", "backdrop"):
        # Leaves too thin for the light map; house fronts too big for it.
        VERTEX_LIT.add(obj.name)
    if item["finish"] in ("barrel-chair", "wc"):
        mesh.shade_smooth()
        mesh.set_sharp_from_angle(angle=math.radians(50))
    if item["finish"] in SOFTNESS:
        bevel = obj.modifiers.new("soft", "BEVEL")
        bevel.width, bevel.segments = SOFTNESS[item["finish"]]
        if item["finish"] == "air-con":
            # Only the body's edges (its vertices span its width; the
            # outlet's are inset): bevelled too, its few millimetres clamped
            # the whole bevel to nothing. The front's lower edge rounded
            # fully, the rest eased. Its curves smooth, its flat faces flat.
            import numpy as np

            mesh = obj.data
            co = np.array([v.co[:] for v in mesh.vertices])
            body = ((np.abs(co[:, 0] - co[:, 0].min()) < 1e-5)
                    | (np.abs(co[:, 0] - co[:, 0].max()) < 1e-5))
            low_front = ((np.abs(co[:, 1] - co[body, 1].min()) < 1e-5)
                         & (np.abs(co[:, 2] - co[body, 2].min()) < 1e-5))
            weight = mesh.attributes.new("bevel_weight_edge", "FLOAT", "EDGE")
            for edge in mesh.edges:
                ends = list(edge.vertices)
                weight.data[edge.index].value = (
                    0.0 if not body[ends].all() else 1.0 if low_front[ends].all() else 0.12)
            bevel.limit_method = "WEIGHT"
            mesh.shade_smooth()
            bevel.harden_normals = True
    if not obj.data.uv_layers:
        plain_uvs(obj)
    return obj


def barrel_chair(bm: bmesh.types.BMesh, w: float, h: float, d: float, slot,
                 surface: str) -> None:
    """
    The tub chairs (living room 0095-0098, study 0069, 0139): a seat to sit
    in, as photographed. A deep cushion at sitting height (44 cm) on an
    upholstered base, its front square with rounded corners, reaching the
    chair's front; round the back of it a padded wall, 75 cm at the back,
    falling to 60 cm arms whose thick ends round off beside the seat; every
    edge a soft roll, upholstered to the floor.
    """
    radius, wall, n = min(w, d) / 2, 0.1, 72
    base_top, seat_top, arm = 0.3, 0.44, 0.6
    inner = radius - wall
    front = -radius + 0.03  # the seat's front edge
    a0, a1 = math.radians(-24), math.radians(204)
    mat = slot(surface)
    made: list = []

    def top(a: float) -> float:
        return arm + (h - arm) * max(0.0, math.sin(a)) ** 1.6

    def section(a: float, lower: float) -> list[tuple[float, float]]:
        """(radius, height) round the wall's section: up outside, over its roll, down inside."""
        z_top = top(a) - lower
        roll = wall / 2
        mid = radius - roll
        pts = [(radius - 0.012, 0.0), (radius + 0.006, 0.3 * z_top), (radius + 0.004, z_top - roll)]
        pts += [(mid + roll * math.cos(math.pi * k / 8),
                 z_top - roll + roll * math.sin(math.pi * k / 8)) for k in range(1, 8)]
        pts += [(radius - wall + 0.004, z_top - roll), (radius - wall, seat_top + 0.06),
                (radius - wall + 0.006, seat_top - 0.01)]
        return pts

    def ring(a: float, pts: list[tuple[float, float]]) -> list:
        c, s_ = math.cos(a), math.sin(a)
        return [bm.verts.new((r * c, r * s_, z)) for r, z in pts]

    def bridge(ra: list, rb: list) -> None:
        m = len(ra)  # the section closed under itself: a tube
        for k in range(m):
            made.append(bm.faces.new((ra[k], ra[(k + 1) % m], rb[(k + 1) % m], rb[k])))

    rings = []
    for i in range(n + 1):
        a = a0 + (a1 - a0) * i / n
        # The arms slope down a little more over their last stretch.
        edge = min(i, n - i) / 8
        lower = 0.0 if edge >= 1 else 0.06 * (1 - edge) ** 2
        rings.append((a, section(a, lower)))
    loops = [ring(a, pts) for a, pts in rings]
    for la, lb in zip(loops, loops[1:], strict=False):
        bridge(la, lb)
    # Each arm's end rounded off: the section shrinks to its middle over a
    # few steps past the end, like the end of a bolster.
    for (a, pts), loop, way in ((rings[0], loops[0], -1), (rings[-1], loops[-1], 1)):
        cr = sum(p[0] for p in pts) / len(pts)
        cz = sum(p[1] for p in pts) / len(pts)
        prev = loop
        for k in range(1, 5):
            f = math.cos(math.pi / 2 * k / 5)
            step = a + way * (wall / radius) * 0.45 * math.sin(math.pi / 2 * k / 5)
            nxt = ring(step, [(cr + (r - cr) * f, cz + (z - cz) * f) for r, z in pts])
            bridge(prev, nxt) if way > 0 else bridge(nxt, prev)
            prev = nxt
        end = a + way * (wall / radius) * 0.45
        tip = bm.verts.new((cr * math.cos(end), cr * math.sin(end), cz))
        m = len(prev)
        for k in range(m):
            j = (k + 1) % m
            tri = (prev[k], prev[j], tip) if way > 0 else (prev[j], prev[k], tip)
            made.append(bm.faces.new(tri))

    def seat_outline(grow: float) -> list[tuple[float, float]]:
        """Round at the back inside the wall; square with soft corners in front."""
        pts = []
        reach = inner + grow
        depth = -(front + 0.02) + grow
        for k in range(64):
            a = 2 * math.pi * k / 64
            c, s_ = math.cos(a), math.sin(a)
            if s_ >= 0:
                pts.append((reach * c, reach * s_))
            else:
                pts.append((reach * math.copysign(abs(c) ** 0.3, c),
                            -depth * abs(s_) ** 0.3))
        return pts

    def slab(z_rings: list[tuple[float, float]], dome: float) -> None:
        """A soft block through outlines grown by `g` at heights `z`, domed on top."""
        loops = [[bm.verts.new((x, y, z)) for x, y in seat_outline(g)] for z, g in z_rings]
        for la, lb in zip(loops, loops[1:], strict=False):
            for k in range(64):
                made.append(bm.faces.new((la[k], la[(k + 1) % 64], lb[(k + 1) % 64], lb[k])))
        made.append(bm.faces.new(list(reversed(loops[0]))))
        centre = bm.verts.new((0.0, front * 0.2, z_rings[-1][0] + dome))
        for k in range(64):
            made.append(bm.faces.new((loops[-1][k], loops[-1][(k + 1) % 64], centre)))

    # The base, upholstered round its front, and the cushion on it.
    slab([(0.0, -0.015), (base_top - 0.01, -0.015), (base_top, -0.025)], 0.0)
    slab([(base_top, -0.03), (base_top + 0.025, -0.005), (seat_top - 0.03, -0.005),
          (seat_top, -0.035)], 0.015)
    for face in made:
        face.material_index = mat
    bmesh.ops.recalc_face_normals(bm, faces=made)


def d_shape(half_w: float, back: float, front: float, n: int = 48) -> list[tuple[float, float]]:
    """
    A WC's plan (0124, 0138): square to the wall at `back` (+y), its sides
    straight for the back half, then round to the `front` (-y), as `n`
    points the same way round every time, so outlines loft into each other.
    """
    mid = back - 0.45 * (back - front)
    corner = 0.02
    pts = [(half_w - corner, back), (half_w, back - corner), (half_w, mid)]
    for k in range(1, n - 5):
        t = math.pi * k / (n - 5)
        pts.append((half_w * math.cos(t), mid - (mid - front) * math.sin(t)))
    pts += [(-half_w, mid), (-half_w, back - corner), (-half_w + corner, back)]
    return pts


def wall_wc(bm: bmesh.types.BMesh, w: float, h: float, d: float, slot, opts: dict) -> None:
    """
    The wall-hung WC (0124, 0125, 0141): the bowl round at the front and
    square to the wall, its underside tapering back to the wall, a deep
    bowl inside its rim, the seat a ring on the rim, and the lid up against
    the wall (`open`). All white ceramic.
    """
    b = d / 2  # the wall
    f = b - d  # the front
    half = w / 2
    outer = [(half, b, f, h), (half, b, f, h - 0.012), (half - 0.005, b, f + 0.01, h - 0.1),
             (half - 0.03, b, f + 0.09, 0.27), (half - 0.07, b, f + 0.24, h - 0.2)]
    inner = [(half - 0.05, b - 0.08, f + 0.035, h), (half - 0.09, b - 0.12, f + 0.09, h - 0.1),
             (half - 0.14, b - 0.2, f + 0.15, h - 0.17)]
    rings = [[bm.verts.new((x, y, z)) for x, y in d_shape(hw, yb, yf)]
             for hw, yb, yf, z in outer + inner]

    def loft(a, c):
        m = len(a)
        for k in range(m):
            bm.faces.new((a[k], a[(k + 1) % m], c[(k + 1) % m], c[k]))

    for i in range(len(outer) - 1):
        loft(rings[i], rings[i + 1])
    bm.faces.new(rings[len(outer) - 1])  # its underside
    loft(rings[0], rings[len(outer)])  # the rim's top
    for i in range(len(outer), len(rings) - 1):
        loft(rings[i], rings[i + 1])
    bm.faces.new(rings[-1])  # the water
    seat = [(half - 0.004, b - 0.06, f + 0.006, h + 0.004),
            (half - 0.004, b - 0.06, f + 0.006, h + 0.022),
            (half - 0.06, b - 0.1, f + 0.05, h + 0.022),
            (half - 0.06, b - 0.1, f + 0.05, h + 0.004)]
    ring = [[bm.verts.new((x, y, z)) for x, y in d_shape(hw, yb, yf)] for hw, yb, yf, z in seat]
    for i in range(4):
        loft(ring[i], ring[(i + 1) % 4])
    if opts.get("open"):
        # The lid stands up against the wall: its plan turned up into the wall's plane.
        lid = d_shape(half - 0.004, h + 0.03, h + 0.03 + (d - 0.08))
        faces = [[bm.verts.new((x, y, z)) for x, z in lid] for y in (b - 0.075, b - 0.05)]
        loft(faces[0], faces[1])
        bm.faces.new(faces[0])
        bm.faces.new(list(reversed(faces[1])))
    for face in bm.faces:
        face.material_index = slot("basin")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)


def wall_clock(bm: bmesh.types.BMesh, w: float, h: float, d: float, slot, opts: dict) -> None:
    """
    A round wall clock (the owner had the photographed one changed): an oak
    rim standing off the wall, a cream face set into it, twelve bar markers
    and black hands at ten past ten.
    """
    r, n = min(w, h) / 2, 96
    y0, y1, cz = -d / 2, d / 2, h / 2
    lip, inset = 0.022, 0.012
    rim, face = slot(opts.get("rim", "oak-warm")), slot(opts.get("face", "clock-face"))
    ink = slot("black")

    def ring(rad: float, y: float) -> list:
        return [bm.verts.new((rad * math.sin(2 * math.pi * k / n), y,
                              cz + rad * math.cos(2 * math.pi * k / n))) for k in range(n)]

    back, front = ring(r, y1), ring(r, y0)
    lip_front, lip_back = ring(r - lip, y0), ring(r - lip, y0 + inset)
    centre = bm.verts.new((0.0, y0 + inset, cz))
    made = []
    for k in range(n):
        j = (k + 1) % n
        for quad, mat in (((back[k], back[j], front[j], front[k]), rim),
                          ((front[k], front[j], lip_front[j], lip_front[k]), rim),
                          ((lip_front[k], lip_front[j], lip_back[j], lip_back[k]), rim)):
            made.append(bm.faces.new(quad))
            made[-1].material_index = mat
        made.append(bm.faces.new((lip_back[k], lip_back[j], centre)))
        made[-1].material_index = face
    # Wound consistently: the face looks out of the wall (-y).
    probe = made[3]
    probe.normal_update()
    if probe.normal.y > 0:
        bmesh.ops.reverse_faces(bm, faces=made)

    def bar(length: float, width: float, out: float, angle: float, y: float, depth: float) -> None:
        """A box from `out` to `out + length` along `angle` (clockwise from 12)."""
        cube = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, verts=cube["verts"], vec=(width, depth, length))
        bmesh.ops.translate(bm, verts=cube["verts"], vec=(0.0, y, out + length / 2))
        bmesh.ops.rotate(bm, verts=cube["verts"], cent=(0.0, 0.0, 0.0),
                         matrix=Matrix.Rotation(angle, 3, "Y"))
        bmesh.ops.translate(bm, verts=cube["verts"], vec=(0.0, 0.0, cz))
        for f in {f for v in cube["verts"] for f in v.link_faces}:
            f.material_index = ink

    face_y = y0 + inset
    for k in range(12):
        long = k % 3 == 0
        length = 0.045 if long else 0.028
        bar(length, 0.012 if long else 0.007, r - lip - 0.02 - length, 2 * math.pi * k / 12,
            face_y - 0.002, 0.003)
    bar(0.52 * r, 0.014, -0.03, 2 * math.pi * (10 + 10 / 60) / 12, face_y - 0.006, 0.004)  # hours
    bar(0.78 * r, 0.009, -0.04, 2 * math.pi * 10 / 60, face_y - 0.011, 0.004)  # minutes
    cap = bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=0.012, radius2=0.012,
                                depth=0.006)
    bmesh.ops.rotate(bm, verts=cap["verts"], cent=(0, 0, 0),
                     matrix=Matrix.Rotation(math.pi / 2, 3, "X"))
    bmesh.ops.translate(bm, verts=cap["verts"], vec=(0.0, face_y - 0.015, cz))
    for f in {f for v in cap["verts"] for f in v.link_faces}:
        f.material_index = ink


def mirror(bm: bmesh.types.BMesh, w: float, h: float, d: float, slot, opts: dict) -> None:
    """
    A mirror on the wall: `shape` "pebble" (the hall's, 0058 and 0059: an
    egg wider at the top by `taper`) or a rectangle, its glass proud of a
    thin `frame` (metres, in `frameSurface`, brass) or frameless.
    """
    frame, y0, y1 = opts.get("frame", 0.012), -d / 2, d / 2
    taper, n = opts.get("taper", 0.3), 96

    def outline(grow: float) -> list[tuple[float, float]]:
        if opts.get("shape") != "pebble":
            hw, hh = w / 2 + grow - frame, h / 2 + grow - frame
            return [(-hw, h / 2 - hh), (hw, h / 2 - hh), (hw, h / 2 + hh), (-hw, h / 2 + hh)]
        pts = []
        for k in range(n):
            t = 2 * math.pi * k / n
            x = (w / 2 + grow - frame) * math.sin(t) * (1 + taper * math.cos(t)) / (1 + taper * 0.5)
            pts.append((x, h / 2 + (h / 2 + grow - frame) * math.cos(t)))
        return list(reversed(pts))

    if frame:
        ring = outline(frame)
        front = [bm.verts.new((x, y0, z)) for x, z in ring]
        back = [bm.verts.new((x, y1, z)) for x, z in ring]
        m = len(ring)
        rim = [bm.faces.new((front[k], front[(k + 1) % m], back[(k + 1) % m], back[k]))
               for k in range(m)]
        rim += [bm.faces.new(front), bm.faces.new(list(reversed(back)))]
        for face in rim:
            face.material_index = slot(opts.get("frameSurface", "brass"))
    glass = bm.faces.new([bm.verts.new((x, y0 - 0.002, z)) for x, z in outline(0.0)])
    glass.material_index = slot("mirror")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)


def glass_leaf(bm: bmesh.types.BMesh, w: float, h: float, slot, opts: dict) -> None:
    """
    The shower door (0123, 0130) closed, round its hinge at the origin: a
    frosted glass leaf from its `hinge` end ("start", its left seen from the
    front, or "end") across, steel hinges, a steel bar handle both sides at
    its free edge. hang_glass_door stands it open.
    """
    sign = 1 if opts.get("hinge", "start") == "start" else -1
    reach = w - 0.012

    def box(surface: str, xa: float, xb: float, ya: float, yb: float, za: float, zb: float):
        cube = bmesh.ops.create_cube(bm, size=1.0)
        lo, hi = sorted((sign * xa, sign * xb))
        bmesh.ops.scale(bm, verts=cube["verts"], vec=(hi - lo, yb - ya, zb - za))
        bmesh.ops.translate(bm, verts=cube["verts"],
                            vec=((lo + hi) / 2, (ya + yb) / 2, (za + zb) / 2))
        for face in {f for v in cube["verts"] for f in v.link_faces}:
            face.material_index = slot(surface)

    if opts.get("frame"):
        # A balcony door's leaf (0100-0102): a white frame round clear glass,
        # a lever handle at its free edge on the room side.
        f, t, frame = 0.07, 0.035, opts["frame"]
        box(opts.get("surface", "door-glass"), f, reach - f, -0.004, 0.004, 0.1, h - f)
        box(frame, 0.0, f, -t, t, 0.0, h)
        box(frame, reach - f, reach, -t, t, 0.0, h)
        box(frame, f, reach - f, -t, t, 0.0, 0.1)
        box(frame, f, reach - f, -t, t, h - f, h)
        box("steel", reach - 0.05, reach - 0.03, -t - 0.012, -t, 1.0, 1.08)
        box("steel", reach - 0.19, reach - 0.04, -t - 0.06, -t - 0.045, 1.03, 1.05)
        box("steel", reach - 0.05, reach - 0.035, -t - 0.06, -t - 0.012, 1.03, 1.05)
        return
    box(opts.get("surface", "frosted"), 0.006, reach, -0.004, 0.004, 0.02, h)
    for z in (0.25, h - 0.3):
        box("steel", 0.0, 0.05, -0.012, 0.012, z, z + 0.07)
    for side in (-1, 1):
        y_bar = side * 0.03
        box("steel", reach - 0.06, reach - 0.045, min(y_bar, y_bar + side * 0.01) - 0.005,
            max(y_bar, y_bar + side * 0.01) + 0.005, 0.85, 1.45)
        for z in (0.9, 1.4):
            box("steel", reach - 0.058, reach - 0.047, min(0.004 * side, y_bar),
                max(0.004 * side, y_bar), z - 0.005, z + 0.005)


def hang_glass_door(obj: bpy.types.Object, item: dict) -> None:
    """
    Move the glass door's origin to its hinge and stand it `open` degrees out
    of its frame towards the front; its `door` extra holds the turns (about
    +Y, as three.js turns a node, which is Blender's +Z) from there to fully
    open and to closed.
    """
    w = item["size"][0]
    opts = item.get("options") or {}
    sign = 1 if opts.get("hinge", "start") == "start" else -1
    stand = math.radians(opts.get("open", 45)) * -sign
    hinge = Matrix.Translation((-sign * w / 2, 0.0, 0.0))
    obj.matrix_world = obj.matrix_world @ hinge @ Matrix.Rotation(stand, 4, "Z")
    full = math.radians(90) * -sign
    tag = {"open": round(full - stand, 5), "close": round(-stand, 5)}
    if opts.get("doorway"):
        # It shuts a doorway (the balcony's): closed, the walk stops there.
        x, _, z = item["center"]
        tag |= {"at": [round(x, 4), round(z, 4)], "width": round(w, 4)}
    obj["door"] = tag


def picture_uvs(bm: bmesh.types.BMesh, faces, span: tuple[float, float, float, float],
                front: float) -> None:
    """
    A picture's image across its face, once: u along x, v up z. On a
    canvas's sides the image's edge wraps round, as a gallery-wrapped print's
    does: mapped flat, each side stretched one row of pixels over its depth
    and read as a smear.
    """
    x0, x1, z0, z1 = span
    w, h = x1 - x0, z1 - z0
    layer = bm.loops.layers.uv.verify()
    for face in faces:
        n = face.normal
        for loop in face.loops:
            co = loop.vert.co
            u, v, back = (co.x - x0) / w, (co.z - z0) / h, co.y - front
            if abs(n.x) > 0.5:
                u = back / w if n.x < 0 else 1 - back / w
            elif abs(n.z) > 0.5:
                v = 1 - back / h if n.z > 0 else back / h
            loop[layer].uv = (u, v)


def vanity_basin(item: dict, piece: bpy.types.Object, surfaces: dict) -> bpy.types.Object | None:
    """
    The bathroom's basin (0117, 0120): a white rectangular basin standing on
    the vanity's oak top, its rim a few centimetres wide round a deep bowl,
    a chrome lever mixer behind it. Its own object, lit per vertex.
    """
    if item.get("finish") != "bath-vanity":
        return None
    w, _, d = item["size"]
    opts = item.get("options") or {}
    x0, x1, y1 = -w / 2, w / 2, d / 2
    right = x0 + opts.get("tall", 0.6) + 0.05 + opts.get("shelves", 0.27) + 0.018
    # 50 x 38 cm and 11 cm tall, as photographed beside the shelves: at
    # 60 x 42 x 14 it read as a trough.
    hw, hd, n, deck, rise = 0.25, 0.19, 64, 0.055, 0.11
    cx, cy, top = (right + x1) / 2 + 0.02, y1 - 0.24, 0.86
    # Outside up to the rim, then the bowl in front of a flat deck at the
    # back the mixer stands on (0119, 0120): its wall steep below the rim,
    # rounding into a flat floor. Rings close down it, so the light baked
    # per vertex shades the hollow (a few rings left it as bright as the
    # rim, and the basin read as a solid block).
    rings = [(cx, cy, hw, hd, 0.03, top), (cx, cy, hw, hd, 0.035, top + rise - 0.005),
             (cx, cy, hw - 0.01, hd - 0.01, 0.03, top + rise)]
    bowl_cy = cy - deck / 2
    for k in range(10):
        f = k / 9
        inset = 0.02 + 0.06 * (1 - math.cos(f * math.pi / 2))
        rings.append((cx, bowl_cy, hw - inset, hd - deck / 2 - inset, 0.04 + 0.03 * f,
                      top + rise - 0.002 - (rise - 0.03) * math.sin(f * math.pi / 2)))
    bm = bmesh.new()
    loops = [[bm.verts.new((px, py, z)) for px, py in rounded_rect(ox, oy, a, b, r, n)]
             for ox, oy, a, b, r, z in rings]
    for upper, lower in zip(loops, loops[1:], strict=False):
        for k in range(n):
            bm.faces.new((upper[k], upper[(k + 1) % n], lower[(k + 1) % n], lower[k]))
    hub = bm.verts.new((cx, bowl_cy, top + 0.026))
    for k in range(n):
        bm.faces.new((loops[-1][k], loops[-1][(k + 1) % n], hub))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for face in tube(bm, (cx, bowl_cy, top + 0.024), (cx, bowl_cy, top + 0.031), 0.022, 0.022,
                     segments=24):
        face.material_index = 1  # the drain
    tx, ty, foot = cx, cy + hd - 0.04, top + rise
    for a, b, r0, r1 in (((tx, ty, foot), (tx, ty, foot + 0.15), 0.022, 0.02),
                         ((tx, ty, foot + 0.14), (tx, ty - 0.13, foot + 0.14), 0.012, 0.011),
                         ((tx, ty, foot + 0.16), (tx, ty + 0.08, foot + 0.19), 0.008, 0.006)):
        for face in tube(bm, a, b, r0, r1, segments=20):
            face.material_index = 1
    mesh = bpy.data.meshes.new(f"{item['id']}-basin")
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.append(surface_material("basin", surfaces["basin"]))
    mesh.materials.append(surface_material("steel", surfaces["steel"]))
    mesh.shade_smooth()
    mesh.set_sharp_from_angle(angle=math.radians(50))
    obj = bpy.data.objects.new(mesh.name, mesh)
    bpy.context.collection.objects.link(obj)
    plain_uvs(obj)
    obj.matrix_world = piece.matrix_world.copy()
    VERTEX_LIT.add(obj.name)
    return obj


#: A generated model comes at ~200k triangles and 2048 px maps. The baked
#: light cannot show that detail and a phone pays for it in download and
#: memory: cut to this many triangles per metre of the piece's largest side
#: (as placed), its colour to a map this size.
MODEL_FACES_PER_M, MODEL_MAP = 8_000, 1024
#: Each model file made light once, as a mesh to copy for every placement.
LIGHTENED: dict[str, bpy.types.Mesh] = {}


#: drop_darker keeps every face in the upper part of a model, this share of
#: its height: a bed's frame stops below half of it.
DROP_BELOW = 0.55


#: ... and drops only dark patches of at least this many faces: a duvet face
#: at the edge of its UV island reads the dark gutter round it, and on its
#: own, dropped, left a hole.
DROP_PATCH = 50

#: Of a dark patch, only what lies below this share of the model's height
#: goes, well inside the drawn frame (as wide as the bed, its top higher than
#: the old frame's): above it the hem met the old frame, and cut there it
#: showed the dark under the duvet through notches. What stays is given
#: the fabric's colour, as is every other dark face of the
#: lower part and any face that reads near black (DROP_SPECK), a speck where
#: a face's UVs ran into the gutter.
DROP_HIDDEN, DROP_SPECK = 0.40, 0.08


def drop_darker(obj: bpy.types.Object, below: float) -> None:
    """
    Remove the dark patches (DROP_PATCH) in the lower part of a model
    (DROP_BELOW), dark where a face's own colour (its material's base colour
    map at its middle) is darker than `below` in linear luminance: a
    generated bed's frame, which decimated into a lumpy black mass under the
    duvet and is drawn instead (bed-frame).
    """
    import numpy as np

    mesh = obj.data
    n = len(mesh.polygons)
    uv = np.empty(len(mesh.loops) * 2, np.float32)
    mesh.uv_layers[0].data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    start, count, slot = (np.empty(n, np.int32) for _ in range(3))
    mesh.polygons.foreach_get("loop_start", start)
    mesh.polygons.foreach_get("loop_total", count)
    mesh.polygons.foreach_get("material_index", slot)
    middle = np.add.reduceat(uv, start) / count[:, None]
    centre = np.empty(n * 3, np.float32)
    mesh.polygons.foreach_get("center", centre)
    height = centre.reshape(-1, 3)[:, 2]
    share = (height - height.min()) / (height.max() - height.min())
    low = share < DROP_BELOW
    light = np.ones(n, np.float32)
    weights = np.array([0.2126, 0.7152, 0.0722], np.float32)
    for i, mat in enumerate(mesh.materials):
        bsdf = next(nd for nd in mat.node_tree.nodes if nd.type == "BSDF_PRINCIPLED")
        links = bsdf.inputs["Base Color"].links
        if not links or links[0].from_node.type != "TEX_IMAGE":
            continue
        image = links[0].from_node.image
        w, h = image.size
        pixels = np.empty(w * h * 4, np.float32)
        image.pixels.foreach_get(pixels)
        mine = slot == i
        at = np.clip(middle[mine] % 1.0, 0.0, 0.9999)
        rgb = pixels.reshape(h, w, 4)[(at[:, 1] * h).astype(int), (at[:, 0] * w).astype(int), :3]
        light[mine] = rgb @ weights
    dark = (light < below) & low
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    drop, seen = [], np.zeros(n, bool)
    for first in np.flatnonzero(dark):
        if seen[first]:
            continue
        seen[first] = True
        patch, todo = [], [bm.faces[first]]
        while todo:
            face = todo.pop()
            patch.append(face)
            for edge in face.edges:
                for near in edge.link_faces:
                    if dark[near.index] and not seen[near.index]:
                        seen[near.index] = True
                        todo.append(near)
        if len(patch) >= DROP_PATCH:
            drop += patch
    hidden = [face for face in drop if share[face.index] < DROP_HIDDEN]
    gone = np.zeros(n, bool)
    gone[[face.index for face in hidden]] = True
    fabric = (dark | (light < DROP_SPECK)) & ~gone
    # The fabric's colour: per material, the UV middle of a face of median
    # brightness among the light ones; every loop of a recoloured face reads it.
    layer = bm.loops.layers.uv[0]
    for i in range(len(mesh.materials)):
        bright = np.flatnonzero((slot == i) & (light > 2 * below))
        if not len(bright):
            continue
        pick = bright[np.argsort(light[bright])[len(bright) // 2]]
        for f in np.flatnonzero(fabric & (slot == i)):
            for loop in bm.faces[f].loops:
                loop[layer].uv = middle[pick]
    bmesh.ops.delete(bm, geom=hidden, context="FACES")
    bm.to_mesh(mesh)
    bm.free()


def lighten(obj: bpy.types.Object, model: str, size: float, scratch: Path,
            colour: str | None = None, darker: float | None = None, flat: bool = False) -> None:
    """
    An imported model made light enough for a phone: decimated, unwrapped
    afresh and its colour baked onto that from the original, smooth shaded,
    and with nothing the viewer does not draw (it shows colour x baked
    light): no glow, relief or sheen maps. A generated model comes split
    along its every UV seam, into thousands of loose patches: decimated so,
    they shrank apart and the duvet showed holes, so it is welded first;
    its colour is baked onto a fresh unwrap because decimating smears UVs
    across seams. Its emission map is near black but not quite, and would
    glow in the bake. `size`, the piece's largest side as placed, sets how
    far it is decimated; `colour` (sRGB hex, read off a photo of the piece)
    is what its fabric is brought to (match_colour): a generated sofa came
    grey where the real one is cream, the stools a pale grey where theirs is
    a mid grey. `darker`: faces darker than this are dropped first
    (drop_darker), so the triangles go to what is kept. `flat`: all of it
    brought to `colour`, not only its fabric (match_colour).
    """
    if model in LIGHTENED:
        imported = obj.data
        obj.data = LIGHTENED[model].copy()
        bpy.data.meshes.remove(imported)
        return
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bm.to_mesh(obj.data)
    bm.free()
    if darker is not None:  # welded first, or its patches stop at every seam
        drop_darker(obj, darker)
    original = obj.copy()
    original.data = obj.data.copy()
    bpy.context.collection.objects.link(original)
    target = int(MODEL_FACES_PER_M * size)
    faces = sum(len(p.vertices) - 2 for p in obj.data.polygons)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    if faces > target:
        decimate = obj.modifiers.new("lighten", "DECIMATE")
        decimate.ratio = target / faces
        bpy.ops.object.modifier_apply(modifier=decimate.name)
    obj.data.shade_smooth()
    obj.data.set_sharp_from_angle(angle=math.radians(40))
    for layer in list(obj.data.uv_layers):
        obj.data.uv_layers.remove(layer)
    obj.data.uv_layers.new(name="colour")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.004)
    bpy.ops.object.mode_set(mode="OBJECT")

    # A big piece (the corner sofa) gets a map twice as fine, or its fabric blurs up close.
    px = MODEL_MAP * (2 if size >= 1.5 else 1)
    colour_map = bpy.data.images.new(f"{Path(model).stem}-colour", px, px)
    colour_map.generated_color = UNBAKED
    nodes = []
    for slot, source in enumerate(obj.data.materials):
        mat = source.copy()
        obj.data.materials[slot] = mat
        tree = mat.node_tree
        bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
        for node in [n for n in tree.nodes if n.type == "TEX_IMAGE"]:
            tree.nodes.remove(node)
        bsdf.inputs["Emission Strength"].default_value = 0.0
        bsdf.inputs["Metallic"].default_value = 0.0
        bsdf.inputs["Roughness"].default_value = 0.8
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = colour_map
        tree.nodes.active = node
        nodes.append((tree, node, bsdf))
    scene = bpy.context.scene
    samples, scene.cycles.samples = scene.cycles.samples, 16
    original.select_set(True)
    bake = scene.render.bake
    bake.use_selected_to_active, bake.cage_extrusion, bake.max_ray_distance = True, 0.03, 0.1
    # Not cleared, and no margin (it blended island edges into UNBAKED):
    # what no ray reached keeps UNBAKED, to be filled in from its neighbours.
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, margin=0, use_clear=False)
    bake.use_selected_to_active = False
    scene.cycles.samples = samples
    original_mesh = original.data
    bpy.data.objects.remove(original, do_unlink=True)
    bpy.data.meshes.remove(original_mesh)
    fill_unbaked(colour_map)
    if colour:
        match_colour(colour_map, colour, flat)
    # Saved as a JPEG and loaded back, so the exporter writes the file as is.
    path = scratch / f"{Path(model).stem}-colour.jpg"
    colour_map.file_format = "JPEG"
    colour_map.save(filepath=str(path), quality=90)
    bpy.data.images.remove(colour_map)
    saved = bpy.data.images.load(str(path))
    for tree, node, bsdf in nodes:
        node.image = saved
        tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
    LIGHTENED[model] = obj.data.copy()


#: A colour map's texels start out this, so those the bake missed can be told
#: from black ones: a ray that missed the original left a black fleck on the
#: white duvet.
UNBAKED = (1.0, 0.0, 1.0, 1.0)


def fill_unbaked(image: bpy.types.Image) -> None:
    """Each texel the bake missed takes its baked neighbours' colour, spreading in from them."""
    import numpy as np

    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    size = image.size[0]
    rgb = pixels.reshape(size, size, 4)[..., :3]
    missing = (rgb[..., 0] > 0.99) & (rgb[..., 1] < 0.01) & (rgb[..., 2] > 0.99)
    for _ in range(64):
        if not missing.any():
            break
        known = (~missing).astype(np.float32)
        total = np.zeros_like(rgb)
        count = np.zeros(missing.shape, np.float32)
        for axis, step in ((0, 1), (0, -1), (1, 1), (1, -1)):
            total += np.roll(rgb * known[..., None], step, axis=axis)
            count += np.roll(known, step, axis=axis)
        reached = missing & (count > 0)
        rgb[reached] = total[reached] / count[reached][:, None]
        missing &= ~reached
    rgb[missing] = rgb[~missing].mean(axis=0) if (~missing).any() else 0.5
    image.pixels.foreach_set(pixels)


def match_colour(image: bpy.types.Image, colour: str, flat: bool = False) -> None:
    """
    Bring a colour map's fabric to `colour`: scaled per channel, in linear
    light, so its near-grey texels' median matches. Coloured ones (a
    stool's beech legs) are left as they are, fading between the two.
    `flat`: every texel brought near `colour`, its own lightness kept only
    as a hint. Scaled, a black office chair's grey plastic went white.
    """
    import numpy as np

    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    rgb = pixels.reshape(-1, 4)[:, :3]
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    saturation = (rgb.max(axis=1) - rgb.min(axis=1)) / np.maximum(rgb.max(axis=1), 1e-4)
    fabric = np.clip((0.25 - saturation) / 0.1, 0.0, 1.0)
    target = np.array(hex_rgba(colour)[:3])
    if flat:
        lum = linear @ np.array([0.2126, 0.7152, 0.0722])
        shade = np.clip((lum / max(float(np.median(lum)), 1e-4)) ** 0.3, 0.75, 1.3)
        linear = np.clip(target[None, :] * shade[:, None], 0.0, 1.0)
        rgb[:] = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055)
        image.pixels.foreach_set(pixels)
        return
    gain = target / np.maximum(np.median(linear[fabric > 0.99], axis=0), 1e-4)
    linear = np.clip(linear * (1 + fabric[:, None] * (gain - 1)), 0.0, 1.0)
    rgb[:] = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055)
    image.pixels.foreach_set(pixels)


def furniture(item: dict, surfaces: dict, scratch: Path) -> bpy.types.Object:
    """The piece's model (or a block), fitted to its scanned box and turned to face `front`."""
    import numpy as np

    w, h, d = item["size"]
    bounds = None
    if item.get("finish") and not item["model"]:
        obj = finished(item, surfaces)
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
        # Fitted to its box as imported: what lighten drops (a bed's frame)
        # must not stretch what is left.
        co = np.empty(len(obj.data.vertices) * 3, np.float32)
        obj.data.vertices.foreach_get("co", co)
        bounds = co.reshape(-1, 3).min(axis=0), co.reshape(-1, 3).max(axis=0)
        opts = item.get("options") or {}
        lighten(obj, item["model"], max(w, h, d), scratch, opts.get("colour"),
                opts.get("dropDarker"), opts.get("flat", False))
        VERTEX_LIT.add(item["id"])
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
        if bounds is None:
            co = np.empty(len(obj.data.vertices) * 3, np.float32)
            obj.data.vertices.foreach_get("co", co)
            bounds = co.reshape(-1, 3).min(axis=0), co.reshape(-1, 3).max(axis=0)
        lo, hi = (Vector(b.tolist()) for b in bounds)
        centre = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
        extent = hi - lo
        scale = Matrix.Diagonal((w / max(extent.x, 1e-6), d / max(extent.y, 1e-6),
                                 h / max(extent.z, 1e-6), 1.0))
        obj.data.transform(scale @ Matrix.Translation(-centre))

    # A model's front is glTF +Z, which is Blender -Y; turn it to `front`.
    fx, fz = item["front"]
    cx, cy, cz = item["center"]
    obj.matrix_world = Matrix.Translation(P(cx, cy - h / 2, cz)) @ Matrix.Rotation(
        math.atan2(fx, fz), 4, "Z"
    )
    if item.get("finish") == "glass-door":
        hang_glass_door(obj, item)
    elif item.get("finish") == "sliding-door":
        # Built shut over the doorway, so its light is baked where both rooms
        # see it; it opens by `slide` (plan x, z) along its rail. Nothing
        # else sees it while baking: where it stands open, the wall behind
        # kept its shadow once it was slid shut.
        opts = item.get("options") or {}
        dx, dz = opts["slide"]
        obj["door"] = {"open": 1.0, "close": 0.0, "slide": [dx, 0.0, dz],
                       "at": list(opts["doorway"]), "width": opts["doorWidth"]}
        obj.visible_shadow = obj.visible_diffuse = obj.visible_glossy = False
    return obj


# --------------------------------------------------------------------------
# Light
# --------------------------------------------------------------------------


def ceiling_lights(spec: dict) -> list[bpy.types.Object]:
    """
    The flat's own ceiling lights that are on (the dressing's `lights`): a
    soft square area light just under each fixture, and the fixture, a flush
    white panel shown lit. A space lit only by light bounced in from
    elsewhere (the hall) bakes noisy, as grey blotches; its own lamp gives
    it clean direct light, as in the owner's photos.
    """
    fixtures = []
    for i, lamp in enumerate(spec.get("lights") or []):
        x, y, z = lamp["at"]
        size = lamp.get("size", 0.25)
        if lamp.get("shape") == "bulb":
            # A table lamp's bulb inside its shade (a pendant piece), which
            # it lights through: a point, no panel of its own.
            bulb = bpy.data.lights.new(f"lamp-light-{i}", "POINT")
            bulb.energy, bulb.shadow_soft_size = lamp.get("power", 5.0), size
            bulb.color = lamp.get("colour", (1.0, 0.86, 0.72))
            obj = bpy.data.objects.new(bulb.name, bulb)
            bpy.context.collection.objects.link(obj)
            obj.location = P(x, y, z)
            continue
        # Round or square, as each room's lamp is photographed (flat-02: round
        # flush lamps in the living room and bedrooms, square panels in the hall).
        round_ = lamp.get("shape") == "round"
        area = bpy.data.lights.new(f"lamp-light-{i}", "AREA")
        area.shape, area.size = ("DISK" if round_ else "SQUARE"), size
        area.energy = lamp.get("power", 15.0)
        area.color = lamp.get("colour", (1.0, 0.86, 0.72))
        obj = bpy.data.objects.new(area.name, area)
        bpy.context.collection.objects.link(obj)
        obj.location = P(x, y - 0.012, z)  # an area light shines down its -Z: down
        bm = bmesh.new()
        h = size / 2
        corners = ([(h * math.cos(a), h * math.sin(a))
                    for a in (2 * math.pi * k / 48 for k in range(48))] if round_
                   else [(-h, -h), (h, -h), (h, h), (-h, h)])
        verts = [bm.verts.new(P(x + dx, y - 0.008, z + dz)) for dx, dz in corners]
        face = bm.faces.new(verts)
        face.normal_update()
        if face.normal.dot(P(0, -1, 0)) < 0:
            face.normal_flip()
        mesh = bpy.data.meshes.new(f"lamp-{i}")
        bm.to_mesh(mesh)
        bm.free()
        mesh.materials.append(material("lamp", "#fffaf2", 0.5))
        panel = bpy.data.objects.new(f"lamp-{i}", mesh)
        bpy.context.collection.objects.link(panel)
        # Seen, not in the way: the light shines past it, and the bake skips it.
        panel.visible_shadow = False
        fixtures.append(panel)
    return fixtures


#: Under-cabinet strips are on once the sun is this low (degrees), fading
#: in from STRIPS_FADE above it: lit in the evening, off by day, as people
#: use them; the slider blends the bakes either side, so they fade.
STRIPS_ON, STRIPS_FADE = 10.0, 20.0


def strip_lights(spec: dict) -> list[bpy.types.Object]:
    """
    LED strips under the kitchen's wall units and lowest shelf (the
    dressing's `strips`: `from` and `to` at the underside, x, y, z; `power`
    in W per metre; `colour`): a long narrow area light shining down, warm
    and soft, so the worktop gets a smooth pool of light. The strip itself
    sits out of sight behind the units' front edge.
    """
    lamps = []
    for i, strip in enumerate(spec.get("strips") or []):
        a, b = Vector(P(*strip["from"])), Vector(P(*strip["to"]))
        length = (b - a).length
        lamp = bpy.data.lights.new(f"strip-{i}", "AREA")
        lamp.shape, lamp.size, lamp.size_y = "RECTANGLE", length, 0.025
        lamp.color = strip.get("colour", (1.0, 0.84, 0.66))
        obj = bpy.data.objects.new(lamp.name, lamp)
        bpy.context.collection.objects.link(obj)
        obj.location = (a + b) / 2 - Vector((0, 0, 0.004))  # shines down its -Z
        obj["power"] = strip.get("power", 4.0) * length
        lamps.append(obj)
    return lamps


def set_strips(lamps: list[bpy.types.Object], entry: dict | None) -> None:
    """The strips' power at this time of day: off by day, on in the evening."""
    elevation = entry["elevation"] if entry else 0.0
    on = min(max((STRIPS_ON + STRIPS_FADE - elevation) / STRIPS_FADE, 0.0), 1.0)
    for obj in lamps:
        obj.data.energy = obj["power"] * on


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

    # Portals in the roof windows: Cycles then aims its sky samples through
    # them instead of over the whole sky, which most of the room never sees.
    # Light reaching a hall only by bouncing came out speckled without them.
    for i, sk in enumerate(spec.get("skylights", [])):
        u0, u1, v0, v1 = sk["sash"]
        frame = sk["frame"]
        lamp = bpy.data.lights.new(f"portal-{i}", "AREA")
        lamp.shape, lamp.size, lamp.size_y = "RECTANGLE", u1 - u0, v1 - v0
        if hasattr(lamp, "cycles") and hasattr(lamp.cycles, "is_portal"):
            lamp.cycles.is_portal = True
        obj = bpy.data.objects.new(lamp.name, lamp)
        bpy.context.collection.objects.link(obj)
        axes = [P(*frame[k]) for k in ("u", "v", "n")]  # its -Z, the light's way, into the room
        obj.matrix_world = Matrix.Translation(
            P(*slope_point(frame, (u0 + u1) / 2, (v0 + v1) / 2, sk["depth"]))
        ) @ Matrix((axes[0], axes[1], axes[2])).transposed().to_4x4()

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
    sun = bpy.data.lights.new("sun", "SUN")
    sun.energy, sun.angle = 5.5, math.radians(1.2)
    sun.color = (1.0, 0.85, 0.68)
    obj = bpy.data.objects.new("sun", sun)
    bpy.context.collection.objects.link(obj)
    set_sun(spec.get("sun") or {"direction": [tx, ty, tz]})
    return patch


def set_sun(entry: dict) -> None:
    """
    Put the sun where `entry` (rebuild.sun) says, in its colour and
    strength, and the sky to match: between bakes, this is what moves the
    time of day. A sun below the horizon gives no light; the sky dims.
    """
    tx, ty, tz = entry["direction"]
    travel = P(-tx, -ty, -tz)
    obj = bpy.data.objects["sun"]
    obj.rotation_euler = travel.to_track_quat("-Z", "Y").to_euler()
    if "colour" in entry:
        obj.data.color = entry["colour"]
        obj.data.energy = entry["energy"]
    world = bpy.context.scene.world.node_tree.nodes
    sky = next(n for n in world if n.type == "TEX_SKY")
    sky.sun_elevation = max(math.asin(max(-1.0, min(1.0, ty))), 0.0)
    sky.sun_rotation = math.atan2(-travel.x, -travel.y)
    world["Background"].inputs["Strength"].default_value = entry.get("sky", 0.5)


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
    # Light off the brass frames and mirrors onto the walls beside them is a
    # caustic: a few samples find it, and the denoiser left a ring of bright
    # blotches round each mirror. Without it, and with glossy blurred, clean.
    scene.cycles.caustics_reflective = False
    scene.cycles.blur_glossy = 1.0
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


def obstacles(pieces: list[bpy.types.Object]) -> list[dict]:
    """
    What a person walks into: each piece standing on the floor, as the
    convex outline of its parts below chest height (so a bar top's overhang
    counts, a wall cupboard above the worktop does not). The viewer keeps
    the walk out of them. Not what lies on the floor (rugs, mats, a
    balcony's floor), which is walked on, nor a doorway's leaf: it swings,
    and closed it shuts its doorway instead (doors.ts).
    """
    out = []
    for obj in pieces:
        if "at" in (obj.get("door") or {}):
            continue
        if max((obj.matrix_world @ v.co).z for v in obj.data.vertices) < 0.05:
            continue
        low = []
        for v in obj.data.vertices:
            w = obj.matrix_world @ v.co  # Blender (x, -z, y)
            if w.z < 1.1:
                low.append((w.x, -w.y))
        bottom = min((obj.matrix_world @ v.co).z for v in obj.data.vertices)
        if low and bottom < 0.9:
            out.append({"id": obj.name, "footprint": convex_hull(low)})
    return out


def expose(exposure: float, views: list[bpy.types.Object]) -> None:
    """Grade the preview as the viewer will show it: the room exposed, the views as photographed."""
    bpy.context.scene.view_settings.exposure = math.log2(exposure)
    for obj in views:
        for mat in obj.data.materials:
            bsdf = mat.node_tree.nodes["Principled BSDF"]
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


#: Atlas share of a piece of furniture, and its extra smoothing: on few
#: texels its light came out in blotches on the kitchen's white fronts.
PIECE_WEIGHT, PIECE_SMOOTH = 1.5, 2.0
#: Atlas share per object, as a multiple of its surface: the trim (door
#: frames, leaves, skirtings, window frames) gets 6x the texels per square
#: metre. At the walls' density a 7 cm architrave was 4 texels wide and its
#: light came out in grey blotches. The floors carry the furniture's
#: shadows, so they get a piece's share (main).
ATLAS_WEIGHT = {"trim": 6.0, "wall": 1.0, "ceiling": 1.0,
                # Thin boards with frames on them, seen close: at a piece's
                # share their light came out pixelated.
                "kitchen-shelves": 5.0, "backsplash": 2.0, "shelf-back": 2.0}
#: Texels between two islands in the atlas. Smoothing stays within an
#: island (ISLANDS), so this only keeps a far-off surface's coarse mip
#: levels from mixing its neighbours' light in.
ISLAND_GAP = 4
#: Each texel's island in the atlas (-1: none), rows up from v = 0 like the
#: pixels, and each island's object's name: set by layout_lightmap.
ISLANDS = None
ISLAND_OWNER: list[str] = []
#: Extra smoothing for an object's light: painted trim's light is smooth,
#: and on few texels the denoiser leaves blotches. So is a white wall's: the
#: hall, lit only by bounce, showed the denoiser's blotches as grey bands,
#: and at 3.5 still as a faint mottle round flat-02's hall mirrors.
SMOOTH = {"trim": 2.5, "wall": 6.0, "ceiling": 6.0}
#: What is lit per vertex instead of from the atlas (vertex_light): the
#: generated models, whose folds unwrapped into ~800 islands of a few
#: texels each and baked black specks, the clothes, and the pendant.
VERTEX_LIT: set[str] = set()
#: Of them, what light passes through: the pendant's fabric shade, lit
#: through from its bright side as a real one is. Its sun stays baked.
TRANSLUCENT: set[str] = set()


def layout_lightmap(objects: list[bpy.types.Object], size: int) -> None:
    """
    A second UV set shared by all objects, laid out once for the whole atlas.

    Each object's seen faces are unwrapped on their own, scaled to its share
    of texels per square metre (ATLAS_WEIGHT), and then every island of every
    object is packed together, tight (rotated, by their outlines) but
    ISLAND_GAP texels apart. Packed object by object into square cells,
    flat-02's atlas was three quarters empty and its walls' light came out
    in 2.4 cm squares, the floor's in 3.7 cm. Unseen faces shrink to a point.
    """
    global ISLANDS
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    bpy.context.scene.tool_settings.use_uv_select_sync = True
    # Unwrapping and packing correct islands for the aspect of a material's
    # active image: the parquet's long strip image squashed the floor's
    # islands 9:1, and its light came out smeared along the boards. A square
    # one in each material meanwhile.
    square = bpy.data.images.new("square", 8, 8)
    added = []
    for mat in {m for obj in objects for m in obj.data.materials if m and m.node_tree}:
        node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = square
        mat.node_tree.nodes.active = node
        added.append((mat, node))
    hiddens = []
    for obj in objects:
        flag = obj.data.attributes.get("unseen")
        count = len(obj.data.polygons)
        hidden = [bool(flag.data[i].value) for i in range(count)] if flag else [False] * count
        hiddens.append(hidden)
        layer = obj.data.uv_layers.new(name="lightmap")
        obj.data.uv_layers.active = layer
        for poly, is_hidden in zip(obj.data.polygons, hidden, strict=True):
            poly.select = not is_hidden
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        # Not corrected for a texture's aspect: that squashed the islands of
        # pieces with a photo texture (the wardrobe's oak, 1:4) up to 16x
        # across, and a drawer front's light came out black.
        bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.0,
                                 correct_aspect=False)
        bpy.ops.object.mode_set(mode="OBJECT")
        to_density(obj.data, hidden, ATLAS_WEIGHT.get(obj.name, 1.0))
    packed = [o for o, h in zip(objects, hiddens, strict=True) if not all(h)]
    bpy.ops.object.select_all(action="DESELECT")
    for obj in packed:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = packed[0]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.uv.pack_islands(rotate=True, scale=True, margin_method="FRACTION",
                            margin=ISLAND_GAP / size, shape_method="CONCAVE")
    bpy.ops.object.mode_set(mode="OBJECT")
    for mat, node in added:
        mat.node_tree.nodes.remove(node)
    bpy.data.images.remove(square)
    for obj, hidden in zip(objects, hiddens, strict=True):
        # The point is the nearest point of a seen face, not the atlas's empty
        # corner: where one shows after all (the slope's cut beside a roof
        # window's lining, a lining board's edge), it carries the light
        # beside it rather than black.
        shrink_unseen(obj.data, hidden)
    ISLANDS = island_map(packed, size)


def to_density(mesh: bpy.types.Mesh, hidden: list[bool], weight: float) -> None:
    """Scale an object's light map UVs so their area is `weight` times its seen faces' area."""
    import numpy as np

    uv = mesh.uv_layers["lightmap"].data
    coords = np.empty(len(uv) * 2, np.float32)
    uv.foreach_get("uv", coords)
    coords = coords.reshape(-1, 2)
    flat, uv_area = 0.0, 0.0
    for poly, is_hidden in zip(mesh.polygons, hidden, strict=True):
        if is_hidden:
            continue
        flat += poly.area
        p = coords[list(poly.loop_indices)]
        uv_area += 0.5 * abs(float(np.dot(p[:, 0], np.roll(p[:, 1], -1))
                                   - np.dot(p[:, 1], np.roll(p[:, 0], -1))))
    if uv_area > 0:
        uv.foreach_set("uv", (coords * math.sqrt(weight * flat / uv_area)).ravel())


def island_map(objects: list[bpy.types.Object], size: int):
    """
    Each texel's light map island, from the islands' triangles drawn into the
    atlas; texels round an island (its bake margin) take the nearest one's.
    Smoothing a texel's light then mixes in only its own surface's, however
    close another lies in the atlas.
    """
    import numpy as np
    from bpy_extras.bmesh_utils import bmesh_linked_uv_islands

    ISLAND_OWNER.clear()
    corners = [[], [], []]
    ids = []
    for obj in objects:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        layer = bm.loops.layers.uv["lightmap"]
        bm.faces.ensure_lookup_table()
        for island in bmesh_linked_uv_islands(bm, layer):
            index = len(ISLAND_OWNER)
            ISLAND_OWNER.append(obj.name)
            for f in island:
                loops = bm.faces[f].loops if isinstance(f, int) else f.loops
                uvs = [tuple(loop[layer].uv) for loop in loops]
                for k in range(1, len(uvs) - 1):
                    for c, p in zip(corners, (uvs[0], uvs[k], uvs[k + 1]), strict=True):
                        c.append(p)
                    ids.append(index)
        bm.free()
    grid = np.full((size, size), -1, np.int32)
    if ids:
        a, b, c = (np.asarray(p, np.float64) * size for p in corners)
        fill_triangles(grid, a, b, c, np.asarray(ids, np.int32))
    # The bake's margin round each island, and texels an edge only grazes.
    for _ in range(ISLAND_GAP):
        empty = grid < 0
        if not empty.any():
            break
        for axis, step in ((0, 1), (0, -1), (1, 1), (1, -1)):
            near = np.roll(grid, step, axis=axis)
            take = empty & (near >= 0) & (grid < 0)
            grid[take] = near[take]
    return grid


def fill_triangles(grid, a, b, c, values) -> None:
    """Triangles in texels (x along a row) into `grid`: each texel whose centre is in one."""
    import numpy as np

    size = grid.shape[0]
    lo = np.floor(np.minimum(np.minimum(a, b), c)).astype(np.int64)
    hi = np.ceil(np.maximum(np.maximum(a, b), c)).astype(np.int64)
    span = (hi - lo).max(axis=1)
    below = 0
    for cap in (2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192):
        sel = np.nonzero((span > below) & (span <= cap) | ((below == 0) & (span == 0)))[0]
        below = cap
        if not len(sel):
            continue
        offs = np.arange(cap + 1)
        ox, oy = np.meshgrid(offs, offs)
        ox, oy = ox.ravel(), oy.ravel()
        chunk = max(1, 4_000_000 // len(ox))
        for start in range(0, len(sel), chunk):
            s = sel[start:start + chunk]
            x = lo[s, 0][:, None] + ox[None]
            y = lo[s, 1][:, None] + oy[None]
            px, py = x + 0.5, y + 0.5
            (ax, ay), (bx, by), (cx, cy) = ((p[s, 0][:, None], p[s, 1][:, None]) for p in (a, b, c))
            w0 = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
            w1 = (cx - bx) * (py - by) - (cy - by) * (px - bx)
            w2 = (ax - cx) * (py - cy) - (ay - cy) * (px - cx)
            eps = 1e-9
            inside = (((w0 >= -eps) & (w1 >= -eps) & (w2 >= -eps))
                      | ((w0 <= eps) & (w1 <= eps) & (w2 <= eps)))
            inside &= (x >= 0) & (x < size) & (y >= 0) & (y < size)
            rows, cols = np.nonzero(inside)
            grid[y[rows, cols], x[rows, cols]] = values[s][rows]


def shrink_unseen(mesh: bpy.types.Mesh, hidden: list[bool]) -> None:
    """Each unseen face's lightmap UVs to one point: where the nearest seen face is nearest."""
    uv = mesh.uv_layers["lightmap"].data
    tris = [(p.loop_indices[0], p.loop_indices[k], p.loop_indices[k + 1])
            for p, is_hidden in zip(mesh.polygons, hidden, strict=True) if not is_hidden
            for k in range(1, len(p.loop_indices) - 1)]
    if not tris:
        return
    corners = [mesh.vertices[mesh.loops[i].vertex_index].co for tri in tris for i in tri]
    tree = BVHTree.FromPolygons(corners, [(3 * n, 3 * n + 1, 3 * n + 2) for n in range(len(tris))])
    for poly, is_hidden in zip(mesh.polygons, hidden, strict=True):
        if not is_hidden:
            continue
        at, _, n, _ = tree.find_nearest(poly.center)
        a, b, c = tris[n]
        point = barycentric_transform(at, *corners[3 * n : 3 * n + 3],
                                      *(uv[i].uv.to_3d() for i in (a, b, c))).to_2d()
        for i in poly.loop_indices:
            uv[i].uv = point


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


#: Below this share of the atlas's median light, a texel's light is also
#: smoothed widely (DIM_SIGMA texels, edge-preserving), fully so in the
#: dark: in nooks (behind the shelves, under the wall units) the bake's
#: last noise came out as blotches. At 0.6 and 6, an evening ceiling along
#: the hall's walls still did.
DIM_BELOW, DIM_SIGMA = 0.9, 8.0
#: How far apart two texels' light may be (in log luminance) and still be
#: smoothed together: noise is, a shadow's edge is not. At 0.25 the noise in
#: dim light (beside the wardrobe) counted as edges and stayed blotchy.
EDGE = 0.5


def smooth_within_islands(rgb, covered, sigma: float = 1.6, islands=None):
    """
    Bake noise, blurred away without bleeding between surfaces or across
    shadows: a Gaussian over covered texels only, each neighbour weighted
    down the further its light is from this texel's (an edge-preserving,
    bilateral blur, one axis at a time), renormalised. A plain Gaussian
    spread the dark behind a wardrobe onto the wall beside it as a grey
    band. With the atlas's `islands`, only a texel's own island counts.
    """
    import numpy as np

    radius = max(int(3 * sigma), 1)
    offsets = np.arange(-radius, radius + 1)
    kernel = np.exp(-(offsets**2) / (2 * sigma**2))
    out = rgb.astype(np.float32)
    for axis in (0, 1):
        lum = np.log(np.maximum(out.mean(axis=-1), 1e-6))
        total = np.zeros_like(out)
        norm = np.zeros(out.shape[:2], np.float32)
        for k, w in zip(offsets, kernel, strict=True):
            near = np.roll(lum, k, axis=axis)
            same = np.roll(covered, k, axis=axis)
            if islands is not None:
                same = same & (np.roll(islands, k, axis=axis) == islands)
            weight = (w * same
                      * np.exp(-((near - lum) ** 2) / (2 * EDGE**2))).astype(np.float32)
            total += np.roll(out, k, axis=axis) * weight[..., None]
            norm += weight
        out = np.where(covered[..., None], total / np.maximum(norm, 1e-6)[..., None], 0.0)
    return out.astype(np.float32)


#: Light below this share of the atlas's median is a surface no light
#: reaches: where it touches another (the ceiling over a wall's top, a wall
#: behind a wardrobe). A dark underside still gets some bounce.
OCCLUDED = 0.003


def fill_occluded(image: bpy.types.Image) -> None:
    """
    Surfaces in contact, given the light of the visible surface beside them,
    before the denoiser and filtering see them. A face runs on past where it
    meets another (the ceiling's underside over the walls' tops, a wall
    behind a wardrobe), and the texels along the line mixed light with
    black: a dashed dark joint, a stepped band beside the wardrobe.
    """
    import numpy as np

    size = image.size[0]
    pixels = np.empty(size * size * 4, np.float32)
    image.pixels.foreach_get(pixels)
    rgba = pixels.reshape(size, size, 4)
    rgb = rgba[..., :3]
    covered = rgb.max(axis=-1) > 0
    if not covered.any():
        return
    light = rgb.mean(axis=-1)
    lit = covered & (light > OCCLUDED * float(np.median(light[covered])))
    dark = covered & ~lit
    rgb[dark] = fill_gaps(rgb, lit)[dark]
    image.pixels.foreach_set(pixels)


def despeckle(rgb, covered):
    """
    Each texel the median of its 3x3: single-texel dots and one-texel dashes
    (the denoiser's leftovers along a joint) gone, while a real edge, which
    runs on, stays. Before the edge-preserving smoothing, which keeps a
    speckle as it would an edge.
    """
    import numpy as np

    n = rgb.shape[0]
    padded = np.pad(fill_gaps(rgb, covered, reach=2), ((1, 1), (1, 1), (0, 0)), mode="edge")
    window = np.lib.stride_tricks.sliding_window_view(padded, (3, 3), axis=(0, 1))
    median = np.median(window.reshape(n, n, 3, 9), axis=-1).astype(np.float32)
    return np.where(covered[..., None], median, 0.0).astype(np.float32)


def downsample(rgb, covered, factor: int):
    """Each texel the mean of the baked sub-texels under it; covered where any is."""
    import numpy as np

    n = rgb.shape[0] // factor
    weight = covered.reshape(n, factor, n, factor).astype(np.float32)
    total = (rgb.reshape(n, factor, n, factor, 3) * weight[..., None]).sum(axis=(1, 3))
    count = weight.sum(axis=(1, 3))
    return (np.where(count[..., None] > 0, total / np.maximum(count, 1.0)[..., None], 0.0)
            .astype(np.float32), count > 0)


def fill_gaps(rgb, covered, reach: int = 48):
    """
    The atlas's empty texels filled in from the islands round them, ring by
    ring: left black, the filtering that samples a texel's neighbours (and
    its smaller mip levels, further away) pulled black into every island's
    edge, as dark lines along the wall's top and the door frames.
    """
    import numpy as np

    out = rgb.copy()
    filled = covered.copy()
    for _ in range(reach):
        if filled.all():
            break
        known = filled.astype(np.float32)
        total = np.zeros_like(out)
        count = np.zeros(filled.shape, np.float32)
        for axis, step in ((0, 1), (0, -1), (1, 1), (1, -1)):
            total += np.roll(out * known[..., None], step, axis=axis)
            count += np.roll(known, step, axis=axis)
        reached = ~filled & (count > 0)
        out[reached] = total[reached] / count[reached][:, None]
        filled |= reached
    if filled.any():
        out[~filled] = out[filled].mean(axis=0)
    return out


#: Exposure, like a camera's: the room's typical surface lands at this
#: (linear, before tone mapping), and what is brighter than CLIP times it
#: clips — sun on a window lining, which a photograph blows out too. Set
#: against the owner's photos: white walls in shade read grey, as a phone
#: exposing for the window shows them; brighter read as a render.
EXPOSE_TO, CLIP = 0.25, 16.0
#: How far the light's colour cast is taken out, like a camera's white
#: balance: the shade's colour is made neutral (walls white, as in the
#: photos), sun patches stay warmer than it. 1 would neutralise it fully.
WHITE_BALANCE = 0.6
#: Then a warm cast over it all, like a camera set below daylight: the owner
#: found the neutral room a touch cool, and asked again for warm light, as
#: the photos have it (flat-02's lamps are warm white). Held to where the
#: photos' white walls still read white beside the lamps, not beige.
WARMTH = (1.08, 1.0, 0.86)
#: The light map holds (light / scale) ** (1 / LIGHTMAP_GAMMA): steeper than
#: sRGB, so dim light (a hall lit only by bounce) gets enough of the 256
#: levels. With sRGB, the hall's few levels showed as grey blotches and JPEG
#: blocks once the viewer brightened it. daylight.ts LIGHT_MAP_GAMMA matches.
LIGHTMAP_GAMMA = 4.0


#: The atlas is baked this many times finer than it is published, and
#: averaged down: a quarter of the samples per texel for the same bake
#: time, but four texels in each, edges sampled four times across, and the
#: denoiser working on the finer image. Small parts (the sink, the shelves
#: under the air conditioner) had come out speckled.
SUPERSAMPLE = 2


def save_lightmap(baked: bpy.types.Image, path: str, scratch: Path,
                  size: int) -> tuple[float, float, list[float]]:
    """
    The float bake as an 8-bit JPEG (light ** 1/LIGHTMAP_GAMMA), plus the
    scale that undoes it, the exposure the room wants, and the colour
    balance applied (white balance and warmth, per channel: the viewer's
    live sun gets the same).

    A float EXR of a 2048 atlas is ~50 MB; light is smooth, so 8 bits in a
    perceptual curve lose nothing visible and come to a megabyte or two. The
    range is set by the room, not its brightest texel: normalised to the
    sunlit window linings, the walls got a few dozen of the 256 levels and
    JPEG turned them into blotches once exposed.
    """
    import numpy as np

    fine = baked.size[0]
    rgba = np.empty(fine * fine * 4, dtype=np.float32)
    baked.pixels.foreach_get(rgba)
    rgba = rgba.reshape(fine, fine, 4)
    # What the bake wrote, margins included. Not the alpha: a new image is
    # opaque black throughout, so alpha marks every texel as baked.
    covered = rgba[..., :3].max(axis=-1) > 0
    clean, covered = downsample(denoise(baked, scratch)[..., :3], covered, fine // size)
    clean = despeckle(clean, covered)
    # A light smoothing after, kept inside each island, takes the denoiser's
    # last grain off; outside the islands stays empty. Each object's own
    # smoothing (SMOOTH) on its islands' texels.
    islands = ISLANDS if ISLANDS is not None and ISLANDS.shape == covered.shape else None
    rgb = smooth_within_islands(clean, covered, 1.0, islands)
    if islands is not None:
        owner_sigma = np.array([SMOOTH.get(name, 1.0) for name in ISLAND_OWNER] + [1.0],
                               np.float32)
        sigmas = owner_sigma[islands]  # -1, no island, takes the last
        for sigma in sorted({float(s) for s in owner_sigma} - {1.0}):
            mask = covered & (sigmas == sigma)
            if mask.any():
                rgb = np.where(mask[..., None],
                               smooth_within_islands(clean, mask, sigma, islands), rgb)
    light = rgb.mean(axis=-1)
    if covered.any():
        share = light / max(float(np.median(light[covered])), 1e-6)
        dim = np.clip((DIM_BELOW - share) / DIM_BELOW, 0.0, 1.0)[..., None]
        rgb = rgb * (1 - dim) + smooth_within_islands(rgb, covered, DIM_SIGMA, islands) * dim
    rgb = rgb.reshape(-1, 3)
    covered = covered.ravel()
    balance = np.array(WARMTH, dtype=np.float32)
    if covered.any():
        shade = np.median(rgb[covered], axis=0)
        balance = balance * (shade.mean() / np.maximum(shade, 1e-6)) ** WHITE_BALANCE
    rgb = rgb * balance
    # The room's typical light: on its walls and ceilings, which are what a
    # camera exposes for. Over the whole atlas, the trim's larger share of
    # it (ATLAS_WEIGHT) and a lit hall pulled the median up, the room down.
    room = np.zeros((size, size), bool)
    if islands is not None:
        is_room = np.array([name in ("wall", "ceiling") for name in ISLAND_OWNER] + [False])
        room = is_room[islands]
    sample = covered & room.ravel() if (covered & room.ravel()).any() else covered
    typical = float(np.median(rgb.max(axis=1)[sample])) if sample.any() else 1.0
    scale = float(max(min(np.percentile(rgb.max(axis=1), 99.9), CLIP * typical), 1e-6))
    # Mostly white walls and ceilings: albedo ~0.85 under the typical light.
    exposure = min(max(EXPOSE_TO / (0.85 * max(typical, 1e-6)), 1.0), 64.0)
    rgb = fill_gaps(rgb.reshape(size, size, 3), covered.reshape(size, size)).reshape(-1, 3)
    linear = np.clip(rgb / scale, 0.0, 1.0)
    encoded = np.power(linear, 1 / LIGHTMAP_GAMMA).reshape(size, size, 3)
    # Dithered to 8 bits: rounded, a smooth gradient (the hall lamp's light
    # on a wall) showed its steps as rings; half a level of noise hides them.
    noise = np.random.default_rng(0).random(encoded.shape, dtype=np.float32)
    write_jpeg(path, np.clip(encoded[::-1] * 255 + noise, 0, 255).astype(np.uint8))
    return scale, exposure, [float(c) for c in balance]


def write_jpeg(path: str, pixels) -> None:
    """
    An 8-bit RGB image as a JPEG at quality 97, its colour kept at full
    resolution (4:4:4). Blender's own JPEGs halve the colour's resolution,
    which mixed neighbouring islands' colour into coloured specks along
    edges; at its default quality (90) white fronts showed blocks.
    """
    import OpenImageIO as oiio

    h, w, _ = pixels.shape
    spec = oiio.ImageSpec(w, h, 3, "uint8")
    spec.attribute("Compression", "jpeg:97")
    spec.attribute("jpeg:subsampling", "4:4:4")
    out = oiio.ImageOutput.create(path)
    out.open(path, spec)
    out.write_image(pixels)
    out.close()


#: Samples for the bake of the sun's direct light alone: one small light
#: with nothing in the way but the room converges in a few.
SUN_SAMPLES = 128


def bake_sun_direct(**target) -> None:
    """
    The sun's direct light alone, baked with `target` (the atlas's light map
    UVs, or vertex colours): the sky off, the other lamps hidden, no
    bounces. Taken away from the full bake, it leaves what the viewer cannot
    draw live: the sky's light and every bounce, the sun's included.
    """
    scene = bpy.context.scene
    background = scene.world.node_tree.nodes["Background"].inputs["Strength"]
    sky, samples = background.default_value, scene.cycles.samples
    lamps = [o for o in scene.objects if o.type == "LIGHT" and o.name != "sun"]
    background.default_value, scene.cycles.samples = 0.0, SUN_SAMPLES
    for lamp in lamps:
        lamp.hide_render = True
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"DIRECT"}, **target)
    background.default_value, scene.cycles.samples = sky, samples
    for lamp in lamps:
        lamp.hide_render = False


def bake(objects: list[bpy.types.Object], size: int, path: str,
         scratch: Path, live_sun: bool) -> tuple[float, float, list[float]]:
    """
    All objects' diffuse light (no colour) into one atlas on their second UV
    set (laid out by layout_lightmap); with `live_sun`, all but the sun's
    direct light, which the viewer draws. Returns the scale save_lightmap
    stored it at, the exposure the room wants in this light and the colour
    balance it was given.
    """
    import numpy as np

    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]

    # A 4096 atlas already gives the small parts their texels, and at 8192
    # the bake would not fit an 8 GB card beside the scene: baked as published.
    factor = SUPERSAMPLE if size <= 2048 else 1
    fine = size * factor
    image = bpy.data.images.new("lightmap", fine, fine, float_buffer=True)
    scene = bpy.context.scene
    samples = scene.cycles.samples
    scene.cycles.samples = max(samples // factor**2, 64)
    targets = []
    for mat in {m for obj in objects for m in obj.data.materials if m}:
        node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        mat.node_tree.nodes.active = node
        targets.append((mat, node))
    # A wide margin, so the denoiser blends island edges with island, not black.
    # Margins extend each island's own edge: by default they took the light
    # of the face beside it in 3D, which for a wall's top edge is its top
    # face hidden in the ceiling slab, and the joint showed a dashed dark line.
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"DIRECT", "INDIRECT"}, uv_layer="lightmap",
                        margin=ISLAND_GAP * factor, margin_type="EXTEND", use_clear=True)
    if live_sun:
        full = np.empty(fine * fine * 4, dtype=np.float32)
        image.pixels.foreach_get(full)
        bake_sun_direct(uv_layer="lightmap", margin=ISLAND_GAP * factor, margin_type="EXTEND",
                        use_clear=True)
        direct = np.empty_like(full)
        image.pixels.foreach_get(direct)
        full, direct = full.reshape(-1, 4), direct.reshape(-1, 4)
        # Kept above zero where baked: save_lightmap tells baked texels by it.
        rest = np.where(full[:, :3] > 0, np.maximum(full[:, :3] - direct[:, :3], 1e-6), 0.0)
        full[:, :3] = rest
        image.pixels.foreach_set(full.ravel())
    scene.cycles.samples = samples
    fill_occluded(image)
    scale, exposure, balance = save_lightmap(image, path, scratch, size)
    for mat, node in targets:
        mat.node_tree.nodes.remove(node)
    bpy.data.images.remove(image)
    for obj in objects:
        obj.data.uv_layers.active = obj.data.uv_layers[0]
    return scale, exposure, balance


def vertex_light(objects: list[bpy.types.Object], label: str, live_sun: bool,
                 balance: list[float], scale: float) -> None:
    """
    The light of the vertex-lit pieces at this time of day, into their
    light set (store_light), in the light map's units over its
    `scale` (the viewer multiplies by lightmapScale, as for the atlas). With
    `live_sun`, without the sun's direct light on what is opaque. A vertex
    far darker than those round it (inside a fold that cuts through
    another) takes theirs, and each is smoothed with them a little: a
    vertex's light is a single sample of the surface.
    """
    import numpy as np

    for obj in objects:
        obj.data.color_attributes.active_color = obj.data.color_attributes.new(
            BAKING, "FLOAT_COLOR", "POINT")
    opaque = [o for o in objects if o.name not in TRANSLUCENT]
    through = [o for o in objects if o.name in TRANSLUCENT]
    light = {}
    if opaque:
        light |= bake_vertices(opaque, BAKING, live_sun)
    if through:
        light |= bake_vertices(through, BAKING, live_sun=False, translucent=True)
    gain = np.array(balance, np.float32) / scale
    for obj in objects:
        store_light(obj.data, label, smooth_vertices(obj.data, light[obj.name]) * gain, "POINT")


def bake_vertices(objects: list[bpy.types.Object], name: str, live_sun: bool,
                  translucent: bool = False) -> dict:
    """
    Diffuse light into the `name` colour attribute of `objects`, as
    {name: (vertices, 3)}; with `live_sun`, less the sun's direct light.
    `translucent`: as fabric that lets half the light through, the light
    on either side averaged: a white half-diffuse, half-translucent stand-in
    for the bake (COMBINED, which counts what passes through).
    """
    import numpy as np

    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    kept = {}
    if translucent:
        fabric = bpy.data.materials.get("bake-translucent") or translucent_material()
        for obj in objects:
            kept[obj.name] = list(obj.data.materials)
            for i in range(len(obj.data.materials)):
                obj.data.materials[i] = fabric
        bpy.ops.object.bake(type="COMBINED", target="VERTEX_COLORS",
                            pass_filter={"DIRECT", "INDIRECT", "DIFFUSE", "TRANSMISSION"})
    else:
        bpy.ops.object.bake(type="DIFFUSE", pass_filter={"DIRECT", "INDIRECT"},
                            target="VERTEX_COLORS")

    def read() -> dict:
        out = {}
        for obj in objects:
            data = np.empty(len(obj.data.vertices) * 4, np.float32)
            obj.data.color_attributes[name].data.foreach_get("color", data)
            out[obj.name] = data.reshape(-1, 4)[:, :3].copy()
        return out

    light = read()
    if live_sun:
        bake_sun_direct(target="VERTEX_COLORS")
        light = {k: np.maximum(v - read()[k], 0.0) for k, v in light.items()}
    for obj in objects:
        for i, mat in enumerate(kept.get(obj.name, [])):
            obj.data.materials[i] = mat
    return light


def translucent_material() -> bpy.types.Material:
    mat = bpy.data.materials.new("bake-translucent")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    mix = nodes.new("ShaderNodeMixShader")
    diffuse = nodes.new("ShaderNodeBsdfDiffuse")
    through = nodes.new("ShaderNodeBsdfTranslucent")
    for node in (diffuse, through):
        node.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    mix.inputs["Fac"].default_value = 0.5
    links.new(diffuse.outputs[0], mix.inputs[1])
    links.new(through.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


def smooth_vertices(mesh: bpy.types.Mesh, rgb):
    """A vertex's light: its neighbours' where far darker, then blended with theirs."""
    import numpy as np

    edges = np.empty(len(mesh.edges) * 2, np.int32)
    mesh.edges.foreach_get("vertices", edges)
    edges = edges.reshape(-1, 2)

    def around(values):
        total = np.zeros_like(values)
        count = np.zeros(len(values), np.float32)
        for a, b in ((0, 1), (1, 0)):
            np.add.at(total, edges[:, a], values[edges[:, b]])
            np.add.at(count, edges[:, a], 1.0)
        return total / np.maximum(count, 1.0)[:, None]

    mean = around(rgb)
    dark = rgb.mean(axis=1) < 0.4 * mean.mean(axis=1)
    rgb = np.where(dark[:, None], mean, rgb)
    for _ in range(2):  # once left the clothes' light patchy
        rgb = 0.5 * rgb + 0.5 * around(rgb)
    return rgb


def sun_path(path: list[dict], lighting: list[dict]) -> list[dict]:
    """
    The viewer's sun: per sample of its path, the direction towards it and
    its direct light on a surface facing it, in the light maps' units: the
    colour balance and exposure of the bakes either side, as the viewer
    blends their maps. A diffuse bake holds a sun's light / pi.
    """
    keys = sorted(((clock(k["time"]), k["sunGain"]) for k in lighting), key=lambda k: k[0])
    out = []
    for sample in path:
        t = sample["minutes"]
        i = max([n for n, (m, _) in enumerate(keys) if m <= t], default=0)
        j = min(i + 1, len(keys) - 1)
        span = keys[j][0] - keys[i][0]
        f = min(max((t - keys[i][0]) / span, 0.0), 1.0) if span else 0.0
        gain = [a + (b - a) * f for a, b in zip(keys[i][1], keys[j][1], strict=True)]
        light = [c * sample["energy"] * g / math.pi
                 for c, g in zip(sample["colour"], gain, strict=True)]
        out.append({"minutes": t, "direction": [round(v, 5) for v in sample["direction"]],
                    "light": [round(v, 5) for v in light]})
    return out


def clock(time: str) -> int:
    hours, minutes = (int(v) for v in time.split(":"))
    return hours * 60 + minutes


def main() -> None:
    global WALL_T
    args = sys.argv[sys.argv.index("--") + 1 :]
    spec = json.loads(Path(args[0]).read_text())
    preview_only = "--preview-only" in args
    # A flat drawn from its floor plan has its outer walls' real thickness.
    WALL_T = flat_geometry.WALL_T = spec["rooms"][0].get("wallT", WALL_T)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    setup_cycles(spec["samples"])

    b = Builder()
    windows = []
    for i, room in enumerate(spec["rooms"]):
        windows += build_room(b, room, spec["skylights"] if i == 0 else [])
    trim = material("trim", "#f1efea", 0.45)
    mats = {
        "steel": surface_material("steel", spec["surfaces"]["steel"]),
        "wall": material("wall", spec["wall_colour"], 0.92),
        "ceiling": material("ceiling", "#f4f2ee", 0.95),
        "trim": trim,
        "skylight": trim,
        "gap": material("gap", "#1c1b1a", 0.8),
        "floor": floor_material("floor", spec["floors"]["main"]),
        "glass": glass_material(),
    }
    for label, floor in spec["floors"].items():
        if label != "main":
            mats[f"floorzone-{label}"] = floor_material(f"floorzone-{label}", floor)
    shell = b.finish(mats)
    air = spec["rooms"] + [{"polygon": o["polygon"], "height": o["height"], "walls": []}
                           for o in spec.get("outdoors", [])]
    for obj in shell:
        # The roof window's shaft is not room air, so its lining would count as unseen.
        if obj.name not in ("glass", "skylight"):
            mark_unseen(obj, air)
        elif obj.name == "skylight":
            mark_lining(obj, spec["skylights"], LINING_T)
    for obj in shell:
        if obj.name.startswith(("floor-", "floorzone-")):
            ATLAS_WEIGHT[obj.name] = PIECE_WEIGHT
        if obj.name.startswith("floor-"):
            floor_uvs(obj, spec["floors"]["main"]["tile"])
            # Exported as glTF extras: the viewer scales a swapped floor's
            # texture by the ratio of tile sizes, as these UVs are in tiles.
            obj["tileM"] = list(spec["floors"]["main"]["tile"])
            obj["roomId"] = obj.name.removeprefix("floor-")
        elif obj.name.startswith("floorzone-"):
            floor_uvs(obj, spec["floors"][obj.name.removeprefix("floorzone-")]["tile"])
            # Seen, but no shadow or bounce on the floor 2 mm beneath: that
            # floor bakes lit as the room is, so a new floor laid over both
            # (the viewer lays it on the zone too) is one light, no edge.
            obj.visible_shadow = obj.visible_diffuse = obj.visible_glossy = False
        else:
            plain_uvs(obj)
    scratch = Path(spec["out"]["meta"]).parent
    pieces = [furniture(item, spec["surfaces"], scratch) for item in spec["items"]]
    leaves = leaf_objects(DOOR_LEAVES, mats)
    for item, piece in zip(spec["items"], pieces, strict=True):
        # Its own share of the atlas: a balcony's floor and ceiling, seen
        # from a distance, need not a sofa's texels per metre.
        if "atlas" in (item.get("options") or {}):
            ATLAS_WEIGHT[piece.name] = item["options"]["atlas"]
    for piece in pieces + leaves:
        ATLAS_WEIGHT.setdefault(piece.name, PIECE_WEIGHT)
        SMOOTH.setdefault(piece.name, PIECE_SMOOTH)
    clothes = [c for item, piece in zip(spec["items"], pieces, strict=True)
               if (c := hang_clothes(item, piece, spec["surfaces"])
                   or build_sink(item, piece, spec["surfaces"])
                   or vanity_basin(item, piece, spec["surfaces"])
                   or bedding(item, piece, spec["surfaces"]))]
    vertex_lit = [o for o in pieces + clothes if o.name in VERTEX_LIT]
    for obj in vertex_lit:
        # Exported as a glTF extra: the viewer lights it from its vertices,
        # and draws the sun on it when the bake left the sun out.
        obj["vertexLight"] = "baked" if obj.name in TRANSLUCENT else "sun"
    views = outside(spec)
    site = neighbourhood(spec)
    patch = light(spec, windows)
    ceiling_lights(spec)
    strips = strip_lights(spec)

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
    lit = ([o for o in shell if o not in glass] + leaves
           + [p for p in pieces if p.name not in VERTEX_LIT])
    layout_lightmap(lit, spec["lightmap_size"])
    # One bake per time of day, the default first: its exposure is the
    # reference, and its neighbourhood light sets the scale for the others.
    default = spec.get("sun")
    times = [default] + [t for t in spec.get("suns") or [] if t != default] if default else [None]
    # With a path for it, the sun's direct light is the viewer's to draw.
    live_sun = bool(spec.get("sun_path"))
    lighting, reference, site_scale = [], None, None
    for entry in times:
        if entry:
            set_sun(entry)
        set_strips(strips, entry)
        # The first (the default) at full quality; the other times, lit by
        # day and so less noisy, at half: five full bakes took hours.
        bpy.context.scene.cycles.samples = spec["samples"] if entry is times[0] else max(
            16, spec["samples"] // 2)
        label = entry["time"].replace(":", "") if entry else "default"
        path = out["lightmap"].replace(".jpg", f"-{label}.jpg")
        scale, auto, balance = bake(lit, spec["lightmap_size"], path, Path(out["meta"]).parent,
                                    live_sun)
        # A camera adjusts to the light, but only halfway: evenings stay darker.
        reference = reference or auto
        exposure_t = math.sqrt(reference * auto)
        lighting.append({"time": entry["time"] if entry else None, "lightmap": Path(path).name,
                         "lightmapScale": scale * exposure_t, "exposure": exposure_t,
                         "sunGain": [c * exposure_t for c in balance],
                         "skyTint": entry.get("tint") if entry else None,
                         "colours": light_set(label) if site or vertex_lit else None})
        if vertex_lit:
            vertex_light(vertex_lit, label, live_sun, balance, scale)
        if site:
            site_scale = bake_neighbourhood(site, label, site_scale)
    if default:
        set_sun(default)
    exposure = lighting[0]["exposure"]
    # The viewer shows colour x light x lightmapScale, so the exposure rides
    # on the scale; the views out of the windows carry no baked light and
    # so stay as photographed.
    Path(out["meta"]).write_text(json.dumps({"lightmapScale": lighting[0]["lightmapScale"],
                                             "lightmap": lighting[0]["lightmap"],
                                             "exposure": exposure,
                                             "lighting": lighting,
                                             "sunPath": (sun_path(spec["sun_path"], lighting)
                                                         if live_sun else []),
                                             "obstacles": obstacles(pieces)}))
    for obj in glass + views:
        obj.hide_render = False
    expose(exposure, views)
    preview(spec, windows, patch, out["preview"])
    bpy.ops.export_scene.gltf(filepath=out["glb"], export_format="GLB", export_apply=True,
                              export_lights=False, export_cameras=False, export_extras=True,
                              export_vertex_color="NONE", export_attributes=True)


main()
