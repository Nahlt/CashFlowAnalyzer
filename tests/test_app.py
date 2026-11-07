import sys
from decimal import Decimal

import pytest

import sintetici
from spese import db, importa, llm, servizi
from spese.numeri import FormatoNonValido, a_centesimi, a_decimal
from spese.privacy import controlla
from spese.ricalcolo import ricalcola


def righe(conn, sql, *par):
    return [dict(r) for r in conn.execute(sql, par).fetchall()]


def analisi_di(conn, conto, **filtro):
    cond = " AND ".join("m.%s = ?" % k for k in filtro)
    return righe(conn, "SELECT a.*, m.tipo, m.esercente FROM analisi a JOIN movimenti m ON m.id = a.mov_id "
                       "WHERE a.conto_id = ? AND " + cond, conto, *filtro.values())


# --- numeri -------------------------------------------------------------------
def test_numeri():
    assert a_centesimi("-1.000,50", "italiano") == -100050
    assert a_centesimi("-44,7", "italiano") == -4470
    assert a_centesimi("-30", "italiano") == -3000
    assert a_centesimi("-40.780000", "punto") == -4078
    assert a_centesimi(-1251.63, "excel") == -125163
    assert a_decimal("2.0000000000", "punto") == Decimal("2")
    assert a_centesimi("", "punto") is None
    with pytest.raises(FormatoNonValido):
        a_centesimi("4.212222", "punto")       # cifre oltre i centesimi: mai arrotondare in silenzio


# --- database cifrato ------------------------------------------------------------
def test_database_cifrato(tmp_path):
    p = tmp_path / "x.db"
    c = db.apri(p, "giusta-passphrase")
    c.execute("INSERT INTO impostazioni VALUES ('segreto', '\"ESSELUNGA\"')")
    c.close()
    assert b"ESSELUNGA" not in p.read_bytes()
    with pytest.raises(db.PassphraseErrata):
        db.apri(p, "sbagliata")
    assert db.apri(p, "giusta-passphrase").execute("SELECT valore FROM impostazioni").fetchone()[0] == '"ESSELUNGA"'


# --- import ----------------------------------------------------------------------
def test_import_e_deduplica(caricato):
    conn, conti, rep = caricato
    assert rep["ing"]["quadratura"] == "OK"
    assert rep["credem"]["nuove"] == 8                       # due righe identiche legittime restano due
    assert rep["tr"]["nuove"] == 9
    assert rep["carta"]["nuove"] == 3
    again = importa.importa(conn, conti["tr"], "tr.csv", sintetici.tr_csv())
    assert again["nuove"] == 0 and again["duplicate"] == 9
    m = righe(conn, "SELECT esercente, paese, ora FROM movimenti WHERE conto_id = ? AND tipo = 'pagamento_carta' "
                    "ORDER BY data DESC LIMIT 1", conti["credem"])[0]
    assert m["esercente"] == "ESSELUNGA VIALE MILANO"         # titolare tolto, paese separato
    assert m["paese"] == "ITA" and m["ora"] == "17:58"
    mcc = righe(conn, "SELECT mcc FROM movimenti WHERE esercente = 'MISTERO SNC'")[0]["mcc"]
    assert mcc == "5999"
    carta = righe(conn, "SELECT esercente, intermediario FROM movimenti WHERE conto_id = ? AND esercente LIKE 'VEEPEE%'", conti["carta"])[0]
    assert carta == {"esercente": "VEEPEE IT", "intermediario": "PAYPAL"}


# --- ricalcolo -------------------------------------------------------------------
def test_trasferimenti_e_perimetri(caricato):
    conn, conti, _ = caricato
    # ING (personale) -> Credem (comune): contributo, non giroconto
    ing = analisi_di(conn, conti["ing"], tipo="bonifico")[0]
    assert (ing["natura"], ing["l2"]) == ("uscita", "Contributo versato")
    cr = [a for a in analisi_di(conn, conti["credem"], tipo="bonifico") if a["importo_cent"] > 0][0]
    assert (cr["natura"], cr["l2"]) == ("entrata", "Contributo ricevuto")
    # ING -> Trade Republic, stesso perimetro e IBAN: trasferimento interno su entrambi i lati
    assert analisi_di(conn, conti["ing"], tipo="giroconto")[0]["natura"] == "trasferimento_interno"
    assert analisi_di(conn, conti["tr"], tipo="versamento")[0]["natura"] == "trasferimento_interno"
    # bonifico TR a nome del titolare senza contropartita: proposta, da rivedere
    usc = analisi_di(conn, conti["tr"], tipo="bonifico")[0]
    assert usc["natura"] == "trasferimento_interno" and usc["da_rivedere"] == 1
    # addebito SEPA con controparte = titolare: NON e' un trasferimento
    sepa = analisi_di(conn, conti["tr"], tipo="addebito_sepa")[0]
    assert sepa["natura"] == "uscita"


def test_destinazione_per_beneficiario(caricato):
    conn, conti, _ = caricato
    benef = righe(conn, "SELECT beneficiario FROM trasferimenti WHERE stato = 'proposto_proprio'")[0]["beneficiario"]
    servizi.decidi_destinazione(conn, benef, "non_tracciato")
    ricalcola(conn)
    usc = analisi_di(conn, conti["tr"], tipo="bonifico")[0]
    assert (usc["natura"], usc["l2"], usc["da_rivedere"]) == ("risparmio", "Accantonamento", 0)


def test_carta_di_credito_per_ciclo(caricato):
    conn, conti, _ = caricato
    add = analisi_di(conn, conti["ing"], tipo="addebito_carta_credito")[0]
    assert add["natura"] == "contenitore"
    c = righe(conn, "SELECT * FROM contenitori")[0]
    assert c["stato"] == "dettagliato" and c["righe"] == 2      # la riga del 16/10 e' nel ciclo successivo


def test_componenti_trade_republic(caricato):
    conn, conti, _ = caricato
    buy = analisi_di(conn, conti["tr"], tipo="acquisto_titoli")
    comp = {a["componente"]: (a["natura"], a["l2"], a["importo_cent"]) for a in buy}
    assert comp["principale"] == ("risparmio", "Accantonamento", -10000)
    assert comp["commissione"] == ("uscita", "Commissioni bancarie", -100)
    inter = {a["componente"]: (a["natura"], a["l2"]) for a in analisi_di(conn, conti["tr"], tipo="interessi")}
    assert inter == {"principale": ("entrata", "Interessi e cedole"), "imposta": ("uscita", "Imposte su rendimenti")}
    bollo = analisi_di(conn, conti["tr"], tipo="imposte")
    assert [(a["componente"], a["l2"]) for a in bollo] == [("imposta", "Bollo")]
    assert analisi_di(conn, conti["tr"], tipo="movimento_titoli") == []    # migrazione: nessun importo


def test_catena_e_regole(caricato):
    conn, conti, _ = caricato
    lidl = [a for a in analisi_di(conn, conti["tr"], tipo="pagamento_carta") if a["esercente"] == "LIDL MILANO"][0]
    assert lidl["l2"] == "Supermercato"
    ess = [a for a in analisi_di(conn, conti["credem"], tipo="pagamento_carta") if a["esercente"].startswith("ESSELUNGA")][0]
    assert ess["l2"] == "Supermercato" and ess["fonte"] == "regola"
    ign = [a for a in analisi_di(conn, conti["credem"], tipo="pagamento_carta") if "PINCO" in a["esercente"]]
    assert all(a["l2"] is None and a["da_rivedere"] for a in ign)
    servizi.conferma_esercente(conn, ign[0]["chiave"], "Negozi alimentari")
    ricalcola(conn)
    ign = [a for a in analisi_di(conn, conti["credem"], tipo="pagamento_carta") if "PINCO" in a["esercente"]]
    assert all(a["l2"] == "Negozi alimentari" and not a["da_rivedere"] for a in ign)
    # bonifico a terzi: regola sul beneficiario
    terzi = [a for a in analisi_di(conn, conti["credem"], tipo="bonifico") if a["importo_cent"] < 0][0]
    assert terzi["natura"] == "uscita" and terzi["l2"] is None
    servizi.crea_regola(conn, terzi["chiave"], "Ristoranti")
    ricalcola(conn)
    terzi = [a for a in analisi_di(conn, conti["credem"], tipo="bonifico") if a["importo_cent"] < 0][0]
    assert terzi["l2"] == "Ristoranti"
    sconosciuto = analisi_di(conn, conti["credem"], tipo="altro")[0]
    assert sconosciuto["da_rivedere"] == 1 and "OCCIN" in sconosciuto["motivo"]


def test_ricalcolo_idempotente(caricato):
    conn, _, _ = caricato
    prima = righe(conn, "SELECT mov_id, componente, natura, l2, importo_cent FROM analisi ORDER BY mov_id, componente")
    ricalcola(conn)
    dopo = righe(conn, "SELECT mov_id, componente, natura, l2, importo_cent FROM analisi ORDER BY mov_id, componente")
    assert prima == dopo


# --- privacy e LLM ----------------------------------------------------------------
def test_controllo_di_sicurezza(conn):
    assert controlla(conn, "CRF EXP GAR# MILANO") == []
    assert "IBAN" in controlla(conn, "PAGAMENTO IT60X0542811101000000123456")
    assert "sequenza di cifre" in controlla(conn, "NEGOZIO 1234567")
    assert "nome o parola sensibile" in controlla(conn, "BAR DA MARIO")
    assert "area riservata (solo locale)" in controlla(conn, "FARMACIA COMUNALE")


def test_modello_locale_e_claude(caricato, monkeypatch):
    conn, conti, _ = caricato
    voci = llm.chiavi_da_classificare(conn)
    chiavi = [v["chiave"] for v in voci]
    assert "MISTERO SNC" in chiavi

    class Risposta:
        def __init__(self, dati):
            self.dati = dati

        def raise_for_status(self):
            pass

        def json(self):
            return self.dati

    def finto_post(url, json=None, timeout=None):
        import json as js
        items = js.loads(json["messages"][1]["content"].split("Esercenti da classificare:\n")[1])
        ris = []
        for it in items:
            nome = "Inventato Spa" if "MISTERO" in it["esercente"] else it["esercente"].title()
            ris.append({"idx": it["idx"], "l2": "Varie", "esercente_normalizzato": nome, "confidenza": 0.9})
        return Risposta({"message": {"content": js.dumps({"risultati": ris})}})

    import requests
    monkeypatch.setattr(requests, "post", finto_post)
    pid, out = llm.classifica_locale(conn, [{"chiave": k, "testo": k, "paese": None} for k in chiavi])
    assert out["MISTERO SNC"][1] == 0.0          # nome inventato: confidenza azzerata
    assert not righe(conn, "SELECT * FROM cache_esercenti")          # in anteprima: nulla e' applicato
    assert llm.applica_prova(conn, pid) == len(out)
    assert {r["modello"] for r in righe(conn, "SELECT modello FROM cache_esercenti")} == {"qwen2.5:3b"}
    ricalcola(conn)
    llm.prepara_candidati(conn)
    cand = {r["chiave"]: r["stato"] for r in righe(conn, "SELECT chiave, stato FROM candidati_invio")}
    assert cand.get("MISTERO SNC") == "approvato"          # spuntata: parte solo col pulsante Invia
    conn.execute("UPDATE candidati_invio SET stato = 'approvato'")
    voci_inv, sistema, utente = llm.payload_claude(conn)
    assert "MISTERO SNC" in utente
    for vietato in ("IT60X", "Rossi", "ROSSI", "-11", "2026-"):
        assert vietato not in utente


def test_mcc_a_tre_cifre(conn):
    from spese.lettore import leggi
    dati = sintetici.tr_csv().replace(b'"t3","","","","5999"', b'"t3","","","","742"')
    fl = leggi("tr.csv", [], dati)
    assert [m["mcc"] for m in fl.movimenti if m["id_banca"] == "t3"] == ["0742"]


# --- annullamento di un import ------------------------------------------------------
def test_annulla_import(caricato):
    conn, conti, _ = caricato
    tr_imp = righe(conn, "SELECT id FROM import_file WHERE conto_id = ?", conti["tr"])[0]["id"]
    mov = righe(conn, "SELECT id FROM movimenti WHERE conto_id = ? LIMIT 1", conti["tr"])[0]["id"]
    servizi.decidi_movimento(conn, mov, "Varie")
    esito = importa.annulla_import(conn, tr_imp)
    ricalcola(conn)
    assert esito["tolti"] == 9 and esito["mantenuti"] == 0 and esito["conto_eliminato"] is None
    assert righe(conn, "SELECT * FROM movimenti WHERE conto_id = ?", conti["tr"]) == []
    assert righe(conn, "SELECT * FROM decisioni WHERE mov_id = ?", mov) == []
    assert righe(conn, "SELECT * FROM righe_raw WHERE import_id = ?", tr_imp) == []
    # il giroconto ING -> TR non trova piu' la contropartita
    giro = analisi_di(conn, conti["ing"], tipo="giroconto")[0]["mov_id"]
    assert righe(conn, "SELECT controparte_mov_id FROM trasferimenti WHERE mov_id = ?", giro)[0]["controparte_mov_id"] is None
    # reimport: tutto torna
    assert importa.importa(conn, conti["tr"], "tr.csv", sintetici.tr_csv())["nuove"] == 9


def test_annulla_import_con_righe_in_comune(caricato):
    conn, conti, _ = caricato
    primo = righe(conn, "SELECT id FROM import_file WHERE conto_id = ?", conti["tr"])[0]["id"]
    # secondo file: le stesse righe piu' una nuova
    extra = ('"2026-10-31T10:00:00.000Z","2026-10-31","DEFAULT","CASH","CARD_TRANSACTION","","LIDL MILANO","","","",'
             '"-5.000000","","","EUR","","","","TR Card Transaction","t10","","","","5411"\n').encode()
    rep = importa.importa(conn, conti["tr"], "tr2.csv", sintetici.tr_csv() + extra)
    assert (rep["nuove"], rep["duplicate"]) == (1, 9)
    esito = importa.annulla_import(conn, primo)
    assert (esito["tolti"], esito["mantenuti"]) == (0, 9)
    assert len(righe(conn, "SELECT id FROM movimenti WHERE conto_id = ?", conti["tr"])) == 10
    secondo = righe(conn, "SELECT id FROM import_file WHERE conto_id = ?", conti["tr"])[0]["id"]
    assert {r["import_id"] for r in righe(conn, "SELECT import_id FROM movimenti WHERE conto_id = ?", conti["tr"])} == {secondo}


def test_annulla_import_elimina_conto(caricato):
    conn, conti, _ = caricato
    imp_carta = righe(conn, "SELECT id FROM import_file WHERE conto_id = ?", conti["carta"])[0]["id"]
    esito = importa.annulla_import(conn, imp_carta, elimina_conto=True)
    ricalcola(conn)
    assert esito["conto_eliminato"] == "Carta ING"
    assert righe(conn, "SELECT * FROM conti WHERE id = ?", conti["carta"]) == []
    assert righe(conn, "SELECT * FROM contenitori WHERE stato = 'dettagliato'") == []


def test_celle_vuote_pandas():
    """pandas 3 rappresenta i testi mancanti come NaN: l'interfaccia non deve trattarli come stringhe."""
    import pandas as pd
    from spese.ui.comune import testo
    from spese.ui.pagine_dati import _cat_da_proposta
    df = pd.DataFrame([{"proposta": "ALIMENTARI · Supermercato (marchio Esselunga)"}, {"proposta": None},
                       {"proposta": "Supermercato (marchio Esselunga)"}])            # formato delle versioni precedenti
    assert _cat_da_proposta(df.iloc[0]["proposta"]) == ("ALIMENTARI", "Supermercato")
    assert _cat_da_proposta(df.iloc[1]["proposta"]) is None
    assert _cat_da_proposta(df.iloc[2]["proposta"]) == ("ALIMENTARI", "Supermercato")
    assert testo(float("nan")) == "" and testo(None) == "" and testo("  IT60 ") == "IT60"


def test_direttamente_claude_a_lotti(caricato):
    """Senza modello locale: tutte le voci vanno in coda, si inviano in piu' lotti e vengono salvate."""
    import json as js
    from types import SimpleNamespace
    conn, conti, _ = caricato
    da_fare = {v["chiave"] for v in llm.chiavi_da_classificare(conn)}
    llm.prepara_candidati(conn)
    coda = {r["chiave"]: r["stato"] for r in righe(conn, "SELECT chiave, stato FROM candidati_invio")}
    assert set(coda) == da_fare                       # nessun passaggio dal modello locale
    conn.execute("UPDATE candidati_invio SET stato = 'approvato' WHERE stato = 'in_attesa'")
    approvate = [k for k, s in coda.items() if s == "approvato"]
    lotti = llm.lotti_claude(conn, dimensione=1)
    assert len(lotti) == len(approvate) >= 2

    inviati = []

    def crea(**kw):
        items = js.loads(kw["messages"][0]["content"].split("Esercenti da classificare:\n")[1])
        inviati.append(kw["messages"][0]["content"])
        out = {"risultati": [{"idx": it["idx"], "l2": "Varie", "esercente_normalizzato": it["esercente"].split()[0],
                              "confidenza": 0.9} for it in items]}
        assert "tool_choice" not in kw and kw["output_config"]["format"]["type"] == "json_schema"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=js.dumps(out))], stop_reason="end_turn")

    client = SimpleNamespace(messages=SimpleNamespace(create=crea))
    passi = []
    pid, ris = llm.invia_claude(conn, dimensione=1, avanzamento=lambda f, t: passi.append((f, t)), client=client,
                                modello="claude-opus-5-5")
    assert inviati == [l[2] for l in lotti]          # inviato esattamente cio' che mostrava l'anteprima
    assert set(ris) == set(approvate) and passi[-1] == (len(lotti), len(lotti))
    assert not righe(conn, "SELECT * FROM cache_esercenti")          # risposte in anteprima
    llm.applica_prova(conn, pid)
    assert {(r["motore"], r["modello"]) for r in righe(conn, "SELECT motore, modello FROM cache_esercenti")} == \
        {("claude", "claude-opus-5-5")}
    assert all(r["stato"] == "inviato" for r in righe(conn, "SELECT stato FROM candidati_invio WHERE chiave IN (%s)"
                                                       % ",".join("?" * len(approvate)), *approvate))
    ricalcola(conn)
    assert llm.chiavi_da_classificare(conn) == [] or all(
        v["chiave"] not in approvate for v in llm.chiavi_da_classificare(conn))


def test_titolari_aggiunti_dopo_e_blocco_claude(conn, tmp_path):
    from spese.db import scrivi_impostazione, leggi_impostazione
    scrivi_impostazione(conn, "titolari", [])
    credem = importa.crea_conto(conn, "Credem comune", "credem", "conto", "comune", "Credem")
    importa.importa(conn, credem, "LISTA_MOVIMENTI.csv", sintetici.credem_csv())
    ricalcola(conn)
    chiavi = {r["chiave"] for r in righe(conn, "SELECT chiave FROM movimenti WHERE tipo = 'pagamento_carta'")}
    assert any(k.startswith("MARIO ") for k in chiavi)              # senza titolari il nome resta
    llm.prepara_candidati(conn)
    conn.execute("UPDATE candidati_invio SET stato = 'approvato' WHERE stato = 'in_attesa'")
    assert llm.lotti_claude(conn) == []                              # e niente parte verso Claude
    # l'estratto ING riporta l'intestatario: viene aggiunto e i movimenti gia' importati si ripuliscono
    ing = importa.crea_conto(conn, "ING", "ing_conto", "conto", "personale", "ING")
    x = tmp_path / "ing.xlsx"
    sintetici.ing_xlsx(x)
    rep = importa.importa(conn, ing, "ing.xlsx", x.read_bytes())
    assert rep["titolare_aggiunto"] and leggi_impostazione(conn, "titolari") == ["Mario Rossi"]
    assert rep["nomi_ripuliti"] == 3
    chiavi = {r["chiave"] for r in righe(conn, "SELECT chiave FROM movimenti WHERE tipo = 'pagamento_carta'")}
    assert not any("MARIO" in k for k in chiavi) and "BOTTEGA PINCO PALLINO" in chiavi
    assert righe(conn, "SELECT * FROM candidati_invio WHERE chiave LIKE 'MARIO%'") == []


def test_rimetti_in_classificazione(caricato):
    import json as js
    from types import SimpleNamespace
    conn, conti, _ = caricato
    llm.prepara_candidati(conn)
    conn.execute("UPDATE candidati_invio SET stato = 'approvato' WHERE stato = 'in_attesa'")

    def crea(**kw):
        items = js.loads(kw["messages"][0]["content"].split("Esercenti da classificare:\n")[1])
        out = {"risultati": [{"idx": it["idx"], "l2": "Condominio", "esercente_normalizzato": it["esercente"].split()[0],
                              "confidenza": 0.95} for it in items]}
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=js.dumps(out))], stop_reason="end_turn")

    pid, _ = llm.invia_claude(conn, client=SimpleNamespace(messages=SimpleNamespace(create=crea)))
    llm.applica_prova(conn, pid)
    ricalcola(conn)
    inviate = [r["chiave"] for r in righe(conn, "SELECT chiave FROM candidati_invio WHERE stato = 'inviato'")]
    assert inviate and llm.chiavi_da_classificare(conn) == []
    llm.prepara_candidati(conn)                                   # gia' inviate: non tornano in coda da sole
    assert not righe(conn, "SELECT * FROM candidati_invio WHERE stato = 'in_attesa'")
    servizi.conferma_esercente(conn, "LIDL MILANO", "Supermercato")
    elenco = {r["chiave"]: r for r in servizi.esercenti_classificati(conn)}
    assert elenco[inviate[0]]["motore"] == "claude" and elenco[inviate[0]]["movimenti"] >= 1

    assert servizi.riapri_esercenti(conn, inviate + ["LIDL MILANO"]) == len(inviate) + 1
    ricalcola(conn)
    da_fare = {v["chiave"] for v in llm.chiavi_da_classificare(conn)}
    assert set(inviate) <= da_fare
    assert righe(conn, "SELECT * FROM cache_esercenti WHERE chiave IN (%s)" % ",".join("?" * len(inviate)), *inviate) == []
    llm.prepara_candidati(conn)                                   # ora rientrano in coda
    coda = {r["chiave"]: r["stato"] for r in righe(conn, "SELECT chiave, stato FROM candidati_invio")}
    assert all(coda.get(k) == "approvato" for k in inviate)
    # LIDL resta classificato dalle regole: la conferma tolta non lo rende "da classificare"
    lidl = [a for a in analisi_di(conn, conti["tr"], tipo="pagamento_carta") if a["esercente"] == "LIDL MILANO"][0]
    assert lidl["l2"] == "Supermercato" and lidl["fonte"] != "utente"


# --- vista cumulativa --------------------------------------------------------------
def test_vista_cumulativa(caricato):
    from spese import analisi as an
    conn, conti, _ = caricato
    pers = an.flussi_mensili(conn, an.Filtri("personale"))
    com = an.flussi_mensili(conn, an.Filtri("comune"))
    tot = an.flussi_mensili(conn, an.Filtri("tutti"))
    somma = lambda df, col: int(df[col].sum()) if not df.empty else 0
    contributo = 50000                                   # ING (personale) -> Credem (comune), abbinato
    assert somma(tot, "uscite") == somma(pers, "uscite") + somma(com, "uscite") - contributo
    assert somma(tot, "entrate") == somma(pers, "entrate") + somma(com, "entrate") - contributo
    rip = an.ripartizione(conn, an.Filtri("tutti"))
    assert "CONTO COMUNE" not in set(rip["voce"])
    # con un solo conto selezionato il contributo resta: l'altro lato non e' nella vista
    solo_ing = an.flussi_mensili(conn, an.Filtri("tutti", conti=[conti["ing"]]))
    assert somma(solo_ing, "uscite") == somma(an.flussi_mensili(conn, an.Filtri("personale", conti=[conti["ing"]])), "uscite")
    assert an.periodo_disponibile(conn)[0] <= an.periodo_disponibile(conn, [conti["ing"]])[0]


# --- tassonomia modificabile ----------------------------------------------------------
def test_tassonomia_modificabile(caricato):
    from spese import configurazione as cfg
    from spese import tassonomia_utente as tu
    conn, conti, _ = caricato
    righe0 = tu.righe(conn)
    assert any(r["l2"] == "Supermercato" and r["predefinita"] for r in righe0)
    # aggiunta di categoria e sottocategoria nuove
    nuove = [dict(r) for r in righe0] + [{"sezione": "uscite", "l1": "Svago e cultura", "l2": "Lidi e impianti", "originale": ""}]
    assert tu.salva(conn, nuove) == ([], [])
    assert cfg.tassonomia().risolvi("Lidi e impianti") == ("SVAGO E CULTURA", "Lidi e impianti")
    assert "SVAGO E CULTURA · Lidi e impianti" in \
        llm.schema_risposta()["properties"]["risultati"]["items"]["properties"]["categoria"]["enum"]
    # uso e rinomina propagata
    servizi.conferma_esercente(conn, "MISTERO SNC", "Lidi e impianti")
    servizi.crea_regola(conn, "BAGNO", "Lidi e impianti", "chiave", "contiene")
    righe1 = tu.righe(conn)
    assert next(r for r in righe1 if r["l2"] == "Lidi e impianti")["in_uso"] == 2
    for r in righe1:
        if r["l2"] == "Lidi e impianti":
            r["l2"] = "Stabilimenti e impianti"
    assert tu.salva(conn, righe1)[0] == []
    assert righe(conn, "SELECT l2 FROM cache_esercenti WHERE chiave = 'MISTERO SNC'")[0]["l2"] == "Stabilimenti e impianti"
    assert righe(conn, "SELECT l2 FROM regole_utente WHERE pattern = 'BAGNO'")[0]["l2"] == "Stabilimenti e impianti"
    ricalcola(conn)
    mist = [a for a in analisi_di(conn, conti["tr"], tipo="pagamento_carta") if a["esercente"] == "MISTERO SNC"][0]
    assert (mist["l1"], mist["l2"]) == ("SVAGO E CULTURA", "Stabilimenti e impianti")
    # le predefinite si spostano ma non si rinominano ne' si eliminano
    righe2 = tu.righe(conn)
    sposta = [dict(r, l1="ACQUISTI") if r["l2"] == "Supermercato" else r for r in righe2]
    assert tu.salva(conn, sposta)[0] == [] and cfg.tassonomia().risolvi("Supermercato") == ("ACQUISTI", "Supermercato")
    rinomina = [dict(r, l2="Spesa") if r["l2"] == "Supermercato" else r for r in tu.righe(conn)]
    assert "predefinita" in tu.salva(conn, rinomina)[0][0]
    senza = [r for r in tu.righe(conn) if r["l2"] != "Supermercato"]
    assert "non si puo' eliminare" in tu.salva(conn, senza)[0][0]
    # una sottocategoria in uso non si elimina, si unisce
    senza_mia = [r for r in tu.righe(conn) if r["l2"] != "Stabilimenti e impianti"]
    assert "uniscila" in tu.salva(conn, senza_mia)[0][0]
    doppia = tu.righe(conn) + [{"sezione": "uscite", "l1": "ACQUISTI", "l2": "Varie", "originale": ""}]
    assert "due volte" in tu.salva(conn, doppia)[0][0]                 # due volte nella stessa categoria
    with pytest.raises(ValueError):
        tu.ripristina(conn)                                   # c'e' ancora una sottocategoria mia in uso
    tu.unisci(conn, "Stabilimenti e impianti", "Sport e palestra")
    assert righe(conn, "SELECT l2 FROM cache_esercenti WHERE chiave = 'MISTERO SNC'")[0]["l2"] == "Sport e palestra"
    assert "Stabilimenti e impianti" not in cfg.tassonomia().l1_per_l2
    tu.ripristina(conn)
    assert cfg.tassonomia().risolvi("Supermercato") == ("ALIMENTARI", "Supermercato")
    # la tassonomia salvata torna attiva al riaccesso
    tu.salva(conn, tu.righe(conn) + [{"sezione": "entrate", "l1": "ENTRATE", "l2": "Affitti percepiti", "originale": ""}])
    cfg.imposta_tassonomia(None)
    assert "Affitti percepiti" not in cfg.tassonomia().l1_per_l2
    tu.carica(conn)
    assert cfg.tassonomia().sezione_di[("ENTRATE", "Affitti percepiti")] == "entrate"


def test_modifica_esercente_classificato(caricato):
    conn, conti, _ = caricato
    servizi.conferma_esercente(conn, "MISTERO SNC", "Varie")
    servizi.aggiorna_esercente(conn, "MISTERO SNC", "Hobby", nome="Mistero")
    r = righe(conn, "SELECT * FROM cache_esercenti WHERE chiave = 'MISTERO SNC'")[0]
    assert (r["l2"], r["motore"], r["esercente_normalizzato"]) == ("Hobby", "utente", "Mistero")
    servizi.aggiorna_esercente(conn, "MISTERO SNC", nome="Mistero Snc")
    assert righe(conn, "SELECT l2 FROM cache_esercenti WHERE chiave = 'MISTERO SNC'")[0]["l2"] == "Hobby"


def test_tassonomia_celle_vuote(caricato):
    """Righe aggiunte e lasciate (in parte) vuote: NaN non deve diventare la parola 'nan'."""
    from spese import configurazione as cfg
    from spese import tassonomia_utente as tu
    conn, _, _ = caricato
    nan = float("nan")
    righe0 = tu.righe(conn)
    vuote = righe0 + [{"sezione": nan, "l1": nan, "l2": nan, "originale": nan},
                      {"sezione": None, "l1": None, "l2": None, "originale": None}]
    assert tu.salva(conn, vuote) == ([], [])
    mezza = righe0 + [{"sezione": "uscite", "l1": nan, "l2": nan, "originale": nan}]
    errori, _ = tu.salva(conn, mezza)
    assert errori and "servono sia la categoria sia la sottocategoria" in errori[0]
    assert not any(x.lower() == "nan" for x in cfg.tassonomia().l1_per_l2)
    assert "NAN" not in cfg.tassonomia().ordine_l1


def test_versamenti_sul_conto_comune(caricato):
    """Bonifici in entrata sul conto comune da un titolare (conto di partenza non caricato) o da un familiare:
    sono entrate del conto comune (Contributo ricevuto), non giroconti da chiarire."""
    from spese import analisi as an
    from spese.db import scrivi_impostazione
    conn, conti, _ = caricato
    extra = ("06/10/2026;06/10/2026;300,00;300,00;EUR;AUT;-;48;OCIST;BON ISTANTANEO A CREDITO ROSSI MARIO "
             "Rif. QQQ111222333 settembre;-\r\n"
             "07/10/2026;07/10/2026;250,00;250,00;EUR;AUT;-;48;OCIST;BON ISTANTANEO A CREDITO BIANCHI ANNA "
             "Rif. QQQ444555666 quota;-\r\n").encode()
    prima = an.flussi_mensili(conn, an.Filtri("comune"))["entrate"].sum()
    importa.importa(conn, conti["credem"], "LISTA_MOVIMENTI_2.csv", sintetici.credem_csv() + extra)
    ricalcola(conn)
    entrate = {a["importo_cent"]: a for a in analisi_di(conn, conti["credem"], tipo="bonifico") if a["importo_cent"] > 0}
    mio = entrate[30000]
    assert (mio["natura"], mio["l2"], mio["da_rivedere"]) == ("entrata", "Contributo ricevuto", 0)
    assert entrate[25000]["natura"] == "entrata" and entrate[25000]["l2"] is None      # ancora "altra persona"
    # la compagna indicata tra i familiari: anche il suo versamento e' un contributo
    scrivi_impostazione(conn, "familiari", ["Anna Bianchi"])
    ricalcola(conn)
    sua = [a for a in analisi_di(conn, conti["credem"], tipo="bonifico") if a["importo_cent"] == 25000][0]
    assert (sua["natura"], sua["l2"], sua["da_rivedere"]) == ("entrata", "Contributo ricevuto", 0)
    assert an.flussi_mensili(conn, an.Filtri("comune"))["entrate"].sum() == prima + 55000
    # il nome del familiare e' protetto come quello di un titolare
    assert "nome o parola sensibile" in controlla(conn, "PIZZERIA DA ANNA")
    # sui conti personali un bonifico a proprio nome senza contropartita resta una domanda
    tr6 = analisi_di(conn, conti["tr"], tipo="bonifico")[0]
    assert tr6["natura"] == "trasferimento_interno" and tr6["da_rivedere"] == 1
    # nella vista cumulativa i versamenti non abbinati contano come entrate (arrivano da fuori dai conti caricati)
    assert an.flussi_mensili(conn, an.Filtri("tutti"))["entrate"].sum() >= 55000


# --- IBAN -----------------------------------------------------------------------------
def test_iban_normalizzato_e_controllato(caricato):
    from spese.iban import controlla as controlla_iban
    conn, conti, _ = caricato
    assert controlla_iban("it60 x054 2811 1010 0000 0123 456") == ("IT60X0542811101000000123456", None)
    assert controlla_iban(" DE89 3704 0044 0532 0130 00 ") == ("DE89370400440532013000", None)
    assert controlla_iban("") == (None, None) and controlla_iban(float("nan")) == (None, None)
    assert "cifra di controllo" in controlla_iban("IT60X0542811101000000123457")[1]       # ultima cifra sbagliata
    assert "27 caratteri" in controlla_iban("IT60X054281110100000012345")[1]             # un carattere in meno
    assert "codice del paese" in controlla_iban("6060X0542811101000000123456")[1]
    assert "non ammessi" in controlla_iban("IT60X05428111010000001234.6")[1]
    # salvataggio: spazi tolti, IBAN errato rifiutato senza scrivere nulla
    servizi.aggiorna_conto(conn, conti["credem"], iban="IT60 X054 2811 1010 0000 0123 456")
    assert righe(conn, "SELECT iban FROM conti WHERE id = ?", conti["credem"])[0]["iban"] == "IT60X0542811101000000123456"
    with pytest.raises(ValueError):
        servizi.aggiorna_conto(conn, conti["credem"], iban="IT60X0542811101000000123457", nome="Non deve cambiare")
    r = righe(conn, "SELECT iban, nome FROM conti WHERE id = ?", conti["credem"])[0]
    assert (r["iban"], r["nome"]) == ("IT60X0542811101000000123456", "Credem comune")
    # creazione: anche i conti nuovi si salvano senza spazi
    nuovo = importa.crea_conto(conn, "Deposito", None, "deposito", "personale", iban="de89 3704 0044 0532 0130 00", importato=0)
    assert righe(conn, "SELECT iban FROM conti WHERE id = ?", nuovo)[0]["iban"] == "DE89370400440532013000"


def test_iban_con_spazi_gia_salvati_ripuliti(tmp_path):
    from spese import db
    p = tmp_path / "v.db"
    c = db.apri(p, "passphrase")
    c.execute("INSERT INTO conti(nome, tipo, perimetro, iban) VALUES ('Vecchio', 'conto', 'personale', 'it60 x054 2811 1010 0000 0123 456')")
    c.close()
    c = db.apri(p, "passphrase")
    assert c.execute("SELECT iban FROM conti").fetchone()[0] == "IT60X0542811101000000123456"


def test_iban_gia_salvato_non_valido_non_blocca(caricato):
    conn, conti, _ = caricato
    conn.execute("UPDATE conti SET iban = 'IT60X054281110100000012345' WHERE id = ?", (conti["ing"],))   # 26 caratteri
    servizi.aggiorna_conto(conn, conti["ing"], iban="IT60X054281110100000012345", nome="ING personale")
    assert righe(conn, "SELECT nome FROM conti WHERE id = ?", conti["ing"])[0]["nome"] == "ING personale"
    with pytest.raises(ValueError):
        servizi.aggiorna_conto(conn, conti["ing"], iban="IT60X054281110100000012346")


# --- spese pagate tramite un familiare -------------------------------------------------
def _bonifico_familiare(conn, conti):
    from spese.db import scrivi_impostazione
    scrivi_impostazione(conn, "familiari", ["Giulia Bianchi"])
    ricalcola(conn)
    r = [a for a in analisi_di(conn, conti["credem"], tipo="bonifico") if a["importo_cent"] == -8000]
    return r[0] if r else righe(conn, "SELECT * FROM analisi a JOIN movimenti m ON m.id = a.mov_id "
                                     "WHERE a.conto_id = ? AND m.importo_cent = -8000", conti["credem"])


def test_bonifico_a_familiare_e_spesa(caricato):
    conn, conti, _ = caricato
    prima = analisi_di(conn, conti["credem"], tipo="bonifico")
    assert [a for a in prima if a["importo_cent"] == -8000][0]["natura"] == "uscita"      # terzi, gia' uscita
    a = _bonifico_familiare(conn, conti)
    assert (a["natura"], a["l2"], a["da_rivedere"]) == ("uscita", None, 1)
    t = righe(conn, "SELECT stato FROM trasferimenti WHERE mov_id = ?", a["mov_id"])[0]
    assert t["stato"] == "spesa_familiare"
    assert [r["mov_id"] for r in servizi.spese_tramite_familiari(conn)] == [a["mov_id"]]
    # categoria decisa sul movimento
    servizi.decidi_movimento(conn, a["mov_id"], "Auto: noleggio/rata")
    ricalcola(conn)
    a = analisi_di(conn, conti["credem"], tipo="bonifico", id=a["mov_id"])[0]
    assert (a["natura"], a["l1"], a["l2"], a["da_rivedere"]) == ("uscita", "TRASPORTI", "Auto: noleggio/rata", 0)


def test_regola_sulla_causale(caricato):
    conn, conti, _ = caricato
    a = _bonifico_familiare(conn, conti)
    assert servizi.conta_regola(conn, "cena") == 1
    servizi.crea_regola(conn, "cena", "Ristoranti", "descrizione", "contiene", priorita=20)
    ricalcola(conn)
    a = analisi_di(conn, conti["credem"], tipo="bonifico", id=a["mov_id"])[0]
    assert (a["l2"], a["fonte"], a["da_rivedere"]) == ("Ristoranti", "regola utente", 0)


def test_restituzione_di_contributo(caricato):
    from spese import analisi as an
    conn, conti, _ = caricato
    a = _bonifico_familiare(conn, conti)
    entrate = an.flussi_mensili(conn, an.Filtri("comune"))["entrate"].sum()
    uscite = an.flussi_mensili(conn, an.Filtri("comune"))["uscite"].sum()
    servizi.decidi_restituzione(conn, a["mov_id"])
    ricalcola(conn)
    a = analisi_di(conn, conti["credem"], tipo="bonifico", id=a["mov_id"])[0]
    assert (a["natura"], a["l2"], a["importo_cent"]) == ("entrata", "Contributo ricevuto", -8000)
    f = an.flussi_mensili(conn, an.Filtri("comune"))
    assert f["entrate"].sum() == entrate - 8000 and f["uscite"].sum() == uscite - 8000


def test_suddivisione_di_un_bonifico(caricato):
    from spese import analisi as an
    conn, conti, _ = caricato
    a = _bonifico_familiare(conn, conti)
    mid = a["mov_id"]
    with pytest.raises(ValueError, match="sommano"):
        servizi.salva_ripartizione(conn, mid, [(-5000, "Auto: noleggio/rata", "rata"), (-2000, "Ristoranti", "cena")])
    with pytest.raises(ValueError, match="segno"):
        servizi.salva_ripartizione(conn, mid, [(-9000, "Auto: noleggio/rata", "rata"), (1000, "Ristoranti", None)])
    with pytest.raises(ValueError, match="almeno due"):
        servizi.salva_ripartizione(conn, mid, [(-8000, "Auto: noleggio/rata", "rata")])
    with pytest.raises(ValueError, match="sottocategoria"):
        servizi.salva_ripartizione(conn, mid, [(-5000, "Inesistente", None), (-3000, "Ristoranti", None)])
    servizi.salva_ripartizione(conn, mid, [(-5000, "Auto: noleggio/rata", "rata ottobre"),
                                           (-2000, "Ristoranti", "cena"), (-1000, "Contributo ricevuto", "restituzione")])
    ricalcola(conn)
    parti = righe(conn, "SELECT componente, natura, l2, importo_cent, da_rivedere FROM analisi WHERE mov_id = ? ORDER BY importo_cent",
                  mid)
    assert [(p["componente"], p["natura"], p["l2"], p["importo_cent"]) for p in parti] == [
        ("parte", "uscita", "Auto: noleggio/rata", -5000), ("parte", "uscita", "Ristoranti", -2000),
        ("parte", "entrata", "Contributo ricevuto", -1000)]
    assert all(p["da_rivedere"] == 0 for p in parti)
    assert [r["causale"] for r in servizi.ripartizione(conn, mid)] == ["rata ottobre", "cena", "restituzione"]
    rip = dict(zip(*[an.ripartizione(conn, an.Filtri("comune"), livello="l2")[c] for c in ("voce", "importo")]))
    assert rip["Auto: noleggio/rata"] == 5000
    # la rinomina di una sottocategoria vale anche per le parti
    from spese import tassonomia_utente as tu
    tu.salva(conn, tu.righe(conn) + [{"sezione": "uscite", "l1": "TRASPORTI", "l2": "Bollo auto", "originale": ""}])
    servizi.salva_ripartizione(conn, mid, [(-6000, "Bollo auto", None), (-2000, "Ristoranti", None)])
    tu.unisci(conn, "Bollo auto", "Auto: assicurazione e bollo")
    assert servizi.ripartizione(conn, mid)[0]["l2"] == "Auto: assicurazione e bollo"
    servizi.elimina_ripartizione(conn, mid)
    ricalcola(conn)
    assert righe(conn, "SELECT componente FROM analisi WHERE mov_id = ?", mid) == [{"componente": "principale"}]


def test_causale_dei_bonifici():
    from spese.servizi import causale_bonifico as cb
    assert cb("BON. ISTANTANEO A DEBITO Anna Bianchi Rif. 0000100000000000000000000000001-008 telepass *", "Anna Bianchi") == "telepass"
    assert cb("BONIFICO A DEBITO A FAVORE DI Anna Bianchi rata auto RIF=0000000000000000000000000002IT", "Anna Bianchi") == "rata auto"
    assert cb("BON. ISTANTANEO A DEBITO Anna Bianchi Rif. 00001000000000000000000000003 00-008 rata auto + tari",
              "Anna Bianchi") == "rata auto + tari"
    assert cb("BON. ISTANTANEO A DEBITO Anna Bianchi Rif. 0001", "Anna Bianchi") == ""


def test_stesso_esercente_categorie_diverse(caricato):
    """Una scelta sul singolo movimento prevale sulla categoria dell'esercente."""
    conn, conti, _ = caricato
    movs = [a for a in analisi_di(conn, conti["credem"], tipo="pagamento_carta") if "PINCO" in a["esercente"]]
    assert len(movs) == 2
    servizi.conferma_esercente(conn, movs[0]["chiave"], "Negozi alimentari")
    servizi.decidi_movimento(conn, movs[1]["mov_id"], "Regali e donazioni")
    ricalcola(conn)
    dopo = {a["mov_id"]: (a["l2"], a["fonte"]) for a in analisi_di(conn, conti["credem"], tipo="pagamento_carta")
            if "PINCO" in a["esercente"]}
    assert dopo[movs[0]["mov_id"]][0] == "Negozi alimentari"
    assert dopo[movs[1]["mov_id"]] == ("Regali e donazioni", "utente")
    # tolta l'eccezione, il movimento torna alla categoria dell'esercente
    servizi.annulla_decisione(conn, movs[1]["mov_id"])
    ricalcola(conn)
    assert analisi_di(conn, conti["credem"], tipo="pagamento_carta", id=movs[1]["mov_id"])[0]["l2"] == "Negozi alimentari"


# --- ING: export CSV con colonne uscite ed entrate -----------------------------------------
def test_ing_csv_uscite_entrate(conn):
    from spese.lettore import leggi
    fl = leggi("movimenti.csv", [], sintetici.ing_csv())
    assert fl.profilo["id"] == "ing_conto_csv"
    assert [m["importo_cent"] for m in fl.movimenti] == [-1000, -10000, -50000, -100000, 250000]
    assert [m["tipo"] for m in fl.movimenti] == ["pagamento_carta", "addebito_carta_credito", "bonifico", "giroconto", "stipendio"]
    assert fl.movimenti[0]["esercente"] == "Telecom Italia S.p.A" and fl.movimenti[0]["data"] == "2026-10-01"
    assert fl.metadati["saldo_iniziale"] == "+1000,00" and fl.metadati["saldo_finale"] == "+1890,00"
    assert not any("Saldo" in (m["descrizione"] or "") for m in fl.movimenti)          # i saldi non sono movimenti
    ing = importa.crea_conto(conn, "ING", "ing_conto_csv", "conto", "personale", "ING")
    rep = importa.importa(conn, ing, "movimenti.csv", sintetici.ing_csv())
    assert rep["nuove"] == 5 and rep["quadratura"] == "OK"


def test_ing_csv_e_excel_senza_doppioni(caricato, tmp_path):
    """Lo stesso periodo esportato in Excel e in CSV: il secondo file non aggiunge nulla e viene proposto
    sullo stesso conto ING."""
    conn, conti, _ = caricato
    fl, suggerito, _ = importa.anteprima(conn, "movimenti.csv", sintetici.ing_csv())
    assert suggerito == conti["ing"]
    rep = importa.importa(conn, conti["ing"], "movimenti.csv", sintetici.ing_csv())
    assert (rep["nuove"], rep["duplicate"]) == (0, 5)


def test_csv_importo_in_entrambe_le_colonne(conn):
    from spese.lettore import leggi
    dati = ("DATA CONTABILE;DATA VALUTA;USCITE;ENTRATE;CAUSALE;DESCRIZIONE OPERAZIONE\r\n"
            "02/10/2026;02/10/2026;-5,00;+5,00;Giroconto;strano\r\n"
            "03/10/2026;03/10/2026;40,00;;Giroconto;uscita senza segno\r\n").encode()
    fl = leggi("x.csv", [], dati)
    assert [n for n, _ in fl.scartate] == [2]
    assert [m["importo_cent"] for m in fl.movimenti] == [-4000]                     # uscita sempre negativa


def test_esporta_tassonomia_yaml(caricato):
    import yaml
    from spese import configurazione as cfg
    from spese import tassonomia_utente as tu
    conn, _, _ = caricato
    tu.salva(conn, tu.righe(conn) + [{"sezione": "uscite", "l1": "CASA", "l2": "Box auto", "originale": ""}])
    testo = tu.esporta_yaml(conn)
    assert testo.startswith("# Tassonomia esportata")
    d = yaml.safe_load(testo)
    assert list(d) == ["versione", "uscite", "entrate", "risparmio", "solo_locale"]
    assert d["uscite"]["CASA"][-1] == "Box auto" and "Auto: noleggio/rata" in d["uscite"]["TRASPORTI"]
    # riletta come file di partenza da' la stessa tassonomia
    cfg.imposta_tassonomia(d)
    assert cfg.tassonomia().risolvi("Box auto") == ("CASA", "Box auto")


# --- natura della spesa ed etichette (funzioni in prova) -----------------------------------
def test_natura_della_spesa(caricato):
    from spese import attributi as attr
    conn, conti, _ = caricato
    movs = [a for a in analisi_di(conn, conti["credem"], tipo="pagamento_carta") if "ESSELUNGA" in (a["esercente"] or "")]
    assert movs and movs[0]["l2"] == "Supermercato" and movs[0]["attributo"] == "necessaria"
    # le entrate non hanno natura della spesa
    assert all(r["attributo"] is None for r in righe(conn, "SELECT attributo FROM analisi WHERE natura = 'entrata'"))
    # cambio della sottocategoria: vale per tutti i suoi movimenti
    attr.imposta_predefinito(conn, "Supermercato", "superflua")
    ricalcola(conn)
    assert analisi_di(conn, conti["credem"], id=movs[0]["mov_id"])[0]["attributo"] == "superflua"
    # scelta sul singolo movimento: prevale, e togliendola si torna alla sottocategoria
    attr.imposta_movimento(conn, [movs[0]["mov_id"]], "svago")
    ricalcola(conn)
    assert analisi_di(conn, conti["credem"], id=movs[0]["mov_id"])[0]["attributo"] == "svago"
    attr.imposta_predefinito(conn, "Supermercato", "")         # nessun valore per la sottocategoria
    attr.imposta_movimento(conn, [movs[0]["mov_id"]], None)
    ricalcola(conn)
    assert analisi_di(conn, conti["credem"], id=movs[0]["mov_id"])[0]["attributo"] is None
    with pytest.raises(ValueError):
        attr.imposta_movimento(conn, [movs[0]["mov_id"]], "inventata")
    from spese.analisi import Filtri, per_attributo
    df = per_attributo(conn, Filtri("tutti"))
    assert set(df["attributo"]) <= {"", "necessaria", "svago", "superflua"}


def test_etichette(caricato):
    from spese import attributi as attr
    from spese.analisi import Filtri, etichetta_categorie, per_etichetta
    conn, conti, _ = caricato
    eid = attr.crea_etichetta(conn, "  Vacanza   Puglia ")
    assert attr.crea_etichetta(conn, "vacanza puglia") == eid           # stesso nome, maiuscole a parte
    usc = [r["mov_id"] for r in righe(conn, "SELECT DISTINCT mov_id FROM analisi WHERE natura = 'uscita' LIMIT 3")]
    attr.assegna(conn, usc, eid)
    attr.assegna(conn, usc[:1], eid)                                    # nessun doppione
    assert attr.etichette(conn)[0]["movimenti"] == 3
    tot = -sum(r["s"] for r in [dict(x) for x in conn.execute(
        "SELECT sum(importo_cent) AS s FROM analisi WHERE natura IN ('uscita','rimborso') AND mov_id IN (%s)"
        % ",".join("?" * 3), usc)])
    df = per_etichetta(conn, Filtri("tutti"))
    assert df.iloc[0]["nome"] == "Vacanza Puglia" and df.iloc[0]["uscite"] == tot
    assert etichetta_categorie(conn, Filtri("tutti"), eid)["importo"].sum() == tot
    # le etichette non toccano il ricalcolo
    ricalcola(conn)
    assert attr.etichette(conn)[0]["movimenti"] == 3
    attr.togli(conn, usc[:1], eid)
    assert attr.etichette(conn)[0]["movimenti"] == 2
    altra = attr.crea_etichetta(conn, "Casa")
    with pytest.raises(ValueError):
        attr.rinomina_etichetta(conn, altra, "VACANZA PUGLIA")
    attr.elimina_etichetta(conn, eid)
    assert [e["nome"] for e in attr.etichette(conn)] == ["Casa"]
    assert conn.execute("SELECT count(*) FROM etichette_movimento").fetchone()[0] == 0


def test_etichette_per_periodo_e_annullamento_import(caricato):
    from spese import attributi as attr
    conn, conti, _ = caricato
    d0, d1 = conn.execute("SELECT min(data), max(data) FROM movimenti WHERE conto_id = ?", (conti["credem"],)).fetchone()
    ids = attr.movimenti_nel_periodo(conn, d0, d1, [conti["credem"]])
    assert ids and all(conn.execute("SELECT conto_id FROM movimenti WHERE id = ?", (i,)).fetchone()[0] == conti["credem"]
                       for i in ids)
    entrate = {r[0] for r in conn.execute("SELECT mov_id FROM analisi WHERE natura = 'entrata'")}
    assert not (set(ids) & entrate - {r[0] for r in conn.execute(
        "SELECT mov_id FROM analisi WHERE natura IN ('uscita','rimborso')")})
    eid = attr.crea_etichetta(conn, "Prova")
    attr.assegna(conn, ids, eid)
    attr.imposta_movimento(conn, ids[:1], "svago")
    imp_id = conn.execute("SELECT id FROM import_file WHERE conto_id = ?", (conti["credem"],)).fetchone()[0]
    importa.annulla_import(conn, imp_id)
    assert conn.execute("SELECT count(*) FROM etichette_movimento").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM attributi_movimento").fetchone()[0] == 0


def test_rinomina_sottocategoria_porta_la_natura(caricato):
    from spese import attributi as attr
    from spese import tassonomia_utente as tu
    conn, _, _ = caricato
    tu.salva(conn, tu.righe(conn) + [{"sezione": "uscite", "l1": "CASA", "l2": "Box auto", "originale": ""}])
    attr.imposta_predefinito(conn, "Box auto", "superflua")
    nuove = [dict(r, l2="Box e cantina") if r["l2"] == "Box auto" else r for r in tu.righe(conn)]
    assert tu.salva(conn, nuove)[0] == []
    pred = attr.predefiniti(conn)
    assert pred.get(("CASA", "Box e cantina")) == "superflua" and ("CASA", "Box auto") not in pred


def test_decisione_chiude_la_revisione(caricato):
    """Una tua decisione sul movimento spegne sempre 'da rivedere', anche quando lo chiedeva un trasferimento."""
    conn, _, _ = caricato
    r = righe(conn, "SELECT mov_id, motivo FROM analisi WHERE da_rivedere = 1 AND componente = 'principale' "
                    "AND motivo LIKE '%rasferimento%' LIMIT 1")
    assert r, "serve un trasferimento da rivedere nei dati sintetici"
    servizi.decidi_movimento(conn, r[0]["mov_id"], "Regali e donazioni", "uscita")
    ricalcola(conn)
    dopo = righe(conn, "SELECT da_rivedere, l2 FROM analisi WHERE mov_id = ? AND componente = 'principale'", r[0]["mov_id"])[0]
    assert dopo == {"da_rivedere": 0, "l2": "Regali e donazioni"}


def test_coda_si_pulisce_e_stima(caricato):
    """Le voci in coda classificate altrove escono dalla coda; la stima dice cosa entrerebbe prima di farlo."""
    conn, _, _ = caricato
    stima = llm.stima_coda(conn)
    assert len(stima["nuove"]) >= 2 and stima["in_coda"] == 0
    assert llm.prepara_candidati(conn) == len(stima["nuove"])
    assert llm.stima_coda(conn)["in_coda"] == len(stima["nuove"]) and not llm.stima_coda(conn)["nuove"]
    chiave = next(v["chiave"] for v, motivi in stima["nuove"] if not motivi)
    servizi.conferma_esercente(conn, chiave, "Regali e donazioni")     # classificata in Revisione
    ricalcola(conn)
    assert llm.pulisci_coda(conn) == 1
    assert not righe(conn, "SELECT * FROM candidati_invio WHERE chiave = ?", chiave)
    # anteprima sulle voci spuntate nell'interfaccia, con il testo modificato
    altra = next(v["chiave"] for v, motivi in stima["nuove"] if not motivi and v["chiave"] != chiave)
    lotti = llm.lotti_claude(conn, scelte=[{"chiave": altra, "testo": "NEGOZIO", "paese": None}])
    assert len(lotti) == 1 and lotti[0][0][0]["testo"] == "NEGOZIO"


def test_conto_registrato_a_mano_e_poi_importato(conn):
    """Un conto creato in Conti e copertura viene proposto all'import (per IBAN) e diventa importato."""
    cid = importa.crea_conto(conn, "Il mio ING", None, "conto", "personale", iban=sintetici.IBAN_ING, importato=0)
    assert importa.nome_conto_esistente(conn, "  il mio ing ") == cid
    dati = sintetici.ing_csv()
    _, suggerito, _ = importa.anteprima(conn, "ING_movimenti.csv", dati)
    assert suggerito == cid
    rep = importa.importa(conn, cid, "ING_movimenti.csv", dati)
    assert rep["nuove"] > 0
    c = dict(conn.execute("SELECT importato, profilo, banca FROM conti WHERE id = ?", (cid,)).fetchone())
    assert c["importato"] == 1 and c["profilo"] == "ing_conto_csv" and c["banca"]


def test_elimina_conto_con_movimenti(caricato):
    from spese import attributi as attr
    conn, conti, _ = caricato
    uso = importa.uso_conto(conn, conti["ing"])
    assert uso["movimenti"] > 0 and uso["file"] == 1 and uso["carte"] == ["Carta ING"]
    mid = conn.execute("SELECT id FROM movimenti WHERE conto_id = ? LIMIT 1", (conti["ing"],)).fetchone()[0]
    servizi.decidi_movimento(conn, mid, "Regali e donazioni")
    attr.assegna(conn, [mid], attr.crea_etichetta(conn, "Prova"))
    importa.elimina_conto(conn, conti["ing"])
    ricalcola(conn)
    for sql in ("SELECT count(*) FROM movimenti WHERE conto_id = ?", "SELECT count(*) FROM import_file WHERE conto_id = ?",
                "SELECT count(*) FROM conti WHERE id = ?", "SELECT count(*) FROM analisi WHERE conto_id = ?"):
        assert conn.execute(sql, (conti["ing"],)).fetchone()[0] == 0
    for tab in ("decisioni", "etichette_movimento", "righe_raw"):
        col = "mov_id"
        assert conn.execute("SELECT count(*) FROM %s WHERE %s = ?" % (tab, col), (mid,)).fetchone()[0] == 0
    assert conn.execute("SELECT conto_addebito_id FROM conti WHERE id = ?", (conti["carta"],)).fetchone()[0] is None
    assert conn.execute("SELECT count(*) FROM movimenti WHERE conto_id = ?", (conti["credem"],)).fetchone()[0] > 0


def test_risposta_di_claude_poco_sicura_resta_recuperabile(caricato):
    """Un esercente a cui Claude ha risposto con confidenza bassa resta senza categoria: non deve sparire dalla
    coda, ma comparire tra le voci da rimettere in coda."""
    conn, _, _ = caricato
    k = llm.chiavi_da_classificare(conn)[0]["chiave"]
    llm._salva(conn, {k: ("Regali e donazioni", 0.3, k)}, "claude")
    ricalcola(conn)
    assert k in {v["chiave"] for v in llm.chiavi_da_classificare(conn)}       # ancora da classificare
    stima = llm.stima_coda(conn)
    assert k in stima["senza_risposta"] and k not in {v["chiave"] for v, _ in stima["nuove"]}
    llm.rimetti_in_coda(conn, [k])
    assert righe(conn, "SELECT stato FROM candidati_invio WHERE chiave = ?", k)[0]["stato"] in ("approvato", "bloccato")


def test_sessione_ricordata_in_memoria(monkeypatch):
    from spese import sessioni
    sessioni.chiudi()
    t = sessioni.apri("segreta")
    assert sessioni.stato(t, 60) == "valida" and sessioni.passphrase() == "segreta"
    assert sessioni.stato("codice-inventato", 60) == "sconosciuta" and sessioni.stato(None, 60) == "sconosciuta"
    t2 = sessioni.apri("segreta")                               # seconda scheda o nuovo accesso
    assert sessioni.stato(t, 60) == sessioni.stato(t2, 60) == "valida"
    sessioni.chiudi()                                            # Esci: vale per tutte le schede
    assert sessioni.stato(t2, 60) == "sconosciuta" and sessioni.passphrase() is None
    t3 = sessioni.apri("segreta")
    import time as _t
    monkeypatch.setattr(sessioni.time, "time", lambda: _t.monotonic() * 0 + 10 ** 12)   # molto tempo dopo
    assert sessioni.stato(t3, 60) == "scaduta" and sessioni.passphrase() is None


def test_credit_agricole_csv(conn):
    """Lista movimenti Credit Agricole: causali con spazi, importi con apostrofo, tipo secondo il segno."""
    testo = ("Data Op.;Data Val.;Causale;Descrizione;Importo;Divisa\n"
             "02/11/2026;31/10/2026;PAGAMENTO TRAMITE POS              ;POS CARTA CA DEBIT VISA N. ****1111 DEL 31/10/26 "
             "ORE 17:52 C/O Google YouTube Membe London GBR;'-4,99;EUR\n"
             "03/08/2026;03/08/2026;GIROCONTO/BONIFICO                 ;ORD:TELEPASS S.P.A. DT.ORD:000000 DESCR.OPERAZIONE "
             "SCT:SALDO DOCUM.01111111 DEL 31.07.2026< *> RIFERIMENTO SCT:0000000000000324;1,50;EUR\n"
             "30/09/2026;30/09/2026;INTERESSI/COMPETENZE               ;COMPETENZE DI CHIUSURA;'-12,40;EUR\n"
             "30/09/2026;30/09/2026;INTERESSI/COMPETENZE               ;INTERESSI CREDITORI;0,03;EUR\n"
             "05/10/2026;05/10/2026;PRELIEVO NOSTRO SPORTELLO AUTOM.   ;PRELIEVO BANCOMAT;'-50,00;EUR\n"
             "06/10/2026;06/10/2026;STORNO MOVIMENTI                   ;STORNO POS DEL 01/10/26 ORE 10:00 C/O AMAZON EU SARL "
             "LUXEMBOURG LUX;20,00;EUR\n")
    from spese.lettore import leggi
    fl = leggi("Lista_Movimenti_CAI_20261105182958.csv", [], testo.encode("utf-8"))
    assert fl.profilo["id"] == "credit_agricole" and not fl.scartate
    pos, bon, comp, inter, prel, storno = fl.movimenti
    assert (pos["tipo"], pos["importo_cent"], pos["esercente"], pos["paese"], pos["data"], pos["ora"]) == \
        ("pagamento_carta", -499, "Google YouTube Membe London", "GBR", "2026-10-31", "17:52")
    assert bon["tipo"] == "bonifico" and bon["controparte_nome"] == "TELEPASS S.P.A." and bon["importo_cent"] == 150
    assert comp["tipo"] == "commissione" and inter["tipo"] == "interessi"
    assert prel["tipo"] == "prelievo" and prel["importo_cent"] == -5000
    assert storno["tipo"] == "pagamento_carta" and storno["importo_cent"] == 2000 and storno["esercente"].startswith("AMAZON")
    cid = importa.crea_conto(conn, "Credit Agricole", "credit_agricole", "conto", "personale", "Credit Agricole")
    rep = importa.importa(conn, cid, "Lista_Movimenti_CAI.csv", testo.encode("utf-8"))
    ricalcola(conn)
    assert rep["nuove"] == 6
    nat = {r["tipo"] + str(r["importo_cent"] > 0): r["natura"] for r in righe(
        conn, "SELECT m.tipo, m.importo_cent, a.natura FROM analisi a JOIN movimenti m ON m.id = a.mov_id "
              "WHERE a.conto_id = ? AND a.componente = 'principale'", cid)}
    assert nat["pagamento_cartaTrue"] == "rimborso" and nat["interessiTrue"] == "entrata"


def test_perimetro_composto(caricato):
    """Conti scelti a mano: si sommano solo quelli; un contributo si annulla solo se ci sono entrambi i lati."""
    from spese.analisi import Filtri, flussi_mensili
    conn, conti, _ = caricato
    tot = lambda f: int(flussi_mensili(conn, f)["uscite"].sum()) if not flussi_mensili(conn, f).empty else 0
    singoli = {k: tot(Filtri("composto", conti=[v])) for k, v in conti.items()}
    assert tot(Filtri("composto", conti=[conti["tr"], conti["carta"]])) == singoli["tr"] + singoli["carta"]
    assert tot(Filtri("personale", conti=[])) == tot(Filtri("composto", conti=[conti["ing"], conti["tr"], conti["carta"]]))
    assert flussi_mensili(conn, Filtri("nessuno")).empty
    contributi = conn.execute("SELECT count(*) FROM trasferimenti WHERE stato = 'contributo' "
                              "AND controparte_mov_id IS NOT NULL").fetchone()[0]
    tutti = [conti["ing"], conti["credem"], conti["tr"], conti["carta"]]
    assert tot(Filtri("composto", conti=tutti)) == tot(Filtri("tutti"))
    if contributi:
        assert tot(Filtri("composto", conti=tutti)) < sum(singoli.values())


# --- review 0.5.2: regressioni ----------------------------------------------------------
def _movimento(conn, mid, conto, data, importo, tipo="bonifico", **altri):
    campi = {"id": mid, "conto_id": conto, "data": data, "importo_cent": importo, "tipo": tipo, **altri}
    conn.execute("INSERT INTO movimenti(%s) VALUES (%s)" % (",".join(campi), ",".join("?" * len(campi))),
                 list(campi.values()))


def test_restituzione_dal_comune_al_personale(caricato):
    """Bonifico dal conto comune a un conto personale (abbinato): per il comune e' un contributo ricevuto
    negativo (restituzione), per il personale il rimborso di un contributo versato. Non un'entrata nuova."""
    from spese import analisi as an
    conn, conti, _ = caricato
    _movimento(conn, "T-COM-1", conti["credem"], "2026-10-29", -15000, controparte_iban=sintetici.IBAN_ING,
               descrizione="BONIFICO A FAVORE DI MARIO ROSSI restituzione")
    _movimento(conn, "T-ING-1", conti["ing"], "2026-10-29", 15000, controparte_iban=sintetici.IBAN_CREDEM,
               descrizione="BONIFICO DA CONTO COMUNE")
    ricalcola(conn)
    com = righe(conn, "SELECT natura, l2, importo_cent FROM analisi WHERE mov_id = 'T-COM-1'")[0]
    ing = righe(conn, "SELECT natura, l2, importo_cent FROM analisi WHERE mov_id = 'T-ING-1'")[0]
    assert (com["natura"], com["l2"], com["importo_cent"]) == ("entrata", "Contributo ricevuto", -15000)
    assert (ing["natura"], ing["l2"], ing["importo_cent"]) == ("rimborso", "Contributo versato", 15000)
    # nella vista cumulativa i due lati si annullano
    assert not righe(conn, "SELECT 1 FROM analisi a WHERE a.mov_id IN ('T-COM-1','T-ING-1') AND " +
                     an.Filtri("tutti").where()[0], *an.Filtri("tutti").where()[1])


def test_nomi_omonimi_non_bastano():
    from spese.ricalcolo import _persone, stessa_persona
    p = _persone(["Mario Rossi"])
    assert stessa_persona(p, "ROSSI MARIO") and stessa_persona(p, "Mario Rossi rata auto")
    assert stessa_persona(p, "ROSSI M.") and stessa_persona(p, "SIG. ROSSI")      # nome abbreviato
    assert not stessa_persona(p, "MARIO BIANCHI") and not stessa_persona(p, "ROSSI GIOVANNI")
    assert not stessa_persona(p, "") and not stessa_persona([], "MARIO ROSSI")
    assert stessa_persona(_persone(["Rossi"]), "PAOLO ROSSI")                     # indicato solo il cognome


def test_omonimo_sul_conto_comune_non_e_un_contributo(caricato):
    conn, conti, _ = caricato
    _movimento(conn, "T-OMO-1", conti["credem"], "2026-10-30", 4000, controparte_nome="MARIO BIANCHI",
               descrizione="BON ISTANTANEO A CREDITO MARIO BIANCHI pizza")
    ricalcola(conn)
    a = righe(conn, "SELECT natura, l2 FROM analisi WHERE mov_id = 'T-OMO-1'")[0]
    t = righe(conn, "SELECT stato FROM trasferimenti WHERE mov_id = 'T-OMO-1'")[0]
    assert t["stato"] == "terzi" and a["l2"] is None


def test_contributo_solo_proposto_non_si_annulla(caricato):
    """Un versamento sul comune con una contropartita solo proposta (fiducia bassa) non sparisce dalla vista
    cumulativa: l'altro lato non e' un contributo e resterebbe da solo."""
    from spese import analisi as an
    conn, conti, _ = caricato
    _movimento(conn, "T-PRO-A", conti["tr"], "2026-10-30", -7777, descrizione="Outgoing transfer")
    _movimento(conn, "T-PRO-B", conti["credem"], "2026-10-30", 7777, controparte_nome="MARIO ROSSI",
               descrizione="BON ISTANTANEO A CREDITO ROSSI MARIO")
    ricalcola(conn)
    t = righe(conn, "SELECT stato, fiducia, controparte_mov_id FROM trasferimenti WHERE mov_id = 'T-PRO-B'")[0]
    assert t["stato"] == "contributo" and t["fiducia"] is None and t["controparte_mov_id"] == "T-PRO-A"
    w, par = an.Filtri("tutti").where()
    assert righe(conn, "SELECT 1 FROM analisi a WHERE a.mov_id = 'T-PRO-B' AND " + w, *par)


def test_area_salute_non_esce_anche_senza_parole_chiave(caricato):
    """Un esercente con un nome neutro ma gia' proposto in una sottocategoria della salute (es. dal modello
    locale a confidenza bassa) resta solo locale."""
    conn, conti, _ = caricato
    k = llm.chiavi_da_classificare(conn)[0]["chiave"]
    assert "area riservata (solo locale)" not in controlla(conn, k, chiave=k)
    conn.execute("INSERT INTO cache_esercenti(chiave, l2, motore, confidenza, esercente_normalizzato, aggiornata_il) "
                 "VALUES (?,?,?,?,?,?)", (k, "Farmacia", "modello_locale", 0.4, None, "x"))
    assert "area riservata (solo locale)" in controlla(conn, k, chiave=k)
    llm.prepara_candidati(conn)
    assert righe(conn, "SELECT stato FROM candidati_invio WHERE chiave = ?", k)[0]["stato"] == "bloccato"
    assert all(v["chiave"] != k for lotto in llm.lotti_claude(conn) for v in lotto[0])


def test_area_salute_segue_la_categoria_rinominata(caricato):
    from spese import configurazione as cfg
    from spese import tassonomia_utente as tu
    from spese.privacy import l2_riservate
    conn, _, _ = caricato
    nuove = [{**r, "l1": "SANITA" if r["l1"] == "SALUTE" else r["l1"]} for r in tu.righe(conn)]
    errori, _ = tu.salva(conn, nuove)
    assert not errori
    assert cfg.tassonomia().solo_locale == {"SANITA"} and "Farmacia" in l2_riservate()
    cfg.imposta_tassonomia(None)


def test_importo_excel_con_errore_di_virgola_mobile():
    assert a_centesimi(12.300000000000001, "excel") == 1230
    assert a_centesimi(-1251.6299999999999, "excel") == -125163
    with pytest.raises(FormatoNonValido):
        a_centesimi(12.305, "excel")


def test_due_carte_sullo_stesso_conto():
    """Due carte addebitate sullo stesso conto nello stesso giorno: ogni addebito va alla sua carta."""
    from types import SimpleNamespace
    from spese.ricalcolo import abbina_contenitori
    conti = {1: {"tipo": "conto", "conto_addebito_id": None}, 2: {"tipo": "carta", "conto_addebito_id": 1},
             3: {"tipo": "carta", "conto_addebito_id": 1}}
    m = lambda i, c, d, imp, tipo="pagamento_carta": {"id": i, "conto_id": c, "data": d, "data_registrazione": None,
                                                     "importo_cent": imp, "fee_cent": 0, "tax_cent": 0, "tipo": tipo,
                                                     "ciclo_carta": None}
    movs = [m("a1", 2, "2026-10-05", -1000), m("a2", 2, "2026-10-08", -2000), m("b1", 3, "2026-10-06", -500),
            m("x1", 1, "2026-10-15", -3000, "addebito_carta_credito"), m("x2", 1, "2026-10-15", -500, "addebito_carta_credito")]
    es = abbina_contenitori(SimpleNamespace(conti=conti), movs)
    assert es["x1"]["stato"] == es["x2"]["stato"] == "dettagliato"
    assert (es["x1"]["righe"], es["x2"]["righe"]) == (2, 1)


def test_cambio_passphrase_con_piu_schede(tmp_path):
    """Dopo il cambio di passphrase le altre schede (altre connessioni) non leggono piu' il database: la
    sessione in memoria passa alla passphrase nuova e la generazione dice loro di riaprirlo."""
    from spese import sessioni
    p = tmp_path / "s.db"
    a, b = db.apri(p, "vecchia passphrase"), db.apri(p, "vecchia passphrase")
    sessioni.chiudi()
    t = sessioni.apri("vecchia passphrase")
    g = sessioni.generazione()
    db.cambia_passphrase(a, "nuova passphrase")
    assert sessioni.aggiorna_passphrase("nuova passphrase") == g + 1
    assert sessioni.stato(t, 60) == "valida" and sessioni.passphrase() == "nuova passphrase"
    with pytest.raises(Exception):
        b.execute("SELECT count(*) FROM conti").fetchone()          # la connessione dell'altra scheda e' vecchia
    b.close()
    c = db.apri(p, sessioni.passphrase())                            # riaperta come fa app.py
    assert c.execute("SELECT count(*) FROM conti").fetchone()[0] == 0
    with pytest.raises(db.PassphraseErrata):
        db.apri(p, "vecchia passphrase")
    for x in (a, c):
        x.close()
    sessioni.chiudi()



# --- 0.6.0: prove di classificazione ---------------------------------------------------
def _finto_claude(categoria, confidenza=0.9, inviati=None):
    import json as js
    from types import SimpleNamespace

    def crea(**kw):
        items = js.loads(kw["messages"][0]["content"].split("Esercenti da classificare:\n")[1])
        if inviati is not None:
            inviati.extend(it["esercente"] for it in items)
        out = {"risultati": [{"idx": it["idx"], "l2": categoria, "esercente_normalizzato": it["esercente"].split()[0],
                              "confidenza": confidenza} for it in items]}
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=js.dumps(out))], stop_reason="end_turn")
    return SimpleNamespace(messages=SimpleNamespace(create=crea))


def test_prova_in_anteprima_poi_applicata_in_parte(caricato):
    conn, _, _ = caricato
    llm.prepara_candidati(conn)
    da_fare = [v["chiave"] for v in llm.chiavi_da_classificare(conn)]
    pid, ris = llm.invia_claude(conn, client=_finto_claude("Varie"), modello="claude-haiku-5-5")
    inviate = sorted(ris)
    assert len(inviate) >= 2
    ricalcola(conn)
    assert [v["chiave"] for v in llm.chiavi_da_classificare(conn)] == da_fare      # nulla e' cambiato
    stima = llm.stima_coda(conn)
    assert stima["in_prova"] == len(inviate) and not set(stima["senza_risposta"]) & set(inviate)
    assert llm.prove(conn, "da_confermare")[0]["risposte"] == len(inviate)
    # applicata solo la prima: le altre tornano tra quelle da rimettere in coda
    assert llm.applica_prova(conn, pid, inviate[:1]) == 1
    ricalcola(conn)
    assert inviate[0] not in {v["chiave"] for v in llm.chiavi_da_classificare(conn)}
    assert set(inviate[1:]) <= set(llm.stima_coda(conn)["senza_risposta"])
    assert llm.prove(conn)[0]["stato"] == "applicata"


def test_prova_rimessa_in_coda_per_un_altro_modello(caricato):
    conn, _, _ = caricato
    llm.prepara_candidati(conn)
    pid, ris = llm.invia_claude(conn, client=_finto_claude("Varie", 0.5), modello="claude-haiku-5-5")
    llm.chiudi_prova(conn, pid, rimetti_in_coda_claude=True)
    stati = {r["chiave"]: r["stato"] for r in righe(conn, "SELECT chiave, stato FROM candidati_invio")}
    assert all(stati[k] == "approvato" for k in ris)
    pid2, ris2 = llm.invia_claude(conn, client=_finto_claude("Ristoranti", 0.95), modello="claude-opus-5-5")
    assert set(ris2) == set(ris)
    tabella, riep = llm.confronto(conn, [pid, pid2])
    assert {r["prova"]: r["risposte"] for r in riep} == {pid: len(ris), pid2: len(ris)}
    riga = tabella[0]["risposte"]
    assert riga[pid]["l2"] == "Varie" and riga[pid2]["l2"] == "Ristoranti"
    assert [p["stato"] for p in llm.prove(conn)] == ["da_confermare", "non_applicata"]
    assert not righe(conn, "SELECT * FROM cache_esercenti")


def test_prova_su_esercenti_gia_classificati(caricato):
    """Confronto con le categorie esistenti: le categorie non cambiano, la coda non si tocca, la salute non esce."""
    conn, conti, _ = caricato
    mia, dente = [v["chiave"] for v in llm.chiavi_da_classificare(conn)[:2]]
    servizi.conferma_esercente(conn, mia, "Supermercato")
    servizi.conferma_esercente(conn, dente, "Dentista")                 # nome neutro, categoria della salute
    ricalcola(conn)
    gia = {e["chiave"]: e for e in llm.esercenti_con_categoria(conn)}
    assert gia[mia]["fonte"] == "utente" and gia[mia]["cat"] == ("ALIMENTARI", "Supermercato")
    assert gia["LIDL MILANO"]["fonte"] == "regola"
    prima = righe(conn, "SELECT chiave, l2, fonte FROM analisi ORDER BY id")
    inviati = []
    pid, ris = llm.prova_claude(conn, list(gia), "claude-opus-5-5", client=_finto_claude("Supermercato", 0.8, inviati))
    assert {"LIDL MILANO", mia} <= set(ris) and not righe(conn, "SELECT * FROM candidati_invio")
    ricalcola(conn)
    assert righe(conn, "SELECT chiave, l2, fonte FROM analisi ORDER BY id") == prima
    from spese.privacy import l2_riservate
    salute = [k for k, e in gia.items() if e["cat"][1] in l2_riservate()]
    assert dente in salute and not set(salute) & set(inviati) and dente not in ris
    _, riep = llm.confronto(conn, [pid])
    assert riep[0]["tue"] >= 1 and riep[0]["uguali_tue"] >= 1
    # applicata, non scavalca la tua conferma
    llm.applica_prova(conn, pid)
    assert righe(conn, "SELECT motore FROM cache_esercenti WHERE chiave = ?", mia)[0]["motore"] == "utente"


def test_proposta_scomposta_per_la_revisione():
    from spese.catena import scomponi_proposta
    assert scomponi_proposta("SALUTE · Farmacia (claude, confidenza 0.62)") == \
        {"cat": ("SALUTE", "Farmacia"), "l2": "Farmacia", "fonte": "Claude", "motore": "claude", "confidenza": 0.62}
    assert scomponi_proposta("FINANZA · Assicurazioni (altre) (modello_locale, confidenza 0.40)")["cat"] == \
        ("FINANZA", "Assicurazioni (altre)")
    assert scomponi_proposta("RISTORAZIONE · Bar e pasticcerie (somiglianza 0.67)")["confidenza"] == 0.67
    assert scomponi_proposta("SALUTE · Farmacia (marchio Nova)")["fonte"] == "Marchio Nova"
    assert scomponi_proposta("Farmacia (marchio Nova)")["cat"] == ("SALUTE", "Farmacia")    # versioni precedenti
    assert scomponi_proposta(None)["cat"] is None and scomponi_proposta(float("nan"))["cat"] is None


def test_stessa_sottocategoria_in_due_categorie(caricato):
    """CASA · Varie e ACQUISTI · Varie sono due voci diverse in tutta l'app."""
    from spese import analisi as an
    from spese import attributi as attr
    from spese import configurazione as cfg
    from spese import tassonomia_utente as tu
    conn, conti, _ = caricato
    assert tu.salva(conn, tu.righe(conn) + [{"sezione": "uscite", "l1": "CASA", "l2": "Varie", "originale": ""}]) == ([], [])
    tax = cfg.tassonomia()
    assert tax.valida("CASA", "Varie") and tax.valida("ACQUISTI", "Varie")
    assert tax.risolvi("Varie") == ("ACQUISTI", "Varie")                 # per nome: quella di partenza
    with pytest.raises(ValueError):
        cfg.come_cat(("ALIMENTARI", "Varie"))
    a, b = [v["chiave"] for v in llm.chiavi_da_classificare(conn)[:2]]
    servizi.conferma_esercente(conn, a, ("CASA", "Varie"))
    servizi.conferma_esercente(conn, b, ("ACQUISTI", "Varie"))
    attr.imposta_predefinito(conn, ("CASA", "Varie"), "necessaria")
    ricalcola(conn)
    cat = {r["chiave"]: (r["l1"], r["l2"], r["attributo"]) for r in righe(conn, "SELECT DISTINCT chiave, l1, l2, attributo "
                                                                          "FROM analisi WHERE chiave IN (?, ?)", a, b)}
    assert cat[a] == ("CASA", "Varie", "necessaria") and cat[b][:2] == ("ACQUISTI", "Varie") and cat[b][2] != "necessaria"
    tutto = an.Filtri("tutti")
    casa = an.movimenti(conn, tutto, "CASA", "Varie")
    assert set(casa["chiave"]) == {a}
    # risposta del modello con la coppia; rinomina della sola voce di CASA
    enum = llm.schema_risposta()["properties"]["risultati"]["items"]["properties"]["categoria"]["enum"]
    assert "CASA · Varie" in enum and "ACQUISTI · Varie" in enum
    voci = [{"chiave": "X", "testo": "X"}]
    assert llm.valida(voci, {"risultati": [{"idx": 0, "categoria": "CASA · Varie", "esercente_normalizzato": "",
                                            "confidenza": 0.9}]}, 0.5)["X"][0] == ("CASA", "Varie")
    nuove = [dict(r, l2="Varie casa") if (r["l1"], r["l2"]) == ("CASA", "Varie") else r for r in tu.righe(conn)]
    assert tu.salva(conn, nuove)[0] == []
    assert righe(conn, "SELECT l1, l2 FROM cache_esercenti WHERE chiave = ?", a)[0] == {"l1": "CASA", "l2": "Varie casa"}
    assert righe(conn, "SELECT l1, l2 FROM cache_esercenti WHERE chiave = ?", b)[0] == {"l1": "ACQUISTI", "l2": "Varie"}
    assert attr.predefiniti(conn).get(("CASA", "Varie casa")) == "necessaria"


def test_voci_salvate_senza_categoria_si_completano(conn):
    """Database delle versioni precedenti: conferme, regole e decisioni con la sola sottocategoria."""
    from spese import tassonomia_utente as tu
    conn.execute("INSERT INTO cache_esercenti(chiave, l2, motore, confidenza) VALUES ('A', 'Supermercato', 'utente', 1)")
    conn.execute("INSERT INTO regole_utente(pattern, l2) VALUES ('B', 'Varie')")
    tu.completa_categorie(conn)
    assert righe(conn, "SELECT l1 FROM cache_esercenti")[0]["l1"] == "ALIMENTARI"
    assert righe(conn, "SELECT l1 FROM regole_utente")[0]["l1"] == "ACQUISTI"


# --- elenchi di modelli -------------------------------------------------------------------
def test_elenchi_di_modelli(conn):
    from types import SimpleNamespace
    from spese.db import scrivi_impostazione
    assert llm.separa_modelli(" claude-haiku-5-5;claude-opus-5-5 ;; claude-opus-5-5\n") == ["claude-haiku-5-5", "claude-opus-5-5"]
    assert llm.modelli_claude(conn) == llm.PREDEFINITI_CLAUDE and llm.modello_claude(conn) == "claude-haiku-5-5"
    scrivi_impostazione(conn, "claude_modelli", ["claude-opus-5-5", "claude-fable-5-1"])
    assert llm.modello_claude(conn) == "claude-opus-5-5"              # il precedente non e' piu' in elenco
    scrivi_impostazione(conn, "claude_modello", "claude-fable-5-1")
    assert llm.modello_claude(conn) == "claude-fable-5-1"
    assert llm.errore_nome_modello("claude opus") and not llm.errore_nome_modello("claude-fable-5-1")
    # modello locale: i modelli cloud di Ollama non sono ammessi
    assert llm.errore_nome_modello("gpt-oss:120b-cloud", locale=True) and llm.errore_nome_modello("x:cloud", locale=True)
    assert not llm.errore_nome_modello("qwen2.5:3b", locale=True)
    assert llm.modelli_locali(conn) == ["qwen2.5:3b"]
    scrivi_impostazione(conn, "ollama_modelli", ["llama3.2", "qwen2.5:3b"])
    assert llm.ollama_config(conn)["modello"] == "llama3.2"
    assert llm.installato("llama3.2", ["llama3.2:latest"]) and not llm.installato("llama3.2:1b", ["llama3.2:latest"])

    class NonTrovato(Exception):
        status_code = 404

    def recupera(m):
        if m == "claude-inventato":
            raise NonTrovato()
    finto = SimpleNamespace(models=SimpleNamespace(retrieve=recupera))
    assert llm.verifica_modelli_claude(["claude-opus-5-5", "claude-inventato"], client=finto) == ["claude-inventato"]


def test_scarica_modello_locale(conn, monkeypatch):
    import json as js
    import requests
    from spese.db import scrivi_impostazione
    chiamate, passi = [], []

    class Risposta:
        def __init__(self, righe):
            self.righe = righe

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_lines(self):
            return [js.dumps(r).encode() for r in self.righe]

    def finto_post(url, json=None, stream=None, timeout=None):
        chiamate.append((url, json))
        if json["model"] == "inesistente":
            return Risposta([{"error": "pull model manifest: file does not exist"}])
        return Risposta([{"status": "pulling manifest"}, {"status": "downloading", "completed": 5, "total": 10},
                         {"status": "success"}])
    monkeypatch.setattr(requests, "post", finto_post)
    assert llm.scarica_modello(conn, "llama3.2:3b", lambda *a: passi.append(a))
    assert chiamate[-1] == ("http://127.0.0.1:11434/api/pull", {"model": "llama3.2:3b", "stream": True})
    assert ("downloading", 5, 10) in passi
    with pytest.raises(RuntimeError):
        llm.scarica_modello(conn, "inesistente")
    with pytest.raises(ValueError):
        llm.scarica_modello(conn, "gpt-oss:120b-cloud")                 # non parte nemmeno la richiesta
    assert chiamate[-1][1]["model"] == "inesistente"
    scrivi_impostazione(conn, "ollama_modelli", ["gpt-oss:120b-cloud"])  # scritto a mano nel database
    with pytest.raises(ValueError):
        llm.classifica_locale(conn, [{"chiave": "X", "testo": "X", "paese": None}])


# --- PayPal ---------------------------------------------------------------------------------
INTESTAZIONE_PP = ('"Data","Orario","Fuso orario","Nome","Tipo","Stato","Valuta","Lordo","Tariffa","Netto",'
                   '"Indirizzo email mittente","Indirizzo email destinatario","Codice transazione","Indirizzo di spedizione",'
                   '"Stato dell\'indirizzo","Titolo oggetto","Codice articolo","Importo spese di spedizione e imballaggio",'
                   '"Importo assicurazione","Imposte sulle vendite","Nome opzione 1","Valore opzione 1","Nome opzione 2",'
                   '"Valore opzione 2","Codice transazione di riferimento","N° ordine commerciante","Numero personalizzato",'
                   '"Quantità","Codice ricevuta","Saldo","Indirizzo","Indirizzo (continua)","Città","Provincia",'
                   '"CAP/Codice postale","Paese","Telefono","Oggetto","Messaggio","Prefisso internazionale","Impatto sul saldo"')


def _riga_pp(data, nome, tipo, stato, valuta, lordo, txn, rif="", oggetto="", impatto="Addebito"):
    celle = [data, "10:00:00", "CET", nome, tipo, stato, valuta, lordo, "0,00", lordo, "io@example.com", "x@example.com",
             txn, "", "", oggetto, "", "0,00", "", "0,00", "", "", "", "", rif, "", "", "1", "", "0,00", "", "", "", "",
             "", "", "", oggetto, "", "", impatto]
    return ",".join('"%s"' % c for c in celle)


def paypal_csv():
    righe = [INTESTAZIONE_PP,
             # pagamento all'esercente finanziato dal conto in banca (come nell'esempio reale)
             _riga_pp("01/10/2026", "Patreon Ireland Limited", "Pagamento preautorizzato utenza", "Completata", "EUR",
                      "-10,68", "PAG00000000000001", oggetto="Patreon* Membership"),
             _riga_pp("01/10/2026", "", "Bonifico bancario sul conto PayPal ", "In sospeso", "EUR", "10,68",
                      "RIC00000000000001", "PAG00000000000001", "Patreon* Membership", "Accredito"),
             # pagamento in dollari, finanziato dalla carta, con le due righe di conversione
             _riga_pp("05/10/2026", "Valve Corporation", "Pagamento Express Checkout", "Completata", "USD", "-12,00",
                      "PAG00000000000002"),
             _riga_pp("05/10/2026", "", "Conversione di valuta generica", "Completata", "EUR", "-11,20",
                      "CNV00000000000001", "PAG00000000000002"),
             _riga_pp("05/10/2026", "", "Conversione di valuta generica", "Completata", "USD", "12,00",
                      "CNV00000000000002", "PAG00000000000002", impatto="Accredito"),
             _riga_pp("05/10/2026", "", "Deposito con carta generico", "Completata", "EUR", "11,20",
                      "RIC00000000000002", "PAG00000000000002", impatto="Accredito"),
             # autorizzazione (memo), operazione negata, pagamento a una persona, rimborso di un esercente
             _riga_pp("06/10/2026", "Hotel Esempio", "Autorizzazione generica", "In sospeso", "EUR", "-50,00",
                      "AUT00000000000001", impatto="Memo"),
             _riga_pp("07/10/2026", "Negozio Rifiutato", "Pagamento su sito web", "Negata", "EUR", "-30,00",
                      "PAG00000000000003"),
             _riga_pp("08/10/2026", "Luca Verdi", "Pagamento inviato", "Completata", "EUR", "-20,00", "PAG00000000000004"),
             _riga_pp("09/10/2026", "Valve Corporation", "Rimborso pagamento", "Completata", "EUR", "5,00",
                      "RIM00000000000001", "PAG00000000000002", impatto="Accredito"),
             ]
    return ("\r\n".join(righe) + "\r\n\r\n").encode("utf-8")


def test_lettura_report_paypal():
    from spese.lettore import leggi
    fl = leggi("Download.CSV", [], paypal_csv())
    assert fl.profilo["id"] == "paypal"
    per_id = {m["id_banca"]: m for m in fl.movimenti}
    assert set(per_id) == {"PAG00000000000001", "RIC00000000000001", "PAG00000000000002", "RIC00000000000002",
                           "PAG00000000000004", "RIM00000000000001"}
    pat = per_id["PAG00000000000001"]
    assert (pat["tipo"], pat["esercente"], pat["importo_cent"]) == ("pagamento_carta", "Patreon Ireland Limited", -1068)
    assert "Patreon* Membership" in pat["descrizione"]
    usd = per_id["PAG00000000000002"]
    assert (usd["importo_cent"], usd["importo_orig"], usd["divisa_orig"]) == (-1120, "12.00", "USD")
    assert per_id["RIC00000000000001"]["tipo"] == per_id["RIC00000000000002"]["tipo"] == "ricarica"
    persona = per_id["PAG00000000000004"]
    assert (persona["tipo"], persona["controparte_nome"], persona["esercente"]) == ("bonifico", "Luca Verdi", None)
    assert per_id["RIM00000000000001"]["importo_cent"] == 500 and per_id["RIM00000000000001"]["tipo"] == "pagamento_carta"
    assert [m for _, m in fl.scartate] == ["operazione negata"]
    assert not any("example.com" in (m["descrizione"] or "") for m in fl.movimenti)     # le email restano nel grezzo


def test_paypal_spesa_contata_una_volta(caricato):
    """Con il report PayPal caricato l'addebito 'PAYPAL *...' sulla carta o sul conto diventa un contenitore e la
    ricarica un trasferimento interno: resta la spesa nel report, con il nome vero dell'esercente."""
    conn, conti, _ = caricato
    pp = importa.crea_conto(conn, "PayPal", "paypal", "conto", "personale", "PayPal")
    rep = importa.importa(conn, pp, "Download.CSV", paypal_csv())
    assert rep["nuove"] == 6
    _movimento(conn, "B-PP-1", conti["tr"], "2026-10-02", -1068, tipo="addebito_sepa", esercente="PayPal Europe",
               descrizione="Sepa Direct Debit transfer to PayPal Europe S.a.r.l.")
    _movimento(conn, "B-PP-2", conti["tr"], "2026-10-06", -1120, tipo="pagamento_carta", esercente="VALVE",
               intermediario="PAYPAL", descrizione="PAYPAL *VALVE")
    _movimento(conn, "B-PP-3", conti["tr"], "2026-10-08", -999, tipo="pagamento_carta", esercente="ALTRO",
               intermediario="PAYPAL", descrizione="PAYPAL *ALTRO")
    ricalcola(conn)
    a = {r["mov_id"]: r for r in righe(conn, "SELECT * FROM analisi WHERE mov_id LIKE 'B-%' AND componente = 'principale'")}
    assert a["B-PP-1"]["natura"] == a["B-PP-2"]["natura"] == "contenitore"
    assert a["B-%d-RIC00000000000001" % pp]["natura"] == "trasferimento_interno"
    assert a["B-%d-PAG00000000000001" % pp]["natura"] == "uscita"
    assert a["B-%d-PAG00000000000001" % pp]["data"] == "2026-10-02"        # conta con la data dell'addebito in banca
    # un addebito PayPal senza ricarica nel periodo del report va controllato
    assert a["B-PP-3"]["da_rivedere"] == 1 and "PayPal" in a["B-PP-3"]["motivo"]
    # il pagamento a una persona non diventa un esercente da mandare a Claude
    assert "LUCA VERDI" not in {v["chiave"] for v in llm.chiavi_da_classificare(conn)}


def test_selezione_da_revisione_per_la_classificazione(caricato):
    """Esercenti scelti in Revisione: in coda e verso Claude vanno solo quelli."""
    conn, _, _ = caricato
    tutte = [v["chiave"] for v in llm.chiavi_da_classificare(conn)]
    assert len(tutte) >= 3
    scelti = set(tutte[:2])
    llm.imposta_selezione(conn, list(scelti) + ["NON PIU' DA CLASSIFICARE"])
    assert llm.selezione(conn) == scelti
    stima = llm.stima_coda(conn, scelti)
    assert {v["chiave"] for v, _ in stima["nuove"]} <= scelti
    llm.prepara_candidati(conn, scelti)
    in_coda = {r["chiave"] for r in righe(conn, "SELECT chiave FROM candidati_invio")}
    assert in_coda <= scelti
    llm.prepara_candidati(conn)                                    # ora anche tutti gli altri
    inviati = []
    pid, ris = llm.invia_claude(conn, client=_finto_claude("Varie", 0.9, inviati), solo=scelti)
    assert set(inviati) <= scelti and set(ris) <= scelti
    altri = righe(conn, "SELECT stato FROM candidati_invio WHERE chiave NOT IN (?, ?)", *scelti)
    assert altri and all(r["stato"] != "inviato" for r in altri)    # gli altri restano in coda
    llm.applica_prova(conn, pid)
    ricalcola(conn)
    assert not (llm.selezione(conn) or set()) & set(ris)           # classificati: escono dalla selezione


def test_prova_applicata_in_parte_resta_aperta(caricato):
    conn, _, _ = caricato
    llm.prepara_candidati(conn)
    pid, ris = llm.invia_claude(conn, client=_finto_claude("Varie", 0.9))
    chiavi = sorted(ris)
    assert llm.applica_prova(conn, pid, chiavi[:1], chiudi=False) == 1
    assert llm.prove(conn)[0]["stato"] == "da_confermare"
    assert chiavi[0] not in llm.chiavi_in_prova(conn) and chiavi[1] in llm.chiavi_in_prova(conn)
    llm.applica_prova(conn, pid, chiavi[1:], chiudi=False)                    # applicate tutte: si chiude
    assert llm.prove(conn)[0]["stato"] == "applicata"


# --- portafoglio (prototipo) ----------------------------------------------------------------
def test_portafoglio_costo_medio_e_plusvalenze(conn):
    from spese import portafoglio as pf
    tr = importa.crea_conto(conn, "TR", "trade_republic", "conto", "personale", "TR")
    t = lambda mid, data, tipo, q, imp, fee=0, tax=0, isin="IE00TEST0001": _movimento(
        conn, mid, tr, data, imp, tipo=tipo, isin=isin, nome_titolo="ETF Prova", quantita=q, fee_cent=fee, tax_cent=tax)
    t("a1", "2025-01-10", "acquisto_titoli", "10", -100000, -100)
    t("a2", "2025-02-10", "acquisto_titoli", "10", -120000, -100)
    t("m1", "2025-03-01", "movimento_titoli", "-20", None)               # cambio di ISIN interno: si annulla
    t("m2", "2025-03-01", "movimento_titoli", "20", None)
    t("d1", "2025-03-20", "dividendo", "20", 1000, 0, -260)
    t("v1", "2025-04-10", "vendita_titoli", "-5", 65000, -100, -2561)
    r = pf.calcola(conn)
    p = r["posizioni"][0]
    assert (p["quote"], p["costo"], p["avvisi"]) == (15, 165150, [])          # 2202 - 550,50
    assert round(p["costo_medio"], 2) == Decimal("110.10")
    v = r["vendite"][0]
    assert (v["ricavo"], v["costo"], v["plus"], v["imposte"], v["incompleto"]) == (64900, 55050, 9850, 2561, False)
    assert r["anni"]["2025"]["plus"] == 9850 and r["anni"]["2025"]["dividendi"] == 1000
    # vendita di piu' quote di quelle importate: storico incompleto, finche' non si indica la partenza
    t("b1", "2025-05-10", "vendita_titoli", "-30", 400000, -100, 0, "US00TEST0002")
    t("b0", "2025-04-01", "acquisto_titoli", "10", -100000, -100, 0, "US00TEST0002")
    r = pf.calcola(conn)
    b = [x for x in r["vendite"] if x["isin"] == "US00TEST0002"][0]
    assert b["incompleto"] and b["plus"] is None
    pf.imposta_partenza(conn, "US00TEST0002", "2024-12-31", "20", 180000)
    b = [x for x in pf.calcola(conn)["vendite"] if x["isin"] == "US00TEST0002"][0]
    assert not b["incompleto"] and b["plus"] == 399900 - 280100
    # quote arrivate senza costo: da indicare
    t("m3", "2025-06-01", "movimento_titoli", "3", None, isin="IE00TEST0003")
    p3 = [x for x in pf.calcola(conn)["posizioni"] if x["isin"] == "IE00TEST0003"][0]
    assert p3["costo_da_indicare"] and p3["costo_medio"] is None
    # periodo: le vendite fuori periodo non contano nel riepilogo, ma il costo medio usa tutto lo storico
    r = pf.calcola(conn, da="2025-05-01")
    assert [x["isin"] for x in r["vendite"]] == ["US00TEST0002"]


class _FintoYahoo:
    """Risposte nel formato di Yahoo Finance (search v1, chart v8); registra le richieste fatte."""

    def __init__(self, simboli, chiusure, valute=None, fallisci=None):
        self.simboli, self.chiusure, self.valute = simboli, chiusure, valute or {}
        self.fallisci, self.richieste = fallisci, []

    def get(self, percorso, par):
        from datetime import datetime, timezone
        self.richieste.append((percorso, dict(par)))
        if self.fallisci:
            raise self.fallisci
        if percorso.endswith("/search"):
            return {"quotes": [{"symbol": s, "quoteType": "ETF", "exchDisp": "X", "shortname": "N"}
                               for s in self.simboli.get(par["q"], [])]}
        sim = percorso.rsplit("/", 1)[1]
        if sim not in self.chiusure:
            return {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}}
        dal = datetime.fromtimestamp(par["period1"], tz=timezone.utc).date().isoformat()
        punti = [(d, v) for d, v in self.chiusure[sim] if d >= dal]
        ts = [int(datetime.fromisoformat(d + "T08:00:00+00:00").timestamp()) - 3600 for d, _ in punti]
        return {"chart": {"result": [{"meta": {"currency": self.valute.get(sim, "EUR"), "gmtoffset": 3600},
                                      "timestamp": ts, "indicators": {"quote": [{"close": [v for _, v in punti]}]}}],
                          "error": None}}


def test_prezzi_di_mercato_e_andamento(conn, monkeypatch):
    from datetime import date

    from spese import mercato
    from spese import portafoglio as pf
    tr = importa.crea_conto(conn, "TR", "trade_republic", "conto", "personale", "TR")
    t = lambda mid, data, tipo, q, imp, isin: _movimento(conn, mid, tr, data, imp, tipo=tipo, isin=isin,
                                                         nome_titolo="T " + isin, quantita=q, fee_cent=-100)
    t("a1", "2025-01-10", "acquisto_titoli", "10", -99900, "IE00TEST0001")       # costo 1000 €
    t("a2", "2025-01-14", "acquisto_titoli", "10", -109900, "IE00TEST0001")      # costo 1100 €
    t("b1", "2025-01-10", "acquisto_titoli", "2", -19900, "US00TEST0002")        # costo 200 €, quotato in USD
    eur = [("2025-01-09", 99.0), ("2025-01-10", 100.0), ("2025-01-13", 105.0), ("2025-01-14", 110.0),
           ("2025-01-15", 120.0)]
    yahoo = _FintoYahoo({"IE00TEST0001": ["TST", "TST.F", "TST.DE"], "US00TEST0002": ["UST"]},
                        {"TST.DE": eur, "UST": [("2025-01-10", 110.0), ("2025-01-15", 120.0)],
                         "USDEUR=X": [("2025-01-01", 0.9), ("2025-01-14", 0.95)]}, {"UST": "USD"})
    assert not mercato.attivo(conn)                                             # spento finche' non lo si accende
    r = pf.calcola(conn)
    errori = mercato.aggiorna(conn, {i: date.fromisoformat(p[0][0]) for i, p in r["passi"].items()}, http=yahoo)
    assert errori == {}
    tit = mercato.titoli(conn)
    assert tit["IE00TEST0001"]["simbolo"] == "TST.DE"                           # preferita la borsa in euro
    # cosa esce: solo ISIN e simboli, e la data di partenza e' fissa (mai quella dei propri acquisti)
    for percorso, par in yahoo.richieste:
        assert set(par) <= {"q", "quotesCount", "newsCount", "listsCount", "enableFuzzyQuery", "period1", "period2",
                            "interval", "events"}
        if "period1" in par:
            assert par["period1"] == mercato._epoca(mercato.INIZIO)
    assert {x[0] for x in conn.execute("SELECT motore FROM registro_invii")} == {"yahoo"}
    # i prezzi di un titolo si salvano in una sola transazione (riga per riga con il suo commit: minuti su disco)
    fuori = []
    conn.set_trace_callback(lambda sql: fuori.append(sql) if "prezzi_mercato(" in sql and not conn.in_transaction else None)
    mercato.imposta_simbolo(conn, "US00TEST0002", "UST")
    mercato.aggiorna(conn, {"US00TEST0002": date(2025, 1, 10)}, http=yahoo)
    conn.set_trace_callback(None)
    assert fuori == [] and len(mercato.prezzi(conn)["US00TEST0002"]) == 2
    pr = mercato.prezzi(conn)
    assert pr["IE00TEST0001"][0] == ("2025-01-09", 99.0)                         # dal primo acquisto meno qualche giorno
    assert dict(pr["US00TEST0002"]) == {"2025-01-10": pytest.approx(99.0), "2025-01-15": pytest.approx(114.0)}
    pos = {p["isin"]: p for p in pf.valorizza(r["posizioni"], pr, "2025-01-15")}
    a = pos["IE00TEST0001"]
    assert (a["valore"], a["latente"], round(a["rendimento"], 4)) == (240000, 30000, round(300 / 2100, 4))
    assert pos["US00TEST0002"]["valore"] == 22800
    # andamento giornaliero: posizione dopo gli eventi del giorno, valore = quote x chiusura
    s = pf.andamento(r["passi"]["IE00TEST0001"], pr["IE00TEST0001"])
    assert [(d, int(q), x, v) for d, q, x, v in s] == [
        ("2025-01-10", 10, 100000, 100000), ("2025-01-13", 10, 100000, 105000),
        ("2025-01-14", 20, 210000, 220000), ("2025-01-15", 20, 210000, 240000)]
    assert [d for d, *_ in pf.andamento(r["passi"]["IE00TEST0001"], pr["IE00TEST0001"], da="2025-01-14")] == \
        ["2025-01-14", "2025-01-15"]
    assert [d for d, *_ in pf.andamento(r["passi"]["IE00TEST0001"])] == ["2025-01-10", "2025-01-14"]  # senza prezzi
    # aggiornamento successivo: riparte dall'ultimo prezzo salvato (dato pubblico) e non ricerca il simbolo
    yahoo.richieste.clear()
    monkeypatch.setattr(mercato, "date", type("D", (date,), {"today": staticmethod(lambda: date(2025, 1, 20))}))
    mercato.aggiorna(conn, {"IE00TEST0001": date(2025, 1, 10)}, http=yahoo)
    assert [p for p, _ in yahoo.richieste] == ["/v8/finance/chart/TST.DE"]
    assert yahoo.richieste[0][1]["period1"] == mercato._epoca(date(2025, 1, 8))
    # simbolo indicato a mano; senza rete ci si ferma al primo errore
    mercato.imposta_simbolo(conn, "IE00TEST0001", "tst.f")
    assert mercato.titoli(conn)["IE00TEST0001"]["simbolo"] == "TST.F" and not mercato.prezzi(conn, ["IE00TEST0001"])
    giu = _FintoYahoo({}, {}, fallisci=ConnectionError("rete assente"))
    err = mercato.aggiorna(conn, {"IE00TEST0001": date(2025, 1, 10), "US00TEST0002": date(2025, 1, 10)}, http=giu)
    assert mercato.RETE in err and len(giu.richieste) == 1
    # simbolo inesistente: errore sul titolo, gli altri vanno avanti
    mercato.imposta_simbolo(conn, "IE00TEST0001", "NONCE")
    err = mercato.aggiorna(conn, {"IE00TEST0001": date(2025, 1, 10)}, http=yahoo)
    assert "No data found" in err["IE00TEST0001"] and mercato.titoli(conn)["IE00TEST0001"]["errore"]


def test_sezioni_facoltative(conn):
    from spese import attributi as attr
    from spese import portafoglio as pf
    from spese.db import scrivi_impostazione
    assert attr.etichette_attive(conn) and attr.natura_attiva(conn) and pf.attiva(conn)
    scrivi_impostazione(conn, "funzioni_attributi", False)          # impostazione delle versioni precedenti
    assert not attr.etichette_attive(conn) and not attr.natura_attiva(conn) and not attr.attiva(conn)
    scrivi_impostazione(conn, "sezione_etichette", True)
    assert attr.etichette_attive(conn) and not attr.natura_attiva(conn) and attr.attiva(conn)


def test_conto_censito_indicato_a_mano(caricato):
    """Bonifico a proprio nome senza IBAN della controparte: si indica un conto gia' censito, per beneficiario o per
    singolo movimento; il ricalcolo lo tratta come se l'IBAN fosse quello del conto."""
    from spese.catena import chiave_beneficiario
    conn, conti, _ = caricato
    stato = lambda mid: righe(conn, "SELECT t.stato, a.natura, a.l2, a.da_rivedere FROM trasferimenti t JOIN analisi a "
                                    "ON a.mov_id = t.mov_id AND a.componente = 'principale' WHERE t.mov_id = ?", mid)[0]
    _movimento(conn, "X-1", conti["ing"], "2026-10-20", -50000, controparte_nome="MARIO ROSSI", descrizione="Bonifico")
    _movimento(conn, "X-2", conti["ing"], "2026-10-21", -20000, controparte_nome="ROSSI MARIO", descrizione="Bonifico")
    _movimento(conn, "Y-2", conti["tr"], "2026-10-22", 20000, controparte_nome="BONIFICO IN ENTRATA", descrizione="Bonifico")
    ricalcola(conn)
    assert stato("X-1")["stato"] == "proposto_proprio" and stato("X-1")["da_rivedere"] == 1
    # per beneficiario: un conto deposito (non importato) -> risparmio, per tutti i movimenti di quel nome
    dep = importa.crea_conto(conn, "Deposito", None, "deposito", "personale", importato=0)
    servizi.decidi_conto_destinazione(conn, dep, beneficiario=chiave_beneficiario(None, "Mario Rossi"))
    ricalcola(conn)
    s1 = stato("X-1")
    assert (s1["stato"], s1["natura"], s1["l2"], s1["da_rivedere"]) == ("verso_risparmio", "risparmio", "Accantonamento", 0)
    assert stato("X-2")["stato"] == "verso_risparmio"
    # per singolo movimento (prevale): il conto Trade Republic, dove c'e' la contropartita -> abbinati
    servizi.decidi_conto_destinazione(conn, conti["tr"], mov_ids=["X-2"])
    ricalcola(conn)
    s2 = stato("X-2")
    assert (s2["stato"], s2["natura"], s2["da_rivedere"]) == ("abbinato", "trasferimento_interno", 0)
    assert righe(conn, "SELECT controparte_mov_id FROM trasferimenti WHERE mov_id = 'X-2'")[0]["controparte_mov_id"] == "Y-2"
    assert stato("X-1")["stato"] == "verso_risparmio"
    # una risposta generica sullo stesso beneficiario sostituisce il conto indicato
    servizi.decidi_destinazione(conn, chiave_beneficiario(None, "Mario Rossi"), "altra_persona")
    ricalcola(conn)
    assert stato("X-1")["stato"] == "terzi" and stato("X-2")["stato"] == "abbinato"
    # eliminando il conto spariscono anche le indicazioni che lo riguardano
    servizi.decidi_conto_destinazione(conn, dep, mov_ids=["X-1"])
    conn.execute("DELETE FROM conti WHERE id = ?", (dep,))
    assert not righe(conn, "SELECT 1 FROM destinazioni_movimenti WHERE conto_id = ?", dep)


def test_transazione_annidata_e_annullata(conn):
    from spese.db import leggi_impostazione, scrivi_impostazione, transazione
    with transazione(conn):
        scrivi_impostazione(conn, "prova_a", 1)
        with transazione(conn):                         # dentro una transazione gia' aperta: nessun BEGIN nuovo
            scrivi_impostazione(conn, "prova_b", 2)
    assert not conn.in_transaction and leggi_impostazione(conn, "prova_b") == 2
    with pytest.raises(RuntimeError):
        with transazione(conn):
            scrivi_impostazione(conn, "prova_c", 3)
            raise RuntimeError("errore a meta'")
    assert not conn.in_transaction and leggi_impostazione(conn, "prova_c") is None


def test_partenza_solo_per_i_conti_scelti(conn):
    from spese import portafoglio as pf
    tr = importa.crea_conto(conn, "TR", "trade_republic", "conto", "personale", "TR")
    ing = importa.crea_conto(conn, "ING", "ing_conto", "conto", "personale", "ING")
    _movimento(conn, "p1", tr, "2025-02-01", -10000, tipo="acquisto_titoli", isin="IE00TEST0009", quantita="1",
               nome_titolo="ETF")
    pf.imposta_partenza(conn, "IE00TEST0009", "2024-12-31", "5", 50000)
    assert [p["quote"] for p in pf.calcola(conn, [tr])["posizioni"]] == [6]
    assert pf.calcola(conn, [ing])["posizioni"] == []                  # il conto scelto non ha quel titolo


def test_addebito_paypal_ricondotto_a_mano(caricato):
    """Addebito 'PAYPAL *VEEPEE IT' registrato dalla banca 13 giorni dopo: l'abbinamento automatico (una settimana)
    non lo trova; in Revisione si riconduce al pagamento del report con lo stesso importo e nome simile."""
    from spese import paypal
    from spese.ricalcolo import MOTIVO_PAYPAL, candidati_paypal
    conn, conti, _ = caricato
    extra = [_riga_pp("25/09/2026", "VENTE PRIVEE.COM SA", "Pagamento Express Checkout", "Completata", "EUR", "-45,90",
                      "PAG00000000000005"),
             _riga_pp("25/09/2026", "", "Deposito con carta generico", "Completata", "EUR", "45,90",
                      "RIC00000000000005", "PAG00000000000005", impatto="Accredito"),
             _riga_pp("26/09/2026", "Zalando SE", "Pagamento Express Checkout", "Completata", "EUR", "-45,90",
                      "PAG00000000000006"),
             # pagamento a una persona (tipo non da esercente) e un abbonamento pagato con addebito SEPA di PayPal
             _riga_pp("01/10/2026", "Giulio Esempio", "Pagamento generico", "Completata", "EUR", "-10,00",
                      "PAG00000000000007"),
             _riga_pp("29/09/2026", "Spotify AB", "Pagamento preautorizzato utenza", "Completata", "EUR", "-2,99",
                      "PAG00000000000008")]
    csv = paypal_csv().decode("utf-8").rstrip("\r\n") + "\r\n" + "\r\n".join(extra) + "\r\n"
    pp = importa.crea_conto(conn, "PayPal", "paypal", "conto", "personale", "PayPal")
    importa.importa(conn, pp, "Download.CSV", csv.encode("utf-8"))
    _movimento(conn, "B-VP", conti["tr"], "2026-10-08", -4590, tipo="pagamento_carta", esercente="VEEPEE IT",
               intermediario="PAYPAL", descrizione="PAYPAL *VEEPEE IT")
    _movimento(conn, "B-PAT", conti["ing"], "2026-10-02", -1068, tipo="addebito_sepa", esercente="PayPal Europe",
               descrizione="PayPal Europe S.a.r.l.")
    _movimento(conn, "B-NO", conti["tr"], "2026-10-08", -999, tipo="pagamento_carta", esercente="ALTRO",
               intermediario="PAYPAL", descrizione="PAYPAL *ALTRO")
    ricalcola(conn)
    a = lambda mid: righe(conn, "SELECT * FROM analisi WHERE mov_id = ? AND componente = 'principale'", mid)[0]
    assert a("B-VP")["motivo"] == MOTIVO_PAYPAL and a("B-VP")["natura"] == "uscita"
    assert a("B-PAT")["natura"] == "contenitore"                              # abbinato in automatico
    # proposte: stesso importo, il nome piu' simile prima (VEEPEE ~ VENTE PRIVEE, non Zalando)
    assert paypal.somiglianza("VEEPEE IT", "VENTE PRIVEE.COM SA") > paypal.somiglianza("VEEPEE IT", "Zalando SE")
    cand = candidati_paypal(conn, ["B-VP", "B-NO"])
    assert [x["nome"] for x in cand["B-VP"]] == ["VENTE PRIVEE.COM SA", "Zalando SE"]
    assert cand["B-VP"][0]["proposta"] and cand["B-VP"][0]["giorni"] == -13
    assert cand["B-NO"] == []
    # pagamento a una persona: anche questo si propone ("giulio.esempio" ~ "Giulio Esempio")
    _movimento(conn, "B-MDV", conti["tr"], "2026-10-02", -1000, tipo="pagamento_carta", esercente="giulio.esempio",
               intermediario="PAYPAL", descrizione="PAYPAL *giulio.esempio")
    # addebito SEPA di PayPal: il nome non dice nulla, conta l'unico pagamento con lo stesso importo
    _movimento(conn, "B-SDD", conti["ing"], "2026-10-08", -299, tipo="addebito_sepa",
               descrizione="SDD A : PAYPAL EUROPE S.A.R.L. ET CIE S.C.A 1052084898400/PAYPAL ADDEBI")
    ricalcola(conn)
    cand = candidati_paypal(conn, ["B-MDV", "B-SDD"])
    assert cand["B-MDV"][0]["nome"] == "Giulio Esempio" and cand["B-MDV"][0]["proposta"]
    assert cand["B-MDV"][0]["livello"] == "alta"
    assert cand["B-SDD"][0]["nome"] == "Spotify AB" and cand["B-SDD"][0]["proposta"]
    assert cand["B-SDD"][0]["livello"] == "non valutabile"
    # un pagamento gia' coperto da un altro addebito non si propone (Patreon, abbinato in automatico)
    _movimento(conn, "B-PAT2", conti["tr"], "2026-10-20", -1068, tipo="pagamento_carta", esercente="PATREON",
               intermediario="PAYPAL", descrizione="PAYPAL *PATREON")
    ricalcola(conn)
    assert candidati_paypal(conn, ["B-PAT2"])["B-PAT2"] == []
    # ricerca libera: tutte le uscite del report (anche altri importi e pagamenti a persone), con chi le copre gia'
    from spese.ricalcolo import cerca_pagamenti_paypal
    tutte = cerca_pagamenti_paypal(conn, "B-NO", giorni=30)
    nomi = {x["nome"] for x in tutte}
    assert {"Luca Verdi", "Patreon Ireland Limited", "VENTE PRIVEE.COM SA"} <= nomi
    assert all(x["importo_cent"] < 0 for x in tutte)
    pat = [x for x in tutte if x["nome"] == "Patreon Ireland Limited"][0]
    assert pat["gia_abbinato"] and pat["differenza"] == -1068 + 999
    assert [x["nome"] for x in cerca_pagamenti_paypal(conn, "B-NO", "valve", 30)] == ["Valve Corporation"]
    # ricondotto: l'addebito non conta piu', resta la spesa del report; la ricarica non si riusa
    servizi.riconduci_paypal(conn, "B-VP", "B-%d-PAG00000000000005" % pp)
    servizi.riconduci_paypal(conn, "B-NO", None)                              # non e' nel report: spesa normale
    ricalcola(conn)
    assert (a("B-VP")["natura"], a("B-VP")["da_rivedere"]) == ("contenitore", 0)
    assert a("B-%d-PAG00000000000005" % pp)["natura"] == "uscita"
    assert a("B-%d-PAG00000000000005" % pp)["data"] == "2026-10-08"
    assert a("B-%d-RIC00000000000005" % pp)["natura"] == "trasferimento_interno"
    assert a("B-NO")["natura"] == "uscita" and a("B-NO")["motivo"] != MOTIVO_PAYPAL     # resta da classificare
    # tolta la riconduzione, l'avviso torna; annullando l'import del report spariscono anche le riconduzioni
    servizi.togli_riconduzione_paypal(conn, "B-NO")
    ricalcola(conn)
    assert a("B-NO")["motivo"] == MOTIVO_PAYPAL
    imp_id = righe(conn, "SELECT id FROM import_file WHERE conto_id = ?", pp)[0]["id"]
    importa.annulla_import(conn, imp_id)
    assert not righe(conn, "SELECT 1 FROM abbinamenti_paypal WHERE paypal_id LIKE 'B-%d-%%'" % pp)


def test_carta_ing_addebitata_il_mese_dopo(conn):
    """ING: il 10 del mese si addebitano sul conto le spese della carta del mese prima. Le righe della carta restano
    le spese (ognuna con la sua categoria), l'addebito diventa un contenitore: niente doppio conteggio. La carta
    importata senza indicare il conto di addebito si collega da sola al conto ING che ha gli addebiti."""
    ing = importa.crea_conto(conn, "ING Arancio", "ing_conto", "conto", "personale", "ING")
    importa.crea_conto(conn, "Credem", "credem", "conto", "comune", "Credem")
    carta = importa.crea_conto(conn, "Carta ING", "ing_carta", "carta", "personale", "ING")     # senza conto di addebito
    for i, (d, imp) in enumerate([("2026-01-20", -3000), ("2026-02-02", -1500), ("2026-02-14", -2500),
                                  ("2026-02-27", -4000), ("2026-02-28", 500), ("2026-03-03", -900)]):
        _movimento(conn, "C%d" % i, carta, d, imp, tipo="pagamento_carta", data_registrazione=d, esercente="NEGOZIO %d" % i,
                   chiave="NEGOZIO #", fee_cent=0, tax_cent=0)
    _movimento(conn, "ADD-FEB", ing, "2026-02-10", -3000, tipo="addebito_carta_credito", ciclo_carta="2026-02-10",
               fee_cent=0, tax_cent=0, descrizione="Estratto conto carta di credito al 20260210")
    _movimento(conn, "ADD-MAR", ing, "2026-03-10", -7500, tipo="addebito_carta_credito", ciclo_carta="2026-03-10",
               fee_cent=0, tax_cent=0, descrizione="Estratto conto carta di credito al 20260310")
    ricalcola(conn)
    co = {r["mov_id"]: r for r in righe(conn, "SELECT * FROM contenitori")}
    assert (co["ADD-FEB"]["stato"], co["ADD-FEB"]["righe"]) == ("dettagliato", 1)          # gennaio
    assert (co["ADD-MAR"]["stato"], co["ADD-MAR"]["righe"]) == ("dettagliato", 4)          # febbraio, rimborso compreso
    a = {r["mov_id"]: r for r in righe(conn, "SELECT mov_id, natura, mese FROM analisi WHERE componente = 'principale'")}
    assert a["ADD-MAR"]["natura"] == "contenitore" and a["ADD-FEB"]["natura"] == "contenitore"
    assert a["C1"]["natura"] == "uscita" and a["C1"]["mese"] == "2026-02"
    from spese import analisi as an
    marzo = an.flussi_mensili(conn, an.Filtri("personale", da="2026-03-01", a="2026-03-31"))
    assert int(marzo["uscite"].sum()) == 900                     # a marzo solo la spesa del 3 marzo, non l'addebito
    # la spesa del 3 marzo e' del ciclo successivo: se manca l'addebito di aprile resta una spesa normale
    assert a["C5"]["natura"] == "uscita"
    # importi che non tornano: il mese prima del ciclo, con la differenza da controllare
    conn.execute("UPDATE movimenti SET importo_cent = -7400 WHERE id = 'ADD-MAR'")
    ricalcola(conn)
    c = righe(conn, "SELECT * FROM contenitori WHERE mov_id = 'ADD-MAR'")[0]
    assert (c["stato"], c["righe"], c["differenza_cent"]) == ("differenza", 4, -100)
    # carta senza conto di addebito e due conti con addebiti di altre banche: non si indovina, lo si dice
    from spese.ricalcolo import carte_per_conto
    conti_x = {1: {"tipo": "conto", "banca": "A"}, 2: {"tipo": "conto", "banca": "B"}, 3: {"tipo": "carta", "banca": "C",
               "nome": "Carta C"}}
    movs_x = [{"conto_id": 1, "tipo": "addebito_carta_credito"}, {"conto_id": 2, "tipo": "addebito_carta_credito"}]
    assert carte_per_conto(conti_x, movs_x) == ({}, {})
    conti_x[3]["banca"] = "B"
    assert carte_per_conto(conti_x, movs_x)[1] == {3: 2}



def test_natura_dalla_categoria(caricato):
    """La natura segue la sezione della categoria: niente uscite classificate Stipendio (anche nelle decisioni
    salvate prima); trasferimento interno ed escluso restano senza categoria."""
    conn, conti, _ = caricato
    _movimento(conn, "N-1", conti["ing"], "2026-10-20", -5000, tipo="bonifico", controparte_nome="DITTA SRL",
               fee_cent=0, tax_cent=0)
    _movimento(conn, "N-2", conti["ing"], "2026-10-21", 3000, tipo="pagamento_carta", esercente="NEGOZIO",
               chiave="NEGOZIO", fee_cent=0, tax_cent=0)
    servizi.decidi_movimento(conn, "N-1", ("ENTRATE", "Stipendio"), "uscita")
    assert righe(conn, "SELECT natura FROM decisioni WHERE mov_id = 'N-1'")[0]["natura"] == "entrata"
    conn.execute("INSERT INTO decisioni(mov_id, l1, l2, natura) VALUES ('N-2', 'ENTRATE', 'Stipendio', 'risparmio')")
    servizi.decidi_movimento(conn, "N-1", None, "trasferimento_interno")
    ricalcola(conn)
    a = {r["mov_id"]: r for r in righe(conn, "SELECT * FROM analisi WHERE mov_id LIKE 'N-%' AND componente = 'principale'")}
    assert (a["N-2"]["natura"], a["N-2"]["l2"]) == ("entrata", "Stipendio")      # decisione vecchia incoerente
    assert (a["N-1"]["natura"], a["N-1"]["l2"]) == ("trasferimento_interno", None)
    from spese import configurazione as cfg
    cat_spesa = next(c for c in cfg.tassonomia().per_sezione("uscite"))
    assert cfg.natura_da_categoria(cat_spesa, "rimborso") == "uscita"           # il segno decide poi il rimborso
    assert cfg.natura_da_categoria(cat_spesa, "escluso") == "escluso"


def test_stesso_pagamento_paypal_non_proposto_due_volte(caricato):
    """Due addebiti uguali (es. due mesi di un abbonamento) e un solo pagamento nel report entro i 60 giorni: la
    proposta va a uno solo, il piu' vicino di data; l'altro si sceglie a mano."""
    from spese.ricalcolo import candidati_paypal
    conn, conti, _ = caricato
    pp = importa.crea_conto(conn, "PayPal", "paypal", "conto", "personale", "PayPal")
    importa.importa(conn, pp, "Download.CSV", paypal_csv())
    _movimento(conn, "D-1", conti["tr"], "2026-10-12", -1068, tipo="pagamento_carta", esercente="PATREON",
               intermediario="PAYPAL", descrizione="PAYPAL *PATREON")
    _movimento(conn, "D-2", conti["tr"], "2026-11-12", -1068, tipo="pagamento_carta", esercente="PATREON",
               intermediario="PAYPAL", descrizione="PAYPAL *PATREON")
    ricalcola(conn)
    cand = candidati_paypal(conn, ["D-1", "D-2"])
    proposte = [k for k, v in cand.items() if v and v[0].get("proposta")]
    assert proposte == ["D-1"]


def test_cartella_dati_per_sistema(monkeypatch, tmp_path):
    """Senza SPESE_DATI: Windows usa %LOCALAPPDATA%\\CashFlowAnalyzer, Linux ~/.local/share/spese-app."""
    import os
    import sys
    from spese import percorsi
    monkeypatch.delenv("SPESE_DATI", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert percorsi.cartella_predefinita() == os.path.join(str(tmp_path / "Local"), "CashFlowAnalyzer")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert percorsi.cartella_predefinita() == os.path.join(str(tmp_path / "share"), "spese-app")


def test_portachiavi_su_windows_senza_dbus(monkeypatch):
    """Il controllo della sessione D-Bus vale solo su Linux: su Windows la chiave va nella Gestione credenziali."""
    import sys
    import keyring
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(keyring, "get_password", lambda servizio, utente: "chiave-%s-%s" % (servizio, utente))
    monkeypatch.setattr(sys, "platform", "win32")
    assert llm.chiave_api() == "chiave-spese-app-anthropic"
    monkeypatch.setattr(sys, "platform", "linux")
    assert llm.chiave_api() is None                 # Linux senza sessione grafica: portachiavi non usato


def test_avviatore_riconosce_i_suoi_processi(monkeypatch, tmp_path):
    """Il file pid vale solo se il processo e' vivo ed e' quello giusto: un numero riassegnato dopo un riavvio
    del PC non va considerato (ne' fermato)."""
    import importlib
    import os
    import sys
    from pathlib import Path
    monkeypatch.setenv("SPESE_DATI", str(tmp_path / "dati"))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import avvia
    avvia = importlib.reload(avvia)
    assert avvia.DATI == tmp_path / "dati"
    f = tmp_path / "dati" / "prova.pid"
    avvia.scrivi_pid(f, os.getpid())
    atteso = "pytest"                                     # la riga di comando di questo processo (Linux)
    assert avvia.vivo(f, atteso) == os.getpid()
    if sys.platform != "win32":
        assert avvia.vivo(f, "streamlit") is None         # vivo, ma non e' il server
    else:
        f.write_text("%d 12345\n" % os.getpid())          # stesso numero, ora di creazione diversa
        assert avvia.vivo(f, atteso) is None
    f.write_text("999999999\n")
    assert avvia.vivo(f, atteso) is None
    f.write_text("non un numero")
    assert avvia.vivo(f, atteso) is None


def test_installazione_ollama_parti_pure(monkeypatch):
    from spese import installa_ollama as io
    assert io.indirizzo_locale("http://127.0.0.1:11434") and io.indirizzo_locale("http://localhost:11434")
    assert not io.indirizzo_locale("http://192.168.1.20:11434")
    somme = "%s  OllamaSetup.exe\n%s  ollama-linux-amd64.tar.zst\n" % ("a" * 64, "b" * 64)
    assert io.impronta_pubblicata(somme, "OllamaSetup.exe") == "a" * 64
    assert io.impronta_pubblicata(somme, "manca.exe") is None
    if not io.WINDOWS:
        monkeypatch.setattr(io.os, "geteuid", lambda: 1000)
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setattr(io.shutil, "which", lambda nome: None)
        with pytest.raises(io.NonPossibile) as e:                  # niente desktop ne' sudo: comando da terminale
            io.comando_amministratore()
        assert e.value.comando == io.COMANDO_LINUX
        monkeypatch.setattr(io.shutil, "which", lambda nome: "/usr/bin/" + nome)
        monkeypatch.setenv("DISPLAY", ":0")
        assert io.comando_amministratore() == ["pkexec"]


def test_installazione_ollama_righe_sul_posto():
    """winget e curl ridisegnano la barra con \\r: nel registro resta solo l'ultima versione della riga, mentre
    \\r\\n (fine riga di Windows) chiude una riga normale."""
    from spese import installa_ollama as io
    parti, resto = io.spezza_righe(b"Trovato Ollama\r\n  10 MB / 1.5 GB\r  700 MB / 1.5 GB\r 1.5 GB / 1.5 GB\r\nFatto\r")
    assert parti == [(b"Trovato Ollama", False), (b"  10 MB / 1.5 GB", True), (b"  700 MB / 1.5 GB", True),
                     (b" 1.5 GB / 1.5 GB", False)]
    assert resto == b"Fatto\r"                         # \r in fondo: potrebbe seguire \n
    assert io.spezza_righe(b"Fatto\r", fine=True) == ([(b"Fatto", True)], b"")
    lav = io.Lavoro("http://127.0.0.1:11434", [], True)
    lav.registra("Trovato Ollama")
    lav.registra("10 MB / 1.5 GB")
    lav.registra("700 MB / 1.5 GB", sostituisci=True)
    assert list(lav.righe) == ["Trovato Ollama", "700 MB / 1.5 GB"]
    # su Windows Python scrive \\n come \\r\\n: il risultato non cambia
    comando = [sys.executable, "-c", "import sys; sys.stdout.write('inizio\\n10%\\r50%\\r100%\\r\\nfine\\n')"]
    lav = io.Lavoro("http://127.0.0.1:11434", [], True)
    assert io._esegui(comando, lav.registra) == 0
    assert list(lav.righe) == ["inizio", "100%", "fine"]


def test_fineco(conn, tmp_path):
    """Lista movimenti Fineco in Excel: metadati in testa, entrate e uscite in due colonne, solo i movimenti
    contabilizzati, descrizione spezzata con spazi anche dentro nomi, etichette e IBAN."""
    from spese.lettore import leggi
    p1, p2 = tmp_path / "Fineco_movements_20261031.xlsx", tmp_path / "Fineco_movements_20261107.xlsx"
    saldo1 = sintetici.fineco_xlsx(p1)
    sintetici.fineco_xlsx(p2, "07/11/2026")
    fl = leggi(p1, ["Mario", "Rossi"], p1.read_bytes())
    assert fl.profilo["id"] == "fineco" and not fl.avvisi
    assert fl.metadati == {"numero": "99BB22", "titolare": "MARIO ROSSI", "periodo_da": "01/10/2026",
                           "periodo_a": "31/10/2026", "saldo_alla_data": "1.678,13", "data_saldo": "31/10/2026"}
    assert saldo1 == 1678.13
    assert len(fl.scartate) == 1 and "non ancora contabilizzato" in fl.scartate[0][1]
    per_data = {m["data"]: m for m in fl.movimenti}
    assert len(per_data) == 7
    canone = per_data["2026-10-05"]
    assert (canone["tipo"], canone["importo_cent"], canone["data_valuta"]) == ("commissione", -395, "2026-09-30")
    uscita = per_data["2026-10-12"]                    # istantaneo: IBAN spezzato in due, riunito e verificato
    assert (uscita["tipo"], uscita["controparte_nome"], uscita["controparte_iban"]) == \
        ("bonifico", "MARIO ROSSI", sintetici.IBAN_DEPOSITO)
    entrata = per_data["2026-10-15"]                   # "MARIO RO SSI", "Banca Ordin ante:": nome ricomposto
    assert (entrata["importo_cent"], entrata["controparte_nome"], entrata["controparte_iban"]) == \
        (100000, "MARIO ROSSI", None)
    sepa = per_data["2026-10-20"]
    assert (sepa["controparte_nome"], sepa["controparte_iban"]) == ("Palestra Fit S.r.l.", sintetici.IBAN_PALESTRA)
    sbagliato = per_data["2026-10-21"]                 # cifra di controllo che non torna: nessun IBAN
    assert (sbagliato["controparte_nome"], sbagliato["controparte_iban"]) == ("Giulia Bianchi", None)

    # import: il conto si propone da solo al secondo estratto; quadratura dal saldo del primo
    fin = importa.crea_conto(conn, "Fineco", "fineco", "conto", "personale", "Fineco", sintetici.IBAN_FINECO)
    importa.crea_conto(conn, "Deposito", None, "deposito", "personale", "Banca X", sintetici.IBAN_DEPOSITO,
                             importato=0)
    r1 = importa.importa(conn, fin, p1.name, p1.read_bytes())
    assert r1["nuove"] == 7 and r1["quadratura"].startswith("saldo registrato")
    _, suggerito, _ = importa.anteprima(conn, p2.name, p2.read_bytes())
    assert suggerito == fin
    r2 = importa.importa(conn, fin, p2.name, p2.read_bytes())
    assert (r2["nuove"], r2["duplicate"], r2["scartate"]) == (2, 7, [])
    assert r2["quadratura"] == "OK rispetto al saldo del 2026-10-31"
    ricalcola(conn)
    a = {(r["data"], r["importo_cent"]): r for r in righe(
        conn, "SELECT a.data, a.importo_cent, a.natura, a.l2, a.motivo FROM analisi a "
              "WHERE a.conto_id = ?", fin)}
    assert a[("2026-10-05", -395)]["l2"] == "Commissioni bancarie"
    assert a[("2026-10-08", -42)]["l2"] == "Interessi passivi"
    assert a[("2026-10-09", -850)]["l2"] == a[("2026-11-02", -500)]["l2"] == "Bollo"
    assert a[("2026-10-12", -25000)]["natura"] == "risparmio"      # verso il conto deposito, per IBAN
    assert a[("2026-10-15", 100000)]["motivo"].startswith("Trasferimento a tuo nome")


def test_installazione_ollama_lavoro(monkeypatch):
    """Ollama gia' installato ma fermo: il lavoro lo avvia, aspetta che risponda e scarica i modelli; un errore
    arriva come messaggio, senza eccezioni."""
    import time
    from spese import installa_ollama as io
    stato = {"attivo": False, "avviato": 0, "scaricati": []}
    monkeypatch.setattr(io, "installato", lambda: True)
    monkeypatch.setattr(io, "risponde", lambda url, timeout=2: stato["attivo"])
    monkeypatch.setattr(io, "avvia", lambda lav: stato.update(attivo=True, avviato=stato["avviato"] + 1))
    monkeypatch.setattr(io.time, "sleep", lambda s: None)
    monkeypatch.setattr(io, "_no_cloud_windows", lambda: None)
    orologio = iter(range(0, 10 ** 6, 5))
    monkeypatch.setattr(io.time, "monotonic", lambda: next(orologio))
    monkeypatch.setattr(llm, "scarica_modello_url", lambda url, nome, avanzamento=None: stato["scaricati"].append(nome))
    io.chiudi_lavoro()
    lav = io.avvia_lavoro("http://127.0.0.1:11434", ["qwen2.5:3b", "llama3.2:3b"], installa=False)
    for _ in range(200):
        if lav.finito:
            break
        time.sleep(0.01)
    assert lav.finito and not lav.errore, io.riepilogo(lav)
    assert stato["avviato"] == 1 and stato["scaricati"] == ["qwen2.5:3b", "llama3.2:3b"]
    assert io.avvia_lavoro("http://127.0.0.1:11434", ["x"]) is not lav or lav.finito
    io.chiudi_lavoro()
    assert io.lavoro() is None

    def rotto(url, nome, avanzamento=None):
        raise RuntimeError("pull model manifest: file does not exist")
    monkeypatch.setattr(llm, "scarica_modello_url", rotto)
    lav = io.avvia_lavoro("http://127.0.0.1:11434", ["inesistente:1b"], installa=False)
    for _ in range(200):
        if lav.finito:
            break
        time.sleep(0.01)
    assert lav.errore and "file does not exist" in lav.errore
    io.chiudi_lavoro()
