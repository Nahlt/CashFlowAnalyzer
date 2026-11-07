"""Percorsi dell'applicazione."""
import os
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
CONFIG = RADICE / "config"
PROFILI = CONFIG / "profili"


def cartella_predefinita() -> str:
    """Linux: ~/.local/share/spese-app (o $XDG_DATA_HOME). Windows: %LOCALAPPDATA%\\CashFlowAnalyzer, nel profilo
    dell'utente e non sincronizzata con OneDrive."""
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"), "CashFlowAnalyzer")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "spese-app")


def cartella_dati() -> Path:
    """Cartella dei dati utente (database cifrato, export). Sovrascrivibile con SPESE_DATI."""
    p = Path(os.environ.get("SPESE_DATI") or cartella_predefinita())
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def file_database() -> Path:
    return cartella_dati() / "spese.db"
