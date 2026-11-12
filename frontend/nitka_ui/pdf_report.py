from __future__ import annotations

from html import escape
from typing import Any, Sequence

from .demo_models import CheckView, ExportError, RunSnapshot
from .display import (
    CHECK_STATE_LABELS,
    CHECK_STATE_TONES,
    DECISION_LABELS,
    DECISION_TONES,
    EXECUTION_LABELS,
    REASON_LABELS,
    format_delta,
    format_money,
    operand_value,
    short_run_id,
)
from .pdf_fonts import _pdf_fonts


def _render_pdf(snapshot: RunSnapshot, views: Sequence[CheckView]) -> bytes:
    try:
        from io import BytesIO

        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            KeepTogether,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise ExportError(
            "Для PDF установите reportlab: pip install -r frontend/requirements.txt"
        ) from exc

    fonts = _pdf_fonts()
    result = snapshot.result
    kit = snapshot.kit
    scope = result.scope
    is_demo = kit.info.kit_id.startswith("demo:") or "демо" in kit.info.tags

    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "NitkaBody",
        parent=styles["BodyText"],
        fontName=fonts.regular,
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#24312b"),
        spaceAfter=3,
    )
    small = ParagraphStyle(
        "NitkaSmall",
        parent=body,
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#69756f"),
    )
    label = ParagraphStyle(
        "NitkaLabel",
        parent=body,
        fontName=fonts.bold,
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#69756f"),
    )
    title = ParagraphStyle(
        "NitkaTitle",
        parent=styles["Title"],
        fontName=fonts.bold,
        fontSize=21,
        leading=25,
        textColor=colors.HexColor("#17251f"),
        alignment=0,
        spaceAfter=5,
    )
    heading = ParagraphStyle(
        "NitkaHeading",
        parent=body,
        fontName=fonts.bold,
        fontSize=11.5,
        leading=15,
        textColor=colors.HexColor("#17251f"),
        spaceBefore=12,
        spaceAfter=5,
    )
    cell = ParagraphStyle(
        "NitkaCell",
        parent=body,
        fontSize=7.6,
        leading=9.6,
        spaceAfter=0,
    )
    cell_bold = ParagraphStyle(
        "NitkaCellBold",
        parent=cell,
        fontName=fonts.bold,
    )

    palette = {
        "success": colors.HexColor("#1f6f5c"),
        "danger": colors.HexColor("#b42318"),
        "warning": colors.HexColor("#a15c07"),
        "neutral": colors.HexColor("#52606d"),
    }
    decision_tone = DECISION_TONES.get(result.decision, "neutral")
    decision_color = palette[decision_tone]

    def p(text: str, style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(escape(text).replace("\n", "<br/>"), style)

    def colored(text: str, color: str, style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(f'<font color="{color}">{escape(text)}</font>', style)

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=17 * mm,
        rightMargin=17 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=f"Нитка — заключение {result.run_id}",
        author="Нитка · демонстрационный адаптер",
        subject=kit.info.title,
    )

    page_width = A4[0] - 34 * mm
    story: list[Any] = []

    def add_summary() -> None:
        story.append(
            p("НИТКА · ДОКАЗАТЕЛЬНАЯ СВЕРКА" + (" · ДЕМО" if is_demo else ""), small)
        )
        story.append(Spacer(1, 3 * mm))
        story.append(p("Заключение по результатам сверки", title))
        story.append(p(kit.info.title, heading))
        story.append(p(kit.info.description, body))
        story.append(Spacer(1, 3 * mm))

        meta_rows = [
            [
                p("RUN ID", label),
                p(short_run_id(result.run_id), cell),
                p("РЕДАКЦИЯ", label),
                p(str(result.revision), cell_bold),
                p("ПЕРИОД", label),
                p(scope.reporting_period, cell),
            ],
            [
                p("ПРАВИЛА", label),
                p(result.rules_version, cell),
                p("ИЗВЛЕЧЕНИЕ", label),
                p(result.extraction_version, cell),
                p("ВАЛЮТА / БАЗА", label),
                p(f"{scope.currency} / {scope.amount_basis}", cell),
            ],
        ]
        meta = Table(
            meta_rows, colWidths=[18 * mm, 38 * mm, 20 * mm, 32 * mm, 24 * mm, 42 * mm]
        )
        meta.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.append(meta)
        story.append(Spacer(1, 3 * mm))

        decision = Table(
            [[p(DECISION_LABELS.get(result.decision, result.decision), cell_bold)]],
            colWidths=[page_width],
        )
        decision.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.Color(0.95, 0.96, 0.93)),
                    ("LINEBEFORE", (0, 0), (0, -1), 3, decision_color),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story.append(decision)
        story.append(Spacer(1, 1 * mm))
        story.append(
            p(
                f"{EXECUTION_LABELS.get(result.execution_status, result.execution_status)} · "
                f"проверено строк: {result.coverage.checked_rows} из "
                f"{result.coverage.report_rows} · проверок: "
                f"{result.coverage.completed_checks} из {result.coverage.required_checks}",
                small,
            )
        )
        correct = sum(check.state == "PASS" for check in result.checks)
        errors = sum(check.state == "FAIL" for check in result.checks)
        unverified = sum(check.state == "NOT_RUN" for check in result.checks)
        story.append(
            p(
                f"Корректных проверок: {correct} · ошибочных: {errors} · "
                f"непроверенных: {unverified} · исключённых документов: "
                f"{len(result.excluded_documents)}",
                small,
            )
        )

    def add_checks() -> None:
        story.append(p("1. Проверки по строкам", heading))
        checks_rows: list[list[Any]] = [
            [
                p("Строка", label),
                p("Статус", label),
                p("Заявлено", label),
                p("Подтверждено", label),
                p("Отклонение", label),
                p("Основание", label),
            ]
        ]
        for view in views:
            calculation = view.check.calculation
            reported_value = (
                calculation.actual
                if calculation
                else operand_value(view.check, "reported")
            )
            supported_value = (
                calculation.expected
                if calculation
                else operand_value(view.check, "supported")
            )
            reason = REASON_LABELS.get(view.check.reason_code, view.check.reason_code)
            status_color = {
                "success": "#1f6f5c",
                "danger": "#b42318",
                "warning": "#a15c07",
            }.get(CHECK_STATE_TONES.get(view.check.state, "neutral"), "#52606d")
            checks_rows.append(
                [
                    p(f"{view.row_label} · {view.report_row_id}", cell),
                    colored(
                        CHECK_STATE_LABELS.get(view.check.state, view.check.state),
                        status_color,
                        cell,
                    ),
                    p(format_money(reported_value, scope.currency), cell),
                    p(format_money(supported_value, scope.currency), cell),
                    colored(
                        format_delta(
                            calculation.delta if calculation else None, scope.currency
                        ),
                        status_color,
                        cell,
                    ),
                    p(reason, cell),
                ]
            )
        checks_table = Table(
            checks_rows,
            colWidths=[42 * mm, 20 * mm, 24 * mm, 26 * mm, 24 * mm, 38 * mm],
            repeatRows=1,
        )
        checks_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eee9de")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d8d2c5")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(checks_table)

    def add_processing_issues() -> None:
        story.append(p("2. Ошибки обработки и вопросы", heading))
        if result.issues:
            for issue in result.issues:
                story.append(
                    KeepTogether(
                        [
                            p(
                                f"{issue.code}"
                                + (f" · {issue.file_id}" if issue.file_id else ""),
                                label,
                            ),
                            p(issue.detail, body),
                            Spacer(1, 1 * mm),
                        ]
                    )
                )
        else:
            story.append(p("Ошибок обработки нет.", body))

    def add_excluded_documents() -> None:
        story.append(p("3. Исключённые документы", heading))
        if not result.excluded_documents:
            story.append(p("Исключённых документов нет.", body))
            return
        rows: list[list[Any]] = [
            [
                p("Файл", label),
                p("Документ", label),
                p("Причина", label),
                p("Пояснение", label),
            ]
        ]
        for item in result.excluded_documents:
            rows.append(
                [
                    p(item.file_id, cell),
                    p(item.document_id, cell),
                    p(item.reason, cell),
                    p(item.detail, cell),
                ]
            )
        table = Table(
            rows,
            colWidths=[26 * mm, 30 * mm, 30 * mm, 88 * mm],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eee9de")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d8d2c5")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)

    def add_revision_history() -> None:
        if snapshot.history:
            story.append(p("4. История редакций", heading))
            for item in snapshot.history:
                story.append(
                    p(
                        f"Редакция {item.revision} · {item.row_label}: "
                        f"{format_money(item.old_value, scope.currency)} → "
                        f"{format_money(item.new_value, scope.currency)}. "
                        f"{item.comment or 'Без комментария.'}",
                        body,
                    )
                )

    add_summary()
    add_checks()
    add_processing_issues()
    add_excluded_documents()
    add_revision_history()

    story.append(Spacer(1, 6 * mm))
    story.append(
        p(
            (
                "Демонстрационный образец. Данные синтетические. "
                if is_demo
                else "Заключение по сохранённому запуску. "
            )
            + "PDF и JSON относятся к одной редакции run_id + revision; "
            "модель не формирует итоговый статус.",
            small,
        )
    )

    def draw_footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont(fonts.regular, 7)
        canvas.setFillColor(colors.HexColor("#69756f"))
        canvas.drawString(
            17 * mm,
            9 * mm,
            "Нитка · демонстрационный адаптер" if is_demo else "Нитка · заключение",
        )
        canvas.drawRightString(A4[0] - 17 * mm, 9 * mm, f"стр. {doc.page}")
        canvas.restoreState()

    try:
        document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    except ExportError:
        raise
    except Exception as exc:  # reportlab has several formatting-specific errors
        raise ExportError(f"Не удалось собрать PDF: {exc}") from exc
    return buffer.getvalue()
