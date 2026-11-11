"""Strict parsing of printed monetary amounts.

Supported grammar (first profile):

* optional leading minus;
* integer part as ungrouped digits (1–18) or grouped by exactly one regular
  space, NBSP or narrow NBSP between groups of three digits;
* optional decimal part separated by a comma or a dot, 1–6 digits;
* surrounding whitespace is ignored.

Mixed separators, exponent notation, currency signs, parentheses, plus signs,
NaN/Infinity and broken grouping are rejected. The result is a canonical string
that fits the ``Amount`` contract; the original text stays in ``raw_text``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

_GROUP_SPACES = " \u00a0\u202f"
_LOOSE = re.compile(
    r"^-?(?:(?:[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+)|[0-9]+)"
    r"(?:[.,][0-9]+)?$"
)
_MAX_INTEGER_DIGITS = 18
_MAX_FRACTION_DIGITS = 6


@dataclass(frozen=True)
class ParsedAmount:
    value: str | None
    error: str | None = None


def normalize_amount(raw: Any) -> str | None:
    """Return a canonical Amount string or ``None`` if it is unknown/invalid."""
    return parse_amount(raw).value


def parse_amount(raw: Any) -> ParsedAmount:
    """Parse a cell/text value, keeping an explanatory error for diagnostics."""
    if raw is None:
        return ParsedAmount(None)
    if isinstance(raw, bool):
        return ParsedAmount(None, "boolean is not an amount")
    if isinstance(raw, int):
        return _from_decimal(Decimal(raw))
    if isinstance(raw, float):
        if raw != raw or raw in (float("inf"), float("-inf")):
            return ParsedAmount(None, "non-finite number")
        return _from_decimal(Decimal(str(raw)))
    if isinstance(raw, Decimal):
        if not raw.is_finite():
            return ParsedAmount(None, "non-finite number")
        return _from_decimal(raw)
    if isinstance(raw, str):
        return _from_text(raw)
    return ParsedAmount(None, f"unsupported cell type: {type(raw).__name__}")


def _from_decimal(value: Decimal) -> ParsedAmount:
    text = format(value, "f")
    body = text[1:] if text.startswith("-") else text
    integer, _, fraction = body.partition(".")
    error = _profile_error(integer, fraction)
    if error is not None:
        return ParsedAmount(None, error)
    return ParsedAmount(text)


def _from_text(raw: str) -> ParsedAmount:
    text = raw.strip()
    if not text:
        return ParsedAmount(None)
    if text.startswith("#"):
        return ParsedAmount(None, f"Excel error value: {text}")
    if not _LOOSE.fullmatch(text):
        return ParsedAmount(None, _grammar_error(text))

    body = text[1:] if text.startswith("-") else text
    if "," in body:
        integer_part, fraction = body.split(",", 1)
    else:
        integer_part, _, fraction = body.partition(".")

    integer_digits = integer_part
    for space in _GROUP_SPACES:
        integer_digits = integer_digits.replace(space, "")
    error = _profile_error(integer_digits, fraction)
    if error is not None:
        return ParsedAmount(None, error)

    canonical = f"{'-' if text.startswith('-') else ''}{integer_digits}"
    if fraction:
        canonical = f"{canonical}.{fraction}"
    try:
        value = Decimal(canonical)
    except InvalidOperation:
        return ParsedAmount(None, f"not a number: {text!r}")
    return _from_decimal(value)


def _profile_error(integer: str, fraction: str) -> str | None:
    if len(integer) > _MAX_INTEGER_DIGITS:
        return (
            f"integer part exceeds {_MAX_INTEGER_DIGITS} digits "
            f"({len(integer)} found)"
        )
    if len(fraction) > _MAX_FRACTION_DIGITS:
        return (
            f"fractional part exceeds {_MAX_FRACTION_DIGITS} digits "
            f"({len(fraction)} found)"
        )
    return None


def _grammar_error(text: str) -> str:
    if "," in text and "." in text:
        return "mixed decimal separators are not supported"
    if any(space in text for space in _GROUP_SPACES):
        return "invalid thousands grouping"
    if "e" in text.lower():
        return "exponent notation is not supported"
    if text.startswith("+"):
        return "leading plus sign is not supported"
    if text.startswith("(") and text.endswith(")"):
        return "parentheses are not supported"
    if any(character in text for character in "$€£₽"):
        return "unsupported currency notation"
    if any(character.isalpha() for character in text):
        return "unsupported currency or text notation"
    return "unsupported amount format"
