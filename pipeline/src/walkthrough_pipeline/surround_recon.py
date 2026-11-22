"""
The reconstruction under a surround (surround.py): where each of the
owner's photos was taken, and how far away everything in it is.

- Structure from motion over all the photos at once (COLMAP by pycolmap):
  affine-covariant SIFT and guided matching, as the photos are few and
  taken far apart (round the house, off the balcony, in the street), so
  plain SIFT ties them into three separate models and these into one.
- Multi-view stereo (COLMAP's PatchMatch, geometric): depth where two or
  more photos see the same textured surface from different spots. Exact,
  and sparse: a tenth of each photo.
- A single-image depth network for the rest (Depth Anything V2 Small,
  Apache-2.0; run in the trainer's venv, `pipeline/depth/mono_depth.py`).
  Its depth is relative: `complete` pins it to the stereo's by a line in
  inverse depth and a smooth local correction.

Depths here are COLMAP's: along the camera's axis, in the model's units.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .train import DEFAULT_PYTHON

#: The photos' long edge for matching and texturing, and for stereo.
FRAME_EDGE, STEREO_EDGE = 2400, 1200
#: Nothing is farther than this, in model units (inverse depth's floor).
Q_MIN = 1 / 3000.0
MONO_SCRIPT = Path(__file__).resolve().parents[2] / "depth" / "mono_depth.py"
#: Where docs/PIPELINE.md's setup puts the depth network; override with the env vars.
DEFAULT_DEPTH_PACKAGES = Path.home() / "depth_pkgs"
DEFAULT_DEPTH_MODEL = (Path(__file__).resolve().parents[3] / "data" / "cache"
                       / "depth-anything-v2-small")


class DepthNetworkMissing(RuntimeError):
    pass


def frames(photos: list[Path], out: Path) -> list[Path]:
    """The photos upright at FRAME_EDGE, as JPEGs under `out`: what COLMAP reads."""
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for photo in photos:
        target = out / (photo.stem + ".jpg")
        if not target.is_file() or target.stat().st_mtime < photo.stat().st_mtime:
            image = ImageOps.exif_transpose(Image.open(photo)).convert("RGB")
            image.thumbnail((FRAME_EDGE, FRAME_EDGE), Image.LANCZOS)
            image.save(target, quality=95)
        written.append(target)
    return written


def structure(pycolmap, frame_dir: Path, work: Path) -> Path:
    """
    Structure from motion: the largest model, less the photos it holds by
    too few points to give a depth range (they stall the stereo), written
    to `work/sparse`. About an hour for seventy photos: the affine SIFT
    runs on the CPU.
    """
    db = work / "colmap.db"
    db.unlink(missing_ok=True)
    extraction = pycolmap.FeatureExtractionOptions()
    extraction.max_image_size = FRAME_EDGE
    extraction.sift.max_num_features = 20000
    extraction.sift.estimate_affine_shape = True
    extraction.sift.domain_size_pooling = True
    extraction.use_gpu = False  # COLMAP's GPU SIFT has neither
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "OPENCV"
    pycolmap.extract_features(db, frame_dir, camera_mode=pycolmap.CameraMode.SINGLE,
                              reader_options=reader, extraction_options=extraction)
    matching = pycolmap.FeatureMatchingOptions()
    matching.guided_matching = True
    pycolmap.match_exhaustive(db, matching_options=matching)
    scratch = work / "sparse_all"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    options = pycolmap.IncrementalPipelineOptions()
    options.min_model_size = 5
    models = pycolmap.incremental_mapping(db, frame_dir, scratch, options=options)
    if not models:
        raise RuntimeError("COLMAP registered no model from the photos.")
    best = max(models.values(), key=lambda m: m.num_reg_images())
    for image in list(best.images.values()):
        seen = sum(1 for p in image.points2D if p.has_point3D())
        if seen < 40:
            best.deregister_frame(image.frame_id)
    out = work / "sparse"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    best.write(str(out))
    return out


def stereo(pycolmap, sparse: Path, frame_dir: Path, dense: Path) -> None:
    """Undistorted photos and PatchMatch depth maps (geometric) under `dense`."""
    shutil.rmtree(dense, ignore_errors=True)
    undistort = pycolmap.UndistortCameraOptions()
    undistort.max_image_size = STEREO_EDGE
    pycolmap.undistort_images(str(dense), str(sparse), str(frame_dir), undistort_options=undistort)
    options = pycolmap.PatchMatchOptions()
    options.max_image_size = STEREO_EDGE
    options.geom_consistency = True
    options.filter = True
    pycolmap.patch_match_stereo(str(dense), options=options)


def network_depth(dense: Path) -> None:
    """Relative depth for every undistorted photo, into `dense/mono` (see the module's notes)."""
    python = Path(os.environ.get("GSPLAT_PYTHON", DEFAULT_PYTHON))
    packages = Path(os.environ.get("DEPTH_PACKAGES", DEFAULT_DEPTH_PACKAGES))
    model = Path(os.environ.get("DEPTH_MODEL", DEFAULT_DEPTH_MODEL))
    for what, path in (("the trainer's Python", python), ("transformers", packages),
                       ("the depth network's weights", model / "model.safetensors")):
        if not path.exists():
            raise DepthNetworkMissing(f"{what} not found at {path}. See docs/PIPELINE.md, "
                                      "'The surroundings from photos'.")
    env = {**os.environ, "PYTHONPATH": str(packages)}
    subprocess.run([str(python), str(MONO_SCRIPT), str(dense / "images"), str(dense / "mono"),
                    str(model)], check=True, env=env)


def read_array(path: Path) -> np.ndarray:
    """A COLMAP depth or normal map (.bin: "w&h&c&" then float32, column-major)."""
    with open(path, "rb") as file:
        head = b""
        while head.count(b"&") < 3:
            head += file.read(1)
        w, h, c = (int(v) for v in head.decode().split("&")[:3])
        data = np.fromfile(file, np.float32)
    return data.reshape((w, h, c), order="F").transpose(1, 0, 2).squeeze()


@dataclass
class View:
    """One photo in the model: its pinhole camera (undistorted) and pose."""

    name: str
    rotation: np.ndarray  # model -> camera
    translation: np.ndarray
    k: np.ndarray  # 3 x 3 intrinsics
    size: tuple[int, int]  # (w, h)
    sparse_xy: np.ndarray  # (n, 2) pixels of its SfM points
    sparse_depth: np.ndarray  # (n,) their depth

    @property
    def centre(self) -> np.ndarray:
        return -self.rotation.T @ self.translation

    def rays(self, step: int = 1) -> np.ndarray:
        """Camera rays of unit depth for the pixel grid at `step`, (h, w, 3)."""
        w, h = self.size
        u, v = np.meshgrid(np.arange(0, w, step) + 0.5, np.arange(0, h, step) + 0.5)
        return np.stack([(u - self.k[0, 2]) / self.k[0, 0], (v - self.k[1, 2]) / self.k[1, 1],
                         np.ones_like(u)], -1)

    def world(self, depth: np.ndarray, step: int = 1) -> np.ndarray:
        """Model points of a depth map, (h, w, 3)."""
        return (self.rays(step) * depth[::step, ::step, None] - self.translation) @ self.rotation

    def project(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Model points to pixels (n, 2) and depth (n,)."""
        cam = points @ self.rotation.T + self.translation
        z = cam[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            uv = (cam[:, :2] / z[:, None] * [self.k[0, 0], self.k[1, 1]]
                  + [self.k[0, 2], self.k[1, 2]])
        return uv, z


def views(pycolmap, dense: Path) -> dict[str, View]:
    """The undistorted model under `dense/sparse`, by photo name."""
    model = pycolmap.Reconstruction(str(dense / "sparse"))
    out = {}
    for image in model.images.values():
        camera = model.cameras[image.camera_id]
        pose = image.cam_from_world() if callable(image.cam_from_world) else image.cam_from_world
        fx, fy, cx, cy = (float(p) for p in camera.params[:4])
        seen = [(p.xy, p.point3D_id) for p in image.points2D if p.has_point3D()]
        rotation, translation = pose.rotation.matrix(), np.asarray(pose.translation)
        points = np.array([model.points3D[i].xyz for _, i in seen]).reshape(-1, 3)
        out[image.name] = View(
            image.name, rotation, translation, np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]]),
            (camera.width, camera.height), np.array([xy for xy, _ in seen]).reshape(-1, 2),
            (points @ rotation.T + translation)[:, 2] if len(points) else np.zeros(0))
    return out


def measured(dense: Path, view: View) -> np.ndarray:
    """The stereo's depth map for `view` (0 where it has none)."""
    path = dense / "stereo" / "depth_maps" / f"{view.name}.geometric.bin"
    return read_array(path) if path.is_file() else np.zeros(view.size[::-1], np.float32)


def cloud(dense: Path, all_views: dict[str, View], step: int = 3) -> np.ndarray:
    """Every photo's measured depth as model points."""
    points = []
    for view in all_views.values():
        depth = measured(dense, view)
        points.append(view.world(depth, step)[depth[::step, ::step] > 0])
    return np.concatenate(points)


def seen_depth(view: View, points: np.ndarray, cell: int = 4) -> np.ndarray:
    """
    Depth of `points` (other photos' measurements) as `view` sees them: per
    pixel the nearest, and only where it is the nearest of its
    neighbourhood too, so what lies behind a surface is not read through
    the gaps between the points on it.
    """
    w, h = view.size
    uv, z = view.project(points)
    ok = (z > 0.05) & (uv[:, 0] >= 0) & (uv[:, 0] < w) & (uv[:, 1] >= 0) & (uv[:, 1] < h)
    x, y, z = uv[ok, 0].astype(int), uv[ok, 1].astype(int), z[ok]
    coarse = np.full((h // cell + 1, w // cell + 1), np.inf)
    np.minimum.at(coarse, (y // cell, x // cell), z)
    front = z <= 1.04 * cv2.erode(coarse, np.ones((3, 3)))[y // cell, x // cell]
    depth = np.full((h, w), np.inf)
    np.minimum.at(depth, (y[front], x[front]), z[front])
    depth[~np.isfinite(depth)] = 0
    return depth


def fit_line(mono: np.ndarray, inverse: np.ndarray, weight: np.ndarray) -> tuple[float, float]:
    """inverse ~ a mono + b, least squares reweighted against outliers (Huber)."""
    a, b = 1.0, 0.0
    w = weight.copy()
    for _ in range(12):
        design = np.column_stack([mono, np.ones_like(mono)]) * w[:, None]
        a, b = np.linalg.lstsq(design, inverse * w, rcond=None)[0]
        residual = inverse - (a * mono + b)
        spread = 1.4826 * np.median(np.abs(residual)) + 1e-9
        w = weight / np.maximum(1.0, np.abs(residual) / (1.5 * spread))
    return float(a), float(b)


def complete(view: View, mono: np.ndarray, own: np.ndarray | None, others: np.ndarray | None,
             sigma: float = 45.0) -> np.ndarray | None:
    """
    A depth for every pixel of `view`: the network's relative depth `mono`
    (larger is nearer, nought is sky) pinned to what was measured: the
    photo's `own` stereo depth and SfM points (None: not trusted), and
    `others`, the other photos' measurements as model points. A line in
    inverse depth first, then a correction of it that varies smoothly
    across the photo, from the anchors that agree with their neighbours.
    None if fewer than thirty pixels have anything measured.
    """
    mono = mono.astype(np.float64)
    h, w = mono.shape
    inverse, weight = np.zeros((h, w)), np.zeros((h, w))
    if others is not None:
        depth = seen_depth(view, others)
        inverse[depth > 0], weight[depth > 0] = 1.0 / depth[depth > 0], 0.5
    if own is not None:
        inverse[own > 0], weight[own > 0] = 1.0 / own[own > 0], 1.0
        good = (view.sparse_depth > 0.05) & (view.sparse_depth < 2000)
        x = np.clip(view.sparse_xy[good, 0].astype(int), 0, w - 1)
        y = np.clip(view.sparse_xy[good, 1].astype(int), 0, h - 1)
        inverse[y, x], weight[y, x] = 1.0 / view.sparse_depth[good], 6.0
    anchored = weight > 0
    if anchored.sum() < 30:
        return None
    index = np.flatnonzero(anchored.ravel())
    if len(index) > 60000:
        index = np.random.default_rng(0).choice(index, 60000, replace=False)
    m, q = mono.ravel()[index], inverse.ravel()[index]
    a, b = fit_line(m, q, weight.ravel()[index])
    if a <= 0 or b < -0.25 * a * float(np.median(m)):
        # No sensible line: nearer must stay nearer. Scale alone.
        a, b = float(np.median(q / np.maximum(m, 1e-6))), 0.0
    base = np.maximum(a * mono + b, Q_MIN)
    ratio = np.where(anchored, np.clip(inverse / base, 0.4, 2.5), 0.0)
    local = (cv2.GaussianBlur(ratio * weight, (0, 0), sigma) + 1e-4) / (
        cv2.GaussianBlur(weight, (0, 0), sigma) + 1e-4)
    agree = anchored & (np.abs(ratio / local - 1) < 0.2)
    prior = 2e-3  # towards the line where nothing was measured nearby
    local = (cv2.GaussianBlur(np.where(agree, ratio * weight, 0.0), (0, 0), sigma) + prior) / (
        cv2.GaussianBlur(np.where(agree, weight, 0.0), (0, 0), sigma) + prior)
    return (1.0 / np.maximum(base * local, Q_MIN)).astype(np.float32)
