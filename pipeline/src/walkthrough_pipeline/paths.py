"""Project directory layout (docs/BRIEF.md §4)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @classmethod
    def for_project(cls, repo: Path, project_id: str) -> ProjectPaths:
        return cls(repo / "data" / "projects" / project_id)

    @property
    def input(self) -> Path:
        return self.root / "input"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def scene(self) -> Path:
        return self.root / "scene"

    @property
    def manifest(self) -> Path:
        return self.scene / "manifest.json"

    def ensure(self) -> None:
        for d in (self.input, self.work, self.scene):
            d.mkdir(parents=True, exist_ok=True)
