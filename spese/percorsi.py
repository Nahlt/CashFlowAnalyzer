"""Percorsi dell'applicazione."""
import os
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
CONFIG = RADICE / "config"
PROFILI = CONFIG / "profili"


def cartella_dati() -> Path:
    """Cartella dei dati utente (database cifrato, export). Sovrascrivibile con SPESE_DATI."""
    base = os.environ.get("SPESE_DATI") or os.path.join(
        os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "spese-app")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def file_database() -> Path:
    return cartella_dati() / "spese.db"
