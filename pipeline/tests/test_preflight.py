"""`pipeline check` on a synthetic Stray capture: what it measures, and what it flags."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from walkthrough_pipeline import stray
from walkthrough_pipeline.check import observation_plot, preflight, trajectory_plot
from walkthrough_pipeline.paths import ProjectPaths

sys.path.insert(0, str(Path(__file__).parent))
import test_stray as ts  # noqa: E402


def _project(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path / "p")
    paths.ensure()
    ts._write_capture(paths.input / "stray")
    real_load = stray.load
    # The synthetic capture has no real video to probe: give its size.
    monkeypatch.setattr(stray, "load", lambda root, size=None: real_load(root, size=ts.VIDEO))
    return paths


def test_a_clean_capture_measures_level_and_in_format(tmp_path, monkeypatch):
    paths = _project(tmp_path, monkeypatch)
    report = preflight(paths)
    assert report["capture"] == "stray"
    assert report["numbering"]["gap_count"] == 0
    assert report["numbering"]["missing_depth_count"] == 0
    assert report["timestamps"]["fps"] == 60.0 and report["timestamps"]["monotonic"]
    assert report["depth_aspect_mismatch"] < 0.01
    assert report["intrinsics"]["fx_over_fy"] == 1.0
    assert abs(report["intrinsics"]["cx_offset"]) < 0.01
    assert report["poses"]["max_orthonormal_error"] < 1e-6
    assert report["poses"]["floor"]["tilt_deg"] < 0.5
    assert report["poses"]["floor"]["camera_height_median_m"] == np.round(1.4, 3)
    assert (paths.work / "preflight" / "trajectory.png").is_file()
    assert (paths.work / "preflight" / "report.json").is_file()


def test_gaps_missing_depth_and_odd_intrinsics_are_named(tmp_path, monkeypatch):
    paths = _project(tmp_path, monkeypatch)
    root = paths.input / "stray"
    (root / "depth" / "000010.png").unlink()
    rows = np.loadtxt(root / "odometry.csv", delimiter=",", skiprows=1)
    rows = np.delete(rows, [40, 41], axis=0)  # two dropped frames: 39 -> 42
    rows[:, 11] *= 1.2  # cx 20% off centre
    np.savetxt(root / "odometry.csv", rows, delimiter=",",
               header="timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy", comments="")
    report = preflight(paths)
    text = " ".join(report["warnings"])
    assert report["numbering"]["gaps"] == [[39, 42]]
    assert report["numbering"]["missing_depth"] == [10]
    assert "Frame numbers jump" in text and "no depth" in text
    assert "principal point" in text
    assert report["timestamps"]["hitches"] == 1


def test_a_capture_whose_up_is_not_y_is_caught(tmp_path, monkeypatch):
    """
    Poses in a frame with Z up (a convention mix-up). In a box room a wall
    then passes for a level floor, so the camera's viewing direction tells.
    """
    paths = _project(tmp_path, monkeypatch)
    root = paths.input / "stray"
    rows = np.loadtxt(root / "odometry.csv", delimiter=",", skiprows=1)
    from scipy.spatial.transform import Rotation

    turn = Rotation.from_euler("x", 90, degrees=True)
    rows[:, 2:5] = turn.apply(rows[:, 2:5])
    rows[:, 5:9] = (turn * Rotation.from_quat(rows[:, 5:9])).as_quat()
    np.savetxt(root / "odometry.csv", rows, delimiter=",",
               header="timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy", comments="")
    report = preflight(paths)
    assert any("not Y-up" in w for w in report["warnings"])


def test_pictures_are_drawn(tmp_path):
    rng = np.random.default_rng(0)
    c2w = np.tile(np.eye(4), (50, 1, 1))
    c2w[:, 0, 3] = np.linspace(0, 3, 50)
    trajectory_plot(c2w, rng.uniform(-1, 4, (5000, 3)), tmp_path / "t.png")
    assert cv2.imread(str(tmp_path / "t.png")).shape == (1000, 1000, 3)
    stats = observation_plot({f"f{i}": c for i, c in enumerate([50, 400, 800, 20])},
                             tmp_path / "o.png")
    assert stats["frames_under_100"] == 2 and stats["max"] == 800


def test_rerun_recording_holds_frames_points_and_plan(tmp_path):
    """The optional Rerun export (diagnostics extra), on the synthetic capture."""
    import pytest

    pytest.importorskip("rerun")
    from walkthrough_pipeline.rerun_export import export_recording

    cap = stray.load(ts._write_capture(tmp_path / "cap") and tmp_path / "cap", size=ts.VIDEO)
    paths = ProjectPaths(tmp_path / "p")
    paths.ensure()
    stray.build_dataset(cap, ts._frames(tmp_path, cap), paths.work / "undistorted")
    summary = export_recording(paths, paths.work / "diagnostics" / "p.rrd", every=5)
    assert summary["frames"] == 9 and summary["points"] > 1000
    assert (paths.work / "diagnostics" / "p.rrd").stat().st_size > 100_000
