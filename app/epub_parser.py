import posixpath
import re
import warnings
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from app.japanese_nlp import (
    nlp_engine,
    normalize_typography_and_spacing,
    split_japanese_sentences,
)

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


def extract_epub_metadata_and_spine(epub_path: Path) -> Dict[str, Any]:
    """
    Lit le fichier .epub (archive ZIP) directement pour extraire le titre, l'auteur
    et la liste ordonnée des chapitres (spine) avec leurs titres.
    """
    if not epub_path.exists():
        raise FileNotFoundError(f"EPUB introuvable : {epub_path}")

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
        title = title_el.text.strip() if (title_el is not None and title_el.text) else epub_path.stem
        author = creator_el.text.strip() if (creator_el is not None and creator_el.text) else "Auteur inconnu"

        # Manifest : id -> href
        manifest: Dict[str, Tuple[str, str]] = {}
        for item in root.findall(".//{*}manifest/{*}item"):
            item_id = item.attrib.get("id", "")
            href = item.attrib.get("href", "")
            media_type = item.attrib.get("media-type", "")
            if item_id and href:
                full_href = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href
                manifest[item_id] = (full_href, media_type)

        # Table des matières (NCX ou Nav) pour nommer les chapitres proprement
        toc_titles = _extract_toc_titles(zf, root, opf_dir, manifest)

        # Spine : ordre de lecture
        chapters: List[Dict[str, Any]] = []
        spine_items = root.findall(".//{*}spine/{*}itemref")
        for idx, itemref in enumerate(spine_items):
            idref = itemref.attrib.get("idref", "")
            if idref not in manifest:
                continue
            href, media_type = manifest[idref]
            if "html" not in media_type and not href.endswith((".xhtml", ".html", ".htm")):
                continue

            # Vérifie que le chapitre contient du texte lisible
            chap_title = toc_titles.get(href) or _guess_chapter_title(zf, href, idx)
            chapters.append(
                {
                    "index": len(chapters),
                    "href": href,
                    "title": chap_title,
                }
            )

    return {
        "title": title,
        "author": author,
        "chapters": chapters,
        "total_chapters": len(chapters),
    }


def parse_epub_chapter_tokenized(
    epub_path: Path, chapter_index: int
) -> Dict[str, Any]:
    """
    Extrait un chapitre d'un EPUB, nettoie les balises <ruby>/<rt> et les variations
    de polices/espacements, découpe les phrases avec précision et pré-segmente tous
    les mots avec SudachiPy.
    """
    meta = extract_epub_metadata_and_spine(epub_path)
    chapters = meta["chapters"]
    if not chapters:
        return {
            "chapter_index": 0,
            "chapter_title": "Chapitre vide",
            "total_chapters": 0,
            "blocks": [],
            "sentences": {},
        }

    chapter_index = max(0, min(chapter_index, len(chapters) - 1))
    chap_info = chapters[chapter_index]
    href = chap_info["href"]

    with zipfile.ZipFile(str(epub_path), "r") as zf:
        raw_bytes = zf.read(href)

    html_str = _decode_html_bytes(raw_bytes)
    raw_blocks = extract_normalized_blocks_from_html(html_str)

    # Fusion intelligente des lignes coupées au milieu d'une phrase par la mise en page
    merged_blocks = _merge_soft_wrapped_blocks(raw_blocks)

    rendered_blocks: List[Dict[str, Any]] = []
    sentences_map: Dict[str, Dict[str, Any]] = {}
    sentence_counter = 0

    for b_idx, block in enumerate(merged_blocks):
        b_type = block["type"]
        b_text = block["text"]
        b_style = block.get("style_class", "")

        # Préserver l'alinéa japonais (　) en début de paragraphe
        leading_indent = b_text.startswith("\u3000")
        sents = split_japanese_sentences(b_text)
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
                "type": b_type,
                "style_class": b_style,
                "indent": leading_indent,
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


def extract_normalized_blocks_from_html(html_str: str) -> List[Dict[str, str]]:
    """
    Extrait les paragraphes et titres d'un document HTML/XHTML d'EPUB en :
    1. Supprimant toutes les balises <rt> et <rp> (furigana intégrés) pour ne jamais
       casser un mot en deux (ex: <ruby>私<rt>わたし</rt></ruby> -> 私).
    2. Fusionnant les balises inline de police/style (<span class="font-gothic">, <font>,
       <b>, <em>, etc.) pour reconstituer le flux continu de la phrase.
    3. Gérant les <br> utilisés comme séparateurs de lignes dans certains EPUBs.
    """
    soup = BeautifulSoup(html_str, "lxml")

    # Supprimer les éléments non textuels ET les annotations furigana (<rt>, <rp>)
    for unwanted in soup.find_all(["script", "style", "head", "rt", "rp", "svg", "img"]):
        unwanted.decompose()

    body = soup.find("body") or soup

    # Remplacer les <br> par un marqueur spécial de saut de ligne pour les traiter proprement
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

        # Si ce bloc contient lui-même des sous-blocs (<div class="chapter"><p>...</p></div>), on descend
        has_child_blocks = any(
            isinstance(c, Tag) and (c.name.lower() in BLOCK_TAGS or c.name.lower() == "div")
            for c in node.children
        )

        if tag_name in HEADING_TAGS:
            text = _extract_clean_inline_text(node)
            if text.strip():
                blocks.append({"type": tag_name, "text": text, "style_class": "heading"})
            return

        if tag_name in BLOCK_TAGS and not has_child_blocks:
            style_cls = _detect_Typography_class(node)
            text = _extract_clean_inline_text(node)
            if text.strip():
                _append_lines_as_blocks(blocks, text, "p", style_cls)
            return

        if tag_name == "div" and not has_child_blocks:
            style_cls = _detect_Typography_class(node)
            text = _extract_clean_inline_text(node)
            if text.strip():
                _append_lines_as_blocks(blocks, text, "p", style_cls)
            return

        # Parcourir les enfants en regroupant les nœuds inline consécutifs
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


def _extract_clean_inline_text(node: Any) -> str:
    """
    Extrait le texte continu d'un nœud inline (<span style="font-family:..."><!-- -->,
    <ruby>, <b>, <i>) sans insérer d'espaces parasites aux frontières de balises.
    """
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


def _detect_Typography_class(tag: Tag) -> str:
    """Détecte si un bloc utilise une police ou un style particulier (emphase, gras, citation)."""
    classes = " ".join(tag.get("class", [])).lower()
    style = (tag.get("style", "") or "").lower()
    combined = f"{classes} {style}"
    if any(k in combined for k in ("bold", "gothic", "strong", "font-weight: 700", "font-weight:bold")):
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
    """
    Certains EPUBs (ou conversions PDF/ Calibre) coupent une phrase en deux blocs <p> ou <br>
    à cause de la largeur de page ou d'un changement de police :
    Ex:
      Bloc 1: "彼は静かに古い図書館の扉を" (ne finit pas par une ponctuation finale)
      Bloc 2: "開けて中へと足を踏み入れた。" (ne commence pas par un alinéa '　' ni un dialogue '「')
    Cette fonction recolle intelligemment les phrases brisées.
    """
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

        # On ne fusionne jamais les titres (h1..h6)
        if prev["type"] != "p" or blk["type"] != "p":
            merged.append(dict(blk))
            continue

        # Si le bloc précédent ne se termine PAS par une ponctuation de fin de phrase
        # ET que le bloc courant ne commence PAS par un alinéa ('　') ou un guillemet ouvrant ('「', '『')
        # ET que le bloc précédent ressemble à une phrase en cours (plus de 8 caractères)
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
    raise ValueError("Fichier .opf introuvable dans l'archive EPUB.")


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
                    if label_el is not None and label_el.text and content_el is not None:
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
    return f"Chapitre {idx + 1}"


def _decode_html_bytes(raw_bytes: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "shift_jis", "cp932", "euc-jp"):
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")
