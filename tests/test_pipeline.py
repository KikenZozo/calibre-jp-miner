import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.database import init_app_db
from app.dictionary import init_jmdict_db, lookup_word
from app.epub_parser import parse_epub_chapter_tokenized
from app.japanese_nlp import (
    build_underlined_sentence_html,
    nlp_engine,
    normalize_typography_and_spacing,
    split_japanese_sentences,
)
from scripts.create_sample_epub import create_sample_japanese_epub


def run_tests() -> None:
    init_app_db()
    init_jmdict_db()

    # 1. Test normalisation des espacements d'emphase et polices
    raw_spaced = "「ぜ っ た い に忘れないで！」"
    norm_spaced = normalize_typography_and_spacing(raw_spaced)
    assert "ぜったいに" in norm_spaced, f"Attendu 'ぜったいに', obtenu: {norm_spaced}"
    print("[OK] Normalisation des espacements d'emphase :", norm_spaced)

    # 2. Test découpage de phrases avec dialogues et espaces plein-chasse japonais
    para_dialogue = "「まだ誰も知らない秘密がこの本には書かれているんだ！」と彼女は強い声で言った。しかし彼はすぐにはその言葉を信じなかった。"
    sents = split_japanese_sentences(para_dialogue)
    assert len(sents) == 2, f"Attendu 2 phrases, obtenu {len(sents)}: {sents}"
    assert sents[0] == "「まだ誰も知らない秘密がこの本には書かれているんだ！」と彼女は強い声で言った。"
    print("[OK] Découpage de phrase avec citation intégrée :", sents)

    # 3. Test extraction EPUB complète (avec <ruby><rt>, <span class="font-gothic">, <b>)
    epub_path = create_sample_japanese_epub()
    chap = parse_epub_chapter_tokenized(epub_path, 0)
    assert chap["total_chapters"] == 2
    assert len(chap["blocks"]) >= 4

    # Vérifier que <ruby>図書館<rt>としょかん</rt></ruby> a bien donné "図書館" (sans dupliquer le furigana)
    first_sent = chap["blocks"][1]["sentences"][0]
    assert "図書館としょかん" not in first_sent["text"]
    assert "図書館" in first_sent["text"]
    print("[OK] Nettoyage <ruby>/<rt> et fusion des polices dans l'EPUB :", first_sent["text"])

    # 4. Vérifier le soulignement <u>mot</u> dans la phrase Anki
    target_tok = next(t for t in first_sent["tokens"] if "図書館" in t["surface"])
    s_html, s_plain = build_underlined_sentence_html(
        first_sent["tokens"], target_tok["wid"]
    )
    assert "<u>図書館</u>" in s_html, f"Attendu <u>図書館</u> dans {s_html}"
    print("[OK] Génération de la phrase Anki avec mot souligné :", s_html)

    # 5. Vérifier la recherche JMdict Français + Anglais + Hiragana/Katakana
    res = lookup_word("図書館", "図書館", reading_hiragana="としょかん")
    assert res["reading_hiragana"] == "としょかん"
    assert res["reading_katakana"] == "トショカン"
    assert "bibliothèque" in res["definitions_fr"]
    assert "library" in res["definitions_en"]
    print(
        f"[OK] Dictionnaire JMdict FR/EN : {res['base']} -> "
        f"Hira: {res['reading_hiragana']}, Kata: {res['reading_katakana']}, "
        f"FR: {res['definitions_fr']}, EN: {res['definitions_en']}"
    )

    # 6. Vérifier la création automatique de carte Anki dans le deck Data Mining + Export .apkg
    from app.anki_service import create_and_push_anki_card, export_cards_to_apkg

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
        book_id="demo1",
        book_title="静かな図書館の物語",
        chapter_title="第一章",
        attempt_ankiconnect=False,
    )
    assert card["deck_name"] == "Data Mining"
    assert "<u>図書館</u>" in card["sentence_html"]
    apkg_file = export_cards_to_apkg()
    assert apkg_file.exists() and apkg_file.stat().st_size > 0
    print(
        f"[OK] Carte Anki créée dans le deck '{card['deck_name']}' + paquet .apkg généré ({apkg_file.stat().st_size} octets)"
    )


if __name__ == "__main__":
    run_tests()
