#!/usr/bin/env python3
"""
prova_catena.py - Misura quanto classifica la parte deterministica della catena
(regole esercenti -> MCC -> marchi NSI) sui pagamenti con carta dei tuoi file.
Gira offline e stampa solo statistiche; gli esercenti compaiono solo se ricorrono
almeno k volte e mai quelli dell'area riservata (salute).

Uso:  python3 strumenti/prova_catena.py FILE [FILE ...] --nomi "Mario,Rossi" [--k 3]
Formati riconosciuti: Trade Republic (CSV), Credem (CSV), ING Conto Arancio (Excel),
ING carta di credito (CSV con tabulazioni).
"""
import argparse
import collections
import csv
import io
import json
import re
from pathlib import Path

import yaml

RADICE = Path(__file__).resolve().parent.parent
CONFIG = RADICE / "config"


class Catena:
    def __init__(self):
        t = yaml.safe_load((CONFIG / "tassonomia.yaml").read_text(encoding="utf-8"))
        self.l1 = {v: l1 for s in ("uscite", "entrate", "risparmio") for l1, vs in t[s].items() for v in vs}
        self.solo_locale = set(t.get("solo_locale", []))
        self.regole = [(re.compile(r["pattern"], re.I), r["l2"], r.get("forza", "alta"))
                       for r in yaml.safe_load((CONFIG / "regole_esercenti_it.yaml").read_text(encoding="utf-8"))["regole"]]
        inter = yaml.safe_load((CONFIG / "intermediari.yaml").read_text(encoding="utf-8"))
        self.intermediari = [i["prefisso"] for i in inter["intermediari"] if i.get("attivo", True)]
        with open(CONFIG / "mcc_categorie.csv", encoding="utf-8") as fh:
            self.mcc = {r["mcc"]: r for r in csv.DictReader(fh)}
        self.marchi = [(re.compile(m["pattern"], re.I), m)
                       for m in json.loads((CONFIG / "marchi_nsi.json").read_text(encoding="utf-8"))["marchi"]]
        self._cache = {}

    def pulisci(self, esercente):
        s = re.sub(r"\s+", " ", esercente or "").strip()
        m = re.match(r"^\s*([^*]{1,30}?)\s*\*\s*(.+)$", s)
        if m and m.group(1).strip().upper() in self.intermediari:
            return m.group(2).strip(), m.group(1).strip().upper()
        return s, None

    def regola(self, s, forza):
        migliore = None
        for rx, l2, f in self.regole:
            if f != forza:
                continue
            m = rx.search(s)
            if m and (migliore is None or len(m.group(0)) > migliore[0]):
                migliore = (len(m.group(0)), l2)
        return migliore[1] if migliore else None

    def marchio(self, s):
        migliore = None
        for rx, m in self.marchi:
            if migliore and m["priorita"] > migliore[0]:
                break
            x = rx.search(s)
            if x and len(x.group(0)) >= 4:
                chiave = (m["priorita"], -len(x.group(0)))
                if migliore is None or chiave < migliore[:2]:
                    migliore = (m["priorita"], -len(x.group(0)), m)
        return migliore[2] if migliore else None

    def classifica(self, esercente, mcc=None):
        s, inter = self.pulisci(esercente)
        chiave = (s.lower(), mcc)
        if chiave in self._cache:
            return self._cache[chiave]
        info = self.mcc.get(mcc) if mcc else None
        mcc_decisivo = info["l2"] if info and info["generico"] == "no" else None
        alta, bassa = self.regola(s, "alta"), self.regola(s, "bassa")
        m = self.marchio(s)
        if alta:                                   # marchio preciso: vince anche sull'MCC
            esito = ("regola", alta, mcc_decisivo, None)
        elif mcc_decisivo:
            esito = ("mcc", mcc_decisivo, None, None)
        elif bassa:                                # parola generica, senza MCC che la smentisca
            esito = ("regola", bassa, None, None)
        elif m and m["decisivo"]:
            esito = ("marchio", m["l2"], None, m["marchio"])
        elif m or (info and info["l2"]):           # proposta debole: da confermare
            esito = ("proposta", m["l2"] if m else info["l2"], None, m["marchio"] if m else None)
        else:
            esito = ("nessuna", None, None, None)
        self._cache[chiave] = esito
        return esito


def leggi(percorso, nomi):
    """Restituisce (formato, lista di (esercente, mcc))."""
    if percorso.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        ws = openpyxl.load_workbook(percorso, read_only=True, data_only=True).worksheets[0]
        out = []
        for r in ws.iter_rows(values_only=True):
            celle = [c for c in r if isinstance(c, str)]
            if any(c in ("Pagamento Carta",) for c in celle):
                for c in celle:
                    m = re.search(r"presso\s+(.+)$", c)
                    if m:
                        out.append((m.group(1).strip(), None))
        return "ING", out
    raw = open(percorso, "rb").read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            testo = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    prima = testo.splitlines()[0]
    if "DATA REGISTRAZIONE" in prima and "DESCRIZIONE OPERAZIONE" in prima:
        out = []
        for riga in csv.reader(io.StringIO(testo), delimiter="\t" if "\t" in prima else ";"):
            m = re.match(r"\s*Operazione presso\s+(.+?)\s*$", riga[2]) if len(riga) > 3 else None
            if m:
                out.append((m.group(1), None))
        return "ING carta di credito", out
    if "transaction_id" in prima and "mcc_code" in prima:
        rows = csv.DictReader(io.StringIO(testo))
        out = []
        for r in rows:
            if "CARD" not in (r.get("type") or ""):
                continue
            nome = r["name"] or ("" if r["description"].startswith("TR Card Transaction") else r["description"])
            out.append((nome, r["mcc_code"].zfill(4) if r["mcc_code"] else None))
        return "Trade Republic", out
    out = []
    rx = re.compile(r"-\d{2}/\d{2}/\d{2}-\d{2}:\d{2}\s+(.*?)\s+-[A-Z]{3}\s*$")
    re_nomi = re.compile(r"^(?:%s)\b\s*" % "|".join(re.escape(n) for n in nomi), re.I) if nomi else None
    for riga in csv.reader(io.StringIO(testo), delimiter=";"):
        if len(riga) > 9 and riga[8].strip() == "PDINT":
            m = rx.search(riga[9])
            if m:
                s = m.group(1)
                while re_nomi and re_nomi.match(s):
                    s = re_nomi.sub("", s, count=1)
                out.append((s.strip(), None))
    return "Credem", out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="+")
    ap.add_argument("--nomi", default="")
    ap.add_argument("--k", type=int, default=3)
    a = ap.parse_args()
    nomi = [p for n in a.nomi.split(",") for p in n.split() if p]
    cat = Catena()
    riservato = re.compile(r"farmac|humanitas|ospedal|clinic|medic|dentist|odontoiatr|ottic|optic|psicolog|sanitar|poliambulat|\basl\b|pharma", re.I)

    for percorso in a.file:
        formato, righe = leggi(percorso, nomi)
        passi = collections.Counter()
        conflitti, non_class, marchi = collections.Counter(), collections.Counter(), collections.Counter()
        categorie = collections.Counter()
        for esercente, mcc in righe:
            passo, l2, l2_mcc, marchio = cat.classifica(esercente, mcc)
            passi[passo] += 1
            if l2:
                categorie[cat.l1[l2]] += 1
            if l2_mcc and l2_mcc != l2:
                conflitti["%s (regola) vs %s (MCC)" % (l2, l2_mcc)] += 1
            if passo in ("nessuna", "proposta"):
                nome = cat.pulisci(esercente)[0]
                non_class["<RISERVATO>" if riservato.search(nome) else re.sub(r"\d", "#", nome)] += 1
            if marchio:
                marchi["%s%s -> %s" % (marchio, "" if passo == "marchio" else " (proposta)", l2)] += 1
        tot = len(righe) or 1
        print("\n=== %s: %d pagamenti con carta ===" % (formato, len(righe)))
        for p, etichetta in (("regola", "regole esercenti"), ("mcc", "codice MCC"), ("marchio", "marchi NSI (decisivi)"),
                             ("proposta", "proposta debole (MCC generico o marchio)"), ("nessuna", "non classificati")):
            print("  %-38s %5d  (%d%%)" % (etichetta, passi[p], round(100 * passi[p] / tot)))
        print("  -> classificati in modo deterministico: %d%%" %
              round(100 * (passi["regola"] + passi["mcc"] + passi["marchio"]) / tot))
        print("  Categorie L1:", ", ".join("%s %d" % kv for kv in categorie.most_common()))
        if marchi:
            print("  Marchi NSI riconosciuti (verifica falsi positivi):")
            for m, n in marchi.most_common(15):
                print("    %3d  %s" % (n, m))
        if conflitti:
            print("  Regola e MCC in disaccordo:")
            for c, n in conflitti.most_common(10):
                print("    %3d  %s" % (n, c))
        frequenti = [(e, n) for e, n in non_class.most_common() if n >= a.k]
        print("  Non classificati frequenti (>= %d), candidati per regole:" % a.k)
        for e, n in frequenti[:15]:
            print("    %3d  %s" % (n, e))
        print("  Non classificati rari: %d esercenti" % sum(1 for e, n in non_class.items() if n < a.k))


if __name__ == "__main__":
    main()
