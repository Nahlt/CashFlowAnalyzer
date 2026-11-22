"""Novita' della 0.20.0: Portafoglio con l'analisi attualizzata a oggi (tutta la storia, posizioni chiuse,
rendimento delle aperte e complessivo) o nel periodo selezionato (posizioni chiuse prima del periodo a richiesta);
grafici che si fermano alla vendita; prezzi non aggiornati segnalati e riprovati. Tutti i dati sono sintetici."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from spese import importa, mercato
from spese import portafoglio as pf
from spese.db import leggi_impostazione, scrivi_impostazione


def _mov(conn, conto, mid, data, tipo, isin, q, imp, nome=None, tax=0):
    conn.execute("INSERT INTO movimenti(id, conto_id, data, importo_cent, tipo, isin, nome_titolo, quantita, fee_cent, "
                 "tax_cent) VALUES (?,?,?,?,?,?,?,?,0,?)", (mid, conto, data, imp, tipo, isin, nome or "Titolo " + isin[-1],
                                                            q, tax))


def _prezzi(conn, isin, punti):
    conn.executemany("INSERT OR REPLACE INTO prezzi_mercato(isin, data, prezzo) VALUES (?,?,?)",
                     [(isin, d, str(v)) for d, v in punti])


@pytest.fixture()
def storico(conn):
    """Quattro titoli su un conto Trade Republic (nessuna commissione, per numeri tondi):
    A: 10 quote a 100 € il 10/01/2025, 5 vendute a 120 € il 10/03/2025 (+100 €), dividendo di 20 € -> aperta;
    B: 4 quote a 50 € il 03/02/2025, vendute tutte a 40 € il 05/05/2025 (-40 €) -> chiusa prima del periodo 2026;
    C: 2 quote a 200 € il 01/06/2025, vendute tutte a 250 € il 15/03/2026 (+100 €) -> chiusa dentro il periodo;
    D: 1 quota a 300 € il 01/07/2025, venduta a 330 € il 15/09/2026 (+30 €) -> chiusa dopo il periodo."""
    tr = importa.crea_conto(conn, "TR", "trade_republic", "conto", "personale", "TR")
    _mov(conn, tr, "a1", "2025-01-10", "acquisto_titoli", "IE00TESTAAA1", "10", -100000, "ETF A")
    _mov(conn, tr, "a2", "2025-03-10", "vendita_titoli", "IE00TESTAAA1", "-5", 60000, "ETF A")
    _mov(conn, tr, "a3", "2025-06-20", "dividendo", "IE00TESTAAA1", "5", 2000, "ETF A")
    _mov(conn, tr, "b1", "2025-02-03", "acquisto_titoli", "IE00TESTBBB2", "4", -20000, "ETF B")
    _mov(conn, tr, "b2", "2025-05-05", "vendita_titoli", "IE00TESTBBB2", "-4", 16000, "ETF B")
    _mov(conn, tr, "c1", "2025-06-01", "acquisto_titoli", "IE00TESTCCC3", "2", -40000, "ETF C")
    _mov(conn, tr, "c2", "2026-03-15", "vendita_titoli", "IE00TESTCCC3", "-2", 50000, "ETF C")
    _mov(conn, tr, "d1", "2025-07-01", "acquisto_titoli", "IE00TESTDDD4", "1", -30000, "ETF D")
    _mov(conn, tr, "d2", "2026-09-15", "vendita_titoli", "IE00TESTDDD4", "-1", 33000, "ETF D")
    return conn, tr


# --- calcolo -------------------------------------------------------------------------------------------------
def test_rendimento_complessivo_esempio_della_proposta(conn):
    """10 quote a 100 €, 5 vendute a 120 € (+100 €), le 5 rimaste valgono 110 € (+50 €): aperte +10%,
    complessivo (100 + 50) / 1000 = +15%. Con un dividendo lordo di 20 €: +17%."""
    tr = importa.crea_conto(conn, "TR", "trade_republic", "conto", "personale", "TR")
    _mov(conn, tr, "a1", "2025-01-10", "acquisto_titoli", "IE00TESTAAA1", "10", -100000)
    _mov(conn, tr, "a2", "2025-03-10", "vendita_titoli", "IE00TESTAAA1", "-5", 60000)
    r = pf.calcola(conn)
    p = pf.valorizza(r["posizioni"], {"IE00TESTAAA1": [("2026-11-20", Decimal("110"))]}, "2026-11-22")[0]
    assert (p["costo"], p["valore"], p["latente"], p["realizzato"], p["costo_venduto"]) == (50000, 55000, 5000, 10000, 50000)
    assert p["rendimento"] == pytest.approx(0.10) and p["rendimento_complessivo"] == pytest.approx(0.15)
    t = pf.totali(r["posizioni"])
    assert (t["rendimento_aperte"], t["rendimento_complessivo"]) == (pytest.approx(0.10), pytest.approx(0.15))
    assert (t["latente"], t["complessivo"], t["capitale"], t["valore"]) == (5000, 15000, 50000, 55000)
    _mov(conn, tr, "a3", "2025-06-20", "dividendo", "IE00TESTAAA1", "5", 2000)
    r = pf.calcola(conn)
    p = pf.valorizza(r["posizioni"], {"IE00TESTAAA1": [("2026-11-20", Decimal("110"))]}, "2026-11-22")[0]
    assert p["rendimento_complessivo"] == pytest.approx(0.17)


def test_attualizzata_comprende_tutta_la_storia(storico):
    conn, _ = storico
    r = pf.calcola(conn)                                    # attualizzata: nessun periodo
    pos = {p["isin"][-1]: p for p in r["posizioni"]}
    assert [p["isin"][-1] for p in r["posizioni"]] == ["1", "4", "3", "2"]    # aperte, poi chiuse dalla piu' recente
    assert pos["1"]["aperta"] and pf.stato(pos["1"]) == "venduta in parte"
    assert [(pos[k]["chiusa_il"], pos[k]["realizzato"]) for k in "234"] == [
        ("2025-05-05", -4000), ("2026-03-15", 10000), ("2026-09-15", 3000)]
    assert pf.stato(pos["2"]) == "chiusa il 05/05/2025"
    pf.valorizza(r["posizioni"], {"IE00TESTAAA1": [("2026-11-20", Decimal("110"))]}, "2026-11-22")
    # chiuse: rendimento complessivo = realizzato sul costo delle quote vendute
    assert pos["2"]["rendimento_complessivo"] == pytest.approx(-0.20)
    t = pf.totali(r["posizioni"])
    # capitale e valore solo delle aperte; complessivo: 50 + 100 + 20 (A) - 40 (B) + 100 (C) + 30 (D) = 260 € su
    # 1000 (A) + 200 (B) + 400 (C) + 300 (D) = 1900 €
    assert (t["capitale"], t["valore"], t["complessivo"]) == (50000, 55000, 26000)
    assert t["rendimento_complessivo"] == pytest.approx(260 / 1900) and t["rendimento_aperte"] == pytest.approx(0.10)
    assert r["anni"]["2025"]["plus"] == 10000 and r["anni"]["2026"]["plus"] == 13000     # realizzato su tutta la storia


def test_periodo_vendute_prima_durante_dopo(storico):
    conn, _ = storico
    r = pf.calcola(conn, da="2026-01-01", a="2026-06-30")
    pos = {p["isin"][-1]: p for p in r["posizioni"]}
    assert pos["4"]["aperta"] and pos["4"]["quote"] == 1           # venduta dopo il periodo: ancora posseduta
    assert not pos["3"]["aperta"] and pos["3"]["chiusa_il"] == "2026-03-15"   # venduta nel periodo
    assert [p["isin"][-1] for p in pf.chiuse_prima(r["posizioni"], "2026-01-01")] == ["2"]
    assert pf.chiuse_prima(r["posizioni"], None) == []
    # realizzato del periodo: solo C; rendimento complessivo per titolo dalla nascita alla fine del periodo
    assert [v["isin"][-1] for v in r["vendite"]] == ["3"] and r["anni"] == {"2026": {"disinvestito": 50000, "plus": 10000,
                                                                                       "commissioni": 0, "imposte": 0}}
    assert pos["1"]["realizzato"] == 10000                       # vendita del 2025 compresa (scelta 2a)
    assert "IE00TESTDDD4" not in {v["isin"] for v in r["vendite"]}


def test_andamento_si_ferma_alla_vendita(conn):
    tr = importa.crea_conto(conn, "TR", "trade_republic", "conto", "personale", "TR")
    _mov(conn, tr, "x1", "2025-01-02", "acquisto_titoli", "IE00TESTXXX9", "2", -20000)
    _mov(conn, tr, "x2", "2025-01-06", "vendita_titoli", "IE00TESTXXX9", "-2", 24000)
    _mov(conn, tr, "x3", "2025-01-09", "acquisto_titoli", "IE00TESTXXX9", "1", -11000)
    _mov(conn, tr, "x4", "2025-01-10", "vendita_titoli", "IE00TESTXXX9", "-1", 11500)
    prezzi = [("2025-01-02", Decimal(100)), ("2025-01-03", Decimal(105)), ("2025-01-06", Decimal(120)),
              ("2025-01-07", Decimal(118)), ("2025-01-08", Decimal(117)), ("2025-01-09", Decimal(110)),
              ("2025-01-10", Decimal(115)), ("2025-01-13", Decimal(116)), ("2025-01-14", Decimal(119))]
    passi = pf.calcola(conn)["passi"]["IE00TESTXXX9"]
    s = pf.andamento(passi, prezzi, a="2025-01-14", interrompi=True)
    assert [(d, int(q), x, v) for d, q, x, v in s] == [
        ("2025-01-02", 2, 20000, 20000), ("2025-01-03", 2, 20000, 21000),
        ("2025-01-06", 2, 20000, 24000),        # giorno della vendita: le quote tenute fino a ieri, al prezzo del giorno
        ("2025-01-07", 0, None, None),          # buco: senza quote fino al nuovo acquisto
        ("2025-01-09", 1, 11000, 11000), ("2025-01-10", 1, 11000, 11500)]      # niente in coda dopo la vendita
    # senza interrompi (totali del portafoglio): la posizione resta a zero fino alla fine
    assert pf.andamento(passi, prezzi, a="2025-01-14")[-1][1:] == (0, 0, 0)
    # senza prezzi (capitale investito): eventi fino alla vendita
    assert [(d, x) for d, _, x, _ in pf.andamento(passi, None, a="2025-01-14", interrompi=True)] == [
        ("2025-01-02", 20000), ("2025-01-06", 20000), ("2025-01-09", 11000), ("2025-01-10", 11000)]


def test_prezzo_vecchio(storico):
    conn, _ = storico
    r = pf.calcola(conn)
    pf.valorizza(r["posizioni"], {"IE00TESTAAA1": [("2026-11-10", Decimal("110"))]}, "2026-11-22")
    a = r["posizioni"][0]
    assert pf.prezzo_vecchio(a, "2026-11-22") and not pf.prezzo_vecchio(a, "2026-11-14")


# --- prezzi non aggiornati --------------------------------------------------------------------------------------
class _Yahoo:
    """Chiusure finte per i simboli indicati; i simboli in `rotti` non esistono, con `giu` la rete e' assente."""

    def __init__(self, rotti=(), giu=False):
        self.rotti, self.giu, self.richieste = set(rotti), giu, []

    def get(self, percorso, par):
        from datetime import datetime, timezone
        self.richieste.append(percorso)
        if self.giu:
            raise ConnectionError("rete assente")
        sim = percorso.rsplit("/", 1)[1]
        if sim in self.rotti:
            return {"chart": {"result": None, "error": {"description": "No data found"}}}
        oggi = date.today()
        giorni = [oggi - timedelta(days=i) for i in (3, 2, 1)]
        return {"chart": {"result": [{"meta": {"currency": "EUR", "gmtoffset": 0},
                                      "timestamp": [int(datetime(g.year, g.month, g.day, 12, tzinfo=timezone.utc).timestamp())
                                                    for g in giorni],
                                      "indicators": {"quote": [{"close": [100.0, 101.0, 102.0]}]}}], "error": None}}


def test_titolo_in_errore_non_segna_aggiornato_e_si_riprova(storico):
    conn, _ = storico
    for i, s in (("IE00TESTAAA1", "AAA.DE"), ("IE00TESTDDD4", "DDD.DE")):
        conn.execute("INSERT INTO titoli_mercato(isin, simbolo, scelto) VALUES (?,?,1)", (i, s))
    isin_dal = {"IE00TESTAAA1": date(2025, 1, 10), "IE00TESTDDD4": date(2025, 7, 1)}
    err = mercato.aggiorna(conn, isin_dal, http=_Yahoo(rotti={"DDD.DE"}))
    oggi = date.today().isoformat()
    assert set(err) == {"IE00TESTDDD4"}
    assert leggi_impostazione(conn, mercato.CHIAVE_ULTIMO) is None                  # non "aggiornato oggi"
    assert leggi_impostazione(conn, mercato.CHIAVE_TENTATIVO) == oggi
    assert mercato.non_aggiornati(conn) == ["IE00TESTDDD4"] and mercato.da_aggiornare(conn) is False  # spento
    scrivi_impostazione(conn, mercato.CHIAVE_ATTIVO, True)
    assert mercato.da_aggiornare(conn)
    # nuovo tentativo oggi: solo il titolo rimasto indietro
    assert mercato.da_scaricare(conn, isin_dal) == {"IE00TESTDDD4": date(2025, 7, 1)}
    y = _Yahoo()
    assert mercato.aggiorna(conn, mercato.da_scaricare(conn, isin_dal), http=y) == {}
    assert y.richieste == ["/v8/finance/chart/DDD.DE"]
    assert leggi_impostazione(conn, mercato.CHIAVE_ULTIMO) == oggi and mercato.non_aggiornati(conn) == []
    assert not mercato.da_aggiornare(conn)
    # un giorno nuovo: di nuovo tutti
    scrivi_impostazione(conn, mercato.CHIAVE_TENTATIVO, "2000-01-01")
    assert mercato.da_scaricare(conn, isin_dal) == isin_dal


def test_rete_assente_lascia_indietro_i_titoli_restanti(storico):
    conn, _ = storico
    for i, s in (("IE00TESTAAA1", "AAA.DE"), ("IE00TESTDDD4", "DDD.DE")):
        conn.execute("INSERT INTO titoli_mercato(isin, simbolo, scelto) VALUES (?,?,1)", (i, s))
    err = mercato.aggiorna(conn, {"IE00TESTAAA1": date(2025, 1, 10), "IE00TESTDDD4": date(2025, 7, 1)},
                           http=_Yahoo(giu=True))
    assert mercato.RETE in err and mercato.non_aggiornati(conn) == ["IE00TESTAAA1", "IE00TESTDDD4"]
    assert leggi_impostazione(conn, mercato.CHIAVE_ULTIMO) is None


def test_simbolo_a_mano_si_riscarica_anche_dopo_un_tentativo(storico):
    conn, _ = storico
    scrivi_impostazione(conn, mercato.CHIAVE_TENTATIVO, date.today().isoformat())
    scrivi_impostazione(conn, mercato.CHIAVE_FALLITI, [])
    mercato.imposta_simbolo(conn, "IE00TESTAAA1", "aaa.mi")
    assert mercato.non_aggiornati(conn) == ["IE00TESTAAA1"]
    assert list(mercato.da_scaricare(conn, {"IE00TESTAAA1": date(2025, 1, 10), "IE00TESTDDD4": date(2025, 7, 1)})) == \
        ["IE00TESTAAA1"]


# --- pagina (AppTest) ----------------------------------------------------------------------------------------
def _pagina():
    import os

    import streamlit as st
    from spese import analisi, db, percorsi
    from spese.tassonomia_utente import carica
    from spese.ui import pagine_analisi
    if "conn" not in st.session_state:
        st.session_state["conn"] = db.apri(percorsi.file_database(), "passphrase di prova")
        carica(st.session_state["conn"])
    da, a = (os.environ.get("PERIODO_PROVA") or ",").split(",")
    st.session_state["filtri_attivi"] = analisi.Filtri("tutti", da or None, a or None)
    pagine_analisi.portafoglio()


@pytest.fixture()
def pagina(storico, monkeypatch):
    from streamlit.testing.v1 import AppTest
    conn, _ = storico
    oggi = date.today().isoformat()
    scrivi_impostazione(conn, mercato.CHIAVE_ATTIVO, True)
    scrivi_impostazione(conn, mercato.CHIAVE_ULTIMO, oggi)               # niente rete nella prova
    scrivi_impostazione(conn, mercato.CHIAVE_TENTATIVO, oggi)
    for i, s in (("IE00TESTAAA1", "AAA.DE"), ("IE00TESTBBB2", "BBB.DE"), ("IE00TESTCCC3", "CCC.DE"),
                 ("IE00TESTDDD4", "DDD.DE")):
        conn.execute("INSERT INTO titoli_mercato(isin, simbolo, scelto, valuta) VALUES (?,?,1,'EUR')", (i, s))
    for i, v in (("IE00TESTAAA1", 110), ("IE00TESTBBB2", 45), ("IE00TESTCCC3", 240), ("IE00TESTDDD4", 320)):
        _prezzi(conn, i, [("2025-01-02", v), ("2026-06-30", v), (oggi, v)])

    def apri(periodo=None, **stato):
        monkeypatch.setenv("PERIODO_PROVA", periodo or ",")
        at = AppTest.from_function(_pagina, default_timeout=60)
        for k, v in stato.items():
            at.session_state[k] = v
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        return at
    return conn, apri


def _metriche(at):
    return {m.label: (m.value, m.delta) for m in at.metric}


def test_pagina_attualizzata_e_periodo(pagina):
    conn, apri = pagina
    at = apri("2026-01-01,2026-06-30")
    assert at.radio[0].value == "Analisi attualizzata a oggi"                 # all'apertura sempre attualizzata
    m = _metriche(at)
    assert m["Rendimento posizioni aperte"][0] == "+10,0%"
    assert m["Rendimento complessivo"][0] == "+13,7%" and m["Rendimento complessivo"][1] == "+260 €"   # 260/1900
    assert "Realizzato" in m and "Realizzato nel periodo" not in m
    tab = at.dataframe[0].value
    assert list(tab["Stato"]) == ["venduta in parte", "chiusa il 15/09/2026", "chiusa il 15/03/2026",
                                  "chiusa il 05/05/2025"]
    assert not at.toggle                                                      # nessun interruttore nell'attualizzata
    # nel periodo: B (chiusa prima) nascosta, D (venduta dopo) aperta, C chiusa nel periodo
    at.radio[0].set_value("Analisi nel periodo selezionato").run()
    assert not at.exception
    tab = at.dataframe[0].value
    assert list(tab["Titolo"]) == ["ETF A", "ETF D", "ETF C"]
    assert list(tab["Stato"]) == ["venduta in parte", "aperta", "chiusa il 15/03/2026"]
    m = _metriche(at)
    assert m["Realizzato nel periodo"][0].startswith("100,00")
    assert at.toggle[0].label.startswith("Includi le 1 posizioni chiuse prima del 01/01/2026")
    at.toggle[0].set_value(True).run()
    assert list(at.dataframe[0].value["Titolo"]) == ["ETF A", "ETF D", "ETF C", "ETF B"]


def test_pagina_avvisa_dei_prezzi_non_aggiornati(pagina):
    conn, apri = pagina
    scrivi_impostazione(conn, mercato.CHIAVE_FALLITI, ["IE00TESTDDD4"])
    scrivi_impostazione(conn, mercato.CHIAVE_ULTIMO, "2026-11-01")
    at = apri(_pf_tentato=date.today().isoformat())                           # tentativo gia' fatto: niente rete
    avvisi = [w.value for w in at.warning]
    assert any("Non e' stato possibile aggiornare i prezzi di: ETF D (IE00TESTDDD4)" in w for w in avvisi)
    assert any("non aggiornato 1 titolo" in c.value for c in at.caption)


def test_colore_delle_plus_e_delle_minus():
    """Tabella delle posizioni: plus verdi, minus rosse (latenti e realizzato), niente colore per zero e celle vuote."""
    from spese.ui.comune import NEGATIVO, POSITIVO, colore_segno
    assert colore_segno(31.76) == "color: " + POSITIVO and colore_segno(-40.0) == "color: " + NEGATIVO
    assert colore_segno(0) == colore_segno(None) == colore_segno(float("nan")) == colore_segno("") == ""
