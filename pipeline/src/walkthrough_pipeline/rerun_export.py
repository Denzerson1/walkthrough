"""
`pipeline rerun`: a capture as a local Rerun recording (rerun.io, MIT/Apache)
— every few frames with its camera, the LiDAR or COLMAP points, the trained
splat's centres, and the scan's walls where `align` put them — so poses,
depth and alignment can be looked at together, scrubbing along the walk.

The recording is a file (work/diagnostics/<project>.rrd); nothing is sent
anywhere. Open it with `rerun <file>` (pip install rerun-sdk==0.39.0, the
`diagnostics` extra) on the machine that holds the data.
"""

from __future__ import annotations

import struct
from pathlib import Path

import cv2
import numpy as np

from .colmap import find_model, read_images_bin
from .manifest import Manifest
from .paths import ProjectPaths
from .scene import aligned_dir, trained_ply
from .splat import read_ply


def read_cameras_bin(path: Path) -> dict[int, tuple[int, int, int, np.ndarray]]:
    """camera_id -> (model id, width, height, params), from COLMAP's cameras.bin."""
    counts = {0: 3, 1: 4, 2: 4, 3: 5, 4: 8, 5: 8, 6: 12, 7: 5, 8: 4, 9: 5, 10: 12}
    data = Path(path).read_bytes()
    (n,) = struct.unpack_from("<Q", data, 0)
    off, out = 8, {}
    for _ in range(n):
        cid, model, w, h = struct.unpack_from("<iiQQ", data, off)
        off += 24
        k = counts[model]
        out[cid] = (model, w, h, np.array(struct.unpack_from(f"<{k}d", data, off)))
        off += 8 * k
    return out


def read_points_with_colours(path: Path) -> tuple[np.ndarray, np.ndarray]:
    data = Path(path).read_bytes()
    (count,) = struct.unpack_from("<Q", data, 0)
    pos, rgb = np.empty((count, 3)), np.empty((count, 3), np.uint8)
    off = 8
    record = struct.calcsize("<Q3d3Bd")
    for i in range(count):
        _, x, y, z, r, g, b, _ = struct.unpack_from("<Q3d3Bd", data, off)
        pos[i], rgb[i] = (x, y, z), (r, g, b)
        (track,) = struct.unpack_from("<Q", data, off + record)
        off += record + 8 + 8 * track
    return pos, rgb


def _wall_strips(manifest: Manifest, to_capture: np.ndarray) -> list[np.ndarray]:
    """Each plan wall as a rectangle, carried back into the capture's frame."""
    strips = []
    for room in manifest.rooms:
        for wall in room.walls:
            (x0, z0), (x1, z1) = wall.start, wall.end
            h = wall.height
            corners = np.array([[x0, 0, z0], [x1, 0, z1], [x1, h, z1], [x0, h, z0], [x0, 0, z0]])
            strips.append(corners @ to_capture[:3, :3].T + to_capture[:3, 3])
    return strips


def export_recording(paths: ProjectPaths, out: Path, every: int = 3,
                     max_points: int = 400_000, image_width: int = 640) -> dict:
    import rerun as rr

    model = find_model(paths.work / "undistorted" / "sparse")
    if model is None:
        raise FileNotFoundError("No posed dataset. Run `pipeline poses` first.")
    out.parent.mkdir(parents=True, exist_ok=True)
    rr.init(f"walkthrough-{paths.root.name}", spawn=False)
    rr.save(out)
    rr.log("world", rr.ViewCoordinates.RIGHT_HAND_Y_UP, static=True)

    cams = sorted(read_images_bin(model / "images.bin"), key=lambda c: c.name)
    intrinsics = read_cameras_bin(model / "cameras.bin")
    images = paths.work / "undistorted" / "images"
    logged = 0
    for i, cam in enumerate(cams):
        if i % every:
            continue
        rr.set_time("frame", sequence=i)
        rr.log("world/camera", rr.Transform3D(translation=cam.centre, mat3x3=cam.rotation.T))
        _, w, h, params = intrinsics.get(cam.camera_id, next(iter(intrinsics.values())))
        fx, fy, cx, cy = params[:4] if len(params) >= 4 else (params[0], params[0], *params[1:3])
        s = image_width / w
        rr.log("world/camera/image", rr.Pinhole(
            image_from_camera=[[fx * s, 0, cx * s], [0, fy * s, cy * s], [0, 0, 1]],
            resolution=[image_width, round(h * s)], camera_xyz=rr.ViewCoordinates.RDF))
        frame = cv2.imread(str(images / cam.name))
        if frame is not None:
            small = cv2.resize(frame, (image_width, round(h * s)), interpolation=cv2.INTER_AREA)
            ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                rr.log("world/camera/image", rr.EncodedImage(contents=jpg.tobytes(),
                                                             media_type="image/jpeg"))
        logged += 1
    rr.log("world/path", rr.LineStrips3D([np.array([c.centre for c in cams])]), static=True)

    pts, rgb = read_points_with_colours(model / "points3D.bin")
    if len(pts) > max_points:
        keep = np.random.default_rng(0).choice(len(pts), max_points, replace=False)
        pts, rgb = pts[keep], rgb[keep]
    rr.log("world/points", rr.Points3D(pts, colors=rgb, radii=0.005), static=True)

    summary = {"file": out.name, "frames": logged, "points": len(pts)}
    if trained_ply(paths).is_file():
        splat = read_ply(trained_ply(paths))
        solid = splat.opacities >= np.percentile(splat.opacities, 60)
        idx = np.nonzero(solid)[0]
        if len(idx) > max_points:
            idx = np.random.default_rng(1).choice(idx, max_points, replace=False)
        colours = (np.clip(splat.colours[idx], 0, 1) * 255).astype(np.uint8)
        rr.log("world/splat_centres", rr.Points3D(splat.positions[idx], colors=colours,
                                                  radii=0.003), static=True)
        summary["splat_centres"] = len(idx)
    draft = aligned_dir(paths) / "manifest.json"
    if draft.is_file():
        manifest = Manifest.load(draft)
        to_capture = np.linalg.inv(np.array(manifest.transform))
        strips = _wall_strips(manifest, to_capture)
        if strips:
            rr.log("world/plan_walls", rr.LineStrips3D(strips, colors=[255, 120, 0], radii=0.01),
                   static=True)
        summary["plan_walls"] = len(strips)
    # Logging is asynchronous: close the file before saying it is written.
    rr.disconnect()
    return summary
