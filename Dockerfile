FROM python:3.11-slim

WORKDIR /app

# Dépendances système pour lxml, SudachiPy et healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Variables d'environnement par défaut (personnalisables dans Dokploy -> Environment)
ENV PORT=8085
ENV DATA_DIR=/data
ENV CALIBRE_LIBRARY_PATH=/calibre_library
ENV ANKI_DECK_NAME="Data Mining"
ENV AUTO_MINE_ON_CLICK="true"
ENV AUTO_DOWNLOAD_JMDICT="true"

EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8085/api/health || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8085}"]
