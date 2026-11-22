"""Percorsi dell'applicazione. Solo libreria standard: lo usa anche l'icona di Linux (tray.py), che gira con il
Python di sistema."""
import os
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
CONFIG = RADICE / "config"
PROFILI = CONFIG / "profili"
NOME = "CashFlowAnalyzer"
NOME_VECCHIO = "spese-app"          # cartella dei dati su Linux fino alla 0.20.1
DATABASE = "spese.db"
PID = ("server.pid", "icona.pid")   # scritti da avvia.py finche' server e icona sono accesi


def _base_linux():
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def cartella_predefinita() -> str:
    """Linux: ~/.local/share/CashFlowAnalyzer (o $XDG_DATA_HOME). Windows: %LOCALAPPDATA%\\CashFlowAnalyzer, nel
    profilo dell'utente e non sincronizzata con OneDrive."""
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"), NOME)
    return os.path.join(_base_linux(), NOME)


def cartella_vecchia():
    """Cartella dei dati delle versioni fino alla 0.20.1 (solo Linux), None su Windows."""
    return None if sys.platform == "win32" else os.path.join(_base_linux(), NOME_VECCHIO)


def _acceso(cartella):
    """Server o icona accesi che usano ancora la cartella (file pid di un processo vivo)."""
    for nome in PID:
        try:
            pid = int((Path(cartella) / nome).read_text().split()[0])
        except (OSError, ValueError, IndexError):
            continue
        if Path("/proc/%d" % pid).exists():
            return True
    return False


def scegli_cartella(nuova, vecchia):
    """Cartella dei dati da usare, spostando quella vecchia (spese-app) nella nuova la prima volta.

    - Solo la vecchia: si rinomina nella nuova (database, backup e log la seguono, niente copie). Se server o icona
      la stanno ancora usando, o la rinomina non riesce, si continua con la vecchia: si sposta al prossimo avvio.
    - Tutte e due: si usa quella con il database. Se ce l'hanno entrambe, la nuova. Non si cancella nulla.
    """
    nuova = Path(nuova)
    if not vecchia or not Path(vecchia).is_dir():
        return nuova
    vecchia = Path(vecchia)
    if not nuova.exists():
        if _acceso(vecchia):
            return vecchia
        try:
            nuova.parent.mkdir(parents=True, exist_ok=True)
            os.rename(vecchia, nuova)
            return nuova
        except OSError:
            return vecchia
    if not (nuova / DATABASE).exists() and (vecchia / DATABASE).exists():
        return vecchia
    return nuova


def cartella_dati() -> Path:
    """Cartella dei dati utente (database cifrato, backup, log). Sovrascrivibile con SPESE_DATI (allora non si
    sposta nulla)."""
    if os.environ.get("SPESE_DATI"):
        p = Path(os.environ["SPESE_DATI"])
    else:
        p = scegli_cartella(cartella_predefinita(), cartella_vecchia())
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def file_database() -> Path:
    return cartella_dati() / DATABASE
