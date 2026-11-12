from pathlib import Path

from nitka.contracts.analysis import Match, Scope
from nitka.extraction.loading import load_extraction
from nitka.pipeline import analyze_extraction


def main() -> None:
    fixture = Path(__file__).with_name("extraction.json")
    extraction = load_extraction(fixture)
    matches = [
        Match(
            match_id="M-1",
            report_row_id="R-1",
            support_row_ids=["A-1"],
            state="ACCEPTED",
        )
    ]
    scope = Scope(currency="RUB", amount_basis="GROSS", reporting_period="2026-10")
    result = analyze_extraction(extraction, matches, scope)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
