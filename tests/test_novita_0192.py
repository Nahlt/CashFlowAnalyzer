"""Novita' della 0.19.2: bonifici tra un familiare e un conto personale nella sezione "Bonifici con i familiari"
(classificati a mano uno per uno, senza imparare dal nome), switch accesi all'apertura, conferma delle proposte
nascoste, causale Credit Agricole. Tutti i dati sono sintetici (nomi inventati)."""
import pandas as pd
import pytest

from spese import servizi
from spese.db import scrivi_impostazione
from spese.ricalcolo import ricalcola

FAMILIARI = ["Giulia Bianchi", "  BIANCHI   GIULIA  "]      # due righe per la stessa persona, con spazi in piu'


def _bonifico(c, conto, mid, importo, nome, descrizione, data="2026-10-12"):
    c.execute("INSERT INTO movimenti(id, conto_id, data, importo_cent, tipo, controparte_nome, descrizione) "
              "VALUES (?,?,?,?,?,?,?)", (mid, conto, data, importo, "bonifico", nome, descrizione))


def _esito(c, mid):
    return tuple(c.execute(
        "SELECT t.stato, a.natura, a.l2, a.importo_cent FROM trasferimenti t JOIN analisi a ON a.mov_id = t.mov_id "
        "AND a.componente = 'principale' WHERE t.mov_id = ?", (mid,)).fetchone())


@pytest.fixture()
def con_familiare(caricato):
    """ING (conto personale) riceve 150 € da Giulia e le manda 30 €; il nome e' scritto in modi diversi."""
    c, conti, rep = caricato
    _bonifico(c, conti["ing"], "FAM-IN", 15000, "BIANCHI GIULIA",
              "ORD:BIANCHI GIULIA DT.ORD:121026 DESCR.OPERAZIONE SCT:SPESE SUPERMERCATO")
    _bonifico(c, conti["ing"], "FAM-OUT", -3000, " giulia   bianchi ", "Bonifico a giulia bianchi cinema")
    return c, conti, rep


# --- ricalcolo -------------------------------------------------------------------------------------------------
def test_familiare_su_conto_personale(con_familiare):
    """Senza familiari indicati i due bonifici sono "con altre persone"; con Giulia tra i familiari vanno tra i
    bonifici con i familiari, da classificare, e contano come prima (entrata 150 €, uscita 30 €)."""
    c, conti, _ = con_familiare
    ricalcola(c)
    assert _esito(c, "FAM-IN") == ("terzi", "entrata", None, 15000)
    scrivi_impostazione(c, "familiari", FAMILIARI)
    ricalcola(c)
    assert _esito(c, "FAM-IN") == ("spesa_familiare", "entrata", None, 15000)
    assert _esito(c, "FAM-OUT") == ("spesa_familiare", "uscita", None, -3000)
    righe = {r["mov_id"]: r for r in servizi.spese_tramite_familiari(c)}
    assert righe["FAM-IN"]["perimetro"] == "personale" and righe["FAM-IN"]["familiare"] == "Giulia Bianchi"
    assert righe["FAM-IN"]["causale"] == "SPESE SUPERMERCATO"
    assert righe["FAM-IN"]["conto"] == "ING"
    # il bonifico dal conto comune resta com'era
    comune = [r for r in righe.values() if r["perimetro"] == "comune"]
    assert len(comune) == 1 and comune[0]["importo_cent"] == -8000


def test_non_impara_dal_nome(con_familiare):
    """Classificato a mano il primo bonifico di Giulia, un secondo bonifico di Giulia dello stesso importo resta
    da classificare: la categoria non passa dal nome."""
    c, conti, _ = con_familiare
    scrivi_impostazione(c, "familiari", FAMILIARI)
    ricalcola(c)
    servizi.decidi_movimento(c, "FAM-IN", ("ENTRATE", "Regali ricevuti"))
    _bonifico(c, conti["ing"], "FAM-IN2", 15000, "BIANCHI GIULIA", "ORD:BIANCHI GIULIA SCT:PRESTITO", "2026-10-20")
    ricalcola(c)
    assert _esito(c, "FAM-IN") == ("spesa_familiare", "entrata", "Regali ricevuti", 15000)
    assert _esito(c, "FAM-IN2") == ("spesa_familiare", "entrata", None, 15000)


def test_rimborso_di_una_spesa(con_familiare):
    """Giulia ti restituisce 150 € di una spesa al supermercato: con la categoria di spesa il bonifico diventa un
    rimborso, cioe' riduce le uscite di Alimentari invece di aumentare le entrate."""
    from spese import analisi as an
    c, conti, _ = con_familiare
    scrivi_impostazione(c, "familiari", FAMILIARI)
    ricalcola(c)
    f = an.Filtri("personale", conti=[conti["ing"]], da="2026-10-01", a="2026-10-31")
    totali = lambda: an.flussi_mensili(c, f)[["entrate", "uscite"]].sum()
    prima = totali()
    servizi.decidi_movimento(c, "FAM-IN", ("ALIMENTARI", "Supermercato"))
    ricalcola(c)
    stato, natura, l2, importo = _esito(c, "FAM-IN")
    assert (stato, l2, importo) == ("spesa_familiare", "Supermercato", 15000) and natura in ("uscita", "rimborso")
    dopo = totali()
    assert dopo["entrate"] == prima["entrate"] - 15000
    assert dopo["uscite"] == prima["uscite"] - 15000


def test_opzioni_per_tipo_di_bonifico():
    from spese.ui import pagine_dati as pd_
    riga = lambda per, imp: {"perimetro": per, "importo_cent": imp}
    entrate = pd_._opzioni_familiari(pd.DataFrame([riga("personale", 15000)]))
    assert ("ENTRATE", "Regali ricevuti") in entrate and ("ALIMENTARI", "Supermercato") in entrate
    assert pd_.RESTITUISCE not in entrate
    uscite = pd_._opzioni_familiari(pd.DataFrame([riga("personale", -3000)]))
    assert ("ENTRATE", "Regali ricevuti") not in uscite and pd_.RESTITUISCE not in uscite
    assert pd_._opzioni_familiari(pd.DataFrame([riga("comune", -8000)]))[0] == pd_.RESTITUISCE
    misto = pd_._opzioni_familiari(pd.DataFrame([riga("comune", -8000), riga("personale", 15000)]))
    assert pd_.RESTITUISCE not in misto and ("ENTRATE", "Regali ricevuti") not in misto


def test_causale_credit_agricole():
    assert servizi.causale_bonifico("ORD:BIANCHI GIULIA DT.ORD:080626 DESCR.OPERAZIONE SCT:PRESTITO CASA",
                                    "Giulia Bianchi") == "PRESTITO CASA"
    assert servizi.causale_bonifico("BON. ISTANTANEO A DEBITO Mario Rossi Rif. 0001-008 rata auto",
                                    "Mario Rossi") == "rata auto"


# --- pagina Revisione (AppTest) ----------------------------------------------------------------------------
def _pagina():
    import streamlit as st
    from spese import analisi, db, percorsi
    from spese.tassonomia_utente import carica
    from spese.ui import pagine_dati
    if "conn" not in st.session_state:
        st.session_state["conn"] = db.apri(percorsi.file_database(), "passphrase di prova")
        carica(st.session_state["conn"])
    st.session_state["filtri_attivi"] = analisi.Filtri("tutti")
    pagine_dati.revisione()


def _apri():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_function(_pagina, default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _tabella_familiari(at):
    return next((d.value for d in at.dataframe if "Familiare" in d.value.columns), None)


def test_revisione_switch_accesi_e_conto(con_familiare):
    c, conti, _ = con_familiare
    scrivi_impostazione(c, "familiari", FAMILIARI)
    ricalcola(c)
    at = _apri()
    assert "Bonifici con i familiari" in [s.value for s in at.subheader]
    assert at.toggle(key="fam_solo").value and at.toggle(key="fam_senza_prop").value
    t = _tabella_familiari(at)
    assert list(t.columns[:4]) == ["Data", "Ora", "Conto", "Familiare"]
    assert set(t["Conto"]) == {"ING", "Credem comune"} and len(t) == 3
    # tutti classificati: con gli switch accesi resta solo il messaggio
    for mid in ("FAM-IN", "FAM-OUT"):
        servizi.decidi_movimento(c, mid, ("ALIMENTARI", "Supermercato"))
    servizi.decidi_movimento(c, servizi.spese_tramite_familiari(c)[-1]["mov_id"], ("ALIMENTARI", "Supermercato"))
    ricalcola(c)
    at = _apri()
    assert _tabella_familiari(at) is None
    assert any("hanno una categoria" in s.value for s in at.success)
    t = _tabella_familiari(at.toggle(key="fam_solo").set_value(False).run())
    assert len(t) == 3 and "Supermercato (rimborso)" in " ".join(t["Categoria"])


def test_proposte_solo_conto_comune_e_conferma(con_familiare):
    """Conto comune: un secondo bonifico a Giulia di 80 € dopo uno classificato ha la proposta, e' nascosto e si
    conferma col pulsante. Conto personale: stesso importo, nessuna proposta."""
    c, conti, _ = con_familiare
    scrivi_impostazione(c, "familiari", FAMILIARI)
    ricalcola(c)
    primo = [r for r in servizi.spese_tramite_familiari(c) if r["perimetro"] == "comune"][0]["mov_id"]
    servizi.decidi_movimento(c, primo, ("ALIMENTARI", "Supermercato"))
    servizi.decidi_movimento(c, "FAM-IN", ("ENTRATE", "Regali ricevuti"))
    _bonifico(c, conti["credem"], "COM-2", -8000, "Giulia Bianchi", "BONIFICO A Giulia Bianchi spesa", "2026-10-25")
    _bonifico(c, conti["ing"], "FAM-IN2", 15000, "BIANCHI GIULIA", "ORD:BIANCHI GIULIA SCT:ALTRO", "2026-10-25")
    ricalcola(c)
    at = _apri()
    t = _tabella_familiari(at)
    assert "FAM-IN2" not in t.index and set(t["Conto"]) == {"ING"}        # COM-2 nascosto, FAM-IN2 senza proposta
    assert set(t["Proposta"]) == {""}
    at.button(key="fam_conf_tutte_0").click().run()
    assert not at.exception
    assert _esito(c, "COM-2")[2] == "Supermercato"
    assert _esito(c, "FAM-IN2")[2] is None
