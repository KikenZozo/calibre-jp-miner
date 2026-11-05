import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import (
    APP_DB_PATH,
    DEFAULT_ANKI_DECK,
    DEFAULT_ANKI_MODEL,
    DEFAULT_ANKICONNECT_URL,
    DEFAULT_AUTO_MINE_ON_CLICK,
)


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(APP_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_app_db() -> None:
    conn = get_db_connection()
    cur = conn.cursor()

    # Table des paramètres globaux (synchronisés entre téléphone et PC)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    # Table de progression de lecture par livre
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reading_progress (
            book_id TEXT PRIMARY KEY,
            book_title TEXT,
            chapter_index INTEGER DEFAULT 0,
            scroll_ratio REAL DEFAULT 0.0,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Table des cartes Anki minées (historique + file d'attente de synchronisation Anki)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mined_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word_surface TEXT NOT NULL,
            word_base TEXT NOT NULL,
            reading_hiragana TEXT NOT NULL,
            reading_katakana TEXT NOT NULL,
            pos_detail TEXT DEFAULT '',
            definitions_fr TEXT DEFAULT '[]',
            definitions_en TEXT DEFAULT '[]',
            sentence_html TEXT NOT NULL,
            sentence_plain TEXT NOT NULL,
            book_id TEXT DEFAULT '',
            book_title TEXT DEFAULT '',
            chapter_title TEXT DEFAULT '',
            deck_name TEXT NOT NULL,
            anki_synced INTEGER DEFAULT 0,
            anki_note_id INTEGER DEFAULT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # Index pour retrouver rapidement si un mot a déjà été miné
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_mined_word_base ON mined_cards(word_base)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_mined_synced ON mined_cards(anki_synced)"
    )

    # Valeurs par défaut des paramètres
    defaults = {
        "anki_deck_name": DEFAULT_ANKI_DECK,
        "anki_model_name": DEFAULT_ANKI_MODEL,
        "ankiconnect_url": DEFAULT_ANKICONNECT_URL,
        "auto_mine_on_click": "true" if DEFAULT_AUTO_MINE_ON_CLICK else "false",
    }
    for k, v in defaults.items():
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, str(v))
        )

    conn.commit()
    conn.close()


def get_settings() -> Dict[str, Any]:
    conn = get_db_connection()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    result: Dict[str, Any] = {r["key"]: r["value"] for r in rows}
    result["auto_mine_on_click"] = str(result.get("auto_mine_on_click", "true")).lower() == "true"
    return result


def update_settings(new_settings: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_db_connection()
    cur = conn.cursor()
    for k, v in new_settings.items():
        if isinstance(v, bool):
            val_str = "true" if v else "false"
        else:
            val_str = str(v)
        cur.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, val_str)
        )
    conn.commit()
    conn.close()
    return get_settings()


def save_reading_progress(
    book_id: str, book_title: str, chapter_index: int, scroll_ratio: float
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    conn.execute(
        """
        INSERT OR REPLACE INTO reading_progress
        (book_id, book_title, chapter_index, scroll_ratio, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (book_id, book_title, chapter_index, scroll_ratio, now),
    )
    conn.commit()
    conn.close()


def get_reading_progress(book_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    row = conn.execute(
        "SELECT * FROM reading_progress WHERE book_id = ?", (book_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_reading_progress() -> Dict[str, Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM reading_progress").fetchall()
    conn.close()
    return {r["book_id"]: dict(r) for r in rows}


def insert_mined_card(
    word_surface: str,
    word_base: str,
    reading_hiragana: str,
    reading_katakana: str,
    pos_detail: str,
    definitions_fr: List[str],
    definitions_en: List[str],
    sentence_html: str,
    sentence_plain: str,
    book_id: str,
    book_title: str,
    chapter_title: str,
    deck_name: str,
    anki_synced: int = 0,
    anki_note_id: Optional[int] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    cur = conn.cursor()

    # Évite les doublons exacts (même mot + même phrase)
    existing = cur.execute(
        """
        SELECT * FROM mined_cards
        WHERE word_base = ? AND sentence_plain = ?
        ORDER BY id DESC LIMIT 1
        """,
        (word_base, sentence_plain),
    ).fetchone()

    if existing:
        card = _format_card_row(existing)
        card["already_existed"] = True
        conn.close()
        return card

    cur.execute(
        """
        INSERT INTO mined_cards (
            word_surface, word_base, reading_hiragana, reading_katakana,
            pos_detail, definitions_fr, definitions_en,
            sentence_html, sentence_plain,
            book_id, book_title, chapter_title,
            deck_name, anki_synced, anki_note_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            word_surface,
            word_base,
            reading_hiragana,
            reading_katakana,
            pos_detail,
            json.dumps(definitions_fr, ensure_ascii=False),
            json.dumps(definitions_en, ensure_ascii=False),
            sentence_html,
            sentence_plain,
            book_id,
            book_title,
            chapter_title,
            deck_name,
            anki_synced,
            anki_note_id,
            now,
        ),
    )
    card_id = cur.lastrowid
    conn.commit()
    row = cur.execute("SELECT * FROM mined_cards WHERE id = ?", (card_id,)).fetchone()
    conn.close()
    card = _format_card_row(row)
    card["already_existed"] = False
    return card


def mark_card_synced(card_id: int, anki_note_id: Optional[int] = None) -> None:
    conn = get_db_connection()
    conn.execute(
        "UPDATE mined_cards SET anki_synced = 1, anki_note_id = ? WHERE id = ?",
        (anki_note_id, card_id),
    )
    conn.commit()
    conn.close()


def delete_mined_card(card_id: int) -> bool:
    conn = get_db_connection()
    cur = conn.execute("DELETE FROM mined_cards WHERE id = ?", (card_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def list_mined_cards(only_unsynced: bool = False, limit: int = 200) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    if only_unsynced:
        rows = conn.execute(
            "SELECT * FROM mined_cards WHERE anki_synced = 0 ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM mined_cards ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [_format_card_row(r) for r in rows]


def _format_card_row(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["definitions_fr"] = json.loads(d.get("definitions_fr") or "[]")
    except Exception:
        d["definitions_fr"] = []
    try:
        d["definitions_en"] = json.loads(d.get("definitions_en") or "[]")
    except Exception:
        d["definitions_en"] = []
    d["anki_synced"] = bool(d.get("anki_synced"))
    return d
