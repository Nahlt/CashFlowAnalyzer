"""File sintetici nei quattro formati, con dati inventati e relazioni coerenti tra conti."""
from datetime import datetime

IBAN_ING = "IT60X0347501605000000000001"
IBAN_CREDEM = "IT40Y0303212800000000000002"
IBAN_TR = "DE89100123450000000000003"


def credem_csv():
    righe = [
        "Conto Corrente:;000123456", "Filiale:;FILIALE REMOTA", "Saldo al:;31/10/2026", "Saldo contabile:;1.000,00",
        "Saldo disponibile:;1.000,00", "", "Criteri utilizzati per estrazione", "Data Inizio:;01/10/2026",
        "Data Fine:;31/10/2026", "Tipo movimenti:;Tutti", "",
        "Data contabile;Data valuta;Importo;Importo orig.;Divisa orig.;Canale;Categoria;Causale ABI;Causale;Descrizione:;Note",
        "28/10/2026;26/10/2026;-25,40;-25,40;EUR;AUT;-;43;PDINT;PAGAMENTO DEBINT PAGAMENTO CARTA DI DEBITO INTERNAZIONALE "
        "123456******7890 -26/10/26-17:58 MARIO           ESSELUNGA VIALE MILANO -ITA;-",
        "27/10/2026;25/10/2026;-12,00;-12,00;EUR;AUT;-;43;PDINT;PAGAMENTO DEBINT PAGAMENTO CARTA DI DEBITO INTERNAZIONALE "
        "123456******7890 -25/10/26-19:25 MARIO BOTTEGA PINCO PALLINO -ITA;-",
        "27/10/2026;25/10/2026;-12,00;-12,00;EUR;AUT;-;43;PDINT;PAGAMENTO DEBINT PAGAMENTO CARTA DI DEBITO INTERNAZIONALE "
        "123456******7890 -25/10/26-19:25 MARIO BOTTEGA PINCO PALLINO -ITA;-",
        "20/10/2026;20/10/2026;500,00;500,00;EUR;AUT;-;48;OCIST;BON ISTANTANEO A CREDITO ROSSI MARIO Rif. ABC123456789 spese;-",
        "15/10/2026;15/10/2026;-80,00;-80,00;EUR;VIR;-;-;DIIST;BON. ISTANTANEO A DEBITO Giulia Bianchi Rif. XYZ987654321 cena;-",
        "15/10/2026;15/10/2026;-0,50;-0,50;EUR;VIR;-;-;COIST;COMM BONIFICO ISTANTANEO *;-",
        "05/10/2026;05/10/2026;-70,00;-70,00;EUR;AUT;-;50;PDPRE;PAGAMENTO PREAUTORIZZATO OCTOPUS ENERGY ITALIA SRL MANDATO "
        "QX12 CORE ABC;-",
        "03/10/2026;03/10/2026;-9,99;-9,99;EUR;AUT;-;68;OCCIN;MOVIMENTO SCONOSCIUTO;-",
    ]
    return ("\r\n".join(righe) + "\r\n").encode("utf-8")


def ing_xlsx(percorso):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MovimentiContoCorrenteArancio"
    movimenti = [
        (datetime(2026, 10, 2), datetime(2026, 10, 1), "Pagamento Carta",
         "Operazione Mastercard del 01/10/2026 alle ore 08:09 con Carta xxxxxxxxxxxx0000 Div=EUR Importo in divisa=10 / "
         "Importo in Euro=10 presso Telecom Italia S.p.A", -10.0),
        (datetime(2026, 10, 10), datetime(2026, 10, 10), "Addebito Carta Di Credito", "Estratto conto carta di credito al 20261010", -100.0),
        (datetime(2026, 10, 20), datetime(2026, 10, 20), "Bonifico In Uscita",
         "Bonifico istantaneo da voi disposto N. CPU1 A favore di Conto comune IBAN beneficiario %s Note: Ottobre" % IBAN_CREDEM, -500.0),
        (datetime(2026, 10, 22), datetime(2026, 10, 22), "Giroconto",
         "Giroconto da voi disposto N. CPU2 A favore di MARIO ROSSI IBAN beneficiario %s Note: Giro" % IBAN_TR, -1000.0),
        (datetime(2026, 10, 27), datetime(2026, 10, 27), "Accredito Stipendio/Pensione",
         "Bonifico N. 0623 BIC Ordinante CRPPIT2PXXX Codifica Ordinante IT15O0623001627000000000007 Anagrafica Ordinante "
         "ACME S.P.A. Note: STIPENDIO", 2500.0),
    ]
    saldo_ini = 1000.0
    saldo_fin = round(saldo_ini + sum(m[4] for m in movimenti), 2)
    righe = [[None], [None, "Intestazione: MARIO ROSSI"], [None, "N. di conto: 99AA11"], [None, "Codice IBAN: %s" % IBAN_ING],
             [None], [None, "Saldo iniziale: +1.000,00 EUR"], [None],
             [None, ("Saldo finale: +%s EUR" % "{:,.2f}".format(saldo_fin)).replace(",", "X").replace(".", ",").replace("X", ".")],
             [None], [None, "LISTA MOVIMENTI dal 01/10/2026 al 31/10/2026"], [None],
             [None, "DATA CONTABILE", "DATA VALUTA", "CAUSALE", "DESCRIZIONE OPERAZIONE", "IMPORTO IN EURO"]]
    for m in movimenti:
        righe.append([None] + list(m))
    for r in righe:
        ws.append(r)
    wb.save(percorso)


def ing_carta_tsv():
    righe = ["DATA OPERAZIONE\tDATA REGISTRAZIONE\tDESCRIZIONE OPERAZIONE\tIMPORTO IN EURO",
             "25/09/2026\t26/09/2026\tOperazione presso TRENITALIA MILANO\t-60",
             "02/10/2026\t03/10/2026\tOperazione presso PAYPAL *VEEPEE IT\t-40,0",
             "15/10/2026\t16/10/2026\tOperazione presso BAR CENTRALE MILANO\t-5,5"]
    return ("\n".join(righe) + "\n").encode("utf-8")


def tr_csv():
    intest = ("datetime,date,account_type,category,type,asset_class,name,symbol,shares,price,amount,fee,tax,currency,"
              "original_amount,original_currency,fx_rate,description,transaction_id,counterparty_name,counterparty_iban,"
              "payment_reference,mcc_code")
    r = [
        '"2026-10-22T09:00:00.000Z","2026-10-22","DEFAULT","CASH","CUSTOMER_INBOUND","","MARIO ROSSI","","","","1000.000000","","","EUR","","","","Versamento","t1","MARIO ROSSI","%s","",""' % IBAN_ING,
        '"2026-10-23T10:00:00.000Z","2026-10-23","DEFAULT","CASH","CARD_TRANSACTION","","LIDL MILANO","","","","-30.500000","","","EUR","","","","TR Card Transaction","t2","","","","5411"',
        '"2026-10-24T10:00:00.000Z","2026-10-24","DEFAULT","CASH","CARD_TRANSACTION","","MISTERO SNC","","","","-11.000000","","","EUR","","","","TR Card Transaction","t3","","","","5999"',
        '"2026-10-25T10:00:00.000Z","2026-10-25","DEFAULT","TRADING","BUY","FUND","ETF Mondo","IE00TEST0001","2.0000000000","50.000000","-100.00","-1.00","","EUR","","","","Buy trade IE00TEST0001 ETF Mondo, quantity: 2","t4","","","",""',
        '"2026-10-26T10:00:00.000Z","2026-10-26","DEFAULT","CASH","INTEREST_PAYMENT","","","","","","10.000000","","-2.60","EUR","","","","Interest payment for payout","t5","","","",""',
        '"2026-10-27T10:00:00.000Z","2026-10-27","DEFAULT","CASH","TRANSFER_INSTANT_OUTBOUND","","","","","","-200.000000","","","EUR","","","","Outgoing transfer for Mario Rossi","t6","","","",""',
        '"2026-10-28T10:00:00.000Z","2026-10-28","DEFAULT","CASH","TRANSFER_DIRECT_DEBIT_INBOUND","","MARIO ROSSI","","","","-49.000000","","","EUR","","","","Sepa Direct Debit transfer to Palestra Fit S.p.A. (IT00A0000000000000000000099)","t7","MARIO ROSSI","%s","",""' % IBAN_TR,
        '"2026-10-29T10:00:00.000Z","2026-10-29","DEFAULT","CASH","TAX_OPTIMIZATION","","","","","","0.000000","","-3.10","EUR","","","","Stamp Duty Tax (Portfolio)","t8","","","",""',
        '"2026-10-30T10:00:00.000Z","2026-10-30","DEFAULT","DELIVERY","MIGRATION","FUND","ETF Mondo","IE00TEST0001","-2.0000000000","50.000000","","","","EUR","","","","MIGRATION IE00TEST0001","t9","","","",""',
    ]
    return (intest + "\n" + "\n".join(r) + "\n").encode("utf-8")


def ing_csv():
    """Export CSV di ING con colonne USCITE ed ENTRATE separate (segno esplicito) e righe di saldo.
    Contiene gli stessi movimenti dell'export Excel di ing_xlsx: caricati entrambi non devono duplicarsi."""
    righe = ["DATA CONTABILE;DATA VALUTA;USCITE;ENTRATE;CAUSALE;DESCRIZIONE OPERAZIONE",
             "01/10/2026;;;+1.000,00;;Saldo iniziale"]
    movimenti = [
        ("02/10/2026", "01/10/2026", "Pagamento Carta",
         "Operazione Mastercard del 01/10/2026 alle ore 08:09 con Carta xxxxxxxxxxxx0000 Div=EUR Importo in divisa=10 / "
         "Importo in Euro=10 presso Telecom Italia S.p.A", -10.0),
        ("10/10/2026", "10/10/2026", "Addebito Carta Di Credito", "Estratto conto carta di credito al 20261010", -100.0),
        ("20/10/2026", "20/10/2026", "Bonifico In Uscita",
         "Bonifico istantaneo da voi disposto N. CPU1 A favore di Conto comune IBAN beneficiario %s Note: Ottobre" % IBAN_CREDEM, -500.0),
        ("22/10/2026", "22/10/2026", "Giroconto",
         "Giroconto da voi disposto N. CPU2 A favore di MARIO ROSSI IBAN beneficiario %s Note: Giro" % IBAN_TR, -1000.0),
        ("27/10/2026", "27/10/2026", "Accredito Stipendio/Pensione",
         "Bonifico N. 0623 BIC Ordinante CRPPIT2PXXX Codifica Ordinante IT15O0623001627000000000007 Anagrafica Ordinante "
         "ACME S.P.A. Note: STIPENDIO", 2500.0),
    ]
    euro = lambda v: ("%+.2f" % v).replace(".", ",")
    for dc, dv, caus, descr, imp in movimenti:
        righe.append("%s;%s;%s;%s;%s;%s" % (dc, dv, euro(imp) if imp < 0 else "", euro(imp) if imp > 0 else "", caus, descr))
    finale = 1000.0 + sum(m[4] for m in movimenti)
    righe.append("31/10/2026;;;%s;;Saldo finale" % ("+" + "{:,.2f}".format(finale).replace(",", "X").replace(".", ",").replace("X", ".")))
    return ("\r\n".join(righe) + "\r\n").encode("utf-8")


def iban_valido(paese, bban):
    """IBAN con le cifre di controllo giuste (gli IBAN sopra sono di fantasia e non le hanno)."""
    numero = "".join(str(int(ch, 36)) for ch in bban + paese + "00")
    return "%s%02d%s" % (paese, 98 - int(numero) % 97, bban)


IBAN_FINECO = iban_valido("IT", "X0301503200000000000001")
IBAN_DEPOSITO = iban_valido("IT", "X0306909606100000000002")
IBAN_PALESTRA = iban_valido("IT", "X0200801600000000000003")


def _a_pezzi(testo, larghezza=35):
    """Come Fineco: la descrizione completa a pezzi di lunghezza fissa uniti da uno spazio, anche in mezzo a
    parole, date e IBAN."""
    return " ".join(testo[i:i + larghezza] for i in range(0, len(testo), larghezza))


def fineco_xlsx(percorso, fino_al="31/10/2026"):
    """Lista movimenti Fineco in Excel. Con fino_al='07/11/2026' e' l'estratto successivo: stesse righe, piu' un
    bonifico nuovo, e l'addebito che prima era ancora da contabilizzare ora contabilizzato."""
    import openpyxl
    secondo = fino_al != "31/10/2026"
    movimenti = [
        (datetime(2026, 10, 5), datetime(2026, 9, 30), None, -3.95, "Canone Mensile Conto",
         "Canone Mensile Conto Settembre 2026"),
        (datetime(2026, 10, 8), datetime(2026, 10, 8), None, -0.42, "Interessi Passivi", "Interessi Passivi"),
        (datetime(2026, 10, 9), datetime(2026, 9, 30), None, -8.5, "Imposta bollo dossier titoli",
         "Addebito imposta di bollo Dossier: 99BB22"),
        (datetime(2026, 10, 12), datetime(2026, 10, 12), None, -250.0, "Bonifico Istantaneo", _a_pezzi(
            "Beneficiario: MARIO ROSSI IBAN: %s Data Inserimento: 12/10/2026 09:44:26 Canale: PHONE/VOICE P. "
            "Causale: giroconto deposito TRN: 0000000000000000000320000000IT" % IBAN_DEPOSITO)),
        # spezzato a mano in mezzo al nome e a un'etichetta
        (datetime(2026, 10, 15), datetime(2026, 10, 15), 1000.0, None, "Bonifico Istantaneo",
         "Ordinante: MARIO RO SSI Beneficiario: MARIO ROSSI Banca Ordin ante: ING Bank N.V . Data accredito: "
         "15/10/2026 Causale: Re integro spese"),
        (datetime(2026, 10, 20), datetime(2026, 10, 20), None, -49.0, "Bonifico SEPA Italia", _a_pezzi(
            "Ben: Palestra Fit S.r.l. Ins: 20/10/2026 10:37:58 Da: PHONE/VOICE P. Iban: %s TransID: "
            "0000000000000000003480320033310IT Cau: quota novembre" % IBAN_PALESTRA)),
        # IBAN con un carattere sbagliato: non si tiene
        (datetime(2026, 10, 21), datetime(2026, 10, 21), None, -10.0, "Bonifico SEPA Estero", _a_pezzi(
            "Ben: Giulia Bianchi Ins: 21/10/2026 12:01:17 Da: PHONE/VOICE P. Iban: %s TransID: "
            "00000000000000000803 20033310IT Cau: regalo" % (IBAN_PALESTRA[:-1] + "9"))),
        # nel primo estratto non ancora contabilizzato; nel secondo contabilizzato, con il giorno di contabilizzazione
        (datetime(2026, 11, 2) if secondo else datetime(2026, 10, 31), datetime(2026, 10, 31), None, -5.0,
         "Imposta bollo conto corrente", "Imposta di bollo di conto corrente del 31.10.2026",
         "Contabilizzato" if secondo else "Non contabilizzato"),
    ]
    if secondo:
        movimenti.append((datetime(2026, 11, 4), datetime(2026, 11, 4), 20.0, None, "Bonifico Istantaneo", _a_pezzi(
            "Ordinante: GIULIA BIANCHI Beneficiario: MARIO ROSSI Banca Ordinante: BANCA DI PROVA S.P.A. "
            "Data accredito: 04/11/2026 Causale: cena")))
    movimenti = [m if len(m) == 7 else m + ("Contabilizzato",) for m in movimenti]
    saldo = round(1000 + sum((m[2] or 0) + (m[3] or 0) for m in movimenti if m[6] == "Contabilizzato"), 2)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Movimenti"
    for r in (["Conto Corrente: 99BB22"], ["Intestazione Conto Corrente: MARIO ROSSI"],
              ["Periodo Dal: 01/10/2026 Al: %s" % fino_al], [None],
              ["Saldo Finale: %s" % ("{:,.2f}".format(saldo).replace(",", "X").replace(".", ",").replace("X", "."))],
              ["Nota: Il saldo finale del periodo temporale selezionato considera solo i movimenti contabilizzati."],
              [None], ["Nota: Per le carte di credito la data valuta e' quella di registrazione contabile."],
              ["Nota: Per le carte di debito la data operazione non e' quella del pagamento."], [None],
              ["Risultati Ricerca"], [None],
              ["Data_Operazione", "Data_Valuta", "Entrate", "Uscite", "Descrizione", "Descrizione_Completa", "Stato"]):
        ws.append(r)
    for m in sorted(movimenti, key=lambda m: m[0], reverse=True):
        ws.append(list(m))
    wb.save(percorso)
    return saldo
