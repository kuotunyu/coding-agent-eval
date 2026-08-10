"""One assertion and how it renders, shared by the fixture gates.

Two gates now report on a fixture — `rebuild` (G3) and `environment` (§9.4) —
and a third is plausible. Each printing its own shape would make the output
harder to read for no reason, and would let one of them quietly stop showing
both sides of a mismatch, which is the part that makes a failure actionable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    """One assertion, with both sides of it kept for the report.

    `expected` and `actual` are always populated, including when the check
    passes. A report that only records what went wrong cannot be used to see
    what a fixture currently *is*, which is most of why anyone runs one.
    """

    name: str
    ok: bool
    expected: str
    actual: str
    detail: tuple[str, ...] = ()

    def render(self) -> str:
        lines = [f"  {self.name:<28} {'ok' if self.ok else 'FAILED'}"]
        if not self.ok:
            lines.append(f"      expected  {self.expected}")
            lines.append(f"      actual    {self.actual}")
        lines += [f"      - {line}" for line in self.detail]
        return "\n".join(lines)


@dataclass(frozen=True)
class Observation:
    """Something measured and reported, which no assertion covers.

    Deliberately not a `Check` with `ok=True`. A value nothing verified must not
    render as a passing check, because a reader skimming a green report would
    take it for one that had been confirmed.
    """

    name: str
    value: str
    why_unverified: str

    def render(self) -> str:
        return f"  {self.name:<28} {self.value}\n      (unverified: {self.why_unverified})"
