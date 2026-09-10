"""Typed, environment-driven application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, RedisDsn, SecretStr
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
    database_path: Path = Path("data/gateway.sqlite")
    state_store_backend: Literal["redis", "memory"] = "redis"
    redis_operation_timeout_seconds: float = 2.0
    state_ttl_seconds: int = 86_400
    approval_timeout_seconds: int = 300
    sandbox_runtime: Literal["gvisor", "hardened-docker"] = "hardened-docker"
    sandbox_image: str = "mcp-gateway-tool:local"
    sandbox_output_limit_bytes: int = 1_048_576
    sandbox_startup_timeout_seconds: float = 30.0
    audit_log_path: Path = Path("data/audit.jsonl")
    repair_enabled: bool = False
    repair_llm_url: AnyHttpUrl = AnyHttpUrl("http://localhost:4000/v1/chat/completions")
    repair_llm_model: str = "repair-model"
    repair_llm_api_key: SecretStr | None = None
    repair_llm_timeout_seconds: float = 15.0
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    http_path: str = "/mcp"
    http_auth_token: SecretStr | None = None
    http_max_request_body_bytes: int = 4 * 1024 * 1024
    http_session_idle_timeout_seconds: float = 1800.0
    http_max_sessions: int = 10_000


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
