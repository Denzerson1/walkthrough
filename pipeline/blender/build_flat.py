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
from mathutils.kdtree import KDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "walkthrough_pipeline"))
from flat_geometry import (  # noqa: E402  (standard library only, so importable here)
    WALL_T,
    atlas_cells,
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
                door(b, wall, lo, hi, top, o.get("frame", True), o.get("latch", "end"))
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


def bake_neighbourhood(objects: list[bpy.types.Object], label: str,
                       scale: float | None = None) -> float:
    """
    The neighbourhood's light (sun, sky, shadows, bounce) into its vertices,
    scaled so a sunlit wall shows its plaster at full strength: the viewer
    shows it unlit as texture x vertex colour, at an outdoor exposure, so
    the view stays visible from a room exposed for its inside.
    """
    import numpy as np

    name = f"light-{label}"
    for obj in objects:
        attr = obj.data.color_attributes.new(name, "FLOAT_COLOR", "CORNER")
        obj.data.color_attributes.active_color = attr
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"DIRECT", "INDIRECT"},
                        target="VERTEX_COLORS")
    values = []
    for obj in objects:
        data = np.empty(len(obj.data.color_attributes[name].data) * 4, np.float32)
        obj.data.color_attributes[name].data.foreach_get("color", data)
        values.append(data.reshape(-1, 4))
    if scale is None:  # the first time of day sets the scale for all of them
        every = np.concatenate([v[:, :3].max(axis=1) for v in values])
        scale = 0.95 / max(float(np.percentile(every, 95)), 1e-6)
    for obj, data in zip(objects, values, strict=True):
        data[:, :3] = np.clip(data[:, :3] * scale, 0.0, 1.0)
        data[:, 3] = 1.0
        obj.data.color_attributes[name].data.foreach_set("color", data.ravel())
    return scale


def door(b: Builder, wall: dict, lo: float, hi: float, top: float, framed: bool = True,
         latch: str = "end") -> None:
    """
    A door in the wall from lo to hi: its leaf, the handle at the `latch`
    end of the wall ("start" or "end"), and either an architrave round it
    (`framed`) or, for a concealed-frame door, the leaf flush with the wall
    and only a 4 mm dark shadow gap round it.
    """
    mid = WALL_T / 2
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
        trim = TRIM_W if o.get("frame", True) else 0.0
        lo = max(0.0, o["offset"] - o["width"] / 2 - trim)
        if lo > u:
            wall_box(b, "trim", wall, u, lo, 0, SKIRTING_H, -SKIRTING_D, 0)
        u = o["offset"] + o["width"] / 2 + trim
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

FRONT_T, GAP, GRIP = 0.019, 0.003, 0.03  # front thickness, gap between fronts, grip channel

#: Rounded edges, for the pieces that have them: (bevel width, segments).
SOFTNESS = {"sofa": (0.06, 5), "bed": (0.025, 3), "air-con": (0.02, 3), "tv": (0.003, 1)}

Part = tuple[str, tuple[float, float, float, float, float, float]]


def column_widths(x0: float, x1: float, columns: list) -> list[float]:
    """The widths of a run of `columns` (see `fronts`), a width of 0 sharing what is left."""
    fixed = sum(c[0] for c in columns if c[0])
    free = sum(1 for c in columns if not c[0])
    share = (x1 - x0 - fixed) / free if free else 0.0
    return [c[0] or share for c in columns]


def fronts(x0: float, x1: float, z0: float, z1: float, y: float, columns: list,
           surface: str = "gloss", grips: bool = True) -> list[Part]:
    """
    Door and drawer fronts standing on a carcass face, their front at y.
    `columns` are (width, count) left to right, a width of 0 sharing what is
    left; each column's fronts are stacked with a recessed aluminium grip
    channel between them (`grips`), as on a handleless kitchen, or a plain gap.
    """
    parts: list[Part] = []
    x = x0
    for column, width in zip(columns, column_widths(x0, x1, columns), strict=True):
        count = column[1]
        between = GRIP if grips else GAP
        height = (z1 - z0 - between * (count - 1)) / count
        for i in range(count):
            za = z0 + i * (height + between)
            parts.append((surface, (x + GAP / 2, x + width - GAP / 2, y, y + FRONT_T,
                                    za, za + height)))
            if grips and i < count - 1:
                parts.append(("alu", (x, x + width, y + 0.004, y + FRONT_T + 0.01,
                                      za + height + 0.001, za + height + GRIP - 0.001)))
        x += width
    return parts


def column_span(x0: float, x1: float, columns: list, tag: str) -> tuple[float, float] | None:
    """Where along a run of `columns` (see `fronts`) the one tagged `tag` lies."""
    x = x0
    for column, width in zip(columns, column_widths(x0, x1, columns), strict=True):
        if tag in column[2:]:
            return x, x + width
        x += width
    return None


def sink(x0: float, x1: float, y0: float, y1: float, h: float, lo: float,
         hi: float) -> list[Part]:
    """A worktop with an inset basin between lo and hi, and a tap behind it."""
    cx = (lo + hi) / 2
    hx0, hx1, hy0, hy1 = cx - 0.25, cx + 0.25, y0 + 0.08, y0 + 0.50
    top, wall, rim = h - 0.04, 0.012, 0.02
    return [
        ("stone", (x0, hx0, y0 - 0.02, y1, top, h)),
        ("stone", (hx1, x1, y0 - 0.02, y1, top, h)),
        ("stone", (hx0, hx1, y0 - 0.02, hy0, top, h)),
        ("stone", (hx0, hx1, hy1, y1, top, h)),
        ("basin", (hx0, hx1, hy0, hy1, h - 0.19, h - 0.18)),
        ("basin", (hx0, hx0 + wall, hy0, hy1, h - 0.18, h)),
        ("basin", (hx1 - wall, hx1, hy0, hy1, h - 0.18, h)),
        ("basin", (hx0 + wall, hx1 - wall, hy0, hy0 + wall, h - 0.18, h)),
        ("basin", (hx0 + wall, hx1 - wall, hy1 - wall, hy1, h - 0.18, h)),
        ("basin", (hx0 - rim, hx0, hy0 - rim, hy1 + rim, h, h + 0.006)),
        ("basin", (hx1, hx1 + rim, hy0 - rim, hy1 + rim, h, h + 0.006)),
        ("basin", (hx0, hx1, hy0 - rim, hy0, h, h + 0.006)),
        ("basin", (hx0, hx1, hy1, hy1 + rim, h, h + 0.006)),
        ("steel", (cx - 0.022, cx + 0.022, hy1 + 0.03, hy1 + 0.074, h, h + 0.30)),
        ("steel", (cx - 0.012, cx + 0.012, hy1 - 0.16, hy1 + 0.07, h + 0.276, h + 0.30)),
    ]


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
    worktop = ("stone", (x0, x1, y0 - 0.02, y1, h - 0.04, h))
    xi0, xi1 = x0 + 0.018, x1 - 0.018  # inside a carcass's sides

    if finish == "kitchen-base":
        columns = opts.get("columns", [[0, 1]])
        parts = [plinth, carcass, top_grip,
                 *fronts(x0, x1, 0.1 + GAP, h - 0.04 - GRIP, y0, [c[:2] for c in columns])]
        span = column_span(x0, x1, columns, "sink")
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
        return [("black", (x0, x1, y0, y1, 0, h))]
    if finish == "kitchen-tall":
        return [plinth, ("white", (x0, x1, y0 + FRONT_T, y1, 0.1, h)),
                *fronts(x0, x1, 0.1 + GAP, 0.86, y0, [[0, 2]]),
                ("alu", (x0, x1, y0 + 0.004, y0 + FRONT_T + 0.01, 0.861, 0.889)),
                ("gloss", (x0 + GAP / 2, x1 - GAP / 2, y0, y0 + FRONT_T, 0.89, 1.955)),
                ("gloss", (x0 + GAP / 2, x1 - GAP / 2, y0, y0 + FRONT_T, 1.958, h - 0.002))]
    if finish == "kitchen-wall":
        rows, columns = opts.get("rows", 1), opts.get("columns", 1)
        parts = [("white", (x0, x1, y0 + FRONT_T, y1, 0, h))]
        height = h / rows
        for r in range(rows):
            parts += fronts(x0, x1, r * height + GAP / 2, (r + 1) * height - GAP / 2, y0,
                            [[0, 1]] * columns, grips=False)
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
    if finish == "air-con":
        return [("white", (x0, x1, y0, y1, 0, h)),
                ("vent", (x0 + 0.05, x1 - 0.05, y0 - 0.002, y0 + 0.03, 0.02, 0.07))]
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
        # door hidden behind the other wing gets none.
        columns = opts.get("columns") or [[0, 1]] * opts.get("doors", 2)
        parts = [("oak", (x0, x1, y0 + FRONT_T, y1, 0, h)),
                 *fronts(x0, x1, 0.06, h - 0.004, y0, columns, surface="oak", grips=False)]
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
        # back, two shelves at the top, a rail with clothes, a glass shelf
        # with boxes, drawers and a wire basket below.
        ym, inner = (y0 + y1) / 2, xi1 - xi0
        drawers = opts.get("drawers", 3)
        parts = [("oak", (x0, xi0, y0, y1, 0, h)), ("oak", (xi1, x1, y0, y1, 0, h)),
                 ("oak", (xi0, xi1, y0, y1, h - 0.018, h)),
                 ("oak", (xi0, xi1, y0 + 0.02, y1, 0, 0.06)),
                 ("oak", (xi0, xi1, y1 - 0.008, y1, 0.06, h - 0.018)),
                 ("oak", (xi0, xi1, y0 + 0.01, y1 - 0.008, h - 0.38, h - 0.362)),
                 ("oak", (xi0, xi1, y0 + 0.01, y1 - 0.008, h - 0.2, h - 0.182)),
                 ("steel", (xi0, xi1, ym - 0.01, ym + 0.01, h - 0.46, h - 0.44)),
                 # As photographed (garderobe3): a leather jacket and a pale
                 # coat at the right, the oak back open beside them.
                 ("wenge", (xi0 + 0.66 * inner, xi0 + 0.89 * inner, ym - 0.17, ym + 0.17,
                            1.23, h - 0.47)),
                 ("coat-light", (xi0 + 0.9 * inner, xi1 - 0.01, ym - 0.15, ym + 0.15,
                                 1.15, h - 0.47))]
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
        # a shelf near the top, a rail under it with coats hanging down to a
        # white two-drawer unit with an oak top, on a plinth.
        ym, top = (y0 + y1) / 2, h - 0.40
        parts = [("oak-warm", (x0, xi0, y0, y1, 0, h)),
                 ("oak-warm", (xi0, x1, y1 - 0.018, y1, 0, h)),
                 ("oak-warm", (xi0, x1, y0 + 0.01, y1 - 0.018, h - 0.32, h - 0.30)),
                 ("steel", (xi0, x1, ym - 0.01, ym + 0.01, top, top + 0.02)),
                 ("plinth", (xi0, x1, y0 + 0.05, y1 - 0.018, 0, 0.08)),
                 ("white", (xi0, x1, y0 + FRONT_T, y1 - 0.018, 0.08, 0.5)),
                 ("oak-warm", (xi0, x1, y0, y1 - 0.018, 0.5, 0.518)),
                 *fronts(xi0, x1, 0.08 + GAP, 0.5 - GAP, y0, [[0, 2]], surface="white")]
        # Coats side by side, long dark ones and a pale trench.
        x = xi0 + 0.02
        for share, bottom, surface in ((0.42, 0.6, "coats"), (0.18, 0.88, "coat-light"),
                                       (0.4, 0.66, "coats")):
            width = share * (x1 - 0.04 - (xi0 + 0.02))
            parts.append((surface, (x, x + width - 0.01, ym - 0.2, ym + 0.2, bottom, top - 0.02)))
            x += width
        return parts
    if finish == "bed":
        base = min(0.26, h * 0.4)
        return [("wenge", (x0, x1, y0, y1, 0.02, base)),
                ("wenge", (x0 + 0.04, x1 - 0.04, y0 + 0.04, y1 - 0.04, 0, 0.02)),
                ("linen", (x0 + 0.03, x1 - 0.03, y0 + 0.03, y1 - 0.03, base, h - 0.08)),
                ("linen", (x0 - 0.02, x1 + 0.01, y0 - 0.02, y1 + 0.02, h - 0.14, h))]
    if finish == "sofa":
        return [("boucle", (x0, x1, y0, y1, 0, 0.42)),
                ("boucle", (x0, x1, y1 - 0.26, y1, 0.42, h)),
                ("boucle", (x0, x0 + 0.24, y0, y1 - 0.26, 0.42, 0.62)),
                ("boucle", (x1 - 0.24, x1, y0, y1 - 0.26, 0.42, 0.62)),
                ("boucle", (x0 + 0.34, x0 + 0.8, y1 - 0.38, y1 - 0.24, 0.42, 0.7)),
                ("boucle", (x1 - 0.8, x1 - 0.34, y1 - 0.38, y1 - 0.24, 0.42, 0.7))]
    if finish == "tv":
        return [("black", (x0, x1, y0, y0 + 0.012, 0, h)),
                ("vent", (x0 + 0.08, x1 - 0.08, y0 + 0.012, y1, 0.08, h - 0.08))]
    raise ValueError(f"Unknown finish {finish!r}")


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


def surface_material(name: str, surface: dict) -> bpy.types.Material:
    """A surface from the spec: its colour or its texture, and its roughness."""
    mat = bpy.data.materials.get(f"surface-{name}")
    if mat is not None:
        return mat
    mat = material(f"surface-{name}", surface["colour"], surface["roughness"])
    if surface.get("texture"):
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(surface["texture"])
        links.new(tex.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
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
    if item["finish"] == "stool":
        stool(bm, w, h, d, slot, surfaces)
    elif item["finish"] == "pendant":
        # A drum shade on a cable from the ceiling.
        radius, shade = min(w, d) / 2, 0.28
        cone = bmesh.ops.create_cone(bm, cap_ends=True, segments=40, radius1=radius,
                                     radius2=radius, depth=shade)
        bmesh.ops.translate(bm, verts=cone["verts"], vec=(0, 0, shade / 2))
        cable = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, verts=cable["verts"], vec=(0.008, 0.008, h - shade))
        bmesh.ops.translate(bm, verts=cable["verts"], vec=(0, 0, shade + (h - shade) / 2))
        for face in bm.faces:
            face.material_index = slot("shade")
    else:
        for surface, (px0, px1, py0, py1, pz0, pz1) in finish_parts(item["finish"], w, h, d, opts):
            cube = bmesh.ops.create_cube(bm, size=1.0)
            size = (px1 - px0, py1 - py0, pz1 - pz0)
            bmesh.ops.scale(bm, verts=cube["verts"], vec=size)
            bmesh.ops.translate(bm, verts=cube["verts"],
                                vec=((px0 + px1) / 2, (py0 + py1) / 2, (pz0 + pz1) / 2))
            faces = {f for v in cube["verts"] for f in v.link_faces}
            for face in faces:
                face.material_index = slot(surface)
            box_uvs(bm, faces, size, surfaces[surface].get("tile", (1.0, 1.0)))
        if item["finish"] == "island":
            add_slab(bm, island_top(w, h, d, opts), slot, surfaces)
    bm.to_mesh(mesh)
    bm.free()
    for surface in slots:
        mesh.materials.append(surface_material(surface, surfaces[surface]))
    obj = bpy.data.objects.new(item["id"], mesh)
    bpy.context.collection.objects.link(obj)
    if item["finish"] in SOFTNESS:
        bevel = obj.modifiers.new("soft", "BEVEL")
        bevel.width, bevel.segments = SOFTNESS[item["finish"]]
    if not obj.data.uv_layers:
        plain_uvs(obj)
    return obj


def furniture(item: dict, surfaces: dict) -> bpy.types.Object:
    """The piece's model (or a block), fitted to its scanned box and turned to face `front`."""
    w, h, d = item["size"]
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
        area = bpy.data.lights.new(f"lamp-light-{i}", "AREA")
        area.shape, area.size = "SQUARE", size
        area.energy = lamp.get("power", 15.0)
        area.color = lamp.get("colour", (1.0, 0.86, 0.72))
        obj = bpy.data.objects.new(area.name, area)
        bpy.context.collection.objects.link(obj)
        obj.location = P(x, y - 0.012, z)  # an area light shines down its -Z: down
        bm = bmesh.new()
        h = size / 2
        verts = [bm.verts.new(P(x + dx, y - 0.008, z + dz))
                 for dx, dz in ((-h, -h), (h, -h), (h, h), (-h, h))]
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
    the walk out of them.
    """
    out = []
    for obj in pieces:
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


#: Atlas share per object, as a multiple of its surface: the trim (door
#: frames, leaves, skirtings, window frames) gets 6x, 2.5x the texels per
#: metre. At the walls' density a 7 cm architrave was 4 texels wide and its
#: light came out in grey blotches.
ATLAS_WEIGHT = {"trim": 6.0}
#: Where each object's light lies in the atlas, (x, y, side) in UV units.
CELLS: dict[str, tuple[float, float, float]] = {}
#: Extra smoothing for an object's light: painted trim's light is smooth,
#: and on few texels the denoiser leaves blotches.
SMOOTH = {"trim": 2.5}
#: The same for every piece of furniture: on few texels its light came out
#: in blotches on the kitchen's white fronts once exposed.
PIECE_WEIGHT, PIECE_SMOOTH = 1.5, 2.0


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
        # Not corrected for a texture's aspect: that squashed the islands of
        # pieces with a photo texture (the wardrobe's oak, 1:4) up to 16x
        # across, and a drawer front's light came out black.
        bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02,
                                 correct_aspect=False)
        bpy.ops.object.mode_set(mode="OBJECT")
        # Unseen faces shrink to a point: in the bake, but on no texels. The
        # point is the nearest seen corner's, not the atlas's empty corner:
        # where one shows after all (the slope's cut beside a roof window's
        # lining), it carries its neighbour's light rather than black.
        uv = obj.data.uv_layers["lightmap"].data
        seen = [i for poly, is_hidden in zip(obj.data.polygons, hidden, strict=True)
                if not is_hidden for i in poly.loop_indices]
        tree = KDTree(len(seen))
        for k, i in enumerate(seen):
            tree.insert(obj.data.vertices[obj.data.loops[i].vertex_index].co, k)
        tree.balance()
        for poly, is_hidden in zip(obj.data.polygons, hidden, strict=True):
            if is_hidden:
                point = uv[seen[tree.find(poly.center)[1]]].uv.copy() if seen else (0.0, 0.0)
                for i in poly.loop_indices:
                    uv[i].uv = point
        areas.append(ATLAS_WEIGHT.get(obj.name, 1.0)
                     * sum(p.area for p, h in zip(obj.data.polygons, hidden, strict=True) if not h))
    corners, sides = atlas_cells(areas, margin)
    for obj, side, (cx, cy) in zip(objects, sides, corners, strict=True):
        CELLS[obj.name] = (cx, cy, side)
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
#: clips — sun on a window lining, which a photograph blows out too. Set
#: against the owner's photos: white walls in shade read grey, as a phone
#: exposing for the window shows them; brighter read as a render.
EXPOSE_TO, CLIP = 0.22, 16.0
#: How far the light's colour cast is taken out, like a camera's white
#: balance: the shade's colour is made neutral (walls white, as in the
#: photos), sun patches stay warmer than it. 1 would neutralise it fully.
WHITE_BALANCE = 0.6
#: Then a slight warm cast over it all, like a camera set a little below
#: daylight: the owner found the neutral room a touch cool.
WARMTH = (1.08, 1.0, 0.86)
#: The light map holds (light / scale) ** (1 / LIGHTMAP_GAMMA): steeper than
#: sRGB, so dim light (a hall lit only by bounce) gets enough of the 256
#: levels. With sRGB, the hall's few levels showed as grey blotches and JPEG
#: blocks once the viewer brightened it. daylight.ts LIGHT_MAP_GAMMA matches.
LIGHTMAP_GAMMA = 4.0


def save_lightmap(baked: bpy.types.Image, path: str, scratch: Path) -> tuple[float, float]:
    """
    The float bake as an 8-bit JPEG (light ** 1/LIGHTMAP_GAMMA), plus the scale that undoes it and
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
    rgb = smooth_within_islands(clean, covered, sigma=1.0)
    for name, sigma in SMOOTH.items():
        if name in CELLS:  # rows run up from v = 0, like the pixels
            cx, cy, side = CELLS[name]
            rows = slice(int(cy * size), min(size, int((cy + side) * size) + 1))
            cols = slice(int(cx * size), min(size, int((cx + side) * size) + 1))
            rgb[rows, cols] = smooth_within_islands(clean[rows, cols], covered[rows, cols], sigma)
    rgb = rgb.reshape(-1, 3)
    covered = covered.ravel()
    if covered.any():
        shade = np.median(rgb[covered], axis=0)
        rgb = rgb * (shade.mean() / np.maximum(shade, 1e-6)) ** WHITE_BALANCE
    rgb = rgb * np.array(WARMTH, dtype=np.float32)
    # The room's typical light: on its walls and ceilings, which are what a
    # camera exposes for. Over the whole atlas, the trim's larger share of
    # it (ATLAS_WEIGHT) and a lit hall pulled the median up, the room down.
    room = np.zeros((size, size), bool)
    for name in ("wall", "ceiling"):
        if name in CELLS:
            cx, cy, side = CELLS[name]
            room[int(cy * size) : int((cy + side) * size) + 1,
                 int(cx * size) : int((cx + side) * size) + 1] = True
    sample = covered & room.ravel() if (covered & room.ravel()).any() else covered
    typical = float(np.median(rgb.max(axis=1)[sample])) if sample.any() else 1.0
    scale = float(max(min(np.percentile(rgb.max(axis=1), 99.9), CLIP * typical), 1e-6))
    # Mostly white walls and ceilings: albedo ~0.85 under the typical light.
    exposure = min(max(EXPOSE_TO / (0.85 * max(typical, 1e-6)), 1.0), 64.0)
    out = bpy.data.images.new("lightmap-8bit", size, size)
    out.colorspace_settings.name = "Non-Color"
    linear = np.clip(rgb / scale, 0.0, 1.0)
    encoded = np.ones((size * size, 4), dtype=np.float32)
    # An 8-bit image's pixels are stored as given: encoded here.
    encoded[:, :3] = np.power(linear, 1 / LIGHTMAP_GAMMA)
    out.pixels.foreach_set(encoded.ravel())
    out.filepath_raw = path
    out.file_format = "JPEG"
    # Image.save's own quality: the scene's render setting does not reach it,
    # and the default (90) left blocks on white fronts once exposed.
    out.save(quality=95)
    return scale, exposure


def bake(objects: list[bpy.types.Object], size: int, path: str,
         scratch: Path) -> tuple[float, float]:
    """
    All objects' diffuse light (no colour) into one atlas on their second UV
    set (laid out by layout_lightmap). Returns the scale save_lightmap
    stored it at and the exposure the room wants in this light.
    """
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
    pieces = [furniture(item, spec["surfaces"]) for item in spec["items"]]
    for piece in pieces:
        ATLAS_WEIGHT.setdefault(piece.name, PIECE_WEIGHT)
        SMOOTH.setdefault(piece.name, PIECE_SMOOTH)
    views = outside(spec)
    site = neighbourhood(spec)
    patch = light(spec, windows)
    ceiling_lights(spec)

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
    lit = [o for o in shell if o not in glass] + pieces
    layout_lightmap(lit)
    # One bake per time of day, the default first: its exposure is the
    # reference, and its neighbourhood light sets the scale for the others.
    default = spec.get("sun")
    times = [default] + [t for t in spec.get("suns") or [] if t != default] if default else [None]
    lighting, reference, site_scale = [], None, None
    for entry in times:
        if entry:
            set_sun(entry)
        label = entry["time"].replace(":", "") if entry else "default"
        path = out["lightmap"].replace(".jpg", f"-{label}.jpg")
        scale, auto = bake(lit, spec["lightmap_size"], path, Path(out["meta"]).parent)
        # A camera adjusts to the light, but only halfway: evenings stay darker.
        reference = reference or auto
        exposure_t = math.sqrt(reference * auto)
        lighting.append({"time": entry["time"] if entry else None, "lightmap": Path(path).name,
                         "lightmapScale": scale * exposure_t, "exposure": exposure_t,
                         "skyTint": entry.get("tint") if entry else None,
                         "colours": f"light-{label}" if site else None})
        if site:
            site_scale = bake_neighbourhood(site, label, site_scale)
    if default:
        set_sun(default)
    for obj in site:  # the default time's light is the one the file opens with
        obj.data.color_attributes.active_color = obj.data.color_attributes[lighting[0]["colours"]]
    exposure = lighting[0]["exposure"]
    # The viewer shows colour x light x lightmapScale, so the exposure rides
    # on the scale; the views out of the windows carry no baked light and
    # so stay as photographed.
    Path(out["meta"]).write_text(json.dumps({"lightmapScale": lighting[0]["lightmapScale"],
                                             "lightmap": lighting[0]["lightmap"],
                                             "exposure": exposure,
                                             "lighting": lighting,
                                             "obstacles": obstacles(pieces)}))
    for obj in glass + views:
        obj.hide_render = False
    expose(exposure, views)
    preview(spec, windows, patch, out["preview"])
    bpy.ops.export_scene.gltf(filepath=out["glb"], export_format="GLB", export_apply=True,
                              export_lights=False, export_cameras=False, export_extras=True,
                              export_vertex_color="ACTIVE", export_all_vertex_colors=True)


main()
