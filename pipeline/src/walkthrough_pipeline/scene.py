"""
`align` and `export`: from a trained splat in COLMAP's arbitrary frame to the
published scene — Y up, floor at y = 0, metres, rooms in the manifest, floor
split out, compressed.

Pure numpy over files, so the whole path is tested on synthetic scenes; the
CLI only wires it up.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import roomplan
from .align import compose, fit_plane_ransac, gravity_alignment, rotation_between
from .colmap import Camera, find_model, gravity_from_cameras, read_images_bin
from .floor import simplify_polygon
from .manifest import Manifest, Room, Wall, Waypoint
from .paths import ProjectPaths
from .register import ceiling_height, register_to_plan, solid, solid_segments, wall_points
from .splat import SplatCloud, cap_splat_count, crop_to_box, read_ply, write_ply, write_spz

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
    up_from: str  # "cameras" | "floor plane"
    floor_inlier_ratio: float
    scale_from: str  # "roomplan" | "camera height (estimate)" | "none"
    scale: float
    roomplan: dict = field(default_factory=dict)
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


def align_scene(paths: ProjectPaths, project_id: str, metric: bool = False) -> AlignReport:
    """
    Gravity from the cameras, floor from a plane fit, then yaw, position and
    metric scale from the RoomPlan scan when there is one. Writes
    scene/scene.ply and the manifest's rooms; raises rather than writing a
    scene it does not trust.

    `metric` says the splat is already in metres with Y up along gravity —
    true of an ARKit-posed capture — so scale is 1 and only the floor height,
    yaw and position remain to be found.
    """
    source = trained_ply(paths)
    if not source.is_file():
        raise AlignmentFailed(f"No trained scene at {source}. Run `pipeline train` first.")
    cloud = read_ply(source)
    cameras = load_cameras(paths)
    warnings: list[str] = []

    # 1. Up. The cameras know which way was up; a plane fit alone cannot tell
    #    a floor from a wall in a frame with no fixed orientation.
    if metric:
        up = np.array([0.0, 1.0, 0.0])
        up_from = "ARKit gravity"
    elif cameras:
        up = gravity_from_cameras(cameras)
        up_from = "cameras"
    else:
        up = np.array([0.0, 1.0, 0.0])
        up_from = "floor plane"
        warnings.append("No camera poses found; assuming the scene is already roughly Y-up.")
    level = np.eye(4)
    level[:3, :3] = rotation_between(up, np.array([0.0, 1.0, 0.0]))
    pts = _transform_points(cloud.positions.astype(np.float64), level)
    cam_pts = (
        _transform_points(np.array([c.centre for c in cameras]), level) if cameras else None
    )

    # 2. Floor: the dominant level plane below the cameras.
    opaque = solid(cloud.opacities)
    below = pts[:, 1] < (
        np.median(cam_pts[:, 1]) if cam_pts is not None else np.percentile(pts[:, 1], 35)
    )
    candidates = pts[opaque & below]
    if len(candidates) > 200_000:
        candidates = candidates[np.random.default_rng(0).choice(len(candidates), 200_000, False)]
    # Threshold relative to the scene's size, because COLMAP units are arbitrary.
    extent = float(np.percentile(pts[:, 1], 95) - np.percentile(pts[:, 1], 5))
    threshold = max(extent * 0.008, 1e-6)
    fit = fit_plane_ransac(candidates, threshold=threshold, up_hint=np.array([0.0, 1.0, 0.0]))
    if fit.inlier_ratio < 0.05:
        raise AlignmentFailed(
            "Could not find the floor: no level plane below the cameras. Check that the "
            "floor was filmed and that poses registered."
        )
    floor = gravity_alignment(fit.plane, cam_pts if cam_pts is not None else pts)
    transform = compose(level, floor)

    aligned = _transform_points(cloud.positions.astype(np.float64), transform)
    heights = aligned[:, 1]
    cam_heights = (
        _transform_points(np.array([c.centre for c in cameras]), transform)[:, 1]
        if cameras
        else None
    )

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
        if metric:
            # A small margin only: RoomPlan and ARKit both measure in metres,
            # and separate sessions agree to well under a percent.
            guess, spread = 1.0, (0.98, 1.02)
        elif ceiling is not None:
            guess, spread = wall_height / ceiling, (0.85, 1.15)
        elif cam_heights is not None:
            guess, spread = TYPICAL_CAMERA_HEIGHT_M / float(np.median(cam_heights)), (0.6, 1.6)
            warnings.append("No clear ceiling in the splat; scale search began at camera height.")
        else:
            raise AlignmentFailed("Neither a ceiling nor camera poses to start a scale search.")

        points = wall_points(aligned, cloud.opacities, ceiling or wall_height / guess)
        plan = register_to_plan(points, solid_segments(room), guess, spread)
        rp_report = {
            "file": scan_file.name,
            "walls": len(scan.of("walls")),
            "doors": len(scan.of("doors")) + len(scan.of("openings")),
            "windows": len(scan.of("windows")),
            "objects": len(scan.of("objects")),
            "inlier_ratio": round(plan.inlier_ratio, 3),
            "median_error_mm": round(plan.median_error_m * 1000, 1),
            "symmetry_ratio": round(plan.symmetry_ratio, 3),
        }
        if plan.inlier_ratio < MIN_INLIER_RATIO or plan.median_error_m > MAX_MEDIAN_ERROR_M:
            raise AlignmentFailed(
                f"The splat does not fit the RoomPlan walls (only {plan.inlier_ratio:.0%} of wall "
                f"points within 5 cm, median {plan.median_error_m * 1000:.0f} mm). Is the scan "
                "of the same room? Nothing was written."
            )
        if plan.symmetry_ratio > 0.95:
            warnings.append(
                "The room is nearly symmetric and its doors and windows barely told the two "
                "orientations apart. Check in the viewer that the plan sits the right way round."
            )
        transform = compose(transform, plan.matrix())
        scale, scale_from = plan.scale, "roomplan"
        manifest.rooms = [room]
        manifest.items = items
    else:
        if metric:
            scale, scale_from = 1.0, "ARKit (metric)"
            warnings.append(
                "No RoomPlan scan: walls and doors are not known, so the room is outlined "
                "from the floor and has no doorways."
            )
        elif cam_heights is None:
            raise AlignmentFailed("No RoomPlan scan and no camera poses: scale is unknown.")
        else:
            scale = TYPICAL_CAMERA_HEIGHT_M / float(np.median(cam_heights))
            scale_from = "camera height (estimate)"
            warnings.append(
                "No RoomPlan scan: scale is estimated from a typical phone height and may be "
                "off by 10-20%. Room walls and doors are not known, so the room is outlined "
                "from the floor and has no doorways."
            )
        s = np.diag([scale, scale, scale, 1.0])
        transform = compose(transform, s)
        floor_pts = (aligned[opaque & (np.abs(heights) < threshold)] * scale)[:, [0, 2]]
        manifest.rooms = [_room_from_floor(floor_pts)]
        manifest.items = []

    if cameras:
        _stand_where_filmed(manifest.rooms[0], cameras, transform)

    result = cloud.transformed(transform)
    out = paths.scene / "scene.ply"
    write_ply(result, out)
    manifest.transform = transform.tolist()
    manifest.floor = {"plane": [0.0, 1.0, 0.0, 0.0]}
    manifest.assets.scene = out.name
    manifest.assets.sceneNoFloor = None
    manifest.assets.floor = None
    manifest.write(paths.manifest)

    report = AlignReport(
        source=str(source.name),
        splats=len(cloud),
        up_from=up_from,
        floor_inlier_ratio=round(fit.inlier_ratio, 3),
        scale_from=scale_from,
        scale=round(scale, 5),
        roomplan=rp_report,
        warnings=warnings,
    )
    (paths.work / "align_report.json").write_text(json.dumps(asdict(report), indent=2))
    return report


def floor_outline(floor_xz: np.ndarray, cell: float = 0.1) -> np.ndarray:
    """
    The floor's outline from floor-height splats: the largest connected
    region of well-populated 10 cm cells.

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
    occupied = counts >= max(3, np.percentile(counts[counts > 0], 25))
    grid = cv2.morphologyEx(occupied.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
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


def _room_from_floor(floor_xz: np.ndarray) -> Room:
    """A room outlined from floor splats, for a capture without a scan."""
    if len(floor_xz) < 50:
        raise AlignmentFailed("Too little floor in the splat to outline the room.")
    poly = floor_outline(floor_xz)
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


def export_scene(
    paths: ProjectPaths,
    max_splats: int = 1_500_000,
    crop_margin: float | None = 0.75,
    sh_degree: int = 3,
) -> ExportReport:
    """
    Crop to the rooms, cap the count, split the floor out and write SPZ.

    Cropping drops what the camera saw beyond the walls — the view through
    windows and doors and the floaters that come with it. `crop_margin=None`
    keeps everything.
    """
    manifest = Manifest.load(paths.manifest)
    src = paths.scene / "scene.ply"
    if not src.is_file() or not manifest.rooms:
        raise AlignmentFailed("No aligned scene with rooms. Run `pipeline align` first.")
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

    near_floor = np.abs(cloud.positions[:, 1]) < FLOOR_TOLERANCE_M
    on_floor = np.zeros(len(cloud), dtype=bool)
    for room, poly in zip(manifest.rooms, polys, strict=True):
        inside = near_floor & points_in_polygon(cloud.positions[:, [0, 2]], _grow(poly, 0.05))
        on_floor |= inside
        if inside.any():
            rgb = np.clip(cloud.colours[inside].mean(axis=0), 0, 1)
            room.originalFloorColor = "#{:02x}{:02x}{:02x}".format(*np.round(rgb * 255).astype(int))
    if on_floor.sum() < 1000:
        warnings.append("Very few floor splats found; floor replacement will look patchy.")

    files = {}
    for name, part in (
        ("scene.spz", cloud),
        ("scene_nofloor.spz", cloud.select(~on_floor)),
        ("floor.spz", cloud.select(on_floor)),
    ):
        write_spz(part, paths.scene / name)
        files[name] = round((paths.scene / name).stat().st_size / 1e6, 2)

    manifest.assets.scene = "scene.spz"
    manifest.assets.sceneNoFloor = "scene_nofloor.spz"
    manifest.assets.floor = "floor.spz"
    manifest.write(paths.manifest)

    download = files["scene_nofloor.spz"] + files["floor.spz"]
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
        warnings=warnings,
    )
    (paths.work / "export_report.json").write_text(json.dumps(asdict(report), indent=2))
    return report

