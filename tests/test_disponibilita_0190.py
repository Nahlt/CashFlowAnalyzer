"""Novita' della 0.19.0: saldo dei conti a ogni data (Analisi disponibilita'), saldo inserito a mano in Conti e
copertura, menu con Analisi movimenti e Analisi disponibilita', "Saldo netto" diventato "Differenza entrate uscite".
Tutti i dati sono sintetici (fixture `caricato`: ING con saldo iniziale e finale, Credem con il saldo al 31/10)."""
import ast
from datetime import date, timedelta
from pathlib import Path

import pytest

from spese import disponibilita as dsp
from spese import importa, servizi

ING_MOVIMENTI = {"2026-10-02": -1000, "2026-10-10": -10000, "2026-10-20": -50000, "2026-10-22": -100000,
                 "2026-10-27": 250000}


def _vuota():
    from spese.ui import grafici
    assert grafici.mostra


@pytest.fixture(scope="module")
def grafici():
    """spese.ui.grafici si importa solo dentro un'AppTest (il componente si registra con Streamlit avviato)."""
    from streamlit.testing.v1 import AppTest
    AppTest.from_function(_vuota).run()
    from spese.ui import grafici as g
    return g


def _movimento(conn, mid, conto, data, importo, data_contabile=None, fee=0, tax=0, tipo="bonifico"):
    conn.execute("INSERT INTO movimenti(id, conto_id, data, data_contabile, importo_cent, fee_cent, tax_cent, tipo) "
                 "VALUES (?,?,?,?,?,?,?,?)", (mid, conto, data, data_contabile, importo, fee, tax, tipo))


# --- saldi noti ----------------------------------------------------------------------------------------------
def test_saldi_letti_dagli_estratti(caricato):
    """ING: il saldo iniziale (1.000 € il 01/10) vale a fine del 30/09, prima dei movimenti del primo giorno; il
    finale vale a fine del 31/10. I due quadrano con i movimenti in mezzo. Credem: saldo alla data del 31/10."""
    c, conti, _ = caricato
    ing = dsp.Saldi(c, conti["ing"])
    assert [(a.giorno, a.saldo_cent, a.tipo) for a in ing.ancore] == [("2026-09-30", 100000, "iniziale"),
                                                                    ("2026-10-31", 189000, "finale")]
    assert [x for _, _, x in ing.scarti()] == [0]
    assert ing.saldo("2026-09-30") == 100000
    assert ing.saldo("2026-10-01") == 100000
    assert ing.saldo("2026-10-02") == 99000
    assert ing.saldo("2026-10-15") == 89000
    assert ing.saldo("2026-10-31") == 189000
    assert ing.saldo_iniziale("2026-10-02") == 100000          # prima dei movimenti del 02/10
    assert [(a.giorno, a.tipo) for a in dsp.Saldi(c, conti["credem"]).ancore] == [("2026-10-31", "alla_data")]
    tr = dsp.Saldi(c, conti["tr"])
    assert not tr.calcolabile and tr.saldo("2026-10-31") is None


def test_saldo_alla_data_contabile(caricato):
    """Un pagamento con carta del 26/10 contabilizzato il 28/10 non c'e' ancora nel saldo del 27/10: la banca
    calcola il saldo sui movimenti contabilizzati. Credem al 31/10: 1.000 €; dopo il 27/10 sono contabilizzati
    -25,40 € (il 28/10); il 27/10 altri due da -12,00 €."""
    c, conti, _ = caricato
    s = dsp.Saldi(c, conti["credem"])
    assert s.saldo("2026-10-31") == 100000
    assert s.saldo("2026-10-27") == 100000 + 2540
    assert s.saldo("2026-10-26") == 100000 + 2540 + 2400
    # con la data dell'operazione il saldo del 27/10 sarebbe stato 1.000 € (tutti gia' fatti): non e' cosi'
    assert s.saldo("2026-10-27") != 100000


def test_saldo_manuale_prevale_e_sostituisce(caricato):
    """Nello stesso giorno di un saldo letto da un estratto vince quello inserito a mano; un secondo saldo a mano
    nello stesso giorno sostituisce il primo. Lo scarto rispetto al saldo precedente si vede subito."""
    c, conti, _ = caricato
    servizi.imposta_saldo(c, conti["ing"], "2026-10-31", 190000)
    servizi.imposta_saldo(c, conti["ing"], date(2026, 10, 31), 191000)
    assert c.execute("SELECT count(*) FROM saldi WHERE tipo = 'manuale'").fetchone()[0] == 1
    s = dsp.Saldi(c, conti["ing"])
    assert [(a.giorno, a.saldo_cent, a.tipo) for a in s.ancore][-1] == ("2026-10-31", 191000, "manuale")
    assert [x for _, _, x in s.scarti()] == [2000]          # 2.000 € in piu' di quanto dicono i movimenti
    assert servizi.elimina_saldo(c, conti["ing"], "2026-10-31") == 1
    assert dsp.Saldi(c, conti["ing"]).ancore[-1].tipo == "finale"     # l'estratto torna a valere


def test_ancora_piu_vicina(caricato):
    """Con due saldi noti che non quadrano (scarto di 20 €), ogni giorno parte da quello piu' vicino: i giorni
    di inizio ottobre dal saldo del 30/09, quelli di fine ottobre dal saldo inserito il 31/10."""
    c, conti, _ = caricato
    servizi.imposta_saldo(c, conti["ing"], "2026-10-31", 191000)
    s = dsp.Saldi(c, conti["ing"])
    assert s.saldo("2026-10-05") == 100000 - 1000                    # dal 30/09
    assert s.saldo("2026-10-25") == 191000 - 250000                   # dal 31/10, togliendo l'accredito del 27/10
    assert s.saldo("2026-12-31") == 191000                            # dopo l'ultimo movimento resta fermo


def test_commissioni_imposte_e_serie(conn):
    """Il saldo comprende commissioni e imposte; la serie ha una riga per giorno con entrate e uscite del giorno
    (un accredito con commissione: entrata 100 €, uscita 1,50 €)."""
    conto = importa.crea_conto(conn, "Prova", None, "conto", "personale")
    _movimento(conn, "M1", conto, "2026-10-01", 10000, fee=-150)
    _movimento(conn, "M2", conto, "2026-10-03", -2000, tax=-34)
    _movimento(conn, "M3", conto, "2026-10-03", None, data_contabile="2026-10-04", fee=-100, tipo="commissione")
    servizi.imposta_saldo(conn, conto, "2026-09-30", 50000)
    s = dsp.Saldi(conn, conto)
    assert s.saldo("2026-10-01") == 50000 + 10000 - 150
    assert s.saldo("2026-10-03") == 50000 + 10000 - 150 - 2034
    assert s.saldo("2026-10-04") == 50000 + 10000 - 150 - 2034 - 100
    df = s.serie("2026-10-01", "2026-10-05")
    assert list(df["giorno"]) == ["2026-10-0%d" % i for i in range(1, 6)]
    assert list(df["entrate"]) == [10000, 0, 0, 0, 0]
    assert list(df["uscite"]) == [150, 0, 2034, 100, 0]
    assert list(df["movimenti"]) == [1, 0, 1, 1, 0]
    assert list(df["saldo"]) == [59850, 59850, 57816, 57716, 57716]


def test_movimenti_con_saldo(caricato):
    """Movimenti dal piu' recente con il saldo dopo ciascuno: il primo arriva al saldo di fine periodo, il piu'
    vecchio parte dal saldo di inizio periodo; due movimenti dello stesso giorno si susseguono."""
    c, conti, _ = caricato
    s = dsp.Saldi(c, conti["credem"])
    df = dsp.movimenti_con_saldo(c, conti["credem"], "2026-10-01", "2026-10-31", s)
    assert len(df) == 8 and list(df["data_contabile"]) == sorted(df["data_contabile"], reverse=True)
    assert df["saldo_dopo"].iloc[0] == s.saldo("2026-10-31")
    assert df["saldo_dopo"].iloc[-1] - df["importo_cent"].iloc[-1] == s.saldo_iniziale("2026-10-01")
    for i in range(len(df) - 1):
        assert df["saldo_dopo"].iloc[i] - df["importo_cent"].iloc[i] == df["saldo_dopo"].iloc[i + 1]
    assert {"uscita", "entrata"} <= set(df["natura"])                 # come conta, dalle analisi
    assert dsp.movimenti_con_saldo(c, conti["credem"], "2026-11-01", "2026-11-30", s).empty


# --- saldo inserito a mano -----------------------------------------------------------------------------------
def test_imposta_saldo_controlli(caricato):
    c, conti, _ = caricato
    domani = date.today() + timedelta(days=1)
    with pytest.raises(ValueError, match="futuro"):
        servizi.imposta_saldo(c, conti["ing"], domani, 100)
    with pytest.raises(ValueError, match="carta"):
        servizi.imposta_saldo(c, conti["carta"], "2026-10-31", 100)
    with pytest.raises(ValueError, match="centesimi"):
        servizi.imposta_saldo(c, conti["ing"], "2026-10-31", 10.5)
    assert c.execute("SELECT count(*) FROM saldi WHERE tipo = 'manuale'").fetchone()[0] == 0
    assert [r["nome"] for r in dsp.conti_con_saldo(c)] == ["ING", "Trade Republic", "Credem comune"]


def test_saldo_manuale_resta_dopo_annulla_import_e_va_via_col_conto(caricato):
    c, conti, rep = caricato
    servizi.imposta_saldo(c, conti["credem"], "2026-10-15", 55000)
    importa.annulla_import(c, rep["credem"]["import_id"])
    tipi = [r[0] for r in c.execute("SELECT tipo FROM saldi WHERE conto_id = ?", (conti["credem"],))]
    assert tipi == ["manuale"]                       # il saldo dell'estratto se ne va con l'import, il tuo resta
    importa.elimina_conto(c, conti["credem"])
    assert not c.execute("SELECT 1 FROM saldi WHERE conto_id = ?", (conti["credem"],)).fetchone()


def test_quadratura_import_usa_il_saldo_manuale(conn):
    """Il saldo inserito a mano fa da riferimento per la quadratura dell'import successivo (Credem): 709,89 € al
    02/10 piu' i movimenti contabilizzati dopo fanno i 1.000 € del 31/10 scritti nell'estratto."""
    import sintetici
    conto = importa.crea_conto(conn, "Credem", "credem", "conto", "comune", "Credem", sintetici.IBAN_CREDEM)
    servizi.imposta_saldo(conn, conto, "2026-10-02", 70989)
    r = importa.importa(conn, conto, "LISTA_MOVIMENTI.csv", sintetici.credem_csv())
    assert r["quadratura"] == "OK rispetto al saldo del 2026-10-02"


@pytest.mark.parametrize("testo, cent", [("1.234,56", 123456), ("-50", -5000), ("1234.56", 123456),
                                         ("1234,5", 123450), ("€ 10,00", 1000), ("", None), ("abc", None),
                                         ("1,234", None)])
def test_importo_da_testo(testo, cent):
    assert dsp.importo_da_testo(testo) == cent


# --- grafici, menu, nomi -------------------------------------------------------------------------------------
def test_grafico_saldo_e_uscite(grafici):
    import pandas as pd
    df = pd.DataFrame({"giorno": ["2026-10-01", "2026-10-02"], "saldo": [100000, 95000], "uscite": [0, 5000]})
    o = grafici.saldo_e_uscite(df, "2026-10-02")
    assert len(o["yAxis"]) == 2
    barre, linea = o["series"]
    assert (barre["type"], barre["yAxisIndex"], barre["data"]) == ("bar", 1, [["2026-10-01", None], ["2026-10-02", 50.0]])
    assert (linea["type"], linea["yAxisIndex"], linea["data"][1]) == ("line", 0, ["2026-10-02", 950.0])
    assert linea["markLine"]["data"] == [{"xAxis": "2026-10-02"}]
    assert linea["markPoint"]["data"] == [{"coord": ["2026-10-02", 950.0]}]
    assert "markLine" not in grafici.saldo_e_uscite(df)["series"][1]


def test_differenza_entrate_uscite_nel_grafico(grafici):
    import pandas as pd
    o = grafici.barre_saldo(pd.DataFrame({"mese": ["2026-10"], "netto": [1000]}))
    assert o["series"][0]["name"] == "Differenza entrate uscite"
    assert "Saldo netto" not in str(o["tooltip"]["formatter"])


def test_menu_analisi_movimenti_e_disponibilita():
    """Il gruppo Analisi si chiama Analisi movimenti; Saldo dei conti e Portafoglio stanno in Analisi disponibilita'."""
    albero = ast.parse((Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8"))
    pagine = next(n.value for n in albero.body if isinstance(n, ast.Assign)
                  and getattr(n.targets[0], "id", None) == "PAGINE")
    voci = [(e.elts[0].value, e.elts[-1].value) for e in pagine.elts]
    gruppi = {}
    for gruppo, indirizzo in voci:
        gruppi.setdefault(gruppo, []).append(indirizzo)
    assert gruppi["Analisi movimenti"] == ["andamento", "ripartizione", "etichette"]
    assert gruppi["Analisi disponibilità"] == ["disponibilita", "portafoglio"]
    assert "Analisi" not in gruppi
    assert voci[0] == ("Analisi movimenti", "andamento")             # resta la pagina di apertura


# --- pagine (AppTest) ----------------------------------------------------------------------------------------
def _pagina():
    """Script dell'AppTest: apre lo stesso database della fixture e disegna la pagina indicata."""
    import os

    import streamlit as st
    from spese import analisi, db, percorsi
    from spese.tassonomia_utente import carica
    from spese.ui import pagine_analisi, pagine_disponibilita, pagine_impostazioni
    if "conn" not in st.session_state:
        st.session_state["conn"] = db.apri(percorsi.file_database(), "passphrase di prova")
        carica(st.session_state["conn"])
    st.session_state["filtri_attivi"] = analisi.Filtri("tutti")
    {"disponibilita": pagine_disponibilita.saldo_conti, "conti": pagine_impostazioni.conti,
     "andamento": pagine_analisi.andamento}[os.environ["PAGINA_PROVA"]]()


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


def _metriche(at):
    return {m.label: m.value for m in at.metric}


def test_pagina_periodo(pagina):
    (c, conti, _), apri = pagina
    at = apri("disponibilita", disp_conto=conti["ing"], disp_modo="periodo",
              disp_periodo=(date(2026, 10, 1), date(2026, 10, 31)))
    m = _metriche(at)
    assert m["Saldo a inizio periodo"] == "1.000,00 €"
    assert m["Saldo a fine periodo"] == "1.890,00 €"
    assert m["Saldo minimo"] == "-610,00 €"                     # dopo il trasferimento del 22/10
    assert [x.value for x in at.subheader] == ["Saldo e uscite giorno per giorno", "Movimenti del periodo"]
    tabella = at.dataframe[0].value
    assert len(tabella) == 5 and tabella["Saldo dopo"].iloc[0] == "1.890,00 €"
    assert not at.warning


def test_pagina_giorno(pagina):
    (c, conti, _), apri = pagina
    servizi.imposta_saldo(c, conti["ing"], "2026-09-01", 100000)      # dati dal 01/09: il 27/09 e' coperto
    at = apri("disponibilita", disp_conto=conti["ing"], disp_modo="giorno", disp_giorno=date(2026, 10, 27))
    m = _metriche(at)
    assert m["Saldo a fine giornata"] == "1.890,00 €"
    assert m["Movimenti del giorno"] == "+2.500,00 €"
    assert m["Rispetto a 30 giorni prima"] == "+890,00 €"       # 27/09: 1.000 €
    assert at.subheader[0].value == "Saldo nei 30 giorni fino al 27/10/2026"
    assert len(at.dataframe[0].value) == 1


def test_pagina_scarto_e_conto_senza_saldo(pagina):
    (c, conti, _), apri = pagina
    servizi.imposta_saldo(c, conti["ing"], "2026-10-31", 191000)
    at = apri("disponibilita", disp_conto=conti["ing"], disp_modo="periodo",
              disp_periodo=(date(2026, 10, 1), date(2026, 10, 31)))
    assert any("scarto di +20,00 €" in w.value for w in at.warning)
    at = apri("disponibilita", disp_conto=conti["tr"])
    assert any("nessun saldo noto" in i.value for i in at.info)
    assert not at.metric


def test_conti_inserisce_ed_elimina_saldo(pagina):
    (c, conti, _), apri = pagina
    at = apri("conti")
    at.selectbox(key="saldo_conto").set_value(conti["credem"])
    at.date_input(key="saldo_giorno").set_value(date(2026, 11, 5))
    at.text_input(key="saldo_importo").input("abc")
    at.run()
    assert any("dopo il 28/10/2026" in w.value for w in at.warning)  # piu' recente dell'ultimo movimento
    at.button(key="saldo_salva").click().run()
    assert any("Scrivi il saldo" in e.value for e in at.error)
    at.text_input(key="saldo_importo").input("1.234,56")
    at.button(key="saldo_salva").click().run()
    assert not at.exception
    assert [tuple(r) for r in c.execute("SELECT data, saldo_cent FROM saldi WHERE tipo = 'manuale'")] == [("2026-11-05", 123456)]
    assert any("salvato: 1.234,56 €" in s.value for s in at.success)
    elenco = next(d.value for d in at.dataframe if "Fonte" in d.value.columns)
    assert "Inserito a mano" in list(elenco["Fonte"]) and "Estratto (saldo iniziale del 01/10/2026)" in list(elenco["Fonte"])
    at.selectbox(key="saldo_elim_0").set_value(0)
    at.run()
    at.button(key="saldo_elimina").click().run()
    assert not c.execute("SELECT 1 FROM saldi WHERE tipo = 'manuale'").fetchone()


def test_andamento_differenza_entrate_uscite(pagina):
    _, apri = pagina
    at = apri("andamento")
    etichette = [m.label for m in at.metric]
    assert "Differenza entrate uscite" in etichette and "Saldo netto" not in etichette
    assert "Differenza entrate uscite per mese" in [s.value for s in at.subheader]


def test_pagina_giorno_non_va_prima_dei_dati(pagina):
    """Il primo dato di ING e' il saldo iniziale (fine del 30/09): guardando il 10/10 il confronto con 30 giorni
    prima (10/09) non c'e', e il grafico parte dal 30/09."""
    (c, conti, _), apri = pagina
    at = apri("disponibilita", disp_conto=conti["ing"], disp_modo="giorno", disp_giorno=date(2026, 10, 10))
    m = _metriche(at)
    assert m["Rispetto a 30 giorni prima"] == "—"
    assert m["Saldo a fine giornata"] == "890,00 €"
