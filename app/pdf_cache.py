"""
Local disk cache for Drive-backed PDFs.

The classic PDF view serves the original file to pdf.js, which reads it with HTTP
range requests. Random ranges against an rclone Google Drive mount are the worst
possible access pattern: every seek can cost a round trip and churn the VFS cache.
So the first time a symlinked book is opened, it is copied to DATA_DIR once,
sequentially (which rclone handles well), in the background. Later opens — and every
range request pdf.js makes — are then served from local disk.

Books that already live on local disk are served in place and never copied.
"""

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import DATA_DIR

PDF_CACHE_DIR = DATA_DIR / "pdfcache"

# Opt out entirely with PDF_LOCAL_CACHE=false.
PDF_LOCAL_CACHE_ENABLED = os.getenv("PDF_LOCAL_CACHE", "true").lower() == "true"

# Never copy a single file larger than this (MB), and keep the whole cache under
# the total budget by evicting the least recently used files.
PDF_CACHE_MAX_FILE_MB = int(os.getenv("PDF_CACHE_MAX_FILE_MB", "400"))
PDF_CACHE_MAX_TOTAL_MB = int(os.getenv("PDF_CACHE_MAX_TOTAL_MB", "4096"))

_COPY_LOCK = threading.Lock()
_COPIES: Dict[str, Dict[str, Any]] = {}


def _cache_path(book_uuid: str) -> Path:
    safe = "".join(c for c in book_uuid if c.isalnum() or c in "-_")[:64]
    return PDF_CACHE_DIR / f"{safe}.pdf"


def get_copy_state(book_uuid: str) -> Optional[Dict[str, Any]]:
    with _COPY_LOCK:
        state = _COPIES.get(book_uuid)
        return dict(state) if state else None


def cached_file_if_ready(book_uuid: str) -> Optional[Path]:
    """Returns the local copy only when it is complete."""
    path = _cache_path(book_uuid)
    if not path.exists():
        return None
    state = get_copy_state(book_uuid)
    if state and state.get("status") == "copying":
        return None
    return path


def _evict_if_needed(incoming_bytes: int) -> None:
    """Simple LRU eviction so the cache cannot grow without bound."""
    budget = PDF_CACHE_MAX_TOTAL_MB * 1024 * 1024
    try:
        files = [
            (p, p.stat().st_size, p.stat().st_atime)
            for p in PDF_CACHE_DIR.glob("*.pdf")
            if p.is_file()
        ]
    except OSError:
        return

    total = sum(size for _, size, _ in files) + incoming_bytes
    if total <= budget:
        return

    for path, size, _ in sorted(files, key=lambda f: f[2]):
        try:
            path.unlink()
            total -= size
            print(f"[PDF cache] evicted {path.name} ({size // 1024 // 1024} MB)")
        except OSError:
            pass
        if total <= budget:
            return


def should_cache(book: Dict[str, Any]) -> bool:
    if not PDF_LOCAL_CACHE_ENABLED:
        return False
    if (book.get("preferred_format") or "").upper() != "PDF":
        return False
    # Only remote (Drive symlink) books benefit; local files are already fast.
    return bool(book.get("is_symlink"))


def ensure_cached_async(book: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Starts a one-off background copy of a Drive-backed PDF onto local disk.
    Returns the copy state, or None when caching does not apply.
    """
    if not should_cache(book):
        return None

    book_uuid = book["book_uuid"]
    dest = _cache_path(book_uuid)
    source = Path(book["file_path"])

    if dest.exists():
        existing = get_copy_state(book_uuid)
        if not existing or existing.get("status") != "copying":
            return {"status": "ready", "path": str(dest)}

    with _COPY_LOCK:
        state = _COPIES.get(book_uuid)
        if state and state["status"] == "copying":
            return dict(state)
        _COPIES[book_uuid] = {
            "status": "copying",
            "copied": 0,
            "total": 0,
            "started_at": time.time(),
            "error": "",
        }

    def _worker() -> None:
        tmp = dest.with_suffix(".part")
        try:
            PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            total = source.stat().st_size

            if total > PDF_CACHE_MAX_FILE_MB * 1024 * 1024:
                with _COPY_LOCK:
                    _COPIES[book_uuid] = {
                        "status": "skipped",
                        "copied": 0,
                        "total": total,
                        "error": f"larger than PDF_CACHE_MAX_FILE_MB ({PDF_CACHE_MAX_FILE_MB} MB)",
                    }
                return

            _evict_if_needed(total)
            with _COPY_LOCK:
                _COPIES[book_uuid]["total"] = total

            copied = 0
            # One long sequential read: the access pattern rclone is happiest with.
            with source.open("rb") as src, tmp.open("wb") as out:
                while True:
                    chunk = src.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    copied += len(chunk)
                    with _COPY_LOCK:
                        _COPIES[book_uuid]["copied"] = copied

            tmp.replace(dest)
            with _COPY_LOCK:
                _COPIES[book_uuid] = {
                    "status": "ready",
                    "copied": copied,
                    "total": copied,
                    "error": "",
                }
            print(f"[PDF cache] cached {source.name} ({copied // 1024 // 1024} MB)")
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            with _COPY_LOCK:
                _COPIES[book_uuid] = {
                    "status": "error",
                    "copied": 0,
                    "total": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            print(f"[PDF cache] copy failed for {book_uuid}: {exc}")

    threading.Thread(
        target=_worker, name=f"pdfcache-{book_uuid[:12]}", daemon=True
    ).start()
    return get_copy_state(book_uuid)


def resolve_readable_path(book: Dict[str, Any]) -> Path:
    """
    The path everything should read a PDF from: the local copy when it is ready,
    otherwise the original (and a background copy is kicked off for next time).
    """
    book_uuid = book["book_uuid"]
    cached = cached_file_if_ready(book_uuid)
    if cached is not None:
        try:
            os.utime(cached, None)  # keep LRU ordering meaningful
        except OSError:
            pass
        return cached

    ensure_cached_async(book)
    return Path(book["file_path"])


def clear_cached_file(book_uuid: str) -> bool:
    path = _cache_path(book_uuid)
    with _COPY_LOCK:
        _COPIES.pop(book_uuid, None)
    try:
        path.unlink()
        return True
    except OSError:
        return False
