# Bundled OCR models

These are unmodified, pretrained PaddlePaddle models, not models trained by
Hackathon Gurus. Nitka uses them for local CPU inference.

Both upstream model cards declare Apache-2.0. The license is included in
`LICENSE-PaddleOCR.txt`. Copyright and attribution belong to the PaddlePaddle
authors. No organizer documents or training datasets are included.

Sources inspected when packaging:

- Detector: https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det_onnx/tree/e6f4fa85f00e168c862bc462aebca69eef9b3d3d
- Cyrillic recognizer: https://huggingface.co/PaddlePaddle/cyrillic_PP-OCRv5_mobile_rec_onnx/tree/2cef88145434beb8afa9dd82d77d799eb1ad7b29
- License: https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE

The exact four inference files are identified by SHA-256 in `ppocr-v5.json`.
The same manifest is used by Nitka at inference time.
