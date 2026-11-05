import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import CALIBRE_LIBRARY_PATH, UPLOADS_DIR
from app.database import get_all_reading_progress, get_cached_books_map
from app.epub_parser import extract_epub_metadata_and_spine

FORMAT_PRIORITY = {"EPUB": 1, "PDF": 2}


def _connect_calibre_ro(db_path: Path) -> sqlite3.Connection:
    """Opens Calibre metadata.db strictly read-only with retry on SQLITE_BUSY."""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    last_err: Optional[Exception] = None
    for attempt in range(5):
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.OperationalError as err:
            last_err = err
            time.sleep(0.15 * (attempt + 1))
    raise RuntimeError(f"Could not read Calibre metadata.db: {last_err}")


def list_all_books(search_query: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    """
    Indexes books exclusively from Calibre's metadata.db (never walks the filesystem).
    - Keyed by books.uuid.
    - Prefers EPUB over PDF when multiple formats exist for the same book.
    - Checks os.path.islink() via lstat (0 Drive bytes read) and reads data.uncompressed_size.
    """
    progress_map = get_all_reading_progress()
    cached_map = get_cached_books_map()
    books_by_uuid: Dict[str, Dict[str, Any]] = {}
    q_norm = search_query.strip().lower()

    calibre_db = CALIBRE_LIBRARY_PATH / "metadata.db"
    if calibre_db.exists():
        try:
            conn = _connect_calibre_ro(calibre_db)
            sql = """
                SELECT
                    b.id AS calibre_id,
                    b.uuid AS book_uuid,
                    b.title AS title,
                    b.path AS rel_path,
                    d.name AS file_name,
                    UPPER(d.format) AS fmt,
                    d.uncompressed_size AS uncompressed_size,
                    GROUP_CONCAT(a.name, ', ') AS author
                FROM books b
                INNER JOIN data d ON d.book = b.id
                LEFT JOIN books_authors_link bal ON b.id = bal.book
                LEFT JOIN authors a ON bal.author = a.id
                WHERE UPPER(d.format) IN ('EPUB', 'PDF')
                GROUP BY b.id, d.id
                ORDER BY b.timestamp DESC
            """
            rows = conn.execute(sql).fetchall()
            conn.close()

            for r in rows:
                uuid_val = r["book_uuid"] or f"cal-{r['calibre_id']}"
                title = r["title"] or r["file_name"]
                author = r["author"] or "Unknown"
                fmt = r["fmt"]

                if q_norm and q_norm not in title.lower() and q_norm not in author.lower():
                    continue

                # Composition rule from briefing Section 4:
                # file = LIBRARY_ROOT + "/" + books.path + "/" + data.name + "." + lower(data.format)
                full_path = os.path.join(
                    str(CALIBRE_LIBRARY_PATH),
                    r["rel_path"],
                    f"{r['file_name']}.{fmt.lower()}",
                )
                # os.path.islink uses lstat() on the symlink itself without dereferencing or downloading from Drive
                is_remote_symlink = os.path.islink(full_path)
                size_bytes = int(r["uncompressed_size"] or 0)

                existing = books_by_uuid.get(uuid_val)
                if existing:
                    existing["available_formats"].append(fmt)
                    # Prefer EPUB over PDF
                    if FORMAT_PRIORITY.get(fmt, 99) < FORMAT_PRIORITY.get(
                        existing["preferred_format"], 99
                    ):
                        existing["preferred_format"] = fmt
                        existing["file_path"] = full_path
                        existing["uncompressed_size"] = size_bytes
                        existing["is_symlink"] = is_remote_symlink
                    continue

                cache_info = cached_map.get(uuid_val)
                books_by_uuid[uuid_val] = {
                    "book_id": uuid_val,
                    "book_uuid": uuid_val,
                    "calibre_id": r["calibre_id"],
                    "title": title,
                    "author": author,
                    "preferred_format": fmt,
                    "available_formats": [fmt],
                    "file_path": full_path,
                    "uncompressed_size": size_bytes,
                    "is_symlink": is_remote_symlink,
                    "is_extracted": cache_info is not None,
                    "total_sentences": cache_info["total_sentences"] if cache_info else 0,
                    "source": "Calibre (Google Drive Symlink)"
                    if is_remote_symlink
                    else "Calibre (Local)",
                    "progress": progress_map.get(uuid_val),
                }
        except Exception:
            pass

    # Also include books already in permanent extraction cache or local UPLOADS_DIR
    if UPLOADS_DIR.exists():
        for fpath in sorted(UPLOADS_DIR.iterdir()):
            if fpath.suffix.lower() not in (".epub", ".pdf"):
                continue
            uuid_val = "local-" + hashlib.sha1(fpath.name.encode("utf-8")).hexdigest()[:12]
            if uuid_val in books_by_uuid:
                continue
            fmt = fpath.suffix.lstrip(".").upper()
            cache_info = cached_map.get(uuid_val)
            title = cache_info["title"] if cache_info else fpath.stem
            author = cache_info["author"] if cache_info else "Local Upload"

            if fpath.suffix.lower() == ".epub" and not cache_info:
                try:
                    meta = extract_epub_metadata_and_spine(fpath)
                    title = meta.get("title") or title
                    author = meta.get("author") or author
                except Exception:
                    pass

            if q_norm and q_norm not in title.lower() and q_norm not in author.lower():
                continue

            books_by_uuid[uuid_val] = {
                "book_id": uuid_val,
                "book_uuid": uuid_val,
                "calibre_id": 0,
                "title": title,
                "author": author,
                "preferred_format": fmt,
                "available_formats": [fmt],
                "file_path": str(fpath.resolve()),
                "uncompressed_size": fpath.stat().st_size,
                "is_symlink": False,
                "is_extracted": cache_info is not None,
                "total_sentences": cache_info["total_sentences"] if cache_info else 0,
                "source": "Local Upload",
                "progress": progress_map.get(uuid_val),
            }

    books = list(books_by_uuid.values())[:limit]
    books.sort(
        key=lambda b: (
            0 if b.get("progress") else 1,
            0 if b.get("is_extracted") else 1,
            b["title"],
        )
    )
    return books


def get_book_descriptor(book_uuid: str) -> Optional[Dict[str, Any]]:
    for b in list_all_books(limit=2000):
        if b["book_uuid"] == book_uuid:
            return b
    return None
