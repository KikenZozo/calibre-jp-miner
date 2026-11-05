import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Writable persistent directory (/home/enzo/mining mounted at /data in container)
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Read-only Calibre library root (/home/enzo/calibre/library mounted at /calibre-library:ro)
CALIBRE_LIBRARY_PATH = Path(
    os.getenv("CALIBRE_LIBRARY_PATH", "/calibre-library")
)

# Read-only Anki sync server collection path (/anki_data/enzo/collection.anki2)
ANKI_COLLECTION_PATH = Path(
    os.getenv("ANKI_COLLECTION_PATH", "/anki_data/enzo/collection.anki2")
)

# Local uploads directory inside writable DATA_DIR (never inside CALIBRE_LIBRARY_PATH)
UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# SQLite databases inside writable DATA_DIR
MINING_DB_PATH = DATA_DIR / "mining.db"
JMDICT_DB_PATH = DATA_DIR / "jmdict.db"

# Default Anki configuration (overridable at runtime via UI settings)
DEFAULT_ANKI_DECK = os.getenv("ANKI_DECK_NAME", "Data Mining")
DEFAULT_ANKI_MODEL = os.getenv("ANKI_MODEL_NAME", "YomiCalibre Mining")
DEFAULT_ANKICONNECT_URL = os.getenv("ANKICONNECT_URL", "http://127.0.0.1:8765")
DEFAULT_AUTO_MINE_ON_CLICK = os.getenv("AUTO_MINE_ON_CLICK", "true").lower() == "true"

# Optional HTTP Basic Auth credentials (set in Dokploy Environment tab, never in compose file)
MINING_AUTH_USER = os.getenv("MINING_AUTH_USER", "").strip()
MINING_AUTH_PASSWORD = os.getenv("MINING_AUTH_PASSWORD", "").strip()
