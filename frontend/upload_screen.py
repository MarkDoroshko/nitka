"""Экран загрузки комплекта документов."""

if __package__:
    from .nitka_ui.upload_selfcheck import _run_self_checks
    from .nitka_ui.upload_state import DEFAULT_DEMO_SCENARIOS as DEFAULT_DEMO_SCENARIOS
    from .nitka_ui.upload_state import DemoAction as DemoAction
    from .nitka_ui.upload_state import DemoScenario as DemoScenario
    from .nitka_ui.upload_state import RunAction as RunAction
    from .nitka_ui.upload_state import UploadAction as UploadAction
    from .nitka_ui.upload_state import UploadedFileLike as UploadedFileLike
    from .nitka_ui.upload_state import reset_upload_state as reset_upload_state
    from .nitka_ui.upload_widgets import render_upload as render_upload
else:
    from nitka_ui.upload_selfcheck import _run_self_checks
    from nitka_ui.upload_state import DEFAULT_DEMO_SCENARIOS as DEFAULT_DEMO_SCENARIOS
    from nitka_ui.upload_state import DemoAction as DemoAction
    from nitka_ui.upload_state import DemoScenario as DemoScenario
    from nitka_ui.upload_state import RunAction as RunAction
    from nitka_ui.upload_state import UploadAction as UploadAction
    from nitka_ui.upload_state import UploadedFileLike as UploadedFileLike
    from nitka_ui.upload_state import reset_upload_state as reset_upload_state
    from nitka_ui.upload_widgets import render_upload as render_upload


__all__ = [
    "DEFAULT_DEMO_SCENARIOS",
    "DemoAction",
    "DemoScenario",
    "RunAction",
    "UploadAction",
    "UploadedFileLike",
    "render_upload",
    "reset_upload_state",
]


if __name__ == "__main__":
    _run_self_checks()
