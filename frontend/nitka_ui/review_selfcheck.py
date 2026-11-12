from __future__ import annotations

from .review_sources import _as_mapping, _source_id
from .review_view import (
    _as_decimal,
    _build_view,
    _delta_kind,
    _format_amount,
    _format_delta,
)
from .review_widgets import _back_action, _confirm_action, _save_action


def _run_self_tests() -> None:
    sources = [
        {
            "source_id": "S-R-AMOUNT",
            "file_id": "F-REPORT",
            "file_name": "report.xlsx",
            "document_id": "D-REPORT",
            "location": {"kind": "CELL", "sheet": "Report", "cell": "D2"},
            "method": "TABLE",
            "raw_text": "150 000,00",
            "value": "150000.00",
            "review_status": "UNREVIEWED",
        },
        {
            "source_id": "S-A-AMOUNT",
            "file_id": "F-ACT",
            "file_name": "act.pdf",
            "document_id": "D-ACT",
            "location": {"kind": "PAGE", "page": 2},
            "method": "VISION",
            "raw_text": "120 000,00",
            "value": "120000.00",
            "review_status": "NEEDS_REVIEW",
        },
    ]
    issue = {
        "check_id": "REPORT_AMOUNT:R-1",
        "state": "FAIL",
        "reason_code": "AMOUNT_EXCEEDS_SUPPORT",
        "operands": [
            {"name": "reported", "value": "150000.00", "source_ids": ["S-R-AMOUNT"]},
            {"name": "supported", "value": "120000.00", "source_ids": ["S-A-AMOUNT"]},
        ],
        "calculation": {
            "actual": "150000.00",
            "expected": "120000.00",
            "delta": "30000.00",
            "tolerance": "0.00",
            "unit": "RUB",
        },
        "evidence_ids": ["S-R-AMOUNT", "S-A-AMOUNT"],
    }

    view = _build_view(issue, sources)
    assert view["issue_id"] == "REPORT_AMOUNT:R-1"
    assert _source_id(view["reported_sources"][0]) == "S-R-AMOUNT"
    assert [_source_id(source) for source in view["support_sources"]] == ["S-A-AMOUNT"]
    assert view["delta"] == "30000.00"
    assert view["delta_kind"] == "over"
    assert _format_amount("150000.00", "RUB") == "150\u202f000,00 ₽"
    assert _format_delta("30000.00", "RUB") == "+30\u202f000,00 ₽"
    assert _format_delta("-30000.00", "RUB") == "−30\u202f000,00 ₽"
    assert _format_amount("150 000,00", "RUB") == "150\u202f000,00 ₽"
    assert _delta_kind("0.00", "0.00") == "zero"
    assert _delta_kind(None) == "unknown"
    assert _as_decimal("NaN") is None
    assert _as_decimal("Infinity") is None

    actions = [
        _save_action("I-1", "S-A-AMOUNT", "120000.00"),
        _confirm_action("I-1", "S-A-AMOUNT"),
        _back_action(),
    ]
    assert actions == [
        {
            "action": "save_review",
            "issue_id": "I-1",
            "source_id": "S-A-AMOUNT",
            "value": "120000.00",
        },
        {"action": "confirm_match", "issue_id": "I-1", "source_id": "S-A-AMOUNT"},
        {"action": "back"},
    ]
    assert "_missing" not in _as_mapping(view["reported_sources"][0])

    missing = _build_view(
        {
            "issue_id": "I-2",
            "reason_code": "MISSING_SUPPORT",
            "reported_source_ids": ["S-ONLY-REPORT"],
            "delta": None,
        },
        [sources[0]],
    )
    assert missing["delta_kind"] == "unknown"
    assert _format_delta(missing["delta"], missing["currency"]) == "—"

    print("review_screen: самопроверка пройдена")
