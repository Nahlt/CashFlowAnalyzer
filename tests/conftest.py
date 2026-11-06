import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sintetici  # noqa: E402


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("SPESE_DATI", str(tmp_path / "dati"))
    from spese import configurazione, db, percorsi
    configurazione.imposta_tassonomia(None)
    c = db.apri(percorsi.file_database(), "passphrase di prova")
    db.scrivi_impostazione(c, "titolari", ["Mario Rossi"])
    yield c
    c.close()


@pytest.fixture()
def caricato(conn, tmp_path):
    """Database con i quattro file sintetici importati e ricalcolati."""
    from spese import importa
    from spese.ricalcolo import ricalcola
    ing = importa.crea_conto(conn, "ING", "ing_conto", "conto", "personale", "ING", sintetici.IBAN_ING)
    credem = importa.crea_conto(conn, "Credem comune", "credem", "conto", "comune", "Credem", sintetici.IBAN_CREDEM)
    tr = importa.crea_conto(conn, "Trade Republic", "trade_republic", "conto", "personale", "TR", sintetici.IBAN_TR)
    carta = importa.crea_conto(conn, "Carta ING", "ing_carta", "carta", "personale", "ING", conto_addebito_id=ing)
    x = tmp_path / "ing.xlsx"
    sintetici.ing_xlsx(x)
    rep = {
        "ing": importa.importa(conn, ing, "ing.xlsx", x.read_bytes()),
        "credem": importa.importa(conn, credem, "LISTA_MOVIMENTI.csv", sintetici.credem_csv()),
        "tr": importa.importa(conn, tr, "tr.csv", sintetici.tr_csv()),
        "carta": importa.importa(conn, carta, "carta.csv", sintetici.ing_carta_tsv()),
    }
    ricalcola(conn)
    return conn, {"ing": ing, "credem": credem, "tr": tr, "carta": carta}, rep
