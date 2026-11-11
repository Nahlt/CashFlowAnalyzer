"""Catena di categorizzazione.

Ordine: decisione sul movimento > regole utente > categoria del tipo di movimento > esercente confermato
dall'utente > regole esercenti forza alta > MCC decisivo > regole forza bassa > marchi NSI decisivi >
cache (modello locale / Claude) > storico delle decisioni > somiglianza > proposta debole.

Le conferme dell'utente stanno prima delle regole del programma e dei marchi (dalla 0.17.1): prima una regola
o un marchio decisivo prevaleva in silenzio su "vale per tutti i movimenti di questo esercente".

Regole del programma e marchi dipendono solo dal nome dell'esercente e da file che non cambiano mentre l'app
gira: le corrispondenze si memorizzano per nome (8.108 marchi da provare per ogni movimento rendevano lento
ogni ricalcolo). La categoria si risolve invece ogni volta sulla tassonomia in uso, che puo' cambiare.
"""
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache

from . import configurazione as cfg
from .chiavi import jaccard


@dataclass
class Esito:
    cat: tuple = None            # (categoria, sottocategoria)
    fonte: str = None
    confidenza: float = None
    da_rivedere: bool = False
    motivo: str = None
    proposta: str = None


def _p(cat, dettaglio):
    """Testo della proposta: 'ACQUISTI · Varie (claude, confidenza 0.62)'."""
    return "%s (%s)" % (cfg.etichetta(cat), dettaglio)


MEMORIA_NOMI = 50000          # nomi di esercenti diversi tenuti in memoria (ben oltre quelli di una famiglia)


@lru_cache(maxsize=MEMORIA_NOMI)
def _regole_che_corrispondono(testo, forza):
    """Le regole del programma di quella forza che corrispondono al nome, nell'ordine del file:
    [(lunghezza della corrispondenza, l2)]. Dipende solo dal nome: si calcola una volta per nome."""
    out = []
    for r in cfg.regole_esercenti():
        if r.forza != forza:
            continue
        m = r.rx.search(testo)
        if m:
            out.append((len(m.group(0)), r.l2))
    return tuple(out)


def _miglior_regola(testo, forza):
    """La corrispondenza piu' lunga la cui sottocategoria esiste nella tassonomia in uso (a parita', la prima)."""
    tax = cfg.tassonomia()
    migliore = None
    for lunghezza, l2 in _regole_che_corrispondono(testo, forza):
        if migliore is None or lunghezza > migliore[0]:
            cat = tax.risolvi(l2)
            if cat:
                migliore = (lunghezza, cat)
    return migliore[1] if migliore else None


@lru_cache(maxsize=MEMORIA_NOMI)
def _marchio(testo):
    """Il marchio NSI riconosciuto nel nome (priorita' piu' bassa, poi corrispondenza piu' lunga), o None.
    Dipende solo dal nome: si calcola una volta per nome. Il dizionario restituito non va modificato."""
    migliore = None
    for rx, m in cfg.marchi():
        if migliore and m["priorita"] > migliore[0]:
            break
        x = rx.search(testo)
        if x and len(x.group(0)) >= 4:
            k = (m["priorita"], -len(x.group(0)))
            if migliore is None or k < migliore[:2]:
                migliore = (m["priorita"], -len(x.group(0)), m)
    return migliore[2] if migliore else None


def deterministica(esercente, mcc_code):
    """(fonte, (l1, l2), proposta) dalla parte deterministica; categoria None se non decide."""
    tax = cfg.tassonomia()
    testo = esercente or ""
    info = cfg.mcc().get(mcc_code) if mcc_code else None
    info_cat = tax.risolvi(info["l2"], info.get("l1")) if info and info.get("l2") else None
    mcc_dec = info_cat if info and info["generico"] == "no" else None
    alta = _miglior_regola(testo, "alta") if testo else None
    if alta:
        return "regola", alta, None
    if mcc_dec:
        return "mcc", mcc_dec, None
    bassa = _miglior_regola(testo, "bassa") if testo else None
    if bassa:
        return "regola", bassa, None
    m = _marchio(testo) if testo else None
    m_cat = tax.risolvi(m["l2"], m.get("l1")) if m else None
    if m_cat and m["decisivo"]:
        return "marchio", m_cat, None
    if m_cat:
        return None, None, _p(m_cat, "marchio %s" % m["marchio"])
    if info_cat:
        return None, None, _p(info_cat, "codice MCC generico %s" % mcc_code)
    return None, None, None


def confidenza_storico(conteggi: Counter, minimi: int):
    n = sum(conteggi.values())
    if n == 0:
        return None, 0.0
    cat, top = conteggi.most_common(1)[0]
    k = len(conteggi)
    if k == 1:
        omog = 1.0
    else:
        h = -sum((c / n) * math.log(c / n) for c in conteggi.values())
        omog = 1 - h / math.log(k)
    return cat, omog * min(1.0, n / minimi)


class Catena:
    def __init__(self, conn):
        self.tax = cfg.tassonomia()
        imp = cfg.impostazioni()
        self.s_auto = imp["storico"]["soglia_automatica"]
        self.s_prop = imp["storico"]["soglia_proposta"]
        self.minimi = imp["storico"]["movimenti_minimi"]
        self.s_llm = imp["modello_locale"]["soglia_confidenza"]
        cat = lambda r: self.tax.risolvi(r["l2"], r["l1"]) if r["l2"] else None
        self.regole = []
        for r in conn.execute("SELECT * FROM regole_utente ORDER BY priorita DESC, id DESC"):
            p = r["pattern"]
            if r["modo"] == "regex":
                rx = re.compile(p, re.I)
            elif r["modo"] == "contiene":
                rx = re.compile(re.escape(p), re.I)
            else:
                rx = re.compile("^" + re.escape(p) + "$", re.I)
            self.regole.append((r["campo"], rx, cat(r), r["natura"]))
        self.decisioni = {r["mov_id"]: (cat(r), r["natura"]) for r in conn.execute("SELECT * FROM decisioni")}
        self.cache = {r["chiave"]: {**dict(r), "cat": cat(r)} for r in conn.execute("SELECT * FROM cache_esercenti")}
        # storico: decisioni manuali raggruppate per chiave esercente (o beneficiario)
        self.storico = defaultdict(Counter)
        for r in conn.execute("SELECT m.chiave, m.controparte_iban, m.controparte_nome, d.l1, d.l2 FROM decisioni d "
                              "JOIN movimenti m ON m.id = d.mov_id WHERE d.l2 IS NOT NULL "
                              "AND NOT (d.l2 = 'Contributo ricevuto' AND m.importo_cent < 0)"):
            c = cat(r)
            if not c:
                continue
            for k in (r["chiave"], chiave_beneficiario(r["controparte_iban"], r["controparte_nome"])):
                if k:
                    self.storico[k][c] += 1
        self.note = {}  # chiave -> categoria sicura, per la somiglianza
        for k, r in self.cache.items():
            if r["cat"] and (r["confidenza"] or 0) >= self.s_llm:
                self.note[k] = r["cat"]
        for k, c in self.storico.items():
            cat_s, conf = confidenza_storico(c, self.minimi)
            if conf >= self.s_auto:
                self.note[k] = cat_s

    def regola_utente(self, chiave, descrizione):
        for campo, rx, cat, natura in self.regole:
            testo = chiave if campo == "chiave" else descrizione
            if testo and rx.search(testo) and (cat or natura):
                return cat, natura
        return None, None

    def classifica(self, mov, chiave_x, catena=True, cat_tipo=None):
        """Categoria (l1, l2) della componente principale. chiave_x: chiave esercente o beneficiario."""
        if mov["id"] in self.decisioni and self.decisioni[mov["id"]][0]:
            return Esito(self.decisioni[mov["id"]][0], "utente", 1.0)
        cat, _ = self.regola_utente(chiave_x, mov["descrizione"])
        if cat:
            return Esito(cat, "regola utente", 1.0)
        if cat_tipo:
            return Esito(cat_tipo, "tipo movimento", 1.0)
        confermato = self.cache.get(chiave_x) if chiave_x else None
        if confermato and confermato["motore"] == "utente" and confermato["cat"]:
            # esercente confermato da te: prevale su regole del programma, codici MCC e marchi
            return Esito(confermato["cat"], "utente", confermato["confidenza"] or 1.0)
        if catena:
            fonte, cat, proposta = deterministica(mov["esercente"], mov["mcc"])
            if cat:
                return Esito(cat, fonte, 0.95)
        else:
            proposta = None
        if chiave_x and chiave_x in self.cache:
            r = self.cache[chiave_x]
            if r["cat"] and (r["confidenza"] or 0) >= self.s_llm:
                return Esito(r["cat"], r["motore"], r["confidenza"])
            if r["cat"]:
                proposta = proposta or _p(r["cat"], "%s, confidenza %.2f" % (r["motore"], r["confidenza"] or 0))
        if chiave_x and chiave_x in self.storico:
            cat, conf = confidenza_storico(self.storico[chiave_x], self.minimi)
            if conf >= self.s_auto:
                return Esito(cat, "storico", conf)
            if conf >= self.s_prop:
                return Esito(None, None, conf, True, "Categoria proposta dallo storico, da confermare",
                             _p(cat, "storico, confidenza %.2f" % conf))
        if chiave_x and catena:
            simili = sorted(((jaccard(chiave_x, k), c) for k, c in self.note.items()), reverse=True)
            if simili and simili[0][0] >= 0.6:
                return Esito(None, None, simili[0][0], True, "Esercente simile a uno gia' classificato",
                             _p(simili[0][1], "somiglianza %.2f" % simili[0][0]))
        return Esito(None, None, None, True, "Categoria da assegnare", proposta)


def chiave_beneficiario(iban, nome):
    if iban:
        return "IBAN:" + re.sub(r"\s+", "", iban.upper())
    if nome:
        from .chiavi import chiave
        # parole ordinate: "ROSSI MARIO" e "Mario Rossi" sono lo stesso beneficiario
        return "NOME:" + " ".join(sorted(chiave(nome).split()))
    return None


FONTI_PROPOSTA = {"modello_locale": "Modello locale", "claude": "Claude", "storico": "Storico",
                  "somiglianza": "Somiglianza", "utente": "Confermata da te"}


def scomponi_proposta(p):
    """'ACQUISTI · Varie (claude, confidenza 0.62)' -> {'cat': ('ACQUISTI', 'Varie'), 'l2': 'Varie',
    'fonte': 'Claude', 'motore': 'claude', 'confidenza': 0.62}. Vale per tutte le proposte della catena:
    modello, storico, somiglianza, marchio, codice MCC generico. Senza proposta: tutti i campi None."""
    out = {"cat": None, "l2": None, "fonte": None, "motore": None, "confidenza": None}
    p = (p or "").strip() if isinstance(p, str) else ""
    if not p:
        return out
    m = re.match(r"^(.*) \(([^()]*)\)$", p)          # l'ultima parentesi: "Assicurazioni (altre) (claude, ...)"
    voce, det = (m.group(1), m.group(2)) if m else (p, None)
    if cfg.SEP in voce:
        l1, l2 = voce.split(cfg.SEP, 1)
        out["cat"] = (l1, l2)
    else:                                              # proposte delle versioni precedenti: solo sottocategoria
        out["cat"] = cfg.tassonomia().risolvi(voce) or (None, voce)
    out["l2"] = out["cat"][1]
    if det is None:
        return out
    m2 = re.match(r"^([^,]+), confidenza ([\d.]+)$", det) or re.match(r"^(somiglianza) ([\d.]+)$", det)
    if m2:
        out["motore"] = m2.group(1).strip()
        out["fonte"] = FONTI_PROPOSTA.get(out["motore"], out["motore"])
        out["confidenza"] = float(m2.group(2))
    else:
        out["fonte"] = det[:1].upper() + det[1:]
    return out
