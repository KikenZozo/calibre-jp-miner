import hashlib
import json
import socket
import sqlite3
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import genanki

    _GENANKI_AVAILABLE = True
except ImportError:
    _GENANKI_AVAILABLE = False

from app.config import DATA_DIR
from app.database import (
    compute_deterministic_guid,
    get_settings,
    insert_mined_card,
    list_mined_cards,
    mark_card_synced,
)

CARD_CSS = """
.card {
    font-family: "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic", "Meiryo", sans-serif;
    font-size: 22px;
    text-align: center;
    color: #f0f2f5;
    background-color: #16181d;
    padding: 24px 16px;
    line-height: 1.65;
}
.sentence {
    font-size: 24px;
    margin: 18px auto;
    max-width: 640px;
    text-align: left;
    background: #20242c;
    padding: 16px 20px;
    border-radius: 10px;
    border-left: 4px solid #6366f1;
}
.sentence u {
    text-decoration: underline;
    text-decoration-color: #818cf8;
    text-decoration-thickness: 3px;
    text-underline-offset: 5px;
    color: #a5b4fc;
    font-weight: 700;
}
.word-header {
    margin-top: 14px;
}
.word-kanji {
    font-size: 36px;
    font-weight: 700;
    color: #ffffff;
}
.readings {
    font-size: 18px;
    color: #94a3b8;
    margin-top: 6px;
}
.reading-badge {
    display: inline-block;
    background: #262b36;
    padding: 3px 10px;
    border-radius: 6px;
    margin: 0 4px;
    color: #cbd5e1;
}
.pos-tag {
    display: inline-block;
    font-size: 13px;
    color: #a5b4fc;
    background: rgba(99, 102, 241, 0.16);
    padding: 2px 8px;
    border-radius: 4px;
    margin-top: 6px;
}
.defs-box {
    max-width: 640px;
    margin: 16px auto 0;
    text-align: left;
    background: #1e222a;
    padding: 14px 18px;
    border-radius: 10px;
    font-size: 18px;
}
.def-fr {
    color: #f8fafc;
    margin-bottom: 8px;
}
.def-en {
    color: #94a3b8;
    font-size: 15px;
}
.source-footer {
    margin-top: 18px;
    font-size: 12px;
    color: #64748b;
}
"""

FRONT_TEMPLATE = """
<div class="sentence">{{Phrase_Contexte}}</div>
<div class="word-header">
    <div class="word-kanji">{{Mot}}</div>
</div>
"""

BACK_TEMPLATE = """
<div class="sentence">{{Phrase_Contexte}}</div>
<div class="word-header">
    <div class="word-kanji">{{Mot}}</div>
    <div class="readings">
        <span class="reading-badge">平仮名 : {{Lecture_Hiragana}}</span>
        <span class="reading-badge">片仮名 : {{Lecture_Katakana}}</span>
    </div>
    {{#Grammaire}}<div class="pos-tag">{{Grammaire}}</div>{{/Grammaire}}
</div>
<div class="defs-box">
    {{#Definition_FR}}<div class="def-fr">{{Definition_FR}}</div>{{/Definition_FR}}
    {{#Definition_EN}}<div class="def-en">{{Definition_EN}}</div>{{/Definition_EN}}
</div>
{{#Source}}<div class="source-footer">{{Source}}</div>{{/Source}}
"""


def build_anki_fields_for_card(
    card: Dict[str, Any], custom_field_map: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """
    Maps a mined card to Anki note fields.
    Supports both the default YomiCalibre Mining note type and user-configured
    mappings for existing Anki note types.
    """
    word_base = card["word_base"]
    word_surface = card["word_surface"]
    mot_display = (
        word_base
        if word_base == word_surface
        else f"{word_base} ({word_surface})"
    )

    defs_fr = card.get("definitions_fr") or []
    defs_en = card.get("definitions_en") or []

    fr_str = " ; ".join(defs_fr) if defs_fr else ""
    en_str = " ; ".join(defs_en) if defs_en else ""
    combined_def = " / ".join(filter(None, [fr_str, en_str]))

    source_parts = [p for p in (card.get("book_title"), card.get("chapter_title")) if p]
    source_str = " — ".join(source_parts)

    default_fields = {
        "Mot": word_base,
        "Lecture_Hiragana": card["reading_hiragana"],
        "Lecture_Katakana": card["reading_katakana"],
        "Grammaire": card.get("pos_detail", ""),
        "Definition_FR": fr_str,
        "Definition_EN": en_str,
        "Phrase_Contexte": card["sentence_html"],
        "Source": source_str,
    }

    if not custom_field_map:
        return default_fields

    mapped: Dict[str, str] = {}
    value_lookup = {
        "word": word_base,
        "word_surface": mot_display,
        "reading_hiragana": card["reading_hiragana"],
        "reading_katakana": card["reading_katakana"],
        "pos": card.get("pos_detail", ""),
        "definition_fr": fr_str,
        "definition_en": en_str,
        "definition": combined_def,
        "sentence": card["sentence_html"],
        "sentence_plain": card["sentence_plain"],
        "source": source_str,
    }
    for semantic_key, target_anki_field in custom_field_map.items():
        if target_anki_field and semantic_key in value_lookup:
            mapped[target_anki_field] = value_lookup[semantic_key]

    return mapped if mapped else default_fields


def create_and_push_anki_card(
    word_surface: str,
    word_base: str,
    reading_hiragana: str,
    reading_katakana: str,
    pos_detail: str,
    definitions_fr: List[str],
    definitions_en: List[str],
    sentence_html: str,
    sentence_plain: str,
    book_id: str = "",
    book_title: str = "",
    chapter_title: str = "",
    frequency_rank: int = 999999,
    attempt_ankiconnect: bool = True,
) -> Dict[str, Any]:
    """
    Persists a mined card in mining.db with a deterministic GUID and optionally
    pushes it to AnkiConnect if reachable.
    """
    settings = get_settings()
    deck_name = settings.get("anki_deck_name", "Data Mining")
    model_name = settings.get("anki_model_name", "YomiCalibre Mining")
    ankiconnect_url = settings.get("ankiconnect_url", "http://127.0.0.1:8765")
    field_map = settings.get("anki_field_map")

    card = insert_mined_card(
        word_surface=word_surface,
        word_base=word_base,
        reading_hiragana=reading_hiragana,
        reading_katakana=reading_katakana,
        pos_detail=pos_detail,
        definitions_fr=definitions_fr,
        definitions_en=definitions_en,
        sentence_html=sentence_html,
        sentence_plain=sentence_plain,
        book_id=book_id,
        book_title=book_title,
        chapter_title=chapter_title,
        deck_name=deck_name,
        frequency_rank=frequency_rank,
        anki_synced=0,
    )

    if card.get("already_existed") and card.get("anki_synced"):
        card["anki_fields"] = build_anki_fields_for_card(card, field_map)
        card["sync_message"] = "Already synced to Anki"
        return card

    note_id = None
    if attempt_ankiconnect:
        note_id = try_push_card_to_ankiconnect(
            card, deck_name, model_name, ankiconnect_url, field_map
        )
    if note_id:
        mark_card_synced(card["id"], note_id)
        card["anki_synced"] = True
        card["anki_note_id"] = note_id
        card["sync_message"] = f"Added to Anki deck '{deck_name}'"
    else:
        card["sync_message"] = f"Queued for deck '{deck_name}'"

    card["anki_fields"] = build_anki_fields_for_card(card, field_map)
    card["anki_model_spec"] = get_ankiconnect_model_spec(model_name)
    return card


def get_ankiconnect_model_spec(model_name: str) -> Dict[str, Any]:
    return {
        "modelName": model_name,
        "inOrderFields": [
            "Mot",
            "Lecture_Hiragana",
            "Lecture_Katakana",
            "Grammaire",
            "Definition_FR",
            "Definition_EN",
            "Phrase_Contexte",
            "Source",
        ],
        "css": CARD_CSS,
        "isCloze": False,
        "cardTemplates": [
            {
                "Name": "Mining Card",
                "Front": FRONT_TEMPLATE,
                "Back": BACK_TEMPLATE,
            }
        ],
    }


def _is_ankiconnect_reachable(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 8765)
        with socket.create_connection((host, port), timeout=0.08):
            return True
    except Exception:
        return False


def _post_ankiconnect_sync(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(req, timeout=1.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def try_push_card_to_ankiconnect(
    card: Dict[str, Any],
    deck_name: str,
    model_name: str,
    ankiconnect_url: str,
    field_map: Optional[Dict[str, str]] = None,
) -> Optional[int]:
    if not ankiconnect_url or not _is_ankiconnect_reachable(ankiconnect_url):
        return None

    try:
        _post_ankiconnect_sync(
            ankiconnect_url,
            {"action": "createDeck", "version": 6, "params": {"deck": deck_name}},
        )
        models_resp = _post_ankiconnect_sync(
            ankiconnect_url, {"action": "modelNames", "version": 6}
        )
        existing_models = models_resp.get("result") or []
        if model_name not in existing_models:
            _post_ankiconnect_sync(
                ankiconnect_url,
                {
                    "action": "createModel",
                    "version": 6,
                    "params": get_ankiconnect_model_spec(model_name),
                },
            )

        fields = build_anki_fields_for_card(card, field_map)
        note_payload = {
            "action": "addNote",
            "version": 6,
            "params": {
                "note": {
                    "deckName": deck_name,
                    "modelName": model_name,
                    "fields": fields,
                    "options": {"allowDuplicate": False},
                    "tags": ["yomicalibre", "japanese-mining"],
                }
            },
        }
        add_resp = _post_ankiconnect_sync(ankiconnect_url, note_payload)
        if add_resp.get("error") is None and add_resp.get("result"):
            return int(add_resp["result"])
    except Exception:
        return None
    return None


def export_cards_to_apkg(only_unsynced: bool = False) -> Path:
    """Generates a deterministic-GUID Anki .apkg file using genanki (or stdlib fallback)."""
    settings = get_settings()
    deck_name = settings.get("anki_deck_name", "Data Mining")
    model_name = settings.get("anki_model_name", "YomiCalibre Mining")
    cards = list_mined_cards(only_unsynced=only_unsynced, limit=5000)

    exports_dir = DATA_DIR / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = exports_dir / "data_mining_japanese.apkg"

    if _GENANKI_AVAILABLE:
        deck_id = int(hashlib.sha256(deck_name.encode("utf-8")).hexdigest()[:8], 16)
        model_id = int(hashlib.sha256(model_name.encode("utf-8")).hexdigest()[:8], 16)
        anki_model = genanki.Model(
            model_id,
            model_name,
            fields=[
                {"name": "Mot"},
                {"name": "Lecture_Hiragana"},
                {"name": "Lecture_Katakana"},
                {"name": "Grammaire"},
                {"name": "Definition_FR"},
                {"name": "Definition_EN"},
                {"name": "Phrase_Contexte"},
                {"name": "Source"},
            ],
            templates=[
                {
                    "name": "Mining Card",
                    "qfmt": FRONT_TEMPLATE,
                    "afmt": BACK_TEMPLATE,
                }
            ],
            css=CARD_CSS,
        )
        anki_deck = genanki.Deck(deck_id, deck_name)
        for c in reversed(cards):
            f = build_anki_fields_for_card(c)
            det_guid = c.get("guid") or compute_deterministic_guid(
                c["word_base"], c["sentence_plain"]
            )
            note = genanki.Note(
                model=anki_model,
                fields=[
                    f["Mot"],
                    f["Lecture_Hiragana"],
                    f["Lecture_Katakana"],
                    f["Grammaire"],
                    f["Definition_FR"],
                    f["Definition_EN"],
                    f["Phrase_Contexte"],
                    f["Source"],
                ],
                guid=det_guid,
                tags=["yomicalibre", "japanese-mining"],
            )
            anki_deck.add_note(note)
        genanki.Package(anki_deck).write_to_file(str(tmp_file))
        return tmp_file

    return _build_apkg_stdlib(cards, deck_name, model_name, tmp_file)


def export_cards_to_tsv(only_unsynced: bool = False) -> Path:
    """Exports mined cards to a deterministic TSV file for manual Anki import."""
    cards = list_mined_cards(only_unsynced=only_unsynced, limit=5000)
    exports_dir = DATA_DIR / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = exports_dir / "data_mining_japanese.tsv"

    lines: List[str] = []
    for c in reversed(cards):
        f = build_anki_fields_for_card(c)
        cols = [
            c.get("guid", ""),
            f["Mot"],
            f["Lecture_Hiragana"],
            f["Lecture_Katakana"],
            f["Grammaire"],
            f["Definition_FR"],
            f["Definition_EN"],
            f["Phrase_Contexte"],
            f["Source"],
        ]
        cleaned_cols = [col.replace("\t", " ").replace("\n", " ") for col in cols]
        lines.append("\t".join(cleaned_cols))

    tsv_path.write_text("\n".join(lines), encoding="utf-8")
    return tsv_path


def _build_apkg_stdlib(
    cards: List[Dict[str, Any]], deck_name: str, model_name: str, out_path: Path
) -> Path:
    col_path = out_path.parent / "collection.anki2"
    if col_path.exists():
        col_path.unlink()

    now_sec = int(time.time())
    deck_id = str(int(hashlib.sha256(deck_name.encode("utf-8")).hexdigest()[:8], 16))
    model_id = str(int(hashlib.sha256(model_name.encode("utf-8")).hexdigest()[:8], 16))

    conn = sqlite3.connect(str(col_path))
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE col (
            id integer primary key, crt integer not null, mod integer not null,
            scm integer not null, ver integer not null, dty integer not null,
            usn integer not null, ls integer not null, conf text not null,
            models text not null, decks text not null, dconf text not null, tags text not null
        );
        CREATE TABLE notes (
            id integer primary key, guid text not null, mid integer not null,
            mod integer not null, usn integer not null, tags text not null,
            flds text not null, sfld integer not null, csum integer not null,
            flags integer not null, data text not null
        );
        CREATE TABLE cards (
            id integer primary key, nid integer not null, did integer not null,
            ord integer not null, mod integer not null, usn integer not null,
            type integer not null, queue integer not null, due integer not null,
            ivl integer not null, factor integer not null, reps integer not null,
            lapses integer not null, left integer not null, odue integer not null,
            odid integer not null, flags integer not null, data text not null
        );
        CREATE TABLE revlog (
            id integer primary key, cid integer not null, usn integer not null,
            ease integer not null, ivl integer not null, lastIvl integer not null,
            factor integer not null, time integer not null, type integer not null
        );
        CREATE TABLE graves (usn integer not null, oid integer not null, type integer not null);
        """
    )

    field_names = [
        "Mot",
        "Lecture_Hiragana",
        "Lecture_Katakana",
        "Grammaire",
        "Definition_FR",
        "Definition_EN",
        "Phrase_Contexte",
        "Source",
    ]
    models = {
        model_id: {
            "id": int(model_id),
            "name": model_name,
            "type": 0,
            "mod": now_sec,
            "usn": -1,
            "sortf": 0,
            "did": int(deck_id),
            "tmpls": [
                {
                    "name": "Mining Card",
                    "ord": 0,
                    "qfmt": FRONT_TEMPLATE,
                    "afmt": BACK_TEMPLATE,
                    "bqfmt": "",
                    "bafmt": "",
                    "did": None,
                }
            ],
            "flds": [
                {
                    "name": fn,
                    "ord": idx,
                    "sticky": False,
                    "rtl": False,
                    "font": "Arial",
                    "size": 20,
                    "media": [],
                }
                for idx, fn in enumerate(field_names)
            ],
            "css": CARD_CSS,
            "latexPre": "",
            "latexPost": "",
            "req": [[0, "all", [0]]],
            "tags": [],
            "vers": [],
        }
    }
    decks = {
        "1": {
            "id": 1,
            "mod": now_sec,
            "name": "Default",
            "usn": -1,
            "collapsed": False,
            "desc": "",
        },
        deck_id: {
            "id": int(deck_id),
            "mod": now_sec,
            "name": deck_name,
            "usn": -1,
            "collapsed": False,
            "desc": "",
        },
    }
    cur.execute(
        "INSERT INTO col VALUES (1, ?, ?, ?, 11, 0, 0, 0, '{}', ?, ?, '{}', '{}')",
        (
            now_sec,
            now_sec * 1000,
            now_sec * 1000,
            json.dumps(models),
            json.dumps(decks),
        ),
    )

    base_ts = int(time.time() * 1000)
    for idx, c in enumerate(reversed(cards)):
        f = build_anki_fields_for_card(c)
        flds_str = "\x1f".join(f[fn] for fn in field_names)
        nid = base_ts + idx * 2
        cid = base_ts + idx * 2 + 1
        guid = c.get("guid") or compute_deterministic_guid(
            c["word_base"], c["sentence_plain"]
        )
        cur.execute(
            "INSERT INTO notes VALUES (?, ?, ?, ?, -1, ' yomicalibre ', ?, ?, 0, 0, '')",
            (nid, guid, int(model_id), now_sec, flds_str, f["Mot"]),
        )
        cur.execute(
            "INSERT INTO cards VALUES (?, ?, ?, 0, ?, -1, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (cid, nid, int(deck_id), now_sec, idx + 1),
        )

    conn.commit()
    conn.close()

    with zipfile.ZipFile(str(out_path), "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(str(col_path), "collection.anki2")
        zf.writestr("media", "{}")

    col_path.unlink(missing_ok=True)
    return out_path
