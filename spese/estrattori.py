"""Regole note per estrarre informazioni da un campo del file (di solito la descrizione), per i formati di import
configurati dall'utente.

Unico punto da cui passano queste regole: il formato salva il nome della regola, non l'espressione, cosi' una
regola migliorata qui vale dai prossimi import senza rifare il formato. Le regole vengono dai profili dell'app
(Credem, ING, Fineco...) e da forme generiche (IBAN con la cifra di controllo, ora, data). Si affinano sui file
reali: vedi STATUS, "Formati di import: affinare l'estrazione dalla descrizione".

I profili dell'app non passano da qui: hanno le loro regole `estrazione` nel YAML.
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta

from .iban import verifica as verifica_iban

# informazioni che si possono estrarre, con i tipi di movimento per cui valgono (None = tutti)
TIPI_SPESA = ("pagamento_carta", "addebito_sepa")
TIPI_PERSONA = ("bonifico", "giroconto", "stipendio")
INFORMAZIONI = {
    "esercente": {"nome": "Esercente", "tipi": TIPI_SPESA,
                  "descrizione": "Pagamenti con carta e addebiti diretti"},
    "controparte": {"nome": "Controparte", "tipi": TIPI_PERSONA, "descrizione": "Bonifici, giroconti e stipendi"},
    "iban": {"nome": "IBAN della controparte", "tipi": TIPI_PERSONA,
             "descrizione": "Bonifici, giroconti e stipendi"},
    "data_op": {"nome": "Data dell'operazione", "tipi": None, "descrizione": "Tutti i movimenti"},
    "ora": {"nome": "Ora dell'operazione", "tipi": None, "descrizione": "Tutti i movimenti"},
}

# una data dell'operazione si accetta solo vicino alla data contabile: evita di prendere altre date del testo
# (scadenze, periodi di riferimento)
GIORNI_PRIMA = 90
GIORNI_DOPO = 3

_FINE = r"\s*$"


@dataclass(frozen=True)
class Regola:
    nome: str           # identificativo salvato nel formato: non si cambia
    info: str           # informazione estratta (chiave di INFORMAZIONI)
    etichetta: str      # come la vede l'utente
    regex: str          # con il gruppo che ha il nome dell'informazione (giorno_mese per la data senza anno)


REGOLE = [
    # esercente
    Regola("tutto_il_campo_esercente", "esercente", "Tutto il testo della colonna", r"^\s*(?P<esercente>.+?)" + _FINE),
    Regola("dopo_pos_data", "esercente", "Dopo «POS gg/mm»",
           r"(?i)^\s*POS\s+\d{2}[/.-]\d{2}(?:[/.-]\d{2,4})?(?:\s+\d{2}[:.]\d{2})?\s+(?P<esercente>.+?)" + _FINE),
    Regola("dopo_presso", "esercente", "Dopo «presso»",
           r"(?i)\bpresso:?\s*(?P<esercente>.+?)(?:\s+carta\s+n\b.*)?" + _FINE),
    Regola("credem_carta", "esercente", "Tra «-gg/mm/aa-hh:mm» e «-PAESE» (Credem)",
           r"-\d{2}/\d{2}/\d{2}-\d{2}:\d{2}\s+(?P<esercente>.*?)\s+-[A-Z]{3}" + _FINE),
    Regola("preautorizzato", "esercente", "Tra «PAGAMENTO PREAUTORIZZATO» e «MANDATO»",
           r"(?i)PAGAMENTO PREAUTORIZZATO\s+(?P<esercente>.+?)\s+MANDATO\b"),
    Regola("dopo_sdd", "esercente", "Dopo «SDD» (fino a «MANDATO»)",
           r"(?i)^\s*(?:ADDEBITO\s+)?SDD\s+(?P<esercente>.+?)(?:\s+MANDATO\b.*)?" + _FINE),
    # controparte
    Regola("tutto_il_campo_controparte", "controparte", "Tutto il testo della colonna",
           r"^\s*(?P<controparte>.+?)" + _FINE),
    Regola("a_favore_di", "controparte", "Dopo «A FAVORE DI»",
           r"(?i)\ba favore di:?\s+(?P<controparte>.+?)(?:\s+(?:IBAN\b|Rif\.|RIF[=.]|CAUSALE\b|NOTE\b).*)?" + _FINE),
    Regola("ordinante", "controparte", "Dopo «Ordinante»",
           r"(?i)\b(?:Anagrafica\s+)?Ordinante:?\s+(?P<controparte>.+?)"
           r"(?:\s+(?:Note:|Beneficiario:|Causale:|IBAN\b|Banca\b).*)?" + _FINE),
    Regola("beneficiario", "controparte", "Dopo «Beneficiario:» o «Ben:»",
           r"(?i)\b(?:Beneficiario|Ben):\s*(?P<controparte>.+?)(?:\s+(?:IBAN\b|Ins:|Da:|Causale:|Data\b).*)?" + _FINE),
    Regola("credem_bonifico", "controparte", "Dopo «A CREDITO» o «A DEBITO» (Credem)",
           r"A (?:CREDITO|DEBITO)\s+(?:A FAVORE DI\s+)?(?P<controparte>.+?)\s+(?:Rif\.|RIF[=.])"),
    # IBAN
    Regola("iban_controllato", "iban", "IBAN con la cifra di controllo",
           r"(?<![A-Z0-9])(?P<iban>[A-Z]{2}\d{2}[A-Z0-9]{11,30})(?![A-Z0-9])"),
    # data dell'operazione
    Regola("data_completa", "data_op", "Data gg/mm/aaaa o gg/mm/aa",
           r"(?<![\d/.-])(?P<data_op>\d{2}[/.-]\d{2}[/.-](?:\d{4}|\d{2}))(?![\d/.-])"),
    Regola("giorno_mese", "data_op", "Giorno e mese gg/mm (anno dalla data contabile)",
           r"(?<![\d/.-])(?P<giorno_mese>\d{2}/\d{2})(?![\d/.-])"),
    # ora
    Regola("ora_hhmm", "ora", "Ora hh:mm", r"(?<!\d)(?P<ora>(?:[01]\d|2[0-3]):[0-5]\d)(?!\d)"),
]

PER_NOME = {r.nome: r for r in REGOLE}
_COMPILATE = {}


def regole_di(info):
    return [r for r in REGOLE if r.info == info]


def _compilata(regola):
    if regola.nome not in _COMPILATE:
        _COMPILATE[regola.nome] = re.compile(regola.regex)
    return _COMPILATE[regola.nome]


def _data_vicina(g, m, a, rif):
    """Data valida e vicina alla data di riferimento (la contabile), altrimenti None."""
    try:
        d = date(a, m, g)
    except ValueError:
        return None
    if rif and not (rif - timedelta(days=GIORNI_PRIMA) <= d <= rif + timedelta(days=GIORNI_DOPO)):
        return None
    return d


def _valore(regola, testo, data_rif=None):
    """Applica una regola a un testo: il valore estratto (stringa; la data in ISO) o None."""
    if not testo:
        return None
    x = _compilata(regola).search(str(testo))
    if not x:
        return None
    rif = date.fromisoformat(data_rif) if data_rif else None
    if regola.info == "data_op":
        if "giorno_mese" in x.groupdict():
            if not rif:
                return None
            g, m = (int(v) for v in x.group("giorno_mese").split("/"))
            d = _data_vicina(g, m, rif.year, rif) or _data_vicina(g, m, rif.year - 1, rif)
        else:
            g, m, a = (int(v) for v in re.split(r"[/.-]", x.group("data_op")))
            d = _data_vicina(g, m, a + 2000 if a < 100 else a, rif)
        return d.isoformat() if d else None
    v = re.sub(r"\s+", " ", x.group(regola.info) or "").strip()
    if regola.info == "iban":
        v = v.upper()
        if verifica_iban(v):                # cifra di controllo sbagliata: meglio nessun IBAN che uno inventato
            return None
    return v or None


def estrai(estrattori, tipo, valori, data_rif=None):
    """Informazioni estratte da una riga con le scelte del formato.

    estrattori: {informazione: {"colonna": nome, "regole": [nomi, in ordine]}} (dal formato); valori: {colonna:
    testo} della riga; data_rif: data contabile ISO, per controllare la data dell'operazione e completarne l'anno.
    Vale la prima regola che trova qualcosa. Restituisce {informazione: valore}; una regola sconosciuta (formato di
    una versione piu' recente) si ignora."""
    out = {}
    for info, scelta in (estrattori or {}).items():
        spec = INFORMAZIONI.get(info)
        if not spec or (spec["tipi"] and tipo not in spec["tipi"]):
            continue
        for nome in nomi_regole(scelta):
            regola = PER_NOME.get(nome)
            if not regola or regola.info != info:
                continue
            v = _valore(regola, valori.get((scelta or {}).get("colonna")), data_rif)
            if v:
                out[info] = v
                break
    return out


def nomi_regole(scelta):
    """Regole scelte per un'informazione, in ordine ('regola' singola nei formati salvati senza elenco)."""
    scelta = scelta or {}
    if scelta.get("regole"):
        return list(scelta["regole"])
    return [scelta["regola"]] if scelta.get("regola") else []


def proponi(info, righe):
    """Regole dell'informazione provate sulle righe del file: [(regola, trovati, totale, esempi)], dalla migliore.

    righe: [(tipo, testo, data_rif)] delle righe del file, gia' lette con il formato in prova; contano solo quelle
    dei tipi per cui l'informazione vale. Le regole "tutto il testo" trovano sempre qualcosa: vanno in fondo, e si
    scelgono a mano (servono quando la banca ha una colonna dedicata, es. il nome dell'esercente)."""
    spec = INFORMAZIONI[info]
    utili = [(t, s, d) for t, s, d in righe if not spec["tipi"] or t in spec["tipi"]]
    out = []
    for regola in regole_di(info):
        trovati, esempi = 0, []
        for _, testo, d in utili:
            v = _valore(regola, testo, d)
            if v:
                trovati += 1
                if len(esempi) < 3 and (testo, v) not in esempi:
                    esempi.append((testo, v))
        out.append((regola, trovati, len(utili), esempi))
    out.sort(key=lambda x: (x[0].nome.startswith("tutto_il_campo"), -x[1]))
    return out


def proposta(info, righe):
    """Regole proposte, in ordine: la migliore, poi quelle che trovano qualcosa nelle righe rimaste (es. "dopo
    POS gg/mm" per le carte e "dopo SDD" per gli addebiti). Escluse le "tutto il testo"."""
    spec = INFORMAZIONI[info]
    utili = [(s, d) for t, s, d in righe if not spec["tipi"] or t in spec["tipi"]]
    scelte, coperte = [], set()
    for regola, trovati, _, _ in proponi(info, righe):
        if not trovati or regola.nome.startswith("tutto_il_campo"):
            continue
        nuove = {k for k, (s, d) in enumerate(utili) if k not in coperte and _valore(regola, s, d)}
        if nuove:
            scelte.append(regola.nome)
            coperte |= nuove
    return scelte


def prova_regole(info, nomi, righe):
    """(trovati, totale, esempi) delle regole scelte, in ordine, sulle righe del file."""
    spec = INFORMAZIONI[info]
    utili = [(s, d) for t, s, d in righe if not spec["tipi"] or t in spec["tipi"]]
    trovati, esempi = 0, []
    for s, d in utili:
        for nome in nomi:
            regola = PER_NOME.get(nome)
            v = _valore(regola, s, d) if regola and regola.info == info else None
            if v:
                trovati += 1
                if len(esempi) < 3:
                    esempi.append((s, v))
                break
    return trovati, len(utili), esempi
