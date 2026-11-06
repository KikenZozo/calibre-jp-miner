import os
import posixpath
import threading
import warnings
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning

from app.database import (
    get_cached_book_section,
    is_book_extracted,
    store_extracted_book,
)
from app.japanese_nlp import nlp_engine, split_japanese_sentences

try:
    import fitz  # PyMuPDF

    _PYMUPDF_AVAILABLE = True
except ImportError:
    _PYMUPDF_AVAILABLE = False

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Global lock to guarantee strictly sequential extraction from Google Drive symlinks
_EXTRACTION_LOCK = threading.Lock()

BLOCK_TAGS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "li",
    "dt",
    "dd",
    "pre",
    "figcaption",
}

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

SENTENCE_END_CHARS = set("。．｡！？!?」』）)】〉》”…‥")

# Bump when the extractor starts producing richer sections, so books cached by an
# older build are re-extracted instead of staying without their illustrations.
#   1 = illustrations, table of contents and in-text links
EXTRACTION_VERSION = 1


def ensure_book_extracted_and_get_section(
    book_descriptor: Dict[str, Any], section_index: int = 0
) -> Dict[str, Any]:
    """
    Returns the requested section from the permanent mining.db cache, extracting the
    book first if needed. Kept for callers that can afford to block (tests, batch i+1).
    """
    book_uuid = book_descriptor["book_uuid"]

    cached = get_cached_book_section(book_uuid, section_index)
    if cached is not None:
        return cached

    extract_book_into_cache(book_descriptor)

    cached = get_cached_book_section(book_uuid, section_index)
    if cached is None:
        raise RuntimeError(f"Failed to load cached section for {book_uuid}")
    return cached


def extract_book_into_cache(
    book_descriptor: Dict[str, Any], progress: Optional[Callable[..., None]] = None
) -> None:
    """
    Extracts a book (EPUB or PDF) exactly once and caches it permanently in mining.db,
    keyed by book_uuid. Extraction is serialised so a Google Drive symlink is never
    opened by two requests at the same time.
    """
    book_uuid = book_descriptor["book_uuid"]
    report = progress or (lambda *a, **k: None)

    if is_book_extracted(book_uuid, EXTRACTION_VERSION):
        return

    report("waiting", 0, 0, "Waiting for the extraction queue...")
    with _EXTRACTION_LOCK:
        # Another request may have finished the same book while we waited.
        if is_book_extracted(book_uuid, EXTRACTION_VERSION):
            return

        file_path_str = book_descriptor["file_path"]
        file_path = Path(file_path_str)
        # Re-check os.path.islink right before opening (the offload cron runs every 10 min)
        is_symlink = os.path.islink(file_path_str)
        fmt = (book_descriptor.get("preferred_format") or "EPUB").upper()

        if not file_path.exists():
            raise FileNotFoundError(
                f"Book file not found or Drive mount unavailable: {file_path_str}"
            )

        report("opening", 0, 0, f"Opening {file_path.name}...")

        toc: List[Dict[str, Any]] = []
        if fmt == "PDF" or file_path.suffix.lower() == ".pdf":
            has_text, sections, all_sentences = _extract_pdf_full(
                file_path, book_uuid, progress=report
            )
        else:
            has_text, sections, all_sentences, toc = _extract_epub_full(
                file_path, book_uuid, progress=report
            )

        report("saving", len(sections), max(1, len(sections)), "Saving to mining.db...")
        store_extracted_book(
            book_uuid=book_uuid,
            calibre_id=int(book_descriptor.get("calibre_id") or 0),
            title=book_descriptor.get("title") or file_path.stem,
            author=book_descriptor.get("author") or "Unknown",
            source_format=fmt,
            uncompressed_size=int(book_descriptor.get("uncompressed_size") or 0),
            is_symlink=is_symlink,
            has_text_layer=has_text,
            sections=sections,
            sentences=all_sentences,
            toc=toc,
            extraction_version=EXTRACTION_VERSION,
        )


def _extract_epub_full(
    epub_path: Path, book_uuid: str, progress: Optional[Callable[..., None]] = None
) -> Tuple[bool, List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    report = progress or (lambda *a, **k: None)
    meta = extract_epub_metadata_and_spine(epub_path)
    chapters = meta["chapters"]
    sections: List[Dict[str, Any]] = []
    all_sentences: List[Dict[str, Any]] = []
    total_chapters = len(chapters)
    href_to_section = {c["href"]: c["index"] for c in chapters}

    with zipfile.ZipFile(str(epub_path), "r") as zf:
        zip_names = set(zf.namelist())
        for sec_idx, chap_info in enumerate(chapters):
            report(
                "segmenting",
                sec_idx,
                total_chapters,
                f"Section {sec_idx + 1}/{total_chapters}: {chap_info['title']}",
            )
            raw_bytes = zf.read(chap_info["href"])
            html_str = _decode_html_bytes(raw_bytes)
            raw_blocks = extract_normalized_blocks_from_html(
                html_str,
                base_dir=posixpath.dirname(chap_info["href"]),
                zip_names=zip_names,
            )
            merged_blocks = _merge_soft_wrapped_blocks(raw_blocks)

            rendered_blocks: List[Dict[str, Any]] = []
            sent_counter = 0

            for b_idx, blk in enumerate(merged_blocks):
                if blk.get("type") == "image":
                    rendered_blocks.append(
                        {
                            "block_id": f"b{b_idx}",
                            "type": "image",
                            "style_class": "",
                            "src": blk.get("src", ""),
                            "alt": blk.get("alt", ""),
                            "sentences": [],
                        }
                    )
                    continue

                sents = split_japanese_sentences(blk["text"])
                if not sents:
                    continue
                block_sentences: List[Dict[str, Any]] = []
                for s_text in sents:
                    sid = f"{book_uuid}:s{sec_idx}:n{sent_counter}"
                    analyzed = nlp_engine.analyze_sentence(s_text, sid)
                    analyzed["section_index"] = sec_idx
                    analyzed["sentence_index"] = sent_counter
                    sent_counter += 1
                    all_sentences.append(analyzed)
                    block_sentences.append(analyzed)

                rendered = {
                    "block_id": f"b{b_idx}",
                    "type": blk["type"],
                    "style_class": blk.get("style_class", ""),
                    "sentences": block_sentences,
                }
                if blk.get("link_href"):
                    rendered["link_href"] = blk["link_href"]
                    rendered["link_target"] = href_to_section.get(blk["link_href"])
                rendered_blocks.append(rendered)

            sections.append(
                {
                    "section_index": sec_idx,
                    "section_title": chap_info["title"],
                    "blocks": rendered_blocks,
                }
            )

    return len(all_sentences) > 0, sections, all_sentences, meta.get("toc", [])


def _extract_pdf_full(
    pdf_path: Path,
    book_uuid: str,
    pages_per_section: int = 10,
    progress: Optional[Callable[..., None]] = None,
) -> Tuple[bool, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Extracts Japanese text from a PDF using PyMuPDF (fitz).
    Always tests for an embedded text layer first; an image-only scan yields no
    reflowed sections at all (has_text_layer=False) and is handled by the classic
    PDF view with its on-demand OCR instead.
    """
    report = progress or (lambda *a, **k: None)
    if not _PYMUPDF_AVAILABLE:
        raise RuntimeError("PyMuPDF (fitz) is required for PDF extraction.")

    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)

    # 1. Sample pages to verify if a text layer exists
    sample_indices = set(
        range(min(15, total_pages))
    ) | {total_pages // 2, max(0, total_pages - 1)}
    sampled_chars = 0
    for p_idx in sorted(sample_indices):
        if 0 <= p_idx < total_pages:
            sampled_chars += len(doc[p_idx].get_text("text").strip())

    has_text_layer = sampled_chars >= 20
    sections: List[Dict[str, Any]] = []
    all_sentences: List[Dict[str, Any]] = []

    if not has_text_layer:
        doc.close()
        # An image-only scan has nothing to reflow. Cache it as an empty, text-less
        # book so the UI routes the reader straight to the classic PDF + OCR view
        # instead of showing a placeholder paragraph as if it were the book.
        return False, [], []

    # 2. Extract text layer grouped by section
    sec_idx = 0
    for start_page in range(0, total_pages, pages_per_section):
        end_page = min(total_pages, start_page + pages_per_section)
        report(
            "segmenting",
            start_page,
            total_pages,
            f"Pages {start_page + 1}-{end_page} of {total_pages}",
        )
        raw_blocks: List[Dict[str, str]] = []

        for p_num in range(start_page, end_page):
            page_text = doc[p_num].get_text("text")
            if not page_text.strip():
                continue
            for line in page_text.splitlines():
                cleaned = line.strip()
                if cleaned:
                    raw_blocks.append({"type": "p", "text": cleaned, "style_class": ""})

        merged_blocks = _merge_soft_wrapped_blocks(raw_blocks)
        if not merged_blocks:
            continue

        rendered_blocks: List[Dict[str, Any]] = []
        sent_counter = 0

        for b_idx, blk in enumerate(merged_blocks):
            sents = split_japanese_sentences(blk["text"])
            if not sents:
                continue
            block_sentences: List[Dict[str, Any]] = []
            for s_text in sents:
                sid = f"{book_uuid}:s{sec_idx}:n{sent_counter}"
                analyzed = nlp_engine.analyze_sentence(s_text, sid)
                analyzed["section_index"] = sec_idx
                analyzed["sentence_index"] = sent_counter
                sent_counter += 1
                all_sentences.append(analyzed)
                block_sentences.append(analyzed)

            rendered_blocks.append(
                {
                    "block_id": f"b{b_idx}",
                    "type": "p",
                    "style_class": "",
                    "sentences": block_sentences,
                }
            )

        if rendered_blocks:
            sections.append(
                {
                    "section_index": sec_idx,
                    "section_title": f"Pages {start_page + 1}–{end_page}",
                    "blocks": rendered_blocks,
                }
            )
            sec_idx += 1

    doc.close()
    return True, sections, all_sentences


def parse_epub_chapter_tokenized(
    epub_path: Path, chapter_index: int
) -> Dict[str, Any]:
    """Direct single-chapter EPUB parser used by unit tests and standalone previews."""
    meta = extract_epub_metadata_and_spine(epub_path)
    chapters = meta["chapters"]
    if not chapters:
        return {
            "chapter_index": 0,
            "chapter_title": "Empty",
            "total_chapters": 0,
            "blocks": [],
            "sentences": {},
        }

    chapter_index = max(0, min(chapter_index, len(chapters) - 1))
    chap_info = chapters[chapter_index]

    with zipfile.ZipFile(str(epub_path), "r") as zf:
        raw_bytes = zf.read(chap_info["href"])

    html_str = _decode_html_bytes(raw_bytes)
    raw_blocks = extract_normalized_blocks_from_html(html_str)
    merged_blocks = _merge_soft_wrapped_blocks(raw_blocks)

    rendered_blocks: List[Dict[str, Any]] = []
    sentences_map: Dict[str, Dict[str, Any]] = {}
    sentence_counter = 0

    for b_idx, block in enumerate(merged_blocks):
        sents = split_japanese_sentences(block["text"])
        if not sents:
            continue

        block_sentences: List[Dict[str, Any]] = []
        for s_text in sents:
            sid = f"c{chapter_index}_s{sentence_counter}"
            sentence_counter += 1
            analyzed = nlp_engine.analyze_sentence(s_text, sid)
            sentences_map[sid] = analyzed
            block_sentences.append(analyzed)

        rendered_blocks.append(
            {
                "block_id": f"b{b_idx}",
                "type": block["type"],
                "style_class": block.get("style_class", ""),
                "sentences": block_sentences,
            }
        )

    return {
        "book_title": meta["title"],
        "chapter_index": chapter_index,
        "chapter_title": chap_info["title"],
        "total_chapters": len(chapters),
        "chapters": chapters,
        "blocks": rendered_blocks,
        "sentences": sentences_map,
    }


def extract_epub_metadata_and_spine(epub_path: Path) -> Dict[str, Any]:
    if not epub_path.exists():
        raise FileNotFoundError(f"EPUB not found: {epub_path}")

    with zipfile.ZipFile(str(epub_path), "r") as zf:
        opf_path = _find_opf_path(zf)
        opf_dir = posixpath.dirname(opf_path)
        opf_xml = zf.read(opf_path)
        root = ET.fromstring(opf_xml)

        ns = {
            "opf": "http://www.idpf.org/2007/opf",
            "dc": "http://purl.org/dc/elements/1.1/",
        }

        title_el = root.find(".//dc:title", ns)
        creator_el = root.find(".//dc:creator", ns)
        title = (
            title_el.text.strip()
            if (title_el is not None and title_el.text)
            else epub_path.stem
        )
        author = (
            creator_el.text.strip()
            if (creator_el is not None and creator_el.text)
            else "Unknown"
        )

        manifest: Dict[str, Tuple[str, str]] = {}
        for item in root.findall(".//{*}manifest/{*}item"):
            item_id = item.attrib.get("id", "")
            href = item.attrib.get("href", "")
            media_type = item.attrib.get("media-type", "")
            if item_id and href:
                full_href = (
                    posixpath.normpath(posixpath.join(opf_dir, href))
                    if opf_dir
                    else href
                )
                manifest[item_id] = (full_href, media_type)

        toc_titles = _extract_toc_titles(zf, root, opf_dir, manifest)

        chapters: List[Dict[str, Any]] = []
        toc_context = (zf, root, opf_dir, manifest)
        spine_items = root.findall(".//{*}spine/{*}itemref")
        for idx, itemref in enumerate(spine_items):
            idref = itemref.attrib.get("idref", "")
            if idref not in manifest:
                continue
            href, media_type = manifest[idref]
            if "html" not in media_type and not href.endswith(
                (".xhtml", ".html", ".htm")
            ):
                continue

            chap_title = toc_titles.get(href) or _guess_chapter_title(zf, href, idx)
            chapters.append(
                {
                    "index": len(chapters),
                    "href": href,
                    "title": chap_title,
                }
            )

        href_to_section = {c["href"]: c["index"] for c in chapters}
        try:
            toc = build_toc_entries(*toc_context, href_to_section)
        except Exception as exc:
            print(f"[EPUB] table of contents unreadable: {exc}")
            toc = []

    return {
        "title": title,
        "author": author,
        "chapters": chapters,
        "total_chapters": len(chapters),
        "toc": toc,
    }


def extract_normalized_blocks_from_html(
    html_str: str, base_dir: str = "", zip_names: Optional[Set[str]] = None
) -> List[Dict[str, str]]:
    """
    Extracts paragraphs, headings and illustrations from EPUB HTML while:
    1. Stripping <rt> and <rp> ruby furigana annotations so words are never split.
    2. Merging inline font/style tags (<span>, <font>, <b>, <em>) to keep sentences whole.
    3. Emitting image blocks (<img> and <svg><image>) with their path inside the EPUB,
       so illustrations survive instead of being thrown away with the markup.
    4. Tagging a block that is essentially one link with its target, which is what
       makes a book's own table-of-contents page clickable.
    """
    soup = BeautifulSoup(html_str, "lxml")

    for unwanted in soup.find_all(["script", "style", "head", "rt", "rp"]):
        unwanted.decompose()

    body = soup.find("body") or soup

    for br in body.find_all("br"):
        br.replace_with("\n")

    blocks: List[Dict[str, str]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, NavigableString):
            txt = str(node)
            if txt.strip():
                _append_lines_as_blocks(blocks, txt, "p", "")
            return

        if not isinstance(node, Tag):
            return

        tag_name = node.name.lower() if node.name else ""

        if tag_name in ("img", "image"):
            src = _resolve_resource_href(node, base_dir, zip_names)
            if src:
                blocks.append(
                    {
                        "type": "image",
                        "text": "",
                        "style_class": "",
                        "src": src,
                        "alt": (node.get("alt") or "").strip(),
                    }
                )
            return

        if tag_name == "svg":
            for img in node.find_all(["image", "img"]):
                src = _resolve_resource_href(img, base_dir, zip_names)
                if src:
                    blocks.append(
                        {
                            "type": "image",
                            "text": "",
                            "style_class": "",
                            "src": src,
                            "alt": (img.get("alt") or "").strip(),
                        }
                    )
            return

        has_child_blocks = any(
            isinstance(c, Tag)
            and (c.name.lower() in BLOCK_TAGS or c.name.lower() == "div")
            for c in node.children
        )

        if tag_name in HEADING_TAGS:
            text = _extract_clean_inline_text(node)
            if text.strip():
                blocks.append(
                    {"type": tag_name, "text": text, "style_class": "heading"}
                )
                _tag_blocks_with_link(blocks, len(blocks) - 1, node, base_dir, text)
            return

        if tag_name in BLOCK_TAGS and not has_child_blocks:
            style_cls = _detect_typography_class(node)
            text = _extract_clean_inline_text(node)
            inline_images = _collect_inline_images(node, base_dir, zip_names)
            if text.strip():
                before_count = len(blocks)
                _append_lines_as_blocks(blocks, text, "p", style_cls)
                _tag_blocks_with_link(blocks, before_count, node, base_dir, text)
            blocks.extend(inline_images)
            return

        if tag_name == "div" and not has_child_blocks:
            style_cls = _detect_typography_class(node)
            text = _extract_clean_inline_text(node)
            inline_images = _collect_inline_images(node, base_dir, zip_names)
            if text.strip():
                before_count = len(blocks)
                _append_lines_as_blocks(blocks, text, "p", style_cls)
                _tag_blocks_with_link(blocks, before_count, node, base_dir, text)
            blocks.extend(inline_images)
            return

        inline_buf: List[str] = []
        for child in node.children:
            if isinstance(child, Tag) and (
                child.name.lower() in BLOCK_TAGS or child.name.lower() == "div"
            ):
                if inline_buf:
                    combined = "".join(inline_buf)
                    if combined.strip():
                        _append_lines_as_blocks(blocks, combined, "p", "")
                    inline_buf = []
                _walk(child)
            else:
                inline_buf.append(_extract_clean_inline_text(child))

        if inline_buf:
            combined = "".join(inline_buf)
            if combined.strip():
                _append_lines_as_blocks(blocks, combined, "p", "")

    _walk(body)
    return blocks


def _resolve_resource_href(
    tag: Tag, base_dir: str, zip_names: Optional[Set[str]]
) -> str:
    """
    Resolves an <img>/<image> reference to its actual entry inside the EPUB zip.

    Returns "" when the target cannot be found, so a broken reference is dropped
    rather than rendered as a missing image.
    """
    raw = (
        tag.get("src")
        or tag.get("href")
        or tag.get("xlink:href")
        or tag.get("{http://www.w3.org/1999/xlink}href")
        or ""
    ).strip()
    if not raw or raw.startswith(("data:", "http://", "https://")):
        return ""

    raw = unquote(raw.split("#")[0])
    candidate = posixpath.normpath(posixpath.join(base_dir, raw)) if base_dir else raw
    candidate = candidate.lstrip("./")

    if zip_names is None:
        return candidate
    if candidate in zip_names:
        return candidate

    # Some books reference images with paths that do not match the zip layout;
    # fall back to a unique basename match before giving up.
    basename = posixpath.basename(candidate)
    matches = [n for n in zip_names if posixpath.basename(n) == basename]
    return matches[0] if len(matches) == 1 else ""


def _collect_inline_images(
    node: Tag, base_dir: str, zip_names: Optional[Set[str]]
) -> List[Dict[str, str]]:
    """Image blocks for illustrations sitting inside a text block."""
    found: List[Dict[str, str]] = []
    for img in node.find_all(["img", "image"]):
        src = _resolve_resource_href(img, base_dir, zip_names)
        if src:
            found.append(
                {
                    "type": "image",
                    "text": "",
                    "style_class": "",
                    "src": src,
                    "alt": (img.get("alt") or "").strip(),
                }
            )
    return found


def _tag_blocks_with_link(
    blocks: List[Dict[str, str]],
    start_index: int,
    node: Tag,
    base_dir: str,
    text: str,
) -> None:
    """
    Marks blocks that are essentially a single link with their target.

    A table-of-contents entry is a short paragraph whose text is the anchor's text;
    tagging those (and not every paragraph that happens to contain a link) is what
    makes the book's own contents page navigable without turning prose into links.
    """
    anchors = node.find_all("a", href=True) if node.name != "a" else [node]
    if len(anchors) != 1:
        return

    anchor = anchors[0]
    anchor_text = anchor.get_text(strip=True)
    if not anchor_text or len(anchor_text) < len(text.strip()) * 0.6:
        return

    href = unquote((anchor.get("href") or "").strip())
    if not href or href.startswith(("http://", "https://", "mailto:")):
        return

    target, _, fragment = href.partition("#")
    resolved = (
        posixpath.normpath(posixpath.join(base_dir, target)).lstrip("./")
        if target and base_dir
        else target
    )

    for blk in blocks[start_index:]:
        blk["link_href"] = resolved
        blk["link_fragment"] = fragment


def _extract_clean_inline_text(node: Any) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    parts: List[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            if child.name.lower() in ("rt", "rp"):
                continue
            parts.append(_extract_clean_inline_text(child))
    return "".join(parts)


def _detect_typography_class(tag: Tag) -> str:
    classes = " ".join(tag.get("class", [])).lower()
    style = (tag.get("style", "") or "").lower()
    combined = f"{classes} {style}"
    if any(
        k in combined
        for k in ("bold", "gothic", "strong", "font-weight: 700", "font-weight:bold")
    ):
        return "font-emphasis"
    if any(k in combined for k in ("quote", "italic", "block")):
        return "font-quote"
    return ""


def _append_lines_as_blocks(
    blocks: List[Dict[str, str]], raw_text: str, block_type: str, style_class: str
) -> None:
    for line in raw_text.split("\n"):
        cleaned = line.strip(" \t\r")
        if cleaned:
            blocks.append(
                {
                    "type": block_type,
                    "text": cleaned,
                    "style_class": style_class,
                }
            )


def _merge_soft_wrapped_blocks(blocks: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if not blocks:
        return []

    merged: List[Dict[str, str]] = []
    for blk in blocks:
        if not merged:
            merged.append(dict(blk))
            continue

        prev = merged[-1]
        prev_txt = prev["text"].rstrip()
        curr_txt = blk["text"]

        if prev["type"] != "p" or blk["type"] != "p":
            merged.append(dict(blk))
            continue

        if (
            len(prev_txt) >= 8
            and prev_txt[-1] not in SENTENCE_END_CHARS
            and not curr_txt.startswith(("\u3000", "「", "『", "（", "【", "―", "…"))
        ):
            prev["text"] = prev_txt + curr_txt.lstrip()
        else:
            merged.append(dict(blk))

    return merged


def _find_opf_path(zf: zipfile.ZipFile) -> str:
    try:
        container_xml = zf.read("META-INF/container.xml")
        root = ET.fromstring(container_xml)
        for el in root.findall(".//{*}rootfile"):
            full_path = el.attrib.get("full-path")
            if full_path:
                return full_path
    except Exception:
        pass
    for name in zf.namelist():
        if name.endswith(".opf"):
            return name
    raise ValueError("No .opf file found in EPUB archive.")


def build_toc_entries(
    zf: zipfile.ZipFile,
    opf_root: ET.Element,
    opf_dir: str,
    manifest: Dict[str, Tuple[str, str]],
    href_to_section: Dict[str, int],
) -> List[Dict[str, Any]]:
    """
    The book's own table of contents, flattened with nesting depth, each entry
    resolved to the spine section it points at so it can be jumped to directly.
    """
    entries: List[Dict[str, Any]] = []

    def _add(title: str, raw_href: str, level: int) -> None:
        title = (title or "").strip()
        if not title:
            return
        target, _, fragment = unquote(raw_href or "").partition("#")
        resolved = target
        if target:
            base = posixpath.dirname(raw_href_base[0])
            resolved = (
                posixpath.normpath(posixpath.join(base, target)).lstrip("./")
                if base
                else target
            )
        entries.append(
            {
                "title": title,
                "href": resolved,
                "fragment": fragment,
                "level": level,
                "section_index": href_to_section.get(resolved),
            }
        )

    raw_href_base = [""]

    # EPUB 2: toc.ncx
    for _, (href, media_type) in manifest.items():
        if not (href.endswith(".ncx") or "ncx" in media_type):
            continue
        try:
            ncx_root = ET.fromstring(zf.read(href))
        except Exception:
            continue
        raw_href_base[0] = href

        def _walk_nav(parent: ET.Element, level: int) -> None:
            for nav_point in parent.findall("./{*}navPoint"):
                label = nav_point.find("./{*}navLabel/{*}text")
                content = nav_point.find("./{*}content")
                if label is not None and content is not None:
                    _add(label.text or "", content.attrib.get("src", ""), level)
                _walk_nav(nav_point, level + 1)

        nav_map = ncx_root.find("./{*}navMap")
        if nav_map is not None:
            _walk_nav(nav_map, 0)
        if entries:
            return entries

    # EPUB 3: nav document with epub:type="toc"
    for _, (href, media_type) in manifest.items():
        if "html" not in media_type and not href.endswith((".xhtml", ".html")):
            continue
        try:
            soup = BeautifulSoup(_decode_html_bytes(zf.read(href)), "lxml")
        except Exception:
            continue
        nav = None
        for candidate in soup.find_all("nav"):
            type_attr = (
                candidate.get("epub:type") or candidate.get("type") or ""
            ).lower()
            if "toc" in type_attr:
                nav = candidate
                break
        if nav is None:
            continue
        raw_href_base[0] = href

        def _walk_list(list_tag: Any, level: int) -> None:
            for li in list_tag.find_all("li", recursive=False):
                anchor = li.find("a", href=True)
                if anchor is not None:
                    _add(anchor.get_text(strip=True), anchor["href"], level)
                for sub in li.find_all(["ol", "ul"], recursive=False):
                    _walk_list(sub, level + 1)

        for root_list in nav.find_all(["ol", "ul"], recursive=False):
            _walk_list(root_list, 0)
        if entries:
            return entries

    return entries


def _extract_toc_titles(
    zf: zipfile.ZipFile,
    opf_root: ET.Element,
    opf_dir: str,
    manifest: Dict[str, Tuple[str, str]],
) -> Dict[str, str]:
    toc_map: Dict[str, str] = {}
    for _, (href, media_type) in manifest.items():
        if href.endswith(".ncx") or "ncx" in media_type:
            try:
                ncx_root = ET.fromstring(zf.read(href))
                ncx_dir = posixpath.dirname(href)
                for nav_point in ncx_root.findall(".//{*}navPoint"):
                    label_el = nav_point.find(".//{*}text")
                    content_el = nav_point.find(".//{*}content")
                    if (
                        label_el is not None
                        and label_el.text
                        and content_el is not None
                    ):
                        src = content_el.attrib.get("src", "").split("#")[0]
                        if src:
                            full_src = (
                                posixpath.normpath(posixpath.join(ncx_dir, src))
                                if ncx_dir
                                else src
                            )
                            toc_map[full_src] = label_el.text.strip()
            except Exception:
                pass
    return toc_map


def _guess_chapter_title(zf: zipfile.ZipFile, href: str, idx: int) -> str:
    try:
        raw = zf.read(href)
        html_str = _decode_html_bytes(raw)
        soup = BeautifulSoup(html_str, "lxml")
        for rt in soup.find_all(["rt", "rp"]):
            rt.decompose()
        for tag in ("h1", "h2", "h3", "title"):
            el = soup.find(tag)
            if el and el.get_text(strip=True):
                txt = el.get_text(strip=True)
                if len(txt) <= 60:
                    return txt
    except Exception:
        pass
    return f"Section {idx + 1}"


def _decode_html_bytes(raw_bytes: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "shift_jis", "cp932", "euc-jp"):
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")
