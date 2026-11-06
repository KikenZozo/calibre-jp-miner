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

    return {
        "available": bool(binary and has_jpn),
        "binary": binary or "",
        "languages": langs,
        "has_vertical": has_vert,
        "dpi": OCR_DPI,
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

    width = sum(c["bbox"][2] - c["bbox"][0] for c in chars)
    height = sum(c["bbox"][3] - c["bbox"][1] for c in chars)
    vertical = height > width

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


def _score(lines: List[Dict[str, Any]]) -> float:
    """Confidence-weighted character count, used to pick horizontal vs vertical."""
    total = 0.0
    for line in lines:
        for ch in line["chars"]:
            total += max(0.0, ch.get("conf", 0.0)) / 100.0
    return total


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
