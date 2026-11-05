import json
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from app.config import ANKI_COLLECTION_PATH
from app.database import upsert_known_words
from app.japanese_nlp import contains_japanese_chars

HTML_TAG_RE = re.compile(r"<[^>]+>")
FURIGANA_BRACKET_RE = re.compile(r"\[[^\]]*\]")
SOUND_TAG_RE = re.compile(r"\[sound:[^\]]+\]")


def clean_anki_field_to_word(raw_field: str) -> str:
    """Strips HTML tags, [sound:...] tags, and Anki furigana brackets (漢字[かんじ] -> 漢字)."""
    if not raw_field:
        return ""
    txt = SOUND_TAG_RE.sub("", raw_field)
    txt = HTML_TAG_RE.sub("", txt)
    txt = FURIGANA_BRACKET_RE.sub("", txt)
    txt = txt.replace("&nbsp;", " ").strip()
    return txt


def inspect_and_sync_anki_snapshot(
    collection_path: Path = ANKI_COLLECTION_PATH,
) -> Dict[str, Any]:
    """
    Safely copies collection.anki2 AND its -wal/-shm files into a temporary directory
    before reading. Detects schema at runtime (notetypes/fields tables vs col.models JSON),
    extracts existing Note Types + field lists, and imports known vocabulary into mining.db.
    """
    if not collection_path.exists():
        return {
            "available": False,
            "path": str(collection_path),
            "message": f"Anki collection not found at {collection_path}",
            "notetypes": [],
            "imported_words": 0,
        }

    tmp_dir = Path(tempfile.mkdtemp(prefix="anki_ro_snap_"))
    try:
        snap_db = tmp_dir / "collection.anki2"
        shutil.copy2(str(collection_path), str(snap_db))

        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(collection_path) + suffix)
            if sidecar.exists():
                shutil.copy2(str(sidecar), str(tmp_dir / f"collection.anki2{suffix}"))

        conn = sqlite3.connect(str(snap_db))
        conn.row_factory = sqlite3.Row

        notetypes = _extract_notetypes_runtime(conn)
        known_words = _extract_known_words_from_notes(conn)
        conn.close()

        total_known = upsert_known_words(list(known_words), source="anki_snapshot")
        return {
            "available": True,
            "path": str(collection_path),
            "notetypes": notetypes,
            "imported_words": len(known_words),
            "total_known_words": total_known,
            "message": f"Synced {len(known_words)} words from Anki collection snapshot.",
        }
    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


def _extract_notetypes_runtime(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Detects at runtime whether Note Types live in modern `notetypes` + `fields` tables
    or in the legacy `col.models` JSON column.
    """
    tables = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    result: List[Dict[str, Any]] = []

    if "notetypes" in tables and "fields" in tables:
        nt_rows = conn.execute("SELECT id, name FROM notetypes").fetchall()
        for nt in nt_rows:
            nt_id = nt["id"]
            f_rows = conn.execute(
                "SELECT name, ord FROM fields WHERE ntid = ? ORDER BY ord ASC",
                (nt_id,),
            ).fetchall()
            result.append(
                {
                    "id": str(nt_id),
                    "name": nt["name"],
                    "fields": [fr["name"] for fr in f_rows],
                }
            )
        if result:
            return result

    if "col" in tables:
        col_row = conn.execute("SELECT models FROM col LIMIT 1").fetchone()
        if col_row and col_row["models"]:
            try:
                models_dict = json.loads(col_row["models"])
                for mid, mdata in models_dict.items():
                    flds = sorted(
                        mdata.get("flds", []), key=lambda x: x.get("ord", 0)
                    )
                    result.append(
                        {
                            "id": str(mid),
                            "name": mdata.get("name", f"Model {mid}"),
                            "fields": [f.get("name", "") for f in flds],
                        }
                    )
            except Exception:
                pass

    return result


def _extract_known_words_from_notes(conn: sqlite3.Connection) -> Set[str]:
    """
    Reads `notes.sfld` and `notes.flds` (joined by 0x1F) to extract all Japanese
    terms present in the user's collection.
    """
    words: Set[str] = set()
    rows = conn.execute("SELECT sfld, flds FROM notes").fetchall()
    for r in rows:
        sfld_clean = clean_anki_field_to_word(str(r["sfld"] or ""))
        if sfld_clean and len(sfld_clean) <= 25 and contains_japanese_chars(sfld_clean):
            words.add(sfld_clean)

        flds_raw = str(r["flds"] or "")
        if flds_raw:
            parts = flds_raw.split("\x1f")
            for part in parts[:2]:
                cleaned = clean_anki_field_to_word(part)
                if cleaned and len(cleaned) <= 20 and contains_japanese_chars(cleaned):
                    words.add(cleaned)
    return words
