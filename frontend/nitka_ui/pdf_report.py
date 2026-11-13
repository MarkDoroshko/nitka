from __future__ import annotations

from html import escape
from typing import Any, Mapping, Sequence

from nitka.contracts.analysis import AnalysisResult

from .demo_models import CheckView, CorrectionRecord, ExportError, RunSnapshot
from .display import (
    CHECK_STATE_LABELS,
    DECISION_LABELS,
    DECISION_TONES,
    EXECUTION_LABELS,
    REASON_LABELS,
    format_delta,
    format_money,
    operand_value,
)
from .pdf_fonts import _pdf_fonts


def _render_pdf(snapshot: RunSnapshot, views: Sequence[CheckView]) -> bytes:
    result = snapshot.result
    rows = []
    for view in views:
        check = view.check
        calculation = check.calculation
        rows.append(
            (
                f"{view.row_label} · {view.report_row_id}",
                CHECK_STATE_LABELS[check.state],
                format_money(
                    calculation.actual
                    if calculation
                    else operand_value(check, "reported"),
                    result.scope.currency,
                ),
                format_money(
                    calculation.expected
                    if calculation
                    else operand_value(check, "supported"),
                    result.scope.currency,
                ),
                format_delta(
                    calculation.delta if calculation else None, result.scope.currency
                ),
                REASON_LABELS.get(check.reason_code, check.reason_code),
            )
        )
    return render_report_pdf(
        result,
        snapshot.kit.info.title,
        snapshot.kit.info.description,
        rows,
        snapshot.history,
        demo=True,
    )


def render_report_pdf(
    result: AnalysisResult,
    report_title: str,
    description: str,
    rows: Sequence[tuple[str, str, str, str, str, str]],
    history: Sequence[CorrectionRecord],
    *,
    exclusions: Mapping[str, str] | None = None,
    evidence: Sequence[tuple[str, Sequence[str]]] = (),
    demo: bool = False,
) -> bytes:
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
    scope = result.scope
    file_names = {file.file_id: file.name for file in result.files}

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

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=17 * mm,
        rightMargin=17 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=f"Нитка — заключение {result.run_id}",
        author="Нитка",
        subject=report_title,
    )

    page_width = A4[0] - 34 * mm
    story: list[Any] = []

    def add_summary() -> None:
        story.append(
            p("НИТКА · ДОКАЗАТЕЛЬНАЯ СВЕРКА" + (" · ДЕМО" if demo else ""), small)
        )
        story.append(Spacer(1, 3 * mm))
        story.append(p("Заключение по результатам сверки", title))
        story.append(p(report_title, heading))
        story.append(p(description, body))
        story.append(Spacer(1, 3 * mm))
        story.append(p(f"Запуск {result.run_id} · Редакция {result.revision}", body))

        meta_rows = [
            [
                p("ПЕРИОД", label),
                p(scope.reporting_period, cell),
                p("ВАЛЮТА / БАЗА", label),
                p(f"{scope.currency} / {scope.amount_basis}", cell),
            ],
            [
                p("ПРАВИЛА", label),
                p(result.rules_version, cell),
                p("ИЗВЛЕЧЕНИЕ", label),
                p(result.extraction_version, cell),
            ],
        ]
        meta = Table(meta_rows, colWidths=[21 * mm, 64 * mm, 34 * mm, 55 * mm])
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

    def add_checks() -> None:
        story.append(p("Проверки по строкам", heading))
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
        checks_rows.extend([p(value, cell) for value in row] for row in rows)
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
        story.append(p("Ограничения и замечания", heading))
        if result.coverage.completed_checks < result.coverage.required_checks:
            story.append(
                p(
                    "Часть показателей требует дополнительных документов или ручной проверки. Основания указаны в таблице проверок.",
                    body,
                )
            )
        if result.issues:
            for issue in result.issues:
                story.append(
                    KeepTogether(
                        [
                            p(
                                f"{issue.code}"
                                + (
                                    f" · {file_names.get(issue.file_id, issue.file_id)}"
                                    if issue.file_id
                                    else ""
                                ),
                                label,
                            ),
                            p(issue.detail, body),
                            Spacer(1, 1 * mm),
                        ]
                    )
                )
        else:
            story.append(p("Ошибок обработки нет.", body))

    def add_revision_history() -> None:
        if history:
            story.append(p("История редакций", heading))
            for item in history:
                story.append(
                    p(
                        f"Редакция {item.revision} · {item.row_label}: "
                        f"{format_money(item.old_value, scope.currency)} → "
                        f"{format_money(item.new_value, scope.currency)}. "
                        f"{item.comment or 'Без комментария.'}",
                        body,
                    )
                )

    def add_exclusions() -> None:
        if result.excluded_documents:
            story.append(p("Исключено из расчёта", heading))
        for item in result.excluded_documents:
            name = file_names.get(item.file_id, item.file_id)
            reason = (exclusions or {}).get(item.reason, item.reason)
            story.append(
                KeepTogether([p(f"{name} · {reason}", label), p(item.detail, body)])
            )

    add_summary()
    add_checks()
    add_exclusions()
    add_processing_issues()
    add_revision_history()
    if evidence:
        story.append(p("Адреса источников", heading))
        for check_label, addresses in evidence:
            story.append(p(check_label, label))
            story.extend(p(address, small) for address in addresses)

    if demo:
        story.append(Spacer(1, 6 * mm))
        story.append(p("Демонстрационный образец. Данные синтетические.", small))

    def draw_footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont(fonts.regular, 7)
        canvas.setFillColor(colors.HexColor("#69756f"))
        canvas.drawString(17 * mm, 9 * mm, f"Нитка · редакция {result.revision}")
        canvas.drawRightString(A4[0] - 17 * mm, 9 * mm, f"стр. {doc.page}")
        canvas.restoreState()

    try:
        document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    except ExportError:
        raise
    except Exception as exc:  # reportlab has several formatting-specific errors
        raise ExportError(f"Не удалось собрать PDF: {exc}") from exc
    return buffer.getvalue()
