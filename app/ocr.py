"""
Optional Tesseract OCR layer for image-only (scanned) PDFs.

Produces the same per-character bounding boxes that PyMuPDF's text layer gives,
so a scanned page can feed exactly the same word-segmentation and mining pipeline
as a digital one. Degrades gracefully (and loudly) when Tesseract is missing.
"""

import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

# Rendering resolution for OCR. 300 dpi is the Tesseract sweet spot for CJK.
OCR_DPI = int(os.getenv("OCR_DPI", "300"))

# Horizontal Japanese, vertical Japanese (tategaki), and the fallback order.
OCR_LANG_HORIZONTAL = os.getenv("OCR_LANG_HORIZONTAL", "jpn")
OCR_LANG_VERTICAL = os.getenv("OCR_LANG_VERTICAL", "jpn_vert")

OCR_TIMEOUT_SECONDS = int(os.getenv("OCR_TIMEOUT_SECONDS", "120"))

# --- Engine selection -------------------------------------------------------
# "auto" prefers Google Vision when a key is configured and the monthly budget
# still allows it, and falls back to Tesseract otherwise.
OCR_ENGINE = os.getenv("OCR_ENGINE", "auto").strip().lower()

GOOGLE_VISION_API_KEY = os.getenv("GOOGLE_VISION_API_KEY", "").strip()
GOOGLE_VISION_ENDPOINT = os.getenv(
    "GOOGLE_VISION_ENDPOINT", "https://vision.googleapis.com/v1/images:annotate"
)
# Google bills per page beyond the first 1000 each month. Staying inside the free
# tier is the whole reason to pick a number here, so the cap is enforced rather
# than trusted: past it, pages fall back to Tesseract instead of costing money.
GOOGLE_VISION_MONTHLY_PAGE_LIMIT = int(
    os.getenv("GOOGLE_VISION_MONTHLY_PAGE_LIMIT", "1000")
)
# Vision reads comfortably below Tesseract's resolution, and a smaller JPEG keeps
# the request well inside the 20 MB limit.
GOOGLE_VISION_DPI = int(os.getenv("GOOGLE_VISION_DPI", "200"))
GOOGLE_VISION_TIMEOUT = int(os.getenv("GOOGLE_VISION_TIMEOUT", "60"))

_BBOX_RE = re.compile(r"bbox (-?\d+) (-?\d+) (-?\d+) (-?\d+)")
_CBOX_RE = re.compile(r"x_bboxes((?:\s+-?\d+)+)")
_CONF_RE = re.compile(r"x_conf ([\d.]+)")
_WCONF_RE = re.compile(r"x_wconf (-?[\d.]+)")


@lru_cache(maxsize=1)
def tesseract_binary() -> Optional[str]:
    return shutil.which(os.getenv("TESSERACT_BIN", "tesseract"))


@lru_cache(maxsize=1)
def available_languages() -> List[str]:
    binary = tesseract_binary()
    if not binary:
        return []
    try:
        out = subprocess.run(
            [binary, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return []
    langs = []
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line and not line.lower().startswith("list of"):
            langs.append(line)
    return langs


def vision_budget() -> Dict[str, Any]:
    """Where this month stands against the free tier."""
    from app.database import get_ocr_usage

    used = get_ocr_usage("google_vision")
    return {
        "used": used,
        "limit": GOOGLE_VISION_MONTHLY_PAGE_LIMIT,
        "remaining": max(0, GOOGLE_VISION_MONTHLY_PAGE_LIMIT - used),
        "exhausted": used >= GOOGLE_VISION_MONTHLY_PAGE_LIMIT > 0,
    }


def vision_usable() -> bool:
    """Configured, selected, and still inside the monthly page budget."""
    if not vision_configured() or OCR_ENGINE not in ("auto", "google_vision"):
        return False
    if GOOGLE_VISION_MONTHLY_PAGE_LIMIT <= 0:
        return True
    return not vision_budget()["exhausted"]


def ocr_status() -> Dict[str, Any]:
    """Describes OCR readiness so the UI can explain exactly what is missing."""
    binary = tesseract_binary()
    langs = available_languages()
    has_jpn = OCR_LANG_HORIZONTAL in langs
    has_vert = OCR_LANG_VERTICAL in langs

    if not binary:
        reason = (
            "Tesseract is not installed in this container. "
            "Add 'tesseract-ocr tesseract-ocr-jpn tesseract-ocr-jpn-vert' to the image."
        )
    elif not has_jpn:
        reason = (
            f"Tesseract is installed but the '{OCR_LANG_HORIZONTAL}' language data is missing. "
            "Install the 'tesseract-ocr-jpn' package."
        )
    else:
        reason = ""

    tesseract_ready = bool(binary and has_jpn)
    budget = vision_budget()
    vision_ready = vision_usable()

    if vision_ready:
        engine = "google_vision"
        reason = ""
    elif tesseract_ready:
        engine = "tesseract"
        if vision_configured() and budget["exhausted"]:
            reason = (
                f"Google Vision budget for this month is used up "
                f"({budget['used']}/{budget['limit']} pages); falling back to Tesseract."
            )
    else:
        engine = "none"

    return {
        "available": tesseract_ready or vision_ready,
        "engine": engine,
        "binary": binary or "",
        "languages": langs,
        "has_vertical": has_vert,
        "dpi": GOOGLE_VISION_DPI if vision_ready else OCR_DPI,
        "tesseract_available": tesseract_ready,
        "vision_configured": vision_configured(),
        "vision_usable": vision_ready,
        "vision_budget": budget,
        "engine_setting": OCR_ENGINE,
        "reason": reason,
    }


def is_available() -> bool:
    return ocr_status()["available"]


def _run_tesseract_hocr(image_path: Path, lang: str, psm: str) -> str:
    binary = tesseract_binary()
    if not binary:
        raise RuntimeError("Tesseract binary not available")

    with tempfile.TemporaryDirectory() as tmp:
        out_base = Path(tmp) / "page"
        cmd = [
            binary,
            str(image_path),
            str(out_base),
            "-l",
            lang,
            "--psm",
            psm,
            "-c",
            "hocr_char_boxes=1",
            "hocr",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=OCR_TIMEOUT_SECONDS
        )
        hocr_file = out_base.with_suffix(".hocr")
        if not hocr_file.exists():
            raise RuntimeError(
                f"Tesseract produced no output (exit {proc.returncode}): "
                f"{(proc.stderr or '').strip()[:300]}"
            )
        return hocr_file.read_text(encoding="utf-8", errors="replace")


def _title_of(tag: Any) -> str:
    return tag.get("title", "") or ""


def _split_box_over_text(box: List[float], text: str, conf: float) -> List[Dict[str, Any]]:
    """Splits one bounding box evenly across the glyphs it covers."""
    chars: List[Dict[str, Any]] = []
    glyphs = [c for c in text if c.strip()]
    if not glyphs:
        return chars
    if len(glyphs) == 1:
        return [{"c": glyphs[0], "bbox": [float(v) for v in box], "conf": conf}]

    x0, y0, x1, y1 = (float(v) for v in box)
    width, height = x1 - x0, y1 - y0
    vertical = height > width * 1.6
    span = (height if vertical else width) / len(glyphs)
    for k, ch in enumerate(glyphs):
        if vertical:
            chars.append(
                {"c": ch, "bbox": [x0, y0 + span * k, x1, y0 + span * (k + 1)], "conf": conf}
            )
        else:
            chars.append(
                {"c": ch, "bbox": [x0 + span * k, y0, x0 + span * (k + 1), y1], "conf": conf}
            )
    return chars


def _declash_char_boxes(chars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Trims overlapping character boxes on a line.

    Tesseract sometimes reports the whole word's box for its first glyph, which would
    make one clickable word swallow its neighbours. Reading order is known, so each
    box is clamped to start where the previous one ends.
    """
    if len(chars) < 2:
        return chars

    # Direction comes from how far the line RUNS, not from the size of its glyphs.
    # Summing per-character widths and heights always made Japanese look vertical —
    # a CJK glyph is about as tall as it is wide, so the heights win every time —
    # and the trimming below then collapsed every box on a horizontal line to a
    # zero-height sliver, leaving words that could not be clicked.
    x_span = max(c["bbox"][2] for c in chars) - min(c["bbox"][0] for c in chars)
    y_span = max(c["bbox"][3] for c in chars) - min(c["bbox"][1] for c in chars)
    vertical = y_span > x_span

    for i in range(len(chars) - 1):
        cur, nxt = chars[i]["bbox"], chars[i + 1]["bbox"]
        if vertical:
            if cur[3] > nxt[1] > cur[1]:
                cur[3] = nxt[1]
        else:
            if cur[2] > nxt[0] > cur[0]:
                cur[2] = nxt[0]

    return [c for c in chars if c["bbox"][2] > c["bbox"][0] and c["bbox"][3] > c["bbox"][1]]


def _parse_hocr_lines(hocr: str) -> List[Dict[str, Any]]:
    """
    Parses hOCR into lines of characters with PIXEL bounding boxes.

    Returns [{"bbox": [x0,y0,x1,y1], "chars": [{"c", "bbox", "conf"}]}].
    """
    import warnings

    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    soup = BeautifulSoup(hocr, "lxml")
    lines: List[Dict[str, Any]] = []

    for line_tag in soup.find_all(
        class_=lambda c: c and ("ocr_line" in c or "ocr_textfloat" in c or "ocr_caption" in c)
    ):
        line_bbox_m = _BBOX_RE.search(_title_of(line_tag))
        if not line_bbox_m:
            continue
        line_bbox = [float(v) for v in line_bbox_m.groups()]

        chars: List[Dict[str, Any]] = []
        for word_tag in line_tag.find_all(class_="ocrx_word"):
            word_title = _title_of(word_tag)
            wconf_m = _WCONF_RE.search(word_title)
            word_conf = float(wconf_m.group(1)) if wconf_m else 0.0

            cinfos = word_tag.find_all(class_="ocrx_cinfo")
            if cinfos:
                for ci in cinfos:
                    ci_title = _title_of(ci)
                    text = ci.get_text()
                    box_m = _CBOX_RE.search(ci_title)
                    if not box_m or not text.strip():
                        continue
                    nums = [float(v) for v in box_m.group(1).split()]
                    if len(nums) < 4:
                        continue
                    conf_m = _CONF_RE.search(ci_title)
                    conf = float(conf_m.group(1)) if conf_m else word_conf
                    chars.extend(_split_box_over_text(nums[:4], text, conf))
            else:
                box_m = _BBOX_RE.search(word_title)
                text = word_tag.get_text()
                if box_m and text.strip():
                    chars.extend(
                        _split_box_over_text(
                            [float(v) for v in box_m.groups()], text, word_conf
                        )
                    )

        chars = _declash_char_boxes(chars)
        if chars:
            lines.append({"bbox": line_bbox, "chars": chars})

    return lines


# --- Google Cloud Vision -----------------------------------------------------


def vision_configured() -> bool:
    return bool(GOOGLE_VISION_API_KEY)


def _vertices_to_bbox(bounding: Dict[str, Any]) -> Optional[List[float]]:
    """
    Axis-aligned box from Vision's four vertices.

    Vision omits a coordinate entirely when it is zero, so a missing x or y means
    0 rather than "unknown" — reading them with .get(..., 0) is deliberate.
    """
    vertices = bounding.get("vertices") or bounding.get("normalizedVertices") or []
    xs = [float(v.get("x", 0) or 0) for v in vertices]
    ys = [float(v.get("y", 0) or 0) for v in vertices]
    if not xs or not ys:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _symbol_breaks_line(symbol: Dict[str, Any]) -> bool:
    break_type = (
        symbol.get("property", {}).get("detectedBreak", {}).get("type", "")
    )
    return break_type in ("LINE_BREAK", "EOL_SURE_SPACE")


def parse_vision_response(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Turns a DOCUMENT_TEXT_DETECTION response into the same character lines
    Tesseract produces, so both engines feed one pipeline.

    Vision returns a box per symbol, produced by the same model that read the
    text, which is why its boxes cannot drift away from the glyphs they describe.
    """
    responses = payload.get("responses") or []
    if not responses:
        return []

    first = responses[0]
    if "error" in first and first["error"]:
        message = first["error"].get("message", "unknown error")
        raise RuntimeError(f"Google Vision error: {message}")

    annotation = first.get("fullTextAnnotation") or {}
    lines: List[Dict[str, Any]] = []

    for page in annotation.get("pages", []):
        for block in page.get("blocks", []):
            for paragraph in block.get("paragraphs", []):
                current: List[Dict[str, Any]] = []
                for word in paragraph.get("words", []):
                    for symbol in word.get("symbols", []):
                        text = symbol.get("text", "")
                        bbox = _vertices_to_bbox(symbol.get("boundingBox", {}))
                        if text.strip() and bbox:
                            current.append({"c": text, "bbox": bbox, "conf": 100.0 * float(symbol.get("confidence", 1.0) or 1.0)})
                        if _symbol_breaks_line(symbol) and current:
                            lines.append(_line_from_chars(current))
                            current = []
                if current:
                    lines.append(_line_from_chars(current))

    return lines


def _line_from_chars(chars: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "bbox": [
            min(c["bbox"][0] for c in chars),
            min(c["bbox"][1] for c in chars),
            max(c["bbox"][2] for c in chars),
            max(c["bbox"][3] for c in chars),
        ],
        "chars": chars,
    }


def vision_image_to_char_lines(image_bytes: bytes) -> Dict[str, Any]:
    """Sends one rendered page to Google Vision and returns its character lines."""
    import base64

    import httpx

    if not GOOGLE_VISION_API_KEY:
        raise RuntimeError("GOOGLE_VISION_API_KEY is not set")

    request = {
        "requests": [
            {
                "image": {"content": base64.b64encode(image_bytes).decode("ascii")},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                # Japanese plus vertical Japanese: the hint keeps Vision from
                # reading a tategaki page as if it ran left to right.
                "imageContext": {"languageHints": ["ja", "ja-t-i0-handwrit"]},
            }
        ]
    }

    response = httpx.post(
        GOOGLE_VISION_ENDPOINT,
        params={"key": GOOGLE_VISION_API_KEY},
        json=request,
        timeout=GOOGLE_VISION_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Google Vision HTTP {response.status_code}: {response.text[:200]}"
        )

    lines = parse_vision_response(response.json())
    return {"lang": "google-vision", "vertical": _lines_look_vertical(lines), "lines": lines}


def _lines_look_vertical(lines: List[Dict[str, Any]]) -> bool:
    """True when most lines run down the page rather than across it."""
    vertical = 0
    for line in lines:
        x0, y0, x1, y1 = line["bbox"]
        if (y1 - y0) > (x1 - x0):
            vertical += 1
    return bool(lines) and vertical > len(lines) / 2


def _score(lines: List[Dict[str, Any]]) -> float:
    """Confidence-weighted character count, used to pick horizontal vs vertical."""
    total = 0.0
    for line in lines:
        for ch in line["chars"]:
            total += max(0.0, ch.get("conf", 0.0)) / 100.0
    return total


def recognize_page(
    image_path: Path, image_bytes: Optional[bytes] = None
) -> Dict[str, Any]:
    """
    Reads one rendered page with whichever engine is available.

    Google Vision first when it is configured and still inside its monthly budget,
    because its Japanese accuracy — and its per-symbol boxes — are what the word
    overlay needs. Tesseract remains the free fallback, including when the Vision
    call fails, so a network hiccup degrades the reading instead of losing the page.
    """
    if vision_usable() and image_bytes is not None:
        from app.database import record_ocr_usage

        try:
            result = vision_image_to_char_lines(image_bytes)
            if result["lines"]:
                record_ocr_usage("google_vision", 1)
                result["engine"] = "google_vision"
                return result
            print("[OCR] Google Vision returned no text; falling back to Tesseract")
        except Exception as exc:
            print(f"[OCR] Google Vision failed ({exc}); falling back to Tesseract")

    result = ocr_image_to_char_lines(image_path)
    result["engine"] = "tesseract"
    return result


def ocr_image_to_char_lines(
    image_path: Path, try_vertical: bool = True
) -> Dict[str, Any]:
    """
    OCRs one rendered page image and returns character lines in PIXEL coordinates.

    Tries horizontal Japanese first and, when the vertical model is installed,
    also tries tategaki, keeping whichever reads more confidently.
    """
    status = ocr_status()
    if not status["available"]:
        raise RuntimeError(status["reason"] or "OCR is not available")

    attempts: List[Dict[str, Any]] = []

    try:
        horizontal = _parse_hocr_lines(
            _run_tesseract_hocr(image_path, OCR_LANG_HORIZONTAL, "6")
        )
        attempts.append({"lang": OCR_LANG_HORIZONTAL, "vertical": False, "lines": horizontal})
    except Exception as exc:
        print(f"[OCR] horizontal pass failed: {exc}")

    if try_vertical and status["has_vertical"]:
        try:
            vertical = _parse_hocr_lines(
                _run_tesseract_hocr(image_path, OCR_LANG_VERTICAL, "5")
            )
            attempts.append({"lang": OCR_LANG_VERTICAL, "vertical": True, "lines": vertical})
        except Exception as exc:
            print(f"[OCR] vertical pass failed: {exc}")

    if not attempts:
        raise RuntimeError("Tesseract returned no usable output for this page")

    best = max(attempts, key=lambda a: _score(a["lines"]))
    return {
        "lang": best["lang"],
        "vertical": best["vertical"],
        "lines": best["lines"],
    }
