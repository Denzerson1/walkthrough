"""PLY and SPZ round trips, and spherical-harmonic rotation."""

from __future__ import annotations

import gzip
import struct

import numpy as np
import pytest
from walkthrough_pipeline.align import _axis_angle
from walkthrough_pipeline.splat import (
    SplatCloud,
    read_ply,
    rotate_sh,
    sh_basis,
    write_ply,
    write_spz,
)


def _cloud(n: int = 500, degree: int = 3, seed: int = 0) -> SplatCloud:
    rng = np.random.default_rng(seed)
    q = rng.normal(size=(n, 4))
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    k = {0: 0, 1: 3, 2: 8, 3: 15}[degree]
    return SplatCloud(
        positions=rng.uniform(-6, 6, size=(n, 3)).astype(np.float32),
        colours=rng.uniform(-0.1, 1.1, size=(n, 3)).astype(np.float32),
        opacities=rng.uniform(0.01, 0.99, size=n).astype(np.float32),
        scales=np.exp(rng.uniform(-7, 0, size=(n, 3))).astype(np.float32),
        rotations=q.astype(np.float32),
        sh_rest=rng.uniform(-0.4, 0.4, size=(n, k, 3)).astype(np.float32) if k else None,
    )


def _eval_sh(cloud: SplatCloud, directions: np.ndarray) -> np.ndarray:
    """View-dependent colour term per splat for one direction per splat."""
    basis = sh_basis(directions, cloud.sh_degree)  # (N, K)
    return np.einsum("nk,nkc->nc", basis, cloud.sh_rest)


# --------------------------------------------------------------------------
# PLY
# --------------------------------------------------------------------------


def test_ply_round_trip_keeps_higher_order_sh(tmp_path):
    cloud = _cloud()
    write_ply(cloud, tmp_path / "s.ply")
    back = read_ply(tmp_path / "s.ply")
    assert back.sh_degree == 3
    np.testing.assert_allclose(back.sh_rest, cloud.sh_rest, atol=1e-6)
    np.testing.assert_allclose(back.colours, cloud.colours, atol=1e-5)
    np.testing.assert_allclose(back.positions, cloud.positions, atol=1e-6)


def test_ply_f_rest_is_channel_major(tmp_path):
    """The layout gsplat writes: f_rest_0..14 red, 15..29 green, 30..44 blue."""
    cloud = _cloud(n=1)
    cloud.sh_rest[:] = 0
    cloud.sh_rest[0, 0, 1] = 7.0  # first coefficient, green channel
    write_ply(cloud, tmp_path / "s.ply")
    raw = (tmp_path / "s.ply").read_bytes()
    header, body = raw.split(b"end_header\n")
    fields = [ln.split()[-1].decode() for ln in header.splitlines() if ln.startswith(b"property")]
    values = np.frombuffer(body, dtype="<f4")
    assert values[fields.index("f_rest_15")] == 7.0


def test_colour_is_not_clipped_on_read(tmp_path):
    """A trained DC term can sit outside 0-1; clipping it shifts the colour."""
    cloud = _cloud(n=4, degree=0)
    cloud.colours[0] = [1.08, -0.05, 0.5]
    write_ply(cloud, tmp_path / "s.ply")
    back = read_ply(tmp_path / "s.ply")
    np.testing.assert_allclose(back.colours[0], [1.08, -0.05, 0.5], atol=1e-5)


# --------------------------------------------------------------------------
# SH rotation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("degree", [1, 2, 3])
def test_rotated_sh_matches_original_seen_from_the_rotated_direction(degree):
    """
    Rotating the scene by R must not change what a viewer sees: the new
    coefficients evaluated at R·d equal the old ones evaluated at d.
    """
    cloud = _cloud(n=200, degree=degree, seed=degree)
    rotation = _axis_angle(np.array([0.3, 1.0, -0.5]), 1.1)
    rotated = cloud.transformed(np.block([[rotation, np.zeros((3, 1))], [0, 0, 0, 1]]))

    rng = np.random.default_rng(9)
    d = rng.normal(size=(200, 3))
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    np.testing.assert_allclose(
        _eval_sh(rotated, d @ rotation.T), _eval_sh(cloud, d), atol=1e-5
    )


def test_sh_rotation_by_identity_is_a_no_op():
    sh = _cloud(n=10).sh_rest
    np.testing.assert_allclose(rotate_sh(sh, np.eye(3)), sh, atol=1e-6)


def test_uniform_scale_leaves_sh_alone():
    cloud = _cloud(n=10)
    scaled = cloud.transformed(np.diag([2.5, 2.5, 2.5, 1.0]))
    np.testing.assert_allclose(scaled.sh_rest, cloud.sh_rest, atol=1e-6)
    np.testing.assert_allclose(scaled.scales, cloud.scales * 2.5, rtol=1e-5)


# --------------------------------------------------------------------------
# SPZ — decoded with a transcription of the reference reader
# (nianticlabs/spz load-spz.cc: deserializePackedGaussians / unpackGaussians)
# --------------------------------------------------------------------------


def _read_spz(path) -> dict:
    raw = gzip.decompress(path.read_bytes())
    magic, version, n, degree, frac, flags, _ = struct.unpack_from("<IIIBBBB", raw, 0)
    assert magic == 0x5053474E
    off = 16
    k = {0: 0, 1: 3, 2: 8, 3: 15}[degree]

    def take(size):
        nonlocal off
        chunk = np.frombuffer(raw, dtype=np.uint8, count=size, offset=off)
        off += size
        return chunk

    pos = take(n * 9).reshape(n, 3, 3).astype(np.int32)
    fixed = pos[..., 0] | (pos[..., 1] << 8) | (pos[..., 2] << 16)
    fixed = np.where(fixed & 0x800000, fixed | ~0xFFFFFF, fixed)  # sign-extend 24 bits
    alphas = take(n).astype(np.float64) / 255.0
    colours = take(n * 3).reshape(n, 3).astype(np.float64)
    scales = take(n * 3).reshape(n, 3).astype(np.float64) / 16.0 - 10.0
    rot = take(n * 4).reshape(n, 4).astype(np.uint32)
    sh = take(n * k * 3).reshape(n, k, 3).astype(np.float64)
    assert off == len(raw)

    quats = np.zeros((n, 4))  # xyzw
    for row in range(n):
        comp = int(rot[row, 0] | (rot[row, 1] << 8) | (rot[row, 2] << 16) | (rot[row, 3] << 24))
        largest = comp >> 30
        total = 0.0
        for i in (3, 2, 1, 0):
            if i == largest:
                continue
            mag, neg = comp & 511, (comp >> 9) & 1
            comp >>= 10
            v = np.sqrt(0.5) * mag / 511.0
            quats[row, i] = -v if neg else v
            total += v * v
        quats[row, largest] = np.sqrt(max(0.0, 1.0 - total))

    return {
        "version": version,
        "degree": degree,
        "flags": flags,
        "positions": fixed / float(1 << frac),
        "opacities": alphas,
        "dc": (colours / 255.0 - 0.5) / 0.15,
        "log_scales": scales,
        "quats_wxyz": quats[:, [3, 0, 1, 2]],
        "sh_rest": (sh - 128.0) / 128.0,
    }


def test_spz_round_trip_within_quantisation(tmp_path):
    cloud = _cloud()
    write_spz(cloud, tmp_path / "s.spz")
    got = _read_spz(tmp_path / "s.spz")

    assert got["version"] == 3 and got["degree"] == 3 and got["flags"] == 0
    np.testing.assert_allclose(got["positions"], cloud.positions, atol=1 / 4096)
    np.testing.assert_allclose(got["opacities"], cloud.opacities, atol=1 / 255)
    dc = (cloud.colours - 0.5) / 0.28209479177387814
    np.testing.assert_allclose(got["dc"], dc, atol=1 / (0.15 * 255))
    np.testing.assert_allclose(got["log_scales"], np.log(cloud.scales), atol=1 / 32 + 1e-6)
    # Bucketed to 5 bits (degree 1) and 4 bits (higher bands) of 8.
    np.testing.assert_allclose(got["sh_rest"][:, :3], cloud.sh_rest[:, :3], atol=8 / 128)
    np.testing.assert_allclose(got["sh_rest"][:, 3:], cloud.sh_rest[:, 3:], atol=16 / 128)


def test_spz_rotations_survive_smallest_three_packing(tmp_path):
    cloud = _cloud()
    write_spz(cloud, tmp_path / "s.spz")
    q = _read_spz(tmp_path / "s.spz")["quats_wxyz"]
    # q and -q are the same rotation.
    dots = np.abs(np.sum(q * cloud.rotations, axis=1))
    assert dots.min() > 0.999


def test_spz_handles_negative_coordinates_and_no_sh(tmp_path):
    cloud = _cloud(n=50, degree=0)
    cloud.positions[0] = [-3.25, -0.001, -2047.0]
    write_spz(cloud, tmp_path / "s.spz")
    got = _read_spz(tmp_path / "s.spz")
    assert got["degree"] == 0
    np.testing.assert_allclose(got["positions"][0], [-3.25, -0.001, -2047.0], atol=1 / 4096)


def test_spz_is_much_smaller_than_ply(tmp_path):
    cloud = _cloud(n=20_000)
    write_ply(cloud, tmp_path / "s.ply")
    write_spz(cloud, tmp_path / "s.spz")
    assert (tmp_path / "s.spz").stat().st_size * 4 < (tmp_path / "s.ply").stat().st_size
