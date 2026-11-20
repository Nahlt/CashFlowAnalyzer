"""Novita' della 0.19.1: la Revisione dei bonifici non chiede piu' i movimenti gia' decisi, periodo delle analisi
proposto all'apertura (Impostazioni), colonna Ora nelle tabelle dei movimenti, ora letta dalla descrizione Fineco.
Tutti i dati sono sintetici."""
from datetime import date

import pytest

from spese import analisi as an
from spese import configurazione as cfg
from spese import importa, servizi
from spese.db import scrivi_impostazione
from spese.lettore import ora_da_descrizione
from spese.ricalcolo import ricalcola


# --- pagine (AppTest) ----------------------------------------------------------------------------------------
def _pagina():
    import os

    import streamlit as st
    from spese import analisi, db, percorsi
    from spese.tassonomia_utente import carica
    from spese.ui import comune, pagine_dati, pagine_disponibilita, pagine_impostazioni
    if "conn" not in st.session_state:
        st.session_state["conn"] = db.apri(percorsi.file_database(), "passphrase di prova")
        carica(st.session_state["conn"])
    nome = os.environ["PAGINA_PROVA"]
    if nome == "filtri":
        with st.sidebar:
            comune.mostra_filtri()
        return
    st.session_state["filtri_attivi"] = analisi.Filtri("tutti")
    {"revisione": pagine_dati.revisione, "movimenti": pagine_dati.movimenti,
     "impostazioni": pagine_impostazioni.impostazioni, "disponibilita": pagine_disponibilita.saldo_conti}[nome]()


@pytest.fixture()
def pagina(caricato, monkeypatch):
    from streamlit.testing.v1 import AppTest

    def apri(nome, **stato):
        monkeypatch.setenv("PAGINA_PROVA", nome)
        at = AppTest.from_function(_pagina, default_timeout=60)
        for k, v in stato.items():
            at.session_state[k] = v
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        return at
    return caricato, apri


def _oggi(monkeypatch, giorno, *moduli):
    """date.today() fisso nei moduli indicati (importano `date` da datetime)."""
    class Data(date):
        @classmethod
        def today(cls):
            return date.fromisoformat(giorno)
    for m in moduli:
        monkeypatch.setattr(m, "date", Data)


def _sottotitoli(at):
    return [s.value for s in at.subheader]


def _mov(c, conto, importo, stato=None):
    q = ("SELECT m.id FROM movimenti m JOIN trasferimenti t ON t.mov_id = m.id WHERE m.conto_id = ? "
         "AND m.importo_cent = ?" + (" AND t.stato = ?" if stato else ""))
    return c.execute(q, (conto, importo) + ((stato,) if stato else ())).fetchone()[0]


# --- 1. Revisione dei bonifici -------------------------------------------------------------------------------
def test_bonifico_escluso_non_compare_in_revisione(pagina):
    """Il bonifico di 80 € a una persona (Credem) senza categoria e' in "Bonifici con altre persone"; segnato
    Escluso dalle analisi in Movimenti non compare piu' (e il contatore del menu gia' non lo contava)."""
    (c, conti, _), apri = pagina
    assert "Bonifici con altre persone o aziende" in _sottotitoli(apri("revisione"))
    mid = _mov(c, conti["credem"], -8000, "terzi")
    servizi.decidi_movimento(c, mid, natura="escluso")
    ricalcola(c)
    assert c.execute("SELECT natura, l2, da_rivedere FROM analisi WHERE mov_id = ? AND componente = 'principale'",
                     (mid,)).fetchone()[:] == ("escluso", None, 0)
    assert "Bonifici con altre persone o aziende" not in _sottotitoli(apri("revisione"))


def test_bonifico_a_tuo_nome_deciso_non_compare(pagina):
    """Il bonifico a tuo nome senza contropartita (Trade Republic, 200 €) deciso a mano come trasferimento
    interno non si chiede piu'."""
    (c, conti, _), apri = pagina
    assert "Trasferimenti a tuo nome senza contropartita" in _sottotitoli(apri("revisione"))
    servizi.decidi_movimento(c, _mov(c, conti["tr"], -20000, "proposto_proprio"), natura="trasferimento_interno")
    ricalcola(c)
    assert "Trasferimenti a tuo nome senza contropartita" not in _sottotitoli(apri("revisione"))


def test_coppia_proposta_sparisce_con_una_decisione(pagina):
    """Una coppia proposta (qui costruita a mano) sparisce se decidi anche solo uno dei due movimenti."""
    (c, conti, _), apri = pagina
    uscita, entrata = _mov(c, conti["ing"], -50000), _mov(c, conti["credem"], 50000)
    c.execute("UPDATE trasferimenti SET stato = 'terzi', controparte_mov_id = ? WHERE mov_id = ?", (entrata, uscita))
    assert "Possibili trasferimenti tra tuoi conti" in _sottotitoli(apri("revisione"))
    servizi.decidi_movimento(c, entrata, natura="escluso")                 # la decisione sull'altro lato basta
    assert "Possibili trasferimenti tra tuoi conti" not in _sottotitoli(apri("revisione"))


def test_bonifici_ai_familiari_decisi(caricato):
    """Bonifico a un familiare: escluso a mano sparisce; con una categoria resta, per poterla cambiare."""
    c, conti, _ = caricato
    scrivi_impostazione(c, "familiari", ["Giulia Bianchi"])
    ricalcola(c)
    mid = servizi.spese_tramite_familiari(c)[0]["mov_id"]
    servizi.decidi_movimento(c, mid, "Auto: noleggio/rata")
    ricalcola(c)
    assert [r["mov_id"] for r in servizi.spese_tramite_familiari(c)] == [mid]
    servizi.decidi_movimento(c, mid, natura="escluso")
    ricalcola(c)
    assert servizi.spese_tramite_familiari(c) == []


# --- 2. Periodo proposto all'apertura ------------------------------------------------------------------------
DMIN, DMAX, OGGI = "2024-03-10", "2026-11-15", "2026-11-19"


@pytest.mark.parametrize("voce, mese_in_corso, atteso", [
    ("intero", False, ("2024-03-10", "2026-11-15")),
    ("mese_corrente", False, ("2026-11-01", "2026-11-15")),
    ("mese_scorso", False, ("2026-10-01", "2026-10-31")),
    ("mese_scorso", True, ("2026-10-01", "2026-11-15")),
    ("3_mesi", False, ("2026-08-01", "2026-10-31")),
    ("6_mesi", False, ("2026-05-01", "2026-10-31")),
    ("6_mesi", True, ("2026-05-01", "2026-11-15")),
    ("anno_corrente", False, ("2026-01-01", "2026-11-15")),
    ("12_mesi", False, ("2025-11-01", "2026-10-31")),
    ("24_mesi", False, ("2024-11-01", "2026-10-31")),
    ("anno_corrente", True, ("2026-01-01", "2026-11-15")),          # la casella vale solo per i mesi interi
])
def test_periodo_predefinito(voce, mese_in_corso, atteso):
    da, a, nota = an.periodo_predefinito(voce, mese_in_corso, OGGI, DMIN, DMAX)
    assert (da.isoformat(), a.isoformat(), nota) == atteso + (None,)


def test_periodo_predefinito_casi_limite():
    p = an.periodo_predefinito
    # passaggio d'anno
    assert p("3_mesi", False, "2027-01-05", DMIN, "2027-01-04")[:2] == (date(2026, 10, 1), date(2026, 12, 31))
    assert p("mese_scorso", False, "2027-01-05", DMIN, "2027-01-04")[:2] == (date(2026, 12, 1), date(2026, 12, 31))
    # dati che iniziano dopo: il periodo si restringe
    assert p("24_mesi", False, OGGI, "2025-06-10", DMAX)[:2] == (date(2025, 6, 10), date(2026, 10, 31))
    # nessun dato nel periodo (estratti importati fino a ottobre): l'ultimo mese con movimenti, con la nota
    da, a, nota = p("mese_corrente", False, OGGI, DMIN, "2026-10-28")
    assert (da, a) == (date(2026, 10, 1), date(2026, 10, 28)) and "ultimo mese con movimenti" in nota


def test_leggi_periodo_predefinito(conn):
    assert an.leggi_periodo_predefinito(conn) == ("intero", False)            # come prima della 0.19.1
    scrivi_impostazione(conn, an.CHIAVE_PERIODO, {"voce": "3_mesi", "mese_in_corso": True})
    assert an.leggi_periodo_predefinito(conn) == ("3_mesi", True)
    scrivi_impostazione(conn, an.CHIAVE_PERIODO, {"voce": "boh"})
    assert an.leggi_periodo_predefinito(conn) == ("intero", False)


def test_filtri_si_aprono_col_periodo_predefinito(pagina, monkeypatch):
    """Dati sintetici di settembre-ottobre 2026. Con "Mese in corso" e oggi 20/10 il filtro parte dal 01/10; con
    oggi 10/12 a dicembre non c'e' niente e si apre sull'ultimo mese con movimenti, con la nota."""
    from spese.ui import comune
    (c, _, _), apri = pagina
    scrivi_impostazione(c, an.CHIAVE_PERIODO, {"voce": "mese_corrente", "mese_in_corso": False})
    _oggi(monkeypatch, "2026-10-20", comune)
    at = apri("filtri")
    assert at.date_input(key="f_periodo").value == (date(2026, 10, 1), date(2026, 10, 20))
    _oggi(monkeypatch, "2026-12-10", comune)
    at = apri("filtri")
    da, a = at.date_input(key="f_periodo").value
    assert da == date(2026, 10, 1) and a.month == 10
    assert any("ultimo mese con movimenti" in x.value for x in at.sidebar.caption)


def test_filtri_senza_impostazione_intero_periodo(pagina):
    (c, _, _), apri = pagina
    dmin, dmax = an.periodo_disponibile(c, [r[0] for r in c.execute("SELECT id FROM conti WHERE importato = 1")])
    at = apri("filtri")
    assert at.date_input(key="f_periodo").value == (date.fromisoformat(dmin), date.fromisoformat(dmax))


def test_impostazione_periodo(pagina):
    (c, _, _), apri = pagina
    at = apri("impostazioni")
    assert at.checkbox(key="imp_periodo_mese").disabled                    # "Intero periodo": niente da aggiungere
    at.selectbox(key="imp_periodo").set_value("6_mesi").run()
    assert not at.checkbox(key="imp_periodo_mese").disabled
    at.checkbox(key="imp_periodo_mese").check().run()
    assert not at.exception
    assert an.leggi_periodo_predefinito(c) == ("6_mesi", True)


def test_saldo_dei_conti_usa_il_periodo_predefinito(pagina, monkeypatch):
    from spese.ui import pagine_disponibilita
    (c, conti, _), apri = pagina
    scrivi_impostazione(c, an.CHIAVE_PERIODO, {"voce": "mese_scorso", "mese_in_corso": False})
    _oggi(monkeypatch, "2026-11-19", pagine_disponibilita)
    at = apri("disponibilita", disp_conto=conti["ing"], disp_modo="periodo")
    assert at.session_state["disp_periodo"] == (date(2026, 10, 1), date(2026, 10, 31))
    assert {m.label: m.value for m in at.metric}["Saldo a fine periodo"] == "1.890,00 €"


# --- 3. Ora ----------------------------------------------------------------------------------------------------
def test_ora_dalla_descrizione_fineco():
    p = cfg.profili()["fineco"]
    assert ora_da_descrizione(p, "Pag. del 10/04/26 ora 12:30 presso: BAR ROSSI MILANO Carta N° ****1234") == "12:30"
    assert ora_da_descrizione(p, "Pag. del 1 0/04/26 or a 1 2:30 presso: BAR") == "12:30"     # spezzato dalla banca
    assert ora_da_descrizione(p, "Ben: MARIO ROSSI Ins: 20/11/2026 14:35:12 Da: x Iban: IT00X") == "14:35"
    assert ora_da_descrizione(p, "Beneficiario: X Data Inserimento: 20/11/2026 09:05 Canale: web") == "09:05"
    assert ora_da_descrizione(p, "Pag. del 10/04/26 ora 25:30 presso: BAR") is None               # ora non valida
    assert ora_da_descrizione(p, "Bonifico senza ora") is None
    assert ora_da_descrizione(cfg.profili()["credem"], "Pag. del 10/04/26 ora 12:30") is None    # regola solo Fineco


def test_ora_completata_sui_movimenti_gia_importati(conn):
    """Un movimento Fineco importato prima della regola riceve l'ora al ricalcolo; una seconda volta non cambia
    niente, e un'ora gia' presente non si tocca."""
    conto = importa.crea_conto(conn, "Fineco", "fineco", "conto", "personale", "Fineco")
    for mid, descr, ora in (("F1", "Pag. del 10/04/26 ora 12:30 presso: BAR", None),
                            ("F2", "Pag. del 10/04/26 ora 12:30 presso: BAR", "08:00"),
                            ("F3", "Bonifico senza ora", None)):
        conn.execute("INSERT INTO movimenti(id, conto_id, data, importo_cent, tipo, descrizione, ora) "
                     "VALUES (?,?,?,?,?,?,?)", (mid, conto, "2026-04-10", -500, "pagamento_carta", descr, ora))
    ricalcola(conn)
    assert dict(conn.execute("SELECT id, ora FROM movimenti ORDER BY id").fetchall()) == {
        "F1": "12:30", "F2": "08:00", "F3": None}
    assert importa.completa_ore(conn) == 0


def test_colonna_ora_in_movimenti(pagina):
    """Le tre spese con carta Credem hanno l'ora (dalla descrizione); i bonifici no. A parita' di giorno, prima
    la piu' tarda."""
    _, apri = pagina
    at = apri("movimenti")
    df = at.dataframe[0].value
    assert list(df.columns[:3]) == ["Data", "Ora", "Conto"]
    assert {"17:58", "19:25"} <= set(df["Ora"])
    assert "" in set(df["Ora"])


def test_colonna_ora_in_saldo_dei_conti(pagina):
    (c, conti, _), apri = pagina
    at = apri("disponibilita", disp_conto=conti["tr"], disp_modo="periodo",
              disp_periodo=(date(2026, 10, 1), date(2026, 10, 31)))
    assert not at.dataframe                                   # Trade Republic senza saldo noto: niente tabella
    servizi.imposta_saldo(c, conti["tr"], "2026-10-31", 250000)
    at = apri("disponibilita", disp_conto=conti["tr"], disp_modo="periodo",
              disp_periodo=(date(2026, 10, 1), date(2026, 10, 31)))
    ore = list(at.dataframe[0].value["Ora"])
    assert ore and all(len(x) == 5 for x in ore)               # Trade Republic ha l'ora su ogni movimento
