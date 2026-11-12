from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_METHODS = {
    "TABLE": "таблица",
    "PDF_TEXT": "текст PDF",
    "VISION": "распознавание",
    "MANUAL": "ручной ввод",
}

_REVIEW_STATUSES = {
    "UNREVIEWED": "не проверено",
    "NEEDS_REVIEW": "нужна проверка",
    "CONFIRMED": "подтверждено",
    "CORRECTED": "исправлено",
}


def _field(obj: object, *names: str, default: Any = None) -> Any:
    """Вернуть первое непустое поле из словаря или объекта."""

    if isinstance(obj, Mapping):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
        return default

    for name in names:
        try:
            value = getattr(obj, name)
        except AttributeError:
            continue
        if value is not None:
            return value
    return default


def _as_mapping(obj: object) -> Mapping[str, Any]:
    if isinstance(obj, Mapping):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        data = dump()
        if isinstance(data, Mapping):
            return data
    try:
        data = vars(obj)
    except TypeError:
        return {}
    return data if isinstance(data, Mapping) else {}


def _as_sequence(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, (str, bytes)):
        return [value]
    try:
        return list(value)  # type: ignore[arg-type]
    except TypeError:
        return [value]


def _ids(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Mapping):
        value = list(value.values())
    return [str(item) for item in _as_sequence(value) if item]


def _source_id(source: object) -> str:
    return str(_field(source, "source_id", "id", default="") or "")


def _coerce_sources(sources: object) -> list[Any]:
    if sources is None:
        return []
    if isinstance(sources, Mapping):
        if "source_id" in sources:
            return [sources]
        return list(sources.values())
    if isinstance(sources, (str, bytes)):
        return []
    try:
        return list(sources)  # type: ignore[arg-type]
    except TypeError:
        return [sources]


def _operand(issue: object, name: str) -> object | None:
    for operand in _as_sequence(_field(issue, "operands", default=[])):
        if _field(operand, "name") == name:
            return operand
    return None


def _kind(source: object | None) -> str:
    if source is None:
        return ""
    location = _as_mapping(_field(source, "location", default={}))
    return str(
        _field(source, "kind", default=_field(location, "kind", default="")) or ""
    )


def _location_text(source: object | None) -> str:
    if source is None:
        return "источник не передан"
    location = _as_mapping(_field(source, "location", default={}))
    kind = _kind(source)
    if kind == "CELL":
        sheet = _field(location, "sheet")
        cell = _field(location, "cell", default="")
        return f"лист {sheet} · {cell}" if sheet else str(cell or "ячейка")
    if kind == "PAGE":
        page = _field(location, "page", default="")
        return f"стр. {page}" if page else "страница"
    return "адрес не указан"


def _short_location(source: object | None) -> str:
    text = _location_text(source)
    if text.startswith("лист "):
        text = text.removeprefix("лист ")
    return text


def _file_name(source: object | None) -> str:
    if source is None:
        return ""
    name = _field(source, "file_name", "name", "document_name")
    if name:
        return str(name)
    file_id = _field(source, "file_id")
    return str(file_id) if file_id else ""


def _method_text(source: object | None) -> str:
    method = str(_field(source, "method", default="") or "")
    return _METHODS.get(method, method.lower() or "не указан")


def _review_status_text(source: object | None) -> str:
    status = str(_field(source, "review_status", default="") or "")
    return _REVIEW_STATUSES.get(status, status.lower() or "не проверено")


def _select_sources(
    issue: object, sources: object
) -> tuple[list[object], list[object]]:
    """Выделить источник отчёта и подтверждающие документы."""

    all_sources = _coerce_sources(sources)
    lookup = {
        _source_id(source): source for source in all_sources if _source_id(source)
    }

    reported_ids = _ids(
        _field(
            issue,
            "reported_source_ids",
            "report_source_ids",
            "reported_source_id",
            "report_source_id",
        )
    )
    support_ids = _ids(
        _field(
            issue,
            "support_source_ids",
            "supported_source_ids",
            "support_source_id",
            "supported_source_id",
        )
    )

    reported_operand = _operand(issue, "reported")
    supported_operand = _operand(issue, "supported")
    if not reported_ids and reported_operand is not None:
        reported_ids = _ids(_field(reported_operand, "source_ids", "source_id"))
    if not support_ids and supported_operand is not None:
        support_ids = _ids(_field(supported_operand, "source_ids", "source_id"))

    evidence_ids = _ids(_field(issue, "evidence_ids", "source_ids"))

    if not reported_ids and not support_ids:
        single_source = _field(issue, "source_id")
        if single_source:
            support_ids = [str(single_source)]

    if reported_ids and not support_ids:
        support_ids = [
            source_id for source_id in evidence_ids if source_id not in reported_ids
        ]

    if support_ids and not reported_ids:
        candidates = [
            source_id for source_id in evidence_ids if source_id not in support_ids
        ]
        cells = [
            source_id
            for source_id in candidates
            if _kind(lookup.get(source_id)) == "CELL"
        ]
        if cells or candidates:
            reported_ids = [(cells or candidates)[0]]

    if not reported_ids and not support_ids:
        available = [_source_id(source) for source in all_sources if _source_id(source)]
        cells = [
            source_id
            for source_id in available
            if _kind(lookup.get(source_id)) == "CELL"
        ]
        if cells:
            reported_ids = [cells[0]]
            support_ids = [
                source_id for source_id in available if source_id != cells[0]
            ]
        elif available:
            reported_ids = [available[0]]
            support_ids = available[1:]

    def resolve(source_id: str) -> object:
        return lookup.get(source_id, {"source_id": source_id, "_missing": True})

    reported = [resolve(source_id) for source_id in reported_ids]
    support = [resolve(source_id) for source_id in support_ids]
    return reported, support
