# Storage compatibility fixture

`legacy-run` was written by `nitka.storage.run_and_save` at commit `558e69c`,
before the repository/workflow refactor. It uses the synthetic XLSX and support
bytes from `tests/test_storage.py`, not user documents. The support PDF is an
opaque test input, not a renderable document.

The expected result is a 150000.00 report amount against 120000.00 support,
with a +30000.00 discrepancy. Keep these bytes fixed: regenerating this fixture
with the current writer would defeat its backward-compatibility purpose.
