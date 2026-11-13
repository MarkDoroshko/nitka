from __future__ import annotations

import os
from pathlib import Path

import pytest

from nitka.storage import load_run, replay_run

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

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
