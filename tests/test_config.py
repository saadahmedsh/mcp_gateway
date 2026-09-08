"""Phase 0 configuration smoke tests."""

from pathlib import Path

import pytest

from gateway.config import Settings, get_settings


def test_settings_have_safe_local_defaults() -> None:
    # `_env_file` is a supported pydantic-settings runtime keyword that is not
    # represented in BaseSettings' static constructor signature.
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert str(settings.redis_url) == "redis://localhost:6379/0"
    assert str(settings.opa_url) == "http://localhost:8181/"
    assert settings.sandbox_runtime == "hardened-docker"
    assert settings.audit_log_path == Path("data/audit.jsonl")


def test_settings_can_be_overridden_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATEWAY_ENVIRONMENT", "test")
    monkeypatch.setenv("GATEWAY_APPROVAL_TIMEOUT_SECONDS", "30")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.environment == "test"
    assert settings.approval_timeout_seconds == 30
    get_settings.cache_clear()
