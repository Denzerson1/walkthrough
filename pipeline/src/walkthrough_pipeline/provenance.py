"""
Run records and safe writes for the capture pipeline.

Every stage leaves `work/runs/<stage>.json`: what it read (input
fingerprints, and the run id of each stage it built on), the settings, the
code revision and tool versions, and what it wrote. A stage refuses to build
on an upstream stage that was rerun since — frames cut from an older video,
poses for older frames, a splat trained on older poses — instead of silently
mixing them. A failed run keeps its record, log and partial output under
`work/failed/`, and never touches the last good output.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .paths import ProjectPaths, repo_root

#: The stages and what each builds on. `align` also reads the RoomPlan scan,
#: which is fingerprinted as one of its inputs.
UPSTREAM: dict[str, tuple[str, ...]] = {
    "ingest": (),
    "frames": ("ingest",),
    "poses": ("frames",),
    "train": ("poses",),
    "align": ("train",),
    "export": ("align",),
}


class StaleInput(RuntimeError):
    """A stage's input was produced from something that has since been replaced."""


# --------------------------------------------------------------------------
# Atomic writes
# --------------------------------------------------------------------------


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` so a reader sees the old file or the new, never half."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_copy(src: Path, dst: Path) -> None:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


@contextmanager
def replace_dir(target: Path, keep_failed: Path | None = None) -> Iterator[Path]:
    """
    Build a directory beside `target` and swap it in only if the block
    finishes. Until then the old `target` is untouched. On failure the
    partial build moves to `keep_failed` (for diagnosis) or is removed.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:8]
    build = target.with_name(f".{target.name}.partial-{tag}")
    build.mkdir()
    try:
        yield build
    except BaseException:
        if keep_failed is not None:
            keep_failed.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(build), str(keep_failed))
        else:
            shutil.rmtree(build, ignore_errors=True)
        raise
    old = target.with_name(f".{target.name}.old-{tag}")
    if target.exists():
        os.replace(target, old)
    os.replace(build, target)
    shutil.rmtree(old, ignore_errors=True)


# --------------------------------------------------------------------------
# Fingerprints and versions
# --------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(path: Path | None) -> dict | None:
    """Content hash of a file, or of every file in a folder (names, sizes, contents)."""
    if path is None or not Path(path).exists():
        return None
    path = Path(path)
    if path.is_file():
        return {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
    h = hashlib.sha256()
    files = sorted(p for p in path.rglob("*") if p.is_file())
    total = 0
    for p in files:
        rel = p.relative_to(path).as_posix()
        size = p.stat().st_size
        total += size
        h.update(f"{rel}\0{size}\0{_sha256(p)}\n".encode())
    return {"name": path.name, "files": len(files), "bytes": total, "sha256": h.hexdigest()}


def code_revision() -> dict:
    """The git commit, whether the tree had local changes, and a hash of the pipeline source."""
    root = repo_root()
    source = hashlib.sha256()
    for p in sorted((root / "pipeline" / "src").rglob("*.py")):
        source.update(p.read_bytes())
    out = {"pipeline_source_sha256": source.hexdigest()[:16]}
    try:
        out["commit"] = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
            check=True, timeout=20,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--", "pipeline", "scripts"],
            capture_output=True, text=True, check=True, timeout=20,
        ).stdout
        out["dirty"] = bool(status.strip())
    except (OSError, subprocess.SubprocessError):
        out["commit"] = None  # not a git checkout, or git missing
    return out


def tool_versions() -> dict:
    """Versions of what the stages in this process use (the trainer adds its own)."""
    import cv2
    import numpy
    import scipy

    versions = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "numpy": numpy.__version__,
        "opencv": cv2.__version__,
        "scipy": scipy.__version__,
    }
    try:
        import pycolmap  # type: ignore[import-not-found]

        versions["pycolmap"] = pycolmap.__version__
    except ImportError:
        pass
    try:
        from .frames import ffmpeg_path

        first = subprocess.run([ffmpeg_path(), "-version"], capture_output=True, text=True,
                               timeout=20).stdout.splitlines()
        versions["ffmpeg"] = first[0] if first else None
    except (OSError, subprocess.SubprocessError, RuntimeError):
        versions["ffmpeg"] = None
    return versions


# --------------------------------------------------------------------------
# Run records
# --------------------------------------------------------------------------


def runs_dir(paths: ProjectPaths) -> Path:
    return paths.work / "runs"


def record_path(paths: ProjectPaths, stage: str) -> Path:
    return runs_dir(paths) / f"{stage}.json"


def load_record(paths: ProjectPaths, stage: str) -> dict | None:
    path = record_path(paths, stage)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def freshness(paths: ProjectPaths, stage: str) -> str | None:
    """
    None if `stage`'s last good run was built on the current run of every
    stage upstream of it (all the way back to ingest); otherwise why not.
    """
    record = load_record(paths, stage)
    if record is None:
        return f"`{stage}` has no run record; run `pipeline {stage}`"
    for up in UPSTREAM[stage]:
        current = load_record(paths, up)
        if current is None:
            return f"`{up}` has no run record; run `pipeline {up}`"
        if record["upstream"].get(up) != current["run_id"]:
            return (f"`{stage}` was built on an older `{up}` run; "
                    f"rerun `pipeline {stage}` (and what follows it)")
        reason = freshness(paths, up)
        if reason is not None:
            return reason
    return None


@dataclass
class StageRun:
    """One run of a stage: filled in by the stage, written when it ends."""

    paths: ProjectPaths
    stage: str
    settings: dict
    inputs: dict = field(default_factory=dict)
    upstream: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    tools: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)
    run_id: str = field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S-")
                        + uuid.uuid4().hex[:6])
    started: float = field(default_factory=time.time)

    @property
    def failed_dir(self) -> Path:
        return self.paths.work / "failed" / f"{self.stage}-{self.run_id}"

    @property
    def log_path(self) -> Path:
        return self.paths.work / "logs" / f"{self.stage}-{self.run_id}.log"

    def record(self, status: str, error: str | None = None) -> dict:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "status": status,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.started)),
            "minutes": round((time.time() - self.started) / 60, 2),
            "settings": self.settings,
            "inputs": self.inputs,
            "upstream": self.upstream,
            "outputs": self.outputs,
            "metrics": self.metrics,
            "notes": self.notes,
            "code": code_revision(),
            "tools": {**tool_versions(), **self.tools},
            "command": sys.argv,
            "log": str(self.log_path.relative_to(self.paths.root))
            if self.log_path.is_file() else None,
            "error": error,
        }


@contextmanager
def stage_run(
    paths: ProjectPaths, stage: str, settings: dict, *, check_upstream: bool = True,
    record_as: str | None = None,
) -> Iterator[StageRun]:
    """
    Run a stage under a record. Refuses (StaleInput) when an upstream stage
    is missing or was rerun after what this stage would build on. The record
    of a good run replaces `runs/<record_as or stage>.json`; a failed run's
    record goes to `failed/` with its log, and the last good record stays.
    `record_as` keeps an experiment's record apart from the stage's own.
    """
    run = StageRun(paths, stage, settings)
    if check_upstream:
        for up in UPSTREAM[stage]:
            reason = freshness(paths, up)
            if reason is not None:
                raise StaleInput(f"Cannot run `{stage}`: {reason}.")
            run.upstream[up] = load_record(paths, up)["run_id"]
    try:
        yield run
    except BaseException as exc:
        run.failed_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            run.failed_dir / "run.json",
            json.dumps(run.record("failed", "".join(traceback.format_exception(exc))), indent=2),
        )
        raise
    record = run.record("ok")
    name = record_as or stage
    atomic_write_text(runs_dir(paths) / "history" / f"{name}-{run.run_id}.json",
                      json.dumps(record, indent=2))
    atomic_write_text(record_path(paths, name), json.dumps(record, indent=2))
