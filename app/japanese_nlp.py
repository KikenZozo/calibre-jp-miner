import html
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from app.config import JMDICT_DB_PATH

try:
    from sudachipy import dictionary as sudachi_dict
    from sudachipy import tokenizer as sudachi_tokenizer

    _SUDACHI_AVAILABLE = True
except ImportError:
    _SUDACHI_AVAILABLE = False


# Mapping des parties du discours (POS) de Sudachi/UniDic vers le français
POS_FR_MAP = {
    "名詞": "Nom",
    "代名詞": "Pronom",
    "動詞": "Verbe",
    "形容詞": "Adjectif (-i)",
    "形状詞": "Adjectif (-na)",
    "副詞": "Adverbe",
    "連体詞": "Adnominal",
    "接続詞": "Conjonction",
    "感動詞": "Interjection",
    "助詞": "Particule",
    "助動詞": "Auxiliaire",
    "接頭辞": "Préfixe",
    "接尾辞": "Suffixe",
}

# Paires de guillemets et parenthèses japonaises
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

# Particules ou continuations qui rattachent une citation à la proposition suivante (ex: 「行くよ！」と彼は言った。)
QUOTE_CONTINUATION_CHARS = set("とてやかのにをはがも、，・でへよりまでくらいほどなど")

# Auxiliaires courants après la forme en -te/-de (ex: 読んでいる, 食べてしまう)
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

# Règles de déconjugaison japonaise (suffixe de surface -> (terminaison dictionnaire, lecture suffixe))
DECONJUGATION_RULES = [
    # Ichidan (-ru) & formes composées
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
    # Godan -ku (書く -> 書いた / 書かない / 書かれている)
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
    # Godan -gu (泳ぐ -> 泳いだ)
    ("がなかった", "ぐ"),
    ("がない", "ぐ"),
    ("いだ", "ぐ"),
    ("いで", "ぐ"),
    # Godan -su (話す / 濡らす -> 濡らしていた)
    ("していた", "す"),
    ("している", "す"),
    ("しなかった", "す"),
    ("しない", "す"),
    ("しました", "す"),
    ("します", "す"),
    ("した", "す"),
    ("して", "す"),
    # Godan -mu / -bu / -nu (読む / 飲む -> 飲みながら)
    ("みながら", "む"),
    ("んでいた", "む"),
    ("んでいる", "む"),
    ("まなかった", "む"),
    ("まない", "む"),
    ("みました", "む"),
    ("みます", "む"),
    ("んだ", "む"),
    ("んで", "む"),
    # Godan -u / -tsu / -ru (言う / 思う / 知る -> 知られていない / 知らない)
    ("られていない", "る"),
    ("られていた", "る"),
    ("られている", "る"),
    ("らながら", "る"),
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
    # Adjectifs -i (古い -> 古かった / 古くない)
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
    "から",
    "だけ",
    "しか",
    "ほど",
    "くらい",
    "ぐらい",
    "など",
    "ながら",
}


def katakana_to_hiragana(text: str) -> str:
    """Convertit les katakana en hiragana tout en préservant les autres caractères (ex: ー)."""
    out = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def hiragana_to_katakana(text: str) -> str:
    """Convertit les hiragana en katakana."""
    out = []
    for ch in text:
        code = ord(ch)
        if 0x3041 <= code <= 0x3096:
            out.append(chr(code + 0x60))
        else:
            out.append(ch)
    return "".join(out)


def is_japanese_word_char(ch: str) -> bool:
    """Vérifie si un caractère est un Kanji, Hiragana, Katakana ou caractère d'itération."""
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
    Normalise les espacements typographiques japonais problématiques dans les EPUBs :
    1. Supprime les caractères invisibles (zero-width spaces, BOM, soft-hyphens).
    2. Fusionne les espacements d'emphase artificiels entre caractères japonais isolés
       (ex: 'ま さ か' ou 'ぜ っ た い に' causés par certaines polices/mises en page)
       tout en préservant l'alinéa de début de paragraphe ('　') et l'espace après '！' / '？'.
    3. Normalise les retours à la ligne doux au milieu d'une phrase japonaise.
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
    Découpe un paragraphe en phrases japonaises exactes en tenant compte :
    - Des guillemets japonais imbriqués (「...」, 『...』, （...）, 【...】)
    - Des dialogues suivis d'une proposition narrative (ex: 「行くよ！」と彼は言った。 -> 1 seule phrase)
    - Des espaces plein-chasse après '！' ou '？' (ex: まさか！　そんなはずはない。 -> 2 phrases)
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
    Moteur d'analyse morphologique basé sur SudachiPy (entraîné sur BCCWJ/UniDic)
    avec agrégation intelligente des verbes/adjectifs conjugués, doublé d'un
    segmenteur morphologique par dictionnaire + déconjugaison.
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

            pos_fr = POS_FR_MAP.get(pos0, pos0)
            if conjugation_parts and surface != base_form:
                pos_detail = f"{pos_fr} (forme conjuguée de {base_form})"
            else:
                pos_detail = pos_fr

            base_hira = katakana_to_hiragana(base_reading_kata)
            base_kata = hiragana_to_katakana(base_reading_kata)
            surf_hira = katakana_to_hiragana(surface_reading_kata)
            surf_kata = hiragana_to_katakana(surface_reading_kata)

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
                    "is_word": True,
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
        """
        Segmenteur morphologique basé sur le dictionnaire JMdict + règles de déconjugaison
        verbale/adjectivale japonaise (utilisé lorsque SudachiPy n'est pas compilé localement).
        """
        lex = self._load_lexicon()
        tokens: List[Dict[str, Any]] = []
        n = len(sentence_text)
        i = 0
        token_idx = 0

        while i < n:
            ch = sentence_text[i]

            # 1. Ponctuation ou espaces
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
                    }
                )
                token_idx += 1
                i = j
                continue

            # 2. Recherche du plus long mot ou forme conjuguée à partir de la position i
            matched_token = None
            max_len = min(12, n - i)
            for length in range(max_len, 0, -1):
                candidate = sentence_text[i : i + length]
                if not all(is_japanese_word_char(c) for c in candidate):
                    continue

                # a) Correspondance directe dans le lexique
                if candidate in lex:
                    hira, pos = lex[candidate]
                    pos_fr = POS_FR_MAP.get(pos, pos or "Mot")
                    matched_token = (candidate, candidate, hira, pos, pos_fr, length)
                    break

                # b) Correspondance par déconjugaison (verbes Godan/Ichidan & adjectifs)
                for surf_suffix, dict_ending in DECONJUGATION_RULES:
                    if candidate.endswith(surf_suffix) and len(candidate) > len(surf_suffix):
                        stem = candidate[: -len(surf_suffix)]
                        base_candidate = stem + dict_ending
                        if base_candidate in lex:
                            hira, pos = lex[base_candidate]
                            pos_fr = POS_FR_MAP.get(pos, "Verbe")
                            pos_detail = f"{pos_fr} (forme conjuguée de {base_candidate})"
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
                        "is_word": True,
                    }
                )
                token_idx += 1
                i += step
                continue

            # 3. Séparation par classe de caractères (Kanji vs Katakana vs Hiragana/Particule)
            if 0x4E00 <= ord(ch) <= 0x9FFF or ch == "々":
                j = i + 1
                while j < n and (0x4E00 <= ord(sentence_text[j]) <= 0x9FFF or sentence_text[j] == "々"):
                    j += 1
                surf = sentence_text[i:j]
                pos_detail = "Nom"
            elif 0x30A0 <= ord(ch) <= 0x30FF:
                j = i + 1
                while j < n and (0x30A0 <= ord(sentence_text[j]) <= 0x30FF or sentence_text[j] == "ー"):
                    j += 1
                surf = sentence_text[i:j]
                pos_detail = "Nom (Katakana)"
            else:
                # Séquence Hiragana (particule ou mot grammatical)
                surf = ch
                for p_len in (3, 2, 1):
                    if i + p_len <= n and sentence_text[i : i + p_len] in PARTICLES_SET:
                        surf = sentence_text[i : i + p_len]
                        break
                j = i + len(surf)
                pos_detail = "Particule" if surf in PARTICLES_SET else "Mot"

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
                    "is_word": True,
                }
            )
            token_idx += 1
            i = j

        return {"sid": sentence_id, "text": sentence_text, "tokens": tokens}


def build_underlined_sentence_html(
    tokens: List[Dict[str, Any]], target_wid: str
) -> Tuple[str, str]:
    """
    Construit la phrase complète pour la carte Anki en soulignant (<u>...</u>)
    exactement l'occurrence du mot cliqué dans la phrase que l'utilisateur est en train de lire.
    Retourne (sentence_html, sentence_plain).
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


nlp_engine = JapaneseNLPEngine()
