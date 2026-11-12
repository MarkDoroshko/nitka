"""Экран результата сверки."""

if __package__:
    from .nitka_ui.results_models import AMOUNT_BASIS_LABELS as AMOUNT_BASIS_LABELS
    from .nitka_ui.results_models import DECISION_DETAILS as DECISION_DETAILS
    from .nitka_ui.results_models import DECISION_LABELS as DECISION_LABELS
    from .nitka_ui.results_models import EXECUTION_LABELS as EXECUTION_LABELS
    from .nitka_ui.results_models import FILTER_STATES as FILTER_STATES
    from .nitka_ui.results_models import (
        PROCESSING_REASON_LABELS as PROCESSING_REASON_LABELS,
    )
    from .nitka_ui.results_models import REASON_LABELS as REASON_LABELS
    from .nitka_ui.results_models import STATE_LABELS as STATE_LABELS
    from .nitka_ui.results_models import Action as Action
    from .nitka_ui.results_models import CoverageView as CoverageView
    from .nitka_ui.results_models import ResultRow as ResultRow
    from .nitka_ui.results_models import ResultsView as ResultsView
    from .nitka_ui.results_selfcheck import _selfcheck_results
    from .nitka_ui.results_view import build_results_view as build_results_view
    from .nitka_ui.results_view import filter_rows as filter_rows
    from .nitka_ui.results_widgets import render_results as render_results
else:
    from nitka_ui.results_models import AMOUNT_BASIS_LABELS as AMOUNT_BASIS_LABELS
    from nitka_ui.results_models import DECISION_DETAILS as DECISION_DETAILS
    from nitka_ui.results_models import DECISION_LABELS as DECISION_LABELS
    from nitka_ui.results_models import EXECUTION_LABELS as EXECUTION_LABELS
    from nitka_ui.results_models import FILTER_STATES as FILTER_STATES
    from nitka_ui.results_models import (
        PROCESSING_REASON_LABELS as PROCESSING_REASON_LABELS,
    )
    from nitka_ui.results_models import REASON_LABELS as REASON_LABELS
    from nitka_ui.results_models import STATE_LABELS as STATE_LABELS
    from nitka_ui.results_models import Action as Action
    from nitka_ui.results_models import CoverageView as CoverageView
    from nitka_ui.results_models import ResultRow as ResultRow
    from nitka_ui.results_models import ResultsView as ResultsView
    from nitka_ui.results_selfcheck import _selfcheck_results
    from nitka_ui.results_view import build_results_view as build_results_view
    from nitka_ui.results_view import filter_rows as filter_rows
    from nitka_ui.results_widgets import render_results as render_results


if __name__ == "__main__":
    _selfcheck_results()
