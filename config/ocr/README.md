# Local OCR

Install the CPU runtime in the same Python 3.12 environment as Nitka. The tested
engine versions are PaddleOCR 3.7.0, PaddleX 3.7.2 and ONNX Runtime 1.31.1.

```shell
python -m pip install "paddleocr==3.7.0" "paddlex[ocr-core]==3.7.2" "onnxruntime==1.31.1"
```

Provision the two Apache-2.0 PaddlePaddle ONNX models from the sources below.
Copy `inference.onnx` and `inference.yml` from each source into the corresponding
local subdirectory. Existing laboratory weights with the same hashes can be used
directly. The worker checks all four files against [the manifest](ppocr-v5.json)
and reports missing or mismatched files before loading the runtime.

| Local subdirectory | Source |
| --- | --- |
| `PP-OCRv5_mobile_det` | [PaddlePaddle detector](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det_onnx/tree/main) |
| `cyrillic_PP-OCRv5_mobile_rec` | [PaddlePaddle Cyrillic recognizer](https://huggingface.co/PaddlePaddle/cyrillic_PP-OCRv5_mobile_rec_onnx/tree/main) |

```text
ppocr-v5/
├── PP-OCRv5_mobile_det/
│   ├── inference.onnx
│   └── inference.yml
└── cyrillic_PP-OCRv5_mobile_rec/
    ├── inference.onnx
    └── inference.yml
```

The [Docker image](../../README.md#запуск-в-docker) already includes the CPU
runtime. Mount the `ppocr-v5` directory at `/models` read-only.

```python
from pathlib import Path

from nitka.ocr import OcrEngine

engine = OcrEngine(Path("models/ppocr-v5"), Path("config/ocr/ppocr-v5.json"))
page = engine.recognize(Path("scan.png"))
```

Call the engine once per PNG, JPEG or WebP page. Render scanned PDFs to raster
pages before calling it. `page.blocks` preserves model order, original text and
recognition confidence. Each bounding box is `(left, top, right, bottom)` in input
image pixels. `page.text` joins the original strings using `page.reading_order`.
The whitespace-cut algorithm only runs when a page has at least 100 alphanumeric
tokens. `page.model_dump_json()` includes the raw blocks, reading order, image
dimensions, settings version and verified SHA-256 digests.

Inference uses the local ONNX files, CPU only, with two ONNX intra-op threads and
one inter-op thread. No model lookup is performed. A worker handles one page and
is killed when the 60-second timeout expires. Default input limits are 32 MiB
encoded and 24 million pixels. Pass `OcrLimits` to adjust these three limits.

Run the unit tests with `python -m pytest tests/ocr/test_engine.py`. To run the
real-model smoke, set `NITKA_OCR_MODELS` to the directory containing both model
subdirectories and `NITKA_OCR_SMOKE_IMAGES` to one or more local Cyrillic scans
separated by the platform path separator, then run
`python -m pytest tests/ocr/test_smoke.py`.
