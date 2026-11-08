"""
`align` and `export`: from a trained splat in COLMAP's arbitrary frame to the
published scene — Y up, floor at y = 0, metres, rooms in the manifest, floor
split out, compressed.

Pure numpy over files, so the whole path is tested on synthetic scenes; the
CLI only wires it up.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from . import roomplan
from .align import FloorRejected, compose, find_floor, gravity_alignment, rotation_between
from .colmap import (
    Camera,
    find_model,
    gravity_from_cameras,
    read_images_bin,
    read_points3d_bin,
)
from .floor import simplify_polygon
from .manifest import Manifest, Obstacle, Room, Wall, Waypoint
from .paths import ProjectPaths
from .provenance import atomic_write_text, replace_dir
from .register import (
    ceiling_height,
    register_to_plan,
    solid,
    solid_segments_by_wall,
    wall_lean,
    wall_points,
)
from .splat import (
    SplatCloud,
    cap_splat_count,
    crop_to_box,
    read_ply,
    read_spz,
    write_ply,
    write_spz,
)

#: Typical height of a handheld phone across the capture guide's three
#: circles (eye, chest, knee). Only used to guess scale when nothing better
#: exists, and labelled as a guess when it is.
TYPICAL_CAMERA_HEIGHT_M = 1.3

#: Below this the splat and the scan disagree too much to trust the fit.
MIN_INLIER_RATIO = 0.25
MAX_MEDIAN_ERROR_M = 0.03


class AlignmentFailed(RuntimeError):
    pass


@dataclass
class AlignReport:
    source: str
    splats: int
    up_from: str  # "ARKit gravity (kept)" | "cameras"
    #: The floor's share of the area under the cameras (see align.find_floor).
    floor_inlier_ratio: float
    scale_from: str  # "roomplan" | "camera height (estimate)" | "none"
    scale: float
    roomplan: dict = field(default_factory=dict)
    floor: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def trained_ply(paths: ProjectPaths) -> Path:
    """The trainer's output, untouched; `align` never overwrites it."""
    return paths.work / "trained.ply"


def load_cameras(paths: ProjectPaths) -> list[Camera]:
    for sparse in (paths.work / "undistorted" / "sparse", paths.work / "sparse"):
        model = find_model(sparse) if sparse.exists() else None
        if model is not None:
            return read_images_bin(model / "images.bin")
    return []


def _transform_points(points: np.ndarray, m: np.ndarray) -> np.ndarray:
    return points @ m[:3, :3].T + m[:3, 3]


#: A video's floor plane may correct the cameras' up by at most this much;
#: the cameras' up is good to a few degrees, so more means a wrong plane.
MAX_VIDEO_FLOOR_TILT_DEG = 10.0
#: ARKit gravity is kept; a floor leaning more than this from it is reported.
WARN_FLOOR_TILT_DEG = 2.0
#: Walls leaning more than this (scan check) mean the scene is not level.
WARN_WALL_LEAN_DEG = 1.5
MAX_WALL_LEAN_DEG = 5.0
#: Below this share of the plan's solid wall length seen, the fit is refused.
MIN_WALL_COVERAGE = 0.2
#: An ARKit capture and its RoomPlan scan are both metric; a free fit that
#: lands further than this from the rigid one means they disagree on size.
SCALE_DISAGREEMENT = 0.03


def aligned_dir(paths: ProjectPaths) -> Path:
    """`align`'s output — the levelled splat and the draft manifest — until `export` publishes."""
    return paths.work / "aligned"


def align_scene(
    paths: ProjectPaths, project_id: str, metric: bool = False, scale_tolerance: float = 0.0
) -> AlignReport:
    """
    Level the splat, put its floor at y = 0, and fit yaw, position (and, for
    a video, scale) to the RoomPlan scan when there is one. Writes
    work/aligned/{scene.ply, manifest.json}; the published scene is left
    alone until `export` has built and checked its replacement. Raises
    rather than writing a scene it does not trust.

    `metric`: the splat is in metres with Y up along gravity — an ARKit
    capture. Gravity is kept (the floor only gives its height) and the plan
    fit is rigid unless `scale_tolerance` (e.g. 0.01) allows a bounded
    scale refinement.
    """
    source = trained_ply(paths)
    if not source.is_file():
        raise AlignmentFailed(f"No trained scene at {source}. Run `pipeline train` first.")
    if not 0.0 <= scale_tolerance <= 0.05:
        raise AlignmentFailed("scale_tolerance must be between 0 and 0.05")
    cloud = read_ply(source)
    cameras = load_cameras(paths)
    if not cameras:
        raise AlignmentFailed("No camera poses: up is unknown. Run `pipeline poses` first.")
    warnings: list[str] = []
    centres = np.array([c.centre for c in cameras])

    # 1. Up. ARKit measured gravity; a video's cameras say how the phone was
    #    held. A plane fit alone cannot tell a floor from a wall.
    if metric:
        up, up_from = np.array([0.0, 1.0, 0.0]), "ARKit gravity (kept)"
    else:
        up, up_from = gravity_from_cameras(cameras), "cameras"
    level = np.eye(4)
    level[:3, :3] = rotation_between(up, np.array([0.0, 1.0, 0.0]))
    pts = _transform_points(cloud.positions.astype(np.float64), level)
    cam_y = _transform_points(centres, level)[:, 1]
    opaque = solid(cloud.opacities)

    # 2. Floor.
    floor_report: dict = {}
    try:
        if metric:
            lidar = _lidar_points(paths)
            measured = lidar is not None and len(lidar) > 1000
            floor_pts = _transform_points(lidar, level) if measured else pts[opaque]
            floor = find_floor(floor_pts, cam_y, threshold=0.02, cell=0.1, keep_gravity=True,
                               min_area=1.0, clearance_range=(0.4, 2.2))
            floor_report["from"] = "LiDAR" if measured else "splats"
            if measured:  # does the splat agree with what LiDAR measured?
                try:
                    splat_floor = find_floor(pts[opaque], cam_y, threshold=0.02, cell=0.1,
                                             keep_gravity=True, min_area=1.0,
                                             clearance_range=(0.4, 2.2))
                    gap = splat_floor.height - floor.height
                    floor_report["splat_minus_lidar_m"] = round(gap, 4)
                    if abs(gap) <= 0.03:
                        # The viewer shows the splats: furniture and the
                        # walk stand on the floor as seen, LiDAR vouching
                        # for it (the first real room: 1.35 cm apart).
                        floor_report["from"] = "splats, within 3 cm of LiDAR"
                        floor_report["lidar_height"] = round(floor.height, 4)
                        splat_floor.rejected = floor.rejected + splat_floor.rejected
                        floor = splat_floor
                    else:
                        warnings.append(f"The splat's floor sits {gap * 100:+.1f} cm from the "
                                        "LiDAR floor; the LiDAR floor was used.")
                except FloorRejected as exc:
                    floor_report["splat_floor"] = f"not found: {exc}"
            if floor.tilt_deg > WARN_FLOOR_TILT_DEG:
                warnings.append(f"The floor's own points lean {floor.tilt_deg:.1f}° from ARKit's "
                                "gravity; gravity was kept. Check the room is level in the viewer.")
            transform = level.copy()
            transform[1, 3] -= floor.height
        else:
            # Thresholds relative to the scene's size: a video has no metres yet.
            extent = float(np.percentile(pts[:, 1], 95) - np.percentile(pts[:, 1], 5))
            floor = find_floor(pts[opaque], cam_y, threshold=max(extent * 0.008, 1e-6),
                               cell=max(extent * 0.04, 1e-6), keep_gravity=False,
                               max_tilt_deg=MAX_VIDEO_FLOOR_TILT_DEG)
            floor_report["from"] = "splats"
            if floor.tilt_deg > 3.0:
                warnings.append(f"The floor plane corrected the cameras' up by "
                                f"{floor.tilt_deg:.1f}°.")
            plane = (*floor.normal, floor.plane_d)
            transform = compose(level, gravity_alignment(plane, _transform_points(centres, level)))
    except FloorRejected as exc:
        raise AlignmentFailed(f"Could not find the floor. {exc}") from exc
    floor_report.update(
        tilt_deg=round(floor.tilt_deg, 2), inliers=floor.inliers, area=round(floor.area, 3),
        coverage=round(floor.coverage, 3),
        camera_clearance={k: round(v, 3) for k, v in floor.clearance.items()},
        rejected=floor.rejected,
    )

    aligned = _transform_points(cloud.positions.astype(np.float64), transform)
    heights = aligned[:, 1]
    cam_heights = _transform_points(centres, transform)[:, 1]

    # 3. Scale, yaw and position.
    manifest = Manifest.load(paths.manifest) if paths.manifest.is_file() else Manifest(
        id=project_id, name=project_id
    )
    scan_file = roomplan.find_export(paths.input / "roomplan")
    rp_report: dict = {}
    if scan_file is not None:
        scan = roomplan.load(scan_file)
        room, items, _ = roomplan.to_rooms(scan, room_id="room", room_name="Room")
        wall_height = room.walls[0].height
        ceiling = ceiling_height(heights[opaque])
        fixed = False
        if metric:
            # ARKit and RoomPlan both measure in metres: keep the splat's
            # scale unless a small, bounded refinement was asked for.
            guess, spread = 1.0, (1 - scale_tolerance, 1 + scale_tolerance)
            fixed = scale_tolerance == 0.0
        elif ceiling is not None:
            guess, spread = wall_height / ceiling, (0.85, 1.15)
        else:
            guess, spread = TYPICAL_CAMERA_HEIGHT_M / float(np.median(cam_heights)), (0.6, 1.6)
            warnings.append("No clear ceiling in the splat; scale search began at camera height.")

        points = wall_points(aligned, cloud.opacities, ceiling or wall_height / guess)
        seg_a, seg_b, seg_wall = solid_segments_by_wall(room)
        plan = register_to_plan(points, (seg_a, seg_b), guess, spread, fixed_scale=fixed,
                                wall_of_segment=seg_wall)
        rp_report = {
            "file": scan_file.name,
            "walls": len(scan.of("walls")),
            "doors": len(scan.of("doors")) + len(scan.of("openings")),
            "windows": len(scan.of("windows")),
            "objects": len(scan.of("objects")),
            "scale": round(plan.scale, 5),
            "scale_fixed": plan.scale_fixed,
            "scale_bounds": [round(b, 5) for b in plan.scale_bounds],
            "inlier_ratio": round(plan.inlier_ratio, 3),
            "median_error_mm": round(plan.median_error_m * 1000, 1),
            "rms_error_mm": round(plan.rms_error_m * 1000, 1),
            "p90_error_mm": round(plan.p90_error_m * 1000, 1),
            "wall_coverage": round(plan.coverage, 3),
            "symmetry_ratio": round(plan.symmetry_ratio, 3),
            "per_wall": plan.walls,
        }
        if (plan.inlier_ratio < MIN_INLIER_RATIO or plan.median_error_m > MAX_MEDIAN_ERROR_M
                or plan.coverage < MIN_WALL_COVERAGE):
            raise AlignmentFailed(
                f"The splat does not fit the RoomPlan walls at scale {plan.scale:.3f} "
                f"(held to {plan.scale_bounds[0]:.3f}-{plan.scale_bounds[1]:.3f}): "
                f"{plan.inlier_ratio:.0%} of wall points within 5 cm, median "
                f"{plan.median_error_m * 1000:.0f} mm, {plan.coverage:.0%} of the plan's wall "
                "length seen. Is the scan of the same room? Nothing was written."
            )
        if metric:
            # A rigid fit at the wrong size can still lay one side of the room
            # on its walls. A free fit says whether the scan and the capture
            # agree on size at all; if not, neither is to be trusted.
            probe = register_to_plan(points, (seg_a, seg_b), 1.0, (0.8, 1.25))
            rp_report["free_scale_probe"] = {
                "scale": round(probe.scale, 4), "inlier_ratio": round(probe.inlier_ratio, 3)}
            if abs(probe.scale - plan.scale) > SCALE_DISAGREEMENT and \
                    probe.inlier_ratio > plan.inlier_ratio + 0.1:
                raise AlignmentFailed(
                    f"The RoomPlan scan and the ARKit capture disagree on size: the walls fit "
                    f"best at scale {probe.scale:.3f} ({probe.inlier_ratio:.0%} of wall points "
                    f"on the walls) against {plan.scale:.3f} ({plan.inlier_ratio:.0%}). Both "
                    "should be metres; is the scan of this room? Nothing was written."
                )
        if plan.symmetry_ratio > 0.95:
            warnings.append(
                "The room is nearly symmetric and its doors and windows barely told the two "
                "orientations apart. Check in the viewer that the plan sits the right way round."
            )
        if plan.coverage < 0.5:
            warnings.append(f"Only {plan.coverage:.0%} of the plan's solid wall length was seen "
                            "by the capture; the fit rests on the rest.")
        transform = compose(transform, plan.matrix())
        # Gravity checked against the scan: plan walls stand vertical.
        in_plan = _transform_points(cloud.positions[opaque].astype(np.float64), transform)
        leans = wall_lean(in_plan, (seg_a, seg_b), seg_wall, wall_height)
        rp_report["wall_lean"] = leans
        worst = max((abs(w["lean_deg"]) for w in leans), default=0.0)
        if worst > MAX_WALL_LEAN_DEG:
            raise AlignmentFailed(
                f"The splat's walls lean up to {worst:.1f}° against the scan's: the scene is not "
                f"level (up from {up_from}). Nothing was written."
            )
        if worst > WARN_WALL_LEAN_DEG:
            warnings.append(f"Walls lean up to {worst:.1f}° against the scan.")
        scale, scale_from = plan.scale, "roomplan (rigid, ARKit metres)" if fixed else "roomplan"
        manifest.rooms = [room]
        manifest.items = items
    else:
        if metric:
            scale, scale_from = 1.0, "ARKit (metric)"
            warnings.append(
                "No RoomPlan scan: walls and doors are not known, so the room is outlined "
                "from the floor and has no doorways."
            )
        else:
            scale = TYPICAL_CAMERA_HEIGHT_M / float(np.median(cam_heights))
            scale_from = "camera height (estimate)"
            warnings.append(
                "No RoomPlan scan: scale is estimated from a typical phone height and may be "
                "off by 10-20%. Room walls and doors are not known, so the room is outlined "
                "from the floor and has no doorways."
            )
        transform = compose(transform, np.diag([scale, scale, scale, 1.0]))
        lidar = _lidar_points(paths) if metric else None
        if lidar is not None:
            # Measured surfaces, not trained splats: training lets a few splats
            # drift metres away, and an outline from splats picks them up.
            plan_pts = _transform_points(lidar, transform)
            outline_xz = plan_pts[(plan_pts[:, 1] > -0.05) & (plan_pts[:, 1] < 2.2)][:, [0, 2]]
            ceiling = ceiling_height(plan_pts[:, 1])
        else:
            near = opaque & (np.abs(heights * scale) < 0.02)
            outline_xz = (aligned[near] * scale)[:, [0, 2]]
            ceiling = ceiling_height(heights[opaque] * scale)
        room = _room_from_floor(outline_xz, measured=lidar is not None)
        # The walls stand as high as the ceiling measured: export crops at
        # the walls' height, and a guessed 2.6 m cut the 3.3 m ceiling off
        # the first real Stray room.
        if ceiling is not None and 2.0 < ceiling < 6.0:
            for wall in room.walls:
                wall.height = round(ceiling, 3)
        else:
            warnings.append("No clear ceiling: walls left at 2.6 m, and export crops 0.5 m "
                            "above that. Check the ceiling in the viewer.")
        floor_report["ceiling_m"] = None if ceiling is None else round(ceiling, 3)
        manifest.rooms = [room]
        manifest.items = []

    _stand_where_filmed(manifest.rooms[0], cameras, transform)
    if scan_file is None and metric:
        lidar = _lidar_points(paths)
        if lidar is not None:
            room = manifest.rooms[0]
            room.obstacles = lidar_obstacles(_transform_points(lidar, transform), room)
            floor_report["obstacles"] = len(room.obstacles)

    manifest.transform = transform.tolist()
    manifest.floor = {"plane": [0.0, 1.0, 0.0, 0.0]}
    report = AlignReport(
        source=str(source.name),
        splats=len(cloud),
        up_from=up_from,
        floor_inlier_ratio=round(floor.coverage, 3),
        scale_from=scale_from,
        scale=round(scale, 5),
        roomplan=rp_report,
        floor=floor_report,
        warnings=warnings,
    )
    with replace_dir(aligned_dir(paths)) as build:
        write_ply(cloud.transformed(transform), build / "scene.ply")
        atomic_write_text(build / "manifest.json", manifest.to_json())
        atomic_write_text(build / "align_report.json", json.dumps(asdict(report), indent=2))
    atomic_write_text(paths.work / "align_report.json", json.dumps(asdict(report), indent=2))
    return report


def floor_outline(
    floor_xz: np.ndarray, cell: float = 0.1, measured: bool = False
) -> np.ndarray:
    """
    The room's outline from points near the floor: the largest connected
    region of occupied 10 cm cells. `measured` points are LiDAR (floor and
    walls up to 2.2 m); otherwise they are trained splats at floor height.

    A hull over the points themselves is ruined by strays — a real capture
    has floor-height splats metres outside the room, seen through a doorway
    or floating in unfilmed space, and the first one tried outlined 740 m²
    for a single room. Density and connectivity ignore them.
    """
    lo = np.percentile(floor_xz, 0.5, axis=0) - cell
    idx = np.floor((floor_xz - lo) / cell).astype(int)
    keep = np.all((idx >= 0) & (idx < 2000), axis=1)  # 200 m: a sanity bound
    idx = idx[keep]
    counts = np.zeros(idx.max(axis=0) + 1, dtype=np.int32)
    np.add.at(counts, (idx[:, 0], idx[:, 1]), 1)
    # Measured points (LiDAR) are sparse but trustworthy: two in a cell is
    # surface. Trained splats need a density bar to ignore the strays.
    occupied = counts >= (2 if measured else max(3, np.percentile(counts[counts > 0], 25)))
    # LiDAR mostly sees walls, so its cells form a ring whose outer contour is
    # the room — once the ring is closed. Bridge gaps up to ~1 m (a doorway,
    # a wall hidden behind a wardrobe); splat floors only need small holes shut.
    size = 11 if measured else 5
    pad = size if measured else 0  # room for the wide close to reach the edges
    grid = np.pad(occupied.astype(np.uint8), pad)
    grid = cv2.morphologyEx(grid, cv2.MORPH_CLOSE, np.ones((size, size), np.uint8))
    lo = lo - pad * cell
    n, labels, stats, _ = cv2.connectedComponentsWithStats(grid, connectivity=4)
    if n < 2:
        raise AlignmentFailed("Too little floor in the splat to outline the room.")
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask = (labels == largest).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float64)
    # Contour points are (column, row) = (z cell, x cell) at cell corners.
    poly = np.column_stack([contour[:, 1], contour[:, 0]]) * cell + lo
    return simplify_polygon(poly, tolerance=cell * 1.5)


def _lidar_points(paths: ProjectPaths) -> np.ndarray | None:
    """The fused LiDAR points a posed capture wrote as the trainer's seed points."""
    model = paths.work / "undistorted" / "sparse" / "points3D.bin"
    return read_points3d_bin(model) if model.is_file() else None


def _room_from_floor(floor_xz: np.ndarray, measured: bool = False) -> Room:
    """A room outlined from floor points, for a capture without a scan."""
    if len(floor_xz) < 50:
        raise AlignmentFailed("Too little floor in the splat to outline the room.")
    poly = floor_outline(floor_xz, measured=measured)
    x, z = poly[:, 0], poly[:, 1]
    if 0.5 * float(np.sum(x * np.roll(z, -1) - np.roll(x, -1) * z)) < 0:
        poly = poly[::-1]
    corners = [(round(float(a), 4), round(float(b), 4)) for a, b in poly]
    centre = poly.mean(axis=0)
    return Room(
        id="room",
        name="Room",
        type="living",
        waypoint=Waypoint(position=(round(float(centre[0]), 3), 1.6, round(float(centre[1]), 3))),
        floorPolygon=corners,
        walls=[Wall(start=corners[i], end=corners[(i + 1) % len(corners)])
               for i in range(len(corners))],
    )


#: What a person cannot walk through: LiDAR surfaces between these heights.
#: Below, rugs, shoes and skirting; above, shelves a person walks under.
OBSTACLE_BAND_M = (0.3, 1.8)


def lidar_obstacles(points: np.ndarray, room: Room, cell: float = 0.1,
                    min_points: int = 6) -> list[Obstacle]:
    """
    Furniture standing in a room without a scan, as footprints the walk keeps
    out of (Room.obstacles, `canStand`): 10 cm cells with LiDAR surface
    between knee and head height, away from the walls (the walls block on
    their own), merged into outlines. Without them the camera walked into
    the first real room's wardrobe. The cell the camera starts in is never
    blocked: the person stood there.
    """
    poly = np.array(room.floorPolygon, dtype=np.float64)
    band = points[(points[:, 1] > OBSTACLE_BAND_M[0]) & (points[:, 1] < OBSTACLE_BAND_M[1])]
    xz = band[:, [0, 2]]
    xz = xz[points_in_polygon(xz, poly)]
    if len(xz) == 0:
        return []
    # A margin round the room: erosion treats the image's border as solid.
    lo = poly.min(axis=0) - 4 * cell
    size = np.ceil((poly.max(axis=0) + 4 * cell - lo) / cell).astype(int)
    idx = np.floor((xz - lo) / cell).astype(int)
    counts = np.zeros((size[1], size[0]), np.int32)
    np.add.at(counts, (idx[:, 1], idx[:, 0]), 1)
    blocked = (counts >= min_points).astype(np.uint8)
    # Not the walls: a band along the outline is theirs.
    outline = np.round((poly - lo) / cell).astype(np.int32)
    inner = np.zeros_like(blocked)
    cv2.fillPoly(inner, [outline], 1)
    inner = cv2.erode(inner, np.ones((5, 5), np.uint8))  # 0.2 m in from the walls
    blocked &= inner
    # Close small gaps (an even-sized opening would shift it all a cell;
    # specks go by area below).
    blocked = cv2.morphologyEx(blocked, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    sx, sz = np.floor((np.array(room.waypoint.position)[[0, 2]] - lo) / cell).astype(int)
    blocked[max(sz - 3, 0): sz + 4, max(sx - 3, 0): sx + 4] = 0
    contours, _ = cv2.findContours(blocked, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for contour in contours:
        if cv2.contourArea(contour) < 3:  # under 0.03 m²: a speck
            continue
        c = cv2.approxPolyDP(contour, 0.6, True).reshape(-1, 2).astype(np.float64)
        if len(c) < 3:
            continue
        # Boundary cells' centres back to metres (half a cell inside the
        # surface; the walk keeps a body's radius from it besides).
        footprint = (c + 0.5) * cell + lo
        out.append(Obstacle(id=f"lidar-{len(out)}",
                            footprint=[(round(float(x), 3), round(float(z), 3))
                                       for x, z in footprint]))
    return out


def _stand_where_filmed(room: Room, cameras: list[Camera], transform: np.ndarray) -> None:
    """
    Put the room's starting point where the camera actually was.

    The splat is sharpest from the viewpoints it was trained on. The capture
    guide has the person film a circle at each room's viewpoint, so the
    median camera position is that viewpoint; a polygon's centroid can be
    anywhere, including in front of a sofa nobody filmed from.
    """
    centres = _transform_points(np.array([c.centre for c in cameras]), transform)[:, [0, 2]]
    spot = np.median(centres, axis=0)
    poly = np.array(room.floorPolygon)
    if not points_in_polygon(spot[None], poly)[0]:
        return
    yaw = room.waypoint.yaw
    # Face the way the camera mostly faced: that side was filmed. A full
    # outward circle averages out to nothing, and then any yaw is as good.
    rotation = transform[:3, :3] / np.cbrt(np.linalg.det(transform[:3, :3]))
    forward = (np.array([c.rotation[2] for c in cameras]) @ rotation.T)[:, [0, 2]].mean(axis=0)
    if np.linalg.norm(forward) > 0.3:
        # yaw 0 looks down -Z (lib/camera.ts).
        yaw = round(float(np.degrees(np.arctan2(-forward[0], -forward[1]))) % 360, 1)
    x, z = round(float(spot[0]), 3), round(float(spot[1]), 3)
    room.waypoint = Waypoint(position=(x, room.waypoint.position[1], z), yaw=yaw)


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------


@dataclass
class ExportReport:
    splats: int
    cropped_out: int
    floor_splats: int
    sh_degree: int
    files_mb: dict[str, float]
    download_mb: float
    #: Splats per room's floor asset, and what the privacy masks did.
    floors: dict[str, int] = field(default_factory=dict)
    privacy: list[dict] = field(default_factory=list)
    #: Each published SPZ read back and compared with what was written.
    validation: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


#: A floor splat is within this of y = 0. Wide enough for real floor
#: thickness and rugs, narrow enough to leave skirting and furniture feet.
FLOOR_TOLERANCE_M = 0.035


def points_in_polygon(xz: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Vectorised even-odd test. Mirrors point_in_polygon in layout.py."""
    x, z = xz[:, 0], xz[:, 1]
    inside = np.zeros(len(xz), dtype=bool)
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, zi = poly[i]
        xj, zj = poly[j]
        crosses = (zi > z) != (zj > z)
        with np.errstate(divide="ignore", invalid="ignore"):
            x_at = (xj - xi) * (z - zi) / (zj - zi) + xi
        inside ^= crosses & (x < x_at)
        j = i
    return inside


def _grow(poly: np.ndarray, margin: float) -> np.ndarray:
    """Push each vertex away from the centroid — enough for convex-ish rooms."""
    centre = poly.mean(axis=0)
    d = poly - centre
    n = np.linalg.norm(d, axis=1, keepdims=True)
    return poly + d / np.maximum(n, 1e-9) * margin


def drop_sh(cloud: SplatCloud, degree: int) -> SplatCloud:
    if cloud.sh_rest is None or degree >= cloud.sh_degree:
        return cloud
    k = {0: 0, 1: 3, 2: 8}[degree]
    return SplatCloud(
        cloud.positions, cloud.colours, cloud.opacities, cloud.scales, cloud.rotations,
        sh_rest=cloud.sh_rest[:, :k] if k else None,
    )


#: A floor splat lies flat: its thinnest axis within this of vertical.
#: Skirting boards and furniture legs stand upright and stay in the scene.
FLOOR_NORMAL_MAX_DEG = 35.0
#: Floor splats this far above the floor plane, in number, suggest a rug.
RUG_BAND_M = (0.008, FLOOR_TOLERANCE_M)


def splat_normals(cloud: SplatCloud) -> np.ndarray:
    """Each splat's thinnest axis (its surface normal), unit, (N, 3)."""
    q = cloud.rotations.astype(np.float64)
    q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    w, x, y, z = q.T
    axis = np.argmin(cloud.scales, axis=1)
    cols = np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y)], axis=1),
        np.stack([2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)], axis=1),
        np.stack([2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)], axis=1),
    ], axis=1)  # (N, 3 axes, 3): rotation matrix columns
    return cols[np.arange(len(q)), axis]


def floor_splats(cloud: SplatCloud, polys: list[np.ndarray]) -> list[np.ndarray]:
    """
    Per room, which splats are its floor: centred within FLOOR_TOLERANCE_M
    of y = 0, inside the room (grown 5 cm to reach under the skirting), and
    not standing upright: a splat with a clear thin axis (a disc, under half
    as thick as it is wide) must have it within FLOOR_NORMAL_MAX_DEG of
    vertical. Round splats say nothing about orientation and go by position.
    A splat belongs to the first room that claims it.
    """
    near = np.abs(cloud.positions[:, 1]) < FLOOR_TOLERANCE_M
    idx = np.nonzero(near)[0]
    candidates = cloud.select(near)
    ordered = np.sort(candidates.scales, axis=1)
    disc = ordered[:, 0] < 0.5 * ordered[:, 1]
    level = np.abs(splat_normals(candidates)[:, 1]) >= np.cos(np.radians(FLOOR_NORMAL_MAX_DEG))
    idx = idx[~disc | level]
    claimed = np.zeros(len(cloud), dtype=bool)
    out = []
    for poly in polys:
        inside = np.zeros(len(cloud), dtype=bool)
        inside[idx] = points_in_polygon(cloud.positions[idx][:, [0, 2]], _grow(poly, 0.05))
        inside &= ~claimed
        claimed |= inside
        out.append(inside)
    return out


def _box_local(positions: np.ndarray, box) -> np.ndarray:
    """Positions in a manifest box's frame: centred, turned by -yaw about Y."""
    c, s = np.cos(np.radians(box.yaw)), np.sin(np.radians(box.yaw))
    d = positions.astype(np.float64) - np.array(box.center)
    # yaw turns the box about +Y (three.js); undo it.
    return np.column_stack([c * d[:, 0] - s * d[:, 2], d[:, 1], s * d[:, 0] + c * d[:, 2]])


#: Blurred regions are averaged over cells this size: no text, face or
#: document survives it, and the room's colours still read.
PRIVACY_BLUR_CELL_M = 0.08


def apply_privacy_masks(cloud: SplatCloud, masks: list) -> tuple[SplatCloud, list[dict]]:
    """
    Apply the editor's privacy masks to the splats themselves, so no
    published file holds what they hide. `delete` drops every splat that
    reaches into the box (its centre within the box grown by the splat's own
    3-sigma extent, up to 15 cm). `blur` keeps the geometry but averages
    colour over PRIVACY_BLUR_CELL_M cells and drops the view-dependent
    colour, which could otherwise carry detail.
    """
    report = []
    keep = np.ones(len(cloud), dtype=bool)
    colours = cloud.colours.copy()
    sh_rest = None if cloud.sh_rest is None else cloud.sh_rest.copy()
    reach = np.minimum(3 * cloud.scales.max(axis=1), 0.15)
    for mask in masks:
        half = np.array(mask.box.size) / 2
        local = _box_local(cloud.positions, mask.box)
        if mask.mode == "delete":
            hit = np.all(np.abs(local) <= half + reach[:, None], axis=1)
            keep &= ~hit
        else:
            hit = np.all(np.abs(local) <= half, axis=1)
            idx = np.nonzero(hit)[0]
            if len(idx):
                cells = np.floor(local[idx] / PRIVACY_BLUR_CELL_M).astype(np.int64)
                _, group = np.unique(cells, axis=0, return_inverse=True)
                group = group.ravel()
                for k in range(3):
                    mean = np.bincount(group, colours[idx, k]) / np.bincount(group)
                    colours[idx, k] = mean[group]
                if sh_rest is not None:
                    sh_rest[idx] = 0.0
        report.append({"id": mask.id, "mode": mask.mode, "splats": int(hit.sum())})
    masked = SplatCloud(cloud.positions, colours, cloud.opacities, cloud.scales,
                        cloud.rotations, sh_rest=sh_rest)
    return masked.select(keep), report


#: Floor shading maps: one texel per this many metres, at most this many a side.
SHADING_CELL_M = 0.04
SHADING_MAX_PX = 512


def floor_shading(cloud: SplatCloud, poly: np.ndarray) -> np.ndarray | None:
    """
    How light falls on a room's floor, from its floor splats: relative
    luminance over the polygon's bounding box (row 0 at its min z, column 0
    at its min x), 1.0 at the median, blurred ~10 cm so the old floor's own
    pattern (planks, tiles) does not print onto a new one. 8-bit, value/127.5
    is the multiplier. None with too few splats to say.
    """
    if len(cloud) < 500:
        return None
    lo, hi = poly.min(axis=0), poly.max(axis=0)
    size = np.maximum(np.ceil((hi - lo) / SHADING_CELL_M).astype(int), 2)
    size = np.minimum(size, SHADING_MAX_PX)
    xz = cloud.positions[:, [0, 2]].astype(np.float64)
    col = np.clip(((xz[:, 0] - lo[0]) / (hi[0] - lo[0]) * size[0]).astype(int), 0, size[0] - 1)
    row = np.clip(((xz[:, 1] - lo[1]) / (hi[1] - lo[1]) * size[1]).astype(int), 0, size[1] - 1)
    srgb = np.clip(cloud.colours.astype(np.float64), 0, 1)
    linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    lum = linear @ np.array([0.2126, 0.7152, 0.0722])
    weight = cloud.opacities.astype(np.float64)
    total = np.zeros((size[1], size[0]))
    wsum = np.zeros((size[1], size[0]))
    np.add.at(total, (row, col), lum * weight)
    np.add.at(wsum, (row, col), weight)
    sigma = 0.10 / SHADING_CELL_M
    # Normalised convolution: blur, and fill cells nothing landed in.
    blur_t = cv2.GaussianBlur(total, (0, 0), sigma)
    blur_w = cv2.GaussianBlur(wsum, (0, 0), sigma)
    filled = blur_w > 1e-6
    if not filled.any():
        return None
    value = np.where(filled, blur_t / np.maximum(blur_w, 1e-12), 0.0)
    if not filled.all():  # beyond the splats' reach: the nearest known value
        _, (iy, ix) = _nearest_filled(filled)
        value = value[iy, ix]
    shading = value / max(float(np.median(value[filled])), 1e-6)
    return np.clip(np.round(np.clip(shading, 0.25, 1.75) * 127.5), 0, 255).astype(np.uint8)


def _nearest_filled(filled: np.ndarray) -> tuple[np.ndarray, tuple[np.ndarray, np.ndarray]]:
    from scipy import ndimage

    return ndimage.distance_transform_edt(~filled, return_indices=True)


def _keep_editor_fields(draft: Manifest, published: Manifest) -> None:
    """
    What the editor decided stays decided across a re-export: the name,
    listing, opening view and privacy masks, and each room's name and type
    where the room is still there.
    """
    draft.name, draft.listing, draft.heroView = published.name, published.listing, \
        published.heroView
    draft.privacyMasks = published.privacyMasks
    edited = {r.id: r for r in published.rooms}
    for room in draft.rooms:
        if room.id in edited:
            room.name, room.type = edited[room.id].name, edited[room.id].type


def _check_spz(path: Path, expected: SplatCloud) -> dict:
    """Read a written SPZ back; it must hold the same splats where they were."""
    back = read_spz(path)
    if len(back) != len(expected):
        raise AlignmentFailed(f"{path.name} holds {len(back)} splats, not {len(expected)}")
    if len(back) == 0:
        return {"splats": 0}
    err = np.abs(back.positions.astype(np.float64) - expected.positions).max()
    # 12 fractional bits: half a step, plus float32 slack.
    if err > 0.5 / (1 << 12) + 1e-4:
        raise AlignmentFailed(f"{path.name}: positions moved {err * 1000:.2f} mm on the way out")
    if back.sh_degree != expected.sh_degree:
        raise AlignmentFailed(f"{path.name}: SH degree {back.sh_degree}, not {expected.sh_degree}")
    return {"splats": len(back), "max_position_error_mm": round(float(err) * 1000, 3),
            "bounds": [np.round(b, 3).tolist() for b in back.bounds()]}


def export_scene(
    paths: ProjectPaths,
    max_splats: int = 1_500_000,
    crop_margin: float | None = 0.75,
    sh_degree: int = 3,
) -> ExportReport:
    """
    Crop to the rooms, cap the count, apply the privacy masks, split each
    room's floor out and write SPZ — then read every file back, and only
    when all of it is there and checks out, move it into scene/ and publish
    the manifest that points at it. Until then the viewer keeps showing the
    last export.

    Cropping drops what the camera saw beyond the walls — the view through
    windows and doors and the floaters that come with it. `crop_margin=None`
    keeps everything.
    """
    draft_dir = aligned_dir(paths)
    src = draft_dir / "scene.ply"
    if not src.is_file() or not (draft_dir / "manifest.json").is_file():
        raise AlignmentFailed("No aligned scene. Run `pipeline align` first.")
    manifest = Manifest.load(draft_dir / "manifest.json")
    if not manifest.rooms:
        raise AlignmentFailed("The aligned scene has no rooms. Run `pipeline align` again.")
    if paths.manifest.is_file():
        _keep_editor_fields(manifest, Manifest.load(paths.manifest))
    cloud = drop_sh(read_ply(src), sh_degree)
    total = len(cloud)
    warnings: list[str] = []

    polys = [np.array(r.floorPolygon, dtype=np.float64) for r in manifest.rooms]
    if crop_margin is not None:
        allpts = np.vstack(polys)
        height = max(w.height for r in manifest.rooms for w in r.walls) if any(
            r.walls for r in manifest.rooms
        ) else 3.0
        lo = np.array([allpts[:, 0].min() - crop_margin, -0.5, allpts[:, 1].min() - crop_margin])
        hi = np.array([allpts[:, 0].max() + crop_margin, height + 0.5,
                       allpts[:, 1].max() + crop_margin])
        cloud = crop_to_box(cloud, lo, hi)
    cropped = total - len(cloud)
    cloud = cap_splat_count(cloud, max_splats)
    # Masks after cropping and capping: what is published is what is masked.
    cloud, privacy = apply_privacy_masks(cloud, manifest.privacyMasks)

    per_room = floor_splats(cloud, polys)
    on_floor = np.any(per_room, axis=0) if per_room else np.zeros(len(cloud), bool)
    parts: dict[str, SplatCloud] = {"scene.spz": cloud,
                                    "scene_nofloor.spz": cloud.select(~on_floor)}
    shading: dict[str, np.ndarray] = {}
    floors: dict[str, str] = {}
    for room, poly, mine in zip(manifest.rooms, polys, per_room, strict=True):
        part = cloud.select(mine)
        if len(part) < 1000:
            warnings.append(f"{room.name}: only {len(part)} floor splats; a new floor there "
                            "will look patchy.")
        if len(part) == 0:
            continue
        rgb = np.clip(part.colours.mean(axis=0), 0, 1)
        room.originalFloorColor = "#{:02x}{:02x}{:02x}".format(*np.round(rgb * 255).astype(int))
        # A rug is a second layer just above the floor (y = 0, the lowest
        # broad layer, so under any rug): the layer goes lopsided. A trained
        # floor is a centimetre or two thick either way (the first real
        # room: 32% above, 36% below), which is not a rug.
        y = part.positions[:, 1]
        raised = float(((y > RUG_BAND_M[0]) & (y < RUG_BAND_M[1])).mean())
        lowered = float(((y < -RUG_BAND_M[0]) & (y > -RUG_BAND_M[1])).mean())
        if raised - lowered > 0.15:
            warnings.append(f"{room.name}: {raised:.0%} of the floor splats sit 1-3 cm above the "
                            f"floor's level, {lowered:.0%} below it: a rug or an uneven floor. "
                            "Check it before replacing that floor.")
        floors[room.id] = f"floor-{room.id}.spz"
        parts[floors[room.id]] = part
        map_ = floor_shading(part, poly)
        if map_ is not None:
            shading[room.id] = map_

    files: dict[str, float] = {}
    validation: dict[str, dict] = {}
    staging = paths.scene / f".export-{uuid4().hex[:8]}"
    staging.mkdir(parents=True)
    try:
        for name, part in parts.items():
            write_spz(part, staging / name)
            validation[name] = _check_spz(staging / name, part)
            files[name] = round((staging / name).stat().st_size / 1e6, 2)
        shading_files = {}
        for room_id, image in shading.items():
            name = f"floor-shading-{room_id}.png"
            if not cv2.imwrite(str(staging / name), image) or cv2.imread(
                    str(staging / name), cv2.IMREAD_UNCHANGED).shape != image.shape:
                raise AlignmentFailed(f"Could not write {name}")
            shading_files[room_id] = name
        # Everything is written and read back: publish. Files first, then
        # the manifest that points at them, then what nothing points at.
        for f in staging.iterdir():
            os.replace(f, paths.scene / f.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    manifest.assets.scene = "scene.spz"
    manifest.assets.sceneNoFloor = "scene_nofloor.spz"
    manifest.assets.floor = None
    manifest.assets.floors = floors
    manifest.assets.floorShading = shading_files
    atomic_write_text(paths.manifest, manifest.to_json())
    wanted = {"scene.spz", "scene_nofloor.spz", *floors.values(), *shading_files.values()}
    for stale in [*paths.scene.glob("floor*.spz"), *paths.scene.glob("floor-shading-*.png"),
                  paths.scene / "scene.ply"]:
        if stale.is_file() and stale.name not in wanted:
            stale.unlink()  # an unmasked aligned PLY, or a floor of a room that went

    download = files["scene_nofloor.spz"] + sum(files[f] for f in floors.values())
    if download > 60:
        warnings.append(
            f"The viewer downloads {download:.0f} MB, over the 60 MB target. Lower --max-splats "
            "or --sh-degree."
        )
    report = ExportReport(
        splats=len(cloud),
        cropped_out=cropped,
        floor_splats=int(on_floor.sum()),
        sh_degree=cloud.sh_degree,
        files_mb=files,
        download_mb=round(download, 2),
        floors={room_id: len(parts[name]) for room_id, name in floors.items()},
        privacy=privacy,
        validation=validation,
        warnings=warnings,
    )
    atomic_write_text(paths.work / "export_report.json", json.dumps(asdict(report), indent=2))
    return report

