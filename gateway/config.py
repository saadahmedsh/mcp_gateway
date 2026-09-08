"""Typed, environment-driven application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Gateway settings loaded from ``GATEWAY_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")
    opa_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8181")
    otlp_endpoint: AnyHttpUrl = AnyHttpUrl("http://localhost:4317")
    jaeger_ui_url: AnyHttpUrl = AnyHttpUrl("http://localhost:16686")
    approval_timeout_seconds: int = 300
    sandbox_runtime: Literal["gvisor", "hardened-docker"] = "hardened-docker"
    audit_log_path: Path = Path("data/audit.jsonl")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
