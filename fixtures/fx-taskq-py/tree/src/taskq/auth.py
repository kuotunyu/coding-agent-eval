"""Bearer-token authentication for the admin routes.

Two properties the README commits to, both easy to lose in a refactor.

Comparison is constant time. `==` on strings stops at the first differing byte,
so how long a rejection takes tells the caller how much of their guess was
right, and a token can be recovered one byte at a time.

A missing token and a wrong token produce the same answer. Distinguishing them
tells an unauthenticated caller whether authentication is configured at all,
which is only useful for probing.
"""

from __future__ import annotations

import hmac

from taskq.errors import Unauthorized

BEARER_PREFIX = "Bearer "

#: One message for every authentication failure, whatever the cause.
_FAILURE_MESSAGE = "authentication required"


def extract_bearer_token(header_value: str | None) -> str | None:
    """Pull the token out of an Authorization header, or return None.

    Returning None rather than raising keeps the decision about what a missing
    token means with the caller, which is the only place that knows whether the
    route needs one.
    """
    if not header_value:
        return None
    if not header_value.startswith(BEARER_PREFIX):
        return None
    token = header_value[len(BEARER_PREFIX) :].strip()
    return token or None


def verify_admin_token(presented: str | None, expected: str | None) -> None:
    """Raise `Unauthorized` unless `presented` matches `expected`.

    When no admin token is configured the admin routes are closed rather than
    open. An unset secret is a deployment that has not finished, not a
    deployment that trusts everyone.
    """
    if not expected:
        raise Unauthorized(_FAILURE_MESSAGE)
    if presented is None:
        raise Unauthorized(_FAILURE_MESSAGE)

    if not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        raise Unauthorized(_FAILURE_MESSAGE)


def authenticate_admin(header_value: str | None, expected: str | None) -> None:
    verify_admin_token(extract_bearer_token(header_value), expected)
