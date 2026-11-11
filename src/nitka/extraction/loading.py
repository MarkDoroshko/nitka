from pathlib import Path

from nitka.contracts.extraction import ExtractionResult


def load_extraction(path: Path | str) -> ExtractionResult:
    """Load an externally produced extraction and check its links."""
    return ExtractionResult.model_validate_json(Path(path).read_bytes())
