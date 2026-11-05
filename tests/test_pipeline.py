import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.anki_service import create_and_push_anki_card, export_cards_to_apkg, export_cards_to_tsv
from app.database import init_app_db, upsert_known_words
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


if __name__ == "__main__":
    run_tests()
