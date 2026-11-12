"""0.18.0: diagnostica (modalita' di debug). Controlli di coerenza, report anonimizzato, transcodifica.
Tutti i dati sono inventati."""
import re

import pytest

from spese import diagnostica as dg
from spese import servizi
from spese.chiavi import chiave
from spese.db import leggi_impostazione, scrivi_impostazione

ERRORI = [k for k, v in dg.CONTROLLI.items() if v[0] == "errore"]


def _mov(conn, conto, mid, importo, tipo="pagamento_carta", esercente=None, data="2026-10-05", descrizione=None,
         controparte=None, iban=None, import_id=None):
    conn.execute("INSERT INTO movimenti(id, conto_id, import_id, data, importo_cent, tipo, descrizione, esercente, chiave, "
                 "controparte_nome, controparte_iban) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (mid, conto, import_id, data, importo, tipo, descrizione, esercente, chiave(esercente) if esercente else None,
                  controparte, iban))


def _codici(anomalie):
    return [a.codice for a in anomalie]


def _di(anomalie, codice):
    return [a for a in anomalie if a.codice == codice]


# --- le regole valgono sui dati sintetici -------------------------------------------------------
def test_nessun_errore_sui_dati_sintetici(caricato):
    c, _, _ = caricato
    anomalie = dg.esegui(c)
    assert not [a for a in anomalie if a.gravita == "errore"], _codici(anomalie)
    assert "TIPO_NON_RICONOSCIUTO" in _codici(anomalie)          # il movimento "sconosciuto" del file Credem


def test_spenta_di_partenza(conn):
    assert dg.attiva(conn) is False
    scrivi_impostazione(conn, dg.CHIAVE_ATTIVA, True)
    assert dg.attiva(conn) is True


# --- ogni controllo trova il suo guasto ---------------------------------------------------------
def test_componenti(caricato):
    c, _, _ = caricato
    mid = c.execute("SELECT mov_id FROM analisi WHERE componente = 'principale' AND natura = 'uscita' LIMIT 1").fetchone()[0]
    c.execute("UPDATE analisi SET importo_cent = importo_cent - 100 WHERE mov_id = ? AND componente = 'principale'", (mid,))
    c.execute("INSERT INTO analisi(mov_id, componente, data, mese, conto_id, perimetro, importo_cent, natura) "
              "VALUES ('FANTASMA', 'principale', '2026-10-01', '2026-10', 1, 'personale', -500, 'uscita')")
    trovate = _di(dg.esegui(c, ricalcolo=False), "COMPONENTI")
    assert any(a.movimenti == (mid,) and ("differenza principale (analisi - movimento)", "euro", -100) in a.dati
               for a in trovate)
    assert any(not a.movimenti for a in trovate)                  # la riga senza movimento
    assert "COMPONENTI" not in _codici(dg.esegui(c))              # il ricalcolo rimette a posto


def test_trasferimenti(caricato):
    c, _, _ = caricato
    a, b = c.execute("SELECT mov_id, controparte_mov_id FROM trasferimenti WHERE stato = 'abbinato' LIMIT 1").fetchone()
    assert not _di(dg.esegui(c, ricalcolo=False), "TRASF_CONTATO")
    c.execute("UPDATE analisi SET natura = 'uscita' WHERE mov_id = ?", (a,))
    assert _di(dg.esegui(c, ricalcolo=False), "TRASF_CONTATO")[0].movimenti in ((a, b), (b, a))
    c.execute("UPDATE trasferimenti SET controparte_mov_id = 'ALTRO' WHERE mov_id = ?", (b,))
    assert _di(dg.esegui(c, ricalcolo=False), "TRASF_ASIMMETRICO")


def test_natura_categoria_e_totali(caricato):
    c, _, _ = caricato
    mid = c.execute("SELECT mov_id FROM analisi WHERE natura = 'uscita' AND l2 IS NOT NULL LIMIT 1").fetchone()[0]
    c.execute("UPDATE analisi SET l1 = 'ENTRATE', l2 = 'Stipendio' WHERE mov_id = ?", (mid,))
    assert _di(dg.esegui(c, ricalcolo=False), "NATURA_CATEGORIA")[0].movimenti == (mid,)
    c.execute("UPDATE analisi SET l1 = 'INVENTATA', l2 = 'Che non esiste' WHERE mov_id = ?", (mid,))
    assert _di(dg.esegui(c, ricalcolo=False), "NATURA_CATEGORIA")


def test_doppione_tra_conti(caricato):
    c, conti, _ = caricato
    _mov(c, conti["ing"], "D1", -4990, esercente="NEGOZIO THETA", data="2026-10-05")
    _mov(c, conti["tr"], "D2", -4990, esercente="NEGOZIO THETA", data="2026-10-07")
    _mov(c, conti["ing"], "D3", -1000, esercente="BAR DELTA", data="2026-10-05")          # importo tondo, esercenti
    _mov(c, conti["tr"], "D4", -1000, esercente="BOTTEGA IOTA", data="2026-10-06")        # diversi: non segnalato
    _mov(c, conti["ing"], "D5", -7777, esercente="LIBRERIA KAPPA", data="2026-09-01")
    _mov(c, conti["tr"], "D6", -7777, esercente="LIBRERIA KAPPA", data="2026-10-20")      # troppo lontani
    trovate = _di(dg.esegui(c), "DOPPIONE_TRA_CONTI")
    assert [a.movimenti for a in trovate] == [("D1", "D2")]


def test_doppione_nel_conto(caricato):
    c, conti, _ = caricato
    for mid, imp in (("E1", 901), ("E2", 902), ("E3", 901), ("E4", 901)):
        _mov(c, conti["ing"], mid, -1500 if mid != "E4" else -1600, descrizione="PAGAMENTO CARTA BAR DELTA", import_id=imp)
    trovate = _di(dg.esegui(c), "DOPPIONE_NEL_CONTO")
    assert [a.movimenti for a in trovate] == [("E1", "E2", "E3")]


def test_storno_contato_come_entrata(caricato):
    """Il caso da cui e' nata la diagnostica: lo storno di un addebito diretto che la banca scrive con una
    dicitura non riconosciuta conta come entrata, mentre l'addebito resta un'uscita."""
    c, conti, _ = caricato
    _mov(c, conti["credem"], "S1", -4990, "addebito_sepa", esercente="ENERGIA ETA", data="2026-09-10")
    _mov(c, conti["credem"], "S2", 4990, "altro", esercente="ENERGIA ETA", data="2026-10-02",
         descrizione="STORNO DI VERSAMENTO CON ADDEBITO DIRETTO ENERGIA ETA")
    _mov(c, conti["credem"], "S3", 2500, "altro", data="2026-10-03", descrizione="RIMBORSO SPESE")
    _mov(c, conti["credem"], "S4", 120000, "altro", data="2026-10-04", descrizione="EMOLUMENTI")
    trovate = {a.movimenti: dict((n, v) for n, _, v in a.dati) for a in _di(dg.esegui(c), "STORNO_COME_ENTRATA")}
    assert set(trovate) == {("S2", "S1"), ("S3",)}
    assert trovate[("S2", "S1")]["la banca lo chiama storno o rimborso"] == "si'"
    # classificato come la spesa diventa un rimborso e non e' piu' segnalato
    servizi.decidi_movimento(c, "S2", ("CASA", "Energia e gas"), "uscita")
    assert ("S2", "S1") not in [a.movimenti for a in _di(dg.esegui(c), "STORNO_COME_ENTRATA")]


def test_suddivisione_ignorata(caricato):
    c, conti, _ = caricato
    _mov(c, conti["ing"], "P1", -10000, "bonifico", controparte="LUCA BIANCHI")
    for n, imp in enumerate((-6000, -3000)):
        c.execute("INSERT INTO ripartizioni(mov_id, n, importo_cent, l1, l2) VALUES ('P1', ?, ?, 'CASA', 'Acqua')", (n, imp))
    a = _di(dg.esegui(c), "SUDDIVISIONE_IGNORATA")[0]
    assert a.movimenti == ("P1",) and ("somma delle parti - movimento", "euro", 1000) in a.dati


def test_carta_non_abbinata_e_quadratura(caricato):
    c, conti, _ = caricato
    add = c.execute("SELECT mov_id FROM contenitori").fetchone()[0]
    c.execute("UPDATE contenitori SET stato = 'senza_dettaglio' WHERE mov_id = ?", (add,))
    c.execute("UPDATE import_file SET quadratura = 'NON quadra (differenza +12.50 EUR)' WHERE conto_id = ?", (conti["ing"],))
    anomalie = dg.esegui(c, ricalcolo=False)
    carta = _di(anomalie, "CARTA_NON_ABBINATA")[0]
    assert carta.movimenti[0] == add and len(carta.movimenti) > 1          # con le righe della carta di contesto
    assert ("differenza", "euro", 1250) in _di(anomalie, "QUADRATURA")[0].dati


def test_diciture_non_riconosciute_raggruppate(caricato):
    c, conti, _ = caricato
    for i in range(3):
        _mov(c, conti["credem"], "T%d" % i, 1000 + i, "altro", data="2026-10-0%d" % (i + 1),
             descrizione="STORNO DI VERSAMENTO CON ADDEBITO DIRETTO RIF. %d LUCA BIANCHI" % (1234 + i))
    gruppi = [dict((n, v) for n, _, v in a.dati) for a in _di(dg.esegui(c), "TIPO_NON_RICONOSCIUTO")]
    g = next(x for x in gruppi if x["movimenti"] == 3)
    assert g["dicitura"].startswith("STORNO DI VERSAMENTO CON ADDEBITO DIRETTO RIF")
    assert "BIANCHI" not in g["dicitura"] and "1234" not in g["dicitura"] and g["accrediti"] == 3


# --- report: cosa esce e cosa no ----------------------------------------------------------------
IBAN_ESTERNO = "IT60X0542811101000000123456"
SEGRETI = ["LUCA BIANCHI", "BIANCHI", "FARMACIA SIGMA", "SIGMA", "NEGOZIO THETA", "THETA", "Mario", "Rossi", "ROSSI",
           IBAN_ESTERNO, "0542811101", "RIF987654", "987654", "Credem comune", "Trade Republic", "Carta ING",
           "2026-", "/2026", "Farmacia", "SALUTE"]


@pytest.fixture()
def con_casi(caricato):
    c, conti, _ = caricato
    _mov(c, conti["ing"], "D1", -4990, esercente="NEGOZIO THETA", data="2026-10-05",
         descrizione="PAGAMENTO CARTA 4857 NEGOZIO THETA MILANO MARIO ROSSI")
    _mov(c, conti["tr"], "D2", -4990, esercente="NEGOZIO THETA", data="2026-10-07", descrizione="CARD PAYMENT NEGOZIO THETA")
    _mov(c, conti["ing"], "F1", -2350, esercente="FARMACIA SIGMA", data="2026-10-08", descrizione="PAGAMENTO CARTA FARMACIA SIGMA")
    _mov(c, conti["tr"], "F2", -2350, esercente="FARMACIA SIGMA", data="2026-10-09", descrizione="CARD PAYMENT FARMACIA SIGMA")
    _mov(c, conti["credem"], "B1", -30000, "bonifico", controparte="LUCA BIANCHI", iban=IBAN_ESTERNO, data="2026-10-10",
         descrizione="BONIFICO A FAVORE DI LUCA BIANCHI %s RIF987654 SALDO CENA DA GINO" % IBAN_ESTERNO)
    c.execute("INSERT INTO ripartizioni(mov_id, n, importo_cent, l1, l2) VALUES ('B1', 0, -10000, 'SALUTE', 'Farmacia')")
    anomalie = dg.esegui(c)
    scelte = [a for a in anomalie if a.codice in ("DOPPIONE_TRA_CONTI", "SUDDIVISIONE_IGNORATA")]
    assert len(scelte) == 3
    return c, anomalie, scelte


def test_riepilogo_senza_importi_date_nomi(con_casi):
    c, anomalie, _ = con_casi
    testo = dg.riepilogo(c, anomalie)
    testo_solo, _ = dg.report(c, anomalie, [])
    for t in (testo, testo_solo):
        assert "€" not in t and not re.search(r"\d,\d\d\b", t) and not re.search(r"\b20\d\d\b", t)
        for s in SEGRETI + ["ING"]:
            assert not re.search(r"(?<![\w_])%s(?![\w_])" % re.escape(s), t), s
    assert "Nessun caso scelto" in testo_solo
    assert leggi_impostazione(c, dg.CHIAVE_MAPPA, {}).get("etichette", {}) == {}      # nessuna etichetta creata


def test_casi_anonimizzati(con_casi):
    c, anomalie, scelte = con_casi
    testo, _ = dg.report(c, anomalie, scelte)
    casi = testo[testo.index("## 2. Casi scelti"):]
    for s in SEGRETI + ["GINO", "CENA", "MILANO", "4857", "D1", "B1", "F1"]:
        assert not re.search(r"(?<![\w_])%s(?![\w_])" % re.escape(s), casi), s
    # restano importi veri, etichette, parole della banca
    for atteso in ("-49,90 €", "-300,00 €", "-23,50 €", "ESERCENTE 1", "SOGGETTO 1", "IBAN 1", "[TITOLARE]",
                   "PAGAMENTO CARTA # [ESERCENTE_1]", "BONIFICO A FAVORE DI [SOGGETTO_1] [IBAN_1]",
                   "(categoria riservata)", "CONTO 1"):
        assert atteso in casi, atteso
    # date spostate di un numero fisso di anni, giorno e mese uguali
    anni = leggi_impostazione(c, dg.CHIAVE_MAPPA)["anni"]
    assert anni in dg.ANNI_POSSIBILI and "%d-10-05" % (2026 - anni) in casi and "%d-10-07" % (2026 - anni) in casi


def test_transcodifica_ed_etichette_stabili(con_casi):
    c, anomalie, scelte = con_casi
    testo1, tc1 = dg.report(c, anomalie, scelte[:1])
    testo2, tc2 = dg.report(c, anomalie, scelte)
    eti = re.search(r"ESERCENTE \d+", testo1).group(0)
    riga = next(r for r in tc2.splitlines() if r.startswith("ESERCENTE;%s;" % eti))
    assert "NEGOZIO THETA" in riga or "FARMACIA SIGMA" in riga
    assert riga in tc1.splitlines()                                           # stessa etichetta nei due report
    assert tc2.startswith("﻿NON INVIARE") and "spostate indietro di" in tc2
    assert any(r.startswith("M;M 1;") and "2026-10-" in r for r in tc2.splitlines())      # data vera del movimento
    assert "SOGGETTO;SOGGETTO 1;LUCA BIANCHI" in tc2 and "IBAN;IBAN 1;%s" % IBAN_ESTERNO in tc2
    assert "Credem comune" in tc2
    # un terzo report non cambia le etichette gia' date
    _, tc3 = dg.report(c, anomalie, list(reversed(scelte)))
    assert set(tc2.splitlines()) <= set(tc3.splitlines())


def test_scheletro_nasconde_cio_che_non_e_della_banca(conn):
    a = dg.Anonimo(conn)
    s = a.scheletro("Storno di versamento con addebito diretto SDD Rossi Mario via Verdi 12 mandato 55/A-7")
    assert s.startswith("STORNO DI VERSAMENTO CON ADDEBITO DIRETTO SDD")
    assert "ROSSI" not in s and "VERDI" not in s and "12" not in s and "55" not in s and "MANDATO" in s
    assert a.scheletro(None) == "" and a.scheletro("   ") == ""


# --- pagine -------------------------------------------------------------------------------------
def _pagina():
    import os

    import streamlit as st
    from spese import db, percorsi
    from spese.tassonomia_utente import carica
    from spese.ui import pagine_diagnostica, pagine_impostazioni
    if "conn" not in st.session_state:
        st.session_state["conn"] = db.apri(percorsi.file_database(), "passphrase di prova")
        carica(st.session_state["conn"])
    {"diagnostica": pagine_diagnostica.diagnostica, "guida": pagine_diagnostica.guida,
     "impostazioni": pagine_impostazioni.impostazioni}[os.environ["PAGINA_PROVA"]]()


@pytest.fixture()
def pagina(caricato, monkeypatch):
    from streamlit.testing.v1 import AppTest

    def apri(nome):
        monkeypatch.setenv("PAGINA_PROVA", nome)
        at = AppTest.from_function(_pagina, default_timeout=60)
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        return at
    return caricato[0], apri


def test_impostazioni_interruttore(pagina):
    c, apri = pagina
    at = apri("impostazioni")
    assert "Modalita' di debug" in [s.value for s in at.subheader]
    inter = next(t for t in at.toggle if t.label == "Diagnostica dei conteggi")
    assert inter.value is False
    inter.set_value(True).run()
    assert not at.exception and dg.attiva(c)


def test_pagina_diagnostica(pagina):
    c, apri = pagina
    _mov(c, 1, "D1", -4990, esercente="NEGOZIO THETA", data="2026-10-05")
    _mov(c, 3, "D2", -4990, esercente="NEGOZIO THETA", data="2026-10-07")
    at = apri("diagnostica")
    assert not at.metric                                                      # niente finche' non si eseguono
    next(b for b in at.button if b.label == "Esegui i controlli").click().run()
    assert not at.exception
    assert [m.label for m in at.metric] == ["Errori", "Avvisi", "Informazioni"] and at.metric[0].value == "0"
    casi = at.dataframe[1].value
    assert "DOPPIONE_TRA_CONTI" in list(casi["Controllo"]) and "NEGOZIO THETA" in list(casi["Esercente o controparte"])
    next(b for b in at.button if b.label == "Prepara il report").click().run()
    assert not at.exception
    assert "report di diagnostica" in at.code[0].value and "NEGOZIO THETA" not in at.code[0].value


def test_pagina_guida(pagina):
    _, apri = pagina
    at = apri("guida")
    testo = " ".join(m.value for m in at.markdown)
    assert "Guida alla diagnostica" in testo and all(k in testo for k in dg.CONTROLLI)
