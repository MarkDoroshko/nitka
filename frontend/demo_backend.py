"""Demo adapter for the Nitka Streamlit frontend."""

if __package__:
    from .nitka_ui.demo_backend import KIT_ORDER as KIT_ORDER
    from .nitka_ui.demo_backend import DemoAdapter as DemoAdapter
    from .nitka_ui.demo_backend import _selftest
    from .nitka_ui.demo_models import AdapterError as AdapterError
    from .nitka_ui.demo_models import CheckView as CheckView
    from .nitka_ui.demo_models import CorrectionError as CorrectionError
    from .nitka_ui.demo_models import CorrectionRecord as CorrectionRecord
    from .nitka_ui.demo_models import DemoKit as DemoKit
    from .nitka_ui.demo_models import DocumentPreview as DocumentPreview
    from .nitka_ui.demo_models import ExportError as ExportError
    from .nitka_ui.demo_models import FrontendAdapter as FrontendAdapter
    from .nitka_ui.demo_models import KitInfo as KitInfo
    from .nitka_ui.demo_models import ProcessingStage as ProcessingStage
    from .nitka_ui.demo_models import RunSnapshot as RunSnapshot
    from .nitka_ui.demo_models import SourceView as SourceView
    from .nitka_ui.display import CHECK_STATE_LABELS as CHECK_STATE_LABELS
    from .nitka_ui.display import CHECK_STATE_TONES as CHECK_STATE_TONES
    from .nitka_ui.display import CURRENCY_SYMBOLS as CURRENCY_SYMBOLS
    from .nitka_ui.display import DECISION_LABELS as DECISION_LABELS
    from .nitka_ui.display import DECISION_TONES as DECISION_TONES
    from .nitka_ui.display import EXECUTION_LABELS as EXECUTION_LABELS
    from .nitka_ui.display import FILE_ROLE_LABELS as FILE_ROLE_LABELS
    from .nitka_ui.display import FILE_STATUS_LABELS as FILE_STATUS_LABELS
    from .nitka_ui.display import MATCH_STATE_LABELS as MATCH_STATE_LABELS
    from .nitka_ui.display import METHOD_LABELS as METHOD_LABELS
    from .nitka_ui.display import REASON_LABELS as REASON_LABELS
    from .nitka_ui.display import REVIEW_STATUS_LABELS as REVIEW_STATUS_LABELS
    from .nitka_ui.display import format_delta as format_delta
    from .nitka_ui.display import format_money as format_money
    from .nitka_ui.display import operand_value as operand_value
    from .nitka_ui.display import short_run_id as short_run_id
    from .nitka_ui.display import source_location_text as source_location_text
else:
    from nitka_ui.demo_backend import KIT_ORDER as KIT_ORDER
    from nitka_ui.demo_backend import DemoAdapter as DemoAdapter
    from nitka_ui.demo_backend import _selftest
    from nitka_ui.demo_models import AdapterError as AdapterError
    from nitka_ui.demo_models import CheckView as CheckView
    from nitka_ui.demo_models import CorrectionError as CorrectionError
    from nitka_ui.demo_models import CorrectionRecord as CorrectionRecord
    from nitka_ui.demo_models import DemoKit as DemoKit
    from nitka_ui.demo_models import DocumentPreview as DocumentPreview
    from nitka_ui.demo_models import ExportError as ExportError
    from nitka_ui.demo_models import FrontendAdapter as FrontendAdapter
    from nitka_ui.demo_models import KitInfo as KitInfo
    from nitka_ui.demo_models import ProcessingStage as ProcessingStage
    from nitka_ui.demo_models import RunSnapshot as RunSnapshot
    from nitka_ui.demo_models import SourceView as SourceView
    from nitka_ui.display import CHECK_STATE_LABELS as CHECK_STATE_LABELS
    from nitka_ui.display import CHECK_STATE_TONES as CHECK_STATE_TONES
    from nitka_ui.display import CURRENCY_SYMBOLS as CURRENCY_SYMBOLS
    from nitka_ui.display import DECISION_LABELS as DECISION_LABELS
    from nitka_ui.display import DECISION_TONES as DECISION_TONES
    from nitka_ui.display import EXECUTION_LABELS as EXECUTION_LABELS
    from nitka_ui.display import FILE_ROLE_LABELS as FILE_ROLE_LABELS
    from nitka_ui.display import FILE_STATUS_LABELS as FILE_STATUS_LABELS
    from nitka_ui.display import MATCH_STATE_LABELS as MATCH_STATE_LABELS
    from nitka_ui.display import METHOD_LABELS as METHOD_LABELS
    from nitka_ui.display import REASON_LABELS as REASON_LABELS
    from nitka_ui.display import REVIEW_STATUS_LABELS as REVIEW_STATUS_LABELS
    from nitka_ui.display import format_delta as format_delta
    from nitka_ui.display import format_money as format_money
    from nitka_ui.display import operand_value as operand_value
    from nitka_ui.display import short_run_id as short_run_id
    from nitka_ui.display import source_location_text as source_location_text


if __name__ == "__main__":
    _selftest()
