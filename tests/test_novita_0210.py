"""0.21.0: budget mensili (calcolo, rimborsi, previsione conto per conto, servizi, tassonomia, pagine) e
copertura degli import."""
from datetime import date

import pytest

import sintetici
from spese import budget as bd
from spese import configurazione, db, importa, percorsi, servizi
from spese.ricalcolo import ricalcola

OGGI = date(2026, 11, 22)
SUPER = ("ALIMENTARI", "Supermercato")


# ---------------------------------------------------------------- dati

def _pagamenti_mensili(fino_al=(2026, 11, 20)):
    """Spesa al supermercato: 100 € il 3, 50 € il 12, 80 € il 25 di ogni mese, da novembre 2025."""
    out = []
    for a, m in [(2025, 11), (2025, 12)] + [(2026, k) for k in range(1, 12)]:
        for g, cent in ((3, -10000), (12, -5000), (25, -8000)):
            if (a, m, g) <= fino_al:
                out.append(("%02d/%02d/%d" % (g, m, a), cent, "ESSELUNGA VIALE MILANO"))
    return out


@pytest.fixture()
def mesi(conn):
    """Conto comune Credem con un anno di spesa al supermercato, dati fino al 20/11/2026, piu':
    un rimborso il 02/10 di una spesa del 25/09 (80 €), un rimborso Amazon del 15/11 senza spesa e un bonifico
    di Giulia Bianchi del 18/11 (40 €) classificato come spesa al supermercato (rimborso da una persona)."""
    pag = _pagamenti_mensili() + [("02/10/2026", 8000, "ESSELUNGA VIALE MILANO"),
                                  ("15/11/2026", 2500, "AMAZON MARKETPLACE")]
    cid = importa.crea_conto(conn, "Credem comune", "credem", "conto", "comune", "Credem", sintetici.IBAN_CREDEM)
    importa.importa(conn, cid, "LISTA_MOVIMENTI.csv", sintetici.credem_mesi(
        pag, inizio="01/11/2025", fine="20/11/2026", bonifici=[("18/11/2026", 4000, "Giulia Bianchi")]))
    ricalcola(conn)
    bon = conn.execute("SELECT id FROM movimenti WHERE tipo = 'bonifico'").fetchone()[0]
    servizi.decidi_movimento(conn, bon, SUPER)
    ricalcola(conn)
    return conn, cid


# ---------------------------------------------------------------- schema e copertura

def test_schema_3_con_budget_e_copertura(tmp_path, monkeypatch):
    monkeypatch.setenv("SPESE_DATI", str(tmp_path / "dati"))
    configurazione.imposta_tassonomia(None)
    c = db.apri(percorsi.file_database(), "pp")
    # database della 0.20.x: niente tabella budget, niente copertura degli import
    c.execute("DROP TABLE budget")
    c.execute("DROP TABLE import_file")
    c.execute("CREATE TABLE import_file (id INTEGER PRIMARY KEY, conto_id INTEGER NOT NULL REFERENCES conti(id), "
              "nome_file TEXT, hash_file TEXT, profilo TEXT, importato_il TEXT, righe_dati INTEGER, "
              "righe_scartate INTEGER, nuove INTEGER, duplicate INTEGER, data_min TEXT, data_max TEXT, "
              "quadratura TEXT, avvisi TEXT, conto_prima TEXT)")
    c.execute("PRAGMA user_version = 2")
    c.close()
    c = db.apri(percorsi.file_database(), "pp")
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.VERSIONE_SCHEMA
    colonne = {r[1] for r in c.execute("PRAGMA table_info(import_file)")}
    assert {"coperto_al", "coperto_da"} <= colonne
    assert c.execute("SELECT count(*) FROM budget").fetchone()[0] == 0
    c.close()


@pytest.mark.parametrize("profilo, md, nome, caricato, ultimo, atteso", [
    # fine del periodo scritta nel file (Credem "Data Fine", ING "dal ... al ...")
    ({}, {"periodo_a": "20/11/2026"}, "x.csv", "2026-11-22T10:00:00", "2026-11-10", ("2026-11-20", "periodo")),
    # il periodo finisce dopo il caricamento: vale il giorno del caricamento
    ({}, {"periodo_a": "30/11/2026"}, "x.csv", "2026-11-22T10:00:00", "2026-11-10", ("2026-11-22", "periodo")),
    # data nel nome del file (Fineco, Credit Agricole)
    ({"data_nome_file": r"movements_(?P<data>\d{8})"}, {}, "Fineco_movements_20261118.xlsx", "2026-11-22T10:00:00",
     "2026-11-10", ("2026-11-18", "nome_file")),
    ({"data_nome_file": r"CAI_(?P<data>\d{8})\d{6}"}, {}, "Lista_Movimenti_CAI_20261119083015.csv",
     "2026-11-22T10:00:00", "2026-11-10", ("2026-11-19", "nome_file")),
    # nessuna indicazione nel file: la data di caricamento
    ({}, {}, "report.csv", "2026-11-22T10:00:00", "2026-11-10", ("2026-11-22", "caricamento")),
    # file molto piu' vecchio del caricamento: l'ultimo movimento, non il caricamento
    ({}, {}, "report.csv", "2026-11-22T10:00:00", "2026-08-31", ("2026-08-31", "movimenti")),
])
def test_copertura_del_file(profilo, md, nome, caricato, ultimo, atteso):
    assert importa.copertura_file(profilo, md, nome, caricato, ultimo) == atteso


def test_copertura_mai_prima_dell_ultimo_movimento():
    assert importa.copertura_file({}, {"periodo_a": "10/11/2026"}, "x", "2026-11-22T10:00", "2026-11-15") == \
        ("2026-11-15", "periodo")


def test_profili_con_la_data_nel_nome_del_file():
    p = configurazione.profili()
    assert importa._giorno_nome_file(p["fineco"], "Fineco_movements_20261120.xlsx") == "2026-11-20"
    assert importa._giorno_nome_file(p["credit_agricole"], "Lista_Movimenti_CAI_20261119083015.csv") == "2026-11-19"


def test_copertura_salvata_all_import_e_ricostruita_per_i_vecchi(caricato):
    conn, conti, _ = caricato
    cop = importa.coperture(conn)
    # Credem: "Data Fine" del file sintetico (31/10/2026); la carta ING non ha periodo: data di caricamento,
    # o l'ultimo movimento se il file e' vecchio rispetto a oggi
    assert cop[conti["credem"]] == ("2026-10-31", "periodo")
    assert cop[conti["carta"]][1] in ("caricamento", "movimenti")
    # import fatti prima della 0.21: la copertura si ricostruisce al ricalcolo (Credem dal saldo del file)
    conn.execute("UPDATE import_file SET coperto_al = NULL, coperto_da = NULL")
    ricalcola(conn)
    assert importa.coperture(conn)[conti["credem"]] == ("2026-10-31", "periodo")
    assert importa.coperture(conn)[conti["carta"]][1] in ("caricamento", "movimenti")


# ---------------------------------------------------------------- servizi

def test_aggiungi_cambia_togli_elimina(conn):
    servizi.aggiungi_budget(conn, SUPER, "comune", 40000, "2026-01")
    with pytest.raises(ValueError, match="esiste gia'"):
        servizi.aggiungi_budget(conn, SUPER, "comune", 30000, "2026-03")
    servizi.aggiungi_budget(conn, SUPER, "tutti", 50000, "2026-01")          # stessa voce, altro perimetro
    servizi.cambia_importo_budget(conn, SUPER, "comune", 45000, "2026-06")
    b = next(x for x in bd.elenco(conn) if x["perimetro"] == "comune")
    assert b["importi"] == [("2026-01", 40000), ("2026-06", 45000)]
    assert bd.importo_nel_mese(b, "2025-12") is None
    assert bd.importo_nel_mese(b, "2026-05") == 40000
    assert bd.importo_nel_mese(b, "2026-11") == 45000
    servizi.cambia_importo_budget(conn, SUPER, "comune", 47000, "2026-06")    # stesso mese: sostituisce
    with pytest.raises(ValueError, match="unico importo"):
        servizi.togli_cambio_budget(conn, SUPER, "tutti", "2026-01")
    servizi.togli_cambio_budget(conn, SUPER, "comune", "2026-06")
    b = next(x for x in bd.elenco(conn) if x["perimetro"] == "comune")
    assert b["importi"] == [("2026-01", 40000)]
    assert servizi.elimina_budget(conn, SUPER, "comune") == 1
    assert [x["perimetro"] for x in bd.elenco(conn)] == ["tutti"]


@pytest.mark.parametrize("cat, perimetro, importo, mese, errore", [
    (("STIPENDIO", None), "tutti", 100, "2026-01", "categoria di spesa"),
    (("ALIMENTARI", "Benzina"), "tutti", 100, "2026-01", "non e' una sottocategoria"),
    (SUPER, "famiglia", 100, "2026-01", "perimetro"),
    (SUPER, "tutti", 0, "2026-01", "sopra zero"),
    (SUPER, "tutti", 10.5, "2026-01", "centesimi interi"),
    (SUPER, "tutti", 100, "2026-13", None),
])
def test_aggiungi_rifiuta_i_valori_sbagliati(conn, cat, perimetro, importo, mese, errore):
    with pytest.raises(ValueError, match=errore):
        servizi.aggiungi_budget(conn, cat, perimetro, importo, mese)


def test_solo_categorie_di_spesa():
    cat = bd.categorie_di_spesa()
    assert "Supermercato" in cat["ALIMENTARI"]
    tax = configurazione.tassonomia()
    assert all(tax.sezione((l1, l2)) == "uscite" for l1, v in cat.items() for l2 in v)


# ---------------------------------------------------------------- calcolo puro

def _v(giorno, cent, conto):
    return {"data": giorno, "conto_id": conto, "l1": "X", "l2": "Y", "spesa": cent, "mov_id": giorno,
            "rimborso_del": None}


def test_previsione_conto_per_conto_con_coperture_diverse():
    """Febbraio 2026 (28 giorni). Conto 1: dati fino al 10/02, storia da novembre 2025; nei mesi passati spende
    50, 70 e 90+30 € dopo il 10 (il 31 gennaio vale come ultimo giorno di febbraio): mediana 70 €. A febbraio ha
    speso 100 € il 5. Conto 2: dati fino al 25/01 (fermo a prima del mese): stima di tutto il mese, mediana di
    40 € sugli 11 mesi interi coperti. Previsto = 100 + 70 + 0 + 40 = 210 €."""
    voci = []
    for m, dopo in (("2025-11", 5000), ("2025-12", 7000), ("2026-01", 9000)):
        voci += [_v(m + "-05", 10000, 1), _v(m + "-20", dopo, 1)]
    voci.append(_v("2026-01-31", 3000, 1))
    voci.append(_v("2026-02-05", 10000, 1))
    for k in range(2, 13):
        voci.append(_v("2025-%02d-15" % k, 4000, 2))
    voci.append(_v("2026-01-15", 99999, 2))                 # gennaio del conto 2 non e' coperto per intero
    r = bd.calcola(voci, "2026-02", {1: "2026-02-10", 2: "2026-01-25"}, {1: "2025-11", 2: "2025-02"})
    assert r["giorni"] == 28
    assert r["conti"][1] == {"giorno": 10, "speso": 10000, "resto": 7000, "mesi": 3}
    assert r["conti"][2] == {"giorno": 0, "speso": 0, "resto": 4000, "mesi": 11}
    assert (r["speso"], r["previsto"], r["previsione"]) == (10000, 21000, True)
    # con un conto fermo a prima del mese non c'e' parte "certa": la linea piena non parte
    assert r["reale"] == [None] * 28
    assert r["stimato"][4] == 10000 and r["stimato"][-1] == 21000


def test_mese_coperto_per_intero_senza_previsione():
    voci = [_v("2026-01-05", 10000, 1), _v("2026-02-05", 12000, 1)]
    r = bd.calcola(voci, "2026-01", {1: "2026-02-10"}, {1: "2026-01"})
    assert (r["speso"], r["previsto"], r["previsione"], r["stimato"]) == (10000, 10000, False, None)


def test_stato():
    assert bd.stato(20000, 21000, 25000) == bd.SUPERATO
    assert bd.stato(20000, 15000, 20001) == bd.A_RISCHIO
    assert bd.stato(20000, 15000, 20000) == bd.IN_LINEA


# ---------------------------------------------------------------- calcolo sui dati

def test_budget_del_mese_con_rimborsi_e_previsione(mesi):
    """Novembre: 100 + 50 € spesi, meno 40 € restituiti da Giulia il 18 (rimborso da una persona: conta il giorno
    in cui arriva) = 110 €. Dopo il 20 di solito si spendono 80 € (a settembre 0: il rimborso del 02/10 conta il
    25/09): mediana 80 €, previsto 190 €. Il rimborso Amazon senza spesa non conta ed e' elencato."""
    conn, _ = mesi
    servizi.aggiungi_budget(conn, SUPER, "comune", 18000, "2026-01")
    r = bd.analizza(conn, "2026-11", OGGI)
    b = r["budget"][0]
    assert (b["speso"], b["previsto"], b["stato"], b["sopra"]) == (11000, 19000, bd.A_RISCHIO, 1000)
    assert (b["saturazione"], b["saturazione_prevista"]) == (61, 106)
    assert [(x["data"], x["importo"]) for x in r["rimborsi_non_attribuiti"]] == [("2026-11-15", 2500)]
    storico = {m: (s, p) for m, _, s, p in b["storico"]}
    assert storico["2026-09"] == (15000, None)        # 230 € meno il rimborso arrivato a ottobre
    assert storico["2026-10"] == (23000, None)        # il rimborso del 02/10 non libera budget di ottobre
    assert storico["2026-11"] == (11000, 19000)
    assert r["coperture"][0]["giorno"] == "2026-11-20" and r["coperture"][0]["indietro"] is None


def test_superato_e_in_linea(mesi):
    conn, _ = mesi
    servizi.aggiungi_budget(conn, SUPER, "comune", 10000, "2026-01")
    servizi.aggiungi_budget(conn, "ALIMENTARI", "comune", 30000, "2026-01")
    r = {(b["l2"]): b for b in bd.analizza(conn, "2026-11", OGGI)["budget"]}
    assert (r["Supermercato"]["stato"], r["Supermercato"]["sopra"]) == (bd.SUPERATO, 1000)
    assert r[None]["stato"] == bd.IN_LINEA


def test_perimetro_del_budget(mesi):
    """La spesa e' sul conto comune: un budget sul perimetro personale non la vede."""
    conn, _ = mesi
    servizi.aggiungi_budget(conn, SUPER, "personale", 18000, "2026-01")
    servizi.aggiungi_budget(conn, SUPER, "tutti", 18000, "2026-01")
    r = {b["perimetro"]: b for b in bd.analizza(conn, "2026-11", OGGI)["budget"]}
    assert r["personale"]["speso"] == 0 and r["personale"]["previsto"] == 0
    assert r["tutti"]["speso"] == 11000


def test_budget_che_parte_dopo_il_mese(mesi):
    conn, _ = mesi
    servizi.aggiungi_budget(conn, SUPER, "comune", 18000, "2026-12")
    r = bd.analizza(conn, "2026-11", OGGI)
    assert r["budget"] == [] and len(r["tutti"]) == 1


def test_avviso_conto_fermo_e_sottocategorie_oltre_la_categoria(mesi):
    conn, _ = mesi
    servizi.aggiungi_budget(conn, SUPER, "comune", 50000, "2026-01")
    servizi.aggiungi_budget(conn, "ALIMENTARI", "comune", 40000, "2026-01")
    r = bd.analizza(conn, "2026-11", date(2026, 12, 5))
    assert r["coperture"][0]["indietro"] == 15
    assert any("sommano" in a for a in r["avvisi"])


def test_da_classificare_fuori_dai_budget(mesi):
    conn, cid = mesi
    servizi.aggiungi_budget(conn, SUPER, "comune", 18000, "2026-01")
    assert bd.analizza(conn, "2026-11", OGGI)["da_classificare"]["comune"] == (0, 0)


# ---------------------------------------------------------------- tassonomia

def test_il_budget_segue_la_sottocategoria_rinominata_e_unita(conn):
    from spese import tassonomia_utente as tu
    servizi.aggiungi_budget(conn, SUPER, "comune", 30000, "2026-01")
    servizi.aggiungi_budget(conn, "ALIMENTARI", "tutti", 50000, "2026-01")
    righe = tu.righe(conn)
    for r in righe:
        if r["l1"] == "ALIMENTARI":
            r["l1"] = "SPESA DI CASA"
        if (r["l1"], r["l2"]) == ("SPESA DI CASA", "Supermercato") and not r["predefinita"]:
            r["l2"] = "Supermercati"
    errori, _ = tu.salva(conn, righe)
    assert not errori
    voci = {(b["l1"], b["l2"], b["perimetro"]) for b in bd.elenco(conn)}
    assert ("SPESA DI CASA", "Supermercato", "comune") in voci
    assert ("SPESA DI CASA", None, "tutti") in voci


def _aggiungi_sottocategoria(conn, l1, l2):
    from spese import tassonomia_utente as tu
    errori, _ = tu.salva(conn, tu.righe(conn) + [{"sezione": "uscite", "l1": l1, "l2": l2, "originale": ""}])
    assert not errori


def test_il_budget_segue_una_sottocategoria_rinominata(conn):
    from spese import tassonomia_utente as tu
    _aggiungi_sottocategoria(conn, "ALIMENTARI", "Mercato")
    servizi.aggiungi_budget(conn, ("ALIMENTARI", "Mercato"), "comune", 30000, "2026-01")
    righe = tu.righe(conn)
    for r in righe:
        if (r["l1"], r["l2"]) == ("ALIMENTARI", "Mercato"):
            r["l2"] = "Mercato rionale"
    errori, _ = tu.salva(conn, righe)
    assert not errori
    assert [(b["l1"], b["l2"]) for b in bd.elenco(conn)] == [("ALIMENTARI", "Mercato rionale")]


def test_unione_tiene_il_budget_della_destinazione(conn):
    from spese import tassonomia_utente as tu
    _aggiungi_sottocategoria(conn, "ALIMENTARI", "Mercato")
    altra = "Mercato"
    servizi.aggiungi_budget(conn, SUPER, "comune", 30000, "2026-01")
    servizi.aggiungi_budget(conn, ("ALIMENTARI", altra), "comune", 10000, "2026-01")
    servizi.aggiungi_budget(conn, ("ALIMENTARI", altra), "tutti", 10000, "2026-01")
    tu.unisci(conn, ("ALIMENTARI", altra), SUPER)
    voci = {(b["l2"], b["perimetro"]): b["importi"] for b in bd.elenco(conn)}
    assert voci == {("Supermercato", "comune"): [("2026-01", 30000)], ("Supermercato", "tutti"): [("2026-01", 10000)]}


def test_una_sottocategoria_con_budget_non_si_elimina_senza_unirla(conn):
    from spese import tassonomia_utente as tu
    servizi.aggiungi_budget(conn, SUPER, "comune", 30000, "2026-01")
    assert tu.utilizzo(conn)[SUPER] == 1


# ---------------------------------------------------------------- pagine

class _Oggi(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 22)


def _pagina_budget():
    import streamlit as st
    from spese import db, percorsi
    from spese.tassonomia_utente import carica
    from spese.ui import pagine_budget
    from test_novita_0210 import _Oggi
    pagine_budget.date = _Oggi
    if "conn" not in st.session_state:
        st.session_state["conn"] = db.apri(percorsi.file_database(), "passphrase di prova")
        carica(st.session_state["conn"])
    pagine_budget.budget()


def _apri(funzione):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_function(funzione, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def test_pagina_vuota_e_aggiunta(conn):
    at = _apri(_pagina_budget)
    assert any("primo budget" in m.value for m in at.markdown)
    at.selectbox(key="bud_n_l1").select("ALIMENTARI").run()
    at.selectbox(key="bud_n_l2").select("Supermercato").run()
    at.number_input(key="bud_n_importo").set_value(300).run()
    at.button(key="bud_n_salva").click().run()
    assert not at.exception
    b = bd.elenco(conn)
    assert [(x["l1"], x["l2"], x["perimetro"], x["importi"]) for x in b] == \
        [("ALIMENTARI", "Supermercato", "tutti", [("2026-11", 30000)])]


def test_pagina_con_i_budget(mesi):
    conn, _ = mesi
    servizi.aggiungi_budget(conn, SUPER, "comune", 18000, "2026-01")
    servizi.aggiungi_budget(conn, "ALIMENTARI", "comune", 10000, "2026-01")
    at = _apri(_pagina_budget)
    metriche = {m.label: m.value for m in at.metric}
    assert metriche["Superati"] == "1" and metriche["A rischio"] == "1"
    assert metriche["Giorni alla fine del mese"] == "8"
    # all'apertura il dettaglio e' quello messo peggio (superato)
    assert any("superato" in e.value for e in at.error)
    assert any("1 rimborso non attribuito" in e.label for e in at.expander)
    # mese chiuso: niente previsione, niente giorni alla fine
    at.selectbox(key="bud_mese").select("2026-09").run()
    assert not at.exception
    assert {m.label: m.value for m in at.metric}["Mese"] == "chiuso"


def test_pagina_gestione_cambia_ed_elimina(mesi):
    conn, _ = mesi
    servizi.aggiungi_budget(conn, SUPER, "comune", 18000, "2026-01")
    at = _apri(_pagina_budget)
    at.number_input(key="bud_g_importo").set_value(250).run()
    at.selectbox(key="bud_g_dal").select("2026-11").run()
    at.button(key="bud_g_cambia").click().run()
    assert bd.elenco(conn)[0]["importi"] == [("2026-01", 18000), ("2026-11", 25000)]
    at.button(key="bud_g_elimina").click().run()              # senza conferma non elimina
    assert bd.elenco(conn) and at.error
    at.checkbox(key="bud_g_conferma").check().run()
    at.button(key="bud_g_elimina").click().run()
    assert bd.elenco(conn) == []


def _grafici_ripartizione():
    """Opzioni dei grafici di "Dove vanno i soldi" con un budget: si leggono dallo stato della sessione."""
    import streamlit as st
    from spese import analisi, db, percorsi
    from spese.tassonomia_utente import carica
    from spese.ui import grafici, pagine_analisi
    if "conn" not in st.session_state:
        st.session_state["conn"] = db.apri(percorsi.file_database(), "passphrase di prova")
        carica(st.session_state["conn"])
    c = st.session_state["conn"]
    f = analisi.Filtri("comune", "2026-01-01", "2026-11-30")
    mesi = ["2025-12", "2026-01", "2026-02"]
    linea = pagine_analisi._budget_per_mese(c, f, "ALIMENTARI", "Supermercato", mesi)
    st.session_state["prova_linea"] = linea
    st.session_state["prova_serie"] = grafici.serie_mensile(mesi, [1, 2, 3], None, budget_cent=linea)["series"][-1]["name"]
    st.session_state["prova_barre_mese"] = pagine_analisi._budget_del_mese(c, f, "2026-11", None)
    st.session_state["prova_barre_sotto"] = pagine_analisi._budget_del_mese(c, f, "2026-11", "ALIMENTARI")
    st.session_state["prova_barre_periodo"] = pagine_analisi._budget_del_mese(c, f, None, None)
    st.session_state["prova_altro_perimetro"] = pagine_analisi._budget_per_mese(
        c, analisi.Filtri("personale", "2026-01-01", "2026-11-30"), "ALIMENTARI", "Supermercato", mesi)
    opz, _ = grafici.barre_orizzontali(["ALIMENTARI"], [5000], budget={"ALIMENTARI": 6000})
    st.session_state["prova_segno"] = opz["series"][-1]["data"]


def test_dove_vanno_i_soldi_mostra_il_budget(mesi):
    conn, _ = mesi
    servizi.aggiungi_budget(conn, SUPER, "comune", 18000, "2026-01")
    servizi.aggiungi_budget(conn, "ALIMENTARI", "comune", 40000, "2026-01")
    at = _apri(_grafici_ripartizione)
    s = at.session_state
    assert s["prova_linea"] == [None, 18000, 18000] and s["prova_serie"] == "Budget"
    assert s["prova_barre_mese"] == {"ALIMENTARI": 40000}
    assert s["prova_barre_sotto"] == {"Supermercato": 18000}
    assert s["prova_barre_periodo"] is None                  # periodo lungo: nessun segno sulle barre
    assert s["prova_altro_perimetro"] is None
    assert s["prova_segno"] == [[60.0, "Alimentari"]]
