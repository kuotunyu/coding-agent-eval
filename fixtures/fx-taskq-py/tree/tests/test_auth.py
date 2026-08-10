"""Admin authentication.

The timing property cannot be proven by a unit test — measuring it reliably needs
statistics and a quiet machine. What is tested is the thing that actually
regresses: that the comparison goes through `hmac.compare_digest` rather than
`==`. A refactor swapping one for the other is the realistic failure, and this
catches it.
"""

from __future__ import annotations

import pytest

from taskq.auth import authenticate_admin, extract_bearer_token, verify_admin_token
from taskq.errors import Unauthorized

TOKEN = "s3cret-admin-token"


# ------------------------------------------------------------- extraction


def test_a_bearer_token_is_extracted() -> None:
    assert extract_bearer_token(f"Bearer {TOKEN}") == TOKEN


def test_surrounding_whitespace_is_trimmed() -> None:
    assert extract_bearer_token(f"Bearer  {TOKEN}  ") == TOKEN


@pytest.mark.parametrize(
    "header", [None, "", "Basic abc123", "bearer lowercase-scheme", "Bearer", "Bearer   "]
)
def test_anything_that_is_not_a_bearer_token_yields_none(header: str | None) -> None:
    assert extract_bearer_token(header) is None


# ----------------------------------------------------------- verification


def test_the_right_token_is_accepted() -> None:
    verify_admin_token(TOKEN, TOKEN)


def test_the_wrong_token_is_rejected() -> None:
    with pytest.raises(Unauthorized):
        verify_admin_token("wrong", TOKEN)


def test_a_token_differing_only_in_the_last_byte_is_rejected() -> None:
    with pytest.raises(Unauthorized):
        verify_admin_token(TOKEN[:-1] + "X", TOKEN)


def test_a_prefix_of_the_token_is_rejected() -> None:
    with pytest.raises(Unauthorized):
        verify_admin_token(TOKEN[:5], TOKEN)


def test_a_missing_token_and_a_wrong_token_answer_identically() -> None:
    """Differing answers would tell a caller whether authentication is configured."""
    with pytest.raises(Unauthorized) as missing:
        verify_admin_token(None, TOKEN)
    with pytest.raises(Unauthorized) as wrong:
        verify_admin_token("wrong", TOKEN)
    assert str(missing.value) == str(wrong.value)


def test_admin_routes_are_closed_when_no_token_is_configured() -> None:
    """An unset secret is an unfinished deployment, not an open one."""
    with pytest.raises(Unauthorized):
        verify_admin_token("anything", None)
    with pytest.raises(Unauthorized):
        verify_admin_token(None, None)


def test_authenticate_admin_accepts_a_full_header() -> None:
    authenticate_admin(f"Bearer {TOKEN}", TOKEN)


def test_authenticate_admin_rejects_a_missing_header() -> None:
    with pytest.raises(Unauthorized):
        authenticate_admin(None, TOKEN)


# --------------------------------------------------- constant-time comparison


def test_comparison_uses_hmac_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """The realistic regression is someone replacing this with `==`."""
    import taskq.auth as auth_module

    calls: list[tuple[bytes, bytes]] = []
    original = auth_module.hmac.compare_digest

    def recording(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return bool(original(a, b))

    monkeypatch.setattr(auth_module.hmac, "compare_digest", recording)
    verify_admin_token(TOKEN, TOKEN)

    assert calls, "token comparison did not go through hmac.compare_digest"


def test_comparison_operates_on_bytes_not_str() -> None:
    """compare_digest on str raises for non-ASCII, so encoding is not optional."""
    unicode_token = "tökén-ünïcodé"
    verify_admin_token(unicode_token, unicode_token)
    with pytest.raises(Unauthorized):
        verify_admin_token(unicode_token, unicode_token + "x")
