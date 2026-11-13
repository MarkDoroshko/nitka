from __future__ import annotations

from .upload_state import (
    _MAX_PACKAGE_BYTES,
    _input_problems,
    _normalize_scenarios,
    _store,
    _UploadState,
)
from .upload_widgets import _files_word, _format_bytes


def _run_self_checks() -> None:
    class FakeFile:
        type = "application/octet-stream"

        def __init__(self, name: str, data: bytes) -> None:
            self.name = name
            self._data = data
            self.size = len(data)

        def getvalue(self) -> bytes:
            return self._data

    scenarios = _normalize_scenarios(None)
    assert [scenario.id for scenario in scenarios] == [
        "clean",
        "discrepancy",
        "incomplete",
    ]

    custom = _normalize_scenarios([{"title": "Свой сценарий"}])
    assert custom[0].id == "Свой сценарий"

    class FakeKit:
        kit_id = "from_adapter"
        title = "Комплект адаптера"
        description = "Описание"
        expected_label = "Ожидание"

    adapted = _normalize_scenarios([FakeKit()])
    assert adapted[0].id == "from_adapter"
    assert adapted[0].expected == "Ожидание"

    report = _store(FakeFile("report.pdf", b"report"))
    document = _store(FakeFile("act.pdf", b"act"))
    state = _UploadState(report=report, documents=[document])
    assert _input_problems(state) == []

    assert _input_problems(_UploadState()) == [
        "Добавьте одну КФЗ в PDF.",
        "Добавьте хотя бы один первичный документ.",
    ]
    assert _format_bytes(1024) == "1.0 КБ"
    assert _format_bytes(_MAX_PACKAGE_BYTES) == "25.0 МБ"
    assert _files_word(1) == "файл"
    assert _files_word(2) == "файла"
    assert _files_word(5) == "файлов"
    assert _files_word(11) == "файлов"

    print("upload_screen: self-checks passed")
