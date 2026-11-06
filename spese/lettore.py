"""Lettura dei file bancari guidata dai profili (config/profili/*.yaml)."""
import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import configurazione as cfg
from .chiavi import chiave, separa_intermediario, togli_nomi
from .numeri import FormatoNonValido, a_centesimi, a_data, testo_decimal

RIGHE_TESTA = 60


class FileNonRiconosciuto(Exception):
    pass


@dataclass
class FileLetto:
    profilo: dict
    righe: list                    # righe grezze (liste di valori)
    riga_intestazione: int
    intestazione: list
    metadati: dict = field(default_factory=dict)
    movimenti: list = field(default_factory=list)
    scartate: list = field(default_factory=list)   # (n_riga, motivo)
    avvisi: list = field(default_factory=list)
    hash_file: str = ""


def _vuoto(v):
    return v is None or (isinstance(v, str) and not v.strip())


def leggi_grezzo(percorso: Path, dati: bytes = None):
    """Restituisce (righe, formato). CSV: testo; Excel: valori tipizzati."""
    dati = dati if dati is not None else Path(percorso).read_bytes()
    nome = str(percorso).lower()
    if nome.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(dati), read_only=True, data_only=True)
        righe = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
        return righe, "xlsx", dati
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            testo = dati.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    prime = testo.splitlines()[:RIGHE_TESTA]
    migliore, punti = ",", -1
    for sep in (";", "\t", ",", "|"):
        conti = [len(next(csv.reader([r], delimiter=sep))) for r in prime if r.strip()]
        conti = [c for c in conti if c > 1]
        if conti:
            moda = max(set(conti), key=conti.count)
            p = moda * conti.count(moda)
            if p > punti:
                migliore, punti = sep, p
    righe = list(csv.reader(io.StringIO(testo), delimiter=migliore))
    return righe, "csv", dati


def riconosci(righe):
    """Trova profilo e riga di intestazione dalla firma delle colonne."""
    for p in cfg.profili().values():
        firma = set(p["firma"])
        for i, r in enumerate(righe[:RIGHE_TESTA]):
            celle = {str(c).strip() for c in r if not _vuoto(c)}
            if firma <= celle:
                return p, i
    raise FileNonRiconosciuto("nessun profilo riconosce le colonne di questo file")


def _metadati(profilo, testa):
    out = {}
    mappa = profilo.get("metadati", {})
    for r in testa:
        celle = [str(c).strip() for c in r if not _vuoto(c)]
        if not celle:
            continue
        testo = " ".join(celle)
        if len(celle) >= 2:
            etichetta, valore = celle[0].rstrip(":").strip(), " ".join(celle[1:])
        elif ":" in celle[0]:
            etichetta, valore = [x.strip() for x in celle[0].split(":", 1)]
        else:
            etichetta, valore = None, celle[0]
        if etichetta and etichetta in mappa:
            out[mappa[etichetta]] = valore
        if profilo.get("periodo"):
            m = re.search(profilo["periodo"], testo)
            if m:
                out["periodo_da"], out["periodo_a"] = m.group("da"), m.group("a")
    return out


def _colonna(profilo, campo):
    c = profilo.get("colonne", {}).get(campo)
    if isinstance(c, dict):
        return c["colonna"], c.get("formato")
    return c, None


def _tipo(profilo, valori_codice):
    if profilo.get("tipo_fisso"):
        return profilo["tipo_fisso"], ""
    tipi = profilo.get("tipi", {})
    for v in valori_codice:
        if v and v in tipi:
            return tipi[v], v
    return tipi.get("*", "altro"), next((v for v in valori_codice if v), "")


def leggi(percorso, titolari=(), dati: bytes = None) -> FileLetto:
    righe, formato, dati = leggi_grezzo(Path(percorso), dati)
    profilo, h = riconosci(righe)
    intest = [("" if c is None else str(c).strip()) for c in righe[h]]
    idx = {n: i for i, n in enumerate(intest) if n}
    fl = FileLetto(profilo, righe, h, intest, _metadati(profilo, righe[:h]),
                   hash_file=hashlib.sha256(dati).hexdigest())
    if profilo.get("lettore") == "paypal":
        # piu' righe per la stessa operazione (pagamento, ricarica, conversioni): lettura dedicata
        from . import paypal
        return paypal.leggi(fl, titolari)
    numeri = profilo["numeri"]
    formato_date = profilo["date"]

    def val(r, campo):
        nome, _ = _colonna(profilo, campo)
        if not nome or nome not in idx:
            return None
        i = idx[nome]
        return r[i] if i < len(r) else None

    def data(r, campo):
        nome, fmt = _colonna(profilo, campo)
        v = val(r, campo)
        fmt = fmt or formato_date
        if fmt == "excel":
            return a_data(v, "%d/%m/%Y")
        return a_data(v, fmt)

    estrazioni = {t: [re.compile(x) for x in xs] for t, xs in profilo.get("estrazione", {}).items()}
    larghezza = len([c for c in intest if c])
    righe_saldo = {k.strip().lower(): v for k, v in (profilo.get("righe_saldo") or {}).items()}

    def importo(r):
        """Importo con segno. Con due colonne (uscite/entrate) vale quella compilata: le uscite sono sempre
        negative e le entrate positive, anche se la banca non scrive il segno."""
        if not _colonna(profilo, "importo_uscite")[0]:
            return a_centesimi(val(r, "importo"), numeri)
        u = a_centesimi(val(r, "importo_uscite"), numeri)
        e = a_centesimi(val(r, "importo_entrate"), numeri)
        if u not in (None, 0) and e not in (None, 0):
            raise FormatoNonValido("importo sia nelle uscite sia nelle entrate")
        if u not in (None, 0):
            return -abs(u)
        if e is not None:
            return abs(e)
        return u
    for n, r in enumerate(righe[h + 1:], start=h + 2):
        piene = sum(1 for c in r if not _vuoto(c))
        if piene == 0:
            continue
        if piene < min(2, larghezza):
            fl.scartate.append((n, "riga incompleta o di totale"))
            continue
        descr_riga = str(val(r, "descrizione") or "").strip().lower()
        if descr_riga in righe_saldo:
            # 'Saldo iniziale' / 'Saldo finale' scritti come righe: servono alla quadratura, non sono movimenti
            try:
                cent = importo(r)
                fl.metadati[righe_saldo[descr_riga]] = ("%+.2f" % ((cent or 0) / 100)).replace(".", ",")
                dt = data(r, "data_contabile")[0]
                if dt:
                    fl.metadati["data_" + righe_saldo[descr_riga]] = dt
            except FormatoNonValido:
                fl.scartate.append((n, "saldo non leggibile"))
            continue
        try:
            codici = []
            for c in profilo.get("codice", []):
                i = idx.get(c)
                v = r[i] if i is not None and i < len(r) else None
                codici.append("" if _vuoto(v) else str(v).strip())
            tipo, codice = _tipo(profilo, codici)
            descr = str(val(r, "descrizione") or "").strip()
            m = {"n_riga": n, "tipo": tipo, "codice": codice or "|".join(c for c in codici if c),
                 "descrizione": descr, "grezzo": [None if c is None else str(c) for c in r]}
            d_op, ora = data(r, "data_operazione")
            m["data_contabile"] = data(r, "data_contabile")[0]
            m["data_valuta"] = data(r, "data_valuta")[0]
            m["data_registrazione"] = data(r, "data_registrazione")[0]
            m["importo_cent"] = importo(r)
            if isinstance(tipo, dict):
                # stessa causale con significato diverso secondo il segno (es. INTERESSI/COMPETENZE)
                tipo = tipo.get("uscita" if (m["importo_cent"] or 0) < 0 else "entrata", "altro")
                m["tipo"] = tipo
            m["fee_cent"] = a_centesimi(val(r, "fee"), numeri) or 0
            m["tax_cent"] = a_centesimi(val(r, "tax"), numeri) or 0
            m["importo_orig"] = testo_decimal(val(r, "importo_orig"), numeri)
            m["divisa_orig"] = (str(val(r, "divisa_orig") or "").strip() or None)
            m["cambio"] = testo_decimal(val(r, "cambio"), numeri)
            m["quantita"] = testo_decimal(val(r, "quantita"), numeri)
            m["prezzo"] = testo_decimal(val(r, "prezzo"), numeri)
            m["isin"] = (str(val(r, "isin") or "").strip() or None)
            mcc = str(val(r, "mcc") or "").strip()
            m["mcc"] = mcc.zfill(4) if mcc else None
            m["id_banca"] = (str(val(r, "id") or "").strip() or None)
            m["controparte_nome"] = (str(val(r, "controparte_nome") or "").strip() or None)
            m["controparte_iban"] = (str(val(r, "controparte_iban") or "").strip() or None)
            if tipo in profilo.get("ignora_controparte_colonne", []):
                m["controparte_nome"] = m["controparte_iban"] = None
            esercente = (str(val(r, "esercente") or "").strip() or None)
            if profilo.get("nome_titolo_da") == "esercente" and m["isin"]:
                m["nome_titolo"] = esercente
            if profilo.get("esercente_solo_per") and tipo not in profilo["esercente_solo_per"]:
                esercente = None
            if not esercente and tipo in profilo.get("esercente_solo_per", []) and profilo.get("esercente_ripiego_descrizione"):
                if re.match(profilo["esercente_ripiego_descrizione"], descr):
                    esercente = descr or None
            ora_op = ora
            for rx in estrazioni.get(tipo, []):
                x = rx.search(descr)
                if not x:
                    continue
                g = {k: v for k, v in x.groupdict().items() if v}
                if "esercente" in g:
                    esercente = g["esercente"]
                if "controparte" in g and not m["controparte_nome"]:
                    m["controparte_nome"] = g["controparte"].strip()
                if "iban" in g and not m["controparte_iban"]:
                    m["controparte_iban"] = g["iban"]
                if "paese" in g:
                    m["paese"] = g["paese"]
                if "ciclo" in g:
                    c = g["ciclo"]
                    m["ciclo_carta"] = "%s-%s-%s" % (c[:4], c[4:6], c[6:8])
                if "divisa_orig" in g and g["divisa_orig"] != "EUR":
                    m["divisa_orig"] = g["divisa_orig"]
                if "data_op" in g and not d_op:
                    try:
                        d_op = a_data(g["data_op"], profilo.get("formato_data_op", "%d/%m/%Y"))[0]
                    except FormatoNonValido:
                        pass
                if "ora" in g and not ora_op:
                    ora_op = g["ora"]
            if esercente and tipo in profilo.get("titolari_davanti_esercente", []):
                esercente = togli_nomi(esercente, titolari)
            if esercente:
                esercente, inter = separa_intermediario(esercente)
                m["intermediario"] = inter
                m["esercente"] = re.sub(r"null$", "", esercente).strip() or None
                m["chiave"] = chiave(m["esercente"])
            m["ora"] = ora_op
            m["data"] = d_op or m["data_registrazione"] or m["data_contabile"] or m["data_valuta"]
            if not m["data"]:
                raise FormatoNonValido("data mancante")
            if m["importo_cent"] is None and tipo not in ("movimento_titoli",):
                if m["tax_cent"] == 0 and m["fee_cent"] == 0:
                    raise FormatoNonValido("importo mancante")
            fl.movimenti.append(m)
        except (FormatoNonValido, ValueError) as e:
            fl.scartate.append((n, str(e)))
    if fl.scartate and len(fl.movimenti) < 0.1 * (len(fl.movimenti) + len(fl.scartate)):
        fl.avvisi.append("Il profilo legge meno del 10% delle righe: probabilmente il formato e' cambiato.")
    return fl
