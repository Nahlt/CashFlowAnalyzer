"""Report delle attivita' PayPal (Attivita' > Scarica report, formato CSV).

Il report ha una riga per ogni evento del conto PayPal e piu' righe per la stessa operazione, legate dal
"Codice transazione di riferimento":
- il pagamento all'esercente (Impatto sul saldo: Addebito), con il nome vero dell'esercente;
- la ricarica che lo finanzia, se il saldo non basta: "Bonifico bancario sul conto PayPal" (dal conto in banca)
  o "Deposito con carta generico" (dalla carta), Accredito, spesso "In sospeso";
- per i pagamenti in valuta, due righe "Conversione di valuta generica" (euro in uscita, valuta in entrata);
- righe "Memo" (autorizzazioni, blocchi) che non cambiano il saldo.

Qui si ricavano i movimenti del conto PayPal: pagamenti e rimborsi come spese dell'esercente (in euro, con la
valuta originale accanto), ricariche, prelievi verso la banca e pagamenti da/a persone. L'addebito della stessa
spesa sul conto in banca o sulla carta ("PAYPAL *...") viene abbinato alla ricarica nel ricalcolo, cosi' la
spesa si conta una volta sola, con il nome dell'esercente preso da PayPal.

Le colonne e le parole dei tipi stanno nei profili config/profili/paypal*.yaml.
"""
import re
from difflib import SequenceMatcher

from .chiavi import chiave
from .numeri import FormatoNonValido, a_centesimi, a_data

# parole che non distinguono un esercente: forme societarie, domini, sigle di paese, PayPal stesso
_VUOTE = {"SA", "SAS", "SARL", "SRL", "SRLS", "SPA", "SNC", "SE", "LTD", "LIMITED", "GMBH", "AG", "INC", "LLC", "BV",
          "NV", "SL", "SLU", "CO", "AB", "AS", "OY", "UAB", "COM", "NET", "ORG", "WWW", "EU", "EUROPE", "IT", "FR", "DE",
          "ES", "NL", "IE", "UK", "LU", "US", "PAYPAL", "PP", "THE", "INTERNATIONAL", "S", "A", "R", "L", "P"}


def _parole_nome(s):
    s = (s or "").upper()
    return [w for w in re.split(r"[^A-Z0-9À-Ü]+", s) if w and not w.isdigit() and w not in _VUOTE]


def somiglianza(a, b):
    """Somiglianza (0-1) tra il nome di un addebito PayPal in banca ("PAYPAL *VEEPEE IT") e il nome
    dell'esercente nel report ("VENTE PRIVEE.COM SA"). Media tra sequenza comune e coppie di lettere in comune,
    alzata quando un nome contiene l'altro o quando una parola coincide quasi del tutto."""
    pa, pb = _parole_nome(a), _parole_nome(b)
    if not pa or not pb:
        return 0.0
    sa, sb = "".join(pa), "".join(pb)
    ca = {sa[i:i + 2] for i in range(len(sa) - 1)}
    cb = {sb[i:i + 2] for i in range(len(sb) - 1)}
    dice = 2 * len(ca & cb) / (len(ca) + len(cb)) if ca and cb else 0.0
    r = (SequenceMatcher(None, sa, sb).ratio() + dice) / 2
    corto, lungo = sorted((sa, sb), key=len)
    if len(corto) >= 4 and corto in lungo:
        r = max(r, 0.9)
    lunghe_a, lunghe_b = [x for x in pa if len(x) >= 3], [y for y in pb if len(y) >= 3]
    if lunghe_a and lunghe_b:
        parola = max(SequenceMatcher(None, x, y).ratio() for x in lunghe_a for y in lunghe_b)
        if parola >= 0.8:
            r = max(r, 0.9 * parola)
    return round(min(r, 1.0), 2)


# parole degli addebiti SEPA di PayPal e delle causali bancarie: non dicono nulla dell'esercente
_GENERICHE = {"EUROPE", "ET", "CIE", "SCA", "SDD", "SEPA", "DIRECT", "DEBIT", "ADDEBITO", "ADDEBI", "ADDEB",
              "MANDATO", "RIF", "BONIFICO", "PAGAMENTO", "LUXEMBOURG", "TRANSFER", "PAYMENT", "INSTANT", "SDDCORE",
              "CORE", "CID", "CREDITOR", "IDENTIFIER"}


def nome_utile(s):
    """Il nome di un addebito dice qualcosa dell'esercente? No per "SDD A : PAYPAL EUROPE S.A.R.L. ET CIE..."."""
    return any(len(w) >= 3 and sum(ch.isalpha() for ch in w) >= 3 and w not in _GENERICHE for w in _parole_nome(s))


def livello_somiglianza(x):
    return "alta" if x >= 0.7 else ("media" if x >= 0.35 else "bassa")


def _ruolo(tipo, profilo):
    """Ruolo di una riga secondo le parole del suo Tipo: la prima lista che contiene una parola vince."""
    t = (tipo or "").strip().lower()
    for ruolo in ("conversione", "ignora", "ricarica", "prelievo", "rimborso", "esercente"):
        if any(p.lower() in t for p in profilo["tipi"].get(ruolo, [])):
            return ruolo
    return None


def leggi(fl, titolari=()):
    """Riempie fl.movimenti e fl.scartate a partire dalle righe del report."""
    p = fl.profilo
    righe, h = fl.righe, fl.riga_intestazione
    idx = {n: i for i, n in enumerate(fl.intestazione) if n}
    col = p["colonne"]
    numeri = p["numeri"]

    def val(r, campo):
        nome = col.get(campo)
        i = idx.get(nome) if nome else None
        v = r[i] if i is not None and i < len(r) else None
        return v.strip() if isinstance(v, str) else v

    impatto = {k: {x.lower() for x in v} for k, v in p["impatto"].items()}
    esclusi = {x.lower() for x in p.get("stati_esclusi", [])}
    euro = p.get("valuta_conto", "EUR")
    letti, conversioni = [], {}
    for n, r in enumerate(righe[h + 1:], start=h + 2):
        if not any(c not in (None, "") and str(c).strip() for c in r):
            continue
        tipo = val(r, "tipo") or ""
        imp = (val(r, "impatto") or "").lower()
        stato = (val(r, "stato") or "").lower()
        if imp in impatto["memo"]:
            continue                                   # autorizzazioni e blocchi: il saldo non cambia
        if stato in esclusi:
            fl.scartate.append((n, "operazione %s" % (val(r, "stato") or "").lower()))
            continue
        ruolo = _ruolo(tipo, p)
        if ruolo == "ignora":
            continue
        try:
            lordo = a_centesimi(val(r, "lordo"), numeri)
            tariffa = a_centesimi(val(r, "tariffa"), numeri) or 0
            d, _ = a_data(val(r, "data"), p["date"])
        except (FormatoNonValido, ValueError) as e:
            fl.scartate.append((n, str(e)))
            continue
        if lordo is None or not d:
            fl.scartate.append((n, "importo o data mancante"))
            continue
        # il segno lo da' l'impatto sul saldo (alcune versioni del report scrivono gli importi senza segno)
        if imp in impatto["addebito"]:
            lordo, tariffa = -abs(lordo), -abs(tariffa)
        elif imp in impatto["accredito"]:
            lordo = abs(lordo)
        riga = {"n": n, "r": r, "tipo_pp": tipo, "ruolo": ruolo, "stato": val(r, "stato"), "lordo": lordo,
                "tariffa": tariffa, "valuta": (val(r, "valuta") or euro).upper(), "data": d,
                "ora": (val(r, "ora") or "")[:5] or None, "nome": val(r, "nome") or "", "id": val(r, "id") or "",
                "rif": val(r, "rif") or "", "oggetto": val(r, "oggetto") or val(r, "titolo") or ""}
        if ruolo == "conversione":
            conversioni.setdefault(riga["rif"], []).append(riga)
        else:
            letti.append(riga)

    for x in letti:
        importo, orig, divisa = x["lordo"], None, None
        if x["valuta"] != euro:
            conv = [c for c in conversioni.get(x["id"], []) if c["valuta"] == euro]
            if not conv:
                fl.scartate.append((x["n"], "importo in %s senza conversione in euro (pagato con un saldo in "
                                            "valuta): non entra nelle analisi" % x["valuta"]))
                continue
            orig, divisa = "%d.%02d" % divmod(abs(x["lordo"]), 100), x["valuta"]
            importo = conv[0]["lordo"]
            x["tariffa"] = 0                           # la tariffa e' in valuta: e' gia' dentro la conversione
        ruolo = x["ruolo"]
        m = {"n_riga": x["n"], "codice": x["tipo_pp"], "grezzo": [None if c is None else str(c) for c in x["r"]],
             "data": x["data"], "data_contabile": x["data"], "data_valuta": None, "data_registrazione": None,
             "ora": x["ora"], "importo_cent": importo, "fee_cent": x["tariffa"], "tax_cent": 0,
             "importo_orig": orig, "divisa_orig": divisa, "cambio": None, "quantita": None, "prezzo": None,
             "isin": None, "mcc": None, "id_banca": x["id"] or None, "controparte_nome": None,
             "controparte_iban": None, "esercente": None, "chiave": None, "intermediario": None,
             "descrizione": " · ".join(v for v in (x["tipo_pp"], x["nome"], x["oggetto"]) if v)}
        if ruolo == "ricarica":
            m["tipo"] = "ricarica"
        elif ruolo == "prelievo":
            m["tipo"] = "prelievo_verso_conto"
        elif ruolo in ("esercente", "rimborso") and x["nome"]:
            # pagamento a un esercente, o il suo rimborso: la spesa si classifica con il nome dell'esercente
            m["tipo"] = "pagamento_carta"
            m["esercente"] = x["nome"]
            m["chiave"] = chiave(x["nome"])
        else:
            # pagamenti inviati o ricevuti da persone, e tipi non riconosciuti: come un bonifico (il nome non
            # diventa un esercente, quindi non va mai verso Claude)
            m["tipo"] = "bonifico"
            m["controparte_nome"] = x["nome"] or None
        fl.movimenti.append(m)
    if fl.scartate and len(fl.movimenti) < 0.1 * (len(fl.movimenti) + len(fl.scartate)):
        fl.avvisi.append("Il profilo legge meno del 10% delle righe: probabilmente il formato e' cambiato.")
    return fl
