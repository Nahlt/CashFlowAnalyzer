"""Controllo di sicurezza prima di qualunque invio esterno.

Verso l'esterno esce solo la chiave dell'esercente (cifre gia' sostituite da #), il paese e la
direzione. Questo modulo blocca ogni testo che contenga dati identificativi o parole riservate.

Tutto cio' che va verso Claude passa da un solo punto, `uscita_claude`: normalizza il testo (anche quello
corretto a mano) come una chiave, lo controlla insieme ai nomi originali dell'esercente e restituisce le voci
ammesse con il testo esatto che parte. L'elenco delle categorie inviato insieme viene da
`categorie_in_uscita` (le sottocategorie segnate "non inviare" nella tassonomia restano fuori).
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
# gli stessi dati dopo la normalizzazione della chiave (ogni gruppo di cifre e' diventato #): sulla chiave i
# controlli qui sopra non possono piu' scattare
CONTROLLI_MASCHERATI = [
    ("codice fiscale", re.compile(r"\b[A-Z]{6}#[A-Z]#[A-Z]#[A-Z]\b", re.I)),
    ("IBAN", re.compile(r"\b[A-Z]{2}#[A-Z]#(?![A-Z])", re.I)),
    ("numero di carta", re.compile(r"#\s?[*xX]{4,}\s?#|[xX*]{6,}\s?#")),
]
# sui nomi originali dell'esercente (prima della normalizzazione) si cercano i dati identificativi veri e
# propri; i codici di punto vendita ("GAR4857", "000123456") sono normali e spariscono con la chiave
_SOLO_SUL_TESTO = {"sequenza di cifre"}

SOLO_LOCALE = ["farmac", "parafarmac", "humanitas", "ospedal", "clinic", "medic", "dentist", "odontoiatr", "ortodon",
               "ottic", "optic", "psicolog", "psicoterap", "sanitar", "poliambulat", "fisioterap", "laboratorio analisi",
               "diagnostic", r"\basl\b", r"\bats\b", "pharma", "apotheke", "hospital", "veterinar",
               r"\bdott\b", "dottssa", "osteopat", "podolog", "ortoped", "pediatr", "ginecolog", "dermatolog",
               "cardiolog", "oculist", "radiolog", "logoped",
               # 0.16.2: molti estratti non riportano il codice MCC, quindi conta solo il nome
               r"\bdr\b", r"\bdssa\b", r"\bdottor", "irccs", "salute", "prelievi", r"\banalisi\b", "ambulator",
               "policlin", "fisio", "kinesi", "riabilitaz", "terapi", "chirurg", "infermier", "ostetric",
               "oncolog", "psichiatr", "urolog", "neurolog", "endocrin", "nutrizion", "dietolog", "dietist",
               "androlog", "senolog", "ematolog", "reumatolog", "gastroenter", "pneumolog", "nefrolog",
               "allergolog", "otorino", "geriatr", "odontotecn", "audioprotes", "amplifon", "ticket sanit",
               "auxologico", "santagostino", "sant agostino", "unisalute", "synlab", "cerba", "lifebrain",
               "multimedica", "san raffaele", "niguarda", "monzino", "galeazzi", "maugeri", r"\basst\b"]


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


def nomi_originali(conn, chiave):
    """I nomi dell'esercente come compaiono nei movimenti, prima della normalizzazione in chiave."""
    if not chiave:
        return []
    return [r[0] for r in conn.execute("SELECT DISTINCT esercente FROM movimenti WHERE chiave = ? "
                                       "AND esercente IS NOT NULL", (chiave,))]


def controlla(conn, testo, chiave=None):
    """Restituisce l'elenco dei motivi di blocco (vuoto = inviabile). Con la chiave dell'esercente controlla
    anche i nomi originali dei suoi movimenti (dove le cifre ci sono ancora) e che non sia gia' collocato
    nell'area solo locale."""
    motivi = [nome for nome, rx in CONTROLLI + CONTROLLI_MASCHERATI if rx.search(testo or "")]
    if chiave is not None:
        for originale in nomi_originali(conn, chiave):
            for nome, rx in CONTROLLI:
                if nome not in _SOLO_SUL_TESTO and nome not in motivi and rx.search(originale):
                    motivi.append(nome)
    motivi = list(dict.fromkeys(motivi))
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


# ---------------------------------------------------------------------------
# Punto unico di uscita verso Claude
# ---------------------------------------------------------------------------
def testo_in_uscita(testo):
    """Il testo come esce: normalizzato come una chiave (maiuscole, ogni gruppo di cifre -> #, punteggiatura
    -> spazio). Vale anche per il testo corretto a mano nell'anteprima."""
    from .chiavi import chiave as normalizza
    return normalizza(testo or "")


def uscita_claude(conn, voci):
    """Controllo unico di cio' che va verso Claude. voci: righe con chiave, testo (facoltativo: senza, vale la
    chiave) e paese. Restituisce (ammesse, bloccate): le ammesse hanno il testo esatto che parte, le bloccate
    {chiave, motivi}. Si controllano il testo normalizzato, i nomi originali dell'esercente e l'area solo locale."""
    ammesse, bloccate = [], []

    def scritto(x):
        return x.strip() if isinstance(x, str) else ""      # None e le celle vuote delle tabelle (NaN) valgono vuoto
    for v in voci:
        k = v["chiave"]
        testo = testo_in_uscita(scritto(v["testo"]) or k) or testo_in_uscita(k)
        motivi = controlla(conn, testo, chiave=k)
        paese = scritto(v["paese"]).upper()
        if paese and not re.fullmatch(r"[A-Z]{2,3}", paese):
            paese = ""                                  # il paese e' una sigla: qualunque altra cosa non esce
        if motivi:
            bloccate.append({"chiave": k, "motivi": motivi})
        else:
            ammesse.append({"chiave": k, "testo": testo, "paese": paese or None, "tipo": "pagamento"})
    return ammesse, bloccate


def categorie_in_uscita():
    """Le voci della tassonomia (uscite) che si possono scrivere in una richiesta a Claude: tutte, tranne quelle
    segnate "non inviare a Claude" in Regole e tassonomia."""
    from . import configurazione as cfg
    tax = cfg.tassonomia()
    return [c for c in tax.per_sezione("uscite") if c not in (tax.non_inviare or set())]
