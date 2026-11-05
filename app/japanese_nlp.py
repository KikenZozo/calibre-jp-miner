import html
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config import JMDICT_DB_PATH

try:
    from sudachipy import dictionary as sudachi_dict
    from sudachipy import tokenizer as sudachi_tokenizer

    _SUDACHI_AVAILABLE = True
except ImportError:
    _SUDACHI_AVAILABLE = False


POS_EN_MAP = {
    "名詞": "Noun",
    "代名詞": "Pronoun",
    "動詞": "Verb",
    "形容詞": "i-Adjective",
    "形状詞": "na-Adjective",
    "副詞": "Adverb",
    "連体詞": "Adnominal",
    "接続詞": "Conjunction",
    "感動詞": "Interjection",
    "助詞": "Particle",
    "助動詞": "Auxiliary",
    "接頭辞": "Prefix",
    "接尾辞": "Suffix",
}

CONTENT_POS_SET = {"名詞", "動詞", "形容詞", "形状詞", "副詞", "連体詞", "接続詞"}

OPEN_TO_CLOSE_QUOTES = {
    "「": "」",
    "『": "』",
    "（": "）",
    "(": ")",
    "【": "】",
    "〈": "〉",
    "《": "》",
    "［": "］",
    "“": "”",
}
CLOSE_QUOTES = set(OPEN_TO_CLOSE_QUOTES.values())

QUOTE_CONTINUATION_CHARS = set("とてやかのにをはがも、，・でへよりまでくらいほどなど")

TE_FORM_AUX_BASES = {
    "いる",
    "居る",
    "ある",
    "おる",
    "みる",
    "見る",
    "しまう",
    "仕舞う",
    "おく",
    "置く",
    "いく",
    "行く",
    "くる",
    "来る",
    "あげる",
    "くれる",
    "もらう",
    "いただく",
    "くださる",
    "ほしい",
}

DECONJUGATION_RULES = [
    ("ていなかった", "る"),
    ("ていました", "る"),
    ("ていない", "る"),
    ("ていた", "る"),
    ("ている", "る"),
    ("られた", "る"),
    ("られて", "る"),
    ("られる", "る"),
    ("させた", "る"),
    ("させて", "る"),
    ("させる", "る"),
    ("なかった", "る"),
    ("ました", "る"),
    ("ません", "る"),
    ("ないで", "る"),
    ("ない", "る"),
    ("ます", "る"),
    ("よう", "る"),
    ("たい", "る"),
    ("た", "る"),
    ("て", "る"),
    ("かれていた", "く"),
    ("かれている", "く"),
    ("かれた", "く"),
    ("かれる", "く"),
    ("かなかった", "く"),
    ("かない", "く"),
    ("きました", "く"),
    ("きます", "く"),
    ("いた", "く"),
    ("いて", "く"),
    ("がなかった", "ぐ"),
    ("がない", "ぐ"),
    ("いだ", "ぐ"),
    ("いで", "ぐ"),
    ("していた", "す"),
    ("している", "す"),
    ("しなかった", "す"),
    ("しない", "す"),
    ("しました", "す"),
    ("します", "す"),
    ("した", "す"),
    ("して", "す"),
    ("みながら", "む"),
    ("んでいた", "む"),
    ("んでいる", "む"),
    ("まなかった", "む"),
    ("まない", "む"),
    ("みました", "む"),
    ("みます", "む"),
    ("んだ", "む"),
    ("んで", "む"),
    ("られていない", "る"),
    ("られていた", "る"),
    ("られている", "る"),
    ("わなかった", "う"),
    ("わない", "う"),
    ("いました", "う"),
    ("います", "う"),
    ("らなかった", "る"),
    ("らない", "る"),
    ("りました", "る"),
    ("ります", "る"),
    ("った", "う"),
    ("って", "う"),
    ("った", "る"),
    ("って", "る"),
    ("った", "つ"),
    ("って", "つ"),
    ("くなかった", "い"),
    ("くない", "い"),
    ("かった", "い"),
    ("くて", "い"),
]

PARTICLES_SET = {
    "は",
    "が",
    "を",
    "に",
    "で",
    "と",
    "から",
    "まで",
    "より",
    "へ",
    "の",
    "も",
    "や",
    "か",
    "ね",
    "よ",
    "わ",
    "ぞ",
    "ぜ",
    "な",
    "には",
    "とは",
    "では",
    "へと",
    "にも",
    "だけ",
    "しか",
    "ほど",
    "くらい",
    "ぐらい",
    "など",
    "ながら",
    "だ",
    "です",
    "た",
    "て",
}

# Core BCCWJ / JPDB frequency ranks for candidate sorting (lower rank = more common/useful)
CORE_FREQUENCY_RANKS: Dict[str, int] = {
    "私": 15,
    "彼": 42,
    "彼女": 85,
    "人": 12,
    "人間": 140,
    "世界": 165,
    "時間": 95,
    "今日": 110,
    "昨日": 320,
    "明日": 280,
    "今": 28,
    "言葉": 175,
    "本": 190,
    "図書館": 1850,
    "扉": 1420,
    "部屋": 410,
    "窓": 980,
    "光": 620,
    "影": 1150,
    "声": 230,
    "目": 65,
    "手": 58,
    "心": 210,
    "記憶": 890,
    "秘密": 1350,
    "魔法": 1680,
    "物語": 920,
    "文字": 1120,
    "意味": 260,
    "理由": 390,
    "本当": 145,
    "絶対": 540,
    "静か": 670,
    "不思議": 940,
    "古い": 720,
    "新しい": 195,
    "深い": 580,
    "強い": 240,
    "優しい": 830,
    "美しい": 760,
    "重い": 690,
    "温かい": 1480,
    "読む": 310,
    "書く": 270,
    "話す": 220,
    "聞く": 130,
    "見る": 22,
    "食べる": 290,
    "飲む": 480,
    "行く": 35,
    "来る": 40,
    "帰る": 340,
    "開ける": 780,
    "開く": 510,
    "閉じる": 1240,
    "思う": 18,
    "考える": 105,
    "知る": 75,
    "分かる": 115,
    "忘れる": 490,
    "覚える": 560,
    "探す": 640,
    "見つける": 610,
    "待つ": 330,
    "許す": 950,
    "叫ぶ": 1320,
    "笑う": 380,
    "歩く": 460,
    "走る": 530,
    "立ち上がる": 1180,
    "踏み入れる": 3400,
    "始まる": 275,
    "終わる": 285,
    "生きる": 360,
    "選ぶ": 470,
    "変わる": 350,
    "続く": 395,
    "落ちる": 590,
    "置く": 370,
    "信じる": 450,
    "濡らす": 2800,
    "言う": 10,
    "机": 1290,
    "場所": 315,
    "一冊": 2600,
    "夜": 205,
    "朝": 345,
    "空": 520,
    "風": 550,
    "雨": 740,
    "街": 680,
    "道": 305,
    "足": 265,
    "ページ": 1520,
    "コーヒー": 1380,
}


def get_word_frequency_rank(lemma: str) -> int:
    return CORE_FREQUENCY_RANKS.get(lemma, 15000)


def katakana_to_hiragana(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def hiragana_to_katakana(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0x3041 <= code <= 0x3096:
            out.append(chr(code + 0x60))
        else:
            out.append(ch)
    return "".join(out)


def is_japanese_word_char(ch: str) -> bool:
    if not ch:
        return False
    code = ord(ch)
    return (
        (0x3040 <= code <= 0x309F)
        or (0x30A0 <= code <= 0x30FF)
        or (0x4E00 <= code <= 0x9FFF)
        or (0x3400 <= code <= 0x4DBF)
        or ch in ("々", "〆", "ヶ", "ー")
    )


def contains_japanese_chars(text: str) -> bool:
    return any(is_japanese_word_char(c) for c in text)


def normalize_typography_and_spacing(text: str) -> str:
    """
    Normalizes Japanese typographic spacing quirks in EPUB/PDF text:
    1. Strips zero-width characters, BOM, soft hyphens.
    2. Collapses artificial emphasis spacing between isolated CJK/kana characters
       (e.g. 'ぜ っ た い に') while preserving paragraph indents ('　') and spaces after '！'/'？'.
    3. Joins soft line breaks inside paragraphs.
    """
    if not text:
        return ""

    text = re.sub(r"[\u200b\u200c\u200d\ufeff\u00ad]", "", text)

    text = re.sub(
        r"(?<=[\u3000-\u30ff\u4e00-\u9fff\uff01-\uff60])\r?\n\s*(?=[\u3000-\u30ff\u4e00-\u9fff\uff01-\uff60])",
        "",
        text,
    )
    text = re.sub(r"\r?\n", " ", text)

    def _collapse_spaced_run(match: re.Match) -> str:
        raw = match.group(0)
        return re.sub(r"[ \u3000]+", "", raw)

    text = re.sub(
        r"(?<![\u3040-\u30ff\u4e00-\u9fff])(?:[\u3040-\u30ff\u4e00-\u9fff][ \u3000]){2,}[\u3040-\u30ff\u4e00-\u9fff](?![\u3040-\u30ff\u4e00-\u9fff])",
        _collapse_spaced_run,
        text,
    )

    text = re.sub(
        r"(?<=[\u3040-\u30ff\u4e00-\u9fff]) +(?=[\u3040-\u30ff\u4e00-\u9fff])",
        "",
        text,
    )

    return text


def split_japanese_sentences(paragraph_text: str) -> List[str]:
    """
    Splits a paragraph into Japanese sentences while respecting:
    - Nested quotes/brackets (「...」, 『...』, （...）, 【...】)
    - Quoted dialogue followed by a quotative clause (e.g. 「行くよ！」と彼は言った。 -> 1 sentence)
    - Full-width space after '！' or '？' outside quotes.
    """
    text = normalize_typography_and_spacing(paragraph_text)
    if not text.strip():
        return []

    sentences: List[str] = []
    current: List[str] = []
    quote_stack: List[str] = []
    n = len(text)
    i = 0

    while i < n:
        ch = text[i]
        current.append(ch)

        if ch in OPEN_TO_CLOSE_QUOTES:
            quote_stack.append(OPEN_TO_CLOSE_QUOTES[ch])
            i += 1
            continue
        elif ch in CLOSE_QUOTES:
            if quote_stack and quote_stack[-1] == ch:
                quote_stack.pop()
            elif ch in quote_stack:
                quote_stack.clear()

            if not quote_stack and len(current) >= 2 and current[-2] in "。！？!?":
                next_idx = i + 1
                has_space_after = False
                while next_idx < n and text[next_idx] in (" ", "\u3000", "\t"):
                    has_space_after = True
                    next_idx += 1

                if next_idx >= n:
                    sentences.append("".join(current))
                    current = []
                elif has_space_after or text[next_idx] in OPEN_TO_CLOSE_QUOTES:
                    sentences.append("".join(current))
                    current = []
                elif text[next_idx] not in QUOTE_CONTINUATION_CHARS:
                    sentences.append("".join(current))
                    current = []
            i += 1
            continue

        if not quote_stack:
            if ch in ("。", "．", "｡"):
                while i + 1 < n and text[i + 1] in ("。", "．", "」", "』", "）", ")"):
                    i += 1
                    current.append(text[i])
                sentences.append("".join(current))
                current = []
            elif ch in ("！", "？", "!", "?"):
                while i + 1 < n and text[i + 1] in ("！", "？", "!", "?", "…", "‥"):
                    i += 1
                    current.append(text[i])
                if i + 1 >= n or text[i + 1] not in QUOTE_CONTINUATION_CHARS:
                    sentences.append("".join(current))
                    current = []

        i += 1

    if current:
        tail = "".join(current)
        if tail.strip():
            sentences.append(tail)
        elif sentences:
            sentences[-1] += tail

    return sentences


class JapaneseNLPEngine:
    """
    Morphological analyzer powered by SudachiPy (SplitMode.C) with deinflection
    and compound verb/auxiliary aggregation, plus dictionary-driven fallback.
    """

    def __init__(self) -> None:
        self._tokenizer = None
        self._mode = None
        self._lexicon_cache: Optional[Dict[str, Tuple[str, str]]] = None
        if _SUDACHI_AVAILABLE:
            try:
                self._tokenizer = sudachi_dict.Dictionary(dict="core").create()
                self._mode = sudachi_tokenizer.Tokenizer.SplitMode.C
            except Exception:
                self._tokenizer = None

    def analyze_sentence(
        self, sentence_text: str, sentence_id: str
    ) -> Dict[str, Any]:
        if not self._tokenizer:
            return self._dictionary_morphological_analyze(sentence_text, sentence_id)

        morphemes = self._tokenizer.tokenize(sentence_text, self._mode)
        grouped_tokens: List[Dict[str, Any]] = []

        i = 0
        m_len = len(morphemes)
        token_idx = 0

        while i < m_len:
            m = morphemes[i]
            surface = m.surface()
            pos = m.part_of_speech()
            pos0 = pos[0] if pos else "記号"

            if pos0 in ("空白", "補助記号", "記号") or not contains_japanese_chars(surface):
                grouped_tokens.append(
                    {
                        "wid": f"{sentence_id}_t{token_idx}",
                        "sid": sentence_id,
                        "surface": surface,
                        "is_word": False,
                        "is_content_word": False,
                    }
                )
                token_idx += 1
                i += 1
                continue

            base_form = m.dictionary_form()
            norm_form = m.normalized_form()
            base_reading_kata = self._get_base_reading_katakana(base_form, m.reading_form())
            surface_reading_kata = (
                m.reading_form() if m.reading_form() != "*" else hiragana_to_katakana(surface)
            )

            conjugation_parts: List[str] = []

            if pos0 in ("動詞", "形容詞", "形状詞"):
                j = i + 1
                while j < m_len:
                    next_m = morphemes[j]
                    next_pos = next_m.part_of_speech()
                    next_pos0 = next_pos[0] if next_pos else ""
                    next_pos1 = next_pos[1] if len(next_pos) > 1 else ""
                    next_surf = next_m.surface()
                    next_base = next_m.dictionary_form()

                    if next_pos0 == "助動詞":
                        surface += next_surf
                        surface_reading_kata += (
                            next_m.reading_form()
                            if next_m.reading_form() != "*"
                            else hiragana_to_katakana(next_surf)
                        )
                        conjugation_parts.append(next_base)
                        j += 1
                        continue

                    if next_pos0 == "助詞" and next_pos1 == "接続助詞" and next_surf in (
                        "て",
                        "で",
                        "ちゃ",
                        "じゃ",
                        "たり",
                        "だり",
                        "たら",
                        "だら",
                        "ば",
                        "ながら",
                    ):
                        surface += next_surf
                        surface_reading_kata += (
                            next_m.reading_form()
                            if next_m.reading_form() != "*"
                            else hiragana_to_katakana(next_surf)
                        )
                        conjugation_parts.append(next_surf)
                        j += 1

                        if j < m_len:
                            aux_m = morphemes[j]
                            aux_pos0 = aux_m.part_of_speech()[0]
                            if (
                                aux_pos0 in ("動詞", "形容詞")
                                and aux_m.dictionary_form() in TE_FORM_AUX_BASES
                            ):
                                surface += aux_m.surface()
                                surface_reading_kata += (
                                    aux_m.reading_form()
                                    if aux_m.reading_form() != "*"
                                    else hiragana_to_katakana(aux_m.surface())
                                )
                                conjugation_parts.append(aux_m.dictionary_form())
                                j += 1
                        continue

                    if next_pos0 == "接尾辞" and next_pos1 in (
                        "動詞的",
                        "形容詞的",
                        "形状詞的",
                    ):
                        surface += next_surf
                        surface_reading_kata += (
                            next_m.reading_form()
                            if next_m.reading_form() != "*"
                            else hiragana_to_katakana(next_surf)
                        )
                        conjugation_parts.append(next_base)
                        j += 1
                        continue

                    if (
                        next_pos0 == "形容詞"
                        and next_base == "ない"
                        and next_surf.startswith("な")
                    ):
                        surface += next_surf
                        surface_reading_kata += (
                            next_m.reading_form()
                            if next_m.reading_form() != "*"
                            else hiragana_to_katakana(next_surf)
                        )
                        conjugation_parts.append("ない")
                        j += 1
                        continue

                    break

                i = j
            else:
                i += 1

            pos_en = POS_EN_MAP.get(pos0, pos0)
            if conjugation_parts and surface != base_form:
                pos_detail = f"{pos_en} (inflected from {base_form})"
            else:
                pos_detail = pos_en

            base_hira = katakana_to_hiragana(base_reading_kata)
            base_kata = hiragana_to_katakana(base_reading_kata)
            surf_hira = katakana_to_hiragana(surface_reading_kata)
            surf_kata = hiragana_to_katakana(surface_reading_kata)
            is_content = pos0 in CONTENT_POS_SET and base_form not in PARTICLES_SET

            grouped_tokens.append(
                {
                    "wid": f"{sentence_id}_t{token_idx}",
                    "sid": sentence_id,
                    "surface": surface,
                    "base": base_form,
                    "normalized": norm_form,
                    "reading_hiragana": base_hira,
                    "reading_katakana": base_kata,
                    "surface_hiragana": surf_hira,
                    "surface_katakana": surf_kata,
                    "pos": pos0,
                    "pos_detail": pos_detail,
                    "frequency_rank": get_word_frequency_rank(base_form),
                    "is_word": True,
                    "is_content_word": is_content,
                }
            )
            token_idx += 1

        return {
            "sid": sentence_id,
            "text": sentence_text,
            "tokens": grouped_tokens,
        }

    def _get_base_reading_katakana(self, base_form: str, fallback_reading: str) -> str:
        if not self._tokenizer:
            return hiragana_to_katakana(base_form)
        try:
            ms = self._tokenizer.tokenize(base_form, self._mode)
            if ms:
                r = "".join(
                    m.reading_form()
                    if m.reading_form() != "*"
                    else hiragana_to_katakana(m.surface())
                    for m in ms
                )
                if r:
                    return r
        except Exception:
            pass
        return fallback_reading if fallback_reading != "*" else hiragana_to_katakana(base_form)

    def _load_lexicon(self) -> Dict[str, Tuple[str, str]]:
        if self._lexicon_cache is not None:
            return self._lexicon_cache
        lex: Dict[str, Tuple[str, str]] = {}
        if JMDICT_DB_PATH.exists():
            try:
                conn = sqlite3.connect(str(JMDICT_DB_PATH))
                rows = conn.execute(
                    "SELECT kanji, reading_hira, pos FROM entries ORDER BY priority DESC"
                ).fetchall()
                conn.close()
                for k, h, p in rows:
                    if k not in lex:
                        lex[k] = (h, p)
                    if h not in lex:
                        lex[h] = (h, p)
            except Exception:
                pass
        self._lexicon_cache = lex
        return lex

    def _dictionary_morphological_analyze(
        self, sentence_text: str, sentence_id: str
    ) -> Dict[str, Any]:
        lex = self._load_lexicon()
        tokens: List[Dict[str, Any]] = []
        n = len(sentence_text)
        i = 0
        token_idx = 0

        while i < n:
            ch = sentence_text[i]

            if not is_japanese_word_char(ch):
                j = i + 1
                while j < n and not is_japanese_word_char(sentence_text[j]):
                    j += 1
                surf = sentence_text[i:j]
                tokens.append(
                    {
                        "wid": f"{sentence_id}_t{token_idx}",
                        "sid": sentence_id,
                        "surface": surf,
                        "is_word": False,
                        "is_content_word": False,
                    }
                )
                token_idx += 1
                i = j
                continue

            matched_token = None
            max_len = min(12, n - i)
            for length in range(max_len, 0, -1):
                candidate = sentence_text[i : i + length]
                if not all(is_japanese_word_char(c) for c in candidate):
                    continue

                if candidate in lex:
                    hira, pos = lex[candidate]
                    pos_en = POS_EN_MAP.get(pos, pos or "Word")
                    matched_token = (candidate, candidate, hira, pos, pos_en, length)
                    break

                for surf_suffix, dict_ending in DECONJUGATION_RULES:
                    if candidate.endswith(surf_suffix) and len(candidate) > len(surf_suffix):
                        stem = candidate[: -len(surf_suffix)]
                        base_candidate = stem + dict_ending
                        if base_candidate in lex:
                            hira, pos = lex[base_candidate]
                            pos_en = POS_EN_MAP.get(pos, "Verb")
                            pos_detail = f"{pos_en} (inflected from {base_candidate})"
                            matched_token = (
                                candidate,
                                base_candidate,
                                hira,
                                pos,
                                pos_detail,
                                length,
                            )
                            break
                if matched_token:
                    break

            if matched_token:
                surf, base, hira, pos, pos_detail, step = matched_token
                kata = hiragana_to_katakana(hira)
                is_content = (pos in CONTENT_POS_SET or not pos) and base not in PARTICLES_SET
                tokens.append(
                    {
                        "wid": f"{sentence_id}_t{token_idx}",
                        "sid": sentence_id,
                        "surface": surf,
                        "base": base,
                        "normalized": base,
                        "reading_hiragana": hira,
                        "reading_katakana": kata,
                        "surface_hiragana": hira,
                        "surface_katakana": kata,
                        "pos": pos,
                        "pos_detail": pos_detail,
                        "frequency_rank": get_word_frequency_rank(base),
                        "is_word": True,
                        "is_content_word": is_content,
                    }
                )
                token_idx += 1
                i += step
                continue

            if 0x4E00 <= ord(ch) <= 0x9FFF or ch == "々":
                j = i + 1
                while j < n and (0x4E00 <= ord(sentence_text[j]) <= 0x9FFF or sentence_text[j] == "々"):
                    j += 1
                surf = sentence_text[i:j]
                pos_detail = "Noun"
                is_content = True
            elif 0x30A0 <= ord(ch) <= 0x30FF:
                j = i + 1
                while j < n and (0x30A0 <= ord(sentence_text[j]) <= 0x30FF or sentence_text[j] == "ー"):
                    j += 1
                surf = sentence_text[i:j]
                pos_detail = "Noun (Katakana)"
                is_content = True
            else:
                surf = ch
                for p_len in (3, 2, 1):
                    if i + p_len <= n and sentence_text[i : i + p_len] in PARTICLES_SET:
                        surf = sentence_text[i : i + p_len]
                        break
                j = i + len(surf)
                is_content = surf not in PARTICLES_SET and len(surf) > 1
                pos_detail = "Particle" if surf in PARTICLES_SET else "Word"

            hira = katakana_to_hiragana(surf)
            kata = hiragana_to_katakana(surf)
            tokens.append(
                {
                    "wid": f"{sentence_id}_t{token_idx}",
                    "sid": sentence_id,
                    "surface": surf,
                    "base": surf,
                    "normalized": surf,
                    "reading_hiragana": hira,
                    "reading_katakana": kata,
                    "surface_hiragana": hira,
                    "surface_katakana": kata,
                    "pos": pos_detail,
                    "pos_detail": pos_detail,
                    "frequency_rank": get_word_frequency_rank(surf),
                    "is_word": True,
                    "is_content_word": is_content,
                }
            )
            token_idx += 1
            i = j

        return {"sid": sentence_id, "text": sentence_text, "tokens": tokens}


def build_underlined_sentence_html(
    tokens: List[Dict[str, Any]], target_wid: str
) -> Tuple[str, str]:
    """
    Reconstructs the full context sentence with the target word underlined (<u>...</u>).
    Returns (sentence_html, sentence_plain).
    """
    html_parts: List[str] = []
    plain_parts: List[str] = []

    for tok in tokens:
        surf = tok.get("surface", "")
        plain_parts.append(surf)
        escaped = html.escape(surf)
        if tok.get("wid") == target_wid:
            html_parts.append(f"<u>{escaped}</u>")
        else:
            html_parts.append(escaped)

    return "".join(html_parts), "".join(plain_parts)


def find_i_plus_one_candidates(
    sentences: List[Dict[str, Any]],
    known_words: Set[str],
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Batch i+1 sentence mining engine:
    1. Counts book-wide occurrences of every content lemma.
    2. Filters sentences that have realistic length (8..90 chars) and contain
       exactly 1 unknown content word (or ifknown_words is empty, surfaces top high-value
       content words with 1 target per sentence).
    3. Ranks candidates by frequency_rank and book_occurrences.
    """
    lemma_book_counts: Dict[str, int] = {}
    for sent in sentences:
        for tok in sent.get("tokens", []):
            if tok.get("is_word") and tok.get("is_content_word", True):
                base = tok.get("base") or tok.get("surface", "")
                if base and base not in PARTICLES_SET:
                    lemma_book_counts[base] = lemma_book_counts.get(base, 0) + 1

    candidates_by_lemma: Dict[str, Dict[str, Any]] = {}
    has_known_baseline = len(known_words) > 0

    for sent in sentences:
        text = sent.get("text", "").strip()
        if len(text) < 6 or len(text) > 110:
            continue

        tokens = sent.get("tokens", [])
        content_tokens = [
            t
            for t in tokens
            if t.get("is_word")
            and t.get("is_content_word", True)
            and (t.get("base") or t.get("surface")) not in PARTICLES_SET
        ]
        if not content_tokens:
            continue

        unknown_tokens = [
            t
            for t in content_tokens
            if (t.get("base") not in known_words and t.get("surface") not in known_words)
        ]

        # Strict i+1 when user has a known words baseline; otherwise pick best target word per sentence
        if has_known_baseline:
            if len(unknown_tokens) != 1:
                continue
            target_tokens = unknown_tokens
        else:
            target_tokens = sorted(
                content_tokens, key=lambda t: get_word_frequency_rank(t.get("base", ""))
            )[:1]

        for target in target_tokens:
            lemma = target.get("base") or target.get("surface", "")
            if not lemma or len(lemma) == 1 and not (0x4E00 <= ord(lemma[0]) <= 0x9FFF):
                continue
            if lemma in candidates_by_lemma:
                continue

            s_html, s_plain = build_underlined_sentence_html(tokens, target["wid"])
            freq_rank = get_word_frequency_rank(lemma)
            book_count = lemma_book_counts.get(lemma, 1)

            candidates_by_lemma[lemma] = {
                "sid": sent["sid"],
                "wid": target["wid"],
                "section_index": sent.get("section_index", 0),
                "section_title": sent.get("section_title", ""),
                "surface": target["surface"],
                "base": lemma,
                "reading_hiragana": target.get("reading_hiragana", ""),
                "reading_katakana": target.get("reading_katakana", ""),
                "pos_detail": target.get("pos_detail", ""),
                "frequency_rank": freq_rank,
                "book_occurrences": book_count,
                "is_strict_i_plus_one": len(unknown_tokens) == 1,
                "sentence_html": s_html,
                "sentence_plain": s_plain,
                "sentence_tokens": tokens,
            }

    ranked = sorted(
        candidates_by_lemma.values(),
        key=lambda c: (c["frequency_rank"], -c["book_occurrences"]),
    )
    return ranked[:limit]


nlp_engine = JapaneseNLPEngine()
