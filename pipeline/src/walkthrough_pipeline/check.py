"""
`pipeline check`: what can be known about a capture before an hour of
training goes into it, written to work/preflight/.

A Stray capture is checked against its own format: frame numbering and
timestamps, depth beside every pose, depth units and confidence, the
intrinsics, the pose convention (ARKit Y up: the LiDAR floor must lie
level), how well the video's edges line up with the depth's, and whether
they line up as well at the corners as in the middle (a hint of lens
distortion the pinhole model ignores, or of thin LiDAR at the edges). A
video is checked for sharpness and exposure along the walk and, once COLMAP
has run, for what its poses are worth.
Both get pictures a person can judge at a glance: a contact sheet, the
camera path with its frustums over the room, depth over the video, and
the depth sightings per frame.

Registration share alone is not a quality gate, so nothing here is: the
report lists numbers and plain-language warnings.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

from . import stray as stray_mod
from .align import FloorRejected, find_floor
from .colmap import find_model, read_images_bin, read_points3d_bin
from .frames import ffmpeg_path, score_directory
from .paths import ProjectPaths

#: ARKit's video runs at 60 fps; a gap over this many frame times is a hitch.
TIMESTAMP_GAP = 2.5
#: Edge agreement in the corners under this share of the centre's is worth a
#: look: lens distortion the PINHOLE model leaves in, or thin LiDAR at the
#: edges (ARKit registers depth to the image, so both share any distortion).
CORNER_AGREEMENT_RATIO = 0.6


def preflight(paths: ProjectPaths) -> dict:
    out = paths.work / "preflight"
    out.mkdir(parents=True, exist_ok=True)
    root = stray_mod.find_capture(paths.input / "stray")
    report: dict = {"warnings": []}
    if root is not None:
        capture = stray_mod.load(root)
        report["capture"] = "stray"
        report.update(check_stray(capture, out, paths))
    else:
        report["capture"] = "video"
        report.update(check_video(paths, out))
    frames = sorted((paths.work / "frames").glob("frame_*.png"))
    if frames:
        report["contact_sheet"] = contact_sheet(frames, out / "contact_sheet.jpg")
    obs = paths.work / "depth_observations.json"
    if obs.is_file():
        report["observations"] = observation_plot(json.loads(obs.read_text()),
                                                  out / "observations.png")
        if report["observations"]["frames_under_100"]:
            report["warnings"].append(
                f"{report['observations']['frames_under_100']} frame(s) have under 100 depth "
                "sightings: depth barely supervises them.")
    (out / "report.json").write_text(json.dumps(report, indent=2, default=_plain))
    return report


def _plain(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(type(value))


# --------------------------------------------------------------------------
# Stray
# --------------------------------------------------------------------------


def check_stray(capture: stray_mod.StrayCapture, out: Path, paths: ProjectPaths) -> dict:
    warnings: list[str] = []
    result: dict = {"frames": len(capture), "video_size": list(capture.size)}

    # Numbering, and depth beside every pose.
    steps = np.diff(capture.frames)
    gaps = [[int(capture.frames[i]), int(capture.frames[i + 1])]
            for i in np.nonzero(steps != 1)[0]]
    missing_depth = [int(f) for f in capture.frames
                     if not capture.depth_path(int(f)).is_file()
                     or not capture.confidence_path(int(f)).is_file()]
    result["numbering"] = {"first": int(capture.frames[0]), "gaps": gaps[:20],
                           "gap_count": len(gaps), "missing_depth": missing_depth[:20],
                           "missing_depth_count": len(missing_depth)}
    if gaps:
        warnings.append(f"Frame numbers jump {len(gaps)} time(s) in odometry.csv, first at "
                        f"{gaps[0]}: poses may not line up with the video there.")
    if missing_depth:
        warnings.append(f"{len(missing_depth)} posed frame(s) have no depth or confidence map.")

    # Timestamps.
    if capture.timestamps is not None and len(capture.timestamps) > 1:
        dt = np.diff(capture.timestamps)
        median = float(np.median(dt))
        hitches = int((dt > TIMESTAMP_GAP * median).sum())
        result["timestamps"] = {"monotonic": bool((dt > 0).all()),
                                "median_dt_s": round(median, 5),
                                "fps": round(1 / median, 2) if median > 0 else None,
                                "hitches": hitches, "duration_s": round(float(
                                    capture.timestamps[-1] - capture.timestamps[0]), 1)}
        if not (dt > 0).all():
            warnings.append("Timestamps in odometry.csv go backwards.")
        if hitches:
            warnings.append(f"{hitches} gap(s) in the timestamps over {TIMESTAMP_GAP}x the frame "
                            "time: the phone dropped frames.")

    # Depth maps: size, units, confidence.
    sample = np.linspace(0, len(capture) - 1, min(12, len(capture))).round().astype(int)
    depth_stats = []
    for row in sample:
        frame = int(capture.frames[row])
        depth = cv2.imread(str(capture.depth_path(frame)), cv2.IMREAD_UNCHANGED)
        conf = cv2.imread(str(capture.confidence_path(frame)), cv2.IMREAD_UNCHANGED)
        if depth is None or conf is None:
            continue
        z = depth.astype(np.float64) / 1000.0
        good = (conf >= stray_mod.MIN_CONFIDENCE) & (z > stray_mod.DEPTH_RANGE_M[0]) & (
            z < stray_mod.DEPTH_RANGE_M[1])
        depth_stats.append({
            "frame": frame, "shape": list(depth.shape), "dtype": str(depth.dtype),
            "median_m": round(float(np.median(z[z > 0])) if (z > 0).any() else 0.0, 3),
            "usable": round(float(good.mean()), 3),
            "confidence": [round(float((conf == c).mean()), 3) for c in (0, 1, 2)],
        })
    result["depth"] = depth_stats
    if depth_stats:
        h, w = depth_stats[0]["shape"]
        aspect_gap = abs(w / h - capture.size[0] / capture.size[1]) / (capture.size[0]
                                                                         / capture.size[1])
        result["depth_aspect_mismatch"] = round(aspect_gap, 4)
        if aspect_gap > 0.01:
            warnings.append(f"Depth maps are {w}x{h} but the video is {capture.size[0]}x"
                            f"{capture.size[1]}: they do not cover the same view.")
        medians = [d["median_m"] for d in depth_stats]
        if not 0.3 < float(np.median(medians)) < 6.0:
            warnings.append(f"Median depth {np.median(medians):.2f} m: not millimetres from an "
                            "indoor LiDAR. Check the depth units.")
        usable = float(np.mean([d["usable"] for d in depth_stats]))
        result["depth_usable_share"] = round(usable, 3)
        if usable < 0.4:
            warnings.append(f"Only {usable:.0%} of depth pixels are high-confidence and in "
                            "range: much of the room is beyond the LiDAR or seen through glass.")

    # Intrinsics.
    fx, fy, cx, cy = capture.intrinsics.T
    w, h = capture.size
    result["intrinsics"] = {
        "cx_offset": round(float(np.median(cx / w - 0.5)), 4),
        "cy_offset": round(float(np.median(cy / h - 0.5)), 4),
        "fx_over_fy": round(float(np.median(fx / fy)), 4),
        "horizontal_fov_deg": round(float(np.degrees(2 * np.arctan(w / (2 * np.median(fx))))), 1),
        "fx_spread": round(float(np.std(fx) / np.mean(fx)), 5),
        "per_frame": bool(np.std(fx) > 0),
    }
    offsets = (result["intrinsics"]["cx_offset"], result["intrinsics"]["cy_offset"])
    if max(abs(o) for o in offsets) > 0.05:
        warnings.append("The principal point is over 5% from the image centre: the intrinsics "
                        "may be for another resolution or orientation.")
    if abs(result["intrinsics"]["fx_over_fy"] - 1) > 0.02:
        warnings.append("fx and fy differ by over 2%: non-square pixels or mixed-up intrinsics.")

    # Pose convention: proper rotations, and gravity along +Y (the LiDAR floor level).
    rot = capture.cam_to_world[:, :3, :3]
    ortho = float(np.abs(np.einsum("nij,nik->njk", rot, rot) - np.eye(3)).max())
    dets = np.linalg.det(rot)
    result["poses"] = {"max_orthonormal_error": round(ortho, 6),
                       "det_range": [round(float(dets.min()), 6), round(float(dets.max()), 6)],
                       "tracking_jumps": len(stray_mod.tracking_jumps(capture))}
    if ortho > 1e-3 or dets.min() < 0.99:
        warnings.append("Some poses are not proper rotations: odometry.csv is corrupt.")
    # A phone in the hand mostly looks across the room, not at the floor or
    # ceiling: in a Y-up world its optical axis is mostly level. A floor test
    # alone misses a frame turned 90°, where a wall becomes a level "floor".
    looking = float(np.median(np.abs(rot[:, 1, 2])))
    result["poses"]["median_abs_forward_y"] = round(looking, 3)
    if looking > 0.6:
        warnings.append(f"The camera mostly looks straight up or down (median |forward.y| "
                        f"{looking:.2f}): the poses are probably not Y-up.")
    world = []
    for row in np.linspace(0, len(capture) - 1, min(60, len(capture))).round().astype(int):
        try:
            pts, _ = stray_mod.depth_points(capture, int(row), stride=4)
            world.append(pts)
        except stray_mod.StrayError:
            continue
    lidar = np.vstack(world) if world else np.zeros((0, 3))
    cam_y = capture.cam_to_world[:, 1, 3]
    if len(lidar) > 1000:
        try:
            floor = find_floor(lidar, cam_y, threshold=0.02, cell=0.1, keep_gravity=True,
                               min_area=1.0, clearance_range=(0.4, 2.2))
            result["poses"]["floor"] = {"tilt_deg": round(floor.tilt_deg, 2),
                                        "camera_height_median_m":
                                            round(floor.clearance["median"], 3),
                                        "area_m2": round(floor.area, 2)}
            if floor.tilt_deg > 2.0:
                warnings.append(f"The LiDAR floor leans {floor.tilt_deg:.1f}° from ARKit's up: "
                                "the pose convention or gravity is off.")
        except FloorRejected as exc:
            result["poses"]["floor"] = f"not found: {exc}"
            warnings.append(f"No level LiDAR floor under the cameras ({exc}). Either the floor "
                            "was not filmed or the poses are not Y-up.")

    # Coverage: where the camera looked from, and gaps in direction.
    forward = capture.cam_to_world[:, :3, 2]
    yaw = np.degrees(np.arctan2(forward[:, 0], -forward[:, 2])) % 360
    hist = np.histogram(yaw, bins=12, range=(0, 360))[0]
    pitch = np.degrees(np.arcsin(np.clip(-forward[:, 1], -1, 1)))
    result["coverage"] = {"yaw_bins_30deg": hist.tolist(),
                          "empty_yaw_bins": int((hist == 0).sum()),
                          "pitch_deg": [round(float(np.percentile(pitch, 5)), 1),
                                        round(float(np.percentile(pitch, 95)), 1)],
                          "path_extent_m": np.round(np.ptp(capture.cam_to_world[:, [0, 2], 3],
                                                           axis=0), 2).tolist()}
    if (hist == 0).any():
        warnings.append(f"{int((hist == 0).sum())} of 12 compass directions were never faced: "
                        "walls there have no views.")
    if np.percentile(pitch, 95) < 25:
        warnings.append("The camera hardly looked up: the ceiling will be thin.")

    # Video against depth: sync, and lens distortion at the corners.
    offset = _measured_offset(paths)
    result["rgb_depth"] = rgb_depth_alignment(capture, offset, out / "depth_overlay.jpg",
                                              paths.work / "frames")
    ratio = result["rgb_depth"].get("corner_to_centre")
    if ratio is not None and ratio < CORNER_AGREEMENT_RATIO:
        warnings.append(f"Depth and video edges agree {ratio:.0%} as well in the corners as in "
                        "the centre. Either lens distortion (Stray records none, so the "
                        "pinhole model is assumed and the frame's edges train less sharply) or "
                        "LiDAR thinner at the edges; the depth overlay shows which.")

    points = paths.work / "undistorted" / "sparse" / "points3D.bin"
    background = read_points3d_bin(points) if points.is_file() else lidar
    result["trajectory"] = trajectory_plot(capture.cam_to_world, background,
                                           out / "trajectory.png")
    result["warnings"] = warnings
    return result


def _measured_offset(paths: ProjectPaths) -> int:
    report = paths.work / "poses_report.json"
    if report.is_file():
        return int(json.loads(report.read_text()).get("video_frame_offset", 0))
    return 0


def _decode(video: Path, frame: int, size: tuple[int, int]) -> np.ndarray | None:
    w, h = size
    raw = subprocess.run(
        [ffmpeg_path(), "-v", "error", "-noautorotate", "-i", str(video), "-vf",
         f"select=eq(n\\,{frame}),scale={w}:{h}", "-fps_mode", "vfr", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3) if len(raw) == w * h * 3 else None


def rgb_depth_alignment(capture: stray_mod.StrayCapture, offset: int, overlay: Path,
                        frames_dir: Path) -> dict:
    """
    Edge agreement between depth and video at the measured offset: over the
    whole frame, the centre, and the four corners. Writes a few frames with
    depth drawn over them.
    """
    rows = np.linspace(len(capture) * 0.1, len(capture) * 0.9, 6).round().astype(int)
    whole, centre, corners, tiles = [], [], [], []
    for row in rows:
        n = int(capture.frames[row])
        depth = cv2.imread(str(capture.depth_path(n)), cv2.IMREAD_UNCHANGED)
        if depth is None:
            continue
        h, w = depth.shape
        try:
            image = _decode(capture.video, n + offset, (w, h))
        except (subprocess.CalledProcessError, OSError, RuntimeError):
            image = None
        if image is None:
            continue
        grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        whole.append(stray_mod.edge_agreement(depth, grey))
        qh, qw = h // 3, w // 3
        centre.append(stray_mod.edge_agreement(depth[qh:2 * qh, qw:2 * qw],
                                               grey[qh:2 * qh, qw:2 * qw]))
        for ys, xs in ((slice(0, qh), slice(0, qw)), (slice(0, qh), slice(w - qw, w)),
                       (slice(h - qh, h), slice(0, qw)), (slice(h - qh, h), slice(w - qw, w))):
            corners.append(stray_mod.edge_agreement(depth[ys, xs], grey[ys, xs]))
        if len(tiles) < 4:
            z = np.clip(depth.astype(np.float32) / 4000.0, 0, 1)
            colour = cv2.applyColorMap((255 * (1 - z)).astype(np.uint8), cv2.COLORMAP_TURBO)
            big = (w * 3, h * 3)
            blend = cv2.addWeighted(cv2.resize(image, big), 0.55, cv2.resize(colour, big), 0.45, 0)
            # Depth's own edges in white: on a well-synced frame they trace the
            # door frames and furniture edges of the picture under them.
            d8 = cv2.resize((z * 255).astype(np.uint8), big, interpolation=cv2.INTER_NEAREST)
            blend[cv2.Canny(d8, 20, 60) > 0] = (255, 255, 255)
            cv2.putText(blend, f"frame {n} (video {offset:+d})", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            tiles.append(blend)
    if tiles:
        while len(tiles) % 2:
            tiles.append(np.zeros_like(tiles[0]))
        grid = np.vstack([np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)])
        cv2.imwrite(str(overlay), grid, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not whole:
        return {"note": "video frames could not be decoded (ffmpeg missing?)"}
    c, k = float(np.mean(centre)), float(np.mean(corners))
    return {"offset": offset, "edge_agreement": round(float(np.mean(whole)), 3),
            "centre": round(c, 3), "corners": round(k, 3),
            "corner_to_centre": round(k / c, 3) if c > 1e-6 else None,
            "overlay": overlay.name if tiles else None}


# --------------------------------------------------------------------------
# Video
# --------------------------------------------------------------------------


def check_video(paths: ProjectPaths, out: Path) -> dict:
    warnings: list[str] = []
    result: dict = {}
    raw = sorted((paths.work / "frames_raw").glob("frame_*.png"))
    if raw:
        lum, sharp = [], []
        for p in raw:
            image = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            small = cv2.resize(image, (320, int(320 * image.shape[0] / image.shape[1])),
                               interpolation=cv2.INTER_AREA)
            lum.append(float(small.mean()))
            sharp.append(float(cv2.Laplacian(small, cv2.CV_32F).var()))
        lum_a, sharp_a = np.array(lum), np.array(sharp)
        jumps = np.abs(np.diff(lum_a)) / np.maximum(lum_a[:-1], 1)
        result["exposure"] = {"mean": round(float(lum_a.mean()), 1),
                              "range": [round(float(lum_a.min()), 1),
                                        round(float(lum_a.max()), 1)],
                              "jumps_over_12pct": int((jumps > 0.12).sum())}
        result["sharpness"] = {"median": round(float(np.median(sharp_a)), 1),
                               "share_under_40pct_median": round(float(
                                   (sharp_a < 0.4 * np.median(sharp_a)).mean()), 3)}
        if result["exposure"]["jumps_over_12pct"] > 3:
            warnings.append(f"Exposure jumps by over 12% {result['exposure']['jumps_over_12pct']} "
                            "times between frames: auto-exposure was on. Lock it (docs/CAPTURE.md)"
                            "; colours will band between views.")
        if result["sharpness"]["share_under_40pct_median"] > 0.25:
            warnings.append("Over a quarter of the footage is motion-blurred: walk and turn "
                            "slower.")
    result["stabilisation"] = ("not readable from the file: record with stabilisation off "
                               "(docs/CAPTURE.md); it moves the optical centre frame to frame")
    poses = paths.work / "poses_report.json"
    if poses.is_file():
        report = json.loads(poses.read_text())
        result["poses"] = {k: report.get(k) for k in (
            "registered", "share", "models", "model_sizes", "mean_reprojection_error_px",
            "mean_track_length", "camera", "unregistered_runs")}
        warnings += report.get("warnings", [])
    model = find_model(paths.work / "undistorted" / "sparse") if (
        paths.work / "undistorted" / "sparse").is_dir() else None
    if model is not None:
        cams = sorted(read_images_bin(model / "images.bin"), key=lambda c: c.name)
        c2w = np.tile(np.eye(4), (len(cams), 1, 1))
        for i, c in enumerate(cams):
            c2w[i, :3, :3] = c.rotation.T
            c2w[i, :3, 3] = c.centre
        points = read_points3d_bin(model / "points3D.bin")
        result["trajectory"] = trajectory_plot(c2w, points, out / "trajectory.png", up_axis=None)
        result["loop"] = loop_consistency(model)
        if result["loop"].get("shared_points_first_last", 1) == 0:
            warnings.append("No 3D point is seen both at the start and the end of the walk: the "
                            "loop did not close, so drift is unchecked.")
    result["warnings"] = warnings
    return result


def loop_consistency(model: Path) -> dict:
    """How many 3D points the first and last tenth of the walk both see (pycolmap)."""
    try:
        import pycolmap  # type: ignore[import-not-found]
    except ImportError:
        return {"note": "pycolmap not installed here"}
    rec = pycolmap.Reconstruction(str(model))
    names = sorted((im.name, im_id) for im_id, im in rec.images.items())
    tenth = max(1, len(names) // 10)
    first = {i for _, i in names[:tenth]}
    last = {i for _, i in names[-tenth:]}
    shared = 0
    for point in rec.points3D.values():
        ids = {el.image_id for el in point.track.elements}
        shared += bool(ids & first) and bool(ids & last)
    return {"shared_points_first_last": shared, "images_per_tenth": tenth}


# --------------------------------------------------------------------------
# Pictures
# --------------------------------------------------------------------------


def contact_sheet(frames: list[Path], path: Path, cells: int = 48, width: int = 240) -> dict:
    """Every Nth frame, small, with its name and sharpness, in time order."""
    picks = [frames[i] for i in np.linspace(0, len(frames) - 1, min(cells, len(frames)))
             .round().astype(int)]
    scores = score_directory(frames[0].parent) if len(frames) <= 2000 else {}
    tiles = []
    for p in picks:
        image = cv2.imread(str(p))
        h = int(width * image.shape[0] / image.shape[1])
        tile = cv2.resize(image, (width, h), interpolation=cv2.INTER_AREA)
        label = p.stem.replace("frame_", "")
        if p in scores:
            label += f"  s{scores[p]:.0f}"
        cv2.putText(tile, label, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3)
        cv2.putText(tile, label, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        tiles.append(tile)
    h = max(t.shape[0] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, h - t.shape[0], 0, 0, cv2.BORDER_CONSTANT) for t in tiles]
    cols = 8
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])
    cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return {"file": path.name, "frames_shown": len(picks)}


def trajectory_plot(cam_to_world: np.ndarray, points: np.ndarray, path: Path,
                    up_axis: int | None = 1, size: int = 1000) -> dict:
    """
    Top-down: the room's points in grey, the camera path from blue (start)
    to red (end), and a frustum every few frames showing where it looked.
    `up_axis` None: find up from the cameras (a video's arbitrary frame).
    """
    pos = cam_to_world[:, :3, 3]
    if up_axis is None:
        rights = cam_to_world[:, :3, 0]
        up = np.linalg.eigh(rights.T @ rights)[1][:, 0]
        if (-cam_to_world[:, :3, 1].mean(axis=0)) @ up < 0:
            up = -up
        a = np.cross(up, [1.0, 0, 0] if abs(up[0]) < 0.9 else [0, 0, 1.0])
        a /= np.linalg.norm(a)
        b = np.cross(up, a)
        basis = np.stack([a, b])
    else:
        basis = np.array([[1.0, 0, 0], [0, 0, 1.0]])
    p2 = pos @ basis.T
    q2 = points @ basis.T if len(points) else np.zeros((0, 2))
    allp = np.vstack([p2, q2]) if len(q2) else p2
    lo, hi = np.percentile(allp, 1, axis=0), np.percentile(allp, 99, axis=0)
    span = float(max(hi - lo)) * 1.1 or 1.0
    centre = (lo + hi) / 2

    def px(xy: np.ndarray) -> np.ndarray:
        return ((xy - centre) / span * (size - 40) + size / 2).astype(int)

    canvas = np.full((size, size, 3), 255, np.uint8)
    if len(q2):
        idx = np.random.default_rng(0).choice(len(q2), min(len(q2), 200_000), replace=False)
        pp = px(q2[idx])
        ok = np.all((pp >= 0) & (pp < size), axis=1)
        canvas[pp[ok, 1], pp[ok, 0]] = (190, 190, 190)
    path_px = px(p2)
    n = len(path_px)
    for i in range(n - 1):
        t = i / max(n - 1, 1)
        colour = (int(255 * (1 - t)), 60, int(255 * t))
        cv2.line(canvas, tuple(path_px[i]), tuple(path_px[i + 1]), colour, 1, cv2.LINE_AA)
    forward = cam_to_world[:, :3, 2] @ basis.T
    right = cam_to_world[:, :3, 0] @ basis.T
    every = max(1, n // 60)
    for i in range(0, n, every):
        t = i / max(n - 1, 1)
        colour = (int(255 * (1 - t)), 60, int(255 * t))
        tip = p2[i] + forward[i] * span * 0.03
        corners = [tip + right[i] * span * 0.015, tip - right[i] * span * 0.015]
        o, c1, c2 = px(p2[i]), px(corners[0]), px(corners[1])
        cv2.polylines(canvas, [np.array([o, c1, c2])], True, colour, 1, cv2.LINE_AA)
    cv2.putText(canvas, "start: blue  end: red  grey: points", (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
    cv2.imwrite(str(path), canvas)
    return {"file": path.name, "cameras": n}


def observation_plot(per_frame: dict[str, int], path: Path) -> dict:
    """Depth sightings per frame along the walk, as bars."""
    counts = np.array(list(per_frame.values()), dtype=np.float64)
    w, h = 1000, 300
    canvas = np.full((h, w, 3), 255, np.uint8)
    top = max(float(counts.max()), 1.0)
    for i, c in enumerate(counts):
        x = int(i / max(len(counts), 1) * w)
        x2 = max(x + 1, int((i + 1) / max(len(counts), 1) * w))
        cv2.rectangle(canvas, (x, h - int(c / top * (h - 30))), (x2 - 1, h - 1),
                      (60, 60, 200) if c < 100 else (120, 120, 120), -1)
    cv2.putText(canvas, f"depth sightings per frame (max {int(top)}; red: under 100)", (10, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1)
    cv2.imwrite(str(path), canvas)
    return {"file": path.name, "min": int(counts.min()), "median": int(np.median(counts)),
            "max": int(counts.max()), "frames_under_100": int((counts < 100).sum())}
