import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import CALIBRE_LIBRARY_PATH, UPLOADS_DIR
from app.database import get_all_reading_progress, get_cached_books_map
from app.epub_parser import extract_epub_metadata_and_spine

FORMAT_PRIORITY = {"EPUB": 1, "PDF": 2}

SUPPORTED_SUFFIXES = (".epub", ".pdf")

# Last error raised while reading Calibre's metadata.db, surfaced to the UI so an
# unreadable library never silently looks like an empty one.
_LAST_LIBRARY_ERROR: Optional[str] = None


def get_last_library_error() -> Optional[str]:
    return _LAST_LIBRARY_ERROR


def local_book_id_for(file_name: str) -> str:
    """Stable id for a manually uploaded file (independent of Calibre)."""
    return "local-" + hashlib.sha1(file_name.encode("utf-8")).hexdigest()[:12]


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


CALIBRE_SQL = """
    SELECT
        b.id AS calibre_id,
        b.uuid AS book_uuid,
        b.title AS title,
        b.path AS rel_path,
        b.timestamp AS added_at,
        d.name AS file_name,
        UPPER(d.format) AS fmt,
        d.uncompressed_size AS uncompressed_size,
        GROUP_CONCAT(a.name, ', ') AS author
    FROM books b
    INNER JOIN data d ON d.book = b.id
    LEFT JOIN books_authors_link bal ON b.id = bal.book
    LEFT JOIN authors a ON bal.author = a.id
    WHERE UPPER(d.format) IN ('EPUB', 'PDF')
    {extra_where}
    GROUP BY b.id, d.id
    ORDER BY b.timestamp DESC
"""


def _calibre_rows(where_uuid: Optional[str] = None) -> List[sqlite3.Row]:
    """Reads candidate rows from metadata.db, recording any failure for the UI."""
    global _LAST_LIBRARY_ERROR

    calibre_db = CALIBRE_LIBRARY_PATH / "metadata.db"
    if not calibre_db.exists():
        _LAST_LIBRARY_ERROR = None
        return []

    conn = None
    try:
        conn = _connect_calibre_ro(calibre_db)
        if where_uuid:
            sql = CALIBRE_SQL.format(extra_where="AND b.uuid = ?")
            rows = conn.execute(sql, (where_uuid,)).fetchall()
        else:
            rows = conn.execute(CALIBRE_SQL.format(extra_where="")).fetchall()
        _LAST_LIBRARY_ERROR = None
        return rows
    except Exception as exc:
        _LAST_LIBRARY_ERROR = f"{type(exc).__name__}: {exc}"
        print(f"[Calibre Library] metadata.db read failed: {exc}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _row_to_book(
    r: sqlite3.Row,
    progress_map: Dict[str, Dict[str, Any]],
    cached_map: Dict[str, Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    uuid_val = r["book_uuid"] or f"cal-{r['calibre_id']}"
    title = r["title"] or r["file_name"]
    author = r["author"] or "Unknown"
    fmt = r["fmt"]

    # Composition rule: LIBRARY_ROOT + "/" + books.path + "/" + data.name + "." + lower(format)
    full_path = os.path.join(
        str(CALIBRE_LIBRARY_PATH),
        r["rel_path"] or "",
        f"{r['file_name']}.{fmt.lower()}",
    )
    # os.path.islink uses lstat() on the symlink itself: no dereference, 0 Drive bytes read.
    is_remote_symlink = os.path.islink(full_path)
    cache_info = cached_map.get(uuid_val)

    return uuid_val, {
        "book_id": uuid_val,
        "book_uuid": uuid_val,
        "calibre_id": r["calibre_id"],
        "title": title,
        "author": author,
        "preferred_format": fmt,
        "available_formats": [fmt],
        "file_path": full_path,
        "uncompressed_size": int(r["uncompressed_size"] or 0),
        "is_symlink": is_remote_symlink,
        "is_extracted": cache_info is not None,
        "total_sentences": cache_info["total_sentences"] if cache_info else 0,
        "added_at": str(r["added_at"] or ""),
        "source": "Calibre (Google Drive Symlink)"
        if is_remote_symlink
        else "Calibre (Local)",
        "progress": progress_map.get(uuid_val),
    }


def _upload_to_book(
    fpath: Path,
    progress_map: Dict[str, Dict[str, Any]],
    cached_map: Dict[str, Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    uuid_val = local_book_id_for(fpath.name)
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

    try:
        stat = fpath.stat()
        size_bytes, mtime = stat.st_size, stat.st_mtime
    except OSError:
        size_bytes, mtime = 0, 0.0

    return uuid_val, {
        "book_id": uuid_val,
        "book_uuid": uuid_val,
        "calibre_id": 0,
        "title": title,
        "author": author,
        "preferred_format": fmt,
        "available_formats": [fmt],
        "file_path": str(fpath.resolve()),
        "uncompressed_size": size_bytes,
        "is_symlink": False,
        "is_extracted": cache_info is not None,
        "total_sentences": cache_info["total_sentences"] if cache_info else 0,
        "added_at": str(mtime),
        "is_upload": True,
        "upload_mtime": mtime,
        "source": "Local Upload",
        "progress": progress_map.get(uuid_val),
    }


def _matches_query(book: Dict[str, Any], q_norm: str) -> bool:
    if not q_norm:
        return True
    return (
        q_norm in (book.get("title") or "").lower()
        or q_norm in (book.get("author") or "").lower()
    )


def list_all_books(search_query: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    """
    Indexes books from Calibre's metadata.db (never walks the library filesystem)
    plus anything manually uploaded into DATA_DIR/uploads.

    Ordering is resolved BEFORE the limit is applied, so a freshly uploaded book is
    never pushed out of the response by a large Calibre library.
    """
    progress_map = get_all_reading_progress()
    cached_map = get_cached_books_map()
    books_by_uuid: Dict[str, Dict[str, Any]] = {}
    q_norm = search_query.strip().lower()

    for r in _calibre_rows():
        try:
            uuid_val, book = _row_to_book(r, progress_map, cached_map)
        except Exception:
            continue

        existing = books_by_uuid.get(uuid_val)
        if existing:
            fmt = book["preferred_format"]
            if fmt not in existing["available_formats"]:
                existing["available_formats"].append(fmt)
            # Prefer EPUB over PDF when a book has both formats
            if FORMAT_PRIORITY.get(fmt, 99) < FORMAT_PRIORITY.get(
                existing["preferred_format"], 99
            ):
                existing.update(
                    {
                        "preferred_format": fmt,
                        "file_path": book["file_path"],
                        "uncompressed_size": book["uncompressed_size"],
                        "is_symlink": book["is_symlink"],
                        "source": book["source"],
                    }
                )
            continue

        books_by_uuid[uuid_val] = book

    # Manually uploaded books live in the writable DATA_DIR, never in the Calibre library
    if UPLOADS_DIR.exists():
        try:
            upload_files = sorted(UPLOADS_DIR.iterdir())
        except OSError:
            upload_files = []
        for fpath in upload_files:
            if fpath.suffix.lower() not in SUPPORTED_SUFFIXES or not fpath.is_file():
                continue
            uuid_val, book = _upload_to_book(fpath, progress_map, cached_map)
            if uuid_val in books_by_uuid:
                continue
            books_by_uuid[uuid_val] = book

    books = [b for b in books_by_uuid.values() if _matches_query(b, q_norm)]

    # Sort FIRST, truncate after: uploads and in-progress books always stay visible.
    books.sort(
        key=lambda b: (
            0 if b.get("progress") else 1,
            0 if b.get("is_upload") else 1,
            -(b.get("upload_mtime") or 0.0),
            0 if b.get("is_extracted") else 1,
            b.get("title") or "",
        )
    )
    return books[: max(1, limit)]


def get_book_descriptor(book_uuid: str) -> Optional[Dict[str, Any]]:
    """
    Resolves a single book without re-listing (and re-parsing) the whole library.
    """
    progress_map = get_all_reading_progress()
    cached_map = get_cached_books_map()

    if book_uuid.startswith("local-"):
        if UPLOADS_DIR.exists():
            for fpath in sorted(UPLOADS_DIR.iterdir()):
                if fpath.suffix.lower() not in SUPPORTED_SUFFIXES:
                    continue
                if local_book_id_for(fpath.name) == book_uuid:
                    return _upload_to_book(fpath, progress_map, cached_map)[1]
        return None

    best: Optional[Dict[str, Any]] = None
    for r in _calibre_rows(where_uuid=book_uuid):
        try:
            _, book = _row_to_book(r, progress_map, cached_map)
        except Exception:
            continue
        if best is None:
            best = book
        else:
            if book["preferred_format"] not in best["available_formats"]:
                best["available_formats"].append(book["preferred_format"])
            if FORMAT_PRIORITY.get(book["preferred_format"], 99) < FORMAT_PRIORITY.get(
                best["preferred_format"], 99
            ):
                book["available_formats"] = best["available_formats"]
                best = book

    if best is not None:
        return best

    # Fallback for cal-<id> ids and for books that vanished from metadata.db but
    # are still readable from the permanent extraction cache.
    for b in list_all_books(limit=5000):
        if b["book_uuid"] == book_uuid:
            return b
    return None
