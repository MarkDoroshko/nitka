from frontend.nitka_ui.display import REASON_LABELS
from frontend.nitka_ui.results_models import REASON_LABELS as RESULT_REASON_LABELS

TOTAL_REASONS = {
    "TOTAL_EXCEEDS_ITEMS",
    "TOTAL_BELOW_ITEMS",
    "TOTAL_MATCHES_ITEMS",
    "TOTAL_MISSING",
    "TOTAL_INVALID",
    "TOTAL_NEEDS_REVIEW",
    "NO_ITEMS",
    "ITEM_MISSING",
    "ITEM_INVALID",
    "ITEM_NEEDS_REVIEW",
    "FORMULA_UNVERIFIED",
    "PAGE_BOUNDARY_UNKNOWN",
}


def test_total_reason_codes_have_user_facing_labels() -> None:
    assert TOTAL_REASONS <= set(REASON_LABELS)
    assert TOTAL_REASONS <= set(RESULT_REASON_LABELS)
