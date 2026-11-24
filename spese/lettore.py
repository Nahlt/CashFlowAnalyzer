"""Lettura dei file bancari guidata dai profili (config/profili/*.yaml)."""
import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import configurazione as cfg
from . import estrattori
from .chiavi import chiave, separa_intermediario, togli_nomi
from .iban import verifica as verifica_iban
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


SEPARATORI = (";", "\t", ",", "|")
CODIFICHE = ("utf-8-sig", "cp1252", "latin-1")


def decodifica(dati: bytes, codifica=None):
    """(testo, codifica). Senza codifica si prova UTF-8, poi Windows (cp1252), poi Latin-1 (legge tutto)."""
    for enc in ((codifica,) if codifica else CODIFICHE):
        try:
            return dati.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return dati.decode("latin-1", errors="replace"), "latin-1"


def indovina_separatore(testo):
    """Il separatore che da' piu' righe con lo stesso numero (>1) di colonne, tra le prime RIGHE_TESTA."""
    prime = testo.splitlines()[:RIGHE_TESTA]
    migliore, punti = ",", -1
    for sep in SEPARATORI:
        conti = [len(next(csv.reader([r], delimiter=sep))) for r in prime if r.strip()]
        conti = [c for c in conti if c > 1]
        if conti:
            moda = max(set(conti), key=conti.count)
            p = moda * conti.count(moda)
            if p > punti:
                migliore, punti = sep, p
    return migliore


def e_excel(percorso) -> bool:
    return str(percorso).lower().endswith((".xlsx", ".xlsm"))


def fogli_excel(dati: bytes) -> list:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(dati), read_only=True, data_only=True)
    return list(wb.sheetnames)


def leggi_grezzo(percorso: Path, dati: bytes = None, separatore=None, codifica=None, foglio=0):
    """Restituisce (righe, formato, dati). CSV: testo; Excel: valori tipizzati. Separatore e codifica dei CSV e
    foglio degli Excel si indovinano se non indicati."""
    dati = dati if dati is not None else Path(percorso).read_bytes()
    if e_excel(percorso):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(dati), read_only=True, data_only=True)
        ws = wb.worksheets[foglio] if 0 <= (foglio or 0) < len(wb.worksheets) else wb.worksheets[0]
        righe = [list(r) for r in ws.iter_rows(values_only=True)]
        return righe, "xlsx", dati
    testo, _ = decodifica(dati, codifica)
    righe = list(csv.reader(io.StringIO(testo), delimiter=separatore or indovina_separatore(testo)))
    return righe, "csv", dati


def attivi(profili=None):
    """Profili che riconoscono i file: tutti tranne i formati dell'utente ritirati."""
    return [p for p in (profili if profili is not None else cfg.profili().values()) if p.get("stato") != "ritirato"]


def riga_intestazione(profilo, righe):
    """Indice della riga con tutte le colonne della firma del profilo, oppure None."""
    firma = set(profilo["firma"])
    for i, r in enumerate(righe[:RIGHE_TESTA]):
        celle = {str(c).strip() for c in r if not _vuoto(c)}
        if firma <= celle:
            return i
    return None


def riconosci(righe, profili=None):
    """Trova profilo e riga di intestazione dalla firma delle colonne: prima i profili dell'app, poi i formati
    dell'utente (in quest'ordine in cfg.profili)."""
    for p in attivi(profili):
        h = riga_intestazione(p, righe)
        if h is not None:
            return p, h
    raise FileNonRiconosciuto("nessun profilo riconosce le colonne di questo file")


def _riconosci_file(percorso, dati):
    """(profilo, riga d'intestazione, righe). Prima con separatore, codifica e foglio indovinati; se nessun
    profilo riconosce il file, con il separatore e la codifica scritti nei profili dei CSV, o con gli altri fogli
    dell'Excel. Un formato dell'utente si rilegge con separatore e codifica scelti nella sua configurazione."""
    righe, formato, dati = leggi_grezzo(Path(percorso), dati)
    profilo = h = None
    try:
        profilo, h = riconosci(righe)
    except FileNonRiconosciuto:
        if formato == "xlsx":
            for i in range(1, len(fogli_excel(dati))):
                righe = leggi_grezzo(Path(percorso), dati, foglio=i)[0]
                try:
                    profilo, h = riconosci(righe)
                    break
                except FileNonRiconosciuto:
                    continue
        else:
            provati = set()
            for p in attivi():
                coppia = (p.get("separatore"), p.get("codifica"))
                if p.get("formato", "csv") != "csv" or coppia in provati or coppia == (None, None):
                    continue
                provati.add(coppia)
                righe = leggi_grezzo(Path(percorso), dati, separatore=coppia[0], codifica=coppia[1])[0]
                candidati = [q for q in attivi() if (q.get("separatore"), q.get("codifica")) == coppia]
                try:
                    profilo, h = riconosci(righe, candidati)
                    break
                except FileNonRiconosciuto:
                    continue
        if profilo is None:
            raise
    if profilo.get("utente") and formato == "csv" and (profilo.get("separatore") or profilo.get("codifica")):
        righe2 = leggi_grezzo(Path(percorso), dati, separatore=profilo.get("separatore"),
                              codifica=profilo.get("codifica"))[0]
        h2 = riga_intestazione(profilo, righe2)
        if h2 is not None:
            righe, h = righe2, h2
    return profilo, h, righe, dati


def coppia_testa(r, date_come_testo=True):
    """(etichetta, valore) di una riga sopra l'intestazione: 'Saldo al | 1.234,56', 'Conto: 123' o una cella
    sola (etichetta None). None per una riga vuota. date_come_testo: le date dell'Excel come gg/mm/aaaa (formati
    dell'utente); i profili dell'app leggono le celle come prima (str)."""
    celle = [(_testo_cella(c) if date_come_testo else str(c).strip()) for c in r if not _vuoto(c)]
    if not celle:
        return None
    if len(celle) >= 2:
        return celle[0].rstrip(":").strip(), " ".join(celle[1:])
    if ":" in celle[0]:
        e, v = [x.strip() for x in celle[0].split(":", 1)]
        return e, v
    return None, celle[0]


def _testo_cella(c):
    """Testo di una cella; le date dell'Excel come gg/mm/aaaa (come le scrivono le banche nei CSV)."""
    from datetime import date, datetime
    if isinstance(c, datetime):
        return c.strftime("%d/%m/%Y")
    if isinstance(c, date):
        return c.strftime("%d/%m/%Y")
    return str(c).strip()


DATA_IN_TESTO = r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}"


def data_testo(s):
    """Una data scritta in un testo, riportata a gg/mm/aaaa (come la vogliono copertura e saldi), oppure None."""
    from datetime import datetime
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%d-%m-%y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return None


def _regole_metadati(profilo, etichetta, valore, out):
    """Regole `metadati_regole` dei formati dell'utente: l'etichetta si riconosce con un'espressione (che puo'
    contenere la data, es. 'Saldo contabile al 31/10/2026'); date e importi si riportano ai formati che usano
    copertura e saldi (gg/mm/aaaa, numeri all'italiana)."""
    for regola in profilo.get("metadati_regole") or []:
        bersaglio = etichetta if etichetta is not None else valore
        x = re.fullmatch(regola["etichetta"], bersaglio or "")
        if not x:
            continue
        campo = regola["campo"]
        if campo == "periodo":
            date_trovate = [d for d in (data_testo(t) for t in re.findall(DATA_IN_TESTO, valore or "")) if d]
            if date_trovate:
                out["periodo_da"], out["periodo_a"] = date_trovate[0], date_trovate[-1]
        elif campo in ("saldo_alla_data", "saldo_iniziale", "saldo_finale"):
            try:
                cent = a_centesimi(re.sub(r"[^\d,.\-+]", "", valore or ""), profilo.get("numeri") or "italiano")
            except FormatoNonValido:
                cent = None
            if cent is not None:
                out[campo] = ("%+.2f" % (cent / 100)).replace(".", ",")
                if "data" in x.groupdict() and x.group("data") and data_testo(x.group("data")):
                    out["data_saldo"] = data_testo(x.group("data"))
        elif campo == "data_saldo":
            d = re.search(DATA_IN_TESTO, valore or "")
            if d and data_testo(d.group(0)):
                out["data_saldo"] = data_testo(d.group(0))
        elif valore:
            out[campo] = valore


def _metadati(profilo, testa):
    out = {}
    mappa = profilo.get("metadati", {})
    utente = bool(profilo.get("utente"))
    for r in testa:
        coppia = coppia_testa(r, utente)
        if not coppia:
            continue
        etichetta, valore = coppia
        testo = " ".join((_testo_cella(c) if utente else str(c).strip()) for c in r if not _vuoto(c))
        if etichetta and etichetta in mappa:
            out[mappa[etichetta]] = valore
        if profilo.get("periodo"):
            m = re.search(profilo["periodo"], testo)
            if m:
                out["periodo_da"], out["periodo_a"] = m.group("da"), m.group("a")
        _regole_metadati(profilo, etichetta, valore, out)
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


def cerca_senza_spazi(rx, testo):
    """Applica la regola al testo senza spazi e riporta i gruppi trovati al testo originale.

    Per le banche che spezzano la descrizione a lunghezza fissa inserendo spazi anche dentro parole e codici
    (Fineco): le etichette ("Beneficiario:", "IBAN:") si riconoscono anche se spezzate. Restituisce
    {gruppo: testo originale con gli spazi ridotti a uno} oppure None."""
    pos = [i for i, ch in enumerate(testo) if not ch.isspace()]
    compatto = "".join(testo[i] for i in pos)
    x = rx.search(compatto)
    if not x:
        return None
    out = {}
    for nome in x.groupdict():
        a, b = x.span(nome)
        if a < 0 or a == b:
            continue
        out[nome] = re.sub(r"\s+", " ", testo[pos[a]:pos[b - 1] + 1])
    return out


ORA_VALIDA = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def ora_da_descrizione(profilo, descr):
    """Ora dell'operazione scritta nella descrizione, con le regole `estrazione_ora` del profilo (valgono per
    qualunque tipo di movimento): 'HH:MM' o None. Serve per le banche che non hanno una colonna dell'ora ma a
    volte la scrivono nel testo (Fineco: "Pag. del 10/04/26 ora 12:30", "Ins: 20/11/2026 14:35:12")."""
    regole = profilo.get("estrazione_ora") or []
    if not regole or not descr:
        return None
    for regola in regole:
        rx = re.compile(regola)
        if profilo.get("estrazione_senza_spazi"):
            g = cerca_senza_spazi(rx, descr)
        else:
            x = rx.search(descr)
            g = x.groupdict() if x else None
        ora = re.sub(r"\s+", "", (g or {}).get("ora") or "")
        if ORA_VALIDA.fullmatch(ora):
            return ora
    return None


def _ricomponi_nome(nome, parole):
    """'MARIO RO SSI' -> 'MARIO ROSSI': riunisce i pezzi consecutivi (fino a tre) che insieme formano una parola
    dei nomi noti (titolari e familiari) e da soli no. Serve a riconoscere i bonifici a tuo nome."""
    note = {p.upper() for p in parole if p}
    pezzi, out, i = nome.split(), [], 0
    while i < len(pezzi):
        for k in (3, 2):
            unito = "".join(pezzi[i:i + k])
            if i + k <= len(pezzi) and unito.upper() in note and pezzi[i].upper() not in note:
                out.append(unito)
                i += k
                break
        else:
            out.append(pezzi[i])
            i += 1
    return " ".join(out)


def leggi(percorso, titolari=(), dati: bytes = None, profilo=None, riga_intestazione_scelta=None) -> FileLetto:
    """Legge un file con il profilo che lo riconosce. Con `profilo` (es. un formato in prova nella procedura
    guidata) si usa quello, con separatore, codifica e foglio scritti nel profilo e la riga d'intestazione indicata
    (o trovata con la firma)."""
    if profilo is None:
        profilo, h, righe, dati = _riconosci_file(percorso, dati)
    else:
        righe, _, dati = leggi_grezzo(Path(percorso), dati, separatore=profilo.get("separatore"),
                                      codifica=profilo.get("codifica"), foglio=profilo.get("foglio") or 0)
        h = riga_intestazione_scelta if riga_intestazione_scelta is not None else riga_intestazione(profilo, righe)
        if h is None or h >= len(righe):
            raise FileNonRiconosciuto("il formato non riconosce le colonne di questo file")
    intest = [("" if c is None else str(c).strip()) for c in righe[h]]
    idx = {n: i for i, n in enumerate(intest) if n}
    fl = FileLetto(profilo, righe, h, intest, _metadati(profilo, righe[:h]),
                   hash_file=hashlib.sha256(dati).hexdigest())
    if profilo.get("data_saldo_da") and fl.metadati.get("saldo_alla_data") and not fl.metadati.get("data_saldo"):
        # es. Fineco: il saldo finale vale alla data di fine del periodo estratto
        if fl.metadati.get(profilo["data_saldo_da"]):
            fl.metadati["data_saldo"] = fl.metadati[profilo["data_saldo_da"]]
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
    senza_spazi = bool(profilo.get("estrazione_senza_spazi"))
    larghezza = len([c for c in intest if c])
    solo_se = profilo.get("solo_se") or {}
    escluse = 0
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

    def importo_con_segno(r):
        """Importo della riga; i formati con `inverti_segno` (carte che scrivono le spese in positivo) lo
        rovesciano."""
        v = importo(r)
        return -v if v is not None and profilo.get("inverti_segno") else v
    for n, r in enumerate(righe[h + 1:], start=h + 2):
        piene = sum(1 for c in r if not _vuoto(c))
        if piene == 0:
            continue
        if piene < min(2, larghezza):
            fl.scartate.append((n, "riga incompleta o di totale"))
            continue
        if solo_se:
            i = idx.get(solo_se["colonna"])
            stato = "" if i is None or i >= len(r) or _vuoto(r[i]) else str(r[i]).strip()
            if stato not in solo_se["valori"]:
                fl.scartate.append((n, "%s (%s: %s)" % (solo_se.get("motivo", "riga esclusa"), solo_se["colonna"],
                                                         stato or "vuoto")))
                escluse += 1
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
            m["importo_cent"] = importo_con_segno(r)
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
                if senza_spazi:
                    g = cerca_senza_spazi(rx, descr)
                    if g is None:
                        continue
                    if "iban" in g:
                        g["iban"] = re.sub(r"\s+", "", g["iban"]).upper()
                        if verifica_iban(g["iban"]):
                            del g["iban"]           # pezzi mal ricomposti: meglio nessun IBAN che uno sbagliato
                    if "controparte" in g:
                        g["controparte"] = _ricomponi_nome(g["controparte"], titolari)
                else:
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
            if profilo.get("estrattori"):
                # formati dell'utente: informazioni estratte con le regole note (spese/estrattori.py)
                valori = {nome: ("" if i >= len(r) or _vuoto(r[i]) else _testo_cella(r[i])) for nome, i in idx.items()}
                g = estrattori.estrai(profilo["estrattori"], tipo, valori, m["data_contabile"])
                esercente = g.get("esercente") or esercente
                if g.get("controparte") and not m["controparte_nome"]:
                    m["controparte_nome"] = g["controparte"]
                if g.get("iban") and not m["controparte_iban"]:
                    m["controparte_iban"] = g["iban"]
                if g.get("data_op") and not d_op:
                    d_op = g["data_op"]
                if g.get("ora") and not ora_op:
                    ora_op = g["ora"]
            if not esercente and descr and tipo in (profilo.get("esercente_da_descrizione") or []):
                esercente = descr               # nessuna regola trova l'esercente: tutta la descrizione
            if esercente and tipo in profilo.get("titolari_davanti_esercente", []):
                esercente = togli_nomi(esercente, titolari)
            if esercente:
                esercente, inter = separa_intermediario(esercente)
                m["intermediario"] = inter
                m["esercente"] = re.sub(r"null$", "", esercente).strip() or None
                m["chiave"] = chiave(m["esercente"])
            m["ora"] = ora_op or ora_da_descrizione(profilo, descr)
            m["data"] = d_op or m["data_registrazione"] or m["data_contabile"] or m["data_valuta"]
            if not m["data"]:
                raise FormatoNonValido("data mancante")
            if m["importo_cent"] is None and tipo not in ("movimento_titoli",):
                if m["tax_cent"] == 0 and m["fee_cent"] == 0:
                    raise FormatoNonValido("importo mancante")
            fl.movimenti.append(m)
        except (FormatoNonValido, ValueError) as e:
            fl.scartate.append((n, str(e)))
    if len(fl.scartate) > escluse and len(fl.movimenti) < 0.1 * (len(fl.movimenti) + len(fl.scartate) - escluse):
        fl.avvisi.append("Il profilo legge meno del 10% delle righe: probabilmente il formato e' cambiato.")
    return fl
