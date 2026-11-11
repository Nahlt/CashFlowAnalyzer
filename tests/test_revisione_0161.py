"""Regressioni dei difetti trovati nella revisione della 0.16.1, corretti nella 0.16.2.

Ogni test afferma il comportamento corretto: sulla 0.16.1 fallivano tutti. Tre usano diciture PayPal ipotizzate,
da confrontare con un report reale: M1 ("Pagamento da cellulare" verso una persona), M7 storno ("Storno
pagamento" senza nome) e M8 ("Prelievo generico con carta" come riga di riaccredito)."""
import sintetici
from spese import analisi as an
from spese import importa, llm, servizi
from spese.chiavi import chiave
from spese.ricalcolo import ricalcola

INT_PP = ('"Data","Orario","Fuso orario","Nome","Tipo","Stato","Valuta","Lordo","Tariffa","Netto",'
          '"Indirizzo email mittente","Indirizzo email destinatario","Codice transazione","Indirizzo di spedizione",'
          '"Stato dell\'indirizzo","Titolo oggetto","Codice articolo","Importo spese di spedizione e imballaggio",'
          '"Importo assicurazione","Imposte sulle vendite","Nome opzione 1","Valore opzione 1","Nome opzione 2",'
          '"Valore opzione 2","Codice transazione di riferimento","N° ordine commerciante","Numero personalizzato",'
          '"Quantità","Codice ricevuta","Saldo","Indirizzo","Indirizzo (continua)","Città","Provincia",'
          '"CAP/Codice postale","Paese","Telefono","Oggetto","Messaggio","Prefisso internazionale","Impatto sul saldo"')


def pp(data, nome, tipo, stato, valuta, lordo, txn, rif="", oggetto="", impatto="Addebito"):
    celle = [data, "10:00:00", "CET", nome, tipo, stato, valuta, lordo, "0,00", lordo, "io@example.com", "x@example.com",
             txn, "", "", oggetto, "", "0,00", "", "0,00", "", "", "", "", rif, "", "", "1", "", "0,00", "", "", "", "",
             "", "", "", oggetto, "", "", impatto]
    return ",".join('"%s"' % c for c in celle)


def report(*righe_pp):
    return ("\r\n".join([INT_PP] + list(righe_pp)) + "\r\n").encode("utf-8")


def righe(conn, sql, *par):
    return [dict(r) for r in conn.execute(sql, par).fetchall()]


def mov(conn, mid, conto, data, importo, tipo="bonifico", **altri):
    campi = {"id": mid, "conto_id": conto, "data": data, "importo_cent": importo, "tipo": tipo, **altri}
    conn.execute("INSERT INTO movimenti(%s) VALUES (%s)" % (",".join(campi), ",".join("?" * len(campi))),
                 list(campi.values()))


def carta_tsv(righe_carta):
    out = ["DATA OPERAZIONE\tDATA REGISTRAZIONE\tDESCRIZIONE OPERAZIONE\tIMPORTO IN EURO"]
    for d, nome, imp in righe_carta:
        out.append("%s\t%s\tOperazione presso %s\t%s" % (d, d, nome, imp))
    return ("\n".join(out) + "\n").encode("utf-8")


def addebito(conn, mid, conto, data, cent):
    mov(conn, mid, conto, data, cent, tipo="addebito_carta_credito", ciclo_carta=data, fee_cent=0, tax_cent=0,
        descrizione="Estratto conto carta di credito al %s" % data.replace("-", ""))


def nature(conn, *ids):
    r = {x["mov_id"]: x for x in righe(conn, "SELECT mov_id, natura, da_rivedere, motivo FROM analisi "
                                             "WHERE componente = 'principale'")}
    return {i: r[i]["natura"] for i in ids}


# --- A1 ---------------------------------------------------------------------------------------
def test_A1_carta_nel_conto_sbagliato_un_solo_mese(conn):
    """Estratto di gennaio nel conto ING: solo l'addebito di febbraio non deve contare."""
    ing = importa.crea_conto(conn, "ING", "ing_conto", "conto", "personale", "ING")
    importa.importa(conn, ing, "carta_gen.tsv", carta_tsv([("05/01/2026", "NEGOZIO A", "-300,00"),
                                                           ("28/01/2026", "NEGOZIO B", "-500,00")]))
    addebito(conn, "ADD-FEB", ing, "2026-02-10", -80000)
    addebito(conn, "ADD-MAR", ing, "2026-03-10", -65000)
    addebito(conn, "ADD-APR", ing, "2026-04-10", -70000)
    ricalcola(conn)
    n = nature(conn, "ADD-FEB", "ADD-MAR", "ADD-APR")
    assert n["ADD-FEB"] == "contenitore"
    assert n["ADD-MAR"] == "uscita", n
    assert n["ADD-APR"] == "uscita", n


# --- A2 ---------------------------------------------------------------------------------------
def test_A2_mese_di_estratto_mancante(conn):
    """Manca l'estratto di febbraio: l'addebito di marzo deve contare (Varie) e quelli dopo restare dettagliati."""
    ing = importa.crea_conto(conn, "ING", "ing_conto", "conto", "personale", "ING")
    carta = importa.crea_conto(conn, "Carta ING", "ing_carta", "carta", "personale", "ING", conto_addebito_id=ing)
    importa.importa(conn, carta, "carta.tsv", carta_tsv([
        ("05/01/2026", "A", "-300,00"), ("20/01/2026", "B", "-500,00"),
        ("03/03/2026", "C", "-100,00"), ("08/03/2026", "D", "-150,00"), ("20/03/2026", "E", "-200,00"),
        ("04/04/2026", "F", "-120,00"), ("09/04/2026", "G", "-80,00"), ("25/04/2026", "H", "-300,00"),
        ("05/05/2026", "I", "-90,00")]))
    addebito(conn, "ADD-FEB", ing, "2026-02-10", -80000)      # gennaio
    addebito(conn, "ADD-MAR", ing, "2026-03-10", -65000)      # febbraio: estratto mancante
    addebito(conn, "ADD-APR", ing, "2026-04-10", -45000)      # marzo
    addebito(conn, "ADD-MAG", ing, "2026-05-10", -50000)      # aprile
    ricalcola(conn)
    co = {r["mov_id"]: r["stato"] for r in righe(conn, "SELECT * FROM contenitori")}
    n = nature(conn, "ADD-FEB", "ADD-MAR", "ADD-APR", "ADD-MAG")
    assert co["ADD-FEB"] == "dettagliato"
    assert n["ADD-MAR"] == "uscita", (n, co)
    assert co["ADD-APR"] == "dettagliato", co
    assert co["ADD-MAG"] == "dettagliato", co


# --- A3 ---------------------------------------------------------------------------------------
def test_A3_stesso_file_su_due_conti(caricato):
    """L'estratto della carta, gia' nel conto Carta, importato anche nel conto ING: niente doppioni silenziosi."""
    conn, conti, *_ = caricato
    prima = an.flussi_mensili(conn, an.Filtri("tutti"))["uscite"].sum()
    _, _, gia = importa.anteprima(conn, "carta.csv", sintetici.ing_carta_tsv())
    assert gia                                              # l'app dice: "le righe gia' presenti verranno ignorate"
    rep = importa.importa(conn, conti["ing"], "carta.csv", sintetici.ing_carta_tsv())
    ricalcola(conn)
    dopo = an.flussi_mensili(conn, an.Filtri("tutti"))["uscite"].sum()
    avvisi = righe(conn, "SELECT motivo FROM analisi WHERE motivo LIKE '%tipo Carta%'")
    assert rep["nuove"] == 0 or avvisi, (rep["nuove"], int(prima), int(dopo), avvisi)


# --- A5 ---------------------------------------------------------------------------------------
def test_A5_codice_fiscale_nell_esercente_non_esce(caricato):
    conn, conti, *_ = caricato
    nome = "ASSOCIAZIONE SPORTIVA RSSMRA80A01F205X"
    mov(conn, "CF-1", conti["credem"], "2026-10-12", -4500, tipo="addebito_sepa", esercente=nome, chiave=chiave(nome),
        descrizione="PAGAMENTO PREAUTORIZZATO " + nome)
    ricalcola(conn)
    llm.prepara_candidati(conn)
    stato = righe(conn, "SELECT chiave, stato, motivo FROM candidati_invio WHERE chiave = ?", chiave(nome))
    inviati = [v["testo"] for lotto in llm.lotti_claude(conn) for v in lotto[0]]
    assert chiave(nome) not in inviati, (stato, inviati)


# --- A6 ---------------------------------------------------------------------------------------
def test_A6_spesa_paypal_pagata_dal_conto_comune(caricato):
    conn, conti, *_ = caricato
    ppc = importa.crea_conto(conn, "PayPal", "paypal", "conto", "personale", "PayPal")
    importa.importa(conn, ppc, "Download.CSV", report(
        pp("01/10/2026", "Apple Services", "Pagamento preautorizzato utenza", "Completata", "EUR", "-7,00", "P1"),
        pp("01/10/2026", "", "Bonifico bancario sul conto PayPal ", "In sospeso", "EUR", "7,00", "R1", "P1",
           impatto="Accredito")))
    mov(conn, "SDD-PP", conti["credem"], "2026-10-03", -700, tipo="addebito_sepa", esercente="PayPal Europe",
        descrizione="PAGAMENTO PREAUTORIZZATO PAYPAL EUROPE")
    ricalcola(conn)
    spesa = righe(conn, "SELECT natura, perimetro, data FROM analisi WHERE mov_id = ?", "B-%d-P1" % ppc)[0]
    assert nature(conn, "SDD-PP")["SDD-PP"] == "contenitore"
    assert spesa["perimetro"] == "comune", spesa


# --- M1 ---------------------------------------------------------------------------------------
def test_M1_pagamento_da_cellulare_a_una_persona(conn):
    from spese.lettore import leggi
    fl = leggi("Download.CSV", [], report(
        pp("08/10/2026", "Luca Verdi", "Pagamento da cellulare", "Completata", "EUR", "-20,00", "P9")))
    m = fl.movimenti[0]
    assert (m["tipo"], m["esercente"]) == ("bonifico", None), (m["tipo"], m["esercente"], m["chiave"])


# --- M4 ---------------------------------------------------------------------------------------
def test_M4_giroconto_a_proprio_nome_e_bonifico_di_terzi(caricato):
    """Giroconto verso un mio conto non caricato e, due giorni dopo, un bonifico di un'azienda dello stesso importo
    sul conto comune: non sono la stessa operazione."""
    conn, conti, *_ = caricato
    mov(conn, "G-1", conti["ing"], "2026-10-05", -77700, tipo="giroconto", controparte_nome="MARIO ROSSI",
        controparte_iban=sintetici.IBAN_DEPOSITO, descrizione="Giroconto a favore di MARIO ROSSI")
    mov(conn, "B-1", conti["credem"], "2026-10-07", 77700, tipo="bonifico", controparte_nome="ACME SRL",
        descrizione="BON A CREDITO ACME SRL Rif. X rimborso")
    ricalcola(conn)
    t = {r["mov_id"]: r for r in righe(conn, "SELECT * FROM trasferimenti WHERE mov_id IN ('G-1','B-1')")}
    assert t["B-1"]["stato"] not in ("abbinato", "contributo") or t["B-1"]["fiducia"] != "alta", dict(t["B-1"])


# --- M5 ---------------------------------------------------------------------------------------
def test_M5_contributo_versato_senza_contropartita(caricato):
    """Versamento dal conto personale al conto comune in un periodo non ancora importato per il comune."""
    conn, conti, *_ = caricato
    mov(conn, "V-1", conti["ing"], "2026-11-20", -50000, tipo="bonifico", controparte_nome="Conto comune",
        controparte_iban=sintetici.IBAN_CREDEM, descrizione="Bonifico a favore di Conto comune")
    ricalcola(conn)
    r = righe(conn, "SELECT natura, l2, da_rivedere FROM analisi WHERE mov_id = 'V-1'")[0]
    assert (r["natura"], r["l2"]) == ("uscita", "Contributo versato"), r


# --- M6 ---------------------------------------------------------------------------------------
def test_M6_conto_broker_importato(conn):
    ing = importa.crea_conto(conn, "ING", "ing_conto", "conto", "personale", "ING", sintetici.IBAN_ING)
    tr = importa.crea_conto(conn, "TR", "trade_republic", "broker", "personale", "TR", sintetici.IBAN_TR)
    mov(conn, "I-1", ing, "2026-10-22", -100000, tipo="giroconto", controparte_nome="MARIO ROSSI",
        controparte_iban=sintetici.IBAN_TR)
    mov(conn, "T-1", tr, "2026-10-22", 100000, tipo="versamento", controparte_nome="MARIO ROSSI",
        controparte_iban=sintetici.IBAN_ING)
    mov(conn, "T-2", tr, "2026-10-25", -100000, tipo="acquisto_titoli", isin="IE00TEST0001", quantita="20")
    ricalcola(conn)
    f = an.flussi_mensili(conn, an.Filtri("tutti"))
    assert int(f["risparmio"].sum()) == 100000, int(f["risparmio"].sum())


# --- M7 ---------------------------------------------------------------------------------------
def test_M7_ricarica_in_valuta_con_dicitura_sconosciuta(conn):
    from spese.lettore import leggi
    from spese.paypal import riallinea_ricariche
    dati = report(
        pp("05/10/2026", "Valve Corporation", "Pagamento Express Checkout", "Completata", "USD", "-12,00", "P2"),
        pp("05/10/2026", "", "Conversione di valuta generica", "Completata", "EUR", "-11,20", "C1", "P2"),
        pp("05/10/2026", "", "Conversione di valuta generica", "Completata", "USD", "12,00", "C2", "P2", impatto="Accredito"),
        pp("05/10/2026", "", "Finanziamento nuovo tipo", "Completata", "EUR", "11,20", "R2", "P2", impatto="Accredito"))
    tipi = {m["id_banca"]: m["tipo"] for m in leggi("Download.CSV", [], dati).movimenti}
    ppc = importa.crea_conto(conn, "PayPal", "paypal", "conto", "personale", "PayPal")
    importa.importa(conn, ppc, "Download.CSV", dati)
    corretti = riallinea_ricariche(conn)
    assert tipi["R2"] == "ricarica" and corretti == 0, (tipi, corretti)


def test_M7_storno_senza_nome_non_e_una_ricarica(conn):
    from spese.lettore import leggi
    dati = report(
        pp("01/10/2026", "Negozio Esempio", "Pagamento Express Checkout", "Completata", "EUR", "-20,00", "P3"),
        pp("15/10/2026", "", "Storno pagamento", "Completata", "EUR", "20,00", "S1", "P3", impatto="Accredito"))
    tipi = {m["id_banca"]: m["tipo"] for m in leggi("Download.CSV", [], dati).movimenti}
    assert tipi["S1"] != "ricarica", tipi


# --- M8 (dipende dalla dicitura reale della riga di riaccredito) ---------------------------------
def test_M8_rimborso_paypal_riaccreditato_sulla_carta(caricato):
    conn, conti, *_ = caricato
    prima = int(an.flussi_mensili(conn, an.Filtri("tutti"))["uscite"].sum())
    ppc = importa.crea_conto(conn, "PayPal", "paypal", "conto", "personale", "PayPal")
    importa.importa(conn, ppc, "Download.CSV", report(
        pp("01/10/2026", "Negozio Esempio", "Pagamento Express Checkout", "Completata", "EUR", "-30,00", "P4"),
        pp("01/10/2026", "", "Deposito con carta generico", "Completata", "EUR", "30,00", "R4", "P4", impatto="Accredito"),
        pp("09/10/2026", "Negozio Esempio", "Rimborso pagamento", "Completata", "EUR", "30,00", "M4", "P4", impatto="Accredito"),
        pp("09/10/2026", "", "Prelievo generico con carta", "Completata", "EUR", "-30,00", "W4", "M4")))
    mov(conn, "TR-A", conti["tr"], "2026-10-02", -3000, tipo="pagamento_carta", esercente="NEGOZIO", intermediario="PAYPAL",
        descrizione="PAYPAL *NEGOZIO", chiave="NEGOZIO")
    mov(conn, "TR-R", conti["tr"], "2026-10-10", 3000, tipo="pagamento_carta", esercente="NEGOZIO", intermediario="PAYPAL",
        descrizione="PAYPAL *NEGOZIO", chiave="NEGOZIO")
    ricalcola(conn)
    dopo = int(an.flussi_mensili(conn, an.Filtri("tutti"))["uscite"].sum())
    assert dopo - prima == 0, dopo - prima       # acquisto di 30 e rimborso di 30: saldo zero


# --- M9 ---------------------------------------------------------------------------------------
def test_M9_bonifico_senza_controparte_compare_in_revisione(caricato):
    conn, conti, *_ = caricato
    mov(conn, "BX-1", conti["ing"], "2026-10-14", -5000, tipo="bonifico", descrizione="BONIFICO SEPA DISPOSTO")
    ricalcola(conn)
    from spese.ricalcolo import MOTIVO_PAYPAL
    r = righe(conn, "SELECT a.da_rivedere, a.chiave, t.stato FROM analisi a JOIN trasferimenti t ON t.mov_id = a.mov_id "
                    "WHERE a.mov_id = 'BX-1'")[0]
    assert (r["da_rivedere"], r["stato"], r["chiave"]) == (1, "terzi", None)
    # senza beneficiario la scheda dei bonifici non lo raggruppa: deve stare in "Altri movimenti"
    assert "BX-1" in [x["mov_id"] for x in an.altri_da_rivedere(conn, MOTIVO_PAYPAL)]
    # un bonifico con il beneficiario resta nella scheda dei bonifici, non qui
    mov(conn, "BX-2", conti["ing"], "2026-10-14", -5000, tipo="bonifico", controparte_nome="Luca Verdi")
    ricalcola(conn)
    assert "BX-2" not in [x["mov_id"] for x in an.altri_da_rivedere(conn, MOTIVO_PAYPAL)]


# --- M10 --------------------------------------------------------------------------------------
def test_M10_regola_con_categoria_di_entrata_su_un_uscita(caricato):
    from spese import configurazione as cfg
    from spese.catena import chiave_beneficiario
    conn, conti, *_ = caricato
    servizi.crea_regola(conn, chiave_beneficiario(None, "Luca Verdi"), ("ENTRATE", "Altre entrate"), "chiave", "esatto")
    mov(conn, "LV-1", conti["ing"], "2026-10-16", -5000, tipo="bonifico", controparte_nome="Luca Verdi")
    ricalcola(conn)
    r = righe(conn, "SELECT natura, l1, l2 FROM analisi WHERE mov_id = 'LV-1'")[0]
    sezione = cfg.tassonomia().sezione((r["l1"], r["l2"]))
    assert not (r["natura"] == "uscita" and sezione == "entrate"), r


# --- M11 --------------------------------------------------------------------------------------
def test_M11_partenza_piu_migrazione(conn):
    from spese import portafoglio as pf
    tr = importa.crea_conto(conn, "TR", "trade_republic", "conto", "personale", "TR")
    mov(conn, "m3", tr, "2025-06-01", None, tipo="movimento_titoli", isin="IE00TEST0003", quantita="3",
        nome_titolo="ETF")
    pf.imposta_partenza(conn, "IE00TEST0003", "2025-06-01", "3", 30000)
    p = [x for x in pf.calcola(conn)["posizioni"] if x["isin"] == "IE00TEST0003"][0]
    assert p["quote"] == 3, (p["quote"], p["avvisi"])


# --- M12 --------------------------------------------------------------------------------------
def test_M12_due_addebiti_ricondotti_allo_stesso_pagamento(caricato):
    conn, conti, *_ = caricato
    ppc = importa.crea_conto(conn, "PayPal", "paypal", "conto", "personale", "PayPal")
    importa.importa(conn, ppc, "Download.CSV", report(
        pp("01/10/2026", "Negozio Esempio", "Pagamento Express Checkout", "Completata", "EUR", "-9,99", "P5")))
    for mid, d in (("A-1", "2026-10-02"), ("A-2", "2026-10-20")):
        mov(conn, mid, conti["tr"], d, -999, tipo="pagamento_carta", esercente="NEGOZIO", intermediario="PAYPAL",
            descrizione="PAYPAL *NEGOZIO", chiave="NEGOZIO")
    servizi.riconduci_paypal(conn, "A-1", "B-%d-P5" % ppc)
    servizi.riconduci_paypal(conn, "A-2", "B-%d-P5" % ppc)
    ricalcola(conn)
    n = nature(conn, "A-1", "A-2")
    assert list(n.values()).count("contenitore") == 1, n
