import json
import sqlite3
from typing import Any, Dict, List

from app.config import JMDICT_DB_PATH
from app.japanese_nlp import hiragana_to_katakana, katakana_to_hiragana

BUILTIN_CORE_ENTRIES = [
    ("私", "わたし", "代名詞", ["je", "moi"], ["I", "me"]),
    ("僕", "ぼく", "代名詞", ["je", "moi"], ["I", "me (male)"]),
    ("俺", "おれ", "代名詞", ["je", "moi"], ["I", "me (rough male)"]),
    ("彼", "かれ", "代名詞", ["il", "lui"], ["he", "him", "boyfriend"]),
    ("彼女", "かのじょ", "代名詞", ["elle", "petite amie"], ["she", "her", "girlfriend"]),
    ("誰", "だれ", "代名詞", ["qui"], ["who"]),
    ("何", "なに", "代名詞", ["quoi", "que"], ["what"]),
    ("此処", "ここ", "代名詞", ["ici"], ["here", "this place"]),
    ("其処", "そこ", "代名詞", ["là", "cet endroit"], ["there"]),
    ("今日", "きょう", "名詞", ["aujourd'hui"], ["today"]),
    ("昨日", "きのう", "名詞", ["hier"], ["yesterday"]),
    ("明日", "あした", "名詞", ["demain"], ["tomorrow"]),
    ("今", "いま", "名詞", ["maintenant"], ["now", "the present"]),
    ("時間", "じかん", "名詞", ["temps", "heure"], ["time", "hour"]),
    ("世界", "せかい", "名詞", ["monde", "univers"], ["the world", "society", "the universe"]),
    ("人間", "にんげん", "名詞", ["être humain", "personne"], ["human being", "person"]),
    ("人", "ひと", "名詞", ["personne", "gens"], ["person", "people"]),
    ("言葉", "ことば", "名詞", ["mot", "parole", "langage"], ["word", "words", "language", "speech"]),
    ("本", "ほん", "名詞", ["livre", "ouvrage"], ["book", "volume", "origin"]),
    ("図書館", "としょかん", "名詞", ["bibliothèque"], ["library"]),
    ("扉", "とびら", "名詞", ["porte", "battant"], ["door", "gate", "title page"]),
    ("部屋", "へや", "名詞", ["chambre", "pièce"], ["room", "chamber"]),
    ("窓", "まど", "名詞", ["fenêtre"], ["window"]),
    ("光", "ひかり", "名詞", ["lumière", "éclat", "rayon"], ["light", "ray", "gleam"]),
    ("影", "かげ", "名詞", ["ombre", "silhouette"], ["shadow", "shade", "silhouette"]),
    ("声", "こえ", "名詞", ["voix"], ["voice"]),
    ("目", "め", "名詞", ["œil", "yeux", "regard"], ["eye", "look", "gaze"]),
    ("手", "て", "名詞", ["main", "bras"], ["hand", "arm"]),
    ("心", "こころ", "名詞", ["cœur", "esprit", "âme"], ["heart", "mind", "spirit"]),
    ("記憶", "きおく", "名詞", ["mémoire", "souvenir"], ["memory", "recollection"]),
    ("秘密", "ひみつ", "名詞", ["secret", "mystère"], ["secret", "secrecy", "mystery"]),
    ("魔法", "まほう", "名詞", ["magie", "sorcellerie"], ["magic", "witchcraft", "sorcery"]),
    ("物語", "ものがたり", "名詞", ["histoire", "récit", "conte"], ["story", "tale", "narrative", "legend"]),
    ("文字", "もじ", "名詞", ["lettre", "caractère", "texte"], ["letter", "character", "writing"]),
    ("意味", "いみ", "名詞", ["sens", "signification"], ["meaning", "significance", "sense"]),
    ("理由", "りゆう", "名詞", ["raison", "motif"], ["reason", "motive", "grounds"]),
    ("本当", "ほんとう", "名詞", ["vérité", "réalité", "vrai"], ["truth", "reality", "fact", "genuine"]),
    ("絶対", "ぜったい", "名詞", ["absolu", "absolument"], ["absolute", "unconditional", "definitely"]),
    ("静か", "しずか", "形状詞", ["calme", "silencieux", "tranquille"], ["quiet", "silent", "peaceful", "calm"]),
    ("不思議", "ふしぎ", "形状詞", ["mystérieux", "étrange", "merveilleux"], ["wonderful", "marvelous", "strange", "mysterious"]),
    ("古い", "ふるい", "形容詞", ["vieux", "ancien"], ["old", "ancient", "antiquated"]),
    ("新しい", "あたらしい", "形容詞", ["nouveau", "neuf", "récent"], ["new", "fresh", "recent"]),
    ("深い", "ふかい", "形容詞", ["profond", "dense"], ["deep", "profound", "dense"]),
    ("強い", "つよい", "形容詞", ["fort", "puissant"], ["strong", "powerful", "mighty"]),
    ("優しい", "やさしい", "形容詞", ["doux", "gentil", "bienveillant"], ["gentle", "kind", "tender", "affectionate"]),
    ("美しい", "うつくしい", "形容詞", ["beau", "magnifique"], ["beautiful", "lovely", "pretty"]),
    ("読む", "よむ", "動詞", ["lire", "déchiffrer"], ["to read", "to recite", "to decipher"]),
    ("書く", "かく", "動詞", ["écrire", "composer"], ["to write", "to compose", "to draw"]),
    ("話す", "はなす", "動詞", ["parler", "raconter"], ["to speak", "to talk", "to tell"]),
    ("聞く", "きく", "動詞", ["écouter", "entendre", "demander"], ["to hear", "to listen", "to ask"]),
    ("見る", "みる", "動詞", ["voir", "regarder", "observer"], ["to see", "to look", "to watch", "to try"]),
    ("食べる", "たべる", "動詞", ["manger", "se nourrir"], ["to eat", "to live on"]),
    ("行く", "いく", "動詞", ["aller", "se rendre à"], ["to go", "to move", "to proceed"]),
    ("来る", "くる", "動詞", ["venir", "arriver"], ["to come", "to arrive", "to approach"]),
    ("帰る", "かえる", "動詞", ["rentrer", "retourner"], ["to return", "to go home"]),
    ("開ける", "あける", "動詞", ["ouvrir"], ["to open (a door, etc.)", "to unwrap"]),
    ("開く", "ひらく", "動詞", ["ouvrir", "s'ouvrir"], ["to open", "to unfold", "to bloom"]),
    ("閉じる", "とじる", "動詞", ["fermer", "clore"], ["to close", "to shut"]),
    ("思う", "おもう", "動詞", ["penser", "croire"], ["to think", "to consider", "to believe", "to feel"]),
    ("考える", "かんがえる", "動詞", ["réfléchir", "penser"], ["to think", "to reflect", "to consider"]),
    ("知る", "しる", "動詞", ["savoir", "connaître"], ["to know", "to be aware of", "to learn"]),
    ("分かる", "わかる", "動詞", ["comprendre", "saisir"], ["to understand", "to comprehend", "to grasp"]),
    ("忘れる", "わすれる", "動詞", ["oublier"], ["to forget", "to leave behind"]),
    ("覚える", "おぼえる", "動詞", ["mémoriser", "se souvenir"], ["to memorize", "to remember", "to learn", "to feel"]),
    ("探す", "さがす", "動詞", ["chercher", "rechercher"], ["to search for", "to look for", "to seek"]),
    ("見つける", "みつける", "動詞", ["trouver", "découvrir"], ["to find", "to discover", "to locate"]),
    ("待つ", "まつ", "動詞", ["attendre"], ["to wait", "to await", "to look forward to"]),
    ("許す", "ゆるす", "動詞", ["pardonner", "permettre"], ["to forgive", "to pardon", "to permit", "to allow"]),
    ("叫ぶ", "さけぶ", "動詞", ["crier", "hurler"], ["to shout", "to cry out", "to scream"]),
    ("笑う", "わらう", "動詞", ["rire", "sourire"], ["to laugh", "to smile"]),
    ("歩く", "あるく", "動詞", ["marcher"], ["to walk"]),
    ("走る", "はしる", "動詞", ["courir"], ["to run"]),
    ("立ち上がる", "たちあがる", "動詞", ["se lever"], ["to stand up", "to rise"]),
    ("踏み入れる", "ふみいれる", "動詞", ["mettre le pied dans", "pénétrer dans"], ["to step into", "to set foot in"]),
    ("始まる", "はじまる", "動詞", ["commencer", "débuter"], ["to begin", "to start"]),
    ("終わる", "おわる", "動詞", ["finir", "se terminer"], ["to end", "to come to an end", "to finish"]),
    ("生きる", "いきる", "動詞", ["vivre", "exister"], ["to live", "to exist"]),
    ("選ぶ", "えらぶ", "動詞", ["choisir", "sélectionner"], ["to choose", "to select"]),
    ("変わる", "かわる", "動詞", ["changer", "se transformer"], ["to change", "to be transformed"]),
    ("続く", "つづく", "動詞", ["continuer", "se poursuivre"], ["to continue", "to follow", "to last"]),
    ("ゆっくり", "ゆっくり", "副詞", ["lentement", "tranquillement"], ["slowly", "unhurriedly", "at ease"]),
    ("きっと", "きっと", "副詞", ["sûrement", "certainement"], ["surely", "undoubtedly", "certainly"]),
    ("まだ", "まだ", "副詞", ["encore", "pas encore"], ["still", "as yet", "not yet"]),
    ("もう", "もう", "副詞", ["déjà", "bientôt"], ["already", "yet", "soon", "more"]),
    ("すぐ", "すぐ", "副詞", ["tout de suite", "immédiatement"], ["immediately", "right away", "soon"]),
    ("なぜ", "なぜ", "副詞", ["pourquoi"], ["why", "how"]),
    ("どう", "どう", "副詞", ["comment"], ["how", "in what way"]),
    ("しかし", "しかし", "接続詞", ["cependant", "mais"], ["however", "but", "nevertheless"]),
    ("そして", "そして", "接続詞", ["et", "ensuite"], ["and", "and then", "thus"]),
    ("だから", "だから", "接続詞", ["c'est pourquoi", "donc"], ["so", "therefore", "for that reason"]),
    ("夜", "よる", "名詞", ["nuit", "soir"], ["night", "evening"]),
    ("朝", "あさ", "名詞", ["matin"], ["morning"]),
    ("空", "そら", "名詞", ["ciel"], ["sky", "the air"]),
    ("風", "かぜ", "名詞", ["vent", "brise"], ["wind", "breeze"]),
    ("雨", "あめ", "名詞", ["pluie"], ["rain"]),
    ("街", "まち", "名詞", ["ville", "quartier"], ["town", "city", "street"]),
    ("道", "みち", "名詞", ["chemin", "route"], ["road", "path", "way"]),
    ("足", "あし", "名詞", ["pied", "jambe", "pas"], ["foot", "leg", "step"]),
    ("中", "なか", "名詞", ["intérieur", "milieu"], ["inside", "interior", "middle"]),
    ("外", "そと", "名詞", ["extérieur", "dehors"], ["outside", "exterior"]),
    ("前", "まえ", "名詞", ["avant", "devant"], ["before", "in front of", "ago"]),
    ("後", "あと", "名詞", ["après", "plus tard"], ["after", "later", "behind"]),
    ("ページ", "ぺーじ", "名詞", ["page"], ["page"]),
    ("コーヒー", "こーひー", "名詞", ["café"], ["coffee"]),
    ("重い", "おもい", "形容詞", ["lourd", "pesant", "grave"], ["heavy", "weighty", "serious"]),
    ("温かい", "あたたかい", "形容詞", ["chaud", "tiède"], ["warm", "mild", "genial"]),
    ("落ちる", "おちる", "動詞", ["tomber", "décliner"], ["to fall", "to drop", "to sink"]),
    ("置く", "おく", "動詞", ["poser", "placer"], ["to put", "to place", "to leave"]),
    ("信じる", "しんじる", "動詞", ["croire", "avoir confiance"], ["to believe", "to trust"]),
    ("飲む", "のむ", "動詞", ["boire", "avaler"], ["to drink", "to swallow"]),
    ("濡らす", "ぬらす", "動詞", ["mouiller", "tremper"], ["to wet", "to soak", "to dampen"]),
    ("言う", "いう", "動詞", ["dire", "parler"], ["to say", "to utter", "to declare"]),
    ("机", "つくえ", "名詞", ["bureau", "table"], ["desk", "table"]),
    ("上", "うえ", "名詞", ["dessus", "haut"], ["above", "up", "over", "top"]),
    ("場所", "ばしょ", "名詞", ["endroit", "lieu"], ["place", "location", "spot"]),
    ("一冊", "いっさつ", "名詞", ["un volume", "un livre"], ["one copy (of a book)"]),
    ("いつまでも", "いつまでも", "副詞", ["pour toujours", "indéfiniment"], ["forever", "endlessly"]),
]


def init_jmdict_db() -> None:
    """Initializes the SQLite JMdict database and seeds core vocabulary if empty."""
    conn = sqlite3.connect(str(JMDICT_DB_PATH), check_same_thread=False)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kanji TEXT NOT NULL,
            reading_hira TEXT NOT NULL,
            reading_kata TEXT NOT NULL,
            pos TEXT DEFAULT '',
            gloss_fr TEXT DEFAULT '[]',
            gloss_en TEXT DEFAULT '[]',
            priority INTEGER DEFAULT 0
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jmdict_kanji ON entries(kanji)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jmdict_hira ON entries(reading_hira)")

    count = cur.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    if count < len(BUILTIN_CORE_ENTRIES):
        cur.execute("DELETE FROM entries")
        for kanji, hira, pos, fr_list, en_list in BUILTIN_CORE_ENTRIES:
            kata = hiragana_to_katakana(hira)
            cur.execute(
                """
                INSERT INTO entries (kanji, reading_hira, reading_kata, pos, gloss_fr, gloss_en, priority)
                VALUES (?, ?, ?, ?, ?, ?, 10)
                """,
                (
                    kanji,
                    hira,
                    kata,
                    pos,
                    json.dumps(fr_list, ensure_ascii=False),
                    json.dumps(en_list, ensure_ascii=False),
                ),
            )
        conn.commit()
    conn.close()


def lookup_word(
    surface: str,
    base: str,
    normalized: str = "",
    reading_hiragana: str = "",
) -> Dict[str, Any]:
    """Queries the SQLite JMdict database by base form, normalized form, surface, and reading."""
    conn = sqlite3.connect(str(JMDICT_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row

    candidates = []
    for term in (base, normalized, surface):
        if term and term not in candidates:
            candidates.append(term)

    hira_query = katakana_to_hiragana(reading_hiragana) if reading_hiragana else ""

    rows = []
    if candidates:
        placeholders = ",".join("?" for _ in candidates)
        rows = conn.execute(
            f"""
            SELECT * FROM entries
            WHERE kanji IN ({placeholders})
            ORDER BY priority DESC, id ASC
            LIMIT 10
            """,
            tuple(candidates),
        ).fetchall()

    if not rows and candidates:
        hira_candidates = [katakana_to_hiragana(c) for c in candidates]
        placeholders = ",".join("?" for _ in hira_candidates)
        rows = conn.execute(
            f"""
            SELECT * FROM entries
            WHERE reading_hira IN ({placeholders})
            ORDER BY priority DESC, id ASC
            LIMIT 10
            """,
            tuple(hira_candidates),
        ).fetchall()

    if not rows and hira_query:
        rows = conn.execute(
            """
            SELECT * FROM entries
            WHERE reading_hira = ?
            ORDER BY priority DESC, id ASC
            LIMIT 10
            """,
            (hira_query,),
        ).fetchall()

    conn.close()

    results: List[Dict[str, Any]] = []
    all_fr: List[str] = []
    all_en: List[str] = []

    for r in rows:
        fr_glosses = json.loads(r["gloss_fr"] or "[]")
        en_glosses = json.loads(r["gloss_en"] or "[]")
        for g in fr_glosses:
            if g not in all_fr:
                all_fr.append(g)
        for g in en_glosses:
            if g not in all_en:
                all_en.append(g)
        results.append(
            {
                "kanji": r["kanji"],
                "reading_hiragana": r["reading_hira"],
                "reading_katakana": r["reading_kata"],
                "pos": r["pos"],
                "definitions_fr": fr_glosses,
                "definitions_en": en_glosses,
            }
        )

    best_hira = (
        results[0]["reading_hiragana"]
        if results
        else (hira_query or katakana_to_hiragana(base or surface))
    )
    best_kata = (
        results[0]["reading_katakana"]
        if results
        else hiragana_to_katakana(best_hira)
    )

    return {
        "surface": surface,
        "base": base or surface,
        "reading_hiragana": best_hira,
        "reading_katakana": best_kata,
        "definitions_fr": all_fr[:8],
        "definitions_en": all_en[:8],
        "entries": results,
        "found": len(results) > 0,
    }


def get_dictionary_stats() -> Dict[str, Any]:
    conn = sqlite3.connect(str(JMDICT_DB_PATH), check_same_thread=False)
    total = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    conn.close()
    return {
        "total_entries": total,
        "is_full_jmdict": total > 10000,
    }
