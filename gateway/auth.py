"""JWT authentication and request-scoped principal propagation."""

import contextvars
import json
from collections.abc import Mapping
from typing import Any, cast

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import RSAAlgorithm

from gateway.errors import AuthenticationError
from gateway.models import Principal, Role

_principal_context: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "gateway_principal", default=None
)


def get_current_principal() -> Principal | None:
    """Return the principal associated with the current request task."""

    return _principal_context.get()


def set_current_principal(
    principal: Principal | None,
) -> contextvars.Token[Principal | None]:
    """Set a request principal and return a token for restoration."""

    return _principal_context.set(principal)


def reset_current_principal(token: contextvars.Token[Principal | None]) -> None:
    """Restore the principal that was active before request authentication."""

    _principal_context.reset(token)


class OIDCAuthenticator:
    """Verify OIDC JWTs against a remote JSON Web Key Set."""

    def __init__(
        self,
        issuer_url: str,
        audience: str,
        jwks_url: str,
        timeout_seconds: float = 2.0,
        initial_keys: Mapping[str, dict[str, Any]] | None = None,
    ) -> None:
        """Create a verifier with issuer, audience, and JWKS constraints."""

        self._issuer_url = issuer_url.rstrip("/")
        self._audience = audience
        self._jwks_url = jwks_url
        self._timeout_seconds = timeout_seconds
        self._keys: dict[str, dict[str, Any]] = dict(initial_keys or {})

    async def authenticate(self, token: str) -> Principal:
        """Validate a bearer token and return its verified principal claims."""

        try:
            header = jwt.get_unverified_header(token)
            key_id = header.get("kid")
            algorithm = header.get("alg")
            if not isinstance(key_id, str) or algorithm != "RS256":
                raise AuthenticationError("Token must use an RS256 key with kid")
            jwk = await self._get_key(key_id)
            public_key = cast(
                RSAPublicKey,
                RSAAlgorithm.from_jwk(json.dumps(jwk)),
            )
            claims = jwt.decode(
                token,
                key=public_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer_url,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except AuthenticationError:
            raise
        except (httpx.HTTPError, jwt.PyJWTError, TypeError, ValueError) as error:
            raise AuthenticationError("Token validation failed") from error
        return self._principal_from_claims(claims)

    async def _get_key(self, key_id: str) -> dict[str, Any]:
        """Load a matching signing key, refreshing the JWKS cache when needed."""

        key = self._keys.get(key_id)
        if key is not None:
            return key
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.get(self._jwks_url)
            response.raise_for_status()
            document = response.json()
        keys = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(keys, list):
            raise AuthenticationError("OIDC JWKS response is invalid")
        self._keys = {
            item["kid"]: item
            for item in keys
            if isinstance(item, dict) and isinstance(item.get("kid"), str)
        }
        key = self._keys.get(key_id)
        if key is None:
            raise AuthenticationError("Token signing key was not found")
        return key

    @staticmethod
    def _principal_from_claims(claims: Mapping[str, Any]) -> Principal:
        """Convert validated OIDC claims into the gateway principal model."""

        subject = claims.get("sub")
        tenant_id = claims.get("tenant_id", claims.get("tenant"))
        roles = claims.get("roles")
        if roles is None and isinstance(claims.get("realm_access"), Mapping):
            roles = claims["realm_access"].get("roles")
        if not isinstance(subject, str) or not isinstance(tenant_id, str):
            raise AuthenticationError("Token requires sub and tenant_id claims")
        if not isinstance(roles, list) or not roles:
            raise AuthenticationError("Token requires at least one role")
        parsed_roles = [
            Role(str(role))
            for role in roles
            if str(role) in {item.value for item in Role}
        ]
        if not parsed_roles:
            raise AuthenticationError("Token contains an unsupported role")
        agent_id = claims.get("agent_id")
        return Principal(
            subject=subject,
            tenant_id=tenant_id,
            roles=parsed_roles,
            issuer=str(claims.get("iss")),
            agent_id=str(agent_id) if agent_id is not None else None,
        )


def principal_from_static_token() -> Principal:
    """Create the explicitly local-only identity for legacy static tokens."""

    return Principal(
        subject="static-local-user",
        tenant_id="local",
        roles=[Role.ADMIN],
        issuer="local-static-token",
    )
