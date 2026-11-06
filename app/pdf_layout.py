"""
Per-page word geometry for the classic PDF view.

The original PDF is rendered untouched by pdf.js in the browser; this module
supplies the invisible, clickable word layer drawn on top of it — the same idea
as Yomitan's text scanning, except the segmentation is pre-computed server-side
by SudachiPy so a click lands on a whole word (and its whole sentence) rather
than on a single character.

Two sources feed exactly the same pipeline:
  * the PDF's embedded text layer (PyMuPDF character boxes), and
  * Tesseract OCR for image-only scans (hOCR character boxes).
"""

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app import ocr as ocr_module
from app.database import (
    get_cached_pdf_info,
    get_cached_pdf_page_layout,
    store_pdf_info,
    store_pdf_page_layout,
    store_section_sentences,
)
from app.japanese_nlp import nlp_engine, split_japanese_sentences

try:
    import pymupdf  # PyMuPDF >= 1.24 exposes the `pymupdf` name

    _PYMUPDF = pymupdf
except ImportError:  # pragma: no cover - older PyMuPDF releases
    try:
        import fitz as _PYMUPDF  # type: ignore
    except ImportError:
        _PYMUPDF = None  # type: ignore

# Serialises page rendering/OCR so a Drive-backed file is never read concurrently.
_PDF_LOCK = threading.Lock()

# A page with fewer real characters than this is treated as an image-only scan.
MIN_TEXT_LAYER_CHARS = 8

# Enough characters that the page is certainly real text, whatever else is on it.
DENSE_TEXT_CHARS = 120

# A single image covering at least this share of the page means the page IS that
# image, and whatever few characters sit on top of it are not the content.
FULL_PAGE_IMAGE_RATIO = 0.6

# How far the aligner may scan ahead to re-sync a token with the character stream.
_ALIGN_LOOKAHEAD = 24


def pymupdf_available() -> bool:
    return _PYMUPDF is not None


def _require_pymupdf():
    if _PYMUPDF is None:
        raise RuntimeError("PyMuPDF is required for PDF support but is not installed.")
    return _PYMUPDF


# --- Character sources -------------------------------------------------------


def _text_layer_lines(page: Any) -> List[Dict[str, Any]]:
    """Character boxes straight from the PDF text layer, grouped by line."""
    lines: List[Dict[str, Any]] = []
    raw = page.get_text("rawdict")

    for block in raw.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        block_no = block.get("number", 0)
        for line in block.get("lines", []):
            chars: List[Dict[str, Any]] = []
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    c = ch.get("c", "")
                    if not c or not c.strip():
                        continue
                    chars.append({"c": c, "bbox": [float(v) for v in ch["bbox"]]})
            if chars:
                lines.append(
                    {
                        "block": block_no,
                        "bbox": [float(v) for v in line.get("bbox", (0, 0, 0, 0))],
                        "dir": tuple(line.get("dir", (1.0, 0.0))),
                        "chars": chars,
                    }
                )
    return lines


def _page_is_scan(page: Any, text_chars: int) -> bool:
    """
    Decides whether a page is really an image that needs OCR.

    Counting characters alone is not enough: plenty of scans carry a thin text
    layer (page numbers, a publisher line, or a bad OCR baked in by the scanner),
    which made the page look like a digital one and silently skipped OCR. A page
    is treated as a scan when a single image covers most of it and there is far
    too little text for that much surface.
    """
    if text_chars < MIN_TEXT_LAYER_CHARS:
        return True
    if text_chars >= DENSE_TEXT_CHARS:
        return False

    page_area = float(page.rect.width) * float(page.rect.height)
    if page_area <= 0:
        return False

    try:
        images = page.get_image_info()
    except Exception:
        return False

    for info in images:
        bbox = info.get("bbox")
        if not bbox:
            continue
        covered = abs(bbox[2] - bbox[0]) * abs(bbox[3] - bbox[1])
        if covered / page_area >= FULL_PAGE_IMAGE_RATIO:
            return True
    return False


def _ocr_lines(page: Any) -> Tuple[List[Dict[str, Any]], str, bool]:
    """
    Renders the page and OCRs it, returning character boxes converted from pixel
    coordinates back into PDF points so both sources share one coordinate space.
    """
    import tempfile

    fitz = _require_pymupdf()
    pix = page.get_pixmap(dpi=ocr_module.OCR_DPI, colorspace=fitz.csGRAY)

    with tempfile.TemporaryDirectory() as tmp:
        img_path = Path(tmp) / "page.png"
        pix.save(str(img_path))
        result = ocr_module.ocr_image_to_char_lines(img_path)

    page_rect = page.rect
    scale_x = page_rect.width / pix.width if pix.width else 1.0
    scale_y = page_rect.height / pix.height if pix.height else 1.0

    lines: List[Dict[str, Any]] = []
    for idx, line in enumerate(result["lines"]):
        chars = [
            {
                "c": ch["c"],
                "bbox": [
                    ch["bbox"][0] * scale_x,
                    ch["bbox"][1] * scale_y,
                    ch["bbox"][2] * scale_x,
                    ch["bbox"][3] * scale_y,
                ],
            }
            for ch in line["chars"]
            if ch["c"].strip()
        ]
        if chars:
            lines.append(
                {
                    "block": idx,
                    "bbox": [
                        line["bbox"][0] * scale_x,
                        line["bbox"][1] * scale_y,
                        line["bbox"][2] * scale_x,
                        line["bbox"][3] * scale_y,
                    ],
                    "dir": (0.0, 1.0) if result["vertical"] else (1.0, 0.0),
                    "chars": chars,
                }
            )

    return lines, result["lang"], result["vertical"]


# --- Grouping & alignment ----------------------------------------------------


def _group_lines_into_paragraphs(
    lines: List[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    """Consecutive lines of the same PDF block read as one paragraph."""
    paragraphs: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_block: Optional[int] = None

    for line in lines:
        if current and line.get("block") != current_block:
            paragraphs.append(current)
            current = []
        current.append(line)
        current_block = line.get("block")

    if current:
        paragraphs.append(current)
    return paragraphs


def _flatten_paragraph(
    paragraph_lines: List[Dict[str, Any]], line_offset: int
) -> Tuple[str, List[Dict[str, Any]]]:
    """Returns the paragraph text and a flat character stream tagged by line."""
    chars: List[Dict[str, Any]] = []
    for local_idx, line in enumerate(paragraph_lines):
        for ch in line["chars"]:
            chars.append(
                {"c": ch["c"], "bbox": ch["bbox"], "line": line_offset + local_idx}
            )
    return "".join(ch["c"] for ch in chars), chars


def _rects_for_chars(matched: List[Dict[str, Any]]) -> List[List[float]]:
    """Merges the boxes of matched characters into one rect per text line."""
    rects: List[List[float]] = []
    current: Optional[List[float]] = None
    current_line: Optional[int] = None

    for ch in matched:
        x0, y0, x1, y1 = ch["bbox"]
        if current is None or ch["line"] != current_line:
            if current is not None:
                rects.append(current)
            current = [x0, y0, x1, y1]
            current_line = ch["line"]
        else:
            current[0] = min(current[0], x0)
            current[1] = min(current[1], y0)
            current[2] = max(current[2], x1)
            current[3] = max(current[3], y1)

    if current is not None:
        rects.append(current)
    return rects


class _CharCursor:
    """
    Walks the raw character stream while consuming analyzer token surfaces.

    Sentence splitting and typography normalisation rewrite spacing, so tokens are
    matched character by character with a bounded re-sync window instead of by index.
    """

    def __init__(self, chars: List[Dict[str, Any]]) -> None:
        self.chars = chars
        self.pos = 0

    def consume(self, surface: str) -> List[Dict[str, Any]]:
        matched: List[Dict[str, Any]] = []
        for want in surface:
            if not want.strip():
                continue
            found = -1
            limit = min(len(self.chars), self.pos + _ALIGN_LOOKAHEAD)
            for i in range(self.pos, limit):
                if self.chars[i]["c"] == want:
                    found = i
                    break
            if found < 0:
                continue
            matched.append(self.chars[found])
            self.pos = found + 1
        return matched


# --- Public API --------------------------------------------------------------


def get_pdf_info(
    pdf_path: Path, sample_pages: int = 4, book_uuid: str = ""
) -> Dict[str, Any]:
    """
    Page count plus a cheap text-layer probe, without extracting anything.

    Sampling reads real pages, which costs real Drive reads, so the answer is cached
    per book: opening the same PDF again does not re-probe it.
    """
    if book_uuid:
        cached = get_cached_pdf_info(book_uuid)
        if cached is not None:
            return cached

    fitz = _require_pymupdf()
    with _PDF_LOCK:
        doc = fitz.open(str(pdf_path))
        try:
            page_count = len(doc)
            indices = sorted(
                {0, page_count // 2, max(0, page_count - 1)}
                | set(range(min(sample_pages, page_count)))
            )
            sampled_chars = 0
            text_pages = 0
            for idx in indices:
                if 0 <= idx < page_count:
                    n = len(doc[idx].get_text("text").strip())
                    sampled_chars += n
                    if n >= MIN_TEXT_LAYER_CHARS:
                        text_pages += 1
            first = doc[0] if page_count else None
            width = float(first.rect.width) if first else 0.0
            height = float(first.rect.height) if first else 0.0
        finally:
            doc.close()

    info = {
        "page_count": page_count,
        "has_text_layer": sampled_chars >= 20,
        "text_pages_sampled": text_pages,
        "pages_sampled": len(indices),
        "first_page_width": width,
        "first_page_height": height,
    }
    if book_uuid:
        store_pdf_info(book_uuid, info)
    return info


def build_page_layout(
    pdf_path: Path,
    page_number: int,
    book_uuid: str,
    allow_ocr: bool = True,
    force_ocr: bool = False,
    persist_sentences: bool = True,
    book_title: str = "",
) -> Dict[str, Any]:
    """
    Builds (or loads from cache) the clickable word layer for one PDF page.

    Coordinates are returned normalised to 0..1 of the page box so the frontend can
    scale them to whatever zoom pdf.js is rendering at.
    """
    fitz = _require_pymupdf()

    if not force_ocr:
        cached = get_cached_pdf_page_layout(book_uuid, page_number)
        if cached is not None:
            return cached

    with _PDF_LOCK:
        doc = fitz.open(str(pdf_path))
        try:
            page_count = len(doc)
            if page_count == 0:
                raise ValueError("This PDF has no pages.")
            page_number = max(0, min(page_number, page_count - 1))
            page = doc[page_number]
            rect = page.rect
            width, height = float(rect.width), float(rect.height)

            ocr_lang = ""
            vertical = False
            source = "text-layer"

            ocr_error = ""
            lines = [] if force_ocr else _text_layer_lines(page)
            total_chars = sum(len(l["chars"]) for l in lines)

            if force_ocr or _page_is_scan(page, total_chars):
                if allow_ocr and ocr_module.is_available():
                    try:
                        lines, ocr_lang, vertical = _ocr_lines(page)
                        source = "ocr"
                    except Exception as exc:
                        # A failing OCR must not take the page down with it: the
                        # scan still renders, and the reason reaches the UI instead
                        # of a bare "it does not work".
                        print(f"[OCR] page {page_number} failed: {exc}")
                        ocr_error = f"{type(exc).__name__}: {exc}"
                        lines = [] if force_ocr else lines
                        source = "ocr-failed" if not lines else "text-layer"
                elif not lines:
                    source = "none"
        finally:
            doc.close()

    words, sentences = _words_from_lines(lines, book_uuid, page_number, width, height)

    layout = {
        "book_uuid": book_uuid,
        "page_number": page_number,
        "page_count": page_count,
        "width": width,
        "height": height,
        "source": source,
        "ocr_lang": ocr_lang,
        "vertical": vertical,
        "ocr_available": ocr_module.is_available(),
        "ocr_reason": ocr_error
        or ("" if ocr_module.is_available() else ocr_module.ocr_status()["reason"]),
        "text_layer_chars": total_chars,
        "words": words,
        "sentences": sentences,
    }

    # A failed OCR is not cached: the next attempt should try again.
    if source not in ("none", "ocr-failed"):
        store_pdf_page_layout(book_uuid, page_number, source, layout)
        if persist_sentences and sentences:
            store_section_sentences(
                book_uuid=book_uuid,
                section_index=page_number,
                section_title=f"Page {page_number + 1}",
                sentences=list(sentences.values()),
                book_title=book_title,
            )

    return layout


def _words_from_lines(
    lines: List[Dict[str, Any]],
    book_uuid: str,
    page_number: int,
    width: float,
    height: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Segments every paragraph into words and projects them onto the page box."""
    words: List[Dict[str, Any]] = []
    sentences: Dict[str, Dict[str, Any]] = {}

    if not lines or width <= 0 or height <= 0:
        return words, sentences

    sentence_counter = 0
    line_offset = 0

    for paragraph_lines in _group_lines_into_paragraphs(lines):
        paragraph_text, chars = _flatten_paragraph(paragraph_lines, line_offset)
        line_offset += len(paragraph_lines)
        if not paragraph_text.strip():
            continue

        cursor = _CharCursor(chars)

        for sentence_text in split_japanese_sentences(paragraph_text):
            sid = f"{book_uuid}:p{page_number}:n{sentence_counter}"
            analyzed = nlp_engine.analyze_sentence(sentence_text, sid)
            analyzed["section_index"] = page_number
            analyzed["sentence_index"] = sentence_counter
            sentence_counter += 1
            sentences[sid] = analyzed

            for token in analyzed.get("tokens", []):
                matched = cursor.consume(token.get("surface", ""))
                if not token.get("is_word") or not matched:
                    continue

                rects = [
                    [
                        round(r[0] / width, 6),
                        round(r[1] / height, 6),
                        round(r[2] / width, 6),
                        round(r[3] / height, 6),
                    ]
                    for r in _rects_for_chars(matched)
                ]
                words.append(
                    {
                        "wid": token["wid"],
                        "sid": sid,
                        "surface": token.get("surface", ""),
                        "base": token.get("base", ""),
                        "normalized": token.get("normalized", ""),
                        "reading_hiragana": token.get("reading_hiragana", ""),
                        "reading_katakana": token.get("reading_katakana", ""),
                        "surface_hiragana": token.get("surface_hiragana", ""),
                        "surface_katakana": token.get("surface_katakana", ""),
                        "pos_detail": token.get("pos_detail", ""),
                        "is_content_word": bool(token.get("is_content_word")),
                        "rects": rects,
                    }
                )

    return words, sentences


def analyze_whole_pdf(
    pdf_path: Path,
    book_uuid: str,
    book_title: str = "",
    force_ocr: bool = False,
    progress: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Walks every page, building (and caching) its word layer.

    This is what makes batch i+1 mining possible on an image-only scan: each page's
    OCR'd sentences are registered in mining.db as it goes, so progress is never lost
    if the job is interrupted.
    """
    report = progress or (lambda *a, **k: None)
    info = get_pdf_info(pdf_path, book_uuid=book_uuid)
    total = info["page_count"]

    pages_done = 0
    by_source: Dict[str, int] = {}

    for page_number in range(total):
        report(
            "ocr" if not info["has_text_layer"] else "segmenting",
            page_number,
            total,
            f"Page {page_number + 1} / {total}",
        )
        try:
            layout = build_page_layout(
                pdf_path=pdf_path,
                page_number=page_number,
                book_uuid=book_uuid,
                force_ocr=force_ocr,
                book_title=book_title,
            )
            by_source[layout["source"]] = by_source.get(layout["source"], 0) + 1
            pages_done += 1
        except Exception as exc:
            print(f"[PDF] page {page_number} failed: {exc}")

    report("done", total, total, "")
    return {"pages_done": pages_done, "page_count": total, "by_source": by_source}
