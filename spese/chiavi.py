"""Esercenti: separazione degli intermediari e chiave normalizzata per cache e regole."""
import re

from . import configurazione as cfg


def separa_intermediario(esercente: str):
    """'PAYPAL *VEEPEE IT' -> ('VEEPEE IT', 'PAYPAL'). Le piattaforme (Google, Amazon...) restano l'esercente."""
    s = re.sub(r"\s+", " ", esercente or "").strip()
    m = re.match(r"^\s*([^*]{1,30}?)\s*\*+\s*(.+)$", s)
    if m:
        pref = m.group(1).strip().upper()
        if pref in cfg.intermediari()["prefissi"]:
            return m.group(2).strip(), pref
    return s, None


def togli_nomi(testo: str, nomi) -> str:
    """Toglie i nomi dei titolari in testa al testo (Credem: 'MARIO   CRF EXP ...')."""
    if not nomi:
        return testo
    rx = re.compile(r"^(?:%s)\b[\s,]*" % "|".join(re.escape(n) for n in nomi if n), re.I)
    prima = None
    while prima != testo:
        prima, testo = testo, rx.sub("", testo, count=1)
    return testo.strip()


def chiave(esercente: str) -> str:
    """Chiave stabile: maiuscole, cifre -> #, punteggiatura -> spazio. 'CRF EXP GAR4857 MILANO' -> 'CRF EXP GAR# MILANO'."""
    if not esercente:
        return ""
    s = esercente.upper()
    s = re.sub(r"NULL$", "", s)                  # artefatto Trade Republic
    s = re.sub(r"\d+", "#", s)
    s = re.sub(r"[^\w&'#*]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parole(s: str) -> set:
    return {w for w in re.findall(r"[A-Z0-9À-Ü]+", (s or "").upper()) if len(w) > 1}


def jaccard(a: str, b: str) -> float:
    pa, pb = parole(a), parole(b)
    if not pa or not pb:
        return 0.0
    return len(pa & pb) / len(pa | pb)


def contenimento(output: str, input_: str) -> float:
    """Quota delle parole dell'output presenti nell'input (controllo anti-invenzione)."""
    po = parole(output)
    if not po:
        return 0.0
    pi = parole(input_.replace("#", " "))
    return len(po & pi) / len(po)
