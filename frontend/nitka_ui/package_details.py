"""Evidence inspection and amount corrections for a stored revision."""

from __future__ import annotations

import logging
from decimal import Decimal

import streamlit as st
from PIL import Image

from nitka.amounts import normalize_amount
from nitka.contracts.analysis import Check
from nitka.contracts.extraction import OcrLocation, PageLocation
from nitka.corrections import AmountCorrectionError
from nitka.pipeline import RULES_VERSION
from nitka.storage import (
    RevisionConflictError,
    StorageError,
    StoredRun,
    correct_run_amount,
)

from .display import REVIEW_STATUS_LABELS, format_money
from .package_evidence import check_evidence, page_preview, source_address, source_texts

_LOGGER = logging.getLogger(__name__)


def show_check(
    stored: StoredRun, check: Check, key: str, *, current: bool
) -> StoredRun | None:
    result = stored.analysis
    evidence = check_evidence(
        check, {source.source_id: source for source in result.sources}
    )
    files = {file.file_id: file for file in result.files}
    groups = (
        ("КФЗ", evidence.reported),
        ("По документам / расчёту", evidence.supported),
        ("Дополнительные материалы", evidence.other),
    )
    sources = {}
    labels = {}
    for group, items in groups:
        if items:
            count = len({source.file_id for source in items})
            st.caption(f"{group} · файлов: {count}")
        for source in items:
            sources[source.source_id] = source
            labels.setdefault(
                source.source_id,
                f"{group} · {files[source.file_id].name} · {source_address(source)}",
            )
    if not sources:
        st.info("Для этой проверки нет сохранённых источников.")
        return None

    selected = st.selectbox(
        "Источник",
        list(sources),
        format_func=labels.__getitem__,
        key=f"{key}_source",
    )
    source = sources[selected]
    source_key = f"{key}_{selected}"
    file = files[source.file_id]
    st.text(f"{file.name} · {source_address(source)}")
    st.caption(
        f"Значение в редакции · {source.value or '—'} · {REVIEW_STATUS_LABELS[source.review_status]}"
    )
    for address, text in source_texts(stored, source):
        st.caption(address)
        st.text(text)

    path = stored.inputs.get(source.file_id)
    if path is None:
        st.warning("Оригинал источника отсутствует в хранилище.")
    elif isinstance(source.location, (PageLocation, OcrLocation)) and st.checkbox(
        "Показать страницу оригинала", key=f"{source_key}_preview"
    ):
        try:
            image, highlighted = page_preview(path, source.location)
            st.image(image)
            if isinstance(source.location, OcrLocation) and not highlighted:
                st.caption("Координаты OCR-области недоступны.")
        except (OSError, ValueError, Image.DecompressionBombError):
            _LOGGER.exception("Cannot preview source %s of %s", selected, stored.run_id)
            st.warning("Не удалось показать страницу оригинала.")
    if path is not None:
        try:
            st.download_button(
                "Скачать оригинал источника",
                path.read_bytes(),
                file_name=file.name,
                key=f"{source_key}_original",
            )
        except OSError:
            _LOGGER.exception("Cannot read input %s of %s", file.file_id, stored.run_id)
            st.warning("Не удалось прочитать оригинал источника.")

    # A report field can be evidence without being a monetary row.
    if not any(row.amount_source_id == selected for row in stored.extraction.rows):
        return None
    if result.rules_version != RULES_VERSION:
        st.info("Для правки нужна редакция с текущей версией правил.")
        return None
    if not current:
        st.info("Для правки откройте последнюю редакцию.")
        return None

    with st.form(f"{source_key}_correct"):
        value = st.text_input("Новая сумма", value=source.value or "", max_chars=100)
        comment = st.text_area("Комментарий к исправлению", max_chars=2000)
        submitted = st.form_submit_button("Сохранить исправление")
    if not submitted:
        return None
    if not comment.strip():
        st.error("Укажите причину исправления в комментарии.")
        return None
    normalized = normalize_amount(value)
    if normalized is None:
        st.error("Введите сумму в формате 120 000,00.")
        return None
    old = normalize_amount(source.value)
    if old is not None and Decimal(normalized) == Decimal(old):
        st.error("Новая сумма совпадает с текущей.")
        return None
    try:
        return correct_run_amount(
            stored.root,
            stored.run_id,
            expected_revision=stored.revision,
            source_id=selected,
            value=normalized,
            comment=comment,
        )
    except RevisionConflictError:
        st.error("Появилась новая редакция. Откройте её перед исправлением.")
    except AmountCorrectionError:
        st.error("Это значение нельзя исправить.")
    except (OSError, StorageError, ValueError):
        _LOGGER.exception("Cannot correct %s of %s", selected, stored.run_id)
        st.error("Не удалось сохранить исправление. Проверьте хранилище.")
    return None


def show_history(stored: StoredRun) -> None:
    if not stored.history:
        return
    names = {file.file_id: file.name for file in stored.analysis.files}
    sources = {source.source_id: source for source in stored.analysis.sources}
    currency = stored.analysis.scope.currency
    with st.expander(f"История редакций · {len(stored.history)}"):
        for item in reversed(stored.history):
            source = sources[item.source_id]
            st.text(
                f"Редакция {item.revision} · {names[source.file_id]} · {source_address(source)}"
            )
            st.text(
                f"{format_money(item.old_value, currency)} → {format_money(item.new_value, currency)}"
            )
            st.text(item.comment)
