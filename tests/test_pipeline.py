import sys
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.anki_service import create_and_push_anki_card, export_cards_to_apkg, export_cards_to_tsv
from app.database import init_app_db, is_book_extracted, upsert_known_words
from app.dictionary import init_jmdict_db, lookup_word
from app.epub_parser import parse_epub_chapter_tokenized
from app.japanese_nlp import (
    build_underlined_sentence_html,
    find_i_plus_one_candidates,
    normalize_typography_and_spacing,
    split_japanese_sentences,
)
from scripts.create_sample_epub import create_sample_japanese_epub


def run_tests() -> None:
    init_app_db()
    init_jmdict_db()

    # 1. Test typography & emphasis spacing normalization
    raw_spaced = "「ぜ っ た い に忘れないで！」"
    norm_spaced = normalize_typography_and_spacing(raw_spaced)
    assert "ぜったいに" in norm_spaced, f"Expected 'ぜったいに', got: {norm_spaced}"
    print("[OK] Emphasis spacing normalization:", norm_spaced)

    # 2. Test Japanese sentence splitting with nested dialogue and full-width space
    para_dialogue = "「まだ誰も知らない秘密がこの本には書かれているんだ！」と彼女は強い声で言った。しかし彼はすぐにはその言葉を信じなかった。"
    sents = split_japanese_sentences(para_dialogue)
    assert len(sents) == 2, f"Expected 2 sentences, got {len(sents)}: {sents}"
    assert sents[0] == "「まだ誰も知らない秘密がこの本には書かれているんだ！」と彼女は強い声で言った。"
    print("[OK] Dialogue sentence boundary detection:", sents)

    # 3. Test EPUB extraction (<ruby>/<rt> stripping and inline font merging)
    epub_path = create_sample_japanese_epub()
    chap = parse_epub_chapter_tokenized(epub_path, 0)
    assert chap["total_chapters"] == 2
    assert len(chap["blocks"]) >= 4

    first_sent = chap["blocks"][1]["sentences"][0]
    assert "図書館としょかん" not in first_sent["text"]
    assert "図書館" in first_sent["text"]
    print("[OK] EPUB <ruby>/<rt> stripping and inline span merging:", first_sent["text"])

    # 4. Test underlined context sentence generation (<u>word</u>)
    target_tok = next(t for t in first_sent["tokens"] if "図書館" in t["surface"])
    s_html, s_plain = build_underlined_sentence_html(
        first_sent["tokens"], target_tok["wid"]
    )
    assert "<u>図書館</u>" in s_html, f"Expected <u>図書館</u> in {s_html}"
    print("[OK] Underlined Anki sentence generation:", s_html)

    # 5. Test JMdict lookup (Hiragana, Katakana, FR/EN glosses)
    res = lookup_word("図書館", "図書館", reading_hiragana="としょかん")
    assert res["reading_hiragana"] == "としょかん"
    assert res["reading_katakana"] == "トショカン"
    assert "bibliothèque" in res["definitions_fr"]
    assert "library" in res["definitions_en"]
    print(
        f"[OK] JMdict lookup: {res['base']} -> "
        f"Hira: {res['reading_hiragana']}, Kata: {res['reading_katakana']}"
    )

    # 6. Test batch i+1 candidate ranking with known vocabulary diffing
    all_chap_sents = list(chap["sentences"].values())
    upsert_known_words(["彼", "静か", "古い", "重い", "扉", "開ける", "深い", "影", "中", "足"], source="test")
    candidates = find_i_plus_one_candidates(all_chap_sents, set())
    assert len(candidates) > 0
    print(f"[OK] Batch i+1 candidate ranking produced {len(candidates)} ranked sentences")

    # 7. Test deterministic GUID Anki card creation + .apkg and .tsv export
    card = create_and_push_anki_card(
        word_surface=target_tok["surface"],
        word_base=target_tok["base"],
        reading_hiragana=res["reading_hiragana"],
        reading_katakana=res["reading_katakana"],
        pos_detail=target_tok["pos_detail"],
        definitions_fr=res["definitions_fr"],
        definitions_en=res["definitions_en"],
        sentence_html=s_html,
        sentence_plain=s_plain,
        book_id="urn:uuid:yomicalibre-sample-001",
        book_title="静かな図書館の物語",
        chapter_title="第一章",
        attempt_ankiconnect=False,
    )
    assert card["deck_name"] == "Data Mining"
    assert len(card["guid"]) == 10
    assert "<u>図書館</u>" in card["sentence_html"]
    apkg_file = export_cards_to_apkg()
    tsv_file = export_cards_to_tsv()
    assert apkg_file.exists() and apkg_file.stat().st_size > 0
    assert tsv_file.exists() and tsv_file.stat().st_size > 0
    print(
        f"[OK] Deterministic GUID Anki card (guid={card['guid']}) + .apkg ({apkg_file.stat().st_size}B) + .tsv exported"
    )


def _write_fake_calibre_db(db_path: Path, book_count: int = 120) -> None:
    """Minimal Calibre metadata.db with enough books to trigger the listing limit."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE books(id INTEGER PRIMARY KEY, uuid TEXT, title TEXT, path TEXT, timestamp TEXT);
        CREATE TABLE data(id INTEGER PRIMARY KEY, book INTEGER, format TEXT, name TEXT, uncompressed_size INTEGER);
        CREATE TABLE authors(id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE books_authors_link(id INTEGER PRIMARY KEY, book INTEGER, author INTEGER);
        """
    )
    conn.execute("INSERT INTO authors VALUES (1, 'テスト作家')")
    for i in range(1, book_count + 1):
        conn.execute(
            "INSERT INTO books VALUES (?, ?, ?, ?, ?)",
            (i, f"uuid-{i:04d}", f"蔵書{i:03d}", f"author/book{i}", f"2024-01-{(i % 28) + 1:02d}"),
        )
        conn.execute(
            "INSERT INTO data VALUES (?, ?, 'EPUB', ?, 500000)", (i, i, f"book{i}")
        )
        conn.execute("INSERT INTO books_authors_link VALUES (?, ?, 1)", (i, i))
    conn.commit()
    conn.close()


def _build_test_pdf(path: Path, scanned: bool = False) -> bool:
    """Writes a small Japanese PDF, optionally flattened to an image-only scan."""
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return False

    lines = [
        "彼は静かに古い図書館の重い扉を開けて、",
        "深い影の中へと足を踏み入れた。",
        "窓から優しい光が落ちて、机の上には",
        "一冊の新しい物語の本が置かれていた。",
    ]
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=595)
    y = 60
    for line in lines:
        page.insert_text((50, y), line, fontname="japan", fontsize=13)
        y += 30

    if scanned:
        pix = page.get_pixmap(dpi=200)
        flat = pymupdf.open()
        flat_page = flat.new_page(width=page.rect.width, height=page.rect.height)
        flat_page.insert_image(flat_page.rect, pixmap=pix)
        flat.save(str(path))
        flat.close()
    else:
        doc.save(str(path))
    doc.close()
    return True


def run_pdf_and_library_tests() -> None:
    import tempfile

    from app import ocr as ocr_module
    from app.calibre_library import local_book_id_for
    from app.pdf_layout import build_page_layout, get_pdf_info, pymupdf_available

    # 8. Regression: an upload must stay visible behind a large Calibre library.
    #    The listing used to be truncated to `limit` BEFORE sorting, and uploads were
    #    appended last, so with >= limit books in metadata.db a freshly imported file
    #    silently never appeared and looked like "the import does not load".
    create_sample_japanese_epub()
    sample_id = local_book_id_for("Sample_Shizuka_na_Toshokan.epub")

    with tempfile.TemporaryDirectory() as fake_library:
        _write_fake_calibre_db(Path(fake_library) / "metadata.db", book_count=120)

        import app.calibre_library as calibre_library

        original_root = calibre_library.CALIBRE_LIBRARY_PATH
        calibre_library.CALIBRE_LIBRARY_PATH = Path(fake_library)
        try:
            listed = calibre_library.list_all_books(limit=100)
            assert len(listed) == 100, f"limit not applied, got {len(listed)}"
            ids = {b["book_id"] for b in listed}
            assert sample_id in ids, (
                "uploaded book was truncated away by a large Calibre library "
                "(the ordering regression)"
            )
            assert listed[0].get("progress") or listed[0].get("is_upload"), (
                "uploads / in-progress books must sort first"
            )

            descriptor = calibre_library.get_book_descriptor("uuid-0042")
            assert descriptor and descriptor["title"] == "蔵書042"
        finally:
            calibre_library.CALIBRE_LIBRARY_PATH = original_root

    print("[OK] Upload stays visible behind 120 Calibre books (ordering regression)")

    if not pymupdf_available():
        print("[SKIP] PyMuPDF missing: PDF word-layer tests skipped")
        return

    with tempfile.TemporaryDirectory() as tmp:
        # 9. PDF text layer -> SudachiPy words positioned on the page
        pdf_path = Path(tmp) / "digital.pdf"
        _build_test_pdf(pdf_path)
        info = get_pdf_info(pdf_path)
        assert info["page_count"] == 1
        assert info["has_text_layer"], "text-layer PDF misdetected as a scan"

        layout = build_page_layout(pdf_path, 0, "test-pdf-digital", book_title="digital")
        assert layout["source"] == "text-layer", layout["source"]
        assert layout["words"], "no clickable words produced from the text layer"
        assert layout["sentences"], "no sentences produced from the text layer"

        for word in layout["words"]:
            for x0, y0, x1, y1 in word["rects"]:
                assert 0 <= x0 < x1 <= 1.001, f"bad x range for {word['surface']}"
                assert 0 <= y0 < y1 <= 1.001, f"bad y range for {word['surface']}"

        surfaces = {w["surface"] for w in layout["words"]}
        assert "図書館" in surfaces, f"expected 図書館 among {sorted(surfaces)[:12]}"
        target = next(w for w in layout["words"] if w["surface"] == "図書館")
        assert target["reading_hiragana"] == "としょかん"
        assert target["sid"] in layout["sentences"]
        print(
            f"[OK] PDF text layer -> {len(layout['words'])} positioned words "
            f"(図書館 at {[round(v, 3) for v in target['rects'][0]]})"
        )

        # 10. OCR fallback for an image-only scan
        scan_path = Path(tmp) / "scan.pdf"
        _build_test_pdf(scan_path, scanned=True)
        scan_info = get_pdf_info(scan_path)
        assert not scan_info["has_text_layer"], "image-only scan misdetected as text"

        scan_layout = build_page_layout(scan_path, 0, "test-pdf-scan", book_title="scan")
        if ocr_module.is_available():
            assert scan_layout["source"] == "ocr", scan_layout["source"]
            assert scan_layout["words"], "OCR produced no clickable words"
            # Boxes must not swallow their neighbours on a line.
            first_line = sorted(
                (w for w in scan_layout["words"] if w["rects"]),
                key=lambda w: (round(w["rects"][0][1], 2), w["rects"][0][0]),
            )[:4]
            for a, b in zip(first_line, first_line[1:]):
                if abs(a["rects"][0][1] - b["rects"][0][1]) < 0.01:
                    assert a["rects"][0][2] <= b["rects"][0][2] + 0.001, (
                        f"overlapping OCR boxes: {a['surface']} / {b['surface']}"
                    )
            print(
                f"[OK] Scanned PDF -> OCR ({scan_layout['ocr_lang']}) produced "
                f"{len(scan_layout['words'])} positioned words"
            )
        else:
            assert scan_layout["source"] == "none"
            assert scan_layout["ocr_reason"], "missing explanation when OCR is unavailable"
            print(f"[SKIP] Tesseract unavailable: {scan_layout['ocr_reason']}")


def _build_illustrated_epub(path: Path) -> None:
    """An EPUB with illustrations, a contents page of links, and an NCX."""
    import struct
    import zipfile
    import zlib

    def png(w: int, h: int) -> bytes:
        def chunk(tag: bytes, data: bytes) -> bytes:
            body = tag + data
            return (
                struct.pack(">I", len(data))
                + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
            )

        raw = b"".join(b"\x00" + bytes((200, 120, 60)) * w for _ in range(h))
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )

    chapter = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja"><head><title>第一章</title></head>
<body><h1>第一章</h1>
<p>　彼は静かに古い図書館の重い扉を開けて、深い影の中へと足を踏み入れた。</p>
<div><img src="../images/plate1.png" alt="挿絵"/></div>
<p>　窓から優しい光が落ちて、机の上には一冊の新しい物語の本が置かれていた。</p>
</body></html>"""

    toc_page = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja"><head><title>目次</title></head>
<body><h1>目次</h1><p><a href="chapter1.xhtml">第一章：古い扉</a></p></body></html>"""

    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>挿絵テスト</dc:title>
<dc:creator>テスト</dc:creator><dc:language>ja</dc:language>
<dc:identifier id="BookId">urn:uuid:illus-test</dc:identifier></metadata>
<manifest>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="tocpage" href="text/toc_page.xhtml" media-type="application/xhtml+xml"/>
<item id="c1" href="text/chapter1.xhtml" media-type="application/xhtml+xml"/>
<item id="img1" href="images/plate1.png" media-type="image/png"/>
</manifest>
<spine toc="ncx"><itemref idref="tocpage"/><itemref idref="c1"/></spine></package>"""

    ncx = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/>
<docTitle><text>挿絵テスト</text></docTitle><navMap>
<navPoint id="n0" playOrder="1"><navLabel><text>目次</text></navLabel><content src="text/toc_page.xhtml"/></navPoint>
<navPoint id="n1" playOrder="2"><navLabel><text>第一章</text></navLabel><content src="text/chapter1.xhtml"/></navPoint>
</navMap></ncx>"""

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/toc.ncx", ncx)
        zf.writestr("OEBPS/text/toc_page.xhtml", toc_page)
        zf.writestr("OEBPS/text/chapter1.xhtml", chapter)
        zf.writestr("OEBPS/images/plate1.png", png(8, 6))


def run_illustration_toc_and_vocabulary_tests() -> None:
    import tempfile

    from app.database import (
        get_cached_book_section,
        get_vocabulary_state,
        set_word_known,
        upsert_known_words,
    )
    from app.epub_parser import EXTRACTION_VERSION, extract_book_into_cache

    with tempfile.TemporaryDirectory() as tmp:
        epub_path = Path(tmp) / "illustrated.epub"
        _build_illustrated_epub(epub_path)

        book = {
            "book_uuid": "test-illustrated",
            "file_path": str(epub_path),
            "preferred_format": "EPUB",
            "title": "挿絵テスト",
            "author": "テスト",
            "calibre_id": 0,
            "uncompressed_size": epub_path.stat().st_size,
        }
        extract_book_into_cache(book)

        # 11. Illustrations survive extraction instead of being stripped with the markup
        chapter = get_cached_book_section("test-illustrated", 1)
        assert chapter is not None
        image_blocks = [b for b in chapter["blocks"] if b["type"] == "image"]
        assert image_blocks, "illustration was dropped during extraction"
        assert image_blocks[0]["src"] == "OEBPS/images/plate1.png", image_blocks[0]["src"]
        assert image_blocks[0]["alt"] == "挿絵"
        with zipfile.ZipFile(epub_path) as zf:
            assert image_blocks[0]["src"] in zf.namelist(), "image path is not in the EPUB"
        print(f"[OK] Illustration kept and resolvable: {image_blocks[0]['src']}")

        # text around the image is still segmented
        text_blocks = [b for b in chapter["blocks"] if b.get("sentences")]
        assert len(text_blocks) >= 2, "text around the illustration was lost"

        # 12. The book's own contents page is clickable
        toc_page = get_cached_book_section("test-illustrated", 0)
        linked = [b for b in toc_page["blocks"] if b.get("link_href")]
        assert linked, "contents-page link was not detected"
        assert linked[0]["link_href"] == "OEBPS/text/chapter1.xhtml"
        assert linked[0]["link_target"] == 1, linked[0]["link_target"]
        print(f"[OK] Contents link resolves to section {linked[0]['link_target']}")

        # 13. The NCX table of contents is parsed and addressable
        toc = chapter["toc"]
        assert len(toc) == 2, toc
        assert [t["title"] for t in toc] == ["目次", "第一章"]
        assert [t["section_index"] for t in toc] == [0, 1], toc
        print(f"[OK] Table of contents: {len(toc)} entries resolved to sections")

        # 14. A cache written by an older extractor is treated as stale
        assert is_book_extracted("test-illustrated", EXTRACTION_VERSION)
        assert not is_book_extracted("test-illustrated", EXTRACTION_VERSION + 1), (
            "a stale cache must be re-extracted, not served as-is"
        )
        print("[OK] Stale extraction cache is re-extracted rather than served")

    # 15. Vocabulary states, and the precedence between them
    set_word_known("図書館", True)
    set_word_known("物語", False)
    upsert_known_words(["物語", "窓"], source="anki")  # an Anki sync must not override

    state = get_vocabulary_state()
    assert "図書館" in state["known"]
    assert "窓" in state["known"]
    assert "物語" in state["unknown"], "explicit 'not known' was overwritten by Anki"
    assert "物語" not in state["known"]

    set_word_known("物語", True)
    assert "物語" in get_vocabulary_state()["known"], "a word could not be re-marked known"
    print("[OK] Known / not-known marking survives an Anki sync and is reversible")


if __name__ == "__main__":
    run_tests()
    run_pdf_and_library_tests()
    run_illustration_toc_and_vocabulary_tests()
