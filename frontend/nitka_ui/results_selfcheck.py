from __future__ import annotations

from collections.abc import Sequence

from .results_view import build_results_view, filter_rows
from .results_widgets import render_results


def _selfcheck_results() -> None:
    import sys
    from pathlib import Path

    from nitka.contracts.analysis import Match, Scope
    from nitka.contracts.extraction import Issue
    from nitka.extraction.loading import load_extraction
    from nitka.pipeline import analyze_extraction

    class _FakeStreamlit:
        """Копит предупреждения и ошибки вместо настоящего Streamlit."""

        def __init__(self, click_first_button: bool = False) -> None:
            self.click_first_button = click_first_button
            self.clicks = 0
            self.warnings: list[str] = []
            self.errors: list[str] = []
            self.successes: list[str] = []
            self.buttons: list[str] = []
            self.session_state: dict[str, object] = {}

        def __getattr__(self, name: str) -> object:
            def noop(*args: object, **kwargs: object) -> None:
                return None

            return noop

        def columns(self, spec: int | list[int]) -> list[_FakeStreamlit]:
            return [self] * (spec if isinstance(spec, int) else len(spec))

        def selectbox(
            self, label: str, options: Sequence[str], **kwargs: object
        ) -> str:
            return options[0]

        def text_input(self, label: str, **kwargs: object) -> str:
            return ""

        def dataframe(self, data: object, **kwargs: object) -> dict[str, object]:
            return {"selection": {"rows": []}}

        def button(self, label: str, **kwargs: object) -> bool:
            self.buttons.append(label)
            self.clicks += 1
            return self.click_first_button and self.clicks == 1

        def warning(self, text: str) -> None:
            self.warnings.append(text)

        def error(self, text: str) -> None:
            self.errors.append(text)

        def success(self, text: str) -> None:
            self.successes.append(text)

    def _make_result(*, complete: bool) -> object:
        fixture = Path(__file__).resolve().parents[2] / "examples" / "extraction.json"
        extraction = load_extraction(fixture)
        if complete:
            extraction.sources[1].value = "150000.00"
        else:
            extraction.files[1].status = "PARTIAL"
            extraction.issues.append(
                Issue(
                    code="PAGE_UNREADABLE",
                    file_id="F-ACT",
                    detail="Страница 2 не распознана",
                )
            )
        return analyze_extraction(
            extraction,
            [
                Match(
                    match_id="M-1",
                    report_row_id="R-1",
                    support_row_ids=["A-1"],
                    state="ACCEPTED",
                )
            ],
            Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-10"),
            run_id="RUN-1",
            revision=2,
        )

    partial = _make_result(complete=False)
    view = build_results_view(partial)
    assert view.incomplete
    assert view.warning and "Проверка неполная" in view.warning
    assert view.discrepancies_count == 1
    assert view.processing_count == 1

    real_streamlit = sys.modules.get("streamlit")
    try:
        fake = _FakeStreamlit()
        sys.modules["streamlit"] = fake  # type: ignore[assignment]
        assert render_results(partial) is None
        # Расхождение и предупреждение о неполноте видны одновременно.
        assert any("Расхождений: 1" in text for text in fake.errors)
        assert any("Проверка неполная" in text for text in fake.warnings)

        click_fake = _FakeStreamlit(click_first_button=True)
        sys.modules["streamlit"] = click_fake  # type: ignore[assignment]
        clicked = render_results(partial)
        issue_id = view.rows[0].issue_id
        assert clicked == {"action": "open_issue", "issue_id": issue_id}

        complete_fake = _FakeStreamlit()
        sys.modules["streamlit"] = complete_fake  # type: ignore[assignment]
        complete_view = build_results_view(_make_result(complete=True))
        assert not complete_view.incomplete
        assert complete_view.warning is None
        render_results(_make_result(complete=True))
        assert not complete_fake.warnings
        assert any(
            "Расхождения не выявлены в проверенном объеме" in text
            for text in complete_fake.successes
        )
    finally:
        if real_streamlit is None:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = real_streamlit

    # Фильтры не теряют строки и ищут по сумме/причине.
    assert len(filter_rows(view.rows, "Все", "")) == len(view.rows)
    assert [row.state for row in filter_rows(view.rows, "Расхождения", "")] == ["FAIL"]
    assert any(row.issue_id for row in filter_rows(view.rows, "Все", "30000.00"))

    print("results_screen self-check: OK")
