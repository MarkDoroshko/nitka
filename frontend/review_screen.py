"""Экран разбора одного замечания сверки."""

if __package__:
    from .nitka_ui.review_selfcheck import _run_self_tests
    from .nitka_ui.review_widgets import render_review as render_review
else:
    from nitka_ui.review_selfcheck import _run_self_tests
    from nitka_ui.review_widgets import render_review as render_review


if __name__ == "__main__":
    _run_self_tests()
