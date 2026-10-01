"""
Gaussian splat I/O.

PLY is the interchange format from gsplat. SPZ is the compressed form Spark
loads in the browser. We write PLY ourselves and shell out for SPZ, because
no permissively licensed Python SPZ writer was available at the time of
writing — see docs/LICENSES.md and docs/PIPELINE.md.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: Spherical-harmonics DC coefficient to 0-1 colour.
SH_C0 = 0.28209479177387814


@dataclass
class SplatCloud:
    """A Gaussian splat cloud, in the fields gsplat's PLY uses."""

    positions: np.ndarray  # (N, 3) float32
    colours: np.ndarray  # (N, 3) float32, 0-1 linear
    opacities: np.ndarray  # (N,) float32, 0-1
    scales: np.ndarray  # (N, 3) float32, metres
    rotations: np.ndarray  # (N, 4) float32, wxyz quaternion

    def __post_init__(self) -> None:
        n = len(self.positions)
        for name, arr, width in (
            ("colours", self.colours, 3),
            ("scales", self.scales, 3),
            ("rotations", self.rotations, 4),
        ):
            if arr.shape != (n, width):
                raise ValueError(f"{name} must be ({n}, {width}), got {arr.shape}")
        if self.opacities.shape != (n,):
            raise ValueError(f"opacities must be ({n},), got {self.opacities.shape}")

    def __len__(self) -> int:
        return len(self.positions)

    def select(self, mask: np.ndarray) -> SplatCloud:
        return SplatCloud(
            positions=self.positions[mask],
            colours=self.colours[mask],
            opacities=self.opacities[mask],
            scales=self.scales[mask],
            rotations=self.rotations[mask],
        )

    def transformed(self, matrix: np.ndarray) -> SplatCloud:
        """Apply a 4x4 transform to positions and scale lengths accordingly."""
        homo = np.hstack([self.positions, np.ones((len(self), 1), dtype=np.float64)])
        positions = (homo @ matrix.T)[:, :3].astype(np.float32)
        # Uniform scale factor from the determinant of the linear part.
        scale_factor = float(abs(np.linalg.det(matrix[:3, :3])) ** (1 / 3))
        return SplatCloud(
            positions=positions,
            colours=self.colours,
            opacities=self.opacities,
            scales=(self.scales * scale_factor).astype(np.float32),
            rotations=self.rotations,
        )

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.positions.min(axis=0), self.positions.max(axis=0)


def colour_to_sh(colour: np.ndarray) -> np.ndarray:
    """0-1 colour to the SH DC coefficient gsplat PLYs store."""
    return (colour - 0.5) / SH_C0


def sh_to_colour(dc: np.ndarray) -> np.ndarray:
    return np.clip(dc * SH_C0 + 0.5, 0.0, 1.0)


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


PLY_FIELDS = [
    "x", "y", "z",
    "nx", "ny", "nz",
    "f_dc_0", "f_dc_1", "f_dc_2",
    "opacity",
    "scale_0", "scale_1", "scale_2",
    "rot_0", "rot_1", "rot_2", "rot_3",
]


def write_ply(cloud: SplatCloud, path: Path) -> None:
    """Write the binary little-endian PLY that gsplat and Spark both read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(cloud)

    dc = colour_to_sh(cloud.colours).astype(np.float32)
    # PLY stores log scale and logit opacity, matching gsplat's convention.
    log_scales = np.log(np.maximum(cloud.scales, 1e-8)).astype(np.float32)
    logit_opacity = _logit(cloud.opacities).astype(np.float32)

    data = np.zeros(n, dtype=[(f, "<f4") for f in PLY_FIELDS])
    data["x"], data["y"], data["z"] = cloud.positions.T
    data["f_dc_0"], data["f_dc_1"], data["f_dc_2"] = dc.T
    data["opacity"] = logit_opacity
    data["scale_0"], data["scale_1"], data["scale_2"] = log_scales.T
    data["rot_0"], data["rot_1"], data["rot_2"], data["rot_3"] = cloud.rotations.T

    header = "ply\nformat binary_little_endian 1.0\n"
    header += f"element vertex {n}\n"
    header += "".join(f"property float {f}\n" for f in PLY_FIELDS)
    header += "end_header\n"

    with path.open("wb") as fh:
        fh.write(header.encode("ascii"))
        fh.write(data.tobytes())


def read_ply(path: Path) -> SplatCloud:
    """Read a splat PLY written by write_ply or gsplat."""
    with path.open("rb") as fh:
        header_bytes = b""
        while b"end_header" not in header_bytes:
            chunk = fh.readline()
            if not chunk:
                raise ValueError("not a PLY file: no end_header")
            header_bytes += chunk
        header = header_bytes.decode("ascii")
        count = 0
        fields: list[str] = []
        for line in header.splitlines():
            if line.startswith("element vertex"):
                count = int(line.split()[-1])
            elif line.startswith("property float"):
                fields.append(line.split()[-1])
        data = np.frombuffer(
            fh.read(count * 4 * len(fields)),
            dtype=[(f, "<f4") for f in fields],
            count=count,
        )

    positions = np.stack([data["x"], data["y"], data["z"]], axis=1).astype(np.float32)
    dc = np.stack([data["f_dc_0"], data["f_dc_1"], data["f_dc_2"]], axis=1)
    scales = np.exp(
        np.stack([data["scale_0"], data["scale_1"], data["scale_2"]], axis=1)
    ).astype(np.float32)
    rotations = np.stack(
        [data["rot_0"], data["rot_1"], data["rot_2"], data["rot_3"]], axis=1
    ).astype(np.float32)
    return SplatCloud(
        positions=positions,
        colours=sh_to_colour(dc).astype(np.float32),
        opacities=_sigmoid(np.asarray(data["opacity"])).astype(np.float32),
        scales=scales,
        rotations=rotations,
    )


class SpzUnavailable(RuntimeError):
    pass


def write_spz(ply_path: Path, spz_path: Path) -> None:
    """
    Compress a PLY to SPZ.

    Requires the `spz` CLI on PATH. We keep the PLY either way, as the brief
    requires, so a missing SPZ encoder degrades the download size but never
    loses data.
    """
    exe = shutil.which("spz")
    if not exe:
        raise SpzUnavailable(
            "The `spz` CLI is not on PATH, so the scene cannot be compressed. "
            "The PLY is still written. See docs/PIPELINE.md for install steps."
        )
    spz_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([exe, "encode", str(ply_path), "-o", str(spz_path)], check=True)


def cap_splat_count(cloud: SplatCloud, max_splats: int, seed: int = 0) -> SplatCloud:
    """
    Reduce the splat count to fit the 8 GB VRAM budget (docs/SPEC.md §2).

    Keeps the most opaque, largest splats — they contribute most of the
    visible surface — and samples the remainder randomly so fine detail is
    thinned rather than removed in blocks.
    """
    n = len(cloud)
    if n <= max_splats:
        return cloud

    importance = cloud.opacities * cloud.scales.max(axis=1)
    keep_top = max_splats // 2
    top_idx = np.argsort(-importance)[:keep_top]

    remaining = np.setdiff1d(np.arange(n), top_idx, assume_unique=False)
    rng = np.random.default_rng(seed)
    sample = rng.choice(remaining, size=max_splats - keep_top, replace=False)

    mask = np.zeros(n, dtype=bool)
    mask[top_idx] = True
    mask[sample] = True
    return cloud.select(mask)
