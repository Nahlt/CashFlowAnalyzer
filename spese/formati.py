"""Formati di import configurati dall'utente (0.22.0): proposte automatiche, composizione e verifica del profilo,
lettura dei formati salvati.

Un formato dell'utente e' un profilo come quelli di config/profili/*.yaml, salvato in JSON nella tabella
`formati_import`. Si compone con la procedura guidata a partire da un file d'esempio: ogni scelta parte da una
proposta automatica che l'utente conferma o corregge. Le scritture (salvataggio, ritiro, eliminazione, causali
aggiunte) passano da servizi.py.
"""
import json
import re
from datetime import date, datetime

from . import configurazione as cfg
from . import estrattori, lettore
from .iban import verifica as verifica_iban
from .numeri import FormatoNonValido, a_decimal

PREFISSO = "utente_"

# righe del file usate per le proposte (le prime dopo l'intestazione)
RIGHE_PROVA = 400
# quota di valori che deve avere la forma giusta perche' una colonna sembri di date o di importi
QUOTA_COLONNA = 0.8

TIPI_LEGGIBILI = {
    "pagamento_carta": "Pagamento con carta",
    "addebito_sepa": "Addebito diretto (SEPA)",
    "bonifico": "Bonifico",
    "giroconto": "Giroconto",
    "stipendio": "Stipendio o pensione",
    "prelievo": "Prelievo",
    "commissione": "Commissione",
    "imposte": "Imposte e bollo",
    "pagamento_pa": "PagoPA, MAV, F24",
    "interessi": "Interessi",
    "dividendo": "Dividendo",
    "cashback": "Cashback e premi",
    "acquisto_titoli": "Acquisto di titoli",
    "vendita_titoli": "Vendita di titoli",
    "addebito_carta_credito": "Addebito della carta di credito",
    "versamento": "Versamento dal proprio conto",
    "altro": "Altro (da rivedere)",
}

TIPI_CONTO = {"conto": "Conto corrente", "carta": "Carta di credito", "broker": "Conto titoli / broker",
              "deposito": "Conto deposito"}

CAMPI_TESTA = {
    "numero": "Numero di conto",
    "iban": "IBAN del conto",
    "periodo": "Periodo (dal… al…)",
    "saldo_alla_data": "Saldo alla data",
    "data_saldo": "Data del saldo",
    "saldo_iniziale": "Saldo iniziale",
    "saldo_finale": "Saldo finale",
}

CAMPI_COLONNE = {
    "data_contabile": "Data contabile",
    "data_valuta": "Data valuta",
    "data_operazione": "Data dell'operazione",
    "importo": "Importo (con il segno)",
    "importo_uscite": "Uscite",
    "importo_entrate": "Entrate",
    "descrizione": "Descrizione",
    "causale": "Causale",
}

DECIMALI = {"virgola": "italiano", "punto": "inglese"}

FORMATI_DATA = ["%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%m-%y", "%d.%m.%y", "%Y/%m/%d",
                "%m/%d/%Y", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"]
ESEMPIO_DATA = date(2026, 10, 3)

# parole della causale -> tipo di movimento (in ordine: la prima che corrisponde vince)
PAROLE_TIPI = [
    (r"ADDEBITO\s+CARTA\s+DI\s+CREDITO|SALDO\s+CARTA|ESTRATTO\s+CONTO\s+CARTA", "addebito_carta_credito"),
    (r"COMMISSION|CANONE|SPESE\s+(?:TENUTA|BONIFICO|INVIO)|COMPETENZE|SPESE$", "commissione"),
    (r"IMPOST|BOLLO|RITENUT", "imposte"),
    (r"F24|PAGOPA|PAGO\s*PA|\bMAV\b|\bRAV\b|CBILL|BOLLETTINO", "pagamento_pa"),
    (r"PRELIEV|\bATM\b|BANCOMAT\s+PREL", "prelievo"),
    (r"STIPEND|EMOLUMENT|PENSION|SALAR", "stipendio"),
    (r"GIROCONTO|GIRO\s+CONTO", "giroconto"),
    (r"\bSDD\b|ADDEBITO\s+DIRETTO|DOMICILIAZ|DIRECT\s+DEBIT|\bRID\b|PREAUTORIZZ", "addebito_sepa"),
    (r"BONIFIC|\bSCT\b|\bSEPA\s+CREDIT|TRANSFER", "bonifico"),
    (r"\bPOS\b|CARTA|CONTACTLESS|PAGOBANCOMAT|APPLE\s*PAY|GOOGLE\s*PAY|CARD", "pagamento_carta"),
    (r"DIVIDEND|CEDOL", "dividendo"),
    (r"INTERESS", "interessi"),
    (r"CASHBACK|PREMIO", "cashback"),
]


# ---------------------------------------------------------------------------
# Lettura delle celle
# ---------------------------------------------------------------------------
def _vuoto(v):
    return v is None or (isinstance(v, str) and not v.strip())


def testo(v) -> str:
    return "" if _vuoto(v) else lettore._testo_cella(v)


def e_data(v) -> bool:
    if isinstance(v, (datetime, date)):
        return True
    if _vuoto(v) or not isinstance(v, str):
        return False
    s = v.strip()
    for fmt in FORMATI_DATA:
        try:
            datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


def e_importo(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if _vuoto(v) or not isinstance(v, str) or e_data(v):
        return False
    s = re.sub(r"\s*(?:€|EUR)\s*", "", v.strip())
    if not re.search(r"\d", s):
        return False
    for fmt in ("italiano", "inglese"):
        try:
            a_decimal(s, fmt)
            return True
        except FormatoNonValido:
            continue
    return False


# ---------------------------------------------------------------------------
# Proposte automatiche
# ---------------------------------------------------------------------------
def proponi_intestazione(righe):
    """Indice (da 0) della riga con i nomi delle colonne: la prima con almeno 3 celle di solo testo (niente date
    ne' importi) seguita da righe con una data e un importo. None se nessuna riga lo sembra."""
    for i, r in enumerate(righe[:lettore.RIGHE_TESTA]):
        celle = [c for c in r if not _vuoto(c)]
        if len(celle) < 3 or any(e_data(c) or e_importo(c) for c in celle):
            continue
        seguenti = [x for x in righe[i + 1:i + 30] if any(not _vuoto(c) for c in x)][:5]
        if not seguenti:
            continue
        buone = sum(1 for x in seguenti if any(e_data(c) for c in x) and any(e_importo(c) for c in x))
        if buone >= min(3, len(seguenti)):
            return i
    return None


def intestazione(righe, h):
    return [testo(c) for c in righe[h]] if h is not None and h < len(righe) else []


def righe_dati(righe, h):
    """Righe dopo l'intestazione con almeno una cella piena (al piu' RIGHE_PROVA)."""
    return [r for r in righe[h + 1:] if any(not _vuoto(c) for c in r)][:RIGHE_PROVA]


def valori(righe, h, nome):
    """Valori non vuoti della colonna `nome`."""
    intest = intestazione(righe, h)
    if nome not in intest:
        return []
    i = intest.index(nome)
    return [r[i] for r in righe_dati(righe, h) if i < len(r) and not _vuoto(r[i])]


def _norma(nome):
    return re.sub(r"[\s_]+", " ", (nome or "").lower()).strip()


def _quota(vals, prova):
    return sum(1 for v in vals if prova(v)) / len(vals) if vals else 0


def proponi_colonne(righe, h):
    """{campo: nome della colonna o None} per data contabile, valuta, data dell'operazione, importo (una colonna
    con il segno o uscite + entrate), descrizione e causale, dal nome della colonna e dal contenuto."""
    nomi = [n for n in intestazione(righe, h) if n]
    vals = {n: valori(righe, h, n) for n in nomi}
    date_col = [n for n in nomi if vals[n] and _quota(vals[n], e_data) >= QUOTA_COLONNA]
    num_col = [n for n in nomi if n not in date_col and vals[n] and _quota(vals[n], e_importo) >= QUOTA_COLONNA
               and not re.search(r"saldo|balance|progressiv", _norma(n))]
    testo_col = [n for n in nomi if n not in date_col and n not in num_col]
    out = dict.fromkeys(CAMPI_COLONNE)

    def rango_data(n):
        k = _norma(n)
        if re.search(r"valut", k):
            return 9
        if re.search(r"contab|registr|booking", k):
            return 0
        if k in ("data", "date"):
            return 1
        if re.search(r"operaz|transaz", k):
            return 2
        return 3
    if date_col:
        ordinate = sorted(date_col, key=lambda n: (rango_data(n), nomi.index(n)))
        out["data_contabile"] = ordinate[0]
        out["data_valuta"] = next((n for n in ordinate[1:] if "valut" in _norma(n)), None)
        out["data_operazione"] = next((n for n in ordinate[1:] if re.search(r"operaz|transaz", _norma(n))), None)
    uscite = next((n for n in num_col if re.search(r"uscit|dare|addebit|debit|prelev|spes", _norma(n))), None)
    entrate = next((n for n in num_col if re.search(r"entrat|avere|accredit|credit|versam|incass", _norma(n))), None)
    if uscite and entrate and uscite != entrate:
        out["importo_uscite"], out["importo_entrate"] = uscite, entrate
    elif num_col:
        out["importo"] = next((n for n in num_col if re.search(r"importo|amount|ammontare|valore|euro|\beur\b",
                                                              _norma(n))), num_col[0])

    def lunghezza(n):
        v = vals[n]
        return sum(len(testo(x)) for x in v) / len(v) if v else 0
    if testo_col:
        chiave = [n for n in testo_col if re.search(r"descriz|dettagl|descr|memo|note|description|causale descr",
                                                    _norma(n))]
        out["descrizione"] = max(chiave or testo_col, key=lunghezza)
        causali = [n for n in testo_col if n != out["descrizione"] and vals[n]
                   and len({testo(x) for x in vals[n]}) <= max(20, len(vals[n]) // 2)]
        out["causale"] = next((n for n in causali if re.search(r"causal|tipo|codice|categor|operaz", _norma(n))),
                              None)
    return out


def prova_date(vals):
    """[(formato, letti, totale)] dal piu' adatto; 'excel' se le celle sono gia' date."""
    tot = len(vals)
    if tot and all(isinstance(v, (datetime, date)) for v in vals):
        return [("excel", tot, tot)]
    out = []
    for fmt in FORMATI_DATA:
        letti = 0
        for v in vals:
            if isinstance(v, (datetime, date)):
                letti += 1
                continue
            try:
                datetime.strptime(str(v).strip(), fmt)
                letti += 1
            except ValueError:
                pass
        out.append((fmt, letti, tot))
    return sorted(out, key=lambda x: -x[1])


def date_ambigue(prove):
    """True se piu' formati leggono tutte le date dando giorni diversi (es. 03/04/2026: 3 aprile o 4 marzo)."""
    pieni = [f for f, letti, tot in prove if tot and letti == tot and f != "excel"]
    ordini = {"gm" if f.index("%d") < f.index("%m") else "mg" for f in pieni if "%d" in f and "%m" in f}
    return len(ordini) > 1


def esempio_data(fmt):
    return "gg/mm/aaaa (data gia' nella cella)" if fmt == "excel" else ESEMPIO_DATA.strftime(fmt)


def prova_decimali(vals):
    """[(decimali, letti, totale)] per 'virgola' e 'punto', dal piu' adatto (a parita', la virgola)."""
    out = []
    for nome, fmt in DECIMALI.items():
        letti = 0
        for v in vals:
            try:
                a_decimal(re.sub(r"\s*(?:€|EUR)\s*", "", v) if isinstance(v, str) else v, fmt)
                letti += 1
            except FormatoNonValido:
                pass
        out.append((nome, letti, len(vals)))
    return sorted(out, key=lambda x: -x[1])


def decimali_ambigui(vals):
    """True se tutti i valori si leggono in entrambi i modi e almeno uno cambia (es. '1.234': mille o uno)."""
    stringhe = [v for v in vals if isinstance(v, str)]
    if not stringhe:
        return False
    for v in stringhe:
        try:
            if a_decimal(v, "italiano") != a_decimal(v, "inglese"):
                return True
        except FormatoNonValido:
            return False
    return False


def proponi_tipo(causale):
    """Tipo di movimento dal nome della causale, oppure None (da scegliere)."""
    t = (causale or "").upper()
    for rx, tipo in PAROLE_TIPI:
        if re.search(rx, t):
            return tipo
    return None


def causali_del_file(righe, h, colonna, colonna_descr=None):
    """[(causale, movimenti, esempio di descrizione)] in ordine di frequenza."""
    intest = intestazione(righe, h)
    if colonna not in intest:
        return []
    i = intest.index(colonna)
    j = intest.index(colonna_descr) if colonna_descr in intest else None
    conta, esempi = {}, {}
    for r in righe_dati(righe, h):
        c = testo(r[i]) if i < len(r) else ""
        if not c:
            continue
        conta[c] = conta.get(c, 0) + 1
        if c not in esempi and j is not None and j < len(r):
            esempi[c] = testo(r[j])
    return sorted(((c, n, esempi.get(c, "")) for c, n in conta.items()), key=lambda x: (-x[1], x[0]))


def testa(righe, h):
    """[(numero di riga da 1, etichetta, valore)] delle righe sopra l'intestazione (non vuote)."""
    out = []
    for i, r in enumerate(righe[:h or 0]):
        coppia = lettore.coppia_testa(r)
        if coppia:
            out.append((i + 1, coppia[0], coppia[1]))
    return out


def proponi_campo_testa(etichetta, valore):
    """Significato proposto per una riga sopra l'intestazione, oppure None."""
    e = (etichetta or "").lower()
    v = valore or ""
    date_trovate = re.findall(lettore.DATA_IN_TESTO, v)
    compatto = re.sub(r"\s+", "", v).upper()
    if "iban" in e or (re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compatto) and not verifica_iban(compatto)):
        return "iban"
    if len(date_trovate) >= 2 or "periodo" in e:
        return "periodo" if date_trovate else None
    if "saldo" in e and e_importo(re.sub(r"[^\d,.\-+]", "", v)):
        if "inizial" in e:
            return "saldo_iniziale"
        if "final" in e and not re.search(lettore.DATA_IN_TESTO, e):
            return "saldo_finale"
        return "saldo_alla_data"
    if ("data" in e and "saldo" in e) and date_trovate:
        return "data_saldo"
    if re.search(r"\bconto\b|rapporto|numero", e) and re.search(r"\d", v):
        return "numero"
    return None


def _generalizza(t):
    """Espressione che riconosce un'etichetta anche il mese dopo: le date diventano un gruppo (la prima si chiama
    `data`), le altre cifre \\d+, gli spazi \\s+."""
    pezzi, pos, nominata = [], 0, False

    def fisso(s):
        out = []
        for p in re.split(r"(\d+|\s+)", s):
            if not p:
                continue
            out.append(r"\d+" if p.isdigit() else r"\s+" if p.isspace() else re.escape(p))
        return "".join(out)
    for m in re.finditer(lettore.DATA_IN_TESTO, t):
        pezzi.append(fisso(t[pos:m.start()]))
        pezzi.append("(?P<data>%s)" % lettore.DATA_IN_TESTO if not nominata else "(?:%s)" % lettore.DATA_IN_TESTO)
        nominata, pos = True, m.end()
    pezzi.append(fisso(t[pos:]))
    return "".join(pezzi)


def regola_testa(etichetta, valore, campo):
    """Regola `metadati_regole` per una riga della testa: l'etichetta (o il testo della riga, se e' una cella sola
    senza etichetta) generalizzata."""
    return {"campo": campo, "etichetta": _generalizza(etichetta if etichetta is not None else valore)}


# ---------------------------------------------------------------------------
# Scelte della procedura -> profilo
# ---------------------------------------------------------------------------
def leggi_file(nome_file, dati, separatore=None, codifica=None, foglio=0):
    """(righe, formato, separatore, codifica) del file con le scelte indicate (indovinate se mancano)."""
    if lettore.e_excel(nome_file):
        righe, formato, _ = lettore.leggi_grezzo(nome_file, dati, foglio=foglio or 0)
        return righe, formato, None, None
    testo_file, cod = lettore.decodifica(dati, codifica)
    sep = separatore or lettore.indovina_separatore(testo_file)
    righe, formato, _ = lettore.leggi_grezzo(nome_file, dati, separatore=sep, codifica=cod)
    return righe, formato, sep, cod


def proponi_scelte(nome_file, dati, separatore=None, codifica=None, foglio=None, riga=None):
    """Tutte le scelte proposte per un file: il punto di partenza della procedura (e di ogni passo rifatto dopo
    aver cambiato separatore, codifica, foglio o riga d'intestazione). Senza foglio, per un Excel si propone il
    primo foglio in cui si trova un'intestazione."""
    if foglio is None:
        foglio = 0
        if lettore.e_excel(nome_file) and riga is None:
            for i in range(len(lettore.fogli_excel(dati))):
                if proponi_intestazione(leggi_file(nome_file, dati, foglio=i)[0]) is not None:
                    foglio = i
                    break
    righe, formato, sep, cod = leggi_file(nome_file, dati, separatore, codifica, foglio)
    h = riga if riga is not None else proponi_intestazione(righe)
    scelte = {"separatore": sep, "codifica": cod, "foglio": foglio or 0, "riga": h, "formato": formato,
              "colonne": dict.fromkeys(CAMPI_COLONNE), "modo_importo": "una", "date": "%d/%m/%Y",
              "decimali": "virgola", "inverti_segno": False, "tipi": {}, "tipo_altre": "altro",
              "tipo_uscite": "altro", "tipo_entrate": "altro", "estrattori": {}, "testa": {},
              "nome": "", "banca": "", "tipo_conto": "conto"}
    if h is None:
        return scelte
    col = proponi_colonne(righe, h)
    scelte["colonne"] = col
    scelte["modo_importo"] = "due" if col["importo_uscite"] and col["importo_entrate"] else "una"
    if col["data_contabile"]:
        scelte["date"] = prova_date(valori(righe, h, col["data_contabile"]))[0][0]
    importi = [v for c in ("importo", "importo_uscite", "importo_entrate") if col[c] for v in valori(righe, h, col[c])]
    if importi:
        scelte["decimali"] = prova_decimali(importi)[0][0]
    if col["causale"]:
        scelte["tipi"] = {c: proponi_tipo(c) for c, _, _ in causali_del_file(righe, h, col["causale"])}
    scelte["testa"] = {n: proponi_campo_testa(e, v) for n, e, v in testa(righe, h)}
    scelte["estrattori"] = proponi_estrattori(nome_file, dati, scelte)
    return scelte


def proponi_estrattori(nome_file, dati, scelte, titolari=()):
    """Regola proposta per ogni informazione, sulla colonna della descrizione."""
    descr = scelte["colonne"].get("descrizione")
    out = {}
    if not descr:
        return out
    righe = righe_per_estrattori(nome_file, dati, scelte, descr, titolari)
    for info in estrattori.INFORMAZIONI:
        out[info] = {"colonna": descr, "regole": estrattori.proposta(info, righe)}
    return out


def righe_per_estrattori(nome_file, dati, scelte, colonna, titolari=()):
    """[(tipo, testo della colonna, data contabile)] delle righe lette con le scelte attuali (senza estrattori)."""
    prova_scelte = dict(scelte, estrattori={})
    try:
        fl = prova(nome_file, dati, prova_scelte, titolari)
    except Exception:
        return []
    intest = fl.intestazione
    if colonna not in intest:
        return []
    i = intest.index(colonna)
    return [(m["tipo"], testo(m["grezzo"][i]) if i < len(m["grezzo"]) else "", m.get("data_contabile"))
            for m in fl.movimenti]


def componi(scelte, righe, nome_file="") -> dict:
    """Profilo (dizionario come i YAML dei formati dell'app) dalle scelte della procedura."""
    h = scelte["riga"]
    intest = intestazione(righe, h)
    col = {k: v for k, v in (scelte.get("colonne") or {}).items() if v}
    if scelte.get("modo_importo") == "due":
        col.pop("importo", None)
    else:
        col.pop("importo_uscite", None)
        col.pop("importo_entrate", None)
    causale = col.pop("causale", None)
    formato = "xlsx" if lettore.e_excel(nome_file) else (scelte.get("formato") or "csv")
    p = {
        "nome": (scelte.get("nome") or "").strip(),
        "banca": (scelte.get("banca") or "").strip() or None,
        "tipo_conto": scelte.get("tipo_conto") or "conto",
        "formato": formato,
        "firma": [n for n in dict.fromkeys(intest) if n],
        "numeri": DECIMALI.get(scelte.get("decimali"), "italiano"),
        "date": scelte.get("date") or "%d/%m/%Y",
        "colonne": col,
    }
    if formato == "csv":
        p["separatore"] = scelte.get("separatore") or ";"
        p["codifica"] = scelte.get("codifica") or "utf-8-sig"
    elif scelte.get("foglio"):
        p["foglio"] = scelte["foglio"]
    if scelte.get("inverti_segno") and scelte.get("modo_importo") != "due":
        p["inverti_segno"] = True
    if causale:
        p["codice"] = [causale]
        tipi = {c: t for c, t in (scelte.get("tipi") or {}).items() if t}
        tipi["*"] = scelte.get("tipo_altre") or "altro"
        p["tipi"] = tipi
    else:
        tu, te = scelte.get("tipo_uscite") or "altro", scelte.get("tipo_entrate") or "altro"
        p["tipi"] = {"*": tu if tu == te else {"uscita": tu, "entrata": te}}
    est = {i: {"colonna": e["colonna"], "regole": estrattori.nomi_regole(e)}
           for i, e in (scelte.get("estrattori") or {}).items()
           if e and e.get("colonna") and estrattori.nomi_regole(e)}
    if est:
        p["estrattori"] = est
    p["esercente_da_descrizione"] = list(estrattori.TIPI_SPESA)
    p["titolari_davanti_esercente"] = list(estrattori.TIPI_SPESA)
    regole = []
    righe_testa = {n: (e, v) for n, e, v in testa(righe, h)}
    for n, campo in (scelte.get("testa") or {}).items():
        if campo and int(n) in righe_testa:
            e, v = righe_testa[int(n)]
            regole.append(regola_testa(e, v, campo))
    if regole:
        p["metadati_regole"] = regole
        campi = {r["campo"] for r in regole}
        if "periodo" in campi and "saldo_alla_data" in campi and "data_saldo" not in campi:
            p["data_saldo_da"] = "periodo_a"        # un saldo senza data vale alla fine del periodo
    return p


def prova(nome_file, dati, scelte, titolari=()):
    """Lettura del file con il formato che nasce dalle scelte (lettore.FileLetto)."""
    righe, _, _, _ = leggi_file(nome_file, dati, scelte.get("separatore"), scelte.get("codifica"), scelte.get("foglio"))
    p = componi(scelte, righe, nome_file)
    p.setdefault("id", "bozza")
    p["nome"] = p["nome"] or "formato in prova"
    p["utente"] = True
    return lettore.leggi(nome_file, titolari, dati, profilo=p, riga_intestazione_scelta=scelte["riga"])


def valida(profilo) -> list:
    """Problemi che impediscono di salvare il formato (lista vuota se va bene)."""
    errori = []
    col = profilo.get("colonne") or {}
    firma = set(profilo.get("firma") or [])
    if not (profilo.get("nome") or "").strip():
        errori.append("manca il nome del formato")
    if len(firma) < 2:
        errori.append("l'intestazione deve avere almeno due colonne")
    if not col.get("data_contabile"):
        errori.append("manca la colonna della data contabile")
    if not col.get("descrizione"):
        errori.append("manca la colonna della descrizione")
    if not col.get("importo") and not (col.get("importo_uscite") and col.get("importo_entrate")):
        errori.append("manca la colonna dell'importo (o le due colonne uscite ed entrate)")
    for campo, nome in col.items():
        nome = nome["colonna"] if isinstance(nome, dict) else nome
        if nome not in firma:
            errori.append("la colonna \"%s\" non e' nell'intestazione" % nome)
    for c in profilo.get("codice") or []:
        if c not in firma:
            errori.append("la colonna \"%s\" non e' nell'intestazione" % c)
    if profilo.get("numeri") not in ("italiano", "inglese", "excel", "punto"):
        errori.append("formato dei numeri non valido")
    validi = set(cfg.tipi_movimento()["tipi"])
    for t in (profilo.get("tipi") or {}).values():
        for x in (t.values() if isinstance(t, dict) else [t]):
            if x not in validi:
                errori.append("tipo di movimento sconosciuto: %s" % x)
    for info, e in (profilo.get("estrattori") or {}).items():
        for nome in estrattori.nomi_regole(e):
            if nome not in estrattori.PER_NOME or estrattori.PER_NOME[nome].info != info:
                errori.append("regola sconosciuta per %s: %s" % (info, nome))
        if e.get("colonna") not in firma:
            errori.append("la colonna \"%s\" non e' nell'intestazione" % e.get("colonna"))
    for r in profilo.get("metadati_regole") or []:
        try:
            re.compile(r["etichetta"])
        except re.error:
            errori.append("regola non valida per la riga %s" % r.get("campo"))
    if profilo.get("tipo_conto") not in TIPI_CONTO:
        errori.append("tipo di conto non valido")
    return errori


def conflitti(profilo, escludi=None):
    """Formati attivi (dell'app o dell'utente) che riconoscerebbero gli stessi file: la firma dell'uno contiene
    quella dell'altro. [(id, nome)]"""
    firma = set(profilo.get("firma") or [])
    out = []
    for p in lettore.attivi():
        if p.get("id") in (escludi, profilo.get("id")):
            continue
        altra = set(p.get("firma") or [])
        if altra and firma and (altra <= firma or firma <= altra):
            out.append((p["id"], p.get("nome", p["id"])))
    return out


# ---------------------------------------------------------------------------
# Formati salvati
# ---------------------------------------------------------------------------
def carica(conn):
    """Legge formati dell'utente e causali aggiunte e li rende visibili a lettore e ricalcolo (cfg.profili)."""
    formati = {}
    for r in conn.execute("SELECT id, nome, profilo, stato, sostituisce FROM formati_import ORDER BY id"):
        p = json.loads(r["profilo"])
        p.update(id=r["id"], nome=r["nome"], utente=True, stato=r["stato"], sostituisce=r["sostituisce"])
        formati[r["id"]] = p
    aggiunte = {}
    for r in conn.execute("SELECT profilo, causale, tipo FROM causali_aggiunte"):
        aggiunte.setdefault(r["profilo"], {})[r["causale"]] = r["tipo"]
    cfg.imposta_formati_utente(formati, aggiunte)


def nuovo_id(conn):
    n = 0
    for (fid,) in conn.execute("SELECT id FROM formati_import"):
        m = re.fullmatch(PREFISSO + r"(\d+)", fid)
        if m:
            n = max(n, int(m.group(1)))
    return "%s%d" % (PREFISSO, n + 1)


def uso(conn, profilo_id):
    """(import, conti) che usano il formato."""
    imp = conn.execute("SELECT count(*) FROM import_file WHERE profilo = ?", (profilo_id,)).fetchone()[0]
    conti = conn.execute("SELECT count(DISTINCT conto_id) FROM import_file WHERE profilo = ?",
                         (profilo_id,)).fetchone()[0]
    return imp, conti


def elenco(conn):
    """Formati dell'utente con stato e uso, dal piu' recente."""
    out = []
    for r in conn.execute("SELECT id, nome, profilo, stato, sostituisce, creato_il, ritirato_il FROM formati_import "
                          "ORDER BY creato_il DESC, id DESC"):
        p = json.loads(r["profilo"])
        imp, conti = uso(conn, r["id"])
        out.append({"id": r["id"], "nome": r["nome"], "stato": r["stato"], "sostituisce": r["sostituisce"],
                    "creato_il": r["creato_il"], "ritirato_il": r["ritirato_il"], "tipo_conto": p.get("tipo_conto"),
                    "banca": p.get("banca"), "import": imp, "conti": conti, "profilo": p})
    return out


def causali_senza_tipo(conn, profilo_id):
    """[(causale, movimenti, esempio)] delle causali che il formato non conosce, viste negli import: oggi quei
    movimenti sono di tipo 'altro'. Escluse quelle gia' aggiunte."""
    gia = {r[0] for r in conn.execute("SELECT causale FROM causali_aggiunte WHERE profilo = ?", (profilo_id,))}
    righe = conn.execute(
        "SELECT m.codice, count(*), min(m.descrizione) FROM movimenti m JOIN import_file f ON f.id = m.import_id "
        "WHERE f.profilo = ? AND m.tipo = 'altro' AND coalesce(m.codice, '') <> '' GROUP BY m.codice "
        "ORDER BY count(*) DESC, m.codice", (profilo_id,)).fetchall()
    return [(c, n, e or "") for c, n, e in righe if c not in gia]


def causali_aggiunte(conn, profilo_id):
    """[(causale, tipo, aggiunta_il, superata)]: superata se ora la traduce il formato stesso (versione nuova
    dell'app), che vince."""
    base = cfg.profili_app().get(profilo_id) or (cfg.profili().get(profilo_id) or {})
    tipi = base.get("tipi") or {}
    if base.get("utente"):
        tipi = {k: v for k, v in tipi.items() if k not in (base.get("causali_aggiunte") or [])}
    return [(c, t, d, c in tipi) for c, t, d in conn.execute(
        "SELECT causale, tipo, aggiunta_il FROM causali_aggiunte WHERE profilo = ? ORDER BY causale", (profilo_id,))]


def scelte_da_profilo(profilo, nome_file, dati):
    """Scelte di partenza per una nuova versione di un formato: quelle del formato dove valgono ancora per il
    file d'esempio (colonne presenti nell'intestazione), le proposte automatiche per il resto."""
    excel = lettore.e_excel(nome_file)
    s = proponi_scelte(nome_file, dati, None if excel else profilo.get("separatore"),
                       None if excel else profilo.get("codifica"), profilo.get("foglio") if excel else None)
    if s["riga"] is not None:
        righe = leggi_file(nome_file, dati, s["separatore"], s["codifica"], s["foglio"])[0]
        h = riga_intestazione_firma(profilo, righe)
        if h is not None and h != s["riga"]:
            s = proponi_scelte(nome_file, dati, s["separatore"], s["codifica"], s["foglio"], riga=h)
    intest = set(intestazione(leggi_file(nome_file, dati, s["separatore"], s["codifica"], s["foglio"])[0],
                              s["riga"])) if s["riga"] is not None else set()
    col = dict(profilo.get("colonne") or {})
    if intest and all((v["colonna"] if isinstance(v, dict) else v) in intest for v in col.values()):
        nuove = dict.fromkeys(CAMPI_COLONNE)
        nuove.update({k: (v["colonna"] if isinstance(v, dict) else v) for k, v in col.items() if k in nuove})
        cod = (profilo.get("codice") or [None])[0]
        nuove["causale"] = cod if cod in intest else s["colonne"].get("causale")
        s["colonne"] = nuove
        s["modo_importo"] = "due" if nuove["importo_uscite"] and nuove["importo_entrate"] else "una"
    if profilo.get("date"):
        s["date"] = profilo["date"]
    s["decimali"] = {v: k for k, v in DECIMALI.items()}.get(profilo.get("numeri"), s["decimali"])
    s["inverti_segno"] = bool(profilo.get("inverti_segno"))
    tipi = {k: v for k, v in (profilo.get("tipi") or {}).items()}
    altre = tipi.pop("*", "altro")
    if isinstance(altre, dict):
        s["tipo_uscite"], s["tipo_entrate"] = altre.get("uscita", "altro"), altre.get("entrata", "altro")
    else:
        s["tipo_altre"] = s["tipo_uscite"] = s["tipo_entrate"] = altre
    for c in list(s["tipi"]):
        if c in tipi and not isinstance(tipi[c], dict):
            s["tipi"][c] = tipi[c]
    for info, e in (profilo.get("estrattori") or {}).items():
        if e.get("colonna") in intest:
            s["estrattori"][info] = {"colonna": e["colonna"], "regole": estrattori.nomi_regole(e)}
    s.update(nome=profilo.get("nome", ""), banca=profilo.get("banca") or "", tipo_conto=profilo.get("tipo_conto",
                                                                                                    "conto"))
    return s


def riga_intestazione_firma(profilo, righe):
    return lettore.riga_intestazione(profilo, righe) if profilo.get("firma") else None
