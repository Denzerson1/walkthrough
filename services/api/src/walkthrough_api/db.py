"""SQLite persistence for analytics events (M10) and upload records (M4)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Field, Session, SQLModel, create_engine

from .config import Settings


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AnalyticsEvent(SQLModel, table=True):
    """One user interaction. Aggregated for the per-project agent summary."""

    id: int | None = Field(default=None, primary_key=True)
    project_id: str = Field(index=True)
    session_id: str = Field(index=True)
    # room_viewed | floor_tried | style_tried | item_added | recolor | room_dwell
    event: str = Field(index=True)
    room_id: str | None = None
    value: str | None = None
    duration_ms: int | None = None
    created_at: datetime = Field(default_factory=_utcnow, index=True)


class UploadRecord(SQLModel, table=True):
    """A user-uploaded floor photo and the derived material (M4)."""

    id: int | None = Field(default=None, primary_key=True)
    upload_id: str = Field(index=True, unique=True)
    project_id: str = Field(index=True)
    original_key: str
    albedo_key: str | None = None
    normal_key: str | None = None
    tile_size_cm: float | None = None
    created_at: datetime = Field(default_factory=_utcnow)


_engine = None


def get_engine(settings: Settings):
    global _engine
    if _engine is None:
        url = settings.database_url
        if url.startswith("sqlite:///./"):
            # Resolve a relative sqlite path against the data root rather than the
            # cwd, so `uv run` from any directory reaches the same database file.
            # Deliberately NOT inside data_root: that directory is served by
            # /files, and the analytics database must not be downloadable.
            rel = Path(url.removeprefix("sqlite:///./"))
            target = settings.data_root.parent / "var" / rel.name
            target.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{target.as_posix()}"
        _engine = create_engine(url, connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(_engine)
    return _engine


def reset_engine() -> None:
    """Test hook: drop the cached engine so a fresh settings object is honoured."""
    global _engine
    _engine = None


def get_session(settings: Settings) -> Iterator[Session]:
    with Session(get_engine(settings)) as session:
        yield session
