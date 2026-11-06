"""Caricamento delle configurazioni (cartella config/)."""
import csv
import json
import re
from dataclasses import dataclass
from functools import lru_cache

import yaml

from .percorsi import CONFIG, PROFILI


def _yaml(nome):
    return yaml.safe_load((CONFIG / nome).read_text(encoding="utf-8"))


SEP = " · "            # tra categoria e sottocategoria quando si mostrano insieme: "ACQUISTI · Varie"


def etichetta(cat):
    """(l1, l2) -> 'ACQUISTI · Varie'; None -> ''."""
    if not cat or not cat[1]:
        return ""
    return "%s%s%s" % (cat[0] or "?", SEP, cat[1])


def chiave_cat(cat):
    """(l1, l2) -> 'ACQUISTI|Varie': chiave testuale per le impostazioni salvate in JSON."""
    return "%s|%s" % tuple(cat) if cat and cat[1] else None


def da_chiave_cat(k):
    if not k:
        return None
    if "|" in k:
        l1, l2 = k.split("|", 1)
        return (l1, l2)
    return (None, k)


@dataclass
class Tassonomia:
    """La categoria e' sempre la coppia (categoria, sottocategoria): la stessa sottocategoria puo' stare sotto
    categorie diverse (CASA · Varie e ACQUISTI · Varie)."""
    voci: list                       # [(l1, l2)] nell'ordine del file
    sezione_di: dict                 # (l1, l2) -> uscite | entrate | risparmio
    ordine_l1: list
    solo_locale: set
    l1_per_l2: dict                  # l2 -> [l1, ...]
    partenza: dict = None            # l2 -> l1 nella tassonomia di partenza (per le regole dei file di configurazione)

    def valida(self, l1, l2=None):
        if l2 is None and isinstance(l1, (tuple, list)):
            l1, l2 = l1
        return (l1, l2) in self.sezione_di

    def sezione(self, cat):
        return self.sezione_di.get(tuple(cat)) if cat else None

    def per_sezione(self, sezione):
        return [c for c in self.voci if self.sezione_di[c] == sezione]

    def risolvi(self, l2, l1=None):
        """La coppia per una sottocategoria indicata con o senza categoria (regole di configurazione, dati di
        versioni precedenti). Con piu' categorie possibili vale quella della tassonomia di partenza; se non
        basta a decidere, None."""
        if not l2:
            return None
        if l1 and (l1, l2) in self.sezione_di:
            return (l1, l2)
        possibili = self.l1_per_l2.get(l2, [])
        if len(possibili) == 1:
            return (possibili[0], l2)
        base = (self.partenza or {}).get(l2)
        if base in possibili:
            return (base, l2)
        return None


def _costruisci(t):
    voci, sez, ordine, per_l2 = [], {}, [], {}
    for sezione in ("uscite", "entrate", "risparmio"):
        for l1, elenco in (t.get(sezione) or {}).items():
            if l1 not in ordine:
                ordine.append(l1)
            for v in elenco or []:
                if (l1, v) in sez:
                    continue
                voci.append((l1, v))
                sez[(l1, v)] = sezione
                per_l2.setdefault(v, []).append(l1)
    return voci, sez, ordine, per_l2


def come_cat(x, obbligatoria=False):
    """Categoria come coppia (l1, l2). Accetta la coppia o, per comodita', la sola sottocategoria quando nella
    tassonomia in uso indica una sola voce. None/'' -> None; non riconosciuta -> ValueError se obbligatoria."""
    if x is None or x == "" or (isinstance(x, (tuple, list)) and not x[1]):
        if obbligatoria:
            raise ValueError("categoria mancante")
        return None
    tax = tassonomia()
    if isinstance(x, (tuple, list)):
        cat = (x[0], x[1]) if x[0] else tax.risolvi(x[1])      # coppia indicata: deve esistere cosi' com'e'
    else:
        cat = tax.risolvi(x)
    if cat is None or not tax.valida(cat):
        raise ValueError("categoria non valida: %s" % (etichetta(x) if isinstance(x, (tuple, list)) else x))
    return cat


_TASSONOMIA_UTENTE = None      # tassonomia modificata dall'utente (dal database), se presente


def imposta_tassonomia(t):
    """Rende attiva una tassonomia nella forma del file YAML, o quella del file con None."""
    global _TASSONOMIA_UTENTE
    _TASSONOMIA_UTENTE = t
    tassonomia.cache_clear()


@lru_cache(maxsize=1)
def _partenza():
    voci, _, _, _ = _costruisci(_yaml("tassonomia.yaml"))
    out = {}
    for l1, l2 in voci:
        out.setdefault(l2, l1)
    return out


@lru_cache(maxsize=1)
def tassonomia() -> Tassonomia:
    t = _TASSONOMIA_UTENTE or _yaml("tassonomia.yaml")
    voci, sez, ordine, per_l2 = _costruisci(t)
    return Tassonomia(voci, sez, ordine, set(t.get("solo_locale", [])), per_l2, _partenza())


@lru_cache(maxsize=1)
def attributi() -> dict:
    """Natura della spesa: {'valori': {codice: etichetta}, 'predefiniti': {l2: codice}} dal file di partenza."""
    d = _yaml("attributi.yaml")
    pred = {}
    for codice, voci in (d.get("predefiniti") or {}).items():
        for l2 in voci or []:
            pred[l2] = codice
    return {"valori": dict(d["valori"]), "predefiniti": pred}


@lru_cache(maxsize=1)
def tipi_movimento() -> dict:
    d = _yaml("tipi_movimento.yaml")
    for nome, t in d["tipi"].items():
        for v in t.get("varianti", []) or []:
            v["_re"] = re.compile(v["se_descrizione"])
    return d


@lru_cache(maxsize=1)
def impostazioni() -> dict:
    return _yaml("impostazioni.yaml")


@dataclass
class Regola:
    rx: re.Pattern
    l2: str
    forza: str


@lru_cache(maxsize=1)
def regole_esercenti() -> list:
    return [Regola(re.compile(r["pattern"], re.I), r["l2"], r.get("forza", "alta"))
            for r in _yaml("regole_esercenti_it.yaml")["regole"]]


@lru_cache(maxsize=1)
def mcc() -> dict:
    with open(CONFIG / "mcc_categorie.csv", encoding="utf-8") as fh:
        return {r["mcc"]: r for r in csv.DictReader(fh)}


@lru_cache(maxsize=1)
def marchi() -> list:
    d = json.loads((CONFIG / "marchi_nsi.json").read_text(encoding="utf-8"))
    return [(re.compile(m["pattern"], re.I), m) for m in d["marchi"]]


@lru_cache(maxsize=1)
def intermediari() -> dict:
    d = _yaml("intermediari.yaml")
    return {"prefissi": [i["prefisso"].upper() for i in d["intermediari"] if i.get("attivo", True)],
            "piattaforme": [p.upper() for p in d.get("piattaforme", [])]}


@lru_cache(maxsize=None)
def profili() -> dict:
    out = {}
    for p in sorted(PROFILI.glob("*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        for e in d.get("estrazione", {}).values():
            pass
        out[d["id"]] = d
    return out


# natura di un movimento secondo la sezione della sua categoria (le uscite positive diventano rimborsi nel ricalcolo)
NATURA_DI_SEZIONE = {"uscite": "uscita", "entrate": "entrata", "risparmio": "risparmio"}
NATURE_CHE_CONTANO = ("uscita", "entrata", "rimborso", "risparmio")


def natura_da_categoria(cat, natura=None):
    """Con una categoria la natura viene dalla sua sezione (uscite, entrate, risparmio): niente combinazioni
    incoerenti come un'uscita classificata Stipendio. Le nature che non contano (trasferimento interno, escluso,
    contenitore) restano come sono."""
    if not cat or natura not in NATURE_CHE_CONTANO + (None,):
        return natura
    sez = tassonomia().sezione(cat)
    return NATURA_DI_SEZIONE.get(sez, natura)
