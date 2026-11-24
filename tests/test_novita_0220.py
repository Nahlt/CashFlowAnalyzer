"""0.22.0 - formati di import configurati dall'utente: proposte automatiche, lettura, salvataggio, ritiro,
eliminazione, nuove versioni, causali aggiunte (anche ai formati dell'app), estrazione dalla descrizione."""
from datetime import date

import pytest

import sintetici
from spese import configurazione as cfg
from spese import db, estrattori, formati, importa, lettore, percorsi, servizi
from spese.lettore import FileNonRiconosciuto
from spese.ricalcolo import ricalcola


def _scelte_esempio(**altro):
    s = formati.proponi_scelte("estratto.csv", sintetici.banca_esempio_csv())
    s.update(nome="Banca Esempio - Lista movimenti", banca="Banca Esempio")
    s.update(altro)
    return s


def _salva_esempio(conn, **altro):
    s = _scelte_esempio(**altro)
    righe = formati.leggi_file("estratto.csv", sintetici.banca_esempio_csv(), s["separatore"], s["codifica"])[0]
    return servizi.salva_formato(conn, formati.componi(s, righe, "estratto.csv"))


# ---------------------------------------------------------------------------
# Proposte automatiche
# ---------------------------------------------------------------------------
def test_intestazione_proposta_dopo_titolo_testa_e_riga_vuota():
    righe = formati.leggi_file("e.csv", sintetici.banca_esempio_csv())[0]
    assert formati.proponi_intestazione(righe) == 5           # riga 6 del file


def test_intestazione_non_trovata_se_il_separatore_e_sbagliato_e_trovata_con_quello_giusto():
    dati = sintetici.virgole_csv()
    assert formati.proponi_scelte("v.csv", dati)["riga"] is None   # indovinato: la virgola
    s = formati.proponi_scelte("v.csv", dati, separatore=";")
    assert s["riga"] == 0
    assert s["colonne"]["data_contabile"] == "Giorno" and s["colonne"]["importo"] == "Euro"


def test_colonne_formati_e_codifica_proposti():
    s = _scelte_esempio()
    assert s["separatore"] == ";" and s["codifica"] == "cp1252"
    assert s["colonne"] == {"data_contabile": "Data", "data_valuta": "Valuta", "data_operazione": None,
                            "importo": None, "importo_uscite": "Dare", "importo_entrate": "Avere",
                            "descrizione": "Descrizione", "causale": "Causale"}
    assert s["modo_importo"] == "due" and s["date"] == "%d/%m/%Y" and s["decimali"] == "virgola"


def test_tipi_proposti_dal_nome_della_causale():
    s = _scelte_esempio()
    assert s["tipi"] == {"PAGAMENTO POS": "pagamento_carta", "ADDEBITO SDD": "addebito_sepa",
                         "BONIFICO": "bonifico", "ACCREDITO STIPENDIO": "stipendio", "CANONE": "commissione",
                         "COMMISSIONI": "commissione", "PRELIEVO": "prelievo"}
    assert formati.proponi_tipo("COMMISSIONE BONIFICO") == "commissione"      # prima della parola bonifico
    assert formati.proponi_tipo("OPERAZIONE VARIA") is None


def test_testa_del_file_proposta():
    assert _scelte_esempio()["testa"] == {1: None, 2: "numero", 3: "periodo", 4: "saldo_alla_data"}


def test_carta_con_punto_decimale_e_date_iso():
    s = formati.proponi_scelte("c.csv", sintetici.carta_esempio_csv())
    assert s["separatore"] == "," and s["riga"] == 0
    assert s["colonne"]["importo"] == "Amount" and s["colonne"]["descrizione"] == "Description"
    assert s["date"] == "%Y-%m-%d" and s["decimali"] == "punto"


def test_excel_foglio_con_l_intestazione_proposto(tmp_path):
    x = tmp_path / "b.xlsx"
    sintetici.banca_esempio_xlsx(x)
    s = formati.proponi_scelte("b.xlsx", x.read_bytes())
    assert s["foglio"] == 1 and s["riga"] == 2 and s["date"] == "excel"
    assert s["colonne"]["causale"] == "Tipo operazione" and s["colonne"]["importo"] == "Importo"


def test_date_e_decimali_ambigui():
    assert formati.date_ambigue(formati.prova_date(["03/04/2026", "05/06/2026"]))
    assert not formati.date_ambigue(formati.prova_date(["03/04/2026", "25/06/2026"]))
    assert formati.decimali_ambigui(["1.234", "2.500"])
    assert not formati.decimali_ambigui(["1.234,50", "12,00"])
    assert formati.prova_decimali(["1.234,50", "12,00"])[0] == ("virgola", 2, 2)


# ---------------------------------------------------------------------------
# Lettura con il formato in prova
# ---------------------------------------------------------------------------
def test_prova_legge_movimenti_testa_ed_estrazioni():
    fl = formati.prova("estratto.csv", sintetici.banca_esempio_csv(), _scelte_esempio(), ["Mario Rossi"])
    assert len(fl.movimenti) == 12
    assert fl.scartate == [(19, "data 'Totale' non nel formato %d/%m/%Y")]
    assert fl.metadati == {"numero": "12345/67", "periodo_da": "01/10/2026", "periodo_a": "31/10/2026",
                           "saldo_alla_data": "+2345,67", "data_saldo": "31/10/2026"}
    m = {x["n_riga"]: x for x in fl.movimenti}
    assert (m[7]["tipo"], m[7]["importo_cent"], m[7]["esercente"], m[7]["data"]) == \
        ("pagamento_carta", -4590, "SUPERMERCATO ROSSI MILANO", "2026-10-02")
    assert m[8]["importo_cent"] == 185000 and m[8]["tipo"] == "stipendio"
    assert m[9]["esercente"] == "ENERGIA ESEMPIO"                     # seconda regola: dopo SDD
    assert (m[11]["controparte_nome"], m[11]["controparte_iban"]) == ("MARIO BIANCHI", sintetici.IBAN_BIANCHI)
    assert m[14]["tipo"] == "bonifico" and m[14]["controparte_nome"] is None


def test_esercente_dalla_descrizione_senza_regole():
    s = _scelte_esempio()
    s["estrattori"]["esercente"]["regole"] = []
    fl = formati.prova("estratto.csv", sintetici.banca_esempio_csv(), s, ["Mario Rossi"])
    m = {x["n_riga"]: x for x in fl.movimenti}
    assert m[7]["esercente"] == "POS 02/10 SUPERMERCATO ROSSI MILANO"
    assert m[7]["chiave"] == m[10]["chiave"].replace("PANIFICIO DEL CORSO", "SUPERMERCATO ROSSI MILANO")


def test_carta_con_segno_invertito_e_tipo_per_le_uscite():
    s = formati.proponi_scelte("c.csv", sintetici.carta_esempio_csv())
    s.update(nome="Carta", tipo_conto="carta", inverti_segno=True, tipo_uscite="pagamento_carta",
             tipo_entrate="pagamento_carta")
    fl = formati.prova("c.csv", sintetici.carta_esempio_csv(), s)
    assert [m["importo_cent"] for m in fl.movimenti] == [-2399, -4850, 2399, -123450]
    assert {m["tipo"] for m in fl.movimenti} == {"pagamento_carta"}
    assert fl.movimenti[1]["esercente"] == "RISTORANTE DA MARIO ROMA"
    s.update(tipo_entrate="altro")
    fl = formati.prova("c.csv", sintetici.carta_esempio_csv(), s)
    assert [m["tipo"] for m in fl.movimenti] == ["pagamento_carta", "pagamento_carta", "altro", "pagamento_carta"]


def test_composizione_e_validazione():
    s = _scelte_esempio()
    righe = formati.leggi_file("e.csv", sintetici.banca_esempio_csv(), ";", "cp1252")[0]
    p = formati.componi(s, righe, "e.csv")
    assert p["firma"] == ["Data", "Valuta", "Dare", "Avere", "Causale", "Descrizione"]
    assert p["separatore"] == ";" and p["codifica"] == "cp1252" and p["codice"] == ["Causale"]
    assert p["tipi"]["*"] == "altro" and p["data_saldo_da"] == "periodo_a"
    assert formati.valida(p) == []
    p2 = dict(p, nome="", colonne=dict(p["colonne"], descrizione="Nota"))
    assert formati.valida(p2) == ["manca il nome del formato", "la colonna \"Nota\" non e' nell'intestazione"]


def test_etichetta_della_testa_vale_anche_il_mese_dopo():
    r = formati.regola_testa("Saldo contabile al 31/10/2026", "2.345,67", "saldo_alla_data")
    import re
    x = re.fullmatch(r["etichetta"], "Saldo contabile al 30/11/2026")
    assert x and x.group("data") == "30/11/2026"


# ---------------------------------------------------------------------------
# Estrattori
# ---------------------------------------------------------------------------
def test_estrattori_iban_controllato_e_date_vicine():
    e = {"iban": {"colonna": "D", "regole": ["iban_controllato"]},
         "data_op": {"colonna": "D", "regole": ["giorno_mese"]}}
    assert estrattori.estrai(e, "bonifico", {"D": "A FAVORE DI X IBAN %s" % sintetici.IBAN_BIANCHI},
                             "2026-10-10")["iban"] == sintetici.IBAN_BIANCHI
    sbagliato = sintetici.IBAN_BIANCHI[:-1] + ("7" if sintetici.IBAN_BIANCHI[-1] != "7" else "8")
    assert "iban" not in estrattori.estrai(e, "bonifico", {"D": "IBAN " + sbagliato}, "2026-10-10")
    # l'anno si completa all'indietro a cavallo di capodanno; una data lontana dalla contabile non vale
    assert estrattori.estrai(e, "pagamento_carta", {"D": "POS 30/12 BAR"}, "2027-01-02")["data_op"] == "2026-12-30"
    assert "data_op" not in estrattori.estrai(e, "pagamento_carta", {"D": "POS 30/06 BAR"}, "2027-01-02")
    # IBAN solo per bonifici, giroconti e stipendi; regole sconosciute ignorate
    assert "iban" not in estrattori.estrai(e, "pagamento_carta", {"D": sintetici.IBAN_BIANCHI}, "2026-10-10")
    assert estrattori.estrai({"esercente": {"colonna": "D", "regole": ["inesistente"]}}, "pagamento_carta",
                             {"D": "X"}) == {}


def test_proposta_delle_regole_in_ordine():
    righe = [("pagamento_carta", "POS 02/10 BAR", "2026-10-03"), ("addebito_sepa", "SDD LUCE MANDATO 1", None),
             ("pagamento_carta", "POS 05/10 EDICOLA", None)]
    assert estrattori.proposta("esercente", righe) == ["dopo_pos_data", "dopo_sdd"]
    assert estrattori.prova_regole("esercente", ["dopo_pos_data"], righe)[:2] == (2, 3)


# ---------------------------------------------------------------------------
# Formati salvati: riconoscimento, import, ritiro, eliminazione, nuova versione
# ---------------------------------------------------------------------------
def test_formato_salvato_riconosce_e_importa(conn):
    fid = _salva_esempio(conn)
    assert fid == "utente_1" and cfg.profili()[fid]["utente"]
    fl, suggerito, gia = importa.anteprima(conn, "ottobre.csv", sintetici.banca_esempio_csv())
    assert fl.profilo["id"] == fid and len(fl.movimenti) == 12 and suggerito is None and not gia
    conto = importa.crea_conto(conn, "Conto Esempio", fid, "conto", "personale", "Banca Esempio")
    rep = importa.importa(conn, conto, "ottobre.csv", sintetici.banca_esempio_csv())
    assert rep["nuove"] == 12 and rep["profilo"] == "Banca Esempio - Lista movimenti"
    assert rep["quadratura"] == "saldo registrato: si verifica dal prossimo import"
    f = conn.execute("SELECT coperto_al, coperto_da FROM import_file").fetchone()
    assert tuple(f) == ("2026-10-31", "periodo")
    assert conn.execute("SELECT numero FROM conti WHERE id = ?", (conto,)).fetchone()[0] == "12345/67"
    ricalcola(conn)
    # il file del mese dopo: l'etichetta del saldo ha un'altra data
    novembre = [("03/11/2026", "03/11/2026", "10,00", "", "PAGAMENTO POS", "POS 02/11 BAR SPORT")]
    rep = importa.importa(conn, conto, "novembre.csv", sintetici.banca_esempio_csv(righe=novembre, mese="11/2026"))
    assert rep["nuove"] == 1
    assert conn.execute("SELECT max(data) FROM saldi WHERE conto_id = ?", (conto,)).fetchone()[0] == "2026-11-30"


def test_i_formati_dell_app_vincono_e_i_formati_restano_dopo_la_riapertura(conn):
    fid = _salva_esempio(conn)
    # un file Credem resta Credem anche se un formato dell'utente avesse colonne in comune
    fl, _, _ = importa.anteprima(conn, "c.csv", sintetici.credem_csv())
    assert fl.profilo["id"] == "credem"
    cfg.imposta_formati_utente({}, {})
    c2 = db.apri(percorsi.file_database(), "passphrase di prova")
    assert fid in cfg.profili()
    c2.close()


def test_formato_ritirato_non_riconosce_ma_resta_per_gli_import(conn):
    fid = _salva_esempio(conn)
    conto = importa.crea_conto(conn, "Conto Esempio", fid, "conto", "personale", "Banca Esempio")
    importa.importa(conn, conto, "ottobre.csv", sintetici.banca_esempio_csv())
    servizi.ritira_formato(conn, fid)
    with pytest.raises(FileNonRiconosciuto):
        importa.anteprima(conn, "ottobre.csv", sintetici.banca_esempio_csv())
    assert cfg.profili()[fid]["stato"] == "ritirato"
    ricalcola(conn)
    servizi.riattiva_formato(conn, fid)
    assert importa.anteprima(conn, "x.csv", sintetici.banca_esempio_csv())[0].profilo["id"] == fid


def test_elimina_solo_senza_import(conn):
    fid = _salva_esempio(conn)
    conto = importa.crea_conto(conn, "Conto Esempio", fid, "conto", "personale", "Banca Esempio")
    importa.importa(conn, conto, "ottobre.csv", sintetici.banca_esempio_csv())
    with pytest.raises(ValueError, match="usato da 1 import"):
        servizi.elimina_formato(conn, fid)
    imp = conn.execute("SELECT id FROM import_file").fetchone()[0]
    importa.annulla_import(conn, imp)
    servizi.elimina_formato(conn, fid)
    assert fid not in cfg.profili()
    assert conn.execute("SELECT profilo FROM conti WHERE id = ?", (conto,)).fetchone()[0] is None


def test_nuova_versione_ritira_la_precedente(conn):
    vecchio = _salva_esempio(conn)
    s = _scelte_esempio(nome="Banca Esempio v2")
    s["tipi"]["CANONE"] = "imposte"
    righe = formati.leggi_file("e.csv", sintetici.banca_esempio_csv(), ";", "cp1252")[0]
    nuovo = servizi.salva_formato(conn, formati.componi(s, righe, "e.csv"), sostituisce=vecchio)
    assert nuovo == "utente_2"
    el = {f["id"]: f for f in formati.elenco(conn)}
    assert el[vecchio]["stato"] == "ritirato" and el[nuovo]["sostituisce"] == vecchio
    fl = importa.anteprima(conn, "e.csv", sintetici.banca_esempio_csv())[0]
    assert fl.profilo["id"] == nuovo
    assert {m["tipo"] for m in fl.movimenti if m["codice"] == "CANONE"} == {"imposte"}


def test_salvataggio_rifiutato_se_il_formato_non_e_valido(conn):
    s = _scelte_esempio(nome=" ")
    righe = formati.leggi_file("e.csv", sintetici.banca_esempio_csv(), ";", "cp1252")[0]
    with pytest.raises(ValueError, match="manca il nome"):
        servizi.salva_formato(conn, formati.componi(s, righe, "e.csv"))
    assert formati.elenco(conn) == []


def test_conflitti_tra_formati(conn):
    fid = _salva_esempio(conn)
    p = dict(cfg.profili()[fid], id="bozza")
    assert formati.conflitti(p) == [(fid, "Banca Esempio - Lista movimenti")]
    assert formati.conflitti(p, escludi=fid) == []


def test_separatore_del_formato_rispettato_se_quello_indovinato_e_sbagliato(conn):
    dati = sintetici.virgole_csv()
    with pytest.raises(FileNonRiconosciuto):
        lettore.leggi("v.csv", (), dati)
    s = formati.proponi_scelte("v.csv", dati, separatore=";")
    s.update(nome="Virgole", tipo_uscite="pagamento_carta")
    righe = formati.leggi_file("v.csv", dati, ";")[0]
    servizi.salva_formato(conn, formati.componi(s, righe, "v.csv"))
    fl = lettore.leggi("v.csv", (), dati)
    assert len(fl.movimenti) == 8 and fl.movimenti[0]["descrizione"] == "SPESA A, B, C, D, E, F, G, H"


def test_excel_riconosciuto_anche_nel_secondo_foglio(conn, tmp_path):
    x = tmp_path / "b.xlsx"
    sintetici.banca_esempio_xlsx(x)
    s = formati.proponi_scelte("b.xlsx", x.read_bytes())
    s.update(nome="Banca Excel")
    righe = formati.leggi_file("b.xlsx", x.read_bytes(), foglio=s["foglio"])[0]
    fid = servizi.salva_formato(conn, formati.componi(s, righe, "b.xlsx"))
    fl = lettore.leggi("b.xlsx", (), x.read_bytes())
    assert fl.profilo["id"] == fid
    assert [(m["tipo"], m["importo_cent"]) for m in fl.movimenti] == \
        [("pagamento_carta", -350), ("bonifico", 5000), ("pagamento_carta", -220), ("commissione", -100)]
    assert fl.movimenti[0]["esercente"] == "BAR CENTRALE" and fl.movimenti[1]["controparte_nome"] == "GIULIA BIANCHI"


# ---------------------------------------------------------------------------
# Causali aggiunte
# ---------------------------------------------------------------------------
def test_causali_aggiunte_a_un_formato_dell_app(conn):
    credem = importa.crea_conto(conn, "Credem", "credem", "conto", "comune", "Credem")
    importa.importa(conn, credem, "c.csv", sintetici.credem_csv())
    assert formati.causali_senza_tipo(conn, "credem") == [("OCCIN", 1, "MOVIMENTO SCONOSCIUTO")]
    servizi.aggiungi_causale(conn, "credem", "OCCIN", "addebito_sepa")
    assert formati.causali_senza_tipo(conn, "credem") == []
    assert formati.causali_aggiunte(conn, "credem")[0][:2] == ("OCCIN", "addebito_sepa")
    # vale per i prossimi import: il movimento gia' importato resta com'era
    assert conn.execute("SELECT tipo FROM movimenti WHERE codice = 'OCCIN'").fetchone()[0] == "altro"
    fl = lettore.leggi("c.csv", (), sintetici.credem_csv())
    assert [m["tipo"] for m in fl.movimenti if m["codice"] == "OCCIN"] == ["addebito_sepa"]
    # una causale che il formato traduce gia' non si cambia
    with pytest.raises(ValueError, match="gia' tradotta"):
        servizi.aggiungi_causale(conn, "credem", "PDINT", "bonifico")
    # i formati con un tipo unico o con un lettore proprio non prendono causali
    with pytest.raises(ValueError, match="non prende il tipo"):
        servizi.aggiungi_causale(conn, "ing_carta", "X", "bonifico")
    servizi.togli_causale(conn, "credem", "OCCIN")
    assert [m["tipo"] for m in lettore.leggi("c.csv", (), sintetici.credem_csv()).movimenti
            if m["codice"] == "OCCIN"] == ["altro"]


def test_causale_aggiunta_superata_da_una_versione_dell_app(conn, monkeypatch):
    servizi.aggiungi_causale(conn, "credem", "OCCIN", "addebito_sepa")
    originale = cfg.profili_app()
    nuovi = dict(originale, credem=dict(originale["credem"], tipi=dict(originale["credem"]["tipi"], OCCIN="bonifico")))
    monkeypatch.setattr(cfg, "profili_app", lambda: nuovi)
    formati.carica(conn)
    assert formati.causali_aggiunte(conn, "credem")[0][3] is True
    assert cfg.profili()["credem"]["tipi"]["OCCIN"] == "bonifico"          # vince il formato dell'app


def test_causali_aggiunte_ai_formati_dell_utente(conn):
    fid = _salva_esempio(conn)
    servizi.aggiungi_causale(conn, fid, "BONIFICO ISTANTANEO", "bonifico")
    assert cfg.profili()[fid]["tipi"]["BONIFICO ISTANTANEO"] == "bonifico"
    with pytest.raises(ValueError, match="gia' tradotta"):
        servizi.aggiungi_causale(conn, fid, "PAGAMENTO POS", "bonifico")


def test_schema_4_con_le_nuove_tabelle(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.VERSIONE_SCHEMA == 4
    tabelle = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"formati_import", "causali_aggiunte"} <= tabelle


def test_data_esempio_dei_formati():
    assert formati.esempio_data("%d/%m/%Y") == "03/10/2026" and formati.esempio_data("%Y-%m-%d") == "2026-10-03"
    assert formati.ESEMPIO_DATA == date(2026, 10, 3)


@pytest.mark.parametrize("info, testo, atteso", [
    ("controparte", "Ordinante: GIULIA BIANCHI Causale: regalo", "GIULIA BIANCHI"),
    ("controparte", "BONIFICO A FAVORE DI MARIO BIANCHI Rif. 123", "MARIO BIANCHI"),
    ("controparte", "Beneficiario: LUCA VERDI IBAN: IT00", "LUCA VERDI"),
    ("controparte", "BON. ISTANTANEO A DEBITO Giulia Bianchi Rif. XYZ987654321 cena", "Giulia Bianchi"),
    ("esercente", "Operazione presso: BAR SPORT Carta N. 1234", "BAR SPORT"),
    ("esercente", "PAGAMENTO PREAUTORIZZATO OCTOPUS ENERGY ITALIA SRL MANDATO QX12", "OCTOPUS ENERGY ITALIA SRL"),
    ("ora", "Pagamento del 10/10/2026 alle ore 12:30", "12:30"),
])
def test_regole_note_sulle_diciture_delle_banche(info, testo, atteso):
    tipo = {"controparte": "bonifico", "esercente": "pagamento_carta", "ora": "altro"}[info]
    nomi = [r.nome for r in estrattori.regole_di(info) if not r.nome.startswith("tutto_il_campo")]
    assert estrattori.estrai({info: {"colonna": "D", "regole": nomi}}, tipo, {"D": testo})[info] == atteso


def test_nuova_versione_parte_dalle_scelte_del_formato(conn):
    fid = _salva_esempio(conn, inverti_segno=False)
    servizi.aggiungi_causale(conn, fid, "BONIFICO ISTANTANEO", "bonifico")
    p = cfg.profili()[fid]
    p = dict(p, tipi=dict(p["tipi"], CANONE="imposte"))
    s = formati.scelte_da_profilo(p, "nuovo.csv", sintetici.banca_esempio_csv())
    assert s["nome"] == "Banca Esempio - Lista movimenti" and s["banca"] == "Banca Esempio"
    assert s["colonne"]["importo_uscite"] == "Dare" and s["tipi"]["CANONE"] == "imposte"
    assert s["estrattori"]["esercente"]["regole"] == ["dopo_pos_data", "dopo_sdd"]


# ---------------------------------------------------------------------------
# Pagine (AppTest)
# ---------------------------------------------------------------------------
def _pagina_formati():
    import streamlit as st
    import sintetici
    from spese import db, percorsi
    from spese.tassonomia_utente import carica
    from spese.ui import pagine_formati as pf
    if "conn" not in st.session_state:
        st.session_state["conn"] = db.apri(percorsi.file_database(), "passphrase di prova")
        carica(st.session_state["conn"])
        pf.avvia("estratto_ottobre.csv", sintetici.banca_esempio_csv(), "formati")
    pf.formati_import()


def _pagina_importa():
    import streamlit as st
    import sintetici
    from spese import db, percorsi
    from spese.tassonomia_utente import carica
    from spese.ui import pagine_dati, pagine_formati as pf
    if "conn" not in st.session_state:
        st.session_state["conn"] = db.apri(percorsi.file_database(), "passphrase di prova")
        carica(st.session_state["conn"])
    if "fmt_proc" not in st.session_state:
        pf.file_non_riconosciuto("estratto_ottobre.csv", sintetici.banca_esempio_csv(), "x")
    else:
        pagine_dati.importa()


def _apri(funzione):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_function(funzione, default_timeout=90)
    at.run()
    assert not at.exception, at.exception
    return at


def _testi(at):
    return " ".join([m.value for m in at.markdown] + [m.value for m in at.success] + [m.value for m in at.warning]
                    + [m.value for m in at.info] + [m.value for m in at.caption])


def test_procedura_completa_dalla_pagina_formati(conn):
    at = _apri(_pagina_formati)
    assert "Ho proposto la **riga 6**" in _testi(at) and "12 movimenti letti · 1 riga scartata" in _testi(at)
    riga = next(s for s in at.selectbox if s.label.startswith("Riga d'intestazione"))
    riga.set_value(2).run()                        # proposta corretta a mano: la riga "Periodo"
    assert not at.exception
    assert "0 movimenti letti" in _testi(at) and "Colonne trovate:** Periodo" in _testi(at)
    assert at.button(key="fmt_avanti").disabled
    next(s for s in at.selectbox if s.label.startswith("Riga d'intestazione")).set_value(5).run()
    assert "12 movimenti letti" in _testi(at) and not at.button(key="fmt_avanti").disabled
    for passo in range(2, 7):
        at.button(key="fmt_avanti").click().run()
        assert not at.exception, (passo, at.exception)
        assert ("Passo %d di 6" % passo) in at.get("progress")[0].proto.text
    assert "copertura del file fino al **31/10/2026**" in _testi(at)
    next(t for t in at.text_input if t.label == "Nome del formato").set_value("Banca Esempio CSV").run()
    at.button(key="fmt_salva").click().run()
    assert not at.exception
    assert [f["nome"] for f in formati.elenco(conn)] == ["Banca Esempio CSV"]
    assert "Formato «Banca Esempio CSV» salvato." in _testi(at)
    # pagina dei formati: ritira e riattiva
    at.button(key="fmt_ritira_utente_1").click().run()
    assert formati.elenco(conn)[0]["stato"] == "ritirato"
    at.button(key="fmt_riattiva_utente_1").click().run()
    assert formati.elenco(conn)[0]["stato"] == "attivo"


def test_passi_con_date_ambigue_e_tipi_da_scegliere(conn):
    at = _apri(_pagina_formati)
    at.button(key="fmt_avanti").click().run()
    at.button(key="fmt_avanti").click().run()          # passo 3: date leggibili in un modo solo
    assert "Formato delle date · proposto" in [s.label for s in at.selectbox]
    at.button(key="fmt_avanti").click().run()          # passo 4: tutte le causali hanno un tipo proposto
    assert "causali da scegliere" not in _testi(at)
    at.button(key="fmt_avanti").click().run()          # passo 5: regole proposte e risultati
    assert "Trovato in **6 su 6** movimenti." in _testi(at)          # esercente: dopo POS + dopo SDD


def test_file_non_riconosciuto_apre_la_procedura_in_importa(conn):
    at = _apri(_pagina_importa)
    assert any("nessun formato riconosce" in e.value for e in at.error)
    at.button(key="fmt_avvia_x").click().run()
    assert not at.exception
    assert st_stato(at)["origine"] == "importa"
    assert "Passo 1 di 6" in at.get("progress")[0].proto.text
    at.button(key="fmt_annulla").click().run()
    assert "fmt_proc" not in at.session_state


def st_stato(at):
    return at.session_state["fmt_proc"]


def test_pagina_causali_senza_tipo(conn):
    credem = importa.crea_conto(conn, "Credem", "credem", "conto", "comune", "Credem")
    importa.importa(conn, credem, "c.csv", sintetici.credem_csv())

    def pagina():
        import streamlit as st
        from spese import db, percorsi
        from spese.ui import pagine_formati as pf
        if "conn" not in st.session_state:
            st.session_state["conn"] = db.apri(percorsi.file_database(), "passphrase di prova")
        pf.formati_import()
    at = _apri(pagina)
    assert any("OCCIN" in m.value for m in at.markdown)
    at.selectbox(key="fmt_caus_tipo_credem_0").set_value("addebito_sepa").run()
    at.button(key="fmt_caus_agg_credem_0").click().run()
    assert not at.exception
    assert formati.causali_aggiunte(conn, "credem")[0][:2] == ("OCCIN", "addebito_sepa")
