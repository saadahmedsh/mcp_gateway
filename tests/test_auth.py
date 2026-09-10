"""Tests for OIDC token validation and principal claim handling."""

from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.responses import PlainTextResponse

from gateway.auth import OIDCAuthenticator
from gateway.errors import AuthenticationError
from gateway.http_server import AuthenticationMiddleware


def _jwk(private_key: Any) -> dict[str, Any]:
    """Build a public JWKS document entry for a generated RSA key."""

    public_numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": "test-key",
        "alg": "RS256",
        "use": "sig",
        "n": jwt.utils.base64url_encode(
            public_numbers.n.to_bytes((public_numbers.n.bit_length() + 7) // 8, "big")
        ).decode(),
        "e": jwt.utils.base64url_encode(
            public_numbers.e.to_bytes((public_numbers.e.bit_length() + 7) // 8, "big")
        ).decode(),
    }


def _token(private_key: Any, **claims: Any) -> str:
    """Sign a test token with the required OIDC claims."""

    return jwt.encode(
        {
            "iss": "https://issuer.example",
            "aud": "mcp-gateway",
            "sub": "user-1",
            "tenant_id": "tenant-a",
            "roles": ["user"],
            "exp": 4_102_444_800,
            **claims,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


@pytest.fixture
def authenticator() -> tuple[OIDCAuthenticator, Any]:
    """Create an authenticator backed by one generated signing key."""

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = OIDCAuthenticator(
        "https://issuer.example",
        "mcp-gateway",
        "https://issuer.example/.well-known/jwks.json",
        initial_keys={"test-key": _jwk(private_key)},
    )
    return verifier, private_key


@pytest.mark.asyncio
async def test_oidc_token_returns_verified_principal(
    authenticator: tuple[OIDCAuthenticator, Any],
) -> None:
    """Verify issuer, audience, signature, tenant, and role claims."""

    verifier, private_key = authenticator
    principal = await verifier.authenticate(_token(private_key))

    assert principal.subject == "user-1"
    assert principal.tenant_id == "tenant-a"
    assert [role.value for role in principal.roles] == ["user"]


@pytest.mark.asyncio
async def test_oidc_token_without_tenant_is_rejected(
    authenticator: tuple[OIDCAuthenticator, Any],
) -> None:
    """Reject tokens that cannot establish tenant context."""

    verifier, private_key = authenticator
    with pytest.raises(AuthenticationError, match="tenant_id"):
        await verifier.authenticate(_token(private_key, tenant_id=None))


@pytest.mark.asyncio
async def test_oidc_token_with_unknown_role_is_rejected(
    authenticator: tuple[OIDCAuthenticator, Any],
) -> None:
    """Reject roles outside the gateway authorization vocabulary."""

    verifier, private_key = authenticator
    with pytest.raises(AuthenticationError, match="unsupported role"):
        await verifier.authenticate(_token(private_key, roles=["owner"]))


@pytest.mark.asyncio
async def test_oidc_token_with_wrong_audience_is_rejected(
    authenticator: tuple[OIDCAuthenticator, Any],
) -> None:
    """Reject a validly signed token intended for another service."""

    verifier, private_key = authenticator
    with pytest.raises(AuthenticationError, match="validation failed"):
        await verifier.authenticate(_token(private_key, aud="other-service"))


@pytest.mark.asyncio
async def test_http_middleware_requires_and_accepts_oidc_token(
    authenticator: tuple[OIDCAuthenticator, Any],
) -> None:
    """Require a valid OIDC bearer token on protected HTTP requests."""

    verifier, private_key = authenticator

    async def endpoint(scope: Any, receive: Any, send: Any) -> None:
        """Return a response for an authenticated request."""

        await PlainTextResponse("ok")(scope, receive, send)

    app = AuthenticationMiddleware(endpoint, "oidc", oidc=verifier)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/resource")
        accepted = await client.get(
            "/resource",
            headers={"Authorization": f"Bearer {_token(private_key)}"},
        )

    assert denied.status_code == 401
    assert accepted.status_code == 200
