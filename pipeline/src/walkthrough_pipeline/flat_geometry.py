"""
Pure geometry for the rebuilt flat, shared by build_flat.py (inside Blender)
and the tests. Standard library only: Blender's Python has neither this
package's dependencies nor pydantic, so it imports this file on its own.

Scene space throughout: metres, Y up, plans in (x, z).
"""

from __future__ import annotations

import math

#: Wall thickness, outward from the scanned interior surface.
WALL_T = 0.15

Point = tuple[float, float]


def wall_frame(start, end) -> tuple[Point, Point, float]:
    """Unit direction, outward normal and length of a wall of a counter-clockwise room."""
    dx, dz = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dz)
    d = (dx / length, dz / length)
    return d, (d[1], -d[0]), length


def grow(polygon, by: float) -> list[list[float]]:
    """The polygon pushed outward by `by`, each corner along its mitre."""
    n, out = len(polygon), []
    for i in range(n):
        p0, p1, p2 = polygon[i - 1], polygon[i], polygon[(i + 1) % n]
        (_, a, _), (_, b, _) = wall_frame(p0, p1), wall_frame(p1, p2)
        mx, mz = a[0] + b[0], a[1] + b[1]
        length = math.hypot(mx, mz)
        cos_half = max(length / 2, 0.3)  # cap the mitre on very sharp corners
        out.append([p1[0] + mx / length * by / cos_half, p1[1] + mz / length * by / cos_half])
    return out


def point_in_polygon(x: float, z: float, polygon) -> bool:
    inside, j = False, len(polygon) - 1
    for i in range(len(polygon)):
        (xi, zi), (xj, zj) = polygon[i], polygon[j]
        if (zi > z) != (zj > z) and x < (xj - xi) * (z - zi) / (zj - zi) + xi:
            inside = not inside
        j = i
    return inside


def opening_spans(wall: dict, height: float) -> list[tuple[float, float, float, float]]:
    """Each opening as (u0, u1, bottom, top) along the wall, clipped to it."""
    _, _, length = wall_frame(wall["start"], wall["end"])
    return [
        (
            max(0.0, o["offset"] - o["width"] / 2),
            min(length, o["offset"] + o["width"] / 2),
            o["bottom"],
            min(o["bottom"] + o["height"], height),
        )
        for o in sorted(wall["openings"], key=lambda o: o["offset"])
    ]


def wall_cells(length: float, height: float, spans) -> list[tuple[float, float, float, float]]:
    """
    The solid part of a wall as a grid of (u0, u1, y0, y1) cells, cut at every
    opening's edges and heights. A full-height piece beside an opening would
    have an end face running past the lintel, seeing the sky through the
    opening; in a grid every such face is buried in a neighbouring cell.
    """
    us = sorted({0.0, length, *(x for u0, u1, _, _ in spans for x in (u0, u1))})
    ys = sorted({0.0, height, *(y for _, _, b, t in spans for y in (b, t))})
    cells = []
    for u0, u1 in zip(us, us[1:], strict=False):
        for y0, y1 in zip(ys, ys[1:], strict=False):
            um, ym = (u0 + u1) / 2, (y0 + y1) / 2
            if not any(a < um < b and lo < ym < hi for a, b, lo, hi in spans):
                cells.append((u0, u1, y0, y1))
    return cells


def is_air(x: float, y: float, z: float, rooms: list[dict]) -> bool:
    """Inside a room, or inside a door or window opening's slot through its wall."""
    for room in rooms:
        if 0 < y < room["height"] and point_in_polygon(x, z, room["polygon"]):
            return True
        for wall in room["walls"]:
            (dx, dz), (nx, nz), _ = wall_frame(wall["start"], wall["end"])
            rx, rz = x - wall["start"][0], z - wall["start"][1]
            u, v = rx * dx + rz * dz, rx * nx + rz * nz
            if not -0.001 <= v <= WALL_T + 0.001:
                continue
            for o in wall["openings"]:
                if (abs(u - o["offset"]) < o["width"] / 2
                        and o["bottom"] < y < o["bottom"] + o["height"]):
                    return True
    return False


def shelf_pack(sides: list[float], margin: float) -> list[Point] | None:
    """Corners for squares of `sides` in the unit square, row by row; None if they overflow."""
    order = sorted(range(len(sides)), key=lambda i: -sides[i])
    corners: list[Point] = [(0.0, 0.0)] * len(sides)
    x = y = row = 0.0
    for i in order:
        cell = sides[i] + margin
        if x + cell > 1.0:
            x, y, row = 0.0, y + row, 0.0
        if y + cell > 1.0:
            return None
        corners[i] = (x + margin / 2, y + margin / 2)
        x, row = x + cell, max(row, cell)
    return corners


def atlas_cells(areas: list[float], margin: float) -> tuple[list[Point], list[float]]:
    """
    Square atlas cells, one per object, sized by the root of its surface area
    so texel density stays roughly even: the largest scale at which they all
    still fit. Returns corners and side lengths.
    """
    sides = [math.sqrt(max(a, 1e-6)) for a in areas]
    lo, hi = 0.0, 1.0 / max(sides)
    for _ in range(40):
        mid = (lo + hi) / 2
        if shelf_pack([s * mid for s in sides], margin) is None:
            hi = mid
        else:
            lo = mid
    scaled = [s * lo for s in sides]
    corners = shelf_pack(scaled, margin)
    assert corners is not None
    return corners, scaled
