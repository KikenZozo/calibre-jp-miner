import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from app.config import (
    DEFAULT_ANKI_DECK,
    DEFAULT_ANKI_MODEL,
    DEFAULT_ANKICONNECT_URL,
    DEFAULT_AUTO_MINE_ON_CLICK,
    MINING_DB_PATH,
)


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(MINING_DB_PATH), timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def compute_deterministic_guid(lemma: str, sentence_plain: str) -> str:
    """Deterministic 10-character hex GUID so re-imports never create duplicate notes."""
    raw = f"{lemma.strip()}::{sentence_plain.strip()}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:10]


def init_app_db() -> None:
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    # Permanent extraction cache keyed on Calibre books.uuid (prevents re-opening Drive symlinks)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS books_cache (
            book_uuid TEXT PRIMARY KEY,
            calibre_id INTEGER DEFAULT 0,
            title TEXT NOT NULL,
            author TEXT DEFAULT '',
            source_format TEXT NOT NULL,
            uncompressed_size INTEGER DEFAULT 0,
            is_symlink INTEGER DEFAULT 0,
            has_text_layer INTEGER DEFAULT 1,
            total_sections INTEGER DEFAULT 0,
            total_sentences INTEGER DEFAULT 0,
            extracted_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS book_sections (
            book_uuid TEXT NOT NULL,
            section_index INTEGER NOT NULL,
            section_title TEXT NOT NULL,
            blocks_json TEXT NOT NULL,
            PRIMARY KEY (book_uuid, section_index)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS book_sentences (
            sentence_id TEXT PRIMARY KEY,
            book_uuid TEXT NOT NULL,
            section_index INTEGER NOT NULL,
            sentence_index INTEGER NOT NULL,
            text_plain TEXT NOT NULL,
            tokens_json TEXT NOT NULL
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_sentences_book_sec ON book_sentences(book_uuid, section_index)"
    )

    # Per-page clickable word geometry for the classic PDF view (text layer or OCR)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pdf_page_layout (
            book_uuid TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            source TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (book_uuid, page_number)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reading_progress (
            book_uuid TEXT PRIMARY KEY,
            book_title TEXT,
            section_index INTEGER DEFAULT 0,
            scroll_ratio REAL DEFAULT 0.0,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Known words imported from collection.anki2 read-only snapshot or AnkiConnect
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS known_vocabulary (
            lemma TEXT PRIMARY KEY,
            reading_hira TEXT DEFAULT '',
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Mined cards queue with deterministic GUID
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mining_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT UNIQUE NOT NULL,
            book_uuid TEXT DEFAULT '',
            book_title TEXT DEFAULT '',
            section_title TEXT DEFAULT '',
            word_surface TEXT NOT NULL,
            word_base TEXT NOT NULL,
            reading_hiragana TEXT NOT NULL,
            reading_katakana TEXT NOT NULL,
            pos_detail TEXT DEFAULT '',
            frequency_rank INTEGER DEFAULT 999999,
            definitions_fr TEXT DEFAULT '[]',
            definitions_en TEXT DEFAULT '[]',
            sentence_html TEXT NOT NULL,
            sentence_plain TEXT NOT NULL,
            deck_name TEXT NOT NULL,
            anki_synced INTEGER DEFAULT 0,
            anki_note_id INTEGER DEFAULT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_mining_base ON mining_queue(word_base)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_mining_synced ON mining_queue(anki_synced)"
    )

    default_field_map = {
        "word": "Mot",
        "reading_hiragana": "Lecture_Hiragana",
        "reading_katakana": "Lecture_Katakana",
        "pos": "Grammaire",
        "definition_fr": "Definition_FR",
        "definition_en": "Definition_EN",
        "sentence": "Phrase_Contexte",
        "source": "Source",
    }

    defaults = {
        "anki_deck_name": DEFAULT_ANKI_DECK,
        "anki_model_name": DEFAULT_ANKI_MODEL,
        "ankiconnect_url": DEFAULT_ANKICONNECT_URL,
        "auto_mine_on_click": "true" if DEFAULT_AUTO_MINE_ON_CLICK else "false",
        "anki_field_map": json.dumps(default_field_map),
    }
    for k, v in defaults.items():
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, str(v))
        )

    _migrate_schema(cur)

    conn.commit()
    conn.close()


def _migrate_schema(cur: sqlite3.Cursor) -> None:
    """Adds columns introduced after the first release, for existing mining.db files."""
    additions = {
        "books_cache": {
            # Bumped when the extractor starts producing richer sections (images,
            # table of contents), so already-cached books are re-extracted instead
            # of staying stuck on the older, poorer output.
            "extraction_version": "INTEGER DEFAULT 0",
            "toc_json": "TEXT DEFAULT '[]'",
        },
        "known_vocabulary": {
            "is_known": "INTEGER DEFAULT 1",
        },
    }
    for table, columns in additions.items():
        existing = {r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, spec in columns.items():
            if name not in existing:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {spec}")


def get_settings() -> Dict[str, Any]:
    conn = get_db_connection()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    result: Dict[str, Any] = {r["key"]: r["value"] for r in rows}
    result["auto_mine_on_click"] = (
        str(result.get("auto_mine_on_click", "true")).lower() == "true"
    )
    try:
        result["anki_field_map"] = json.loads(result.get("anki_field_map") or "{}")
    except Exception:
        result["anki_field_map"] = {}
    return result


def update_settings(new_settings: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_db_connection()
    cur = conn.cursor()
    for k, v in new_settings.items():
        if v is None:
            continue
        if isinstance(v, bool):
            val_str = "true" if v else "false"
        elif isinstance(v, dict):
            val_str = json.dumps(v, ensure_ascii=False)
        else:
            val_str = str(v)
        cur.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, val_str)
        )
    conn.commit()
    conn.close()
    return get_settings()


# --- Permanent Book Extraction Cache Operations ---


def is_book_extracted(book_uuid: str, min_version: int = 0) -> bool:
    """
    True when the book is cached AND was produced by a recent enough extractor.
    A stale cache (missing images or the table of contents) is treated as absent so
    the book is re-extracted once, rather than staying degraded forever.
    """
    conn = get_db_connection()
    row = conn.execute(
        "SELECT extraction_version FROM books_cache WHERE book_uuid = ?", (book_uuid,)
    ).fetchone()
    conn.close()
    if row is None:
        return False
    return int(row["extraction_version"] or 0) >= min_version


def get_cached_books_map() -> Dict[str, Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM books_cache").fetchall()
    conn.close()
    return {r["book_uuid"]: dict(r) for r in rows}


def store_extracted_book(
    book_uuid: str,
    calibre_id: int,
    title: str,
    author: str,
    source_format: str,
    uncompressed_size: int,
    is_symlink: bool,
    has_text_layer: bool,
    sections: List[Dict[str, Any]],
    sentences: List[Dict[str, Any]],
    toc: Optional[List[Dict[str, Any]]] = None,
    extraction_version: int = 0,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM book_sections WHERE book_uuid = ?", (book_uuid,))
    cur.execute("DELETE FROM book_sentences WHERE book_uuid = ?", (book_uuid,))

    cur.execute(
        """
        INSERT OR REPLACE INTO books_cache (
            book_uuid, calibre_id, title, author, source_format,
            uncompressed_size, is_symlink, has_text_layer,
            total_sections, total_sentences, extracted_at,
            extraction_version, toc_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            book_uuid,
            calibre_id,
            title,
            author,
            source_format,
            uncompressed_size,
            1 if is_symlink else 0,
            1 if has_text_layer else 0,
            len(sections),
            len(sentences),
            now,
            extraction_version,
            json.dumps(toc or [], ensure_ascii=False),
        ),
    )

    section_rows = [
        (
            book_uuid,
            sec["section_index"],
            sec["section_title"],
            json.dumps(sec["blocks"], ensure_ascii=False),
        )
        for sec in sections
    ]
    cur.executemany(
        """
        INSERT INTO book_sections (book_uuid, section_index, section_title, blocks_json)
        VALUES (?, ?, ?, ?)
        """,
        section_rows,
    )

    sentence_rows = [
        (
            s["sid"],
            book_uuid,
            s["section_index"],
            s["sentence_index"],
            s["text"],
            json.dumps(s["tokens"], ensure_ascii=False),
        )
        for s in sentences
    ]
    cur.executemany(
        """
        INSERT INTO book_sentences (
            sentence_id, book_uuid, section_index, sentence_index, text_plain, tokens_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        sentence_rows,
    )

    conn.commit()
    conn.close()


def get_cached_book_section(
    book_uuid: str, section_index: int, include_sentence_map: bool = False
) -> Optional[Dict[str, Any]]:
    """
    One section of a cached book.

    `include_sentence_map` adds a sid -> sentence dictionary. It is off by default
    because every token in it is already present inside `blocks`, and shipping both
    doubled the size of a chapter response for nothing.
    """
    conn = get_db_connection()
    book_meta = conn.execute(
        "SELECT * FROM books_cache WHERE book_uuid = ?", (book_uuid,)
    ).fetchone()
    if not book_meta:
        conn.close()
        return None

    all_secs = conn.execute(
        """
        SELECT section_index, section_title
        FROM book_sections
        WHERE book_uuid = ?
        ORDER BY section_index ASC
        """,
        (book_uuid,),
    ).fetchall()

    if not all_secs:
        conn.close()
        return {
            "book_uuid": book_uuid,
            "book_title": book_meta["title"],
            "author": book_meta["author"],
            "has_text_layer": bool(book_meta["has_text_layer"]),
            "chapter_index": 0,
            "chapter_title": "Empty",
            "total_chapters": 0,
            "chapters": [],
            "blocks": [],
            "sentences": {},
        }

    # Sections are dense for an EPUB (0..n-1) but can be sparse for a PDF whose
    # pages were OCR'd on demand, so resolve the request against what exists.
    available = [r["section_index"] for r in all_secs]
    if section_index not in available:
        section_index = available[max(0, min(section_index, len(available) - 1))]

    sec_row = conn.execute(
        """
        SELECT * FROM book_sections
        WHERE book_uuid = ? AND section_index = ?
        """,
        (book_uuid, section_index),
    ).fetchone()

    sentences_map: Dict[str, Any] = {}
    if include_sentence_map:
        sent_rows = conn.execute(
            """
            SELECT * FROM book_sentences
            WHERE book_uuid = ? AND section_index = ?
            ORDER BY sentence_index ASC
            """,
            (book_uuid, section_index),
        ).fetchall()
        for sr in sent_rows:
            sentences_map[sr["sentence_id"]] = {
                "sid": sr["sentence_id"],
                "text": sr["text_plain"],
                "tokens": json.loads(sr["tokens_json"]),
            }
    conn.close()

    blocks = json.loads(sec_row["blocks_json"]) if sec_row else []

    chapters_list = [
        {"index": r["section_index"], "title": r["section_title"]} for r in all_secs
    ]

    try:
        toc = json.loads(book_meta["toc_json"] or "[]")
    except Exception:
        toc = []

    return {
        "book_id": book_uuid,
        "book_uuid": book_uuid,
        "book_title": book_meta["title"],
        "author": book_meta["author"],
        "source_format": book_meta["source_format"],
        "has_text_layer": bool(book_meta["has_text_layer"]),
        "chapter_index": section_index,
        "chapter_title": sec_row["section_title"] if sec_row else f"Section {section_index + 1}",
        "total_chapters": len(chapters_list),
        "chapters": chapters_list,
        "toc": toc,
        "blocks": blocks,
        "sentences": sentences_map,
    }


def get_all_cached_sentences_for_book(book_uuid: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT bs.*, sec.section_title
        FROM book_sentences bs
        LEFT JOIN book_sections sec
          ON bs.book_uuid = sec.book_uuid AND bs.section_index = sec.section_index
        WHERE bs.book_uuid = ?
        ORDER BY bs.section_index ASC, bs.sentence_index ASC
        """,
        (book_uuid,),
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append(
            {
                "sid": r["sentence_id"],
                "section_index": r["section_index"],
                "section_title": r["section_title"] or f"Section {r['section_index'] + 1}",
                "sentence_index": r["sentence_index"],
                "text": r["text_plain"],
                "tokens": json.loads(r["tokens_json"]),
            }
        )
    return result



# --- PDF Page Layout Cache (classic PDF view / OCR) ---


def get_cached_pdf_page_layout(
    book_uuid: str, page_number: int
) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    row = conn.execute(
        "SELECT payload_json FROM pdf_page_layout WHERE book_uuid = ? AND page_number = ?",
        (book_uuid, page_number),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return json.loads(row["payload_json"])
    except Exception:
        return None


def store_pdf_page_layout(
    book_uuid: str, page_number: int, source: str, payload: Dict[str, Any]
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    conn.execute(
        """
        INSERT OR REPLACE INTO pdf_page_layout
        (book_uuid, page_number, source, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (book_uuid, page_number, source, json.dumps(payload, ensure_ascii=False), now),
    )
    conn.commit()
    conn.close()


def count_cached_pdf_pages(book_uuid: str) -> Dict[str, Any]:
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT source, COUNT(*) AS n FROM pdf_page_layout "
        "WHERE book_uuid = ? AND page_number >= 0 GROUP BY source",
        (book_uuid,),
    ).fetchall()
    conn.close()
    by_source = {r["source"]: r["n"] for r in rows}
    return {
        "total": sum(by_source.values()),
        "by_source": by_source,
    }


def clear_pdf_page_layouts(book_uuid: str) -> int:
    conn = get_db_connection()
    cur = conn.execute("DELETE FROM pdf_page_layout WHERE book_uuid = ?", (book_uuid,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted


def get_cached_pdf_info(book_uuid: str) -> Optional[Dict[str, Any]]:
    """Cached page count / text-layer probe (page_number -1 is the per-book row)."""
    conn = get_db_connection()
    row = conn.execute(
        "SELECT payload_json FROM pdf_page_layout WHERE book_uuid = ? AND page_number = -1",
        (book_uuid,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return json.loads(row["payload_json"])
    except Exception:
        return None


def store_pdf_info(book_uuid: str, info: Dict[str, Any]) -> None:
    store_pdf_page_layout(book_uuid, -1, "info", info)


def store_section_sentences(
    book_uuid: str,
    section_index: int,
    section_title: str,
    sentences: List[Dict[str, Any]],
    book_title: str = "",
    source_format: str = "PDF",
) -> None:
    """
    Registers one lazily-produced section (a PDF page) so batch i+1 mining and the
    library cache badge see OCR'd or on-demand content without a full extraction.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        "INSERT OR IGNORE INTO books_cache (book_uuid, calibre_id, title, author, source_format, extracted_at) "
        "VALUES (?, 0, ?, '', ?, ?)",
        (book_uuid, book_title or book_uuid, source_format, now),
    )

    # One renderable block per section so the reflowed reader can display an
    # OCR'd page, not just the batch i+1 tab.
    blocks = (
        [
            {
                "block_id": "b0",
                "type": "p",
                "style_class": "",
                "sentences": sentences,
            }
        ]
        if sentences
        else []
    )
    cur.execute(
        "INSERT OR REPLACE INTO book_sections (book_uuid, section_index, section_title, blocks_json) "
        "VALUES (?, ?, ?, ?)",
        (book_uuid, section_index, section_title, json.dumps(blocks, ensure_ascii=False)),
    )

    cur.execute(
        "DELETE FROM book_sentences WHERE book_uuid = ? AND section_index = ?",
        (book_uuid, section_index),
    )
    cur.executemany(
        """
        INSERT OR REPLACE INTO book_sentences (
            sentence_id, book_uuid, section_index, sentence_index, text_plain, tokens_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                s["sid"],
                book_uuid,
                section_index,
                s.get("sentence_index", idx),
                s.get("text", ""),
                json.dumps(s.get("tokens", []), ensure_ascii=False),
            )
            for idx, s in enumerate(sentences)
        ],
    )

    totals = cur.execute(
        "SELECT COUNT(DISTINCT section_index) AS secs, COUNT(*) AS sents "
        "FROM book_sentences WHERE book_uuid = ?",
        (book_uuid,),
    ).fetchone()
    cur.execute(
        "UPDATE books_cache SET total_sections = ?, total_sentences = ? WHERE book_uuid = ?",
        (totals["secs"], totals["sents"], book_uuid),
    )

    conn.commit()
    conn.close()


# --- Known Vocabulary Operations ---


def upsert_known_words(words: List[str], source: str = "anki_snapshot") -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    cur = conn.cursor()
    rows = [(w.strip(), source, now) for w in words if w and w.strip()]
    # ON CONFLICT ... DO UPDATE (not INSERT OR REPLACE) so an explicit
    # "I do not know this word" is not silently undone by the next Anki sync.
    cur.executemany(
        """
        INSERT INTO known_vocabulary (lemma, source, updated_at, is_known)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(lemma) DO UPDATE SET
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        rows,
    )
    conn.commit()
    total = cur.execute("SELECT COUNT(*) FROM known_vocabulary").fetchone()[0]
    conn.close()
    return total


def get_known_words_set() -> Set[str]:
    """Everything the reader counts as already acquired: known + already mined."""
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT lemma FROM known_vocabulary WHERE COALESCE(is_known, 1) = 1"
    ).fetchall()
    mined_rows = conn.execute("SELECT DISTINCT word_base FROM mining_queue").fetchall()
    conn.close()
    known = {r["lemma"] for r in rows}
    known.update(r["word_base"] for r in mined_rows)
    return known


def get_vocabulary_state() -> Dict[str, Set[str]]:
    """
    The three states the reader colours words with:
      known     - marked known by you, or imported from Anki
      mined     - already encountered and queued/synced as a card
      unknown   - explicitly marked as NOT known, which overrides the others
    Anything in none of these is new.
    """
    conn = get_db_connection()
    known_rows = conn.execute(
        "SELECT lemma, COALESCE(is_known, 1) AS is_known FROM known_vocabulary"
    ).fetchall()
    mined_rows = conn.execute("SELECT DISTINCT word_base FROM mining_queue").fetchall()
    conn.close()

    return {
        "known": {r["lemma"] for r in known_rows if r["is_known"]},
        "unknown": {r["lemma"] for r in known_rows if not r["is_known"]},
        "mined": {r["word_base"] for r in mined_rows if r["word_base"]},
    }


def set_word_known(lemma: str, known: bool, source: str = "manual") -> None:
    """
    Records an explicit decision about one word.

    `known=False` is stored rather than deleted so it can override a word that Anki
    or the mining queue would otherwise mark as acquired.
    """
    lemma = (lemma or "").strip()
    if not lemma:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO known_vocabulary (lemma, source, updated_at, is_known)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(lemma) DO UPDATE SET
            is_known = excluded.is_known,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        (lemma, source, now, 1 if known else 0),
    )
    conn.commit()
    conn.close()


def get_known_words_count() -> int:
    conn = get_db_connection()
    cnt = conn.execute(
        "SELECT COUNT(*) FROM known_vocabulary WHERE COALESCE(is_known, 1) = 1"
    ).fetchone()[0]
    conn.close()
    return cnt


# --- Reading Progress & Mining Queue Operations ---


def save_reading_progress(
    book_uuid: str, book_title: str, chapter_index: int, scroll_ratio: float
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    conn.execute(
        """
        INSERT OR REPLACE INTO reading_progress
        (book_uuid, book_title, section_index, scroll_ratio, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (book_uuid, book_title, chapter_index, scroll_ratio, now),
    )
    conn.commit()
    conn.close()


def get_all_reading_progress() -> Dict[str, Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM reading_progress").fetchall()
    conn.close()
    out = {}
    for r in rows:
        d = dict(r)
        d["chapter_index"] = d["section_index"]
        out[r["book_uuid"]] = d
    return out


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
    frequency_rank: int = 999999,
    anki_synced: int = 0,
    anki_note_id: Optional[int] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    guid = compute_deterministic_guid(word_base or word_surface, sentence_plain)

    conn = get_db_connection()
    cur = conn.cursor()

    existing = cur.execute(
        "SELECT * FROM mining_queue WHERE guid = ? LIMIT 1", (guid,)
    ).fetchone()

    if existing:
        card = _format_card_row(existing)
        card["already_existed"] = True
        conn.close()
        return card

    cur.execute(
        """
        INSERT INTO mining_queue (
            guid, book_uuid, book_title, section_title,
            word_surface, word_base, reading_hiragana, reading_katakana,
            pos_detail, frequency_rank, definitions_fr, definitions_en,
            sentence_html, sentence_plain,
            deck_name, anki_synced, anki_note_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guid,
            book_id,
            book_title,
            chapter_title,
            word_surface,
            word_base,
            reading_hiragana,
            reading_katakana,
            pos_detail,
            frequency_rank,
            json.dumps(definitions_fr, ensure_ascii=False),
            json.dumps(definitions_en, ensure_ascii=False),
            sentence_html,
            sentence_plain,
            deck_name,
            anki_synced,
            anki_note_id,
            now,
        ),
    )
    card_id = cur.lastrowid
    conn.commit()
    row = cur.execute("SELECT * FROM mining_queue WHERE id = ?", (card_id,)).fetchone()
    conn.close()
    card = _format_card_row(row)
    card["already_existed"] = False
    return card


def mark_card_synced(card_id: int, anki_note_id: Optional[int] = None) -> None:
    conn = get_db_connection()
    conn.execute(
        "UPDATE mining_queue SET anki_synced = 1, anki_note_id = ? WHERE id = ?",
        (anki_note_id, card_id),
    )
    conn.commit()
    conn.close()


def delete_mined_card(card_id: int) -> bool:
    conn = get_db_connection()
    cur = conn.execute("DELETE FROM mining_queue WHERE id = ?", (card_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def list_mined_cards(
    only_unsynced: bool = False, limit: int = 300
) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    if only_unsynced:
        rows = conn.execute(
            "SELECT * FROM mining_queue WHERE anki_synced = 0 ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM mining_queue ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [_format_card_row(r) for r in rows]


def _format_card_row(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["book_id"] = d.get("book_uuid", "")
    d["chapter_title"] = d.get("section_title", "")
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
