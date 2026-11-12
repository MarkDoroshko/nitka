"""Streamlit entry point for Nitka."""

if __package__:
    from .nitka_ui.actions import Action as Action
    from .nitka_ui.actions import Navigate as Navigate
    from .nitka_ui.actions import OpenKit as OpenKit
    from .nitka_ui.actions import ProcessingComplete as ProcessingComplete
    from .nitka_ui.actions import ResetAll as ResetAll
    from .nitka_ui.actions import ResetKit as ResetKit
    from .nitka_ui.actions import SaveCorrection as SaveCorrection
    from .nitka_ui.actions import ViewCheck as ViewCheck
    from .nitka_ui.app_state import APP_STATE_KEY as APP_STATE_KEY
    from .nitka_ui.application import main
    from .nitka_ui.catalog import screen_catalog as screen_catalog
    from .nitka_ui.evidence import screen_evidence as screen_evidence
    from .nitka_ui.export_screen import screen_export as screen_export
    from .nitka_ui.presentation import DECISION_SHORT_LABELS as DECISION_SHORT_LABELS
    from .nitka_ui.presentation import SHORT_REASON_LABELS as SHORT_REASON_LABELS
    from .nitka_ui.processing import screen_processing as screen_processing
    from .nitka_ui.results import screen_results as screen_results
else:
    from nitka_ui.actions import Action as Action
    from nitka_ui.actions import Navigate as Navigate
    from nitka_ui.actions import OpenKit as OpenKit
    from nitka_ui.actions import ProcessingComplete as ProcessingComplete
    from nitka_ui.actions import ResetAll as ResetAll
    from nitka_ui.actions import ResetKit as ResetKit
    from nitka_ui.actions import SaveCorrection as SaveCorrection
    from nitka_ui.actions import ViewCheck as ViewCheck
    from nitka_ui.app_state import APP_STATE_KEY as APP_STATE_KEY
    from nitka_ui.application import main
    from nitka_ui.catalog import screen_catalog as screen_catalog
    from nitka_ui.evidence import screen_evidence as screen_evidence
    from nitka_ui.export_screen import screen_export as screen_export
    from nitka_ui.presentation import DECISION_SHORT_LABELS as DECISION_SHORT_LABELS
    from nitka_ui.presentation import SHORT_REASON_LABELS as SHORT_REASON_LABELS
    from nitka_ui.processing import screen_processing as screen_processing
    from nitka_ui.results import screen_results as screen_results


if __name__ == "__main__":
    main()
