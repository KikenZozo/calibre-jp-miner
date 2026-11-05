import hashlib
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import CALIBRE_LIBRARY_PATH, UPLOADS_DIR
from app.database import get_all_reading_progress
from app.epub_parser import extract_epub_metadata_and_spine


def list_all_books() -> List[Dict[str, Any]]:
    """
    Liste tous les livres disponibles :
    1. Depuis la base Calibre (`metadata.db`) si présente dans CALIBRE_LIBRARY_PATH.
    2. Depuis tous les fichiers `.epub` présents dans CALIBRE_LIBRARY_PATH ou UPLOADS_DIR.
    Associe automatiquement la progression de lecture sauvegardée.
    """
    progress_map = get_all_reading_progress()
    books_by_path: Dict[str, Dict[str, Any]] = {}

    # 1. Lecture native de metadata.db (Calibre)
    calibre_db = CALIBRE_LIBRARY_PATH / "metadata.db"
    if calibre_db.exists():
        try:
            conn = sqlite3.connect(f"file:{calibre_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT
                    b.id AS calibre_id,
                    b.title AS title,
                    b.path AS rel_path,
                    b.has_cover AS has_cover,
                    GROUP_CONCAT(a.name, ', ') AS author
                FROM books b
                LEFT JOIN books_authors_link bal ON b.id = bal.book
                LEFT JOIN authors a ON bal.author = a.id
                GROUP BY b.id
                ORDER BY b.timestamp DESC
                """
            ).fetchall()
            conn.close()

            for r in rows:
                book_dir = CALIBRE_LIBRARY_PATH / r["rel_path"]
                if not book_dir.exists():
                    continue
                epubs = list(book_dir.glob("*.epub"))
                if not epubs:
                    continue
                epub_file = epubs[0].resolve()
                book_id = _make_book_id(epub_file)
                cover_file = book_dir / "cover.jpg"
                books_by_path[str(epub_file)] = {
                    "book_id": book_id,
                    "title": r["title"] or epub_file.stem,
                    "author": r["author"] or "Auteur inconnu",
                    "epub_path": str(epub_file),
                    "has_cover": cover_file.exists(),
                    "cover_path": str(cover_file) if cover_file.exists() else None,
                    "source": "Calibre",
                    "progress": progress_map.get(book_id),
                }
        except Exception:
            pass

    # 2. Scan récursif des fichiers .epub dans CALIBRE_LIBRARY_PATH et UPLOADS_DIR
    for root_dir, source_label in (
        (CALIBRE_LIBRARY_PATH, "Calibre"),
        (UPLOADS_DIR, "Import direct"),
    ):
        if not root_dir.exists():
            continue
        for epub_file in root_dir.rglob("*.epub"):
            resolved = str(epub_file.resolve())
            if resolved in books_by_path:
                continue
            try:
                meta = extract_epub_metadata_and_spine(epub_file)
                book_id = _make_book_id(epub_file.resolve())
                cover_candidate = epub_file.parent / "cover.jpg"
                books_by_path[resolved] = {
                    "book_id": book_id,
                    "title": meta.get("title") or epub_file.stem,
                    "author": meta.get("author") or "Auteur inconnu",
                    "epub_path": resolved,
                    "has_cover": cover_candidate.exists(),
                    "cover_path": str(cover_candidate) if cover_candidate.exists() else None,
                    "source": source_label,
                    "progress": progress_map.get(book_id),
                }
            except Exception:
                continue

    books = list(books_by_path.values())
    # Trier : livres en cours de lecture en premier, puis par titre
    books.sort(
        key=lambda b: (
            0 if b.get("progress") else 1,
            -(b["progress"]["chapter_index"] if b.get("progress") else 0),
            b["title"],
        )
    )
    return books


def get_book_by_id(book_id: str) -> Optional[Dict[str, Any]]:
    for b in list_all_books():
        if b["book_id"] == book_id:
            return b
    return None


def _make_book_id(epub_path: Path) -> str:
    return hashlib.sha1(str(epub_path).encode("utf-8")).hexdigest()[:12]
