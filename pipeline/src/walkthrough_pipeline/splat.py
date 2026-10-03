"""
Gaussian splat I/O.

PLY is the interchange format from gsplat. SPZ is the compressed form Spark
loads in the browser, roughly 10x smaller. Both are written here in numpy;
the SPZ writer follows Niantic's reference implementation (MIT,
github.com/nianticlabs/spz, src/cc/load-spz.cc).
"""

from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: Spherical-harmonics DC coefficient to 0-1 colour.
SH_C0 = 0.28209479177387814


@dataclass
class SplatCloud:
    """A Gaussian splat cloud, in the fields gsplat's PLY uses."""

    positions: np.ndarray  # (N, 3) float32
    colours: np.ndarray  # (N, 3) float32, 0-1 linear (may stray outside 0-1)
    opacities: np.ndarray  # (N,) float32, 0-1
    scales: np.ndarray  # (N, 3) float32, metres
    rotations: np.ndarray  # (N, 4) float32, wxyz quaternion
    #: Higher-order SH, (N, K, 3) with K = 3, 8 or 15 for degree 1-3. None for
    #: a cloud with colour only. This is the view-dependent shading a trained
    #: scene has and a generated one does not; dropping it flattens every
    #: highlight and reflection.
    sh_rest: np.ndarray | None = None

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
        if self.sh_rest is not None and (
            self.sh_rest.ndim != 3
            or self.sh_rest.shape[0] != n
            or self.sh_rest.shape[2] != 3
            or self.sh_rest.shape[1] not in SH_REST_COUNTS.values()
        ):
            raise ValueError(f"sh_rest must be (N, 3|8|15, 3), got {self.sh_rest.shape}")

    def __len__(self) -> int:
        return len(self.positions)

    @property
    def sh_degree(self) -> int:
        if self.sh_rest is None:
            return 0
        return next(d for d, k in SH_REST_COUNTS.items() if k == self.sh_rest.shape[1])

    def select(self, mask: np.ndarray) -> SplatCloud:
        return SplatCloud(
            positions=self.positions[mask],
            colours=self.colours[mask],
            opacities=self.opacities[mask],
            scales=self.scales[mask],
            rotations=self.rotations[mask],
            sh_rest=None if self.sh_rest is None else self.sh_rest[mask],
        )

    def transformed(self, matrix: np.ndarray) -> SplatCloud:
        """
        Apply a 4x4 similarity transform to positions, scales, orientations
        and spherical harmonics.

        The orientations matter: a Gaussian is an oriented ellipsoid fitted to
        a surface. Rotating only the centres and leaving the quaternions alone
        leaves every splat tilted away from the surface it belongs to, which
        shows up as streaking after gravity alignment. The SH matter for the
        same reason: they are defined over view directions, so an unrotated
        set puts every highlight on the wrong side.
        """
        homo = np.hstack([self.positions, np.ones((len(self), 1), dtype=np.float64)])
        positions = (homo @ matrix.T)[:, :3].astype(np.float32)

        linear = np.asarray(matrix, dtype=np.float64)[:3, :3]
        # Uniform scale factor from the determinant of the linear part.
        scale_factor = float(abs(np.linalg.det(linear)) ** (1 / 3))
        rotation = linear / scale_factor if scale_factor > 1e-12 else linear

        return SplatCloud(
            positions=positions,
            colours=self.colours,
            opacities=self.opacities,
            scales=(self.scales * scale_factor).astype(np.float32),
            rotations=rotate_quaternions(self.rotations, rotation),
            sh_rest=None if self.sh_rest is None else rotate_sh(self.sh_rest, rotation),
        )

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.positions.min(axis=0), self.positions.max(axis=0)


#: Higher-order SH coefficients per channel, by degree.
SH_REST_COUNTS = {1: 3, 2: 8, 3: 15}


def sh_basis(directions: np.ndarray, degree: int) -> np.ndarray:
    """
    Real SH basis functions of bands 1..degree at unit `directions`, (M, K).

    Constants and ordering are the ones gsplat and the original 3DGS renderer
    evaluate, so coefficients expressed in this basis are the ones in the PLY.
    """
    x, y, z = directions[:, 0], directions[:, 1], directions[:, 2]
    c1 = 0.4886025119029199
    cols = [-c1 * y, c1 * z, -c1 * x]
    if degree >= 2:
        xx, yy, zz = x * x, y * y, z * z
        cols += [
            1.0925484305920792 * x * y,
            -1.0925484305920792 * y * z,
            0.31539156525252005 * (2 * zz - xx - yy),
            -1.0925484305920792 * x * z,
            0.5462742152960396 * (xx - yy),
        ]
    if degree >= 3:
        cols += [
            -0.5900435899266435 * y * (3 * xx - yy),
            2.890611442640554 * x * y * z,
            -0.4570457994644658 * y * (4 * zz - xx - yy),
            0.3731763325901154 * z * (2 * zz - 3 * xx - 3 * yy),
            -0.4570457994644658 * x * (4 * zz - xx - yy),
            1.445305721320277 * z * (xx - yy),
            -0.5900435899266435 * x * (xx - 3 * yy),
        ]
    return np.stack(cols, axis=1)


def sh_rotation_matrix(rotation: np.ndarray, degree: int) -> np.ndarray:
    """
    (K, K) matrix taking SH coefficients of a scene to those of the scene
    rotated by `rotation`.

    Solved numerically rather than from Wigner-D formulas: each SH band is
    closed under rotation, so requiring f'(R d) = f(d) at enough sample
    directions determines the matrix exactly, and it is expressed in exactly
    the basis `sh_basis` evaluates — no sign-convention bookkeeping.
    """
    rng = np.random.default_rng(0)
    dirs = rng.normal(size=(64, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    before = sh_basis(dirs, degree)  # Y(d)
    after = sh_basis(dirs @ np.asarray(rotation, dtype=np.float64).T, degree)  # Y(R d)
    # c' = M c with Y(R d) c' = Y(d) c for every c, so Y(R d) M = Y(d).
    m, *_ = np.linalg.lstsq(after, before, rcond=None)
    return m


def rotate_sh(sh_rest: np.ndarray, rotation: np.ndarray) -> np.ndarray:
    degree = next(d for d, k in SH_REST_COUNTS.items() if k == sh_rest.shape[1])
    m = sh_rotation_matrix(rotation, degree)
    return np.einsum("kj,njc->nkc", m, sh_rest.astype(np.float64)).astype(np.float32)


def crop_to_box(cloud: SplatCloud, lo: np.ndarray, hi: np.ndarray) -> SplatCloud:
    """Keep only splats whose centres lie inside the axis-aligned box."""
    inside = np.all((cloud.positions >= lo) & (cloud.positions <= hi), axis=1)
    return cloud.select(inside)


def matrix_to_quaternion(m: np.ndarray) -> np.ndarray:
    """Rotation matrix to a wxyz quaternion, via the numerically stable branch."""
    trace = float(m[0, 0] + m[1, 1] + m[2, 2])
    if trace > 0:
        s_ = np.sqrt(trace + 1.0) * 2
        w = 0.25 * s_
        x = (m[2, 1] - m[1, 2]) / s_
        y = (m[0, 2] - m[2, 0]) / s_
        z = (m[1, 0] - m[0, 1]) / s_
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s_ = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w = (m[2, 1] - m[1, 2]) / s_
        x = 0.25 * s_
        y = (m[0, 1] + m[1, 0]) / s_
        z = (m[0, 2] + m[2, 0]) / s_
    elif m[1, 1] > m[2, 2]:
        s_ = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w = (m[0, 2] - m[2, 0]) / s_
        x = (m[0, 1] + m[1, 0]) / s_
        y = 0.25 * s_
        z = (m[1, 2] + m[2, 1]) / s_
    else:
        s_ = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w = (m[1, 0] - m[0, 1]) / s_
        x = (m[0, 2] + m[2, 0]) / s_
        y = (m[1, 2] + m[2, 1]) / s_
        z = 0.25 * s_
    q = np.array([w, x, y, z], dtype=np.float64)
    return q / np.linalg.norm(q)


def quaternion_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Hamilton product of wxyz quaternions.

    `a` is a single quaternion (4,), `b` is an array of them (N, 4), and the
    result is a[i] applied after b[i] — i.e. the world rotation composed on
    top of each splat's own orientation.
    """
    aw, ax, ay, az = (float(v) for v in a)
    bw, bx, by, bz = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
    return np.stack(
        [
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ],
        axis=1,
    )


def rotate_quaternions(rotations: np.ndarray, rotation_matrix: np.ndarray) -> np.ndarray:
    """Compose a world rotation onto every splat orientation."""
    rotations = np.asarray(rotations, dtype=np.float64)
    if len(rotations) == 0:
        return rotations.astype(np.float32)
    q = matrix_to_quaternion(np.asarray(rotation_matrix, dtype=np.float64))
    out = quaternion_multiply(q, rotations)
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    norms[norms < 1e-12] = 1.0
    return (out / norms).astype(np.float32)


def colour_to_sh(colour: np.ndarray) -> np.ndarray:
    """0-1 colour to the SH DC coefficient gsplat PLYs store."""
    return (colour - 0.5) / SH_C0


def sh_to_colour(dc: np.ndarray) -> np.ndarray:
    # Not clipped: a trained DC term may sit outside 0-1 and the higher bands
    # pull it back, so clipping here would shift colours on a round trip.
    return dc * SH_C0 + 0.5


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _ply_fields(sh_rest_count: int) -> list[str]:
    # f_rest is channel-major — all red coefficients, then green, then blue —
    # which is how gsplat's exporter and the original 3DGS lay it out.
    return [
        "x", "y", "z",
        "nx", "ny", "nz",
        "f_dc_0", "f_dc_1", "f_dc_2",
        *(f"f_rest_{i}" for i in range(3 * sh_rest_count)),
        "opacity",
        "scale_0", "scale_1", "scale_2",
        "rot_0", "rot_1", "rot_2", "rot_3",
    ]


def write_ply(cloud: SplatCloud, path: Path) -> None:
    """Write the binary little-endian PLY that gsplat and Spark both read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(cloud)
    k = 0 if cloud.sh_rest is None else cloud.sh_rest.shape[1]
    fields = _ply_fields(k)

    dc = colour_to_sh(cloud.colours).astype(np.float32)
    # PLY stores log scale and logit opacity, matching gsplat's convention.
    log_scales = np.log(np.maximum(cloud.scales, 1e-8)).astype(np.float32)
    logit_opacity = _logit(cloud.opacities).astype(np.float32)

    data = np.zeros(n, dtype=[(f, "<f4") for f in fields])
    data["x"], data["y"], data["z"] = cloud.positions.T
    data["f_dc_0"], data["f_dc_1"], data["f_dc_2"] = dc.T
    if cloud.sh_rest is not None:
        channel_major = cloud.sh_rest.transpose(0, 2, 1).reshape(n, 3 * k)
        for i in range(3 * k):
            data[f"f_rest_{i}"] = channel_major[:, i]
    data["opacity"] = logit_opacity
    data["scale_0"], data["scale_1"], data["scale_2"] = log_scales.T
    data["rot_0"], data["rot_1"], data["rot_2"], data["rot_3"] = cloud.rotations.T

    header = "ply\nformat binary_little_endian 1.0\n"
    header += f"element vertex {n}\n"
    header += "".join(f"property float {f}\n" for f in fields)
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
    rest = [f for f in fields if f.startswith("f_rest_")]
    sh_rest = None
    if rest:
        k = len(rest) // 3
        channel_major = np.stack([data[f"f_rest_{i}"] for i in range(3 * k)], axis=1)
        sh_rest = channel_major.reshape(count, 3, k).transpose(0, 2, 1).astype(np.float32)
    return SplatCloud(
        positions=positions,
        colours=sh_to_colour(dc).astype(np.float32),
        opacities=_sigmoid(np.asarray(data["opacity"])).astype(np.float32),
        scales=scales,
        rotations=rotations,
        sh_rest=sh_rest,
    )


#: "NGSP", little-endian.
SPZ_MAGIC = 0x5053474E
#: Version 3 is the newest gzip-framed version; v4 moved to ZSTD streams.
#: Every SPZ reader, Spark's included, reads v3.
SPZ_VERSION = 3
SPZ_FRACTIONAL_BITS = 12
SPZ_COLOR_SCALE = 0.15


def write_spz(cloud: SplatCloud, path: Path) -> None:
    """
    Write a cloud as SPZ v3, quantised exactly as the reference encoder does.

    Coordinates are stored as-is: SPZ's native frame is RUB (three.js), which
    is already ours — Y up, metres — so no axis flip is applied.
    """
    n = len(cloud)
    degree = cloud.sh_degree

    fixed = np.round(cloud.positions.astype(np.float64) * (1 << SPZ_FRACTIONAL_BITS))
    fixed = fixed.astype(np.int64).reshape(-1)
    positions = np.stack([fixed & 0xFF, (fixed >> 8) & 0xFF, (fixed >> 16) & 0xFF], axis=1)

    log_scales = np.log(np.maximum(cloud.scales.astype(np.float64), 1e-30))
    scales = _to_u8((log_scales + 10.0) * 16.0)
    alphas = _to_u8(cloud.opacities.astype(np.float64) * 255.0)
    dc = colour_to_sh(cloud.colours.astype(np.float64))
    colours = _to_u8(dc * (SPZ_COLOR_SCALE * 255.0) + 0.5 * 255.0)
    rotations = _pack_smallest_three(cloud.rotations)

    if cloud.sh_rest is None:
        sh = np.zeros(0, dtype=np.uint8)
    else:
        # Coefficient-major with RGB interleaved, as the format stores it.
        # Degree 1 keeps 5 bits, higher bands 4: the reference defaults.
        sh_vals = cloud.sh_rest.astype(np.float64)
        bucket = np.full(sh_vals.shape[1], 1 << (8 - 4))
        bucket[:3] = 1 << (8 - 5)
        sh = _quantize_sh(sh_vals, bucket[None, :, None]).reshape(-1)

    header = struct.pack(
        "<IIIBBBB", SPZ_MAGIC, SPZ_VERSION, n, degree, SPZ_FRACTIONAL_BITS, 0, 0
    )
    body = b"".join(
        [
            header,
            positions.astype(np.uint8).tobytes(),
            alphas.tobytes(),
            colours.tobytes(),
            scales.tobytes(),
            rotations.tobytes(),
            sh.tobytes(),
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body, compresslevel=6))


def _to_u8(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x), 0, 255).astype(np.uint8)


def _quantize_sh(x: np.ndarray, bucket: np.ndarray) -> np.ndarray:
    # Quantise to 8 bits, then snap to the bucket centre; 0 stays exact.
    q = np.round(x * 128.0) + 128.0
    q = np.floor((q + bucket // 2) / bucket) * bucket
    return np.clip(q, 0, 255).astype(np.uint8)


def _pack_smallest_three(rotations_wxyz: np.ndarray) -> np.ndarray:
    """
    SPZ v3 rotation: drop the largest quaternion component (its index goes in
    the top two bits), store the other three as sign + 9-bit magnitude.
    """
    q = rotations_wxyz.astype(np.float64)[:, [1, 2, 3, 0]]  # xyzw, as SPZ stores
    q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    largest = np.argmax(np.abs(q), axis=1)
    negate = q[np.arange(len(q)), largest] < 0

    comp = largest.astype(np.uint32)
    for i in range(4):
        use = largest != i
        neg = ((q[:, i] < 0) ^ negate).astype(np.uint32)
        mag = (511.0 * (np.abs(q[:, i]) / np.sqrt(0.5)) + 0.5).astype(np.uint32)
        packed = (comp << 10) | (neg << 9) | mag
        comp = np.where(use, packed, comp)
    return comp.astype("<u4").view(np.uint8).reshape(-1, 4)


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

    # Only the top-k *set* matters, never its order, so partition rather than
    # sort: at 1.5 M splats that is ~14 ms instead of ~160 ms. Taking the
    # complement from the mask avoids setdiff1d's 12 MB arange and its sort.
    top_idx = np.argpartition(-importance, keep_top)[:keep_top]
    mask = np.zeros(n, dtype=bool)
    mask[top_idx] = True

    remaining = np.nonzero(~mask)[0]
    rng = np.random.default_rng(seed)
    sample = rng.choice(remaining, size=max_splats - keep_top, replace=False)
    mask[sample] = True
    return cloud.select(mask)
