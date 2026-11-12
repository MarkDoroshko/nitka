"""Pure classification helpers for matching support operations.

Classification uses requisites and document content, never amounts, filenames
or the answer key. Unknown/conflicting evidence is returned as ambiguous so the
matcher can route it to manual review instead of inventing an article.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from nitka.contracts.profile import CaseProfile

PRODUCTION_DOCUMENTS: Final[frozenset[int]] = frozenset({4, 5, 6, 7})
ADMIN_DOCUMENTS: Final[frozenset[int]] = frozenset({8, 9, 11, 12, 13})

_RUSSIAN_MONTHS: Final[dict[str, int]] = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}

_PRODUCTION_WORDS: Final[tuple[str, ...]] = (
    "производ",
    "цех",
    "оборуд",
    "материал",
    "перевоз",
    "склад",
    "ремонт",
    "энерг",
    "подряд",
    "строит",
)
_ADMIN_WORDS: Final[tuple[str, ...]] = (
    "администра",
    "офис",
    "аренд",
    "связ",
    "банк",
    "консульт",
    "юрид",
    "бухгал",
    "командир",
    "программ",
    "it",
)


@dataclass(frozen=True)
class ArticleClassification:
    article_code: str | None
    basis: str
    ambiguous: bool = False
    detail: str = ""


@dataclass(frozen=True)
class PeriodEvaluation:
    month: str | None
    inside: bool | None
    detail: str


class DocumentClassifier:
    """Map support requisites/content to a configured KFZ article."""

    def __init__(self, profile: CaseProfile) -> None:
        self.profile = profile

    def classify(
        self,
        document_number: str | None,
        description: str | None,
        explicit_article: str | None,
    ) -> ArticleClassification:
        explicit = _four_digit_code(explicit_article)
        if explicit is not None:
            if explicit in self.profile.article_codes:
                return ArticleClassification(
                    explicit, "explicit article_code field", detail=""
                )
            return ArticleClassification(
                None,
                "explicit article_code field",
                ambiguous=True,
                detail=f"article {explicit!r} is outside the profile",
            )

        number = _document_number(document_number)
        if number in PRODUCTION_DOCUMENTS:
            return ArticleClassification(
                "0800",
                "document number 04-07",
                detail=f"support document {number:02d} is production",
            )
        if number in ADMIN_DOCUMENTS:
            return ArticleClassification(
                "0900",
                "document number 08/09/11/12/13",
                detail=f"support document {number:02d} is administrative",
            )

        text = (description or "").casefold()
        production = any(word in text for word in _PRODUCTION_WORDS)
        administrative = any(word in text for word in _ADMIN_WORDS)
        if production and not administrative:
            return ArticleClassification(
                "0800",
                "description contains production semantics",
                detail=description or "",
            )
        if administrative and not production:
            return ArticleClassification(
                "0900",
                "description contains administrative semantics",
                detail=description or "",
            )
        if production and administrative:
            return ArticleClassification(
                None,
                "description contains both production and administrative semantics",
                ambiguous=True,
                detail=description or "",
            )
        return ArticleClassification(
            None,
            "no unambiguous article evidence",
            ambiguous=True,
            detail=description or "",
        )


class ServicePeriodGate:
    """Compare a support service period with the configured month range."""

    def __init__(self, profile: CaseProfile) -> None:
        self.profile = profile
        self._months = set(profile.month_labels())

    def evaluate(self, raw_period: str | None) -> PeriodEvaluation:
        if raw_period is None or not raw_period.strip():
            return PeriodEvaluation(None, None, "service period is missing")
        month = _parse_month(raw_period, self.profile.target_year)
        if month is None:
            return PeriodEvaluation(
                None, None, f"service period {raw_period!r} is not parseable"
            )
        inside = month in self._months
        detail = (
            f"service month {month} is inside {min(self._months)}..{max(self._months)}"
            if inside
            else f"service month {month} is outside {min(self._months)}..{max(self._months)}"
        )
        return PeriodEvaluation(month, inside, detail)


def normalize_contract(value: str | None) -> str:
    """Preserve contract punctuation/suffixes; only whitespace/case vary."""
    return re.sub(r"\s+", "", value or "").casefold()


def contract_matches(profile: CaseProfile, value: str | None) -> bool | None:
    """True/False for a usable contract, None when it is missing."""
    normalized = normalize_contract(value)
    if not normalized:
        return None
    return normalized == normalize_contract(profile.contract)


def operation_key(
    operation_id: str | None,
    contract: str | None,
    document_number: str | None,
    document_date: str | None,
) -> tuple[str, ...] | None:
    """Identity for duplicate detection, independent of bytes and amount."""
    identifier = _normalize_text(operation_id)
    if identifier:
        return ("id", identifier)
    normalized_contract = normalize_contract(contract)
    normalized_number = _normalize_text(document_number)
    normalized_date = _normalize_text(document_date)
    if normalized_contract and normalized_number and normalized_date:
        return ("requisites", normalized_contract, normalized_number, normalized_date)
    return None


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def _document_number(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"\b0*(\d{1,2})\b", value)
    return int(match.group(1)) if match else None


def _four_digit_code(value: str | None) -> str | None:
    if value is None:
        return None
    match = re.search(r"\b(\d{4})\b", value)
    return match.group(1) if match else None


def _parse_month(value: str, default_year: int) -> str | None:
    text = value.casefold()
    match = re.search(r"(?P<year>20\d{2})\s*[-./]\s*(?P<month>\d{1,2})", text)
    if match is None:
        match = re.search(r"(?P<month>\d{1,2})\s*[-./]\s*(?P<year>20\d{2})", text)
    if match is not None:
        month = int(match.group("month"))
        year = int(match.group("year"))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}"
        return None

    russian_month = _russian_month(text)
    year_match = re.search(r"\b(20\d{2})\b", text)
    year = int(year_match.group(1)) if year_match else default_year
    return f"{year:04d}-{russian_month:02d}" if russian_month is not None else None


def _russian_month(text: str) -> int | None:
    for stem, number in _RUSSIAN_MONTHS.items():
        if stem in text:
            return number
    return None
