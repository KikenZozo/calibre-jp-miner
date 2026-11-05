"""
Background extraction jobs with live progress.

A full book extraction (open the file, segment every sentence with SudachiPy, cache
it in mining.db) can take minutes for a large book read over a Google Drive mount.
Doing that inside the HTTP request made the reader look frozen and could outlive the
reverse proxy's timeout, so extraction runs in a worker thread and the UI polls the
progress reported here.
"""

import threading
import time
import traceback
from typing import Any, Dict, Optional

_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"


def _now() -> float:
    return time.time()


def get_job(book_uuid: str) -> Optional[Dict[str, Any]]:
    with _JOBS_LOCK:
        job = _JOBS.get(book_uuid)
        return dict(job) if job else None


def is_running(book_uuid: str) -> bool:
    job = get_job(book_uuid)
    return bool(job and job["status"] == STATUS_RUNNING)


def _update(book_uuid: str, **fields: Any) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(book_uuid)
        if job is None:
            return
        job.update(fields)
        job["updated_at"] = _now()


def make_progress_callback(book_uuid: str):
    """Returns a callback the extractor calls as it walks the book."""

    def _report(
        phase: str,
        done: int = 0,
        total: int = 0,
        detail: str = "",
    ) -> None:
        _update(
            book_uuid,
            phase=phase,
            done=int(done),
            total=int(total),
            detail=detail,
            percent=round(100.0 * done / total, 1) if total else 0.0,
        )

    return _report


def _start_job(book: Dict[str, Any], kind: str, run) -> Dict[str, Any]:
    """
    Starts (or re-joins) a background job for one book. One job per book at a time.
    `run(progress)` does the work and may report progress through the callback.
    """
    book_uuid = book["book_uuid"]

    with _JOBS_LOCK:
        existing = _JOBS.get(book_uuid)
        if existing and existing["status"] == STATUS_RUNNING:
            return dict(existing)

        _JOBS[book_uuid] = {
            "book_uuid": book_uuid,
            "book_title": book.get("title", ""),
            "kind": kind,
            "status": STATUS_RUNNING,
            "phase": "queued",
            "detail": "",
            "done": 0,
            "total": 0,
            "percent": 0.0,
            "error": "",
            "started_at": _now(),
            "updated_at": _now(),
        }

    def _worker() -> None:
        try:
            run(make_progress_callback(book_uuid))
            _update(book_uuid, status=STATUS_DONE, phase="done", percent=100.0, detail="")
        except Exception as exc:
            traceback.print_exc()
            _update(
                book_uuid,
                status=STATUS_ERROR,
                phase="error",
                error=f"{type(exc).__name__}: {exc}",
            )

    threading.Thread(
        target=_worker, name=f"{kind}-{book_uuid[:12]}", daemon=True
    ).start()
    return get_job(book_uuid) or {}


def start_extraction(book: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts and caches a whole book in the background."""

    def _run(progress) -> None:
        # Imported lazily to avoid a circular import at module load time.
        from app.epub_parser import extract_book_into_cache

        extract_book_into_cache(book, progress=progress)

    return _start_job(book, "extract", _run)


def start_pdf_ocr(book: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    """OCRs (or re-reads) every page of a PDF so batch i+1 mining sees the whole book."""

    def _run(progress) -> None:
        from pathlib import Path

        from app.pdf_layout import analyze_whole_pdf

        analyze_whole_pdf(
            pdf_path=Path(book["file_path"]),
            book_uuid=book["book_uuid"],
            book_title=book.get("title", ""),
            force_ocr=force,
            progress=progress,
        )

    return _start_job(book, "pdf-ocr", _run)


def clear_job(book_uuid: str) -> None:
    with _JOBS_LOCK:
        _JOBS.pop(book_uuid, None)
