from decimal import Decimal

import pytest

from nitka.amounts import ParsedAmount, normalize_amount, parse_amount


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("150 000,00", "150000.00"),
        ("150\u00a0000,00", "150000.00"),
        ("150\u202f000,00", "150000.00"),
        ("1 234 567,89", "1234567.89"),
        ("150000,00", "150000.00"),
        ("1,5", "1.5"),
        ("1.5", "1.5"),
        ("-12,50", "-12.50"),
        ("0", "0"),
        ("0,00", "0.00"),
        ("100.0", "100.0"),
        ("150000", "150000"),
        (" 150 000,00 ", "150000.00"),
    ],
)
def test_normalizes_supported_text(raw: str, expected: str) -> None:
    assert normalize_amount(raw) == expected
    assert parse_amount(raw) == ParsedAmount(expected, None)


def test_normalizes_numeric_cells_without_float_artefacts() -> None:
    assert normalize_amount(2000) == "2000"
    assert normalize_amount(150000.0) == "150000.0"
    assert normalize_amount(Decimal("0.30")) == "0.30"


def test_blank_values_are_unknown_not_zero() -> None:
    assert normalize_amount(None) is None
    assert normalize_amount("") is None
    assert normalize_amount("   ") is None


@pytest.mark.parametrize(
    "raw",
    [
        "12 34,56",
        "1 234,5678901",
        "1234567890123456789",
        "1e3",
        "NaN",
        "Infinity",
        "1,234.56",
        "1.234,56",
        "$100",
        "100 ₽",
        "(100)",
        "+100",
        "1,",
        "abc",
    ],
)
def test_invalid_values_have_no_numeric_result(raw: str) -> None:
    assert normalize_amount(raw) is None
    assert parse_amount(raw).error is not None


def test_parsing_explains_profile_and_grammar_errors() -> None:
    assert "grouping" in (parse_amount("12 34,56").error or "")
    assert "mixed decimal separators" in (
        parse_amount("1,234.56").error or ""
    )
    assert "fractional part exceeds 6" in (
        parse_amount("1 234,5678901").error or ""
    )
    assert "integer part exceeds 18" in (
        parse_amount("1" * 19).error or ""
    )


def test_excel_error_values_are_diagnosed() -> None:
    parsed = parse_amount("#VALUE!")
    assert parsed.value is None
    assert "Excel error" in (parsed.error or "")


def test_non_finite_and_boolean_values_are_rejected() -> None:
    for raw in (float("nan"), float("inf"), float("-inf"), True):
        assert normalize_amount(raw) is None
        assert parse_amount(raw).error is not None


def test_datetime_and_other_objects_are_rejected() -> None:
    assert normalize_amount(object()) is None
    assert parse_amount(object()).error is not None
