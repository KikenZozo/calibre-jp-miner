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
   * **Interactive Reader Mode:** Pre-segmented words, fixed bottom inspector dock on click, context sentence with `<u>target</u>` underlined. Illustrations are kept and served straight from the EPUB. Section navigation lives in a pinned toolbar (plus `←`/`→` keys, swipe, a **📑 Contents** panel built from the book's NCX/nav, and the book's own contents page, whose links are clickable).
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

6. **Known-Vocabulary Colouring**
   * Every word is tagged server-side as **known**, **mined**, **marked unknown** or **new**, so the browser never has to hold your whole vocabulary.
   * Colouring is by exception: new words keep the normal reading colour with a light dotted underline, words you have acquired recede, and anything you flagged stands out — otherwise a fresh book would be highlighted end to end and nothing would be easier to spot.
   * Click any word and press **✓ I know this** to recolour every occurrence instantly; press it again to mark it as *not* known.
   * An explicit "not known" outranks Anki and the mining queue, so a later `Sync Known Words` never silently un-marks a word you decided you do not know.

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

### Google Vision (optional, better Japanese)

Tesseract is weak on vertical Japanese with furigana — it interleaves the furigana with
the main text and the result segments into words that do not exist. That is a model
limitation, not a setting. Set `GOOGLE_VISION_API_KEY` to read scanned pages with Google
Cloud Vision instead: its Japanese accuracy is far higher, and it returns **one box per
symbol from the same model that read the text**, so the clickable word boxes cannot drift
away from the glyphs.

Google bills per page past the first 1000 each month. Staying inside that free tier is
the point, so the cap is enforced rather than trusted: once
`GOOGLE_VISION_MONTHLY_PAGE_LIMIT` pages have been sent in a calendar month, pages fall
back to Tesseract instead of costing money, and **Settings** shows how much of the budget
is left. A failed or unreachable Vision call also falls back rather than losing the page,
and is not counted against the budget.

| Variable                            | Default | Purpose                                              |
|-------------------------------------|---------|------------------------------------------------------|
| `GOOGLE_VISION_API_KEY`             | *unset* | Enables Vision. Set it in the deployment environment, never in the compose file |
| `OCR_ENGINE`                        | `auto`  | `auto` prefers Vision while budget lasts; `tesseract` or `google_vision` force one |
| `GOOGLE_VISION_MONTHLY_PAGE_LIMIT`  | `1000`  | Monthly page cap; `0` disables the cap                |
| `GOOGLE_VISION_DPI`                 | `200`   | Render resolution for Vision (it needs less than Tesseract) |

Pages are sent to Google as JPEG when this is enabled — scanned content leaves your
server. Leave `GOOGLE_VISION_API_KEY` unset to keep everything local.

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
visible behind a large Calibre library), PDF text-layer word geometry, the OCR fallback,
illustration extraction, contents-page links, the NCX table of contents, stale-cache
re-extraction, the precedence rules for known / not-known words, OCR box geometry
(horizontal and vertical), and the Google Vision response parser. The Vision tests use a
recorded payload and never call Google, so the suite runs offline and costs nothing.

### Re-extraction after an upgrade

`EXTRACTION_VERSION` in `app/epub_parser.py` is bumped whenever the extractor starts
producing richer sections. Books cached by an older build are then treated as *not*
extracted and are rebuilt once, on first open, so an existing `mining.db` picks up
illustrations and the table of contents without being deleted. `mining.db` is migrated
in place at startup (`_migrate_schema`), so no manual step is needed.

`app/static/vendor/pdfjs/` holds a pinned copy of pdf.js plus the CJK CMaps — see the
README there for how to upgrade it.
