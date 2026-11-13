from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict

from nitka.extraction.limits import MAX_PACKAGE_BYTES as _MAX_PACKAGE_BYTES
from nitka.extraction.package import KFZ_SUFFIX, PACKAGE_SUPPORT_SUFFIXES

from .optional_streamlit import st
from .upload_display import _display_name, _file_type_label, _format_bytes, _suffix

_REPORT_TYPES = (KFZ_SUFFIX.lstrip("."),)
_SUPPORT_TYPES = tuple(sorted(suffix.lstrip(".") for suffix in PACKAGE_SUPPORT_SUFFIXES))
_STATE_KEY = "nitka_upload_state"
_SCENARIO_KEY = "nitka_upload_scenario"


class UploadedFileLike(Protocol):
    """Минимальная часть ``streamlit.runtime.uploaded_file_manager.UploadedFile``."""

    name: str
    size: int
    type: str

    def getvalue(self) -> bytes: ...


class RunAction(TypedDict):
    action: Literal["run"]
    report: UploadedFileLike
    documents: list[UploadedFileLike]


class DemoAction(TypedDict):
    action: Literal["demo"]
    scenario: str


UploadAction = RunAction | DemoAction


@dataclass(frozen=True, slots=True)
class DemoScenario:
    """Описание готового демонстрационного комплекта."""

    id: str
    title: str
    description: str = ""
    expected: str = ""


DEFAULT_DEMO_SCENARIOS: tuple[DemoScenario, ...] = (
    DemoScenario(
        id="clean",
        title="Чистый комплект",
        description=(
            "Два акта подтверждают обе строки отчёта. Обязательные проверки "
            "завершены, расхождений нет."
        ),
        expected="Расхождения не выявлены",
    ),
    DemoScenario(
        id="discrepancy",
        title="Расхождение на 30 000 ₽",
        description=(
            "В отчёте аренда указана как 150 000,00 ₽, а акт подтверждает "
            "120 000,00 ₽. Вторая строка сходится. Это основной сценарий "
            "демонстрации."
        ),
        expected="Выявлено расхождение +30 000,00 ₽",
    ),
    DemoScenario(
        id="incomplete",
        title="Неполный комплект",
        description=(
            "Одна строка подтверждена, по второй найдено два равнозначных акта, "
            "по третьей первички нет. Одна страница акта не распознана. "
            "Зелёный статус запрещён."
        ),
        expected="Требуется ручная проверка",
    ),
)


@dataclass(frozen=True, slots=True)
class _StoredFile:
    file: UploadedFileLike
    digest: str
    size: int


@dataclass
class _UploadState:
    report: _StoredFile | None = None
    documents: list[_StoredFile] = field(default_factory=list)
    report_revision: int = 0
    documents_revision: int = 0
    notice: tuple[Literal["success", "warning"], str] | None = None


def _file_name(file: UploadedFileLike) -> str:
    name = getattr(file, "name", None)
    return str(name) if name else "Без имени"


def _file_size(file: UploadedFileLike) -> int:
    size = getattr(file, "size", None)
    if isinstance(size, int) and size >= 0:
        return size
    try:
        return len(file.getvalue())
    except (AttributeError, OSError, ValueError):
        return 0


def _digest(file: UploadedFileLike) -> str:
    try:
        return hashlib.sha256(file.getvalue()).hexdigest()
    except (AttributeError, OSError, ValueError):
        return f"{_file_name(file)}:{_file_size(file)}"


def _store(file: UploadedFileLike) -> _StoredFile:
    return _StoredFile(
        file=file,
        digest=_digest(file),
        size=_file_size(file),
    )


def _total_size(state: _UploadState) -> int:
    report_size = state.report.size if state.report is not None else 0
    return report_size + sum(document.size for document in state.documents)


def _normalize_scenarios(scenarios: Sequence[Any] | None) -> tuple[DemoScenario, ...]:
    if scenarios is None:
        return DEFAULT_DEMO_SCENARIOS

    normalized: list[DemoScenario] = []
    seen: set[str] = set()
    for index, item in enumerate(scenarios, start=1):
        if isinstance(item, DemoScenario):
            scenario = item
        elif isinstance(item, Mapping):
            title = str(item.get("title") or f"Сценарий {index}")
            scenario = DemoScenario(
                id=str(
                    item.get("id") or item.get("key") or item.get("kit_id") or title
                ),
                title=title,
                description=str(item.get("description") or ""),
                expected=str(
                    item.get("expected")
                    or item.get("expected_result")
                    or item.get("expected_label")
                    or ""
                ),
            )
        else:
            # Совместимость с KitInfo из demo_backend.
            title = getattr(item, "title", None)
            scenario_id = getattr(item, "kit_id", None) or getattr(item, "id", None)
            if not title or not scenario_id:
                continue
            scenario = DemoScenario(
                id=str(scenario_id),
                title=str(title),
                description=str(
                    getattr(item, "description", "")
                    or getattr(item, "subtitle", "")
                    or ""
                ),
                expected=str(
                    getattr(item, "expected", "")
                    or getattr(item, "expected_label", "")
                    or ""
                ),
            )

        if not scenario.id or scenario.id in seen:
            continue
        seen.add(scenario.id)
        normalized.append(scenario)

    return tuple(normalized) or DEFAULT_DEMO_SCENARIOS


def _input_problems(state: _UploadState) -> list[str]:
    problems: list[str] = []

    if state.report is None:
        problems.append("Добавьте одну КФЗ в PDF.")
    else:
        report_name = _file_name(state.report.file)
        if _suffix(report_name) not in _REPORT_TYPES:
            problems.append(
                f"Формат отчёта «{_file_type_label(report_name)}» не поддерживается."
            )
        if state.report.size == 0:
            problems.append("Файл отчёта пуст.")

    if not state.documents:
        problems.append("Добавьте хотя бы один первичный документ.")

    for document in state.documents:
        raw_name = _file_name(document.file)
        if _suffix(raw_name) not in _SUPPORT_TYPES:
            problems.append(
                f"Формат документа «{_display_name(raw_name)}» не поддерживается."
            )
        if document.size == 0:
            problems.append(f"Файл «{_display_name(raw_name)}» пуст.")

    total_size = _total_size(state)
    if total_size > _MAX_PACKAGE_BYTES:
        problems.append(
            f"Комплект занимает {_format_bytes(total_size)}, "
            f"а лимит — {_format_bytes(_MAX_PACKAGE_BYTES)}."
        )

    return problems


def _get_state(
    report: UploadedFileLike | None,
    documents: Sequence[UploadedFileLike],
) -> _UploadState:
    state = st.session_state.get(_STATE_KEY)
    if isinstance(state, _UploadState):
        return state

    state = _UploadState()
    if report is not None:
        state.report = _store(report)
    if documents:
        state.documents = [_store(document) for document in documents]
    st.session_state[_STATE_KEY] = state
    return state


def reset_upload_state() -> None:
    """Сбрасывает комплект и выбранный сценарий. Может вызываться из ``app.py``."""

    if st is None:  # pragma: no cover
        raise RuntimeError("Для экрана загрузки требуется Streamlit.")
    for key in (_STATE_KEY, _SCENARIO_KEY):
        st.session_state.pop(key, None)
