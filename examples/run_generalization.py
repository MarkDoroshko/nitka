"""Measure frozen synthetic layouts against the local production pipeline."""

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter
from typing import Self

from pydantic import Field, model_validator

from examples.generalization_corpus import (
    CASES,
    OCR_FILES,
    PROFILE,
    REPORTS,
    SUPPORT,
    build_corpus,
    documents,
    support_names,
)
from nitka import __file__ as nitka_file
from nitka.amounts import parse_amount
from nitka.analysis.aggregation import article_code_for_row
from nitka.analysis.matching import CaseMatcher
from nitka.contracts.analysis import AnalysisResult, Scope
from nitka.contracts.extraction import Amount, Contract, ExtractionResult, OcrProvenance
from nitka.extraction.package import PackageExtractor
from nitka.ocr import OcrEngine
from nitka.storage import load_run, replay_run, run_and_save

_ROOT = Path(__file__).resolve().parents[1]
_EXPECTED = Path(__file__).with_name("generalization_expected.json")
_MANIFEST = _ROOT / "config" / "ocr" / "ppocr-v5.json"
_ARTICLES = set(PROFILE.article_codes)
_REQUIRED_CHECKS = (
    *(f"ARTICLE_AMOUNT:{code}" for code in PROFILE.article_codes), "SUPPORT_TOTAL:OVERALL",
)


class Expectations(Contract):
    report_amounts: dict[str, dict[str, Amount]]
    support_amounts: dict[str, dict[str, Amount]]
    links: dict[str, str | None] = Field(min_length=1)

    @model_validator(mode="after")
    def _complete(self) -> Self:
        if self.report_amounts.keys() != REPORTS.keys():
            raise ValueError("Label both report layouts")
        if self.support_amounts.keys() != SUPPORT.keys() or self.links.keys() != SUPPORT.keys():
            raise ValueError("Label amounts and inclusion for every supporting document")
        required = _ARTICLES | {"total"}
        if any(not required <= fields.keys() for fields in self.report_amounts.values()):
            raise ValueError("Label all report articles and the total in every layout")
        if any(not fields for fields in self.support_amounts.values()):
            raise ValueError("Label at least one amount in every supporting document")
        for case in CASES:
            links = {self.links[name] for name in support_names(case)}
            if links != _ARTICLES | {None} or self.links["foreign.pdf"] is not None:
                raise ValueError("Label all intended articles and exclude the foreign contract")
        return self


@dataclass(frozen=True)
class Counts:
    exact_amounts: int
    expected_amounts: int
    wrong_links: int
    accepted_links: int
    missing_links: int
    expected_links: int
    unverified_rows: int
    expected_rows: int


@dataclass(frozen=True)
class AmountObservation:
    file: str
    field: str
    expected: str
    observed: str | None
    state: str
    source_id: str | None


def _score(
    case: str, expected: Expectations, extraction: ExtractionResult, analysis: AnalysisResult
) -> tuple[Counts, dict[str, object]]:
    layout = CASES[case]
    report = REPORTS[layout]
    support = support_names(case)
    files = {file.file_id: file.name for file in extraction.files}
    sources = {source.source_id: source for source in extraction.sources}
    rows = {row.row_id: row for row in extraction.rows}
    # PackageExtractor projects every semantic field under this source key.
    # Keep field identities separate from OCR spelling, cell position and bytes.
    by_field = {
        (files[source.file_id], source.source_id.removeprefix(f"S-{source.file_id}-")): source
        for source in extraction.sources
    }
    amounts = {
        report: expected.report_amounts[layout],
        **{name: expected.support_amounts[name] for name in support},
    }
    observations = []
    for name, fields in amounts.items():
        for field, target in fields.items():
            source = by_field.get((name, field))
            value = source.value if source else None
            parsed = parse_amount(value)
            if source is None:
                state = "missing"
            elif source.review_status == "NEEDS_REVIEW":
                state = "review"
            elif value is None:
                state = "missing"
            elif parsed.value is None:
                state = "invalid"
            else:
                state = "exact" if Decimal(parsed.value) == Decimal(target) else "wrong"
            observations.append(AmountObservation(
                name, field, target, value, state, source.source_id if source else None
            ))

    accepted: Counter[tuple[str, str]] = Counter()
    for match in analysis.matches:
        if match.state != "ACCEPTED":
            continue
        article = article_code_for_row(rows[match.report_row_id], sources) or "UNKNOWN"
        for support_id in match.support_row_ids:
            file_id = sources[rows[support_id].amount_source_id].file_id
            accepted[article, files[file_id]] += 1
    ideal = Counter(
        (expected_article, name) for name in support
        if (expected_article := expected.links[name]) is not None
    )

    checks = {check.check_id: check for check in analysis.checks}
    printed_total = by_field.get((report, "total"))
    rows_observed = {}
    for check_id in _REQUIRED_CHECKS:
        check = checks.get(check_id)
        # An overall comparison may substitute a sum of extracted articles.
        # Credit the printed total only when it was actually used as evidence.
        evidence_missing = check_id == "SUPPORT_TOTAL:OVERALL" and (
            check is None or printed_total is None or not any(
                operand.name == "reported" and printed_total.source_id in operand.source_ids
                for operand in check.operands
            )
        )
        rows_observed[check_id] = {
            "check": check.model_dump(mode="json") if check is not None else None,
            "verified": check is not None and check.state != "NOT_RUN" and not evidence_missing,
        }
    counts = Counts(
        exact_amounts=sum(item.state == "exact" for item in observations),
        expected_amounts=len(observations),
        wrong_links=(accepted - ideal).total(),
        accepted_links=accepted.total(),
        missing_links=(ideal - accepted).total(),
        expected_links=ideal.total(),
        unverified_rows=sum(not row["verified"] for row in rows_observed.values()),
        expected_rows=len(_REQUIRED_CHECKS),
    )
    return counts, {
        "amounts": [asdict(item) for item in observations],
        "accepted_links": list(accepted.elements()),
        "wrong_links": list((accepted - ideal).elements()),
        "missing_links": list((ideal - accepted).elements()),
        "rows": rows_observed,
        "detail_not_run": sum(
            check.reason_code == "BREAKDOWN_SUPPORT_MISSING" for check in analysis.checks
        ),
        "issues": [{"file": files.get(issue.file_id or ""), **issue.model_dump()}
                   for issue in analysis.issues],
        "exclusions": [
            {"file": files[item.file_id], "reason": item.reason}
            for item in analysis.excluded_documents
        ],
    }


def _runtime_versions() -> dict[str, str]:
    result = {}
    for name in ("paddleocr", "paddlex", "onnxruntime", "pypdfium2", "reportlab", "Pillow", "openpyxl"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = "unavailable"
    return result


def _pipeline_sha256() -> str:
    # Identify the installed code actually exercised, including when mounted
    # examples are run against a separately built Docker image.
    assert nitka_file is not None
    package = Path(nitka_file).parent
    files = {
        path.relative_to(package).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in package.rglob("*.py")
    }
    return sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()


def evaluate(output: Path, font: Path, models: Path) -> bool:
    expected_data = _EXPECTED.read_bytes()
    expected = Expectations.model_validate_json(expected_data)
    provenance = OcrProvenance.model_validate_json(_MANIFEST.read_bytes())
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / "inputs"
    build_corpus(inputs, font)
    if {path.name for path in inputs.iterdir()} != {*REPORTS.values(), *SUPPORT}:
        raise ValueError("Generated files differ from the declared corpus")
    engine = OcrEngine(models, _MANIFEST)
    scope = Scope(currency="RUB", amount_basis="GROSS", reporting_period="2031-03")
    total: Counter[str] = Counter()
    reports = {}
    for case, layout in CASES.items():
        start = perf_counter()
        runs = output / case
        package = documents(inputs, case)
        analysis = run_and_save(
            runs, package, scope,
            extractor=PackageExtractor(PROFILE, ocr=engine, preview_dir=output / "previews" / case),
            matcher=CaseMatcher(PROFILE),
        )
        stored = load_run(runs, analysis.run_id)
        if stored.analysis != analysis or replay_run(runs, analysis.run_id) != analysis:
            raise ValueError(f"Saved analysis or replay differs for {case}")
        file_ids = {file.name: file.file_id for file in analysis.files}
        if len(file_ids) != len(analysis.files) or file_ids.keys() != {
            item.path.name for item in package
        }:
            raise ValueError(f"Run files differ from the declared corpus for {case}")
        missing_ocr = [
            item.path.name for item in package
            if item.path.name in OCR_FILES
            and stored.extraction.ocr_provenance.get(file_ids[item.path.name]) != {1: provenance}
        ]
        metrics, result = _score(case, expected, stored.extraction, analysis)
        counts = asdict(metrics)
        total.update(counts)
        reports[case] = {
            **result,
            "layout": layout,
            "counts": counts,
            "complete": not missing_ocr,
            "missing_ocr": missing_ocr,
            "run_id": analysis.run_id,
            "decision": analysis.decision,
            "seconds": round(perf_counter() - start, 2),
            "extraction_version": analysis.extraction_version,
            "rules_version": analysis.rules_version,
        }

    complete = all(case["complete"] for case in reports.values())
    report = {
        "complete": complete,
        "origin": "synthetic-layouts-v1",
        "profile": PROFILE.model_dump(mode="json"),
        "ocr": provenance.model_dump(mode="json"),
        "versions": _runtime_versions(),
        "python": sys.version.split()[0],
        "sha256": {
            "expectations": sha256(expected_data).hexdigest(),
            "generator": sha256(
                Path(__file__).with_name("generalization_corpus.py").read_bytes()
            ).hexdigest(),
            "evaluator": sha256(Path(__file__).read_bytes()).hexdigest(),
            "pipeline": _pipeline_sha256(),
            "font": sha256(font.read_bytes()).hexdigest(),
            "inputs": {file.name: sha256(file.read_bytes()).hexdigest()
                       for file in sorted(inputs.iterdir())},
        },
        "support_families": SUPPORT,
        "counts": dict(total),
        "cases": reports,
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({"complete": complete, **total}, ensure_ascii=False))
    return complete


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory for the evaluation")
    parser.add_argument("--font", type=Path, required=True, help="Cyrillic TrueType font")
    parser.add_argument("--models", type=Path, required=True, help="Local PP-OCRv5 model directory")
    args = parser.parse_args()
    return 0 if evaluate(args.output, args.font, args.models) else 2


if __name__ == "__main__":
    sys.exit(main())
