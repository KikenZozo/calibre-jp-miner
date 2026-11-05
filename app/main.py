import os
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.anki_service import (
    build_anki_fields_for_card,
    create_and_push_anki_card,
    export_cards_to_apkg,
    get_ankiconnect_model_spec,
    try_push_card_to_ankiconnect,
)
from app.calibre_library import get_book_by_id, list_all_books
from app.config import UPLOADS_DIR
from app.database import (
    delete_mined_card,
    get_settings,
    init_app_db,
    list_mined_cards,
    mark_card_synced,
    save_reading_progress,
    update_settings,
)
from app.dictionary import get_dictionary_stats, init_jmdict_db, lookup_word
from app.epub_parser import parse_epub_chapter_tokenized
from app.japanese_nlp import build_underlined_sentence_html

app = FastAPI(
    title="YomiCalibre — Lecteur & Mineur Japonais pour Calibre & Anki",
    version="1.0.0",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.on_event("startup")
def on_startup() -> None:
    init_app_db()
    init_jmdict_db()
    # Si aucun livre n'est encore présent dans le dossier de démo, génère un livre EPUB d'exemple
    books = list_all_books()
    if not books:
        try:
            from scripts.create_sample_epub import create_sample_japanese_epub

            create_sample_japanese_epub()
        except Exception:
            pass

    # En production (Dokploy/Docker), télécharge automatiquement le dictionnaire complet JMdict FR/EN
    # au premier démarrage si la base ne contient que le dictionnaire de base.
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


# --- Modèles de requêtes ---


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


class MarkSyncedRequest(BaseModel):
    anki_note_id: Optional[int] = None


# --- Endpoints Bibliothèque Calibre & EPUB ---


@app.get("/api/books")
def api_list_books() -> Dict[str, Any]:
    books = list_all_books()
    return {
        "books": books,
        "total": len(books),
    }


@app.post("/api/books/upload")
async def api_upload_epub(file: UploadFile = File(...)) -> Dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".epub"):
        raise HTTPException(status_code=400, detail="Veuillez sélectionner un fichier .epub valide.")
    safe_name = Path(file.filename).name
    dest = UPLOADS_DIR / safe_name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    books = list_all_books()
    return {"status": "ok", "filename": safe_name, "books": books}


@app.get("/api/books/{book_id}/cover")
def api_book_cover(book_id: str):
    book = get_book_by_id(book_id)
    if not book or not book.get("cover_path"):
        raise HTTPException(status_code=404, detail="Couverture absente")
    cover_path = Path(book["cover_path"])
    if not cover_path.exists():
        raise HTTPException(status_code=404, detail="Fichier couverture introuvable")
    return FileResponse(str(cover_path))


@app.get("/api/books/{book_id}/chapter/{chapter_index}")
def api_get_chapter(book_id: str, chapter_index: int) -> Dict[str, Any]:
    book = get_book_by_id(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Livre introuvable dans la bibliothèque.")
    epub_path = Path(book["epub_path"])
    try:
        chapter_data = parse_epub_chapter_tokenized(epub_path, chapter_index)
        chapter_data["book_id"] = book_id
        chapter_data["book_title"] = book["title"]
        chapter_data["author"] = book["author"]
        return chapter_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de lecture EPUB : {e}")


@app.post("/api/books/{book_id}/progress")
def api_save_progress(book_id: str, req: ProgressUpdateRequest) -> Dict[str, Any]:
    save_reading_progress(
        book_id=book_id,
        book_title=req.book_title,
        chapter_index=req.chapter_index,
        scroll_ratio=req.scroll_ratio,
    )
    return {"status": "ok"}


# --- Endpoint Clic sur un mot prédécoupé (Dictionnaire + Phrase soulignée + Anki Auto) ---


@app.post("/api/word/click")
def api_word_click(req: WordClickRequest) -> Dict[str, Any]:
    """
    Appelé dès que l'utilisateur clique sur un mot prédécoupé :
    1. Cherche la définition (Français en priorité + Anglais en complément) dans JMdict.
    2. Récupère la lecture exacte en Hiragana et en Katakana.
    3. Reconstruit la phrase exacte du livre en soulignant (<u>mot</u>) le mot cliqué.
    4. Si le minage automatique est actif (ou si demandé explicitement), crée automatiquement
       la carte Anki dans le deck de data mining.
    """
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
        )

    return {
        "surface": req.surface,
        "base": req.base or req.surface,
        "reading_hiragana": reading_hira,
        "reading_katakana": reading_kata,
        "surface_hiragana": req.surface_hiragana or reading_hira,
        "surface_katakana": req.surface_katakana or reading_kata,
        "pos_detail": req.pos_detail,
        "definitions_fr": dict_info["definitions_fr"],
        "definitions_en": dict_info["definitions_en"],
        "entries": dict_info["entries"],
        "sentence_html": sentence_html,
        "sentence_plain": sentence_plain,
        "mined_card": mined_card,
    }


# --- Endpoints Anki & File de Data Mining ---


@app.get("/api/anki/cards")
def api_get_mined_cards(only_unsynced: bool = False) -> Dict[str, Any]:
    settings = get_settings()
    cards = list_mined_cards(only_unsynced=only_unsynced, limit=300)
    for c in cards:
        c["anki_fields"] = build_anki_fields_for_card(c)
    unsynced_count = len(list_mined_cards(only_unsynced=True, limit=5000))
    return {
        "cards": cards,
        "unsynced_count": unsynced_count,
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
        raise HTTPException(status_code=404, detail="Carte introuvable")
    return {"status": "ok"}


@app.post("/api/anki/sync-server")
def api_sync_unsynced_from_server() -> Dict[str, Any]:
    """Tente de pousser toutes les cartes en attente vers AnkiConnect depuis le serveur."""
    settings = get_settings()
    deck_name = settings.get("anki_deck_name", "Data Mining")
    model_name = settings.get("anki_model_name", "YomiCalibre Mining")
    ankiconnect_url = settings.get("ankiconnect_url", "http://127.0.0.1:8765")

    pending = list_mined_cards(only_unsynced=True, limit=1000)
    synced_now = 0
    for card in pending:
        note_id = try_push_card_to_ankiconnect(
            card, deck_name, model_name, ankiconnect_url
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
        filename="Data_Mining_Japonais.apkg",
    )


# --- Endpoints Paramètres & Dictionnaire JMdict ---


@app.get("/api/settings")
def api_get_settings() -> Dict[str, Any]:
    s = get_settings()
    s["dictionary_stats"] = get_dictionary_stats()
    return s


@app.post("/api/settings")
def api_update_settings(req: SettingsUpdateRequest) -> Dict[str, Any]:
    payload = {k: v for k, v in req.dict().items() if v is not None}
    updated = update_settings(payload)
    updated["dictionary_stats"] = get_dictionary_stats()
    return updated


@app.post("/api/dictionary/build-full")
def api_build_full_jmdict(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    from scripts.build_jmdict import download_and_build_jmdict

    background_tasks.add_task(download_and_build_jmdict)
    return {
        "status": "started",
        "message": "Téléchargement et indexation du dictionnaire JMdict complet (FR + EN) lancés en tâche de fond.",
    }


# Montage des fichiers statiques de la Web-App
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
