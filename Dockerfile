# Python 3.12.16 / Debian Bookworm, linux/amd64
FROM python:3.12.16-slim-bookworm@sha256:0f086103b026048cc50cb5da25ec435c023c5241a83fc44daea593377abc61c4

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    NITKA_OCR_MODELS=/models \
    NITKA_RUNS_DIR=/data/runs

# DejaVu provides Cyrillic glyphs for PDF. OpenCV uses GL and GLib even without a display.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 nitka \
    && useradd --uid 10001 --gid nitka --create-home nitka \
    && install -d -o nitka -g nitka /data/runs \
    && mkdir /models

WORKDIR /app
COPY requirements-docker.txt ./
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements-docker.txt

COPY pyproject.toml README.md ./
COPY src/nitka/ ./src/nitka/
RUN python -m pip install --no-cache-dir --no-build-isolation --no-deps . \
    && python -m pip check

COPY config/case5.json ./config/case5.json
COPY config/ocr/ppocr-v5.json ./config/ocr/ppocr-v5.json
COPY frontend/app.py ./frontend/app.py
COPY frontend/nitka_ui/ ./frontend/nitka_ui/
COPY frontend/assets/ ./frontend/assets/
COPY frontend/demo_data/ ./frontend/demo_data/

ENV STREAMLIT_BROWSER_SERVER_ADDRESS=127.0.0.1
USER nitka
EXPOSE 8501
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3).close()"]
CMD ["python", "-m", "streamlit", "run", "frontend/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
