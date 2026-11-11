"""Regressioni dei difetti segnalati dalla seconda revisione della 0.16.1, corretti nella 0.16.2.
Ogni test afferma il comportamento corretto: sulla 0.16.1 fallivano."""
import sintetici
from spese import analisi as an
from spese import configurazione as cfg
from spese import db, importa, llm, servizi
from spese.ricalcolo import ricalcola
from test_revisione_0161 import addebito, carta_tsv, mov, nature, pp, report, righe


def uscite(conn, conti=None):
    f = an.flussi_mensili(conn, an.Filtri("tutti", conti=conti or []))
    return 0 if f.empty else int(f["uscite"].sum())


# --- O1: la tassonomia nelle richieste a Claude ----------------------------------------------------
def test_O1_sottocategoria_segnata_non_inviare(caricato):
    """Le sottocategorie di spesa si scrivono nelle richieste a Claude, tranne quelle segnate "non inviare":
    quel nome non compare ne' nel testo ne' nello schema della risposta. Il modello locale le vede tutte."""
    from spese import tassonomia_utente as tu
    conn, conti, *_ = caricato
    nuove = [dict(r) for r in tu.righe(conn)] + [
        {"sezione": "uscite", "l1": "SALUTE", "l2": "Terapia Mario Rossi", "originale": "", "non_inviare": True}]
    errori, _ = tu.salva(conn, nuove)
    try:
        assert not errori
        assert ("SALUTE", "Terapia Mario Rossi") in cfg.tassonomia().non_inviare
        llm.prepara_candidati(conn)
        utente = llm.lotti_claude(conn)[0][2]
        assert "Mario Rossi" not in utente and "Mario Rossi" not in str(llm.schema_risposta(esterno=True))
        assert "Farmacia" in utente                                   # di partenza nessuna voce e' esclusa
        assert "Terapia Mario Rossi" in llm.richiesta([])[1]          # modello locale: resta sul PC
        # il segno segue la voce quando la si rinomina, e si puo' togliere
        righe_tax = [dict(r) for r in tu.righe(conn)]
        for r in righe_tax:
            if r["l2"] == "Terapia Mario Rossi":
                assert r["non_inviare"]
                r["l2"] = "Terapia"
        assert not tu.salva(conn, righe_tax)[0]
        assert cfg.tassonomia().non_inviare == {("SALUTE", "Terapia")}
        assert "non_inviare:\n- SALUTE|Terapia" in tu.esporta_yaml(conn)
        righe_tax = [dict(r, non_inviare=False) for r in tu.righe(conn)]
        assert not tu.salva(conn, righe_tax)[0] and not cfg.tassonomia().non_inviare
    finally:
        cfg.imposta_tassonomia(None)


# --- O2: testo modificato a mano ----------------------------------------------------------------
def test_O2_testo_modificato_con_data_importo_e_carta(caricato):
    conn, conti, *_ = caricato
    llm.prepara_candidati(conn)
    k = righe(conn, "SELECT chiave FROM candidati_invio WHERE stato = 'approvato' ORDER BY chiave")[0]["chiave"]
    testo = "NEGOZIO 10/11/2026 EUR 123,45 carta 4111 1111 1111 1111"
    servizi.candidato(conn, k, testo=testo)
    inviati = [v["testo"] for lotto in llm.lotti_claude(conn) for v in lotto[0]]
    assert testo not in inviati, inviati


# --- O4: riga di carta fuori posto, altro conto -------------------------------------------------
def test_O4_riga_fuori_posto_annulla_anche_un_altro_conto(conn):
    a = importa.crea_conto(conn, "ING A", "ing_conto", "conto", "personale", "ING")
    b = importa.crea_conto(conn, "ING B", "ing_conto", "conto", "personale", "ING")
    importa.importa(conn, a, "carta.tsv", carta_tsv([("20/09/2026", "NEGOZIO", "-100,00")]))
    addebito(conn, "A-OTT", a, "2026-10-10", -10000)
    addebito(conn, "A-NOV", a, "2026-11-10", -80000)
    addebito(conn, "B-OTT", b, "2026-10-15", -50000)
    ricalcola(conn)
    n = nature(conn, "A-OTT", "A-NOV", "B-OTT")
    assert uscite(conn) == 140000, (uscite(conn), n)


# --- O5: estratto parziale ------------------------------------------------------------------------
def test_O5_estratto_carta_parziale(conn):
    ing = importa.crea_conto(conn, "ING", "ing_conto", "conto", "personale", "ING")
    carta = importa.crea_conto(conn, "Carta", "ing_carta", "carta", "personale", "ING", conto_addebito_id=ing)
    importa.importa(conn, carta, "carta.tsv", carta_tsv([("20/09/2026", "NEGOZIO", "-20,00")]))
    addebito(conn, "ADD", ing, "2026-10-10", -10000)
    ricalcola(conn)
    assert uscite(conn) == 10000, (uscite(conn), nature(conn, "ADD"), righe(conn, "SELECT * FROM contenitori"))


# --- O6: filtro sui soli conti finanziatori ---------------------------------------------------------
def test_O6_solo_banca_con_paypal(conn):
    banca = importa.crea_conto(conn, "Banca", "credem", "conto", "personale", "Credem")
    ppc = importa.crea_conto(conn, "PayPal", "paypal", "conto", "personale", "PayPal")
    importa.importa(conn, ppc, "Download.CSV", report(
        pp("01/10/2026", "Negozio Esempio", "Pagamento Express Checkout", "Completata", "EUR", "-100,00", "P1"),
        pp("01/10/2026", "", "Deposito con carta generico", "Completata", "EUR", "100,00", "R1", "P1", impatto="Accredito")))
    mov(conn, "BN-1", banca, "2026-10-02", -10000, tipo="pagamento_carta", esercente="NEGOZIO", intermediario="PAYPAL",
        descrizione="PAYPAL *NEGOZIO", chiave="NEGOZIO")
    ricalcola(conn)
    assert uscite(conn, [banca, ppc]) == 10000
    assert uscite(conn, [banca]) == 10000, uscite(conn, [banca])


def test_O6_carta_resta_sul_conto_carta(caricato):
    """Per la carta di credito vale la regola delle specifiche: l'addebito mensile sul conto e' un contenitore e
    le spese stanno sul conto della carta. I due conti da soli sommano al totale: niente si perde o raddoppia."""
    conn, conti, *_ = caricato
    insieme = uscite(conn, [conti["ing"], conti["carta"]])
    assert uscite(conn, [conti["ing"]]) + uscite(conn, [conti["carta"]]) == insieme


# --- O10: natura e categoria, percorsi diversi dalla regola ----------------------------------------
def test_O10_decisione_senza_natura(caricato):
    conn, conti, *_ = caricato
    mov(conn, "D-1", conti["ing"], "2026-10-16", -5000, tipo="pagamento_carta", esercente="NEGOZIO XY", chiave="NEGOZIO XY")
    servizi.decidi_movimento(conn, "D-1", ("ENTRATE", "Stipendio"))
    ricalcola(conn)
    r = righe(conn, "SELECT natura, l1, l2 FROM analisi WHERE mov_id = 'D-1'")[0]
    assert not (r["natura"] == "uscita" and r["l1"] == "ENTRATE"), r


def test_O10_conferma_esercente(caricato):
    conn, conti, *_ = caricato
    mov(conn, "D-2", conti["ing"], "2026-10-16", -5000, tipo="pagamento_carta", esercente="NEGOZIO ZW", chiave="NEGOZIO ZW")
    servizi.conferma_esercente(conn, "NEGOZIO ZW", ("ENTRATE", "Stipendio"))
    ricalcola(conn)
    r = righe(conn, "SELECT natura, l1, l2 FROM analisi WHERE mov_id = 'D-2'")[0]
    assert not (r["natura"] == "uscita" and r["l1"] == "ENTRATE"), r


# --- O11: due ricalcoli concorrenti ------------------------------------------------------------------
def test_O11_ricalcoli_concorrenti(conn, monkeypatch):
    """Mentre una scheda ricalcola, un'altra non puo' scrivere nel mezzo: aspetta (qui, con un'attesa di pochi
    millisecondi, rinuncia). Finito il primo ricalcolo scrive e ricalcola: le analisi coprono tutti i movimenti."""
    from spese import percorsi, ricalcolo
    ing = importa.crea_conto(conn, "ING", "ing_conto", "conto", "personale", "ING")
    mov(conn, "M-1", ing, "2026-10-01", -1000, tipo="pagamento_carta", esercente="A", chiave="A")
    altra = db.apri(percorsi.file_database(), "passphrase di prova")           # seconda scheda
    altra.execute("PRAGMA busy_timeout = 50")
    originale, bloccata = ricalcolo.abbina_contenitori, []

    def in_mezzo(ctx, movs):
        if not bloccata:                                # la scheda A ha gia' letto i movimenti
            try:
                mov(altra, "M-2", ing, "2026-10-02", -2000, tipo="pagamento_carta", esercente="B", chiave="B")
                bloccata.append(False)
            except db.sqlite.OperationalError:
                bloccata.append(True)
        return originale(ctx, movs)
    monkeypatch.setattr(ricalcolo, "abbina_contenitori", in_mezzo)
    ricalcola(conn)
    assert bloccata == [True]
    mov(altra, "M-2", ing, "2026-10-02", -2000, tipo="pagamento_carta", esercente="B", chiave="B")
    ricalcola(altra)
    n_mov = conn.execute("SELECT count(*) FROM movimenti").fetchone()[0]
    n_an = conn.execute("SELECT count(DISTINCT mov_id) FROM analisi").fetchone()[0]
    altra.close()
    assert n_an == n_mov == 2


# --- O12: backup durante una scrittura --------------------------------------------------------------
def test_O12_backup_con_transazione_aperta(conn, tmp_path):
    """Con una scrittura in corso in un'altra scheda il backup non copia il file a meta': rinuncia con un
    messaggio. Finita (qui: annullata) la scrittura, la copia contiene solo dati confermati."""
    import pytest
    from spese import percorsi
    ing = importa.crea_conto(conn, "ING", "ing_conto", "conto", "personale", "ING")
    with db.transazione(conn):
        for i in range(3000):
            mov(conn, "K-%04d" % i, ing, "2026-10-01", -100, tipo="pagamento_carta", descrizione="originale " + "x" * 300)
    conn.execute("PRAGMA busy_timeout = 50")
    scrive = db.apri(percorsi.file_database(), "passphrase di prova")           # scheda che sta scrivendo
    scrive.execute("PRAGMA cache_size = 10")
    scrive.execute("BEGIN")
    scrive.execute("UPDATE movimenti SET descrizione = 'NON CONFERMATO ' || descrizione")
    dest = tmp_path / "backup.db"
    with pytest.raises(db.DatabaseOccupato):
        db.backup(conn, dest)
    scrive.execute("ROLLBACK")
    db.backup(conn, dest)
    assert not conn.in_transaction
    # durante la copia nessun'altra scheda inizia una scrittura (il blocco e' di questa connessione)
    scrive.execute("UPDATE movimenti SET descrizione = descrizione WHERE id = 'K-0000'")
    scrive.close()
    copia = db.apri(dest, "passphrase di prova")
    sporche = copia.execute("SELECT count(*) FROM movimenti WHERE descrizione LIKE 'NON CONFERMATO%'").fetchone()[0]
    assert (sporche, copia.execute("SELECT count(*) FROM movimenti").fetchone()[0]) == (0, 3000)
    assert copia.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    copia.close()


# --- O13: acquisto e vendita nello stesso giorno ----------------------------------------------------
def test_O13_compravendita_nello_stesso_giorno(conn):
    from spese import portafoglio as pf
    tr = importa.crea_conto(conn, "TR", "trade_republic", "conto", "personale", "TR")
    mov(conn, "z-acquisto", tr, "2026-03-02", -100000, tipo="acquisto_titoli", isin="IE00TEST0001", quantita="10",
        ora="09:00", nome_titolo="ETF")
    mov(conn, "a-vendita", tr, "2026-03-02", 110000, tipo="vendita_titoli", isin="IE00TEST0001", quantita="-10",
        ora="15:00", nome_titolo="ETF")
    r = pf.calcola(conn)
    aperte = [(p["isin"], p["quote"]) for p in r["posizioni"] if p["aperta"]]
    assert r["vendite"][0]["plus"] == 10000 and not aperte, (r["vendite"][0]["plus"], aperte)


# --- O14: stesso ISIN su due conti ----------------------------------------------------------------------
def test_O14_costo_medio_per_conto(conn):
    from spese import portafoglio as pf
    a = importa.crea_conto(conn, "TR A", "trade_republic", "conto", "personale", "TR")
    b = importa.crea_conto(conn, "TR B", "trade_republic", "conto", "personale", "TR")
    mov(conn, "a1", a, "2026-01-10", -100000, tipo="acquisto_titoli", isin="IE00TEST0001", quantita="10")
    mov(conn, "b1", b, "2026-01-11", -200000, tipo="acquisto_titoli", isin="IE00TEST0001", quantita="10")
    mov(conn, "a2", a, "2026-02-10", 120000, tipo="vendita_titoli", isin="IE00TEST0001", quantita="-10")
    solo_a = pf.calcola(conn, [a])["vendite"][0]["plus"]
    insieme = pf.calcola(conn, [a, b])["vendite"][0]["plus"]
    assert solo_a == insieme == 20000, {"solo A": solo_a, "A+B": insieme}
