"""Project a private run into its public form (design spec §10.2, §10.3).

The public trace must be sufficient to replay scoring while carrying none of the
content that makes the raw store unpublishable. It achieves that by keeping the
structured, first-party parts in full — findings above all, since replay scores
from them — and reducing everything else to hashes, byte counts, and short
excerpts.

Excerpt length is decided by where the content came from rather than what it
looks like:

* **harness** — our own messages. Safe to publish, and usually the part a reader
  actually needs, so it gets the largest allowance.
* **first party** — fixture code we wrote and license ourselves. A short excerpt
  is enough to follow what happened.
* **third party** — upstream source. Always redacted, whatever its length,
  because republishing someone else's code is not ours to decide.
"""

from __future__ import annotations

from typing import Any, ClassVar

from coding_agent_eval import TRACE_SCHEMA_VERSION
from coding_agent_eval.trace.allowlist import (
    PUBLIC_FIELDS,
    FieldClass,
    UnknownFieldError,
    classify,
)


class ExcerptPolicy:
    """How much of a tool result may appear in public, by content origin."""

    HARNESS_BYTES = 2000
    FIRST_PARTY_BYTES = 400

    _LIMITS: ClassVar[dict[str, int]] = {
        "harness": HARNESS_BYTES,
        "first_party": FIRST_PARTY_BYTES,
    }
    REDACTED = "<redacted>"

    @classmethod
    def excerpt_for(cls, origin: str, content: str) -> str:
        """Return the publishable excerpt of `content` under `origin`'s policy."""
        if origin == "third_party":
            return cls.REDACTED
        if origin not in cls._LIMITS:
            raise ValueError(
                f"unknown excerpt origin {origin!r}; expected one of "
                f"{sorted([*cls._LIMITS, 'third_party'])}"
            )

        limit = cls._LIMITS[origin]
        encoded = content.encode("utf-8")
        if len(encoded) <= limit:
            return content
        truncated = encoded[:limit].decode("utf-8", errors="ignore")
        return f"{truncated}\n...[truncated at {limit} bytes]"


def project_payload(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Keep public fields, drop known-private ones, raise on anything unclassified.

    The event type is checked before its fields, so an unrecognised event with an
    empty payload is still refused. Otherwise a new event carrying nothing today
    would pass silently and start carrying something tomorrow.
    """
    if event not in PUBLIC_FIELDS:
        raise UnknownFieldError(
            f"unknown raw event type {event!r}; classify its fields in allowlist.py before "
            "any run can be published"
        )

    projected: dict[str, Any] = {}
    for field, value in payload.items():
        if classify(event, field) is FieldClass.PUBLIC:
            projected[field] = value
    return projected


def project_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Project one raw event into its public form."""
    event = raw["event"]
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "seq": raw["seq"],
        "ts": raw["ts"],
        "event": event,
        "payload": project_payload(event, raw["payload"]),
    }


def project_events(raw_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [project_record(raw) for raw in raw_events]
