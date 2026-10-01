"""Settings loaded from the environment / .env (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Editor auth
    editor_password: str = "change-me"
    session_secret: str = "change-me"

    # Server
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"
    database_url: str = "sqlite:///./data/walkthrough.db"

    # Storage
    storage_backend: str = "local"
    storage_local_root: str = "./data"
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_public_base_url: str = ""

    # Assistant (M5)
    anthropic_api_key: str = ""
    assistant_model: str = "claude-haiku-4-5-20251001"
    assistant_max_tokens: int = 1024
    assistant_rate_limit_per_hour: int = 60

    # Uploads (M4)
    max_upload_bytes: int = 10 * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def data_root(self) -> Path:
        root = Path(self.storage_local_root)
        return root if root.is_absolute() else (REPO_ROOT / root).resolve()

    @property
    def catalog_root(self) -> Path:
        return REPO_ROOT / "catalog"


@lru_cache
def get_settings() -> Settings:
    return Settings()
