"""0.17.1: le conferme dell'utente prevalgono su regole del programma e marchi; regole e marchi si cercano una
volta per nome dell'esercente (prima a ogni ricalcolo, per ogni movimento: salvataggi lenti). Dati inventati."""
import pytest

from spese import catena, importa, servizi
from spese import configurazione as cfg
from spese.chiavi import chiave
from spese.ricalcolo import ricalcola


def _pagamento(conn, conto, mid, esercente, data="2026-10-01", cent=-5000):
    conn.execute("INSERT INTO movimenti(id, conto_id, data, importo_cent, tipo, esercente, chiave) "
                 "VALUES (?,?,?,?,?,?,?)", (mid, conto, data, cent, "pagamento_carta", esercente, chiave(esercente)))


def _cat(conn, mid):
    r = conn.execute("SELECT l1, l2, fonte FROM analisi WHERE mov_id = ? AND componente = 'principale'",
                     (mid,)).fetchone()
    return (r["l1"], r["l2"]), r["fonte"]


def _marchio_decisivo():
    """Un marchio decisivo presente in Italia che nessuna regola del programma o codice MCC anticipa."""
    return next(m["marchio"] for _, m in cfg.marchi() if m["decisivo"] and m["priorita"] == 0
                and catena.deterministica(m["marchio"].upper(), None)[0] == "marchio")


@pytest.fixture()
def ing(conn):
    return importa.crea_conto(conn, "ING", "ing_conto", "conto", "personale", "ING")


# --- conferme dell'utente ----------------------------------------------------------------------
def test_conferma_prevale_sulla_regola_del_programma(conn, ing):
    _pagamento(conn, ing, "X", "ESSELUNGA")
    ricalcola(conn)
    assert _cat(conn, "X") == (("ALIMENTARI", "Supermercato"), "regola")
    servizi.conferma_esercente(conn, chiave("ESSELUNGA"), ("ALIMENTARI", "Negozi alimentari"))
    ricalcola(conn)
    assert _cat(conn, "X") == (("ALIMENTARI", "Negozi alimentari"), "utente")      # sulla 0.17.0: Supermercato


def test_conferma_prevale_sul_marchio_decisivo(conn, ing):
    nome = _marchio_decisivo().upper()
    _pagamento(conn, ing, "X", nome)
    ricalcola(conn)
    assert _cat(conn, "X")[1] == "marchio"
    servizi.conferma_esercente(conn, chiave(nome), ("CASA", "Acqua"))
    ricalcola(conn)
    assert _cat(conn, "X") == (("CASA", "Acqua"), "utente")


def test_modifica_da_regole_e_tassonomia_prevale(conn, ing):
    """Esercenti gia' classificati (Regole e tassonomia): la modifica fatta a mano diventa una conferma."""
    _pagamento(conn, ing, "X", "ESSELUNGA")
    servizi.conferma_esercente(conn, chiave("ESSELUNGA"), ("ALIMENTARI", "Negozi alimentari"))
    servizi.aggiorna_esercente(conn, chiave("ESSELUNGA"), ("ALIMENTARI", "Mercati"))
    ricalcola(conn)
    assert _cat(conn, "X") == (("ALIMENTARI", "Mercati"), "utente")


@pytest.mark.parametrize("motore", ["claude", "modello_locale"])
def test_modelli_non_prevalgono_su_regole_e_marchi(conn, ing, motore):
    """Solo le conferme dell'utente si spostano avanti: Claude e il modello locale restano dopo regole e marchi."""
    _pagamento(conn, ing, "X", "ESSELUNGA")
    conn.execute("INSERT INTO cache_esercenti(chiave, l1, l2, motore, confidenza) VALUES (?,?,?,?,?)",
                 (chiave("ESSELUNGA"), "CASA", "Acqua", motore, 0.99))
    ricalcola(conn)
    assert _cat(conn, "X") == (("ALIMENTARI", "Supermercato"), "regola")


def test_decisione_sul_movimento_resta_prima_di_tutto(conn, ing):
    _pagamento(conn, ing, "X", "ESSELUNGA")
    _pagamento(conn, ing, "Y", "ESSELUNGA", data="2026-10-02")
    servizi.conferma_esercente(conn, chiave("ESSELUNGA"), ("ALIMENTARI", "Negozi alimentari"))
    servizi.decidi_movimento(conn, "Y", ("RISTORAZIONE", "Bar e pasticcerie"), "uscita")
    ricalcola(conn)
    assert _cat(conn, "X")[0] == ("ALIMENTARI", "Negozi alimentari")
    assert _cat(conn, "Y")[0] == ("RISTORAZIONE", "Bar e pasticcerie")


# --- memoria per nome -------------------------------------------------------------------------
def _deterministica_senza_memoria(esercente, mcc_code):
    """La parte deterministica come nella 0.17.0, senza memoria: riferimento per il confronto."""
    tax = cfg.tassonomia()
    testo = esercente or ""

    def regola(forza):
        migliore = None
        for r in cfg.regole_esercenti():
            if r.forza != forza:
                continue
            m = r.rx.search(testo)
            if m and (migliore is None or len(m.group(0)) > migliore[0]):
                cat = tax.risolvi(r.l2)
                if cat:
                    migliore = (len(m.group(0)), cat)
        return migliore[1] if migliore else None
    info = cfg.mcc().get(mcc_code) if mcc_code else None
    info_cat = tax.risolvi(info["l2"], info.get("l1")) if info and info.get("l2") else None
    mcc_dec = info_cat if info and info["generico"] == "no" else None
    alta = regola("alta") if testo else None
    if alta:
        return "regola", alta, None
    if mcc_dec:
        return "mcc", mcc_dec, None
    bassa = regola("bassa") if testo else None
    if bassa:
        return "regola", bassa, None
    m = catena._marchio.__wrapped__(testo) if testo else None
    m_cat = tax.risolvi(m["l2"], m.get("l1")) if m else None
    if m_cat and m["decisivo"]:
        return "marchio", m_cat, None
    if m_cat:
        return None, None, catena._p(m_cat, "marchio %s" % m["marchio"])
    if info_cat:
        return None, None, catena._p(info_cat, "codice MCC generico %s" % mcc_code)
    return None, None, None


NOMI = ["ESSELUNGA", "ESSELUNGA MILANO VIA ROMA", "COOP LOMBARDIA", "IKEA CARUGATE", "DECATHLON", "AMAZON EU",
        "TRATTORIA GAMMA", "BAR DELTA", "ENI STATION", "PAYPAL *STEAM", "NEGOZIO THETA", "", None]


@pytest.mark.parametrize("mcc_code", [None, "5411", "5812", "5999"])
def test_memoria_non_cambia_i_risultati(conn, mcc_code):
    marchi = [m["marchio"].upper() for _, m in cfg.marchi()[::97]]           # un campione degli 8.108 marchi
    for nome in NOMI + marchi:
        attesa = _deterministica_senza_memoria(nome, mcc_code)
        assert catena.deterministica(nome, mcc_code) == attesa, nome
        assert catena.deterministica(nome, mcc_code) == attesa, nome          # seconda volta dalla memoria


def test_marchi_cercati_una_volta_per_nome(caricato):
    """Molti movimenti con pochi nomi: i marchi si cercano una volta per nome, e un nuovo ricalcolo (cioe' il
    salvataggio successivo) non li cerca di nuovo."""
    c, conti, _ = caricato
    nomi = ["NEGOZIO THETA", "BOTTEGA IOTA", "LIBRERIA KAPPA"]
    for i in range(60):
        _pagamento(c, conti["ing"], "R%d" % i, nomi[i % 3], data="2026-09-%02d" % (1 + i % 28))
    catena._marchio.cache_clear()
    ricalcola(c)
    primo = catena._marchio.cache_info()
    distinti = c.execute("SELECT count(DISTINCT esercente) FROM movimenti WHERE esercente IS NOT NULL").fetchone()[0]
    assert primo.misses <= distinti
    # i nomi che nessuna regola del programma ferma prima arrivano ai marchi: 20 movimenti ciascuno, una sola ricerca
    ai_marchi = [n for n in nomi if not catena._miglior_regola(n, "alta") and not catena._miglior_regola(n, "bassa")]
    assert ai_marchi
    assert primo.hits >= 19 * len(ai_marchi)
    ricalcola(c)
    assert catena._marchio.cache_info().misses == primo.misses             # nessuna nuova ricerca


def test_ricalcolo_uguale_con_e_senza_memoria(caricato):
    c, _, _ = caricato

    def analisi():
        return [tuple(r) for r in c.execute("SELECT * FROM analisi ORDER BY mov_id, componente")]
    catena._marchio.cache_clear()
    catena._regole_che_corrispondono.cache_clear()
    ricalcola(c)
    a = analisi()
    ricalcola(c)                                                           # tutto dalla memoria
    assert analisi() == a
