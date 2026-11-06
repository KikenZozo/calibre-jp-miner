# YomiCalibre — Japanese Text-Mining & Reader Pipeline

A self-hosted Japanese book extraction, `i+1` sentence mining, and interactive pre-segmented reading service designed for **Calibre-Web Automated**, **rclone Google Drive mounts**, and **Anki**.

---

## Architecture Highlights

1. **Zero Filesystem Walk (`metadata.db` Only)**
   * Indexes books strictly from `/calibre-library/metadata.db` opened in `?mode=ro` with `SQLITE_BUSY` retry.
   * Resolves paths via `LIBRARY_ROOT + "/" + books.path + "/" + data.name + "." + lower(data.format)`.
   * Checks `os.path.islink()` (`lstat` only, 0 bytes downloaded from Google Drive) and `data.uncompressed_size`.
   * Prefers `EPUB` over `PDF` when multiple formats exist for the same book.
2. **Extract Once, Cache Permanently by `books.uuid`**
   * Sequential extraction (`threading.Lock`) pulls a book only once when opened, extracts text (EPUB with `<ruby><rt>` stripping or PDF with `PyMuPDF` text-layer detection), segments sentences and morphemes with **SudachiPy**, and caches the result permanently in `/home/enzo/mining/mining.db`.
3. **Read-Only Anki Snapshot + AnkiConnect Bridge + Deterministic `.apkg`**
   * Copies `/anki_data/enzo/collection.anki2` and its `-wal` file to a temporary snapshot directory before reading known vocabulary and detecting note types (`notetypes`/`fields` or `col.models`) at runtime.
   * Never writes to `metadata.db`, `collection.anki2`, `/calibre-library`, or `/home/enzo/calibre/gdrive`.
4. **Non-Blocking Extraction With Live Progress**
   * Opening a book starts extraction in a worker thread and returns `202` immediately; the reader polls `/api/books/{id}/extract-status` and shows a progress bar.
   * A slow Drive-backed book can no longer freeze the reader or outlive the reverse proxy's timeout.
5. **Triple Mode UI (Zero Floating Popups)**
   * **Classic PDF Mode:** the original page rendered untouched by a vendored pdf.js, with an invisible pre-segmented word layer on top — Yomitan-style word scanning, except SudachiPy does the segmenting server-side so a tap selects a whole word *and* its whole sentence. Image-only scans get the same layer from Tesseract OCR, on demand, per page, cached permanently.
   * **Interactive Reader Mode:** Pre-segmented words, fixed bottom inspector dock on click, context sentence with `<u>target</u>` underlined. Section navigation lives in a pinned toolbar (plus `←`/`→` keys and swipe) rather than only at the bottom of the chapter.
   * **Batch `i+1` Mode:** Diffs all sentences in a book against your known Anki vocabulary and ranks `i+1` sentences by BCCWJ/JPDB frequency rank and book occurrence count. For a scanned PDF, **📚 Index all** OCRs every page first so the whole book is diffed, not just the pages you opened.

---

## Dokploy Deployment (`docker-compose.yml`)

Create `/home/enzo/mining` on the host (`mkdir -p /home/enzo/mining && chown 1000:1000 /home/enzo/mining`), then deploy in Dokploy with the following environment variables in Dokploy's **Environment** tab:

```env
MINING_DOMAIN=mining.enzoprojects.net
MINING_AUTH_USER=enzo
MINING_AUTH_PASSWORD=your_strong_password_here
ANKI_DECK_NAME=Data Mining
AUTO_MINE_ON_CLICK=true
AUTO_DOWNLOAD_JMDICT=true
```

---

## Scanned PDFs (OCR)

`tesseract-ocr`, `tesseract-ocr-jpn` and `tesseract-ocr-jpn-vert` are installed by the
Dockerfile. A PDF page with no text layer is rendered at `OCR_DPI` (default 300) and
read with Tesseract; the horizontal and vertical (tategaki) models are both tried and
the more confident reading wins. Character boxes come back as hOCR, so OCR'd pages get
exactly the same clickable word layer and mining pipeline as a digital PDF, and the
result is cached in `mining.db` per page.

If Tesseract is missing the app degrades instead of failing: the PDF still renders, the
word layer is skipped, and **Settings → Scanned PDF OCR** says which package to install.

| Variable              | Default    | Purpose                                      |
|-----------------------|------------|----------------------------------------------|
| `OCR_DPI`             | `300`      | Page rendering resolution for OCR            |
| `OCR_LANG_HORIZONTAL` | `jpn`      | Tesseract model for horizontal text          |
| `OCR_LANG_VERTICAL`   | `jpn_vert` | Tesseract model for tategaki                 |
| `OCR_TIMEOUT_SECONDS` | `120`      | Per-page Tesseract timeout                   |
| `TESSERACT_BIN`       | `tesseract`| Path to the Tesseract binary                 |

---

## Development

```bash
pip install -r requirements.txt
DATA_DIR=./data uvicorn app.main:app --reload --port 8085
```

```bash
DATA_DIR=./data python tests/test_pipeline.py
```

The test suite covers sentence splitting, `<ruby>` stripping, JMdict lookup, `i+1`
ranking, deterministic Anki GUIDs, the library-ordering regression (an upload must stay
visible behind a large Calibre library), PDF text-layer word geometry, and the OCR
fallback.

`app/static/vendor/pdfjs/` holds a pinned copy of pdf.js plus the CJK CMaps — see the
README there for how to upgrade it.
