from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import pytest
from pypdf import PdfReader

from nitka.contracts.analysis import AnalysisResult, Match, Scope
from nitka.contracts.extraction import ExtractionResult
from nitka.extraction.ports import InputDocument
from nitka.extraction.registry import InputRegistry
from nitka.storage import (
    correct_run_amount,
    list_run_revisions,
    load_run,
    replay_run,
    run_and_save,
)

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

from frontend.nitka_ui.package_screen import _render_package_pdf  # noqa: E402

_APP = Path(__file__).with_name("app.py")


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    if not hasattr(AppTest, "file_uploader"):
        pytest.skip("Streamlit AppTest does not support file uploads in this version")
    monkeypatch.setenv("NITKA_RUNS_DIR", str(tmp_path))
    monkeypatch.delenv("NITKA_CASE_PROFILE", raising=False)
    return AppTest.from_file(str(_APP), default_timeout=30).run()


def test_upload_failure_preserves_selection_and_can_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path, monkeypatch)
    app.file_uploader[0].upload("КФЗ.pdf", b"invalid PDF").run()
    app.file_uploader[1].set_value(
        [
            ("09.pdf", b"same", "application/pdf"),
            ("10.pdf", b"same", "application/pdf"),
            ("Акт.docx", b"invalid DOCX", "application/octet-stream"),
        ]
    ).run()
    assert not app.exception

    blocked_root = tmp_path / "blocked"
    blocked_root.touch()
    with monkeypatch.context() as failing:
        failing.setenv("NITKA_RUNS_DIR", str(blocked_root))
        app.button(key="nitka_upload_run_submit").click().run()
        assert not app.exception
        assert "хранилищу" in app.error[0].value
        assert not app.button(key="nitka_upload_run_submit").disabled
        assert list(tmp_path.iterdir()) == [blocked_root]
    blocked_root.unlink()

    app.button(key="nitka_upload_run_submit").click().run(timeout=30)
    assert not app.exception
    assert "Обработка не удалась" in {item.value for item in app.markdown}
    (run_dir,) = tmp_path.iterdir()
    result = load_run(tmp_path, run_dir.name).analysis
    assert {file.name for file in result.files} == {
        "КФЗ.pdf",
        "09.pdf",
        "10.pdf",
        "Акт.docx",
    }

    app.radio(key="nitka_mode").set_value("Демонстрация").run()
    assert not app.exception and len(app.file_uploader) == 0
    app.radio(key="nitka_mode").set_value("Свои документы").run()
    assert not app.exception
    assert "Обработка не удалась" in {item.value for item in app.markdown}
    assert list(tmp_path.iterdir()) == [run_dir]

    app.button(key="nitka_package_new").click().run()
    assert not app.exception
    assert app.button(key="nitka_upload_run_submit").disabled
    assert list(tmp_path.iterdir()) == [run_dir]


def test_opened_revision_stays_pinned_after_another_session_saves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = Path(__file__).resolve().parents[1] / "examples" / "extraction.json"
    source_files = tmp_path / "files"
    source_files.mkdir()
    report = source_files / "report.xlsx"
    support = source_files / "act.pdf"
    report.write_bytes(b"report")
    support.write_bytes(b"support")

    def extract(documents: Sequence[InputDocument]) -> ExtractionResult:
        extraction = ExtractionResult.model_validate_json(fixture.read_bytes())
        registry = InputRegistry.register(documents)
        extraction.files = [
            entry.to_file_record("COMPLETED") for entry in registry.inputs
        ]
        extraction.sources[0].file_id = extraction.files[0].file_id
        extraction.sources[1].file_id = extraction.files[1].file_id
        return extraction

    def match(_: ExtractionResult) -> list[Match]:
        return [
            Match(
                match_id="match",
                report_row_id="R-1",
                support_row_ids=["A-1"],
                state="ACCEPTED",
            )
        ]

    root = tmp_path / "runs"
    result = run_and_save(
        root,
        [InputDocument(report, "REPORT"), InputDocument(support, "SUPPORT")],
        Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-10"),
        extractor=extract,
        matcher=match,
    )
    app = _app(root, monkeypatch)
    app.text_input[0].set_value(result.run_id)
    app.button(
        key="FormSubmitter:nitka_package_open-Открыть последнюю редакцию"
    ).click().run()
    assert not app.exception
    key = f"nitka_package_{result.run_id}_1"
    app.selectbox(key=f"{key}_check").set_value(result.checks[0].check_id).run()
    assert "Новая сумма" in {item.label for item in app.text_input}

    correct_run_amount(
        root,
        result.run_id,
        expected_revision=1,
        source_id="S-R-AMOUNT",
        value="120000.00",
        comment="Сверено",
    )
    app.run()
    assert not app.exception
    assert "Выявлены расхождения" in {item.value for item in app.subheader}
    assert "Новая сумма" not in {item.label for item in app.text_input}
    assert any("редакция 1" in item.value for item in app.caption)
    app.selectbox[0].set_value(2)
    app.button(key=f"FormSubmitter:{key}_revisions-Открыть редакцию").click().run()
    assert not app.exception
    assert any("редакция 2" in item.value for item in app.caption)
    assert "Новая сумма" in {item.label for item in app.text_input}
    assert "Расхождения не выявлены в проверенном объёме" in {
        item.value for item in app.subheader
    }


def test_organizer_case_through_upload_widgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_dir = os.environ.get("NITKA_PACKAGE_SMOKE_DIR")
    if not case_dir or not os.environ.get("NITKA_OCR_MODELS"):
        pytest.skip("Set NITKA_PACKAGE_SMOKE_DIR and NITKA_OCR_MODELS")
    app = _app(tmp_path, monkeypatch)
    (report,) = (Path(case_dir) / "report").iterdir()
    app.file_uploader[0].upload(
        report.name, report.read_bytes(), "application/pdf"
    ).run()
    support = sorted((Path(case_dir) / "support").iterdir())
    app.file_uploader[1].set_value(
        [(file.name, file.read_bytes(), "application/octet-stream") for file in support]
    ).run()
    assert not app.exception
    app.button(key="nitka_upload_run_submit").click().run(timeout=300)
    assert not app.exception
    assert "Выявлены расхождения" in {item.value for item in app.subheader}

    (run_dir,) = tmp_path.iterdir()
    result = load_run(tmp_path, run_dir.name).analysis
    assert result == replay_run(tmp_path, run_dir.name)
    assert len(result.files) == 23
    names = {file.file_id: file.name for file in result.files}
    assert {
        (names[item.file_id][:2], item.reason) for item in result.excluded_documents
    } == {
        ("10", "DUPLICATE"),
        ("14", "OTHER_PERIOD"),
        ("15", "OTHER_CONTRACT"),
    }
    checks = app.dataframe[0].value.set_index("Показатель")
    for label, amounts in {
        "0200 · Оплата труда": ("5 558 474,83", "5 558 474,83", "0,00"),
        "0300 · Страховые взносы": ("1 671 359,96", "1 671 359,96", "0,00"),
        "0800 · Общепроизводственные расходы": (
            "1 145 798,10",
            "1 133 298,10",
            "+12 500,00",
        ),
        "0900 · Общехозяйственные расходы": ("3 204 744,51", "3 204 744,51", "0,00"),
        "Итого по первичным документам": (
            "11 580 377,40",
            "11 567 877,40",
            "+12 500,00",
        ),
    }.items():
        row = checks.loc[label]
        for column, amount in zip(
            ("КФЗ", "По документам / расчёту", "Разница"), amounts
        ):
            assert row[column].replace("\xa0", " ") == amount + " ₽"
    assert (checks["Статус"] == "Не проверено").sum() == 9
    assert set(app.dataframe[1].value["Причина"]) == {
        "Дублирующая операция",
        "Другой период",
        "Другой договор",
    }

    key = f"nitka_package_{run_dir.name}_1"
    select_check = f"{key}_check"
    selected_0800 = "ARTICLE_AMOUNT:0800"
    selected_0900 = "ARTICLE_AMOUNT:0900"
    app.selectbox(key=select_check).set_value(selected_0800).run()
    source_picker = app.selectbox(key=f"{key}_{selected_0800}_source")
    assert sorted(option.split(" · ")[1][:2] for option in source_picker.options) == [
        "01",
        "04",
        "05",
        "06",
        "07",
    ]
    app.selectbox(key=select_check).set_value(selected_0900).run()
    assert sorted(
        option.split(" · ")[1][:2]
        for option in app.selectbox(key=f"{key}_{selected_0900}_source").options
    ) == ["01", "08", "09", "11", "12", "13"]

    sources = {source.source_id: source for source in result.sources}
    check = {check.check_id: check for check in result.checks}
    support_0800 = {
        names[sources[key].file_id][:2]: key
        for operand in check[selected_0800].operands
        if operand.name == "supported"
        for key in operand.source_ids
    }
    app.selectbox(key=select_check).set_value("ARTICLE_AMOUNT:0200").run()
    payroll = next(
        operand
        for operand in check["ARTICLE_AMOUNT:0200"].operands
        if operand.name == "supported"
    ).source_ids[0]
    app.selectbox(key=f"{key}_ARTICLE_AMOUNT:0200_source").set_value(payroll).run()
    assert {"Ведомость!C6", "Ведомость!C7"} <= {
        caption.value for caption in app.caption
    }

    app.selectbox(key=select_check).set_value(selected_0800).run()
    picker = f"{key}_{selected_0800}_source"
    app.selectbox(key=picker).set_value(support_0800["06"]).run()
    assert any("270" in item.value for item in app.text)
    assert any("абзац" in item.value or "ячейка" in item.value for item in app.caption)
    for prefix in ("04", "05"):
        source_id = support_0800[prefix]
        app.selectbox(key=picker).set_value(source_id).run()
        app.checkbox(key=f"{key}_{selected_0800}_{source_id}_preview").check().run()
        assert len(app.get("image")) == 1
        assert not any(
            "Координаты OCR-области недоступны" in item.value for item in app.caption
        )

    source_id = support_0800["05"]
    form = f"{key}_{selected_0800}_{source_id}_correct"
    submit = f"FormSubmitter:{form}-Сохранить исправление"
    amount = next(widget for widget in app.text_input if widget.label == "Новая сумма")
    amount.set_value("330 798,10")
    app.button(key=submit).click().run()
    assert any("Укажите причину" in item.value for item in app.error)
    assert list_run_revisions(tmp_path, run_dir.name) == (1,)
    app.text_area[0].set_value("Сверено с оригиналом <акт> & расчётом.")
    next(
        widget for widget in app.text_input if widget.label == "Новая сумма"
    ).set_value("318 298,1")
    app.button(key=submit).click().run()
    assert any("совпадает с текущей" in item.value for item in app.error)
    assert list_run_revisions(tmp_path, run_dir.name) == (1,)
    next(
        widget for widget in app.text_input if widget.label == "Новая сумма"
    ).set_value("330 798,10")
    app.button(key=submit).click().run()
    assert not app.exception and not app.error
    assert list_run_revisions(tmp_path, run_dir.name) == (2, 1)

    stored = load_run(tmp_path, run_dir.name, 2)
    assert stored.analysis == replay_run(tmp_path, run_dir.name, 2)
    assert stored.analysis.excluded_documents == result.excluded_documents
    updated = {check.check_id: check for check in stored.analysis.checks}
    assert (
        updated[selected_0800].state == updated["SUPPORT_TOTAL:OVERALL"].state == "PASS"
    )
    assert sum(check.state == "NOT_RUN" for check in updated.values()) == 9
    corrected = next(
        source for source in stored.analysis.sources if source.source_id == source_id
    )
    assert corrected.raw_text == sources[source_id].raw_text
    assert corrected.value == "330798.10"
    assert load_run(tmp_path, run_dir.name, 1).analysis == result

    # Reopen in a fresh session using only the run ID, even without the live profile.
    monkeypatch.setenv("NITKA_CASE_PROFILE", str(tmp_path / "missing-profile.json"))
    reopened = AppTest.from_file(str(_APP), default_timeout=30).run()
    reopened.text_input[0].set_value(run_dir.name)
    reopened.button(
        key="FormSubmitter:nitka_package_open-Открыть последнюю редакцию"
    ).click().run()
    assert not reopened.exception and not reopened.error
    assert any("редакция 2" in item.value for item in reopened.caption)
    key2 = f"nitka_package_{run_dir.name}_2"
    reopened.button(key=f"{key2}_pdf_prepare").click().run()
    assert not reopened.exception and not reopened.error
    assert [button.label for button in reopened.get("download_button")] == [
        "Скачать JSON",
        "Скачать PDF",
    ]

    pdf = tmp_path / "revision-2.pdf"
    pdf.write_bytes(_render_package_pdf(stored))
    contents = " ".join(
        " ".join(page.extract_text().split()) for page in PdfReader(pdf).pages
    )
    for text in (
        run_dir.name,
        "Редакция 2",
        "Дублирующая операция",
        "Другой период",
        "Другой договор",
        "Не проверено",
        "Сверено с оригиналом <акт> & расчётом.",
        "Адреса источников",
    ):
        assert text in contents
    assert (
        AnalysisResult.model_validate_json(stored.analysis.model_dump_json())
        == stored.analysis
    )
    reopened.selectbox[0].set_value(1)
    reopened.button(
        key=f"FormSubmitter:{key2}_revisions-Открыть редакцию"
    ).click().run()
    assert not reopened.exception and any(
        "редакция 1" in item.value for item in reopened.caption
    )
    assert [button.label for button in reopened.get("download_button")] == [
        "Скачать JSON"
    ]
    assert "Выявлены расхождения" in {item.value for item in reopened.subheader}
