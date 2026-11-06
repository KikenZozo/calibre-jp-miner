FROM python:3.11-slim

WORKDIR /app

# System dependencies for lxml, SudachiPy, the healthcheck, and Japanese OCR.
# tesseract-ocr-jpn / -jpn-vert give scanned PDFs a text layer on demand
# (horizontal and tategaki); drop them if you never read image-only scans.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    curl \
    tesseract-ocr \
    tesseract-ocr-jpn \
    tesseract-ocr-jpn-vert \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8085
ENV PUID=1000
ENV PGID=1000
ENV DATA_DIR=/data
ENV CALIBRE_LIBRARY_PATH=/calibre-library
ENV ANKI_COLLECTION_PATH=/anki_data/enzo/collection.anki2
ENV ANKI_DECK_NAME="Data Mining"
ENV AUTO_MINE_ON_CLICK="true"
ENV AUTO_DOWNLOAD_JMDICT="true"
ENV OCR_DPI=300

EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8085/api/health || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8085}"]
