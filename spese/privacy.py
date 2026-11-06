"""Controllo di sicurezza prima di qualunque invio esterno.

Verso l'esterno esce solo la chiave dell'esercente (cifre gia' sostituite da #), il paese e la
direzione. Questo modulo blocca ogni testo che contenga dati identificativi o parole riservate.
"""
import re
from functools import lru_cache

from .db import leggi_impostazione
from .importa import nomi_riservati

CONTROLLI = [
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b", re.I)),
    ("numero di carta", re.compile(r"\d{4,6}[*xX]{4,}\d{2,4}|[xX*]{6,}\d{4}")),
    ("sequenza di cifre", re.compile(r"\d{5,}")),
    ("codice fiscale", re.compile(r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b", re.I)),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("telefono", re.compile(r"(\+|00)\d[\d ]{7,}")),
]

SOLO_LOCALE = ["farmac", "parafarmac", "humanitas", "ospedal", "clinic", "medic", "dentist", "odontoiatr", "ortodon",
               "ottic", "optic", "psicolog", "psicoterap", "sanitar", "poliambulat", "fisioterap", "laboratorio analisi",
               "diagnostic", r"\basl\b", r"\bats\b", "pharma", "apotheke", "hospital", "veterinar",
               r"\bdott\b", "dottssa", "osteopat", "podolog", "ortoped", "pediatr", "ginecolog", "dermatolog",
               "cardiolog", "oculist", "radiolog", "logoped"]


def parole_riservate(conn):
    extra = leggi_impostazione(conn, "parole_solo_locale", []) or []
    return SOLO_LOCALE + [re.escape(w.strip().lower()) for w in extra if w.strip()]


@lru_cache(maxsize=1)
def _l2_riservate_di_partenza():
    import yaml

    from . import configurazione as cfg
    base = yaml.safe_load((cfg.CONFIG / "tassonomia.yaml").read_text(encoding="utf-8")) or {}
    aree = {x.upper() for x in base.get("solo_locale", [])}
    return frozenset(l2 for sezione in ("uscite", "entrate", "risparmio")
                     for l1, voci in (base.get(sezione) or {}).items() if l1.upper() in aree for l2 in (voci or []))


def riservate():
    """Categorie (l1, l2) dell'area solo locale: tutte quelle delle categorie elencate in solo_locale (SALUTE)
    nella tassonomia in uso e, per sicurezza, ogni voce con il nome di una sottocategoria della salute di
    partenza (Farmacia, Dentista, ...) anche se spostata sotto un'altra categoria."""
    from . import configurazione as cfg
    tax = cfg.tassonomia()
    aree = {x.upper() for x in tax.solo_locale}
    base = _l2_riservate_di_partenza()
    return {c for c in tax.voci if c[0].upper() in aree or c[1] in base}


def l2_riservate():
    """Nomi delle sottocategorie riservate (per i messaggi e i controlli sui soli nomi)."""
    return {c[1] for c in riservate()} | _l2_riservate_di_partenza()


def area_riservata(conn, chiave):
    """L'esercente risulta gia' (anche solo come proposta) in una categoria dell'area solo locale? Vale la
    categoria in memoria (modello locale o Claude, anche a confidenza bassa), il codice MCC dei suoi movimenti,
    la categoria che ha gia' (regola, marchio, scelta tua) e la proposta della revisione."""
    if not chiave:
        return False
    ris = riservate()
    if not ris:
        return False
    from . import configurazione as cfg
    tax = cfg.tassonomia()

    def dentro(l2, l1=None):
        c = tax.risolvi(l2, l1) if l2 else None
        return bool(c and c in ris)
    r = conn.execute("SELECT l1, l2 FROM cache_esercenti WHERE chiave = ?", (chiave,)).fetchone()
    if r and dentro(r["l2"], r["l1"]):
        return True
    mcc = cfg.mcc()
    for (codice,) in conn.execute("SELECT DISTINCT mcc FROM movimenti WHERE chiave = ? AND mcc IS NOT NULL", (chiave,)):
        voce = mcc.get(str(codice).strip().zfill(4)) or mcc.get(str(codice).strip())
        if voce and dentro(voce.get("l2"), voce.get("l1")):
            return True
    # categoria che l'esercente ha gia' (regola, marchio, scelta tua): conta per le prove su esercenti classificati
    for l1, l2 in conn.execute("SELECT DISTINCT l1, l2 FROM analisi WHERE chiave = ? AND l2 IS NOT NULL", (chiave,)):
        if (l1, l2) in ris:
            return True
    from .catena import scomponi_proposta
    for (p,) in conn.execute("SELECT DISTINCT proposta FROM analisi WHERE chiave = ? AND proposta IS NOT NULL", (chiave,)):
        c = scomponi_proposta(p)["cat"]
        if c and tuple(c) in ris:
            return True
    return False


def controlla(conn, testo, chiave=None):
    """Restituisce l'elenco dei motivi di blocco (vuoto = inviabile). Con la chiave dell'esercente controlla
    anche che non sia gia' collocato nell'area solo locale."""
    motivi = [nome for nome, rx in CONTROLLI if rx.search(testo or "")]
    nomi = nomi_riservati(conn) + [w for w in (leggi_impostazione(conn, "parole_sensibili", []) or []) if w.strip()]
    for n in nomi:
        if re.search(r"\b%s\b" % re.escape(n), testo or "", re.I):
            motivi.append("nome o parola sensibile")
            break
    for w in parole_riservate(conn):
        if re.search(w, (testo or "").lower()):
            motivi.append("area riservata (solo locale)")
            break
    else:
        if chiave is not None and area_riservata(conn, chiave):
            motivi.append("area riservata (solo locale)")
    return motivi
