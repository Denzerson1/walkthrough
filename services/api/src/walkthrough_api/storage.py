"""
Storage adapter.

The MVP runs on local disk. M10 swaps in an S3-compatible backend
(Cloudflare R2) behind the same interface, so nothing above this module
needs to change.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from .config import Settings


class StorageError(RuntimeError):
    pass


class Storage(ABC):
    """Key-addressed blob storage. Keys are POSIX-style relative paths."""

    @abstractmethod
    def read_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def write_bytes(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def public_url(self, key: str) -> str: ...

    @abstractmethod
    def local_path(self, key: str) -> Path | None:
        """Filesystem path when the backend has one, else None."""


def _safe_key(key: str) -> str:
    """
    Reject traversal. Keys come from URL path parameters, so a project id of
    '../../etc' must not escape the data root.
    """
    cleaned = key.replace("\\", "/").strip("/")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise StorageError(f"unsafe storage key: {key!r}")
    if not parts:
        raise StorageError("empty storage key")
    return "/".join(parts)


class LocalStorage(Storage):
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _safe_key(key)).resolve()
        # Defence in depth: the resolved path must stay under the root even if
        # a symlink is involved.
        if not path.is_relative_to(self.root):
            raise StorageError(f"key escapes storage root: {key!r}")
        return path

    def read_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then move, so a reader never sees a partial file.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        shutil.move(str(tmp), str(path))

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except StorageError:
            return False

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()

    def public_url(self, key: str) -> str:
        return f"/files/{_safe_key(key)}"

    def local_path(self, key: str) -> Path | None:
        return self._path(key)


class S3Storage(Storage):
    """
    Placeholder for M10. Deliberately raises rather than silently pretending
    to work — the brief forbids faked results.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        raise StorageError(
            "S3/R2 storage is not implemented yet (planned for M10). "
            "Set STORAGE_BACKEND=local."
        )

    def read_bytes(self, key: str) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def write_bytes(self, key: str, data: bytes) -> None:  # pragma: no cover
        raise NotImplementedError

    def exists(self, key: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    def delete(self, key: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def public_url(self, key: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def local_path(self, key: str) -> Path | None:  # pragma: no cover
        return None


def build_storage(settings: Settings) -> Storage:
    backend = settings.storage_backend.lower()
    if backend == "local":
        return LocalStorage(settings.data_root)
    if backend in ("s3", "r2"):
        return S3Storage(settings)
    raise StorageError(f"unknown STORAGE_BACKEND: {settings.storage_backend!r}")
