import base64
import os
import secrets
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import extraction_jobs
from app import ocr as ocr_module
from app.anki_service import (
    build_anki_fields_for_card,
    create_and_push_anki_card,
    export_cards_to_apkg,
    export_cards_to_tsv,
    get_ankiconnect_model_spec,
    try_push_card_to_ankiconnect,
)
from app.anki_snapshot import inspect_and_sync_anki_snapshot
from app.calibre_library import (
    get_book_descriptor,
    get_last_library_error,
    list_all_books,
    local_book_id_for,
)
from app.config import (
    MINING_AUTH_PASSWORD,
    MINING_AUTH_USER,
    UPLOADS_DIR,
)
from app.database import (
    clear_pdf_page_layouts,
    count_cached_pdf_pages,
    delete_mined_card,
    get_all_cached_sentences_for_book,
    get_cached_book_section,
    get_known_words_count,
    get_known_words_set,
    get_settings,
    init_app_db,
    is_book_extracted,
    list_mined_cards,
    mark_card_synced,
    save_reading_progress,
    update_settings,
    upsert_known_words,
)
from app.dictionary import get_dictionary_stats, init_jmdict_db, lookup_word
from app.japanese_nlp import (
    build_underlined_sentence_html,
    find_i_plus_one_candidates,
    get_word_frequency_rank,
)
from app.pdf_layout import build_page_layout, get_pdf_info, pymupdf_available

app = FastAPI(
    title="YomiCalibre — Japanese Text-Mining & Reader Pipeline",
    version="2.0.0",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    """
    Enforces HTTP Basic Authentication when MINING_AUTH_PASSWORD is set in the environment
    (except for /api/health used by Docker/Dokploy healthchecks).
    """
    if request.url.path == "/api/health" or not MINING_AUTH_PASSWORD:
        return await call_next(request)

    expected_user = MINING_AUTH_USER or "admin"
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            username, _, password = decoded.partition(":")
            if secrets.compare_digest(username, expected_user) and secrets.compare_digest(
                password, MINING_AUTH_PASSWORD
            ):
                return await call_next(request)
        except Exception:
            pass

    return Response(
        content="Authentication required",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="YomiCalibre Mining"'},
    )


@app.on_event("startup")
def on_startup() -> None:
    init_app_db()
    init_jmdict_db()

    books = list_all_books()
    if not books:
        try:
            from scripts.create_sample_epub import create_sample_japanese_epub

            create_sample_japanese_epub()
        except Exception:
            pass

    # Auto-sync known words from read-only Anki snapshot if present on the server
    try:
        inspect_and_sync_anki_snapshot()
    except Exception:
        pass

    if os.getenv("AUTO_DOWNLOAD_JMDICT", "false").lower() == "true":
        stats = get_dictionary_stats()
        if not stats.get("is_full_jmdict"):
            def _bg_build() -> None:
                try:
                    from scripts.build_jmdict import download_and_build_jmdict

                    download_and_build_jmdict()
                except Exception as exc:
                    print(f"[JMdict Auto-Init] Info: {exc}")

            threading.Thread(target=_bg_build, daemon=True).start()


@app.get("/api/health")
def api_health() -> Dict[str, Any]:
    return {"status": "healthy"}


# --- Request Models ---


class WordClickRequest(BaseModel):
    book_id: str = ""
    book_title: str = ""
    chapter_title: str = ""
    wid: str
    sid: str
    surface: str
    base: str
    normalized: str = ""
    reading_hiragana: str = ""
    reading_katakana: str = ""
    surface_hiragana: str = ""
    surface_katakana: str = ""
    pos_detail: str = ""
    sentence_tokens: List[Dict[str, Any]]
    force_mine: Optional[bool] = None


class ProgressUpdateRequest(BaseModel):
    book_title: str = ""
    chapter_index: int
    scroll_ratio: float = 0.0


class SettingsUpdateRequest(BaseModel):
    anki_deck_name: Optional[str] = None
    anki_model_name: Optional[str] = None
    ankiconnect_url: Optional[str] = None
    auto_mine_on_click: Optional[bool] = None
    anki_field_map: Optional[Dict[str, str]] = None


class MarkSyncedRequest(BaseModel):
    anki_note_id: Optional[int] = None


class KnownWordsUploadRequest(BaseModel):
    words: List[str]
    source: str = "ankiconnect"


# --- Calibre Library & Extraction Endpoints ---


@app.get("/api/books")
def api_list_books(q: str = "", limit: int = 200) -> Dict[str, Any]:
    books = list_all_books(search_query=q, limit=limit)
    return {
        "books": books,
        "total": len(books),
        "known_words_count": get_known_words_count(),
        "library_error": get_last_library_error() or "",
    }


@app.post("/api/books/upload")
async def api_upload_book(file: UploadFile = File(...)) -> Dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith((".epub", ".pdf")):
        raise HTTPException(
            status_code=400, detail="Please upload a valid .epub or .pdf file."
        )

    safe_name = Path(file.filename).name
    dest = UPLOADS_DIR / safe_name
    try:
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save upload: {exc}")

    size_bytes = dest.stat().st_size
    if size_bytes == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded file was empty.")

    book_id = local_book_id_for(safe_name)
    books = list_all_books()
    book = next((b for b in books if b["book_id"] == book_id), None)

    return {
        "status": "ok",
        "filename": safe_name,
        "book_id": book_id,
        "size_bytes": size_bytes,
        "book": book,
        "books": books,
    }


def _require_book(book_id: str) -> Dict[str, Any]:
    book = get_book_descriptor(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found in library.")
    return book


def _job_payload(book: Dict[str, Any]) -> Dict[str, Any]:
    job = extraction_jobs.get_job(book["book_uuid"]) or {}
    return {
        "status": "extracting",
        "book_id": book["book_uuid"],
        "book_title": book.get("title", ""),
        "job": job,
    }


@app.get("/api/books/{book_id}/extract-status")
def api_extraction_status(book_id: str) -> Dict[str, Any]:
    """Progress of the background extraction, polled by the reader while it waits."""
    book = _require_book(book_id)
    job = extraction_jobs.get_job(book["book_uuid"])
    extracted = is_book_extracted(book["book_uuid"])
    return {
        "book_id": book["book_uuid"],
        "is_extracted": extracted,
        "status": "ready" if extracted else (job or {}).get("status", "idle"),
        "job": job or {},
    }


@app.get("/api/books/{book_id}/chapter/{chapter_index}")
def api_get_chapter(book_id: str, chapter_index: int):
    """
    Returns a cached section immediately. When the book has not been extracted yet,
    extraction is started in the background and HTTP 202 is returned with progress,
    so a slow Drive-backed book never blocks (or times out) the request.
    """
    book = _require_book(book_id)
    book_uuid = book["book_uuid"]

    if is_book_extracted(book_uuid):
        section = get_cached_book_section(book_uuid, chapter_index)
        if section is not None:
            section["status"] = "ready"
            return section

    job = extraction_jobs.get_job(book_uuid)
    if job and job["status"] == extraction_jobs.STATUS_ERROR:
        extraction_jobs.clear_job(book_uuid)
        raise HTTPException(
            status_code=500, detail=f"Extraction failed: {job.get('error', 'unknown error')}"
        )

    if not (job and job["status"] == extraction_jobs.STATUS_RUNNING):
        extraction_jobs.start_extraction(book)

    return JSONResponse(status_code=202, content=_job_payload(book))


# --- Classic PDF View (original rendering + clickable word overlay) ---


@app.get("/api/books/{book_id}/file")
def api_get_book_file(book_id: str):
    """Streams the original file so pdf.js can render the PDF exactly as it is."""
    book = _require_book(book_id)
    path = Path(book["file_path"])
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="The book file is not reachable (Drive mount unavailable?).",
        )
    media_type = (
        "application/pdf"
        if book.get("preferred_format", "").upper() == "PDF"
        else "application/epub+zip"
    )
    return FileResponse(str(path), media_type=media_type, filename=path.name)


@app.get("/api/books/{book_id}/pdf/info")
def api_pdf_info(book_id: str) -> Dict[str, Any]:
    book = _require_book(book_id)
    if book.get("preferred_format", "").upper() != "PDF":
        raise HTTPException(status_code=400, detail="This book is not a PDF.")
    if not pymupdf_available():
        raise HTTPException(status_code=500, detail="PyMuPDF is not installed.")

    path = Path(book["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF file not reachable.")

    info = get_pdf_info(path)
    info.update(
        {
            "book_id": book["book_uuid"],
            "book_title": book.get("title", ""),
            "ocr": ocr_module.ocr_status(),
            "cached_pages": count_cached_pdf_pages(book["book_uuid"]),
        }
    )
    return info


@app.get("/api/books/{book_id}/pdf/page/{page_number}")
def api_pdf_page(book_id: str, page_number: int, force_ocr: bool = False) -> Dict[str, Any]:
    """
    Clickable word geometry for one PDF page: segmented by SudachiPy, positioned from
    the embedded text layer, or from Tesseract OCR when the page is an image scan.
    """
    book = _require_book(book_id)
    if book.get("preferred_format", "").upper() != "PDF":
        raise HTTPException(status_code=400, detail="This book is not a PDF.")

    path = Path(book["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF file not reachable.")

    try:
        return build_page_layout(
            pdf_path=path,
            page_number=max(0, page_number),
            book_uuid=book["book_uuid"],
            force_ocr=force_ocr,
            book_title=book.get("title", ""),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF page analysis failed: {exc}")


@app.post("/api/books/{book_id}/pdf/analyze-all")
def api_pdf_analyze_all(book_id: str, force_ocr: bool = False) -> Dict[str, Any]:
    """
    Runs the word layer over every page in the background (OCR included) so batch i+1
    mining can see a scanned book in full. Progress is polled via /extract-status.
    """
    book = _require_book(book_id)
    if book.get("preferred_format", "").upper() != "PDF":
        raise HTTPException(status_code=400, detail="This book is not a PDF.")
    if force_ocr and not ocr_module.is_available():
        raise HTTPException(
            status_code=400, detail=ocr_module.ocr_status()["reason"] or "OCR unavailable"
        )

    job = extraction_jobs.start_pdf_ocr(book, force=force_ocr)
    return {"status": "started", "book_id": book["book_uuid"], "job": job}


@app.delete("/api/books/{book_id}/pdf/cache")
def api_clear_pdf_cache(book_id: str) -> Dict[str, Any]:
    book = _require_book(book_id)
    return {"status": "ok", "deleted_pages": clear_pdf_page_layouts(book["book_uuid"])}


@app.get("/api/ocr/status")
def api_ocr_status() -> Dict[str, Any]:
    return ocr_module.ocr_status()


@app.get("/api/books/{book_id}/candidates")
def api_get_book_i_plus_one_candidates(book_id: str, limit: int = 80):
    """
    Batch i+1 sentence mining endpoint:
    1. Ensures the book is extracted and cached in mining.db (keyed on books.uuid).
    2. Diffs all book sentences against the user's known Anki vocabulary.
    3. Ranks i+1 candidate sentences by word frequency and book occurrences.
    """
    book = _require_book(book_id)

    if not is_book_extracted(book["book_uuid"]):
        job = extraction_jobs.get_job(book["book_uuid"])
        if job and job["status"] == extraction_jobs.STATUS_ERROR:
            extraction_jobs.clear_job(book["book_uuid"])
            raise HTTPException(
                status_code=500,
                detail=f"Extraction failed: {job.get('error', 'unknown error')}",
            )
        if not (job and job["status"] == extraction_jobs.STATUS_RUNNING):
            extraction_jobs.start_extraction(book)
        return JSONResponse(status_code=202, content=_job_payload(book))

    all_sentences = get_all_cached_sentences_for_book(book["book_uuid"])
    known_words = get_known_words_set()

    raw_candidates = find_i_plus_one_candidates(
        all_sentences, known_words, limit=limit
    )

    enriched_candidates: List[Dict[str, Any]] = []
    for cand in raw_candidates:
        d_info = lookup_word(
            surface=cand["surface"],
            base=cand["base"],
            reading_hiragana=cand["reading_hiragana"],
        )
        cand["reading_hiragana"] = d_info["reading_hiragana"] or cand["reading_hiragana"]
        cand["reading_katakana"] = d_info["reading_katakana"] or cand["reading_katakana"]
        cand["definitions_fr"] = d_info["definitions_fr"]
        cand["definitions_en"] = d_info["definitions_en"]
        enriched_candidates.append(cand)

    return {
        "book_id": book["book_uuid"],
        "book_title": book["title"],
        "total_book_sentences": len(all_sentences),
        "known_words_count": len(known_words),
        "candidates": enriched_candidates,
    }


@app.post("/api/books/{book_id}/progress")
def api_save_progress(book_id: str, req: ProgressUpdateRequest) -> Dict[str, Any]:
    save_reading_progress(
        book_uuid=book_id,
        book_title=req.book_title,
        chapter_index=req.chapter_index,
        scroll_ratio=req.scroll_ratio,
    )
    return {"status": "ok"}


# --- Word Lookup & Automatic Anki Mining Endpoint ---


@app.post("/api/word/click")
def api_word_click(req: WordClickRequest) -> Dict[str, Any]:
    dict_info = lookup_word(
        surface=req.surface,
        base=req.base,
        normalized=req.normalized,
        reading_hiragana=req.reading_hiragana,
    )

    reading_hira = dict_info["reading_hiragana"] or req.reading_hiragana
    reading_kata = dict_info["reading_katakana"] or req.reading_katakana

    sentence_html, sentence_plain = build_underlined_sentence_html(
        req.sentence_tokens, req.wid
    )

    settings = get_settings()
    should_mine = (
        req.force_mine
        if req.force_mine is not None
        else settings.get("auto_mine_on_click", True)
    )

    freq_rank = get_word_frequency_rank(req.base or req.surface)
    mined_card = None
    if should_mine:
        mined_card = create_and_push_anki_card(
            word_surface=req.surface,
            word_base=req.base or req.surface,
            reading_hiragana=reading_hira,
            reading_katakana=reading_kata,
            pos_detail=req.pos_detail,
            definitions_fr=dict_info["definitions_fr"],
            definitions_en=dict_info["definitions_en"],
            sentence_html=sentence_html,
            sentence_plain=sentence_plain,
            book_id=req.book_id,
            book_title=req.book_title,
            chapter_title=req.chapter_title,
            frequency_rank=freq_rank,
        )

    return {
        "surface": req.surface,
        "base": req.base or req.surface,
        "reading_hiragana": reading_hira,
        "reading_katakana": reading_kata,
        "surface_hiragana": req.surface_hiragana or reading_hira,
        "surface_katakana": req.surface_katakana or reading_kata,
        "pos_detail": req.pos_detail,
        "frequency_rank": freq_rank,
        "definitions_fr": dict_info["definitions_fr"],
        "definitions_en": dict_info["definitions_en"],
        "entries": dict_info["entries"],
        "sentence_html": sentence_html,
        "sentence_plain": sentence_plain,
        "mined_card": mined_card,
    }


# --- Anki Queue, Known Vocabulary & Snapshot Endpoints ---


@app.post("/api/anki/sync-snapshot")
def api_sync_anki_snapshot() -> Dict[str, Any]:
    """Reads a safe read-only snapshot of /anki_data/enzo/collection.anki2 (+ -wal)."""
    return inspect_and_sync_anki_snapshot()


@app.post("/api/anki/known-words")
def api_upload_known_words(req: KnownWordsUploadRequest) -> Dict[str, Any]:
    total = upsert_known_words(req.words, source=req.source)
    return {"status": "ok", "total_known_words": total}


@app.get("/api/anki/cards")
def api_get_mined_cards(only_unsynced: bool = False) -> Dict[str, Any]:
    settings = get_settings()
    field_map = settings.get("anki_field_map")
    cards = list_mined_cards(only_unsynced=only_unsynced, limit=300)
    for c in cards:
        c["anki_fields"] = build_anki_fields_for_card(c, field_map)
    unsynced_count = len(list_mined_cards(only_unsynced=True, limit=5000))
    return {
        "cards": cards,
        "unsynced_count": unsynced_count,
        "known_words_count": get_known_words_count(),
        "deck_name": settings.get("anki_deck_name", "Data Mining"),
        "model_name": settings.get("anki_model_name", "YomiCalibre Mining"),
        "model_spec": get_ankiconnect_model_spec(
            settings.get("anki_model_name", "YomiCalibre Mining")
        ),
    }


@app.post("/api/anki/cards/{card_id}/mark-synced")
def api_mark_card_synced(card_id: int, req: MarkSyncedRequest) -> Dict[str, Any]:
    mark_card_synced(card_id, req.anki_note_id)
    return {"status": "ok", "card_id": card_id}


@app.delete("/api/anki/cards/{card_id}")
def api_delete_card(card_id: int) -> Dict[str, Any]:
    deleted = delete_mined_card(card_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Card not found")
    return {"status": "ok"}


@app.post("/api/anki/sync-server")
def api_sync_unsynced_from_server() -> Dict[str, Any]:
    settings = get_settings()
    deck_name = settings.get("anki_deck_name", "Data Mining")
    model_name = settings.get("anki_model_name", "YomiCalibre Mining")
    ankiconnect_url = settings.get("ankiconnect_url", "http://127.0.0.1:8765")
    field_map = settings.get("anki_field_map")

    pending = list_mined_cards(only_unsynced=True, limit=1000)
    synced_now = 0
    for card in pending:
        note_id = try_push_card_to_ankiconnect(
            card, deck_name, model_name, ankiconnect_url, field_map
        )
        if note_id:
            mark_card_synced(card["id"], note_id)
            synced_now += 1

    remaining = len(list_mined_cards(only_unsynced=True, limit=5000))
    return {
        "synced_now": synced_now,
        "remaining_unsynced": remaining,
        "deck_name": deck_name,
    }


@app.get("/api/anki/export.apkg")
def api_export_apkg(only_unsynced: bool = False):
    apkg_path = export_cards_to_apkg(only_unsynced=only_unsynced)
    return FileResponse(
        str(apkg_path),
        media_type="application/octet-stream",
        filename="Data_Mining_Japanese.apkg",
    )


@app.get("/api/anki/export.tsv")
def api_export_tsv(only_unsynced: bool = False):
    tsv_path = export_cards_to_tsv(only_unsynced=only_unsynced)
    return FileResponse(
        str(tsv_path),
        media_type="text/tab-separated-values",
        filename="Data_Mining_Japanese.tsv",
    )


# --- Settings & Dictionary Endpoints ---


@app.get("/api/settings")
def api_get_settings() -> Dict[str, Any]:
    s = get_settings()
    s["dictionary_stats"] = get_dictionary_stats()
    s["known_words_count"] = get_known_words_count()
    return s


@app.post("/api/settings")
def api_update_settings(req: SettingsUpdateRequest) -> Dict[str, Any]:
    payload = {k: v for k, v in req.dict().items() if v is not None}
    updated = update_settings(payload)
    updated["dictionary_stats"] = get_dictionary_stats()
    updated["known_words_count"] = get_known_words_count()
    return updated


@app.post("/api/dictionary/build-full")
def api_build_full_jmdict(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    from scripts.build_jmdict import download_and_build_jmdict

    background_tasks.add_task(download_and_build_jmdict)
    return {
        "status": "started",
        "message": "Background download and indexing of JMdict started.",
    }


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
