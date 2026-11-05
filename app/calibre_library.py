import hashlib
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import CALIBRE_LIBRARY_PATH, DATA_DIR, UPLOADS_DIR
from app.database import get_all_reading_progress
from app.epub_parser import extract_epub_metadata_and_spine

# Cache local uniquement pour le livre en cours de lecture (évite de solliciter le VFS rclone/Google Drive)
ACTIVE_BOOK_CACHE_DIR = DATA_DIR / "active_book_cache"
ACTIVE_BOOK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
MAX_CACHED_ACTIVE_BOOKS = 3


def list_all_books(search_query: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    """
    Liste les livres SANS JAMAIS parcourir les dossiers de la bibliothèque Calibre
    ni ouvrir les fichiers .epub (protège le cache VFS rclone / Google Drive de 5 Go).

    Fonctionnement :
    1. Lit uniquement les métadonnées SQL de `metadata.db` (table `books` + table `data`
       qui contient déjà le nom du fichier EPUB sans faire d'appel disque VFS).
    2. Affiche en priorité le livre actuellement en cours de lecture et les fichiers
       importés localement dans `UPLOADS_DIR`.
    """
    progress_map = get_all_reading_progress()
    books_list: List[Dict[str, Any]] = []
    seen_ids = set()

    q_norm = search_query.strip().lower()

    # 1. Lecture 100 % SQL dans metadata.db (ZÉRO parcours de dossier / ZÉRO ouverture d'EPUB sur le Drive)
    calibre_db = CALIBRE_LIBRARY_PATH / "metadata.db"
    if calibre_db.exists():
        try:
            conn = sqlite3.connect(f"file:{calibre_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row

            sql = """
                SELECT
                    b.id AS calibre_id,
                    b.title AS title,
                    b.path AS rel_path,
                    d.name AS file_stem,
                    GROUP_CONCAT(a.name, ', ') AS author
                FROM books b
                INNER JOIN data d ON b.id = d.book AND UPPER(d.format) = 'EPUB'
                LEFT JOIN books_authors_link bal ON b.id = bal.book
                LEFT JOIN authors a ON bal.author = a.id
            """
            params: List[Any] = []
            if q_norm:
                sql += " WHERE LOWER(b.title) LIKE ? OR LOWER(a.name) LIKE ? "
                params.extend([f"%{q_norm}%", f"%{q_norm}%"])

            sql += " GROUP BY b.id ORDER BY b.timestamp DESC LIMIT ? "
            params.append(limit)

            rows = conn.execute(sql, tuple(params)).fetchall()
            conn.close()

            for r in rows:
                rel_epub = f"{r['rel_path']}/{r['file_stem']}.epub"
                book_id = f"cal_{r['calibre_id']}"
                seen_ids.add(book_id)
                books_list.append(
                    {
                        "book_id": book_id,
                        "calibre_id": r["calibre_id"],
                        "title": r["title"] or r["file_stem"],
                        "author": r["author"] or "Auteur inconnu",
                        "rel_epub_path": rel_epub,
                        "source": "Calibre (Live à la demande)",
                        "is_cached_locally": (ACTIVE_BOOK_CACHE_DIR / f"{book_id}.epub").exists(),
                        "progress": progress_map.get(book_id),
                    }
                )
        except Exception:
            pass

    # 2. Livres importés directement dans le dossier local UPLOADS_DIR (hors Google Drive)
    if UPLOADS_DIR.exists():
        for epub_file in UPLOADS_DIR.glob("*.epub"):
            book_id = "loc_" + hashlib.sha1(epub_file.name.encode("utf-8")).hexdigest()[:10]
            if book_id in seen_ids:
                continue
            try:
                meta = extract_epub_metadata_and_spine(epub_file)
                title = meta.get("title") or epub_file.stem
                author = meta.get("author") or "Auteur inconnu"
            except Exception:
                title = epub_file.stem
                author = "Import direct"

            if q_norm and q_norm not in title.lower() and q_norm not in author.lower():
                continue

            books_list.append(
                {
                    "book_id": book_id,
                    "title": title,
                    "author": author,
                    "local_epub_path": str(epub_file.resolve()),
                    "source": "Livre local",
                    "is_cached_locally": True,
                    "progress": progress_map.get(book_id),
                }
            )

    # Trier : livre en cours de lecture tout en haut
    books_list.sort(
        key=lambda b: (
            0 if b.get("progress") else 1,
            0 if b.get("is_cached_locally") else 1,
            b["title"],
        )
    )
    return books_list


def resolve_active_book_epub(book_id: str) -> Optional[Dict[str, Any]]:
    """
    Ne charge que LE livre demandé par l'utilisateur (Live) :
    - Si c'est un livre Calibre (`cal_<id>`), récupère uniquement son chemin dans `metadata.db`
      et met en cache ce seul fichier .epub dans `/data/active_book_cache/` pour ne pas
      solliciter le VFS Google Drive à chaque changement de chapitre.
    - Nettoie automatiquement le cache local pour ne garder au maximum que les 3 derniers livres lus.
    """
    progress_map = get_all_reading_progress()

    # Cas A : Livre Calibre (identifiant cal_<id>)
    if book_id.startswith("cal_"):
        calibre_id_str = book_id.replace("cal_", "", 1)
        if not calibre_id_str.isdigit():
            return None
        calibre_db = CALIBRE_LIBRARY_PATH / "metadata.db"
        if not calibre_db.exists():
            return None

        conn = sqlite3.connect(f"file:{calibre_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                b.id AS calibre_id,
                b.title AS title,
                b.path AS rel_path,
                d.name AS file_stem,
                GROUP_CONCAT(a.name, ', ') AS author
            FROM books b
            INNER JOIN data d ON b.id = d.book AND UPPER(d.format) = 'EPUB'
            LEFT JOIN books_authors_link bal ON b.id = bal.book
            LEFT JOIN authors a ON bal.author = a.id
            WHERE b.id = ?
            GROUP BY b.id
            """,
            (int(calibre_id_str),),
        ).fetchone()
        conn.close()

        if not row:
            return None

        cached_epub = ACTIVE_BOOK_CACHE_DIR / f"{book_id}.epub"
        if not cached_epub.exists():
            # On ne touche que CE fichier unique sur le montage Google Drive / rclone VFS !
            source_epub = CALIBRE_LIBRARY_PATH / row["rel_path"] / f"{row['file_stem']}.epub"
            if not source_epub.exists():
                # Fallback uniquement dans le sous-dossier de ce livre précis si le nom diffère
                single_book_dir = CALIBRE_LIBRARY_PATH / row["rel_path"]
                epubs_in_dir = list(single_book_dir.glob("*.epub")) if single_book_dir.exists() else []
                if not epubs_in_dir:
                    return None
                source_epub = epubs_in_dir[0]

            _evict_old_cached_books()
            shutil.copy2(str(source_epub), str(cached_epub))
        else:
            # Met à jour le timestamp d'accès
            cached_epub.touch()

        return {
            "book_id": book_id,
            "title": row["title"] or row["file_stem"],
            "author": row["author"] or "Auteur inconnu",
            "epub_path": str(cached_epub),
            "source": "Calibre (Live)",
            "progress": progress_map.get(book_id),
        }

    # Cas B : Livre importé directement dans UPLOADS_DIR
    for b in list_all_books(limit=500):
        if b["book_id"] == book_id and b.get("local_epub_path"):
            return {
                "book_id": book_id,
                "title": b["title"],
                "author": b["author"],
                "epub_path": b["local_epub_path"],
                "source": "Livre local",
                "progress": b.get("progress"),
            }

    return None


def _evict_old_cached_books() -> None:
    """Ne conserve au maximum que MAX_CACHED_ACTIVE_BOOKS (3 livres, ~5-15 Mo) dans le cache local."""
    cached_files = sorted(
        ACTIVE_BOOK_CACHE_DIR.glob("*.epub"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old_file in cached_files[MAX_CACHED_ACTIVE_BOOKS - 1 :]:
        try:
            old_file.unlink()
        except Exception:
            pass
