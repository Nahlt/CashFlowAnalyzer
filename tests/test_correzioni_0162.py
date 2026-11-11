"""Test delle correzioni della 0.16.2 che non erano gia' coperte dalle due revisioni della 0.16.1."""
from datetime import date
from decimal import Decimal

import pytest

import sintetici
from spese import analisi as an
from spese import configurazione as cfg
from spese import db, importa, llm, servizi
from spese.chiavi import chiave
from spese.privacy import controlla, uscita_claude
from spese.ricalcolo import ricalcola
from test_app import _FintoYahoo, _finto_claude
from test_revisione_0161 import addebito, carta_tsv, mov, pp, report, righe


# --- privacy: un solo punto di controllo ---------------------------------------------------------
def test_controllo_sui_nomi_originali_e_sulle_chiavi_mascherate(caricato):
    conn, conti, *_ = caricato
    casi = {"IBAN": "QUOTA IT60X0542811101000000123456", "telefono": "IDRAULICO +39 333 1234567",
            "email": "NEGOZIO info@example.com", "codice fiscale": "ASSOCIAZIONE RSSMRA80A01F205X"}
    for n, (motivo, nome) in enumerate(casi.items()):
        mov(conn, "ID-%d" % n, conti["credem"], "2026-10-12", -1000, tipo="addebito_sepa", esercente=nome,
            chiave=chiave(nome), descrizione=nome)
        assert motivo in controlla(conn, chiave(nome), chiave=chiave(nome)), (motivo, chiave(nome))
    # i codici di punto vendita sono normali: non bloccano
    nome = "CRF EXP GAR4857123 MILANO"
    mov(conn, "ID-9", conti["credem"], "2026-10-12", -1000, tipo="pagamento_carta", esercente=nome, chiave=chiave(nome))
    assert controlla(conn, chiave(nome), chiave=chiave(nome)) == []
    # una chiave gia' mascherata si riconosce anche senza i movimenti
    assert "codice fiscale" in controlla(conn, "ASSOCIAZIONE RSSMRA#A#F#X")
    assert "IBAN" in controlla(conn, "QUOTA IT#X#")
    assert controlla(conn, "ESSELUNGA # MILANO") == []


@pytest.mark.parametrize("nome", ["DR VERDI", "CENTRO PRELIEVI", "FISIOKINESITERAPIA ROSSI", "AUXOLOGICO MILANO",
                                  "SANTAGOSTINO", "UNISALUTE SPA", "STUDIO ONCOLOGICO", "PSICHIATRA BIANCHI",
                                  "NUTRIZIONISTA NERI", "AMBULATORIO VIA ROMA", "IRCCS OSPEDALE"])
def test_area_salute_dal_nome(conn, nome):
    assert "area riservata (solo locale)" in controlla(conn, chiave(nome))


def test_testo_modificato_normalizzato_e_anteprima_uguale_all_invio(caricato):
    conn, conti, *_ = caricato
    llm.prepara_candidati(conn)
    k = righe(conn, "SELECT chiave FROM candidati_invio WHERE stato = 'approvato' ORDER BY chiave")[0]["chiave"]
    servizi.candidato(conn, k, testo="Negozio 10/11/2026 eur 123,45 carta 4111 1111 1111 1111")
    salvato = righe(conn, "SELECT testo FROM candidati_invio WHERE chiave = ?", k)[0]["testo"]
    assert salvato == "NEGOZIO # # # EUR # # CARTA # # # #" and not any(ch.isdigit() for ch in salvato)
    servizi.candidato(conn, k, testo="   ")                       # cella svuotata: torna la chiave
    assert righe(conn, "SELECT testo FROM candidati_invio WHERE chiave = ?", k)[0]["testo"] == k
    # cio' che l'anteprima mostra e' cio' che parte
    servizi.candidato(conn, k, testo="negozio 42")
    anteprima = [v["testo"] for lotto in llm.lotti_claude(conn) for v in lotto[0]]
    inviati = []
    llm.invia_claude(conn, client=_finto_claude("Varie", 0.9, inviati))
    assert inviati == anteprima and "NEGOZIO #" in inviati
    assert all(not any(ch.isdigit() for ch in x) for x in inviati)


def test_lotto_alterato_non_parte(caricato):
    """Ultimo controllo prima della rete: un lotto diverso da quello uscito dal punto unico di controllo non parte."""
    conn, conti, *_ = caricato
    llm.prepara_candidati(conn)
    voci, sistema, utente = llm.lotti_claude(conn)[0]
    inviati = []
    alterate = [dict(voci[0], testo="MARIO ROSSI 12345")] + voci[1:]
    pid = llm.nuova_prova(conn, "claude", "claude-haiku-5-5", "coda", voci)
    with pytest.raises(RuntimeError, match="controllo di sicurezza"):
        llm._invia_lotto(conn, _finto_claude("Varie", 0.9, inviati), "claude-haiku-5-5", alterate, sistema, utente, pid)
    with pytest.raises(RuntimeError, match="controllo di sicurezza"):
        llm._invia_lotto(conn, _finto_claude("Varie", 0.9, inviati), "claude-haiku-5-5", voci, sistema,
                         utente + " importo 12,50", pid)
    assert inviati == []


def test_uscita_claude_paese_solo_sigla(conn):
    voci = [{"chiave": "NEGOZIO", "testo": None, "paese": "via Roma 1, Milano"},
            {"chiave": "BAR", "testo": "bar 7", "paese": "it"},
            {"chiave": "EDICOLA", "testo": float("nan"), "paese": float("nan")}]     # celle vuote di una tabella
    ammesse, bloccate = uscita_claude(conn, voci)
    assert bloccate == [] and [(v["testo"], v["paese"]) for v in ammesse] == \
        [("NEGOZIO", None), ("BAR #", "IT"), ("EDICOLA", None)]
    assert "NaN" not in llm.richiesta(ammesse, esterno=True)[1]


# --- carta di credito ------------------------------------------------------------------------------
def test_estratto_parziale_il_resto_conta_come_varie(conn):
    ing = importa.crea_conto(conn, "ING", "ing_conto", "conto", "personale", "ING")
    carta = importa.crea_conto(conn, "Carta", "ing_carta", "carta", "personale", "ING", conto_addebito_id=ing)
    importa.importa(conn, carta, "carta.tsv", carta_tsv([("20/09/2026", "NEGOZIO", "-20,00")]))
    addebito(conn, "ADD", ing, "2026-10-10", -10000)
    ricalcola(conn)
    r = {x["componente"]: x for x in righe(conn, "SELECT * FROM analisi WHERE mov_id = 'ADD'")}
    assert (r["principale"]["natura"], r["principale"]["importo_cent"]) == ("contenitore", -10000)
    assert (r["residuo"]["natura"], r["residuo"]["importo_cent"], r["residuo"]["l2"]) == ("uscita", -8000, "Varie")
    assert r["residuo"]["da_rivedere"] == 1 and "80.00 EUR" in r["residuo"]["motivo"]
    # una tua categoria sull'addebito vale per il resto non dettagliato
    servizi.decidi_movimento(conn, "ADD", ("ACQUISTI", "Varie"))
    ricalcola(conn)
    assert righe(conn, "SELECT l1 FROM analisi WHERE mov_id = 'ADD' AND componente = 'residuo'")[0]["l1"] == "ACQUISTI"
    # importato il resto dell'estratto l'addebito torna al centesimo e il residuo sparisce
    importa.importa(conn, carta, "carta2.tsv", carta_tsv([("22/09/2026", "ALTRO NEGOZIO", "-80,00")]))
    ricalcola(conn)
    assert [x["componente"] for x in righe(conn, "SELECT componente FROM analisi WHERE mov_id = 'ADD'")] == ["principale"]
    assert righe(conn, "SELECT stato FROM contenitori")[0]["stato"] == "dettagliato"


# --- import --------------------------------------------------------------------------------------------
def test_file_gia_importato_in_un_altro_conto(caricato):
    conn, conti, *_ = caricato
    fl, _, gia = importa.anteprima(conn, "carta.csv", sintetici.ing_carta_tsv())
    assert gia and importa.conti_del_file(conn, fl.hash_file) == [(conti["carta"], "Carta ING")]
    assert importa.avviso_file_altrove(conn, fl.hash_file, conti["carta"]) is None      # stesso conto: deduplica
    assert "gia' importato nel conto Carta ING" in importa.avviso_file_altrove(conn, fl.hash_file, conti["ing"])
    assert "gia' importato nel conto Carta ING" in importa.avviso_file_altrove(conn, fl.hash_file, None)   # conto nuovo
    rep = importa.importa(conn, conti["ing"], "carta.csv", sintetici.ing_carta_tsv())
    assert any("contano due volte" in a for a in rep["avvisi"])


def test_identificativo_della_banca_ripetuto_nel_file(conn):
    ppc = importa.crea_conto(conn, "PayPal", "paypal", "conto", "personale", "PayPal")
    rep = importa.importa(conn, ppc, "Download.CSV", report(
        pp("01/10/2026", "Negozio Uno", "Pagamento Express Checkout", "Completata", "EUR", "-5,00", "DOPPIO"),
        pp("02/10/2026", "Negozio Due", "Pagamento Express Checkout", "Completata", "EUR", "-9,00", "DOPPIO")))
    assert (rep["nuove"], rep["duplicate"]) == (1, 1)
    assert any("stesso identificativo della banca" in a for a in rep["avvisi"])


def test_annullare_l_import_rimette_il_conto_com_era(conn):
    a_mano = importa.crea_conto(conn, "Conto risparmio", None, "conto", "personale", None, importato=0)
    rep = importa.importa(conn, a_mano, "Download.CSV", report(
        pp("01/10/2026", "Negozio", "Pagamento Express Checkout", "Completata", "EUR", "-5,00", "P1")))
    assert dict(conn.execute("SELECT profilo, banca, importato FROM conti WHERE id = ?", (a_mano,)).fetchone()) == \
        {"profilo": "paypal", "banca": "PayPal", "importato": 1}
    importa.annulla_import(conn, rep["import_id"])
    assert dict(conn.execute("SELECT profilo, banca, importato FROM conti WHERE id = ?", (a_mano,)).fetchone()) == \
        {"profilo": None, "banca": None, "importato": 0}
    # un conto nato con il suo formato lo tiene anche senza file
    ing = importa.crea_conto(conn, "ING", "ing_conto", "conto", "personale", "ING")
    rep = importa.importa(conn, ing, "carta.tsv", carta_tsv([("20/09/2026", "NEGOZIO", "-20,00")]))
    importa.annulla_import(conn, rep["import_id"])
    assert conn.execute("SELECT profilo, banca FROM conti WHERE id = ?", (ing,)).fetchone()[:] == ("ing_conto", "ING")


# --- PayPal --------------------------------------------------------------------------------------------
def test_ricarica_parziale_resta_da_rivedere(conn):
    from spese.ricalcolo import MOTIVO_PAYPAL
    ppc = importa.crea_conto(conn, "PayPal", "paypal", "conto", "personale", "PayPal")
    importa.importa(conn, ppc, "Download.CSV", report(
        pp("01/10/2026", "Negozio Esempio", "Pagamento Express Checkout", "Completata", "EUR", "-100,00", "P1"),
        pp("01/10/2026", "", "Finanziamento nuovo tipo", "Completata", "EUR", "70,00", "R1", "P1", impatto="Accredito")))
    ricalcola(conn)
    r = righe(conn, "SELECT a.natura, a.da_rivedere, a.motivo, m.tipo FROM analisi a JOIN movimenti m ON m.id = a.mov_id "
                    "WHERE a.importo_cent = 7000")[0]
    assert r["tipo"] == "bonifico" and r["da_rivedere"] == 1 and "piu' grande" in r["motivo"]
    assert 7000 in [x["importo_cent"] for x in an.altri_da_rivedere(conn, MOTIVO_PAYPAL)]


def test_storno_senza_nome_prende_l_esercente_del_pagamento():
    from spese.lettore import leggi
    fl = leggi("Download.CSV", [], report(
        pp("01/10/2026", "Negozio Esempio", "Pagamento Express Checkout", "Completata", "EUR", "-20,00", "P3"),
        pp("15/10/2026", "", "Storno pagamento", "Completata", "EUR", "20,00", "S1", "P3", impatto="Accredito")))
    storno = [m for m in fl.movimenti if m["id_banca"] == "S1"][0]
    assert (storno["tipo"], storno["esercente"], storno["importo_cent"]) == ("pagamento_carta", "Negozio Esempio", 2000)


def test_pagamento_da_cellulare_con_oggetto_resta_un_acquisto():
    from spese.lettore import leggi
    fl = leggi("Download.CSV", [], report(
        pp("08/10/2026", "Bar Centrale", "Pagamento da cellulare", "Completata", "EUR", "-4,00", "P9", oggetto="Colazione")))
    assert (fl.movimenti[0]["tipo"], fl.movimenti[0]["esercente"]) == ("pagamento_carta", "Bar Centrale")


def test_accredito_paypal_senza_uscita_nel_report_da_rivedere(caricato):
    conn, conti, *_ = caricato
    ppc = importa.crea_conto(conn, "PayPal", "paypal", "conto", "personale", "PayPal")
    importa.importa(conn, ppc, "Download.CSV", report(
        pp("01/10/2026", "Negozio", "Pagamento Express Checkout", "Completata", "EUR", "-30,00", "P4"),
        pp("20/10/2026", "Altro", "Pagamento Express Checkout", "Completata", "EUR", "-1,00", "P5")))
    mov(conn, "TR-R", conti["tr"], "2026-10-10", 3000, tipo="pagamento_carta", esercente="NEGOZIO", intermediario="PAYPAL",
        descrizione="PAYPAL *NEGOZIO", chiave="NEGOZIO")
    ricalcola(conn)
    r = righe(conn, "SELECT natura, da_rivedere, motivo FROM analisi WHERE mov_id = 'TR-R'")[0]
    assert r["natura"] == "rimborso" and r["da_rivedere"] == 1 and "Accredito PayPal" in r["motivo"]


# --- contributi ------------------------------------------------------------------------------------------
def test_contributo_in_attesa_dell_altro_lato(caricato):
    """Versamento dal conto personale al comune in un mese che il conto comune non copre ancora: nel perimetro
    personale e' un contributo versato; guardando i due conti insieme si annulla, come quelli abbinati."""
    conn, conti, *_ = caricato
    def uscite(f):
        df = an.flussi_mensili(conn, f)
        return 0 if df.empty else int(df[df["mese"] == "2026-11"]["uscite"].sum())
    mov(conn, "V-1", conti["ing"], "2026-11-20", -50000, tipo="bonifico", controparte_nome="Conto comune",
        controparte_iban=sintetici.IBAN_CREDEM)
    ricalcola(conn)
    t = righe(conn, "SELECT stato, fiducia, conto_destinazione_id FROM trasferimenti WHERE mov_id = 'V-1'")[0]
    assert (t["stato"], t["fiducia"], t["conto_destinazione_id"]) == ("contributo", "attesa", conti["credem"])
    assert uscite(an.Filtri("personale")) == 50000
    assert uscite(an.Filtri("tutti")) == 0
    assert uscite(an.Filtri("tutti", conti=[conti["ing"], conti["credem"]])) == 0
    assert uscite(an.Filtri("tutti", conti=[conti["ing"]])) == 50000


# --- database ----------------------------------------------------------------------------------------------
def test_versione_dello_schema(tmp_path):
    p = tmp_path / "v.db"
    c = db.apri(p, "passphrase di prova")
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.VERSIONE_SCHEMA
    c.execute("PRAGMA user_version = %d" % (db.VERSIONE_SCHEMA + 1))
    c.close()
    with pytest.raises(db.SchemaPiuRecente):
        db.apri(p, "passphrase di prova")


def test_migrazione_da_0161(tmp_path):
    """Database creato dalla 0.16.1: prezzi e cambi in virgola mobile, lo stesso pagamento PayPal ricondotto a due
    addebiti, nessuna colonna nuova."""
    p = tmp_path / "vecchio.db"
    c = db.apri(p, "passphrase di prova")
    with db.transazione(c):
        for tab, ddl in (("prezzi_mercato", "(isin TEXT NOT NULL, data TEXT NOT NULL, prezzo REAL NOT NULL, PRIMARY KEY (isin, data))"),
                         ("cambi_mercato", "(valuta TEXT NOT NULL, data TEXT NOT NULL, tasso REAL NOT NULL, PRIMARY KEY (valuta, data))")):
            c.execute("DROP TABLE %s" % tab)
            c.execute("CREATE TABLE %s %s" % (tab, ddl))
        c.execute("INSERT INTO prezzi_mercato VALUES ('IE00TEST0001', '2025-01-10', 1.225)")
        c.execute("INSERT INTO prezzi_mercato VALUES ('IE00TEST0001', '2025-01-13', 104.30000305175781)")
        c.execute("INSERT INTO cambi_mercato VALUES ('USD', '2025-01-10', 0.95)")
        c.execute("DROP INDEX ux_paypal_pagamento")
        c.execute("INSERT INTO abbinamenti_paypal VALUES ('A-1', 'P-1', '2026-11-01T10:00:00')")
        c.execute("INSERT INTO abbinamenti_paypal VALUES ('A-2', 'P-1', '2026-11-02T10:00:00')")
        c.execute("INSERT INTO abbinamenti_paypal VALUES ('A-3', NULL, '2026-11-02T10:00:00')")
        c.execute("ALTER TABLE import_file DROP COLUMN conto_prima")
        c.execute("PRAGMA user_version = 0")
    c.close()
    c = db.apri(p, "passphrase di prova")
    from spese import mercato
    assert mercato.prezzi(c)["IE00TEST0001"] == [("2025-01-10", Decimal("1.225")), ("2025-01-13", Decimal("104.300003"))]
    assert c.execute("SELECT typeof(prezzo) FROM prezzi_mercato").fetchone()[0] == "text"
    assert c.execute("SELECT tasso FROM cambi_mercato").fetchone()[0] == "0.95"
    assert sorted(r[0] for r in c.execute("SELECT addebito_id FROM abbinamenti_paypal")) == ["A-2", "A-3"]
    assert "conto_prima" in {r[1] for r in c.execute("PRAGMA table_info(import_file)")}
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.VERSIONE_SCHEMA
    c.close()
    db.apri(p, "passphrase di prova").close()                    # idempotente


# --- portafoglio e prezzi --------------------------------------------------------------------------------
def test_valore_in_decimale():
    from spese import portafoglio as pf
    pos = [{"isin": "X", "aperta": True, "quote": Decimal("100"), "costo": 10000, "costo_da_indicare": False}]
    pf.valorizza(pos, {"X": [("2025-01-10", Decimal("1.225"))]}, "2025-01-31")
    assert pos[0]["valore"] == 12250
    pos[0]["quote"] = Decimal("1")
    pf.valorizza(pos, {"X": [("2025-01-10", Decimal("1.225"))]}, "2025-01-31")
    assert pos[0]["valore"] == 123                               # mezzo centesimo: per eccesso, sempre
    assert pf._valore_cent(Decimal("3"), Decimal("0.335")) == 101


def test_ordine_con_l_ora_dell_operazione(conn):
    """Con l'ora su tutti gli eventi del giorno vale l'ora: vendo le quote che avevo e ricompro piu' tardi."""
    from spese import portafoglio as pf
    tr = importa.crea_conto(conn, "TR", "trade_republic", "conto", "personale", "TR")
    mov(conn, "a0", tr, "2026-01-10", -100000, tipo="acquisto_titoli", isin="IE00TEST0001", quantita="10", ora="10:00")
    mov(conn, "a-ricompro", tr, "2026-03-02", -120000, tipo="acquisto_titoli", isin="IE00TEST0001", quantita="10", ora="15:00")
    mov(conn, "z-vendo", tr, "2026-03-02", 110000, tipo="vendita_titoli", isin="IE00TEST0001", quantita="-10", ora="09:00")
    r = pf.calcola(conn)
    assert r["vendite"][0]["plus"] == 10000
    assert [(p["quote"], p["costo"]) for p in r["posizioni"]] == [(Decimal("10"), 120000)]


def test_partenza_prima_della_migrazione_si_somma(conn):
    """Una posizione di partenza con una data precedente alla migrazione e' un'altra posizione: si somma, e
    l'avviso sul costo delle quote migrate resta."""
    from spese import portafoglio as pf
    tr = importa.crea_conto(conn, "TR", "trade_republic", "conto", "personale", "TR")
    mov(conn, "m3", tr, "2025-06-01", None, tipo="movimento_titoli", isin="IE00TEST0003", quantita="3")
    pf.imposta_partenza(conn, "IE00TEST0003", "2025-01-01", "5", 50000)
    p = pf.calcola(conn)["posizioni"][0]
    assert p["quote"] == 8 and p["costo_da_indicare"] and len(p["avvisi"]) == 1
    # la partenza dopo la migrazione ne indica il costo
    pf.imposta_partenza(conn, "IE00TEST0003", "2025-06-15", "3", 30000)
    p = pf.calcola(conn)["posizioni"][0]
    assert (p["quote"], p["costo"], p["costo_da_indicare"], p["avvisi"]) == (3, 30000, False, [])


def test_prezzi_recuperati_dopo_uno_storico_piu_vecchio(conn):
    from spese import mercato
    serie = [("2025-03-03", 90.0), ("2025-06-02", 95.0), ("2026-10-01", 100.0), ("2026-10-02", 101.0)]
    yahoo = _FintoYahoo({"IE00TEST0001": ["TST.DE"]}, {"TST.DE": serie})
    assert mercato.aggiorna(conn, {"IE00TEST0001": date(2026, 10, 1)}, http=yahoo) == {}
    assert [d for d, _ in mercato.prezzi(conn)["IE00TEST0001"]] == ["2026-10-01", "2026-10-02"]
    # importato lo storico del 2025: servono anche i prezzi di allora, e la richiesta riparte dalla data fissa
    yahoo.richieste.clear()
    assert mercato.aggiorna(conn, {"IE00TEST0001": date(2025, 3, 1)}, http=yahoo) == {}
    assert [d for d, _ in mercato.prezzi(conn)["IE00TEST0001"]] == [d for d, _ in serie]
    assert [par["period1"] for _, par in yahoo.richieste if "period1" in par] == [mercato._epoca(mercato.INIZIO)]
    # da qui in poi di nuovo solo gli ultimi giorni, anche se Yahoo non ha prezzi piu' vecchi di quelli salvati
    yahoo.richieste.clear()
    mercato.aggiorna(conn, {"IE00TEST0001": date(2025, 1, 1)}, http=yahoo)
    yahoo.richieste.clear()
    mercato.aggiorna(conn, {"IE00TEST0001": date(2025, 1, 1)}, http=yahoo)
    assert all(par["period1"] != mercato._epoca(mercato.INIZIO) for _, par in yahoo.richieste if "period1" in par)


# --- installazione e formati --------------------------------------------------------------------------------
def test_firma_dell_installatore_di_ollama():
    from spese.installa_ollama import firma_di_ollama
    assert firma_di_ollama("Valid|CN=Ollama Inc., O=Ollama Inc., L=Palo Alto, S=California, C=US")
    assert firma_di_ollama('Valid|CN="Ollama, Inc.", O="Ollama, Inc.", C=US')
    assert not firma_di_ollama("Valid|CN=Not Ollama Fan Club, O=Evil Corp")
    assert not firma_di_ollama("Valid|CN=Evil, OU=ollama, O=Evil Corp")
    assert not firma_di_ollama("HashMismatch|CN=Ollama Inc., O=Ollama Inc.")
    assert not firma_di_ollama("")


def test_fineco_causali_comuni():
    from spese.lettore import _tipo, cerca_senza_spazi
    import re
    p = cfg.profili()["fineco"]
    assert _tipo(p, ["Pagamento Visa Debit"])[0] == "pagamento_carta"
    assert _tipo(p, ["Prelievi Bancomat extra Gruppo"])[0] == "prelievo"
    assert _tipo(p, ["SEPA Direct Debit"])[0] == "addebito_sepa"
    assert _tipo(p, ["Stipendio"])[0] == "stipendio"
    assert _tipo(p, ["Dicitura mai vista"])[0] == "altro"
    rx = re.compile(p["estrazione"]["pagamento_carta"][0])
    g = cerca_senza_spazi(rx, "Pag. del 10/04/26 ora 12:30 presso: ESSELUNGA VIA LO SANNA MILANO Carta N° *****123 Nes suna Commissione")
    assert g["esercente"] == "ESSELUNGA VIA LO SANNA MILANO"
