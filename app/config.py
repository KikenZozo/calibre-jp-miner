import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Dossier de données persistantes (SQLite pour la progression, les cartes minées et le dictionnaire JMdict)
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Chemin vers la bibliothèque Calibre (contenant metadata.db et les dossiers auteurs/livres)
CALIBRE_LIBRARY_PATH = Path(
    os.getenv("CALIBRE_LIBRARY_PATH", str(DATA_DIR / "calibre_library"))
)
CALIBRE_LIBRARY_PATH.mkdir(parents=True, exist_ok=True)

# Dossier pour les EPUBs importés directement via l'interface web
UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Chemins des bases de données SQLite
APP_DB_PATH = DATA_DIR / "miner.db"
JMDICT_DB_PATH = DATA_DIR / "jmdict_fr_en.db"

# Configuration Anki par défaut (modifiable dans l'interface)
DEFAULT_ANKI_DECK = os.getenv("ANKI_DECK_NAME", "Data Mining")
DEFAULT_ANKI_MODEL = os.getenv("ANKI_MODEL_NAME", "YomiCalibre Mining")
DEFAULT_ANKICONNECT_URL = os.getenv("ANKICONNECT_URL", "http://127.0.0.1:8765")
DEFAULT_AUTO_MINE_ON_CLICK = os.getenv("AUTO_MINE_ON_CLICK", "true").lower() == "true"
