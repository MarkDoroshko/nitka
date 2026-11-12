from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from .review_sources import (
    _as_mapping,
    _field,
    _file_name,
    _method_text,
    _review_status_text,
    _short_location,
)
from .review_view import (
    _finding,
    _format_amount,
    _format_delta,
    _state_label,
    _support_note,
)


def _ledger_html(view: Mapping[str, Any]) -> str:
    state_label, state_class = _state_label(view)
    delta_class = view["delta_kind"]
    reason = view["reason_code"]
    title = _esc(view["title"])
    detail = view["detail"]
    detail_html = (
        f'<p class="nr-detail">{_esc(detail)}</p>'
        if detail and detail != view["title"]
        else ""
    )
    reported_note = (
        _short_location(view["reported_sources"][0])
        if view["reported_sources"]
        else "источник не передан"
    )
    support_note = _support_note(view["support_sources"])
    return f"""
<div class="nitka-review nr-ledger-block">
  <div class="nr-head">
    <div class="nr-head__copy">
      <p class="nr-eyebrow">Замечание сверки{(" · " + _esc(reason)) if reason else ""}</p>
      <h2 class="nr-title">{title}</h2>
      {detail_html}
    </div>
    <span class="nr-state nr-state--{state_class}">{state_label}</span>
  </div>

  <div class="nr-ledger" role="group" aria-label="Сравнение сумм">
    <section class="nr-amount nr-amount--reported">
      <span class="nr-amount__label">Заявленная сумма</span>
      <span class="nr-amount__value">{_esc(_format_amount(view["reported"], view["currency"]))}</span>
      <span class="nr-amount__note">{_esc(reported_note)}</span>
    </section>
    <section class="nr-amount nr-amount--supported">
      <span class="nr-amount__label">Подтверждённая сумма</span>
      <span class="nr-amount__value">{_esc(_format_amount(view["supported"], view["currency"]))}</span>
      <span class="nr-amount__note">{_esc(support_note)}</span>
    </section>
    <section class="nr-amount nr-amount--delta nr-amount--{delta_class}">
      <span class="nr-amount__label">Разница</span>
      <span class="nr-amount__value">{_esc(_format_delta(view["delta"], view["currency"]))}</span>
      <span class="nr-amount__note">{_esc(_finding(view))}</span>
    </section>
  </div>
</div>
"""


def _source_meta_html(source: object | None) -> str:
    if source is None:
        return ""
    file_name = _file_name(source)
    file_html = (
        f'<span class="nr-source__file">{_esc(file_name)}</span>' if file_name else ""
    )
    return f"""
  <div class="nr-source__meta">
    {file_html}
    <span>{_esc(_method_text(source))}</span>
    <span>·</span>
    <span>{_esc(_review_status_text(source))}</span>
  </div>"""


def _missing_source_html(kind: str) -> str:
    return f"""
<div class="nitka-review nr-doc nr-doc--missing">
  <div class="nr-doc__head">
    <span class="nr-doc__kind">{_esc(kind)}</span>
  </div>
  <p class="nr-empty">Источник не передан в результат сверки.</p>
</div>"""


def _cell_html(source: object | None) -> str:
    if source is None:
        return _missing_source_html("Ячейка отчёта")
    if _as_mapping(source).get("_missing"):
        return _missing_source_html("Ячейка отчёта")

    location = _as_mapping(_field(source, "location", default={}))
    sheet = _field(location, "sheet")
    cell = str(_field(location, "cell", default="") or "—")
    raw_text = _field(source, "raw_text")
    value = _field(source, "value")
    sheet_html = f'<span class="nr-chip">Лист {_esc(sheet)}</span>' if sheet else ""
    return f"""
<div class="nitka-review nr-doc nr-doc--cell">
  <div class="nr-doc__head">
    <span class="nr-doc__kind">Ячейка отчёта</span>
    <span class="nr-doc__loc">{sheet_html}<span class="nr-chip nr-chip--strong">{_esc(cell)}</span></span>
  </div>
  <div class="nr-cell">
    <span class="nr-cell__ref">{_esc(cell)}</span>
    <span class="nr-cell__body">
      <span class="nr-cell__raw">{_esc(raw_text if raw_text not in (None, "") else "—")}</span>
      <span class="nr-cell__norm">Нормализовано: {_esc(value if value is not None else "—")}</span>
    </span>
  </div>
  {_source_meta_html(source)}
</div>"""


def _page_html(source: object | None) -> str:
    if source is None:
        return _missing_source_html("Страница документа")
    if _as_mapping(source).get("_missing"):
        return _missing_source_html("Страница документа")

    location = _as_mapping(_field(source, "location", default={}))
    page = _field(location, "page")
    document = _field(source, "document_id", "file_name", "file_id", default="Документ")
    raw_text = _field(source, "raw_text")
    value = _field(source, "value")
    page_label = f"стр. {page}" if page else "страница не указана"
    return f"""
<div class="nitka-review nr-doc nr-doc--page">
  <div class="nr-doc__head">
    <span class="nr-doc__kind">Страница акта</span>
    <span class="nr-chip nr-chip--strong">{_esc(page_label)}</span>
  </div>
  <div class="nr-paper">
    <span class="nr-paper__document">{_esc(document)}</span>
    <blockquote class="nr-paper__quote">{_esc(raw_text if raw_text not in (None, "") else "Текст на странице не распознан")}</blockquote>
    <span class="nr-paper__norm">Извлечено: {_esc(value if value is not None else "—")}</span>
  </div>
  {_source_meta_html(source)}
</div>"""


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)
