"""
Script d'importation du dictionnaire complet JMdict (Français + Anglais) dans SQLite.
Télécharge l'archive officielle JMdict.gz (EDRDG) contenant les traductions françaises (fre)
et anglaises (eng), et indexe les ~200 000 entrées pour une recherche instantanée (< 2ms).
"""

import gzip
import io
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path
from typing import List, Tuple
from xml.etree import ElementTree as ET

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import JMDICT_DB_PATH
from app.dictionary import init_jmdict_db
from app.japanese_nlp import hiragana_to_katakana, katakana_to_hiragana

JMDICT_URLS = [
    "http://ftp.edrdg.org/pub/Nihongo/JMdict.gz",
    "https://ftp.edrdg.org/pub/Nihongo/JMdict.gz",
]

XML_LANG_ATTR = "{http://www.w3.org/XML/1998/namespace}lang"


def download_and_build_jmdict() -> int:
    init_jmdict_db()
    print("[JMdict] Téléchargement du dictionnaire multilingue JMdict (FR + EN)...")

    gz_bytes = None
    for url in JMDICT_URLS:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "CalibreJPMiner/1.0"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                gz_bytes = resp.read()
            if gz_bytes:
                break
        except Exception as e:
            print(f"[JMdict] Avertissement sur {url}: {e}")

    if not gz_bytes:
        raise RuntimeError("Impossible de télécharger JMdict.gz depuis les miroirs EDRDG.")

    print("[JMdict] Décompression et analyse XML (Français + Anglais)...")
    xml_bytes = gzip.decompress(gz_bytes)

    conn = sqlite3.connect(str(JMDICT_DB_PATH))
    cur = conn.cursor()
    cur.execute("PRAGMA synchronous = OFF")
    cur.execute("PRAGMA journal_mode = MEMORY")

    batch: List[Tuple[str, str, str, str, str, str, int]] = []
    inserted = 0

    context = ET.iterparse(io.BytesIO(xml_bytes), events=("end",))
    for _, elem in context:
        if elem.tag != "entry":
            continue

        # Extraction des formes Kanji (k_ele -> keb) et priorités
        keb_list: List[str] = []
        priority = 1
        for k_ele in elem.findall("k_ele"):
            keb = k_ele.findtext("keb")
            if keb:
                keb_list.append(keb.strip())
            if k_ele.find("ke_pri") is not None:
                priority = 10

        # Extraction des lectures Kana (r_ele -> reb)
        reb_list: List[str] = []
        for r_ele in elem.findall("r_ele"):
            reb = r_ele.findtext("reb")
            if reb:
                reb_list.append(reb.strip())
            if r_ele.find("re_pri") is not None:
                priority = max(priority, 8)

        # Extraction des sens (Français 'fre' et Anglais 'eng')
        gloss_fr: List[str] = []
        gloss_en: List[str] = []
        pos_list: List[str] = []

        for sense in elem.findall("sense"):
            for pos_el in sense.findall("pos"):
                if pos_el.text and pos_el.text not in pos_list:
                    pos_list.append(pos_el.text)
            for gloss in sense.findall("gloss"):
                txt = (gloss.text or "").strip()
                if not txt:
                    continue
                lang = gloss.attrib.get(XML_LANG_ATTR, "eng")
                if lang == "fre":
                    if txt not in gloss_fr:
                        gloss_fr.append(txt)
                elif lang == "eng":
                    if txt not in gloss_en:
                        gloss_en.append(txt)

        if (gloss_fr or gloss_en) and reb_list:
            primary_reb = reb_list[0]
            hira = katakana_to_hiragana(primary_reb)
            kata = hiragana_to_katakana(primary_reb)
            pos_str = ", ".join(pos_list[:2])
            fr_json = json.dumps(gloss_fr[:10], ensure_ascii=False)
            en_json = json.dumps(gloss_en[:10], ensure_ascii=False)

            # Si l'entrée a plus de traductions françaises, on augmente légèrement sa priorité
            eff_prio = priority + (2 if gloss_fr else 0)

            targets = keb_list if keb_list else [primary_reb]
            for k_form in targets:
                batch.append((k_form, hira, kata, pos_str, fr_json, en_json, eff_prio))

        elem.clear()

        if len(batch) >= 5000:
            cur.executemany(
                """
                INSERT INTO entries (kanji, reading_hira, reading_kata, pos, gloss_fr, gloss_en, priority)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                batch,
            )
            inserted += len(batch)
            batch.clear()

    if batch:
        cur.executemany(
            """
            INSERT INTO entries (kanji, reading_hira, reading_kata, pos, gloss_fr, gloss_en, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        inserted += len(batch)
        batch.clear()

    conn.commit()
    conn.close()
    print(f"[JMdict] Terminé : {inserted} entrées indexées dans {JMDICT_DB_PATH}.")
    return inserted


if __name__ == "__main__":
    download_and_build_jmdict()
