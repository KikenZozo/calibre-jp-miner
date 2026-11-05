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
4. **Dual Mode UI (Zero Floating Popups)**
   * **Interactive Reader Mode:** Pre-segmented words, fixed bottom inspector dock on click, context sentence with `<u>target</u>` underlined.
   * **Batch `i+1` Mode:** Diffs all sentences in a book against your known Anki vocabulary and ranks `i+1` sentences by BCCWJ/JPDB frequency rank and book occurrence count.

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
