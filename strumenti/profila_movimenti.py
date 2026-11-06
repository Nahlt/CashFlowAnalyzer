#!/usr/bin/env python3
"""
profila_movimenti.py - Profilazione offline degli export bancari.

Legge uno o piu' file di movimenti (CSV o Excel), ne ricava la struttura e
scrive per ciascuno un documento di specifiche in Markdown, con i dati
sensibili mascherati. Non usa la rete.

Uso:
    python3 profila_movimenti.py FILE [FILE ...] --nomi "Mario,Rossi" [--out cartella] [--k 3]

    --nomi   nomi e cognomi dei titolari (e di chi altro vuoi nascondere),
             separati da virgola. Vengono sostituiti con <NOME> ovunque.
    --out    cartella dei report (default: ./profili)
    --k      soglia di anonimato: un testo viene mostrato solo se compare
             almeno k volte (default 3)
    --senza-esempi     non include righe di esempio mascherate
    --senza-esercenti  non elenca i nomi degli esercenti
    --solo-locale      altre parole da non mostrare mai (es. "veterinario,avvocato");
                       l'area salute (farmacie, cliniche, medici...) e' gia' esclusa

Requisiti: Python 3.9+. Per i file Excel serve openpyxl (pip install openpyxl).
Rileggi sempre il report prima di condividerlo.
"""
import argparse
import collections
import csv
import datetime as dt
import io
import os
import re
import statistics
import sys

VERSIONE = "1.4"
MAX_CATEGORIE = 30          # oltre questo numero di valori distinti una colonna non e' categoriale
RIGHE_ANALISI_TESTA = 60    # righe esaminate per trovare l'intestazione

# ---------------------------------------------------------------------------
# Mascheramento
# ---------------------------------------------------------------------------
RE_CARTA = re.compile(r"\b\d{4,6}[*xX]{4,}\d{2,4}\b|\b[xX*]{6,}\d{4}\b")
RE_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
RE_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
RE_CF = re.compile(r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b", re.I)
RE_DATA = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}(?:T[\d:.]+Z?)?\b")
RE_ORA = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
RE_IMPORTO = re.compile(r"(?<![\w])[-+]?\d{1,3}(?:\.\d{3})*,\d{1,6}\b|(?<![\w.])[-+]?\d+\.\d+\b|=\s*[-+]?\d+(?:[.,]\d+)?")
# Parole dopo le quali c'e' tipicamente un nome di persona: si maschera fino a una parola di stop
TRIGGER_NOME = re.compile(
    r"(transfer (?:for|from|to)|A FAVORE DI|Anagrafica Ordinante|Ordinante|Beneficiario|"
    r"A CREDITO|A DEBITO|Intestazione:?|Intestatario:?|Titolare:?)\s+(?!<)(.+?)"
    r"(?=\s+(?:Rif\.?|RIF=|Note:|IBAN|BIC|Codifica|Anagrafica|N\.|MANDATO|CORE|\(|<IBAN>)|\s*$)",
    re.I)
# Parole dopo le quali c'e' testo libero: si maschera fino alla fine
TRIGGER_FINE = re.compile(r"(Note:|Rif\.|RIF=|Causale:|Messaggio:)\s*.*$", re.I)
# Parole seguite da un identificativo: si maschera solo la parola successiva
TRIGGER_CODICE = re.compile(r"\b(BIC(?: Ordinante| Beneficiario)?|MANDATO|Mandate)\s+(?!<)\S+", re.I)


# Area salute: questi esercenti non compaiono mai nel report (estendibile con --solo-locale)
PAROLE_SOLO_LOCALE = ["farmac", "parafarmac", "humanitas", "ospedal", "clinic", "medic", "dentist", "odontoiatr",
                      "ortodon", "ottic", "optic", "psicolog", "psicoterap", "sanitar", "poliambulat", "fisioterap",
                      "laboratorio analisi", "diagnostic", "asl", "pharma", "apotheke", "hospital"]


# Operazioni su titoli: il nome del titolo dopo l'ISIN non compare
RE_TITOLO = re.compile(r"^(Savings plan execution|Buy trade|Sell trade|Cash Dividend for ISIN|MIGRATION) <COD>(.*?)(, quantity: <IMPORTO>)?$")


class Mascheratore:
    def __init__(self, nomi, solo_locale=()):
        self.nomi = [n.strip() for n in nomi if n and len(n.strip()) >= 2]
        parole = PAROLE_SOLO_LOCALE + [w.strip().lower() for w in solo_locale if w.strip()]
        self.re_locale = re.compile(r"\S*(?:%s)\S*" % "|".join(re.escape(w.strip()) for w in parole), re.I)
        self._compila()

    def _compila(self):
        if self.nomi:
            alt = "|".join(re.escape(n) for n in sorted(set(self.nomi), key=len, reverse=True))
            self.re_nomi = re.compile(r"\b(?:%s)\b" % alt, re.I)
        else:
            self.re_nomi = None

    def aggiungi_nomi(self, testo):
        nuovi = [p for p in re.split(r"[\s,;]+", testo or "") if len(p) >= 2 and p.isalpha()]
        if nuovi:
            self.nomi.extend(nuovi)
            self._compila()

    def maschera(self, testo):
        if testo is None:
            return ""
        s = str(testo)
        if self.re_locale.search(s):
            return "<RISERVATO>"   # area salute o parole riservate: il testo intero non compare
        s = RE_CARTA.sub("<CARTA>", s)
        s = RE_IBAN.sub("<IBAN>", s)
        s = RE_UUID.sub("<UUID>", s)
        s = RE_EMAIL.sub("<EMAIL>", s)
        s = RE_CF.sub("<CF>", s)
        s = RE_DATA.sub("<DATA>", s)
        s = RE_ORA.sub("<ORA>", s)
        s = RE_IMPORTO.sub(lambda m: "=<IMPORTO>" if m.group(0).lstrip().startswith("=") else "<IMPORTO>", s)
        if self.re_nomi:
            s = self.re_nomi.sub("<NOME>", s)
        s = TRIGGER_CODICE.sub(lambda m: m.group(1) + " <COD>", s)
        s = TRIGGER_NOME.sub(lambda m: m.group(1) + " <NOME>", s)
        s = TRIGGER_FINE.sub(lambda m: m.group(1) + " <TESTO>", s)
        # codici alfanumerici e numeri
        out = []
        for tok in s.split():
            if tok.startswith("<") and tok.endswith(">"):
                out.append(tok)
                continue
            if any(c.isdigit() for c in tok):
                core = re.sub(r"[^\w]", "", tok)
                if len(core) >= 6:
                    out.append("<COD>")
                else:
                    out.append(re.sub(r"\d", "#", tok))
            else:
                out.append(tok)
        s = " ".join(out)
        s = RE_TITOLO.sub(lambda m: m.group(1) + " <ISIN> <TITOLO>" + (m.group(3) or ""), s)
        s = re.sub(r"(<NOME>[\s,]*)+", "<NOME> ", s).strip()
        s = re.sub(r"\s{2,}", " ", s)
        return s


# ---------------------------------------------------------------------------
# Lettura file
# ---------------------------------------------------------------------------
def leggi_testo(percorso):
    raw = open(percorso, "rb").read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc), enc, raw
        except UnicodeDecodeError:
            continue
    raise ValueError("codifica non riconosciuta")


def scegli_separatore(righe):
    migliore, punteggio = ",", -1
    for sep in (";", ",", "\t", "|"):
        conteggi = []
        for r in righe[:RIGHE_ANALISI_TESTA]:
            try:
                conteggi.append(len(next(csv.reader([r], delimiter=sep))))
            except Exception:
                conteggi.append(1)
        conteggi = [c for c in conteggi if c > 1]
        if not conteggi:
            continue
        moda, freq = collections.Counter(conteggi).most_common(1)[0]
        p = moda * freq
        if p > punteggio:
            migliore, punteggio = sep, p
    return migliore


def leggi_csv(percorso):
    testo, enc, raw = leggi_testo(percorso)
    fine_riga = "CRLF" if b"\r\n" in raw[:5000] else "LF"
    righe_txt = testo.splitlines()
    sep = scegli_separatore(righe_txt)
    righe = list(csv.reader(io.StringIO(testo), delimiter=sep))
    virgolette = '"' in testo[:5000]
    info = {"formato": "CSV", "codifica": enc, "separatore": {";": "punto e virgola", ",": "virgola",
            "\t": "tabulazione", "|": "barra verticale"}[sep], "fine_riga": fine_riga,
            "virgolette": "si" if virgolette else "no"}
    return righe, info


def leggi_xlsx(percorso):
    try:
        import openpyxl
    except ImportError:
        sys.exit("Per i file Excel serve openpyxl: pip install openpyxl")
    wb = openpyxl.load_workbook(percorso, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    righe = [list(r) for r in ws.iter_rows(values_only=True)]
    info = {"formato": "Excel (.xlsx)", "fogli": len(wb.worksheets), "foglio": ws.title}
    return righe, info


def vuoto(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def trova_intestazione(righe):
    larghezza = max((sum(1 for c in r if not vuoto(c)) for r in righe[:RIGHE_ANALISI_TESTA]), default=0)
    for i, r in enumerate(righe[:RIGHE_ANALISI_TESTA]):
        piene = [c for c in r if not vuoto(c)]
        if len(piene) < max(3, int(0.6 * larghezza)):
            continue
        testuali = sum(1 for c in piene if isinstance(c, str) and not re.fullmatch(r"[-+\d.,/: ]+", c.strip()))
        if testuali < 0.8 * len(piene):
            continue
        seguenti = righe[i + 1:i + 4]
        if seguenti and all(sum(1 for c in s if not vuoto(c)) >= max(3, 0.3 * len(piene)) for s in seguenti):
            return i
    return 0


# ---------------------------------------------------------------------------
# Riconoscimento tipi
# ---------------------------------------------------------------------------
def parse_numero(v):
    """Restituisce (valore, stile) oppure (None, None)."""
    if isinstance(v, bool):
        return None, None
    if isinstance(v, (int, float)):
        return float(v), "numero Excel"
    if not isinstance(v, str):
        return None, None
    s = v.strip().replace("\u00a0", "")
    if not s:
        return None, None
    if re.fullmatch(r"[-+]?\d{1,3}(\.\d{3})*,\d+|[-+]?\d+,\d+", s):
        return float(s.replace(".", "").replace(",", ".")), "italiano (1.234,56)"
    if re.fullmatch(r"[-+]?\d+\.\d+", s):
        return float(s), "punto decimale (1234.56)"
    if re.fullmatch(r"[-+]?\d+", s):
        return float(s), "intero"
    return None, None


FORMATI_DATA = [
    (r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z", "ISO 8601 data e ora in UTC"),
    (r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2})?", "ISO 8601 data e ora"),
    (r"\d{4}-\d{2}-\d{2}", "aaaa-mm-gg"),
    (r"\d{2}/\d{2}/\d{4}", "gg/mm/aaaa"),
    (r"\d{2}/\d{2}/\d{2}", "gg/mm/aa"),
    (r"\d{2}\.\d{2}\.\d{4}", "gg.mm.aaaa"),
]


def parse_data(v):
    if isinstance(v, dt.datetime):
        return v.date(), "data Excel"
    if isinstance(v, dt.date):
        return v, "data Excel"
    if not isinstance(v, str):
        return None, None
    s = v.strip()
    for rx, nome in FORMATI_DATA:
        if re.fullmatch(rx, s):
            try:
                if nome.startswith("ISO") or nome == "aaaa-mm-gg":
                    return dt.date.fromisoformat(s[:10]), nome
                if nome == "gg/mm/aaaa":
                    return dt.datetime.strptime(s, "%d/%m/%Y").date(), nome
                if nome == "gg/mm/aa":
                    return dt.datetime.strptime(s, "%d/%m/%y").date(), nome
                if nome == "gg.mm.aaaa":
                    return dt.datetime.strptime(s, "%d.%m.%Y").date(), nome
            except ValueError:
                return None, None
    return None, None


RE_COLONNA_RISERVATA = re.compile(r"iban|counterparty|controparte|reference|riferimento|transaction_id|^id$|^rif|^symbol$|^isin$|^$", re.I)
RE_COLONNA_IMPORTO = re.compile(r"^(importo|amount|importo in euro|importo \(eur\)|importo eur)$", re.I)


class Colonna:
    def __init__(self, indice, nome):
        self.indice = indice
        self.nome = "" if nome is None else str(nome).strip()
        self.valori = []

    def analizza(self):
        piene = [v for v in self.valori if not vuoto(v)]
        self.n = len(self.valori)
        self.n_piene = len(piene)
        self.tipo, self.formato = "vuota", ""
        self.distinti = len(set(str(v).strip() for v in piene))
        self.riservata = bool(RE_COLONNA_RISERVATA.search(self.nome))
        self.categoriale = False
        if not piene:
            return
        date = [parse_data(v) for v in piene]
        nums = [parse_numero(v) for v in piene]
        quota_date = sum(1 for d in date if d[0]) / len(piene)
        quota_num = sum(1 for x in nums if x[0] is not None) / len(piene)
        if quota_date >= 0.95:
            self.tipo = "data"
            self.formato = collections.Counter(d[1] for d in date if d[1]).most_common(1)[0][0]
            ds = [d[0] for d in date if d[0]]
            self.min, self.max = min(ds), max(ds)
        elif quota_num >= 0.95:
            stili = collections.Counter(x[1] for x in nums if x[1])
            self.tipo = "numero"
            self.formato = ", ".join(s for s, _ in stili.most_common())
            vals = [x[0] for x in nums if x[0] is not None]
            self.negativi = sum(1 for x in vals if x < 0)
            self.positivi = sum(1 for x in vals if x > 0)
            self.zeri = sum(1 for x in vals if x == 0)
            dec = collections.Counter()
            for v in piene:
                if isinstance(v, str):
                    m = re.search(r"[.,](\d+)$", v.strip())
                    dec[len(m.group(1)) if m else 0] += 1
            self.decimali = dec
            if self.distinti <= 3 and self.positivi + self.negativi == 0:
                self.tipo = "numero (sempre zero)"
            elif set(stili) <= {"intero"} and self.distinti <= MAX_CATEGORIE and self.negativi == 0:
                self.tipo = "codice"
                self.formato = "intero"

        else:
            self.tipo = "testo"
            lun = [len(str(v)) for v in piene]
            self.formato = "lunghezza %d-%d" % (min(lun), max(lun))
            if all(RE_UUID.fullmatch(str(v).strip()) for v in piene):
                self.formato = "UUID"
            elif all(RE_IBAN.fullmatch(str(v).strip()) for v in piene):
                self.formato = "IBAN"
        self.riservata = bool(RE_COLONNA_RISERVATA.search(self.nome))
        self.categoriale = (self.tipo in ("testo", "codice") and self.distinti <= MAX_CATEGORIE
                            and self.distinti <= 0.5 * self.n_piene and not self.riservata)


# ---------------------------------------------------------------------------
# Profili noti
# ---------------------------------------------------------------------------
PROFILI = {
    "trade_republic": {
        "nome": "Trade Republic - Esportazione transazioni",
        "firma": {"transaction_id", "category", "type", "mcc_code", "amount"},
        "chiavi": ["category", "type"],
        "importo": "amount",
        "descrizione": "description",
        "esercente": "name",
        "controparte": "counterparty_iban",
        "categoriali_sempre": {"category", "type", "account_type", "asset_class", "currency", "original_currency"},
    },
    "credem": {
        "nome": "Credem - Lista movimenti (CSV)",
        "firma": {"Data contabile", "Causale ABI", "Causale", "Descrizione:"},
        "chiavi": ["Causale ABI", "Causale"],
        "importo": "Importo",
        "descrizione": "Descrizione:",
        "categoriali_sempre": {"Causale ABI", "Causale", "Canale", "Categoria", "Divisa orig.", "Note"},
    },
    "ing_carta": {
        "nome": "ING - Movimenti carta di credito",
        "firma": {"DATA OPERAZIONE", "DATA REGISTRAZIONE", "DESCRIZIONE OPERAZIONE", "IMPORTO IN EURO"},
        "chiavi": [],
        "importo": "IMPORTO IN EURO",
        "descrizione": "DESCRIZIONE OPERAZIONE",
        "categoriali_sempre": set(),
    },
    "ing": {
        "nome": "ING Conto Arancio - Lista movimenti (Excel)",
        "firma": {"DATA CONTABILE", "CAUSALE", "DESCRIZIONE OPERAZIONE", "IMPORTO IN EURO"},
        "chiavi": ["CAUSALE"],
        "importo": "IMPORTO IN EURO",
        "descrizione": "DESCRIZIONE OPERAZIONE",
        "categoriali_sempre": {"CAUSALE"},
    },
}

TIPI_TR_NOTI = {
    "CARD_TRANSACTION", "CARD_TRANSACTION_INTERNATIONAL", "TRANSFER_INSTANT_OUTBOUND", "TRANSFER_OUTBOUND",
    "TRANSFER_INSTANT_INBOUND", "TRANSFER_INBOUND", "CUSTOMER_INBOUND", "CUSTOMER_OUTBOUND_REQUEST",
    "TRANSFER_DIRECT_DEBIT_INBOUND", "INTEREST_PAYMENT", "DIVIDEND", "TAX_OPTIMIZATION", "BENEFITS_SAVEBACK",
    "BUY", "SELL", "MIGRATION",
}


def riconosci_profilo(intestazione):
    nomi = {("" if c is None else str(c).strip()) for c in intestazione}
    for chiave, p in PROFILI.items():
        if p["firma"] <= nomi:
            return chiave
    return None


# ---------------------------------------------------------------------------
# Utilita' Markdown
# ---------------------------------------------------------------------------
def md_cella(v):
    return str(v).replace("|", "\\|").replace("\n", " ")


def md_tabella(intestazioni, righe):
    out = ["| " + " | ".join(intestazioni) + " |", "| " + " | ".join("---" for _ in intestazioni) + " |"]
    for r in righe:
        out.append("| " + " | ".join(md_cella(c) for c in r) + " |")
    return out


def perc(a, b):
    return "%d%%" % round(100 * a / b) if b else "-"


# ---------------------------------------------------------------------------
# Profilazione
# ---------------------------------------------------------------------------
class Profilo:
    def __init__(self, percorso, mask, k, esempi, esercenti=True):
        self.esercenti = esercenti
        self.percorso = percorso
        self.mask = mask
        self.k = k
        self.esempi = esempi
        self.md = []

    # -- caricamento --------------------------------------------------------
    def carica(self):
        est = os.path.splitext(self.percorso)[1].lower()
        if est in (".xlsx", ".xlsm"):
            righe, self.info = leggi_xlsx(self.percorso)
        else:
            righe, self.info = leggi_csv(self.percorso)
        self.righe_tot = len(righe)
        h = trova_intestazione(righe)
        self.riga_intestazione = h + 1
        self.testa = righe[:h]
        intest = righe[h]
        # metadati: i nomi trovati in testa vengono aggiunti alla maschera
        for r in self.testa:
            testo = " ".join(str(c) for c in r if not vuoto(c))
            m = re.search(r"(Intestazione|Intestatario|Titolare)\s*:?\s*;?\s*(.+)", testo, re.I)
            if m:
                self.mask.aggiungi_nomi(m.group(2))
        corpo = righe[h + 1:]
        larg = len(intest)
        self.dati, self.scartate = [], collections.Counter()
        for r in corpo:
            r = list(r) + [None] * max(0, larg - len(r))
            piene = sum(1 for c in r if not vuoto(c))
            if piene == 0:
                self.scartate["vuote"] += 1
            elif piene < max(2, 0.3 * sum(1 for c in intest if not vuoto(c))):
                self.scartate["incomplete o di totale"] += 1
            else:
                if len(r) > larg:
                    self.scartate["con campi in eccesso (tenute)"] += 1
                self.dati.append(r[:larg])
        self.colonne = [Colonna(i, n) for i, n in enumerate(intest)]
        for r in self.dati:
            for c in self.colonne:
                c.valori.append(r[c.indice])
        for c in self.colonne:
            c.analizza()
        self.profilo = riconosci_profilo(intest)
        self.cfg = PROFILI.get(self.profilo, {})
        for c in self.colonne:
            if c.nome in self.cfg.get("categoriali_sempre", set()) and c.tipo != "vuota":
                c.categoriale = True
        self.col = {c.nome: c for c in self.colonne if c.nome}

    # -- helper -------------------------------------------------------------
    def valore(self, riga, nome):
        c = self.col.get(nome)
        return None if c is None else riga[c.indice]

    def colonna_importo(self):
        if self.cfg.get("importo") in self.col:
            return self.col[self.cfg["importo"]]
        for c in self.colonne:
            if RE_COLONNA_IMPORTO.match(c.nome) and c.tipo.startswith("numero"):
                return c
        cand = [c for c in self.colonne if c.tipo == "numero" and getattr(c, "negativi", 0) and getattr(c, "positivi", 0)]
        return cand[0] if cand else None

    def colonna_descrizione(self):
        if self.cfg.get("descrizione") in self.col:
            return self.col[self.cfg["descrizione"]]
        testi = [c for c in self.colonne if c.tipo == "testo" and not c.categoriale]
        if not testi:
            return None
        return max(testi, key=lambda c: statistics.mean(len(str(v)) for v in c.valori if not vuoto(v)))

    def segno(self, riga):
        c = self.colonna_importo()
        if c is None:
            return "?"
        v, _ = parse_numero(riga[c.indice])
        if v is None:
            return "vuoto"
        return "negativo" if v < 0 else "positivo" if v > 0 else "zero"

    def ha_iban(self, riga):
        if self.cfg.get("controparte") in self.col:
            if not vuoto(self.valore(riga, self.cfg["controparte"])):
                return True
        return any(isinstance(v, str) and RE_IBAN.search(v) for v in riga)

    def chiavi(self):
        ch = [n for n in self.cfg.get("chiavi", []) if n in self.col]
        if ch:
            return ch
        cat = sorted((c for c in self.colonne if c.categoriale), key=lambda c: -c.distinti)
        return [c.nome for c in cat[:2]]

    def chiave_riga(self, riga):
        return tuple(str(self.valore(riga, n) or "").strip() for n in self.chiavi())

    def anonimi(self, valori, top=15):
        """Raggruppa testi mascherati e mostra solo quelli con almeno k occorrenze."""
        cont = collections.Counter(self.mask.maschera(v) for v in valori if not vuoto(v))
        mostrati = [(t, n) for t, n in cont.most_common() if n >= self.k][:top]
        rari = sum(n for t, n in cont.items() if n < self.k)
        rari_schemi = sum(1 for t, n in cont.items() if n < self.k)
        return mostrati, rari, rari_schemi

    # -- sezioni ------------------------------------------------------------
    def sezione_riconoscimento(self):
        nome_file = re.sub(r"\d+", "<cifre>", os.path.basename(self.percorso))
        self.md += ["## 1. Riconoscimento del file", ""]
        righe = [("Nome del file (cifre mascherate)", "`%s`" % nome_file),
                 ("Profilo riconosciuto", self.cfg.get("nome", "nessuno (formato non ancora censito)"))]
        for k_, v in self.info.items():
            righe.append((k_.replace("_", " ").capitalize(), v))
        righe += [("Righe totali nel file", self.righe_tot),
                  ("Riga di intestazione", self.riga_intestazione),
                  ("Righe di metadati sopra l'intestazione", len(self.testa)),
                  ("Righe di dati", len(self.dati))]
        for motivo, n in self.scartate.items():
            righe.append(("Righe scartate: %s" % motivo, n))
        doppie = len(self.dati) - len({tuple(str(c) for c in r) for r in self.dati})
        righe.append(("Righe identiche ripetute", doppie))
        self.md += md_tabella(["Voce", "Valore"], righe) + [""]

    def sezione_metadati(self):
        self.md += ["## 2. Metadati in testa al file", ""]
        righe = []
        for r in self.testa:
            celle = [str(c).strip() for c in r if not vuoto(c)]
            if not celle:
                continue
            if len(celle) >= 2:
                chiave, valore = celle[0], " ".join(celle[1:])
            elif ":" in celle[0]:
                chiave, valore = celle[0].split(":", 1)
                chiave += ":"
            else:
                chiave, valore = "(riga senza chiave)", celle[0]
            valore = valore.strip()
            if RE_IBAN.search(valore):
                tipo = "IBAN"
            elif parse_data(valore)[0]:
                tipo = "data"
            elif parse_numero(valore.replace("EUR", "").strip())[0] is not None:
                tipo = "numero/importo"
            else:
                tipo = "testo"
            righe.append((self.mask.maschera(chiave), tipo, "`%s`" % self.mask.maschera(valore)))
        if righe:
            self.md += md_tabella(["Chiave", "Tipo del valore", "Valore mascherato"], righe)
        else:
            self.md.append("Nessun metadato: la prima riga del file e' gia' l'intestazione.")
        self.md.append("")

    def sezione_colonne(self):
        self.md += ["## 3. Colonne", ""]
        righe = []
        for c in self.colonne:
            note = []
            if c.tipo == "data":
                giorni = (c.max - c.min).days
                durata = "%d giorni" % giorni if giorni < 60 else "circa %d mesi" % round(giorni / 30.44)
                note.append("arco temporale %s (date non mostrate)" % durata)
            if c.tipo == "numero":
                note.append("negativi %d, positivi %d, zero %d" % (c.negativi, c.positivi, c.zeri))
                if c.decimali:
                    note.append("decimali: " + ", ".join("%d cifre (%d)" % (d, n) for d, n in sorted(c.decimali.items())))
            if c.riservata:
                note.append("colonna riservata o senza nome: valori non mostrati")
            if c.categoriale:
                note.append("categoriale")
            if not c.nome:
                note.append("colonna senza nome")
            righe.append((c.indice + 1, "`%s`" % c.nome if c.nome else "(vuoto)", perc(c.n_piene, c.n),
                          c.tipo, c.formato, c.distinti, "; ".join(note)))
        self.md += md_tabella(["#", "Nome", "Compilata", "Tipo", "Formato", "Valori distinti", "Note"], righe) + [""]

    def sezione_categoriali(self):
        self.md += ["## 4. Valori delle colonne categoriali", ""]
        sempre = self.cfg.get("categoriali_sempre", set())
        trovate = False
        for c in self.colonne:
            if not (c.categoriale or (c.nome in sempre and c.tipo != "vuota")):
                continue
            if c.distinti > 60:
                continue
            trovate = True
            cont = collections.defaultdict(collections.Counter)
            for r in self.dati:
                v = r[c.indice]
                chiave = "(vuoto)" if vuoto(v) else str(v).strip()
                cont[chiave][self.segno(r)] += 1
            righe, nascosti = [], 0
            for v, segni in sorted(cont.items(), key=lambda x: -sum(x[1].values())):
                tot = sum(segni.values())
                if c.nome not in sempre and tot < self.k:
                    nascosti += tot
                    continue
                etichetta = v if c.nome in sempre else self.mask.maschera(v)
                righe.append(("`%s`" % etichetta, tot, segni["negativo"], segni["positivo"],
                              segni["zero"] + segni["vuoto"]))
            self.md += ["### `%s`" % c.nome, ""]
            self.md += md_tabella(["Valore", "Righe", "Importo negativo", "Importo positivo", "Zero o vuoto"], righe)
            if nascosti:
                self.md.append("")
                self.md.append("Altre %d righe con valori rari (meno di %d occorrenze), non mostrati." % (nascosti, self.k))
            self.md.append("")
        if not trovate:
            self.md += ["Nessuna colonna categoriale.", ""]

    def sezione_combinazioni(self):
        ch = self.chiavi()
        if not ch:
            return
        self.md += ["## 5. Combinazioni per tipo di movimento", "",
                    "Chiave: %s. \"Con IBAN\" conta le righe con un IBAN nei campi controparte o nel testo." %
                    ", ".join("`%s`" % n for n in ch), ""]
        cont = collections.defaultdict(collections.Counter)
        for r in self.dati:
            cont[self.chiave_riga(r)][self.segno(r)] += 1
            cont[self.chiave_riga(r)]["_iban"] += 1 if self.ha_iban(r) else 0
        righe = []
        for chiave, s in sorted(cont.items(), key=lambda x: -(sum(x[1].values()) - x[1]["_iban"])):
            tot = sum(v for k_, v in s.items() if k_ != "_iban")
            righe.append(tuple("`%s`" % v for v in chiave) + (tot, s["negativo"], s["positivo"], s["zero"] + s["vuoto"], s["_iban"]))
        self.md += md_tabella(ch + ["Righe", "Negative", "Positive", "Zero o vuote", "Con IBAN"], righe) + [""]

    def sezione_descrizioni(self):
        cd = self.colonna_descrizione()
        if cd is None:
            return
        self.md += ["## 6. Schemi delle descrizioni (colonna `%s`)" % cd.nome, "",
                    "Testi mascherati: <NOME>, <IBAN>, <CARTA>, <DATA>, <ORA>, <IMPORTO>, <COD> (codici con cifre), "
                    "<UUID>, <TESTO> (testo libero), <RISERVATO> (testo intero nascosto: area salute o parole riservate), # (cifra). Uno schema compare solo se ricorre almeno %d volte." % self.k, ""]
        gruppi = collections.defaultdict(list)
        for r in self.dati:
            gruppi[self.chiave_riga(r)].append(r[cd.indice])
        for chiave, valori in sorted(gruppi.items(), key=lambda x: -len(x[1])):
            mostrati, rari, rari_schemi = self.anonimi(valori, top=8)
            parole = [len(str(v).split()) for v in valori if not vuoto(v)]
            titolo = " / ".join("`%s`" % v for v in chiave) or "(tutte le righe)"
            self.md.append("**%s** - %d righe, parole per descrizione %s" %
                           (titolo, len(valori), "%d-%d" % (min(parole), max(parole)) if parole else "-"))
            self.md.append("")
            if mostrati:
                self.md += md_tabella(["Schema", "Righe"], [("`%s`" % t, n) for t, n in mostrati])
            if rari:
                self.md.append("")
                self.md.append("Schemi rari non mostrati: %d schemi su %d righe." % (rari_schemi, rari))
            self.md.append("")

    def sezione_esempi(self):
        if not self.esempi:
            return
        self.md += ["## 8. Esempi di righe mascherate", "",
                    "Una riga per tipo di movimento. Importi sostituiti dal segno, date dal formato; "
                    "i testi compaiono solo se il loro schema ricorre almeno %d volte." % self.k, ""]
        cd = self.colonna_descrizione()
        visti = set()
        for r in self.dati:
            ch = self.chiave_riga(r)
            if ch in visti:
                continue
            visti.add(ch)
            campi = []
            for c in self.colonne:
                v = r[c.indice]
                if vuoto(v):
                    continue
                if c.riservata:
                    campi.append("%s=<presente>" % c.nome)
                    continue
                n, _ = parse_numero(v)
                if c.nome in self.cfg.get("categoriali_sempre", set()) or c.tipo == "codice":
                    campi.append("%s=%s" % (c.nome, str(v).strip() if c.nome in self.cfg.get("categoriali_sempre", set()) else self.mask.maschera(v)))
                elif c.tipo.startswith("numero") and n is not None:
                    campi.append("%s=%s" % (c.nome, "<-importo>" if n < 0 else "<+importo>" if n > 0 else "0"))
                elif c.tipo == "data":
                    campi.append("%s=<%s>" % (c.nome, c.formato))
                elif c.nome in self.cfg.get("categoriali_sempre", set()):
                    campi.append("%s=%s" % (c.nome, str(v).strip()))
                elif c.categoriale:
                    campi.append("%s=%s" % (c.nome, self.mask.maschera(v)))
                elif c.nome == "name" and not vuoto(self.valore(r, "symbol")):
                    campi.append("name=<TITOLO>")
                else:
                    t = self.mask.maschera(v)
                    conteggio = sum(1 for x in c.valori if not vuoto(x) and self.mask.maschera(x) == t)
                    campi.append("%s=%s" % (c.nome, t if conteggio >= self.k else "<testo raro>"))
            self.md.append("- " + " ; ".join("`%s`" % md_cella(x) for x in campi))
        self.md.append("")

    # -- controlli specifici -----------------------------------------------
    def controlli(self):
        self.md += ["## 7. Controlli specifici del formato", ""]
        f = {"trade_republic": self.controlli_tr, "credem": self.controlli_credem, "ing": self.controlli_ing,
             "ing_carta": self.controlli_ing_carta}.get(self.profilo)
        if f:
            f()
            if self.profilo == "trade_republic":
                self.controlli_tr_trading()
        else:
            self.md += ["Formato non censito: nessun controllo specifico. Le sezioni 3-6 descrivono la struttura.", ""]

    def esercenti_anonimi(self, valori, titolo):
        if not self.esercenti:
            self.md += ["**%s**: %d valori, elenco omesso (--senza-esercenti)." % (titolo, len([v for v in valori if v])), ""]
            return
        norm = [re.sub(r"\d", "#", v) for v in valori if v]
        mostrati, rari, schemi = self.anonimi(norm, top=20)
        self.md += ["**%s** - %d valori estratti, mostrati quelli con almeno %d occorrenze:" % (titolo, len(norm), self.k), ""]
        if mostrati:
            self.md += md_tabella(["Esercente (cifre come #)", "Righe"], [("`%s`" % t, n) for t, n in mostrati])
        self.md += ["", "Esercenti rari non mostrati: %d su %d righe." % (schemi, rari), ""]

    def controlli_tr(self):
        D = self.dati
        g = lambda r, n: self.valore(r, n)
        num = lambda v: parse_numero(v)[0]
        out = []
        ids = [g(r, "transaction_id") for r in D]
        out.append(("`transaction_id` univoco", "si" if len(set(ids)) == len(ids) else "no, %d duplicati" % (len(ids) - len(set(ids)))))
        tipi = {str(g(r, "type")) for r in D}
        nuovi = sorted(tipi - TIPI_TR_NOTI)
        out.append(("Valori di `type` non ancora censiti", ", ".join("`%s`" % t for t in nuovi) if nuovi else "nessuno"))
        diverse = sum(1 for r in D if str(g(r, "date"))[:10] != str(g(r, "datetime"))[:10])
        out.append(("`date` diversa dalla data di `datetime`", diverse))
        ore = collections.Counter()
        for r in D:
            m = re.search(r"T(\d{2}):", str(g(r, "datetime")))
            if m:
                h = int(m.group(1))
                ore["00-05" if h < 6 else "06-11" if h < 12 else "12-17" if h < 18 else "18-21" if h < 22 else "22-23"] += 1
        out.append(("Ore UTC di `datetime`", ", ".join("%s: %d" % kv for kv in sorted(ore.items()))))
        # effetto sul conto
        for col in ("fee", "tax"):
            c = collections.Counter()
            for r in D:
                v = num(g(r, col))
                if v is not None and v != 0:
                    c[(g(r, "type"), "neg" if v < 0 else "pos")] += 1
            out.append(("`%s` valorizzata (tipo, segno)" % col, ", ".join("%s %s: %d" % (t, s, n) for (t, s), n in sorted(c.items())) or "mai"))
        rapporti = [-num(g(r, "tax")) / num(g(r, "amount")) for r in D
                    if g(r, "type") == "INTEREST_PAYMENT" and num(g(r, "tax")) and num(g(r, "amount"))]
        if rapporti:
            out.append(("Ritenuta su interessi (`-tax`/`amount`, mediana)", "%.1f%% su %d righe" % (100 * statistics.median(rapporti), len(rapporti))))
        to = [r for r in D if g(r, "type") == "TAX_OPTIMIZATION"]
        if to:
            zero = sum(1 for r in to if not num(g(r, "amount")))
            out.append(("`TAX_OPTIMIZATION` con `amount` = 0 e importo in `tax`", "%d su %d" % (zero, len(to))))
        # saveback e acquisti
        sb = [r for r in D if g(r, "type") == "BENEFITS_SAVEBACK"]
        if sb:
            buys = [r for r in D if g(r, "type") == "BUY"]
            ok = 0
            for s in sb:
                a = num(g(s, "amount"))
                ds = parse_data(g(s, "date"))[0]
                for b in buys:
                    db = parse_data(g(b, "date"))[0]
                    if a and num(g(b, "amount")) is not None and abs(num(g(b, "amount")) + a) < 0.005 and ds and db and abs((db - ds).days) <= 3:
                        ok += 1
                        break
            out.append(("Saveback con un `BUY` dello stesso importo entro 3 giorni", "%d su %d" % (ok, len(sb))))
        mig = [r for r in D if g(r, "type") == "MIGRATION"]
        if mig:
            gr = collections.defaultdict(float)
            for r in mig:
                gr[(str(g(r, "datetime"))[:19], g(r, "symbol"))] += num(g(r, "shares")) or 0
            nette = sum(1 for v in gr.values() if abs(v) < 1e-6)
            out.append(("`MIGRATION`: gruppi titolo+istante che si annullano", "%d su %d" % (nette, len(gr))))
        carte = [r for r in D if "CARD" in str(g(r, "type"))]
        if carte:
            con_mcc = sum(1 for r in carte if not vuoto(g(r, "mcc_code")))
            out.append(("Pagamenti carta con `mcc_code`", "%d su %d" % (con_mcc, len(carte))))
            null_desc = sum(1 for r in carte if str(g(r, "description")).endswith("null"))
            out.append(("Descrizioni carta con artefatto finale \"null\"", null_desc))
        oltre = collections.Counter()
        for r in D:
            for col in ("amount", "fee", "tax", "original_amount"):
                m = re.fullmatch(r"-?\d+\.(\d+)", str(g(r, col) or "").strip())
                if m and len(m.group(1)) > 2 and set(m.group(1)[2:]) - {"0"}:
                    oltre[col] += 1
        out.append(("Importi con cifre non nulle oltre i centesimi", ", ".join("%s: %d" % kv for kv in oltre.items()) or "nessuno: arrotondare ai centesimi e' innocuo"))
        dec = collections.defaultdict(collections.Counter)
        for r in D:
            for col in ("amount", "shares", "price", "fx_rate"):
                m = re.fullmatch(r"-?\d+(?:\.(\d+))?", str(g(r, col) or "").strip())
                if m:
                    dec[col][len(m.group(1) or "")] += 1
        out.append(("Decimali per colonna", "; ".join("%s: %s" % (col, ", ".join("%d cifre (%d)" % kv for kv in sorted(c_.items())))
                                                       for col, c_ in dec.items())))
        corti = sum(1 for r in D if not vuoto(g(r, "mcc_code")) and len(str(g(r, "mcc_code")).strip()) < 4)
        out.append(("Codici MCC con meno di 4 cifre (da completare con zeri)", corti))
        valute = collections.Counter(str(g(r, "original_currency")) for r in D if not vuoto(g(r, "original_currency")))
        out.append(("Valute originali diverse da EUR", ", ".join("%s: %d" % kv for kv in valute.most_common()) or "nessuna"))
        bon = [r for r in D if "TRANSFER" in str(g(r, "type")) and "DIRECT_DEBIT" not in str(g(r, "type"))]
        if bon:
            con_cp = sum(1 for r in bon if not vuoto(g(r, "counterparty_iban")))
            con_nome_desc = sum(1 for r in bon if re.search(r"transfer (for|from) \S", str(g(r, "description")), re.I))
            out.append(("Bonifici con campi controparte compilati", "%d su %d" % (con_cp, len(bon))))
            out.append(("Bonifici con il nome nella descrizione (\"transfer for/from ...\")", "%d su %d" % (con_nome_desc, len(bon))))
        dd = [r for r in D if g(r, "type") == "TRANSFER_DIRECT_DEBIT_INBOUND"]
        if dd and self.mask.re_nomi:
            tit = sum(1 for r in dd if self.mask.re_nomi.search(str(g(r, "counterparty_name") or "")))
            out.append(("Addebiti SEPA con controparte = titolare configurato", "%d su %d" % (tit, len(dd))))
        senza_nome = [c.indice + 1 for c in self.colonne if not c.nome]
        if senza_nome:
            out.append(("Colonne senza nome in coda (aggiunte a mano?)", ", ".join(str(i) for i in senza_nome)))
        self.md += md_tabella(["Controllo", "Esito"], out) + [""]
        # MCC e prefissi
        mcc = collections.Counter(str(g(r, "mcc_code")) for r in carte if not vuoto(g(r, "mcc_code")))
        if mcc:
            self.md += ["**Codici MCC piu' frequenti nei pagamenti carta**", ""]
            self.md += md_tabella(["MCC", "Righe"], mcc.most_common(20)) + [""]
        pref = [str(g(r, "name")).split("*")[0].strip() for r in carte if "*" in str(g(r, "name"))]
        if pref:
            self.esercenti_anonimi(pref, "Prefissi di intermediario in `name` (testo prima di \"*\")")
        self.esercenti_anonimi([str(g(r, "name")) for r in carte if not vuoto(g(r, "name"))], "Esercenti in `name` (pagamenti carta)")

    def controlli_tr_trading(self):
        """Analisi delle operazioni su titoli con titoli anonimizzati e senza importi o quantita' reali."""
        D = sorted(self.dati, key=lambda r: str(self.valore(r, "datetime")))
        g = lambda r, n: self.valore(r, n)
        num = lambda v: parse_numero(v)[0]
        con_titolo = [r for r in D if not vuoto(g(r, "symbol"))]
        self.md += ["## 7b. Trading (titoli anonimizzati)", "",
                    "I titoli sono etichettati A, B, C... in ordine di prima comparsa; ISIN e nomi non compaiono. "
                    "Nessuna quantita', prezzo o importo reale: solo conteggi, rapporti ed esiti dei controlli.", ""]
        if not con_titolo:
            self.md += ["Nessuna operazione su titoli nel file.", ""]
            return
        ordine = []
        for r in con_titolo:
            if g(r, "symbol") not in ordine:
                ordine.append(g(r, "symbol"))

        def etichetta(i):
            lettere = ""
            i += 1
            while i:
                i, resto = divmod(i - 1, 26)
                lettere = chr(65 + resto) + lettere
            return "Titolo " + lettere
        nome_tit = {sym: etichetta(i) for i, sym in enumerate(ordine)}

        st = {sym: {"classe": "", "domicilio": str(sym)[:2], "piano": [], "manuali": 0, "da_saveback": 0, "vendite": 0, "vendite_tot": 0,
                    "migrazioni": 0, "dividendi": 0, "saveback": 0, "pos": 0.0, "costo": 0.0, "anomalie": collections.Counter()}
              for sym in ordine}
        segni = collections.Counter()
        controvalore = collections.Counter()
        commissioni = collections.Counter()
        rit_vendite, div_ok, div_tot, mig_intera, mig_out = [], 0, 0, 0, 0
        importi_anomali = collections.Counter()
        saveback = [(parse_data(g(r, "date"))[0], num(g(r, "amount"))) for r in D if g(r, "type") == "BENEFITS_SAVEBACK"]
        usati = set()

        def da_saveback(r):
            d, a_ = parse_data(g(r, "date"))[0], num(g(r, "amount"))
            for i, (ds, sa) in enumerate(saveback):
                if i not in usati and d and ds and a_ is not None and sa and abs(a_ + sa) < 0.005 and abs((d - ds).days) <= 3:
                    usati.add(i)
                    return True
            return False

        for r in con_titolo:
            sym, t = g(r, "symbol"), str(g(r, "type"))
            x = st[sym]
            x["classe"] = x["classe"] or str(g(r, "asset_class") or "")
            sh, pr, amt = num(g(r, "shares")), num(g(r, "price")), num(g(r, "amount"))
            fee, tax = num(g(r, "fee")) or 0.0, num(g(r, "tax")) or 0.0
            if sh is not None:
                segni[(t, "positiva" if sh > 0 else "negativa" if sh < 0 else "zero")] += 1
            if t in ("BUY", "SELL") and sh is not None and pr is not None and amt is not None:
                lordo = abs(sh) * pr
                toll = max(0.011, 0.0005 * abs(amt))
                if abs(abs(amt) - lordo) <= toll:
                    controvalore["amount = quantita' x prezzo (commissione a parte in fee)"] += 1
                elif abs(abs(amt) - (lordo - fee)) <= toll or abs(abs(amt) - (lordo + fee)) <= toll:
                    controvalore["amount = quantita' x prezzo con commissione inclusa"] += 1
                else:
                    controvalore["amount diverso da quantita' x prezzo"] += 1
                    importi_anomali["%s %s" % (nome_tit[sym], t)] += 1
                if fee:
                    commissioni["%s %.2f" % (t, fee)] += 1
            if t == "BUY":
                if da_saveback(r):
                    x["da_saveback"] += 1
                elif str(g(r, "description")).startswith("Savings plan execution"):
                    x["piano"].append((parse_data(g(r, "date"))[0], round(abs(amt or 0), 2)))
                else:
                    x["manuali"] += 1
                x["pos"] += sh or 0
                x["costo"] += abs(amt or 0) - fee  # fee negativa: la commissione aumenta il costo
            elif t == "SELL":
                q = abs(sh or 0)
                x["vendite"] += 1
                if q > x["pos"] + 1e-6:
                    x["anomalie"]["vendita oltre la posizione ricostruita (mancano acquisti nel file)"] += 1
                    x["costo"] = x["costo"] * q / x["pos"] if x["pos"] > 1e-9 else 0.0
                    x["pos"] = q
                medio = x["costo"] / x["pos"] if x["pos"] > 1e-9 else 0.0
                plus = abs(amt or 0) - medio * q
                if plus > 0.5 and tax:
                    rit_vendite.append(-tax / plus)
                x["costo"] -= medio * min(q, max(x["pos"], 0))
                x["pos"] -= q
                if abs(x["pos"]) < 1e-6:
                    x["vendite_tot"] += 1
                    x["pos"], x["costo"] = 0.0, 0.0
            elif t == "MIGRATION":
                x["migrazioni"] += 1
                if (sh or 0) < 0:
                    mig_out += 1
                    if abs(abs(sh) - x["pos"]) < 1e-6:
                        mig_intera += 1
                    elif abs(sh) > x["pos"]:
                        x["anomalie"]["migrazione maggiore della posizione ricostruita (mancano acquisti nel file)"] += 1
                        x["costo"] = x["costo"] * abs(sh) / x["pos"] if x["pos"] > 1e-9 else 0.0
                        x["pos"] = abs(sh)
                x["pos"] += sh or 0
            elif t == "DIVIDEND":
                x["dividendi"] += 1
                div_tot += 1
                if sh is not None and abs(sh - x["pos"]) < 1e-4:
                    div_ok += 1
            elif t == "BENEFITS_SAVEBACK":
                x["saveback"] += 1

        # cambi
        fx_ok, fx_tot = 0, 0
        for r in self.dati:
            oa, fx, amt = num(g(r, "original_amount")), num(g(r, "fx_rate")), num(g(r, "amount"))
            if oa is not None and fx and amt is not None:
                fx_tot += 1
                if abs(abs(oa) * fx - abs(amt)) <= max(0.02, 0.01 * abs(amt)):
                    fx_ok += 1

        classi = collections.Counter(x["classe"] or "?" for x in st.values())
        domicili = collections.Counter(x["domicilio"] for x in st.values())
        piani = [x for x in st.values() if x["piano"]]
        out = [
            ("Titoli distinti", "%d (%s)" % (len(ordine), ", ".join("%s: %d" % kv for kv in classi.most_common()))),
            ("Paese dell'ISIN", ", ".join("%s: %d" % kv for kv in domicili.most_common())),
            ("Segno di `shares` per tipo", ", ".join("%s %s: %d" % (t, s_, n) for (t, s_), n in sorted(segni.items()))),
            ("Controvalore di acquisti e vendite", ", ".join("%s: %d" % kv for kv in controvalore.most_common()) or "-"),
            ("Commissioni (`fee`) per tipo e valore", ", ".join("%s: %d" % kv for kv in commissioni.most_common()) or "nessuna"),
            ("Acquisti da piano di accumulo / da saveback / manuali", "%d / %d / %d" % (
                sum(len(x["piano"]) for x in st.values()), sum(x["da_saveback"] for x in st.values()),
                sum(x["manuali"] for x in st.values()))),
            ("Titoli con piano di accumulo", len(piani)),
            ("Vendite totali (posizione azzerata) / parziali", "%d / %d" % (sum(x["vendite_tot"] for x in st.values()),
                                                                             sum(x["vendite"] - x["vendite_tot"] for x in st.values()))),
        ]
        if rit_vendite:
            out.append(("Imposta sulle vendite in plusvalenza (`-tax` / plusvalenza stimata a costo medio)",
                        "mediana %.1f%% su %d vendite" % (100 * statistics.median(rit_vendite), len(rit_vendite))))
        out += [
            ("Migrazioni in uscita pari all'intera posizione", "%d su %d" % (mig_intera, mig_out)),
            ("Dividendi: `shares` uguale alla posizione ricostruita", "%d su %d" % (div_ok, div_tot)),
            ("Righe in valuta: `original_amount` x `fx_rate` = `amount`", "%d su %d" % (fx_ok, fx_tot)),
            ("Saveback associati a un titolo (colonna `symbol`)", sum(x["saveback"] for x in st.values())),
            ("Anomalie sulle posizioni (la posizione viene riallineata)",
             "; ".join("%s: %s" % (nome_tit[sym], ", ".join(st[sym]["anomalie"])) for sym in ordine if st[sym]["anomalie"]) or "nessuna"),
            ("Righe con controvalore da verificare", ", ".join("%s: %d" % kv for kv in importi_anomali.most_common()) or "nessuna"),
        ]
        self.md += md_tabella(["Controllo", "Esito"], out) + [""]
        self.md += ["La posizione e' ricostruita solo dalle righe del file: se il file non parte dal primo acquisto, "
                    "vendite e dividendi possono risultare incoerenti senza esserlo davvero.", ""]

        righe = []
        for sym in ordine:
            x = st[sym]
            if x["piano"]:
                moda, n_moda = collections.Counter(a_ for _, a_ in x["piano"]).most_common(1)[0]
                date = sorted(d for d, a_ in x["piano"] if d and a_ == moda)
                gap = [(b_ - a_).days for a_, b_ in zip(date, date[1:])]
                cadenza = "%d giorni" % statistics.median(gap) if gap else "-"
                altri = len(x["piano"]) - n_moda
                fisso = "ricorrente in %d su %d" % (n_moda, len(x["piano"])) if altri else "fisso"
            else:
                cadenza, fisso = "-", "-"
            stato = "chiusa" if abs(x["pos"]) < 1e-6 else "aperta"
            righe.append((nome_tit[sym], x["classe"], x["domicilio"], len(x["piano"]), x["da_saveback"], x["manuali"], x["vendite"],
                          x["migrazioni"], x["dividendi"], x["saveback"], cadenza, fisso, stato))
        self.md += md_tabella(["Titolo", "Classe", "Paese ISIN", "Acquisti piano", "Acquisti da saveback", "Acquisti manuali", "Vendite",
                               "Migrazioni", "Dividendi", "Saveback", "Cadenza piano (mediana, importo ricorrente)", "Importo piano",
                               "Posizione a fine file"], righe) + [""]

    def controlli_credem(self):
        D = self.dati
        g = lambda r, n: self.valore(r, n)
        carte = [r for r in D if str(g(r, "Causale")).strip() == "PDINT" or str(g(r, "Causale ABI")).strip() == "43"]
        rx = re.compile(r"-(\d{2}/\d{2}/\d{2})-(\d{2}:\d{2})\s+(.*?)\s+-([A-Z]{3})\s*$")
        estratti, paesi, ok = [], collections.Counter(), 0
        for r in carte:
            m = rx.search(str(g(r, "Descrizione:")))
            if m:
                ok += 1
                paesi[m.group(4)] += 1
                testo = m.group(3)
                if self.mask.re_nomi:
                    testo = self.mask.re_nomi.sub("", testo, count=1).strip()
                estratti.append(re.sub(r"\s{2,}", " ", testo))
        out = [("Pagamenti carta (Causale PDINT o ABI 43)", len(carte)),
               ("Con data/ora operazione ed esercente estraibili", "%d su %d" % (ok, len(carte))),
               ("Codici paese finali", ", ".join("%s: %d" % kv for kv in paesi.most_common()) or "-")]
        if not self.mask.re_nomi:
            out.append(("Attenzione", "nessun nome configurato: il nome del titolare resta davanti all'esercente"))
        imp, orig = self.col.get("Importo"), self.col.get("Importo orig.")
        if imp and orig:
            div = sum(1 for r in D if parse_numero(r[imp.indice])[0] != parse_numero(r[orig.indice])[0])
            out.append(("Righe con Importo diverso da Importo orig.", div))
        saldi = [str(r[0]) for r in self.testa if r and not vuoto(r[0]) and "saldo" in str(r[0]).lower()]
        out.append(("Saldi in testa", ", ".join(saldi) or "nessuno"))
        out.append(("Quadratura", "non possibile in un solo file: c'e' il saldo alla data, non quello iniziale"))
        self.md += md_tabella(["Controllo", "Esito"], out) + [""]
        self.esercenti_anonimi(estratti, "Esercenti estratti dai pagamenti carta")

    def controlli_ing_carta(self):
        D = self.dati
        g = lambda r, n: self.valore(r, n)
        desc = [str(g(r, "DESCRIZIONE OPERAZIONE") or "") for r in D]
        presso = [re.match(r"\s*Operazione presso\s+(.+?)\s*$", d) for d in desc]
        importi = [parse_numero(g(r, "IMPORTO IN EURO"))[0] for r in D]
        dec = collections.Counter()
        for r in D:
            v = str(g(r, "IMPORTO IN EURO") or "").strip()
            m = re.search(r",(\d+)$", v)
            dec[len(m.group(1)) if m else 0] += 1
        ordine = 0
        for r in D:
            do, dr = parse_data(g(r, "DATA OPERAZIONE"))[0], parse_data(g(r, "DATA REGISTRAZIONE"))[0]
            if do and dr and dr < do:
                ordine += 1
        out = [("Righe con \"Operazione presso <esercente>\"", "%d su %d" % (sum(1 for m in presso if m), len(D))),
               ("Importi negativi / positivi (rimborsi) / zero",
                "%d / %d / %d" % (sum(1 for v in importi if v is not None and v < 0), sum(1 for v in importi if v and v > 0),
                                  sum(1 for v in importi if v == 0))),
               ("Cifre decimali degli importi", ", ".join("%d: %d" % kv for kv in sorted(dec.items()))),
               ("Data registrazione precedente alla data operazione", "%d righe (attese 0 su un file reale)" % ordine),
               ("Esercenti con asterisco (intermediario o piattaforma)", sum(1 for d in desc if "*" in d)),
               ("Metadati in testa (carta, titolare, periodo)", "nessuno: la carta va indicata all'import"),
               ("Codice MCC", "assente"),
               ("Quadratura", "non possibile nel file; si verifica contro l'addebito \"Estratto conto carta di credito\" sul conto ING")]
        if dec.get(0) or dec.get(1):
            out.append(("Nota sui decimali", "importi senza i due decimali fissi: il file sembra derivato da celle numeriche "
                                          "(es. salvato da Excel); verificare il formato originale scaricato dalla banca"))
        self.md += md_tabella(["Controllo", "Esito"], out) + [""]
        self.esercenti_anonimi([m.group(1) for m in presso if m], "Esercenti dopo \"Operazione presso\"")

    def controlli_ing(self):
        D = self.dati
        g = lambda r, n: self.valore(r, n)
        carte = [r for r in D if re.match(r"(pagamento|prelievo) carta", str(g(r, "CAUSALE")).strip(), re.I)]
        presso = [re.search(r"presso\s+(.+)$", str(g(r, "DESCRIZIONE OPERAZIONE"))) for r in carte]
        out = [("Pagamenti e prelievi con carta", len(carte)),
               ("Con esercente dopo \"presso\"", "%d su %d" % (sum(1 for m in presso if m), len(carte)))]
        iban = collections.Counter(str(g(r, "CAUSALE")) for r in D if RE_IBAN.search(str(g(r, "DESCRIZIONE OPERAZIONE"))))
        out.append(("Causali con IBAN nella descrizione", ", ".join("%s: %d" % kv for kv in iban.most_common()) or "nessuna"))
        prima = self.colonne[0]
        out.append(("Prima colonna", "vuota" if prima.tipo == "vuota" else "compilata"))
        # quadratura
        def saldo(etichetta):
            for r in self.testa:
                testo = " ".join(str(c) for c in r if not vuoto(c))
                m = re.search(etichetta + r"\s*:\s*([-+]?[\d.]+,\d+)", testo, re.I)
                if m:
                    return parse_numero(m.group(1))[0]
            return None
        si, sf = saldo("Saldo iniziale"), saldo("Saldo finale")
        imp = self.colonna_importo()
        if si is not None and sf is not None and imp:
            somma = sum(parse_numero(r[imp.indice])[0] or 0 for r in D)
            out.append(("Quadratura saldo iniziale + movimenti = saldo finale", "OK" if abs(si + somma - sf) < 0.01 else "NON quadra"))
        else:
            out.append(("Quadratura", "saldi non trovati in testa"))
        self.md += md_tabella(["Controllo", "Esito"], out) + [""]
        self.esercenti_anonimi([m.group(1).strip() for m in presso if m], "Esercenti dopo \"presso\"")

    # -- documento ----------------------------------------------------------
    def documento(self):
        self.carica()
        self.md += ["# Specifica del formato: %s" % self.cfg.get("nome", "formato non censito"), "",
                    "Generato offline da profila_movimenti.py v%s. Soglia di anonimato k = %d. "
                    "Nomi configurati da mascherare: %d (non elencati). Le date reali non compaiono." % (VERSIONE, self.k, len(set(self.mask.nomi))), "",
                    "**Rileggi il documento prima di condividerlo.** Il mascheramento e' automatico e potrebbe non "
                    "coprire nomi senza parole chiave riconoscibili.", ""]
        if not self.mask.nomi:
            self.md += ["> Attenzione: nessun nome configurato con `--nomi`. Il nome del titolare potrebbe comparire nei testi.", ""]
        self.sezione_riconoscimento()
        self.sezione_metadati()
        self.sezione_colonne()
        self.sezione_categoriali()
        self.sezione_combinazioni()
        self.sezione_descrizioni()
        self.controlli()
        self.sezione_esempi()
        return "\n".join(self.md) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Profilazione offline degli export bancari, con dati mascherati.")
    ap.add_argument("file", nargs="+", help="file CSV o Excel da profilare")
    ap.add_argument("--nomi", default="", help="nomi da mascherare, separati da virgola (es. \"Mario,Rossi\")")
    ap.add_argument("--out", default="profili", help="cartella dei report (default: profili)")
    ap.add_argument("--k", type=int, default=3, help="occorrenze minime per mostrare un testo (default 3)")
    ap.add_argument("--senza-esempi", action="store_true", help="non includere righe di esempio")
    ap.add_argument("--senza-esercenti", action="store_true", help="non elencare i nomi degli esercenti")
    ap.add_argument("--solo-locale", default="", help="altre parole da non mostrare mai, separate da virgola")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    nomi = [n for n in re.split(r"[,;]", a.nomi) if n.strip()]
    parti = []
    for n in nomi:
        parti.extend(n.split())
    for percorso in a.file:
        mask = Mascheratore(parti, a.solo_locale.split(","))
        p = Profilo(percorso, mask, max(1, a.k), not a.senza_esempi, not a.senza_esercenti)
        try:
            testo = p.documento()
        except Exception as e:
            print("ERRORE su %s: %s" % (percorso, e))
            continue
        base = os.path.splitext(os.path.basename(percorso))[0]
        base = re.sub(r"\d", "#", base).replace("#", "")
        base = re.sub(r"[^\w-]+", "_", base).strip("_") or "file"
        dest = os.path.join(a.out, "profilo_%s.md" % base)
        i = 2
        while os.path.exists(dest):
            dest = os.path.join(a.out, "profilo_%s_%d.md" % (base, i))
            i += 1
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(testo)
        print("Creato %s (%s, %d righe di dati)" % (dest, p.cfg.get("nome", "formato non censito"), len(p.dati)))


if __name__ == "__main__":
    main()
