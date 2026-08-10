"""Document validation: JSON Schema plus the rules JSON Schema cannot express.

Draft 2020-12 has no way to compare two sibling values, so a handful of the
protocol's most important constraints cannot live in the schema files:

* `line_end >= line_start` — an inverted range would silently match nothing.
* `expected_clean != expected_mutated` — a witness that expects the same result
  on both trees proves nothing at all, which is the single failure mode gate G2
  exists to prevent.

Encoding these as Python keeps them enforced and testable rather than reduced to
a comment. Each rule carries the JSON pointer of the offending value so an error
names a location rather than a document.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from coding_agent_eval.schemas.loader import load_schema


@dataclass(frozen=True)
class ValidationProblem:
    """One failure, addressed by JSON pointer so it can be found without guessing."""

    pointer: str
    message: str
    rule: str

    def render(self) -> str:
        return f"{self.pointer or '/'}: {self.message} [{self.rule}]"


def _pointer(parts: tuple[Any, ...]) -> str:
    return "".join(f"/{str(p).replace('~', '~0').replace('/', '~1')}" for p in parts)


def _schema_problems(name: str, document: Any) -> list[ValidationProblem]:
    validator = Draft202012Validator(load_schema(name))
    problems = []
    for error in validator.iter_errors(document):
        problems.append(
            ValidationProblem(
                pointer=_pointer(tuple(error.absolute_path)),
                message=error.message,
                rule="schema",
            )
        )
    return sorted(problems, key=lambda p: (p.pointer, p.message))


def _line_range_problems(prefix: str, loc: Any) -> list[ValidationProblem]:
    if not isinstance(loc, dict):
        return []
    start, end = loc.get("line_start"), loc.get("line_end")
    if isinstance(start, int) and isinstance(end, int) and end < start:
        return [
            ValidationProblem(
                pointer=f"{prefix}/line_end",
                message=f"line_end ({end}) is before line_start ({start})",
                rule="LINE_RANGE_ORDER",
            )
        ]
    return []


def _structural_problems(name: str, document: Any) -> list[ValidationProblem]:
    if not isinstance(document, dict):
        return []
    problems: list[ValidationProblem] = []

    if name == "bug":
        localization = document.get("localization")
        if isinstance(localization, dict):
            problems += _line_range_problems("/localization/primary", localization.get("primary"))
            alternates = localization.get("acceptable_alternates")
            if isinstance(alternates, list):
                for index, alternate in enumerate(alternates):
                    problems += _line_range_problems(
                        f"/localization/acceptable_alternates/{index}", alternate
                    )

        witness = document.get("witness")
        if isinstance(witness, dict):
            clean = witness.get("expected_clean")
            mutated = witness.get("expected_mutated")
            if clean is not None and clean == mutated:
                problems.append(
                    ValidationProblem(
                        pointer="/witness/expected_mutated",
                        message=(
                            "expected_mutated is identical to expected_clean, so the witness "
                            "cannot distinguish the mutated tree and proves nothing"
                        ),
                        rule="WITNESS_DISTINGUISHES",
                    )
                )

    if name == "finding":
        problems += _line_range_problems("", document)

    return problems


def validate_document(name: str, document: Any) -> list[ValidationProblem]:
    """Return every problem in `document`, schema failures first.

    Structural rules run only once the schema passes: reporting a sibling
    comparison against a document with the wrong shape produces noise, not
    information.
    """
    problems = _schema_problems(name, document)
    if problems:
        return problems
    return _structural_problems(name, document)


def is_valid(name: str, document: Any) -> bool:
    return not validate_document(name, document)
