"""Phase 0 configuration smoke tests."""

from pathlib import Path

import pytest

from gateway.config import Settings, get_settings


def _clear_gateway_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every supported gateway override from the test environment."""

    names = (
        "GATEWAY_ENVIRONMENT",
        "GATEWAY_LOG_LEVEL",
        "GATEWAY_REDIS_URL",
        "GATEWAY_OPA_URL",
        "GATEWAY_OTLP_ENDPOINT",
        "GATEWAY_JAEGER_UI_URL",
        "GATEWAY_DATABASE_PATH",
        "GATEWAY_APPROVAL_TIMEOUT_SECONDS",
        "GATEWAY_SANDBOX_RUNTIME",
        "GATEWAY_AUDIT_LOG_PATH",
        "GATEWAY_REPAIR_ENABLED",
        "GATEWAY_REPAIR_LLM_URL",
        "GATEWAY_REPAIR_LLM_MODEL",
        "GATEWAY_REPAIR_LLM_API_KEY",
        "GATEWAY_REPAIR_LLM_TIMEOUT_SECONDS",
        "GATEWAY_HTTP_HOST",
        "GATEWAY_HTTP_PORT",
        "GATEWAY_HTTP_PATH",
        "GATEWAY_HTTP_AUTH_TOKEN",
        "GATEWAY_HTTP_MAX_REQUEST_BODY_BYTES",
        "GATEWAY_HTTP_SESSION_IDLE_TIMEOUT_SECONDS",
        "GATEWAY_HTTP_MAX_SESSIONS",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)


def test_settings_have_safe_local_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify that settings have typed, safe local defaults."""

    _clear_gateway_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    settings = Settings()

    assert str(settings.redis_url) == "redis://localhost:6379/0"
    assert str(settings.opa_url) == "http://localhost:8181/"
    assert settings.database_path == Path("data/gateway.sqlite")
    assert settings.sandbox_runtime == "hardened-docker"
    assert settings.audit_log_path == Path("data/audit.jsonl")
    assert settings.http_host == "0.0.0.0"
    assert settings.http_port == 8080
    assert settings.http_path == "/mcp"


def test_settings_can_be_overridden_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that environment variables override typed defaults."""

    monkeypatch.setenv("GATEWAY_ENVIRONMENT", "test")
    monkeypatch.setenv("GATEWAY_APPROVAL_TIMEOUT_SECONDS", "30")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.environment == "test"
    assert settings.approval_timeout_seconds == 30
    get_settings.cache_clear()
