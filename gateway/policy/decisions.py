"""Policy decision values and helper constructors."""

from typing import Final

from gateway.models import PolicyDecision

ALLOW: Final[str] = "allow"
REQUIRES_APPROVAL: Final[str] = "requires_approval"
DENY: Final[str] = "deny"


def allow(rule: str, reason: str) -> PolicyDecision:
    """Create an allow decision."""

    return PolicyDecision(outcome=ALLOW, matched_rule=rule, reason=reason)
