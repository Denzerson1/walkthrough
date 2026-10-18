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


def mitres(polygon, thickness: float) -> list[tuple[float, float]]:
    """
    For each wall of a room outline, how far its start and end reach along
    it on the wall's outer face, so neighbours meet on the corner's
    diagonal: out past the corner where the room turns in (a convex corner),
    short of it where it turns out (an inside corner, like the mouth of a
    passage). Square-ended walls overlap at an inside corner, and the end of
    one lies in the face of the other, which renders as black flicker.
    """
    n = len(polygon)
    reach = [[0.0, 0.0] for _ in range(n)]
    for i in range(n):
        a = (polygon[i - 1], polygon[i])  # the wall ending at corner i
        b = (polygon[i], polygon[(i + 1) % n])  # the wall starting there
        (da, na, _), (db, nb, _) = wall_frame(*a), wall_frame(*b)
        # Where the two outer faces meet: s along a past its end, t along b.
        det = -da[0] * db[1] + db[0] * da[1]
        if abs(det) < 1e-9:
            continue  # the walls continue each other
        rx, rz = (nb[0] - na[0]) * thickness, (nb[1] - na[1]) * thickness
        s = (-rx * db[1] + db[0] * rz) / det
        t = (da[0] * rz - da[1] * rx) / det
        reach[i - 1][1] = s
        reach[i][0] = t
    return [(r[0], r[1]) for r in reach]


def is_air(x: float, y: float, z: float, rooms: list[dict]) -> bool:
    """Inside a room, or inside a door or window opening's slot through its wall."""
    for room in rooms:
        if point_in_polygon(x, z, room["polygon"]):
            top = ceiling_height(x, z, room.get("ceilings") or [], room["height"])
            if 0 < y < top:
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


# --------------------------------------------------------------------------
# Outlines
# --------------------------------------------------------------------------


def polygon_union(polygons, tol: float = 0.01) -> list[list[float]]:
    """
    The outline of polygons that tile a region without overlapping, like a
    scan's floor zones: every edge two polygons share cancels out, and what
    is left is chained into one counter-clockwise loop with collinear
    corners dropped. Edges are first split wherever another polygon's corner
    lies on them, since neighbours do not always share corners.
    """
    # Corners within `tol` of each other are one corner. Snapping by distance,
    # not by rounding: 1.1849 and 1.1851 round apart, and a shared edge
    # that does not cancel leaves a spike in the outline.
    canonical: list[tuple[float, float]] = []

    def key(p):
        for i, c in enumerate(canonical):
            if abs(c[0] - p[0]) <= tol and abs(c[1] - p[1]) <= tol:
                return i
        canonical.append((float(p[0]), float(p[1])))
        return len(canonical) - 1

    loops = [p if signed_area(p) > 0 else p[::-1] for p in polygons]
    corners = {key(p): canonical[key(p)] for loop in loops for p in loop}
    edges: dict[tuple, int] = {}
    for loop in loops:
        for i in range(len(loop)):
            a, b = loop[i], loop[(i + 1) % len(loop)]
            on = [c for c in corners.values() if _strictly_between(a, b, c, tol)]
            on.sort(key=lambda c: math.hypot(c[0] - a[0], c[1] - a[1]))
            chain = [a, *on, b]
            for p, q in zip(chain, chain[1:], strict=False):
                pk, qk = key(p), key(q)
                if edges.get((qk, pk)):
                    edges[(qk, pk)] -= 1
                else:
                    edges[(pk, qk)] = edges.get((pk, qk), 0) + 1
    remaining = {a: b for (a, b), n in edges.items() if n > 0}
    if not remaining:
        return []
    start = next(iter(remaining))
    loop, at = [start], remaining.pop(start)
    while at != start:
        loop.append(at)
        at = remaining.pop(at)
    points = [corners[k] for k in loop]
    out = [
        list(p) for i, p in enumerate(points)
        if abs(_cross(points[i - 1], p, points[(i + 1) % len(points)])) > tol * tol
    ]
    return out


def signed_area(polygon) -> float:
    return 0.5 * sum(
        polygon[i][0] * polygon[(i + 1) % len(polygon)][1]
        - polygon[(i + 1) % len(polygon)][0] * polygon[i][1]
        for i in range(len(polygon))
    )


def _cross(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])


def _strictly_between(a, b, c, tol: float) -> bool:
    """c lies on segment ab, away from both ends."""
    abx, abz = b[0] - a[0], b[1] - a[1]
    length = math.hypot(abx, abz)
    if length < tol:
        return False
    t = ((c[0] - a[0]) * abx + (c[1] - a[1]) * abz) / length**2
    off = abs((c[0] - a[0]) * abz - (c[1] - a[1]) * abx) / length
    return off < tol and tol / length < t < 1 - tol / length


# --------------------------------------------------------------------------
# Ceilings: flat and sloped planes from the scan
# --------------------------------------------------------------------------


def plane(points) -> tuple[list[float], list[float]]:
    """A polygon's plane as a point on it and its unit normal facing down (into the room)."""
    n = [0.0, 0.0, 0.0]
    for i in range(len(points)):  # Newell's method: robust for any planar polygon
        p, q = points[i], points[(i + 1) % len(points)]
        n[0] += (p[1] - q[1]) * (p[2] + q[2])
        n[1] += (p[2] - q[2]) * (p[0] + q[0])
        n[2] += (p[0] - q[0]) * (p[1] + q[1])
    length = math.sqrt(sum(c * c for c in n)) or 1.0
    n = [c / length for c in n]
    if n[1] > 0:
        n = [-c for c in n]
    return list(points[0]), n


def is_flat(points, tol: float = 0.01) -> bool:
    ys = [p[1] for p in points]
    return max(ys) - min(ys) < tol


def ceiling_height(x: float, z: float, ceilings, default: float) -> float:
    """How high the ceiling is above a floor point: the plane of the piece over it."""
    best = None
    for points in ceilings:
        if point_in_polygon(x, z, [[p[0], p[2]] for p in points]):
            (px, py, pz), (nx, ny, nz) = plane(points)
            y = py - (nx * (x - px) + nz * (z - pz)) / ny
            best = y if best is None else max(best, y)
    return default if best is None else best


def merge_ceilings(ceilings, tol: float = 0.01) -> list[list[list[float]]]:
    """
    Ceiling pieces that share a plane, merged into one: a scan cuts one flat
    ceiling into a piece per zone and one long slope into strips, and two
    slabs meeting edge to edge in one plane show their seam as a line.
    Pieces that do not join into a single outline are kept apart.
    """
    groups: list[tuple[list[float], float, list]] = []
    for c in ceilings:
        p0, n = plane(c)
        d = sum(n[i] * p0[i] for i in range(3))
        for gn, gd, pieces in groups:
            if all(abs(gn[i] - n[i]) < 0.01 for i in range(3)) and abs(gd - d) < tol:
                pieces.append(c)
                break
        else:
            groups.append((n, d, [c]))
    out = []
    for _, _, pieces in groups:
        if len(pieces) == 1:
            out.append(pieces[0])
            continue
        flat = is_flat(pieces[0])
        if flat:
            y = sum(p[1] for c in pieces for p in c) / sum(len(c) for c in pieces)
            plans = [[[p[0], p[2]] for p in c] for c in pieces]
        else:
            frame = slope_frame([p for c in pieces for p in c])
            o, eu, ev = frame["origin"], frame["u"], frame["v"]
            plans = [[[sum((p[i] - o[i]) * eu[i] for i in range(3)),
                       sum((p[i] - o[i]) * ev[i] for i in range(3))] for p in c] for c in pieces]
        merged = polygon_union(plans, tol)
        whole = sum(abs(signed_area(p)) for p in plans)
        if not merged or abs(abs(signed_area(merged)) - whole) > 0.01 * whole:
            out.extend(pieces)
        elif flat:
            out.append([[a, y, b] for a, b in merged])
        else:
            out.append([slope_point(frame, a, b) for a, b in merged])
    return out


def wall_profile(start, end, ceilings, default: float) -> list[tuple[float, float]]:
    """
    The top of a wall as (u, height) corners along it, under the ceiling it
    meets: flat under a flat ceiling, a gable where a slope comes down. The
    corners are the wall's ends and wherever it crosses an edge between
    ceiling pieces; between them the top is straight.
    """
    (dx, dz), (nx, nz), length = wall_frame(start, end)
    us = {0.0, length}
    for points in ceilings:
        plan = [[p[0], p[2]] for p in points]
        for i in range(len(plan)):
            t = _segment_cross(start, end, plan[i], plan[(i + 1) % len(plan)])
            if t is not None:
                us.add(t * length)
    kept = [0.0]  # crossings within a centimetre of a corner add only slivers
    for u in sorted(us):
        if u - kept[-1] >= 0.01 and length - u >= 0.01:
            kept.append(u)
    kept.append(length)
    profile = []
    for u in kept:
        s = min(max(u, 0.01), length - 0.01)  # sample a centimetre into the room
        x, z = start[0] + dx * s - nx * 0.01, start[1] + dz * s - nz * 0.01
        profile.append((u, ceiling_height(x, z, ceilings, default)))
    return profile


def _segment_cross(a, b, c, d, reach: float = 0.02) -> float | None:
    """
    Where segment ab crosses segment cd, as a fraction along ab; None if
    parallel or apart. cd counts as `reach` metres longer at each end: a
    scanned ceiling's edge stops a millimetre or two short of the wall line
    the floor outline gives, and missing the crossing there missed a gable's
    bend.
    """
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    denom = r[0] * s[1] - r[1] * s[0]
    if abs(denom) < 1e-9:
        return None
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / denom
    w = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / denom
    slack = reach / max(math.hypot(*s), 1e-9)
    if 1e-6 < t < 1 - 1e-6 and -slack <= w <= 1 + slack:
        return t
    return None


def wall_pieces(length: float, profile, spans) -> list[tuple[float, float, float, float, float]]:
    """
    The solid part of a wall under a top that may slope, as prisms
    (u0, u1, y0, top0, top1): flat bottom, straight top. Like wall_cells,
    cut at every opening edge, plus where the top crosses a row, so no
    piece's top bends.
    """
    def top(u):
        for (ua, ta), (ub, tb) in zip(profile, profile[1:], strict=False):
            if ua - 1e-9 <= u <= ub + 1e-9:
                return ta if ub - ua < 1e-9 else ta + (tb - ta) * (u - ua) / (ub - ua)
        return profile[-1][1]

    peak = max(t for _, t in profile)
    ys = sorted({0.0, peak, *(y for _, _, b, t in spans for y in (b, t) if y < peak)})
    us = {0.0, length, *(u for u, _ in profile), *(x for a, b, _, _ in spans for x in (a, b))}
    for (ua, ta), (ub, tb) in zip(profile, profile[1:], strict=False):
        for y in ys:
            if (ta - y) * (tb - y) < 0:
                us.add(ua + (y - ta) / (tb - ta) * (ub - ua))
    us = sorted(u for u in us if 0 <= u <= length)
    pieces = []
    for u0, u1 in zip(us, us[1:], strict=False):
        if u1 - u0 < 1e-4:
            continue
        um = (u0 + u1) / 2
        for ya, yb in zip(ys, ys[1:], strict=False):
            if any(a < um < b and lo < (ya + yb) / 2 < hi for a, b, lo, hi in spans):
                continue
            t0, t1 = min(yb, top(u0)), min(yb, top(u1))
            if max(t0, t1) <= ya + 1e-4:
                continue
            pieces.append((u0, u1, ya, max(t0, ya), max(t1, ya)))
    return pieces


# --------------------------------------------------------------------------
# Roof windows
# --------------------------------------------------------------------------


def slope_frame(points) -> dict:
    """
    A sloped ceiling's own axes: `u` level along it, `v` up the slope, `n`
    out of the room; `origin` is where u and v are smallest, so the piece
    spans [0, lu] x [0, lv], and `angle` is its pitch.
    """
    p0, n_in = plane(points)
    n = [-c for c in n_in]
    horizontal = math.hypot(n[0], n[2]) or 1.0
    u = [-n[2] / horizontal, 0.0, n[0] / horizontal]
    v = [n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0]]
    if v[1] < 0:
        v, u = [-c for c in v], [-c for c in u]
    us = [sum((p[i] - p0[i]) * u[i] for i in range(3)) for p in points]
    vs = [sum((p[i] - p0[i]) * v[i] for i in range(3)) for p in points]
    origin = [p0[i] + u[i] * min(us) + v[i] * min(vs) for i in range(3)]
    return {
        "origin": origin, "u": u, "v": v, "n": n,
        "lu": max(us) - min(us), "lv": max(vs) - min(vs),
        "angle": math.asin(max(-1.0, min(1.0, v[1]))),
    }


def slope_point(frame: dict, u: float, v: float, n: float = 0.0) -> list[float]:
    o, eu, ev, en = frame["origin"], frame["u"], frame["v"], frame["n"]
    return [o[i] + eu[i] * u + ev[i] * v + en[i] * n for i in range(3)]


def roof_window(frame: dict, at, width: float, length: float, depth: float,
                bottom: float) -> dict:
    """
    A roof window in a slope, the way they are built: the frame sits in the
    roof's outer plane `depth` out, and its opening is lined with boards that
    run plumb below it and level above it, so the lining flares into the
    room. `at` is a plan point (x, z) the window is centred over, `bottom`
    how far up the slope the lining starts (0 at the eaves, where it
    continues the knee wall).

    Returns the hole in the slope (u0, u1, v0, v1), the sash's corners on
    the outer plane (u0, u1, v0, v1), and the four lining boards as 3D quads.
    """
    o, eu = frame["origin"], frame["u"]
    uc = (at[0] - o[0]) * eu[0] + (at[1] - o[2]) * eu[2]
    u0, u1 = uc - width / 2, uc + width / 2
    pitch = frame["angle"]
    v_hole0 = bottom
    v_frame0 = v_hole0 + depth * math.tan(pitch)  # plumb from the frame's foot
    v_frame1 = v_frame0 + length
    v_hole1 = v_frame1 + depth / math.tan(pitch)  # level from the frame's head

    def q(u, v, n):
        return slope_point(frame, u, v, n)

    lining = [
        [q(u0, v_hole0, 0), q(u0, v_hole1, 0), q(u0, v_frame1, depth), q(u0, v_frame0, depth)],
        [q(u1, v_hole0, 0), q(u1, v_hole1, 0), q(u1, v_frame1, depth), q(u1, v_frame0, depth)],
        [q(u0, v_hole0, 0), q(u1, v_hole0, 0), q(u1, v_frame0, depth), q(u0, v_frame0, depth)],
        [q(u0, v_hole1, 0), q(u1, v_hole1, 0), q(u1, v_frame1, depth), q(u0, v_frame1, depth)],
    ]
    return {
        "hole": (u0, u1, v_hole0, v_hole1),
        "sash": (u0, u1, v_frame0, v_frame1),
        "depth": depth,
        "lining": lining,
        "centre": q(uc, (v_frame0 + v_frame1) / 2, depth),
        "area": width * length,
    }


# --------------------------------------------------------------------------
# Sun
# --------------------------------------------------------------------------


def sun_position(year: int, month: int, day: int, hour: float, utc_offset: float,
                 latitude: float, longitude: float) -> tuple[float, float]:
    """
    The sun's compass bearing and elevation in degrees, from NOAA's general
    solar position equations (good to a fraction of a degree).
    `hour` is local clock time; `utc_offset` the local zone, e.g. 2 for CEST.
    """
    doy = (_days_before_month(year, month) + day)
    gamma = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    solar_minutes = hour * 60 + eqtime + 4 * longitude - 60 * utc_offset
    hour_angle = math.radians(solar_minutes / 4 - 180)
    lat = math.radians(latitude)
    cos_zenith = (math.sin(lat) * math.sin(decl)
                  + math.cos(lat) * math.cos(decl) * math.cos(hour_angle))
    zenith = math.acos(max(-1.0, min(1.0, cos_zenith)))
    azimuth = math.degrees(math.atan2(
        math.sin(hour_angle),
        math.cos(hour_angle) * math.sin(lat) - math.tan(decl) * math.cos(lat),
    )) + 180
    return azimuth % 360, 90 - math.degrees(zenith)


def _days_before_month(year: int, month: int) -> int:
    days = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return sum(days[: month - 1])


def compass_to_scene(bearing: float, known_bearing: float, known_direction) -> tuple[float, float]:
    """
    A compass bearing as a scene direction (x, z), given one direction whose
    bearing is known, e.g. the way the windows face. Seen from above, scene
    angles from -z towards +x run clockwise, as bearings do.
    """
    known = math.degrees(math.atan2(known_direction[0], -known_direction[1]))
    phi = math.radians(known + bearing - known_bearing)
    return math.sin(phi), -math.cos(phi)


def join_partitions(partitions: list[dict], thickness: float) -> list[dict]:
    """
    Partitions whose end butts into another, moved to end on its centre line.
    Two walls that meet at a T or an L otherwise share a face plane where
    they overlap, and coplanar faces render as black flicker; ending inside
    the other wall buries the end face instead.
    """
    out = [dict(p, start=list(p["start"]), end=list(p["end"])) for p in partitions]
    for i, p in enumerate(out):
        for key in ("start", "end"):
            for j, q in enumerate(partitions):
                if i == j:
                    continue
                (dx, dz), (nx, nz), length = wall_frame(q["start"], q["end"])
                rx, rz = p[key][0] - q["start"][0], p[key][1] - q["start"][1]
                along, off = rx * dx + rz * dz, rx * nx + rz * nz
                if -thickness <= along <= length + thickness and abs(off) <= thickness * 0.6:
                    p[key] = [p[key][0] - nx * off, p[key][1] - nz * off]
    return out


def convex_hull(points) -> list[list[float]]:
    """The convex hull of plan points, counter-clockwise (Andrew's monotone chain)."""
    pts = sorted({(round(p[0], 4), round(p[1], 4)) for p in points})
    if len(pts) < 3:
        return [list(p) for p in pts]

    def half(seq):
        out: list[tuple[float, float]] = []
        for p in seq:
            while len(out) >= 2 and _cross(out[-2], out[-1], p) <= 0:
                out.pop()
            out.append(p)
        return out

    lower, upper = half(pts), half(reversed(pts))
    return [list(p) for p in lower[:-1] + upper[:-1]]
