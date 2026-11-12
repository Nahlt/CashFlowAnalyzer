"""Diagnostica (modalita' di debug): controlli di coerenza sui dati veri e report anonimizzato.

I test automatici provano il programma su estratti inventati. Qui si controllano, sul database dell'utente, le
regole che devono valere sempre (un movimento conta una volta sola, i trasferimenti si annullano, ...), per
trovare errori di conteggio che compaiono solo con dati reali.

Il report e' un testo che l'utente legge e decide se inviare. Non parte nulla dalla rete.
- Riepilogo: conteggi, codici dei controlli, diciture della banca non riconosciute. Nessun importo, data o nome.
- Casi scelti dall'utente: i movimenti coinvolti con gli importi veri, le date spostate di un numero fisso di
  anni e, al posto di nomi, conti, IBAN e identificativi, etichette ("ESERCENTE 12"). Delle descrizioni restano
  solo le parole della banca; le categorie dell'area solo locale e quelle "non inviare" non sono nominate.
La corrispondenza tra etichette e valori veri (transcodifica) e' un file a parte, che resta sul PC.
"""
import csv
import io
import re
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache

from . import VERSIONE
from . import configurazione as cfg
from .db import leggi_impostazione, scrivi_impostazione
from .numeri import euro

CHIAVE_ATTIVA = "modalita_debug"
CHIAVE_MAPPA = "diagnostica_mappa"
CONTATE = ("entrata", "uscita", "rimborso")
MASSIMO_CASI = 30               # casi in un report: pochi e scelti
MASSIMO_MOVIMENTI = 40          # movimenti di contesto in un caso (es. righe della carta nella finestra)


def attiva(conn):
    """Modalita' di debug accesa (Impostazioni): compare la pagina Diagnostica."""
    return bool(leggi_impostazione(conn, CHIAVE_ATTIVA, False))


# codice -> (gravita', titolo, spiegazione). errore: una regola del programma non e' rispettata (difetto);
# avviso: probabile errore nei conti, da guardare; info: situazione nota, utile per capire i numeri
CONTROLLI = {
    "COMPONENTI": ("errore", "Movimento contato in modo diverso dal suo importo",
                   "Le righe di analisi di un movimento (principale o parti, commissione, imposta) non tornano con "
                   "importo, commissione e imposta del movimento, oppure ci sono righe di un movimento che non esiste."),
    "TRASF_ASIMMETRICO": ("errore", "Trasferimento abbinato in modo non simmetrico",
                          "Un movimento e' abbinato a una contropartita che non lo ricambia, o i due importi non "
                          "sono opposti."),
    "TRASF_CONTATO": ("errore", "Trasferimento abbinato che conta da un lato solo",
                      "Dei due movimenti abbinati uno conta come entrata o uscita e l'altro no: con i due conti "
                      "insieme non si annullano."),
    "NATURA_CATEGORIA": ("errore", "Natura e categoria in disaccordo",
                         "Una riga conta come uscita, entrata o risparmio ma la sua categoria e' di un'altra "
                         "sezione, o non esiste nella tassonomia in uso."),
    "TOTALI_PAGINE": ("errore", "Totali diversi tra Andamento mensile e Dove vanno i soldi",
                      "In un mese le uscite dell'andamento non coincidono con la somma delle categorie."),
    "RICALCOLO_INSTABILE": ("errore", "Due ricalcoli di fila danno risultati diversi",
                            "Rifacendo il ricalcolo senza cambiare nulla alcune righe di analisi cambiano."),
    "CARTA_NON_ABBINATA": ("avviso", "Addebito della carta contato per intero con le sue righe gia' importate",
                           "L'addebito mensile conta come spesa senza dettaglio, ma nella sua finestra ci sono "
                           "righe della carta collegata: le stesse spese possono contare due volte."),
    "DOPPIONE_TRA_CONTI": ("avviso", "Stessa spesa su due conti",
                           "Due uscite dello stesso importo, a pochi giorni di distanza e su conti diversi, contano "
                           "entrambe: puo' essere la stessa spesa vista da due estratti (carta, PayPal)."),
    "DOPPIONE_NEL_CONTO": ("avviso", "Stesso movimento importato da due file",
                           "Nello stesso conto ci sono movimenti identici (data, importo, descrizione) arrivati da "
                           "file diversi: la deduplica potrebbe non averli riconosciuti."),
    "STORNO_COME_ENTRATA": ("avviso", "Accredito che pareggia un addebito, contato come entrata",
                            "Un accredito ha lo stesso importo di un addebito precedente dello stesso soggetto, o la "
                            "banca lo chiama storno o rimborso, ma conta come entrata: entrate e uscite risultano "
                            "gonfiate dello stesso importo."),
    "SUDDIVISIONE_IGNORATA": ("avviso", "Suddivisione che non somma al movimento",
                              "Le parti di una suddivisione non sommano all'importo del movimento (o il movimento "
                              "non c'e' piu'): la suddivisione viene ignorata in silenzio."),
    "QUADRATURA": ("avviso", "File importato che non quadra con i saldi",
                   "Saldo iniziale piu' movimenti diverso dal saldo finale dichiarato dal file."),
    "CARTA_DIFFERENZA": ("info", "Addebito della carta spiegato solo in parte",
                         "Le righe della carta spiegano meno dell'addebito: la differenza conta come spesa Varie."),
    "CARTA_ALTROVE": ("info", "Addebito della carta con le righe nel conto sbagliato",
                      "Le righe della carta sono in un conto non di tipo Carta, o in una carta senza conto di addebito."),
    "TIPO_NON_RICONOSCIUTO": ("info", "Dicitura della banca non riconosciuta",
                              "Movimenti che il formato della banca non sa classificare (tipo \"altro\"), raggruppati "
                              "per dicitura."),
}
ORDINE_GRAVITA = {"errore": 0, "avviso": 1, "info": 2}


@dataclass
class Anomalia:
    codice: str
    movimenti: tuple = ()                           # id dei movimenti coinvolti, il primo e' quello principale
    dati: list = field(default_factory=list)        # [(nome, tipo, valore)], tipo: euro | data | mese | conto | n | testo

    @property
    def gravita(self):
        return CONTROLLI[self.codice][0]

    @property
    def titolo(self):
        return CONTROLLI[self.codice][1]

    @property
    def id(self):
        return "%s:%s:%s" % (self.codice, "+".join(self.movimenti[:3]), ";".join(str(d[2]) for d in self.dati[:2]))


# ---------------------------------------------------------------------------
# Lettura
# ---------------------------------------------------------------------------
class _Dati:
    def __init__(self, conn):
        self.conn = conn
        self.conti = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM conti")}
        self.movs = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM movimenti")}
        self.analisi = defaultdict(list)
        for r in conn.execute("SELECT * FROM analisi ORDER BY id"):
            self.analisi[r["mov_id"]].append(dict(r))
        self.trasferimenti = {r["mov_id"]: dict(r) for r in conn.execute("SELECT * FROM trasferimenti")}
        self.contenitori = {r["mov_id"]: dict(r) for r in conn.execute("SELECT * FROM contenitori")}

    def principali(self, mid):
        return [a for a in self.analisi.get(mid, []) if a["componente"] in ("principale", "parte")]

    def contato(self, mid):
        return sum(a["importo_cent"] for a in self.principali(mid) if a["natura"] in CONTATE)


def _d(iso):
    return date.fromisoformat(iso[:10])


def _soggetto(m):
    return (m.get("chiave") or "").strip() or (m.get("controparte_nome") or "").strip().upper()


# ---------------------------------------------------------------------------
# Controlli
# ---------------------------------------------------------------------------
def _componenti(d):
    out = []
    for mid, m in d.movs.items():
        righe = d.analisi.get(mid, [])
        per = Counter()
        for a in righe:
            per["principale" if a["componente"] in ("principale", "parte") else a["componente"]] += a["importo_cent"] or 0
        attesi = {"principale": m["importo_cent"] or 0, "commissione": m["fee_cent"] or 0, "imposta": m["tax_cent"] or 0}
        diff = [(k, per.get(k, 0) - v) for k, v in attesi.items() if per.get(k, 0) != v]
        doppia = sum(1 for a in righe if a["componente"] == "principale") > 1
        if diff or doppia:
            dati = [("differenza %s (analisi - movimento)" % k, "euro", v) for k, v in diff]
            if doppia:
                dati.append(("righe principali", "n", sum(1 for a in righe if a["componente"] == "principale")))
            out.append(Anomalia("COMPONENTI", (mid,), dati))
    orfane = sorted(set(d.analisi) - set(d.movs))
    if orfane:
        out.append(Anomalia("COMPONENTI", (), [("righe di analisi senza movimento", "n",
                                                sum(len(d.analisi[k]) for k in orfane))]))
    return out


def _trasferimenti(d):
    out, visti = [], set()
    for mid, t in d.trasferimenti.items():
        if t["stato"] != "abbinato" or not t["controparte_mov_id"] or mid in visti:
            continue
        altro = t["controparte_mov_id"]
        visti.update((mid, altro))
        ta = d.trasferimenti.get(altro)
        a, b = d.movs.get(mid), d.movs.get(altro)
        if not a or not b or not ta or ta["stato"] != "abbinato" or ta["controparte_mov_id"] != mid:
            out.append(Anomalia("TRASF_ASIMMETRICO", tuple(x for x in (mid, altro) if x in d.movs),
                                [("contropartita che ricambia", "testo", "no")]))
            continue
        somma = (a["importo_cent"] or 0) + (b["importo_cent"] or 0)
        if somma:
            out.append(Anomalia("TRASF_ASIMMETRICO", (mid, altro), [("somma dei due importi", "euro", somma)]))
        netto = d.contato(mid) + d.contato(altro)
        if netto:
            out.append(Anomalia("TRASF_CONTATO", (mid, altro), [("contato in tutto", "euro", netto)]))
    return out


def _natura_categoria(d):
    tax = cfg.tassonomia()
    attesa = {"uscita": "uscite", "rimborso": "uscite", "entrata": "entrate", "risparmio": "risparmio"}
    out = []
    for mid, righe in d.analisi.items():
        for a in righe:
            if not a["l2"] or mid not in d.movs:
                continue
            sez = tax.sezione((a["l1"], a["l2"]))
            if sez is None:
                out.append(Anomalia("NATURA_CATEGORIA", (mid,), [("categoria", "testo", "non esiste nella tassonomia"),
                                                                  ("natura", "testo", a["natura"])]))
            elif a["natura"] in attesa and sez != attesa[a["natura"]]:
                out.append(Anomalia("NATURA_CATEGORIA", (mid,), [("natura", "testo", a["natura"]),
                                                                  ("sezione della categoria", "testo", sez)]))
    return out


def _totali_pagine(d):
    from . import analisi as an
    f = an.Filtri("tutti")
    flussi = an.flussi_mensili(d.conn, f)
    out = []
    for mese, uscite in ([] if flussi.empty else zip(flussi["mese"], flussi["uscite"])):
        rip = an.ripartizione(d.conn, an.con_mese(f, mese), "l1")
        somma = 0 if rip.empty else int(rip["importo"].sum())
        if somma != int(uscite):
            out.append(Anomalia("TOTALI_PAGINE", (), [("mese", "mese", mese),
                                                      ("categorie - andamento", "euro", somma - int(uscite))]))
    return out


def _ricalcolo_stabile(conn):
    """Due ricalcoli di fila devono dare la stessa tabella di analisi. Lascia il database ricalcolato."""
    from .ricalcolo import ricalcola

    def foto():
        return {(r["mov_id"], r["componente"], i): tuple(r)[1:] for i, r in enumerate(
            conn.execute("SELECT * FROM analisi ORDER BY mov_id, componente, importo_cent, l1, l2"))}
    ricalcola(conn)
    a = foto()
    ricalcola(conn)
    b = foto()
    diversi = sorted({k[0] for k in set(a) | set(b) if a.get(k) != b.get(k)} - {None})
    if not diversi and a == b:
        return []
    return [Anomalia("RICALCOLO_INSTABILE", tuple(diversi[:MASSIMO_MOVIMENTI]), [("movimenti che cambiano", "n", len(diversi))])]


def _carta(d):
    from .ricalcolo import FINESTRA_CARTA, carte_per_conto
    carte, _ = carte_per_conto(d.conti, list(d.movs.values()))
    righe = defaultdict(list)
    for m in d.movs.values():
        if d.conti[m["conto_id"]]["tipo"] == "carta":
            righe[m["conto_id"]].append(m)
    out = []
    for mid, c in d.contenitori.items():
        m = d.movs.get(mid)
        if not m:
            continue
        ciclo = (c["ciclo"] or m["ciclo_carta"] or m["data"])[:10]
        dal = (_d(ciclo) - timedelta(days=FINESTRA_CARTA)).isoformat()
        vicine = sorted((r for cid in carte.get(m["conto_id"], []) for r in righe[cid] if dal < r["data"][:10] <= ciclo),
                        key=lambda r: (r["data"], r["id"]))
        ids = (mid,) + tuple(r["id"] for r in vicine[:MASSIMO_MOVIMENTI])
        dati = [("addebito", "euro", m["importo_cent"]), ("righe della carta nella finestra", "n", len(vicine)),
                ("somma delle righe nella finestra", "euro", sum(r["importo_cent"] or 0 for r in vicine)),
                ("righe abbinate", "n", c["righe"] or 0), ("differenza", "euro", c["differenza_cent"] or 0)]
        if c["stato"] == "senza_dettaglio" and vicine:
            out.append(Anomalia("CARTA_NON_ABBINATA", ids, dati))
        elif c["stato"] == "differenza":
            out.append(Anomalia("CARTA_DIFFERENZA", ids, dati))
        elif c["stato"] == "righe_altrove":
            out.append(Anomalia("CARTA_ALTROVE", ids, dati))
    return out


GIORNI_DOPPIONE = 5
MINIMO_DOPPIONE = 500           # centesimi: sotto, le coincidenze sono troppe


def _doppioni_tra_conti(d):
    """Due uscite che contano, stesso importo, conti diversi, entro pochi giorni; e in piu' stesso esercente oppure
    importo non tondo (due spese da 10,00 EUR in due conti sono normali)."""
    per_importo = defaultdict(list)
    for mid, righe in d.analisi.items():
        m = d.movs.get(mid)
        for a in righe:
            if m and a["componente"] == "principale" and a["natura"] == "uscita" and (a["importo_cent"] or 0) <= -MINIMO_DOPPIONE:
                per_importo[a["importo_cent"]].append((a["data"][:10], mid, a["conto_id"], m))
    out, usati = [], set()
    for importo, voci in per_importo.items():
        voci.sort(key=lambda v: (v[0], v[1]))
        for i, (da, ida, ca, ma) in enumerate(voci):
            for db_, idb, cb, mb in voci[i + 1:]:
                if ida in usati or idb in usati or (_d(db_) - _d(da)).days > GIORNI_DOPPIONE:
                    continue
                if ca == cb or ma["conto_id"] == mb["conto_id"]:
                    continue
                stesso = bool(_soggetto(ma)) and _soggetto(ma) == _soggetto(mb)
                if stesso or importo % 100:
                    usati.update((ida, idb))
                    out.append(Anomalia("DOPPIONE_TRA_CONTI", (ida, idb),
                                        [("importo", "euro", importo), ("giorni di distanza", "n", (_d(db_) - _d(da)).days),
                                         ("stesso esercente", "testo", "si'" if stesso else "no")]))
    return out


def _doppioni_nel_conto(d):
    gruppi = defaultdict(list)
    for mid, m in d.movs.items():
        if (m["descrizione"] or "").strip() and m["importo_cent"]:
            gruppi[(m["conto_id"], m["data"], m["importo_cent"], m["tipo"], m["descrizione"].strip())].append(m)
    out = []
    for g in gruppi.values():
        if len(g) > 1 and len({m["import_id"] for m in g}) > 1:
            out.append(Anomalia("DOPPIONE_NEL_CONTO", tuple(sorted(m["id"] for m in g)),
                                [("copie", "n", len(g)), ("importo", "euro", g[0]["importo_cent"])]))
    return out


GIORNI_STORNO = 90
PAROLE_STORNO = re.compile(r"\b(storno|stornato|rimborso|riaccredito|restituzione|reversal|refund|chargeback)\b", re.I)


def _storni(d):
    """Entrate che sono con ogni probabilita' la restituzione di un'uscita: stesso soggetto, stesso conto e importo
    opposto nei 90 giorni prima, oppure la descrizione della banca parla di storno o rimborso."""
    uscite = defaultdict(list)
    for mid, m in d.movs.items():
        if (m["importo_cent"] or 0) < 0 and _soggetto(m) and any(a["natura"] == "uscita" for a in d.principali(mid)):
            uscite[(m["conto_id"], _soggetto(m), -m["importo_cent"])].append(m)
    out = []
    for mid, m in d.movs.items():
        if (m["importo_cent"] or 0) <= 0 or not any(a["natura"] == "entrata" and a["componente"] == "principale"
                                                    for a in d.analisi.get(mid, [])):
            continue
        prima = [u for u in uscite.get((m["conto_id"], _soggetto(m), m["importo_cent"]), [])
                 if 0 <= (_d(m["data"]) - _d(u["data"])).days <= GIORNI_STORNO] if _soggetto(m) else []
        dicitura = bool(PAROLE_STORNO.search(m["descrizione"] or ""))
        if prima or dicitura:
            u = max(prima, key=lambda x: x["data"]) if prima else None
            out.append(Anomalia("STORNO_COME_ENTRATA", (mid,) + ((u["id"],) if u else ()),
                                [("accredito", "euro", m["importo_cent"]),
                                 ("addebito uguale nei %d giorni prima" % GIORNI_STORNO, "testo", "si'" if u else "no"),
                                 ("la banca lo chiama storno o rimborso", "testo", "si'" if dicitura else "no")]))
    return out


def _suddivisioni(d):
    out = []
    for r in d.conn.execute("SELECT mov_id, sum(importo_cent) AS somma, count(*) AS n FROM ripartizioni GROUP BY mov_id"):
        m = d.movs.get(r["mov_id"])
        if not m:
            out.append(Anomalia("SUDDIVISIONE_IGNORATA", (), [("parti di un movimento che non c'e' piu'", "n", r["n"])]))
        elif r["somma"] != m["importo_cent"]:
            out.append(Anomalia("SUDDIVISIONE_IGNORATA", (r["mov_id"],),
                                [("parti", "n", r["n"]), ("somma delle parti - movimento", "euro", r["somma"] - (m["importo_cent"] or 0))]))
    return out


def _quadrature(d):
    out = []
    for r in d.conn.execute("SELECT id, conto_id, profilo, righe_dati, quadratura FROM import_file WHERE quadratura LIKE 'NON quadra%'"):
        diff = re.search(r"([+-]?\d+(?:\.\d+)?)\s*EUR", r["quadratura"] or "")
        dati = [("conto", "conto", r["conto_id"]), ("formato", "testo", r["profilo"] or "-"), ("righe del file", "n", r["righe_dati"] or 0)]
        if diff:
            dati.append(("differenza", "euro", round(float(diff.group(1)) * 100)))
        out.append(Anomalia("QUADRATURA", (), dati))
    return out


def _tipi_non_riconosciuti(d, anonimo):
    gruppi = defaultdict(list)
    for mid, m in d.movs.items():
        if m["tipo"] == "altro":
            profilo = d.conti[m["conto_id"]].get("profilo") or "-"
            gruppi[(profilo, m["codice"] or "-", anonimo.scheletro(m["descrizione"], m, generico=True))].append(m)
    out = []
    for (profilo, codice, dicitura), g in sorted(gruppi.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        g.sort(key=lambda m: (m["data"], m["id"]), reverse=True)
        out.append(Anomalia("TIPO_NON_RICONOSCIUTO", tuple(m["id"] for m in g[:5]),
                            [("formato", "testo", profilo), ("codice della banca", "testo", codice),
                             ("dicitura", "testo", dicitura), ("movimenti", "n", len(g)),
                             ("accrediti", "n", sum(1 for m in g if (m["importo_cent"] or 0) > 0))]))
    return out


def esegui(conn, ricalcolo=True):
    """Tutti i controlli. Con ricalcolo=True prima rifa' due volte il ricalcolo (controllo di stabilita'): i
    controlli valgono allora sui dati appena ricalcolati. Restituisce le anomalie, le piu' gravi prima."""
    out = _ricalcolo_stabile(conn) if ricalcolo else []
    d = _Dati(conn)
    anonimo = Anonimo(conn)
    for controllo in (_componenti, _trasferimenti, _natura_categoria, _totali_pagine, _carta, _doppioni_tra_conti,
                      _doppioni_nel_conto, _storni, _suddivisioni, _quadrature):
        out.extend(controllo(d))
    out.extend(_tipi_non_riconosciuti(d, anonimo))
    out.sort(key=lambda a: (ORDINE_GRAVITA[a.gravita], list(CONTROLLI).index(a.codice)))
    return out


# ---------------------------------------------------------------------------
# Anonimizzazione
# ---------------------------------------------------------------------------
NOMI_ETICHETTE = {"M": "M", "CONTO": "CONTO", "ESERCENTE": "ESERCENTE", "SOGGETTO": "SOGGETTO", "IBAN": "IBAN"}
ANNI_POSSIBILI = (4, 8, 12, 16, 20)     # multipli di 4: il 29 febbraio resta una data valida
# parole che una banca scrive in ogni descrizione: restano leggibili nel report
PAROLE_BANCA = set("""
A AD AL ALLA ALLE AI DA DAL DALLA DEL DELLA DELLE DEI DI E ED IL IN LA LE LO PER SU SUL SULLA TRA UN UNA CON NON
ABI ACCREDITO ADDEBITO ADDEBITI AMMINISTRATO ANNULLAMENTO ANNUO ASSEGNO ATM AUTOMATICO AUTORIZZAZIONE BANCA BANCARIO
BANCOMAT BENEFICIARIO BOLLETTINO BOLLO BONIFICO BONIFICI CANONE CAB CARTA CARTE CASSA CAUSALE CEDOLA CEDOLE CIRCUITO
CODICE COMMISSIONE COMMISSIONI COMPETENZE CONTABILE CONTANTE CONTANTI CONTO CORRENTE CREDITO CREDITOR CRO DATA DEBITO
DELEGA DEPOSITO DIRETTO DISPOSIZIONE DISPOSTO DIVIDENDO DOSSIER EFFETTI EMOLUMENTI ESTERO EURO EUR FAVORE FILIALE
GENERICO GIROCONTO GIROFONDI ID IMPORTO IMPOSTA IMPOSTE INCASSO INTERESSI ISTANTANEO MANDATO MAV MENSILE MOVIMENTO
NUMERO ONLINE OPERAZIONE ORDINANTE ORDINE PAGAMENTO PAGAMENTI PAGOBANCOMAT PAGOPA PENSIONE POS PREAUTORIZZATO PRELIEVO
PRESSO RATA RETTIFICA RIACCREDITO RICARICA RICEVUTO RID RIF RIFERIMENTO RIMBORSO RITENUTA SALDO SCT SDD SEPA SPESE
SPORTELLO STIPENDIO STORNO STORNATO TASSO TITOLI TRASFERIMENTO TRIMESTRALE UTENZA UTENZE VALUTA VERSAMENTO VERSO VS
ACQUISTO VENDITA SOTTOSCRIZIONE RIMBORSI TRN REF PAYMENT CARD TRANSFER DIRECT DEBIT REFUND REVERSAL FEE INTEREST
""".split())
_PAROLA = re.compile(r"[^\W\d_]+|\d+|[^\w\s]+|_+", re.UNICODE)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){11,30}\b")
NASCOSTO = "···"


@lru_cache(maxsize=1)
def _vocabolario():
    """Parole della banca: quelle dell'elenco e quelle scritte nei profili dei formati e nei tipi di movimento."""
    parole = set(PAROLE_BANCA)
    for f in list(cfg.CONFIG.glob("profili/*.yaml")) + [cfg.CONFIG / "tipi_movimento.yaml"]:
        try:
            parole.update(w for w in re.findall(r"[A-Z]{3,}", f.read_text(encoding="utf-8")))
        except OSError:
            pass
    return frozenset(parole)


class Anonimo:
    """Etichette stabili al posto dei valori veri. La corrispondenza resta nel database (cifrato), cosi' lo stesso
    esercente ha la stessa etichetta in tutti i report."""

    def __init__(self, conn):
        self.conn = conn
        self.mappa = leggi_impostazione(conn, CHIAVE_MAPPA, None) or {"anni": secrets.choice(ANNI_POSSIBILI), "etichette": {}}
        self._salvata = sum(len(v) for v in self.mappa["etichette"].values()) if leggi_impostazione(conn, CHIAVE_MAPPA, None) else -1
        from .importa import nomi_riservati
        from . import privacy
        self.riservati = sorted({p.upper() for n in nomi_riservati(conn) for p in [n] + n.split() if len(p) > 1},
                                key=len, reverse=True)
        self.parole_locali = [re.compile(w) for w in privacy.parole_riservate(conn)]
        self.cat_riservate = set(privacy.riservate()) | set(cfg.tassonomia().non_inviare or set())
        self.l2_riservate = privacy.l2_riservate()
        self.iban_conti = {re.sub(r"\s", "", r["iban"]).upper(): r["id"] for r in
                           conn.execute("SELECT id, iban FROM conti WHERE iban IS NOT NULL AND iban <> ''")}
        # parole di nomi propri (esercenti, controparti, titoli): mai leggibili, anche se frequenti
        self.nomi = set()
        for r in conn.execute("SELECT DISTINCT esercente, controparte_nome, intermediario, nome_titolo FROM movimenti"):
            for v in r:
                self.nomi.update(re.findall(r"[^\W\d_]+", (v or "").upper()))
        self.nomi -= PAROLE_BANCA

    # -- etichette --
    def etichetta(self, tipo, valore, separatore=" "):
        if valore in (None, ""):
            return ""
        d = self.mappa["etichette"].setdefault(tipo, {})
        k = str(valore)
        if k not in d:
            d[k] = len(d) + 1
        return "%s%s%d" % (NOMI_ETICHETTE[tipo], separatore, d[k])

    def salva(self):
        n = sum(len(v) for v in self.mappa["etichette"].values())
        if n != self._salvata:
            scrivi_impostazione(self.conn, CHIAVE_MAPPA, self.mappa)
            self._salvata = n

    def data(self, iso):
        """Data spostata indietro di un numero fisso di anni: giorno e mese restano (i conti sono per mese e gli
        abbinamenti per giorni di distanza), cambia l'anno."""
        if not iso:
            return ""
        return "%04d%s" % (int(iso[:4]) - self.mappa["anni"], iso[4:10])

    def categoria(self, l1, l2):
        if not l2:
            return ""
        if (l1, l2) in self.cat_riservate or l2 in self.l2_riservate:
            return "(categoria riservata)"
        return "%s · %s" % (l1, l2)

    def iban(self, valore):
        if not valore:
            return ""
        pulito = re.sub(r"\s", "", valore).upper()
        if pulito in self.iban_conti:
            return "di " + self.etichetta("CONTO", self.iban_conti[pulito])
        return self.etichetta("IBAN", pulito)

    # -- descrizioni --
    def scheletro(self, testo, m=None, generico=False):
        """La descrizione con le sole parole della banca: esercente, controparte e IBAN diventano etichette (senza
        numero se generico), le cifre #, tutto il resto e' nascosto."""
        t = (testo or "").upper()
        if not t.strip():
            return ""

        def segnaposto(tipo, valore):
            return "[%s]" % tipo if generico else "[%s]" % self.etichetta(tipo, valore, "_")
        for campo, tipo, chiave in (("esercente", "ESERCENTE", "chiave"), ("controparte_nome", "SOGGETTO", None)):
            nome = ((m or {}).get(campo) or "").upper().strip()
            if len(nome) > 2 and nome in t:
                valore = (m.get(chiave) or nome) if chiave else nome
                t = t.replace(nome, " %s " % segnaposto(tipo, valore))
        t = _IBAN.sub(lambda x: " %s " % ("[IBAN]" if generico else "[%s]" % self.iban(x.group(0)).replace(" ", "_")), t)
        for n in self.riservati:
            t = re.sub(r"(?<!\w)%s(?!\w)" % re.escape(n), " [TITOLARE] ", t)
        vocabolario = _vocabolario()
        out = []
        for pezzo in t.split():
            if pezzo.startswith("[") and pezzo.endswith("]"):
                out.append(pezzo)
                continue
            parti = []
            for p in _PAROLA.findall(pezzo):
                if p.isdigit():
                    parti.append("#")
                elif p.isalpha():
                    visibile = p in vocabolario and p not in self.nomi and not any(rx.search(p.lower()) for rx in self.parole_locali)
                    parti.append(p if visibile else NASCOSTO)
                else:
                    parti.append(p if len(p) <= 2 else p[:1])
            out.append("".join(parti))
        s = " ".join(out)
        s = re.sub(r"(?:%s[\s.,'/-]*)+" % re.escape(NASCOSTO), NASCOSTO + " ", s)
        s = re.sub(r"#(?:[\s.,:/-]*#)+", "#", s)
        return re.sub(r"\s+", " ", s).strip()

    def testo_app(self, testo):
        """Un testo scritto dal programma (motivo della revisione): restano le parole, spariscono i nomi di
        conti, titolari, esercenti e controparti che puo' contenere."""
        t = testo or ""
        for r in self.conn.execute("SELECT id, nome FROM conti ORDER BY length(nome) DESC"):
            if r["nome"] and r["nome"].lower() in t.lower():
                t = re.sub(re.escape(r["nome"]), self.etichetta("CONTO", r["id"]), t, flags=re.I)
        t = _IBAN.sub("[IBAN]", t)
        nascosti = self.nomi | set(self.riservati)
        return re.sub(r"[^\W\d_]{3,}", lambda x: NASCOSTO if x.group(0).upper() in nascosti else x.group(0), t)

    # -- transcodifica --
    def transcodifica(self):
        """CSV (per Excel) con etichetta e valore vero. Resta sul PC: non va inviato con il report."""
        movs = {r["id"]: r for r in self.conn.execute(
            "SELECT m.id, m.data, m.importo_cent, m.esercente, m.controparte_nome, m.descrizione, c.nome AS conto "
            "FROM movimenti m JOIN conti c ON c.id = m.conto_id")}
        conti = {str(r["id"]): r["nome"] for r in self.conn.execute("SELECT id, nome FROM conti")}
        nomi_chiave = defaultdict(set)
        for r in self.conn.execute("SELECT DISTINCT chiave, esercente FROM movimenti WHERE chiave IS NOT NULL AND esercente IS NOT NULL"):
            nomi_chiave[r["chiave"]].add(r["esercente"])
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        w.writerow(["NON INVIARE: corrispondenza tra le etichette del report di diagnostica e i valori veri"])
        w.writerow(["Date del report", "spostate indietro di %d anni" % self.mappa["anni"]])
        w.writerow([])
        w.writerow(["Tipo", "Etichetta", "Valore vero", "Data vera", "Conto", "Importo", "Esercente o controparte", "Descrizione"])
        for tipo in ("CONTO", "ESERCENTE", "SOGGETTO", "IBAN", "M"):
            for valore, n in sorted(self.mappa["etichette"].get(tipo, {}).items(), key=lambda kv: kv[1]):
                riga = [tipo, "%s %d" % (NOMI_ETICHETTE[tipo], n)]
                if tipo == "M":
                    m = movs.get(valore)
                    riga += ([valore, m["data"], m["conto"], euro(m["importo_cent"] or 0), m["esercente"] or m["controparte_nome"] or "",
                              m["descrizione"] or ""] if m else [valore, "", "", "", "", "(movimento non piu' presente)"])
                elif tipo == "CONTO":
                    riga.append(conti.get(valore, "(conto non piu' presente)"))
                elif tipo == "ESERCENTE":
                    riga.append(" | ".join(sorted(nomi_chiave.get(valore, []))) or valore)
                else:
                    riga.append(valore)
                w.writerow(riga)
        return "﻿" + buf.getvalue()


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _tabella(intestazioni, righe):
    if not righe:
        return "(nessuna riga)\n"
    def cella(v):
        return str("" if v is None else v).replace("|", "/").replace("\n", " ")
    out = ["| " + " | ".join(intestazioni) + " |", "|" + "|".join("---" for _ in intestazioni) + "|"]
    out += ["| " + " | ".join(cella(v) for v in r) + " |" for r in righe]
    return "\n".join(out) + "\n"


def _conteggi(conn, sql):
    return [tuple(r) for r in conn.execute(sql)]


def riepilogo(conn, anomalie):
    """Primo livello del report: solo conteggi e diciture della banca. Nessun importo, data o nome."""
    schema = conn.execute("PRAGMA user_version").fetchone()[0]
    mesi = conn.execute("SELECT count(DISTINCT mese) FROM analisi").fetchone()[0]
    t = ["# CashFlow Analyzer - report di diagnostica", "",
         "Versione %s, schema %s. Mesi con movimenti: %d." % (VERSIONE, schema, mesi), "",
         "## 1. Riepilogo", "", "Solo conteggi: nessun importo, data o nome.", "",
         "### Controlli", ""]
    per = Counter(a.codice for a in anomalie)
    t.append(_tabella(["Controllo", "Gravita'", "Casi", "Cosa controlla"],
                      [(k, v[0], per.get(k, 0), v[1]) for k, v in CONTROLLI.items()]))
    t += ["### Conti", "", _tabella(["Tipo", "Perimetro", "Formato", "Conti", "Movimenti"], _conteggi(conn, """
        SELECT c.tipo, c.perimetro, coalesce(c.profilo, '-'), count(DISTINCT c.id), count(m.id)
        FROM conti c LEFT JOIN movimenti m ON m.conto_id = c.id GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""")),
          "### Movimenti per formato e tipo", "", _tabella(["Formato", "Tipo", "Movimenti"], _conteggi(conn, """
        SELECT coalesce(c.profilo, '-'), m.tipo, count(*) FROM movimenti m JOIN conti c ON c.id = m.conto_id
        GROUP BY 1, 2 ORDER BY 1, 3 DESC""")),
          "### Righe di analisi per natura e fonte della categoria", "",
          _tabella(["Natura", "Fonte", "Righe", "Da rivedere", "Senza categoria"], _conteggi(conn, """
        SELECT natura, coalesce(fonte, '-'), count(*), sum(da_rivedere), sum(CASE WHEN l2 IS NULL THEN 1 ELSE 0 END)
        FROM analisi GROUP BY 1, 2 ORDER BY 1, 3 DESC""")),
          "### Trasferimenti e addebiti della carta per stato", "",
          _tabella(["Cosa", "Stato", "Fiducia", "Movimenti"], _conteggi(conn, """
        SELECT 'trasferimento', stato, coalesce(fiducia, '-'), count(*) FROM trasferimenti GROUP BY 2, 3
        UNION ALL SELECT 'addebito carta', stato, '-', count(*) FROM contenitori GROUP BY 2 ORDER BY 1, 4 DESC""")),
          "### Diciture della banca non riconosciute", "",
          "Delle descrizioni restano le sole parole della banca; il resto e' nascosto (%s)." % NASCOSTO, ""]
    diciture = [a for a in anomalie if a.codice == "TIPO_NON_RICONOSCIUTO"]
    valori = lambda a: {n: v for n, _, v in a.dati}
    t.append(_tabella(["Formato", "Codice", "Movimenti", "di cui accrediti", "Dicitura"],
                      [(v["formato"], v["codice della banca"], v["movimenti"], v["accrediti"], v["dicitura"])
                       for v in map(valori, diciture[:60])]))
    if len(diciture) > 60:
        t.append("(altre %d diciture meno frequenti non elencate)\n" % (len(diciture) - 60))
    return "\n".join(t)


def _dato(anonimo, tipo, valore):
    if tipo == "euro":
        return euro(valore)
    if tipo == "data":
        return anonimo.data(valore)
    if tipo == "mese":
        return anonimo.data(valore + "-01")[:7]
    if tipo == "conto":
        return anonimo.etichetta("CONTO", valore)
    return str(valore)


def _caso(conn, d, anonimo, n, a):
    t = ["### Caso %d - %s: %s" % (n, a.codice, a.titolo), ""]
    if a.dati:
        t += ["- %s: %s" % (nome, _dato(anonimo, tipo, valore)) for nome, tipo, valore in a.dati] + [""]
    ids = [m for m in a.movimenti if m in d.movs]
    if not ids:
        return "\n".join(t)
    mcc = cfg.mcc()
    righe, analisi, altro = [], [], []
    for mid in ids:
        m = d.movs[mid]
        c = d.conti[m["conto_id"]]
        em = anonimo.etichetta("M", mid)
        voce = mcc.get(str(m["mcc"]).strip().zfill(4)) if m["mcc"] else None
        cod_mcc = "" if not m["mcc"] else ("(riservato)" if voce and voce.get("l2") in anonimo.l2_riservate else m["mcc"])
        righe.append((em, "%s (%s, %s, %s)" % (anonimo.etichetta("CONTO", c["id"]), c["tipo"], c["perimetro"], c.get("profilo") or "-"),
                      anonimo.data(m["data"]), euro(m["importo_cent"]) if m["importo_cent"] is not None else "",
                      euro(m["fee_cent"]) if m["fee_cent"] else "", euro(m["tax_cent"]) if m["tax_cent"] else "",
                      m["tipo"], m["codice"] or "", anonimo.etichetta("ESERCENTE", m["chiave"]) if m["esercente"] else "",
                      anonimo.etichetta("SOGGETTO", (m["controparte_nome"] or "").upper().strip()),
                      anonimo.iban(m["controparte_iban"]), cod_mcc, anonimo.data(m["ciclo_carta"]),
                      anonimo.scheletro(m["descrizione"], m)))
        for r in d.analisi.get(mid, []):
            analisi.append((em, r["componente"], anonimo.data(r["data"]), anonimo.etichetta("CONTO", r["conto_id"]),
                            euro(r["importo_cent"] or 0), r["natura"], anonimo.categoria(r["l1"], r["l2"]), r["fonte"] or "",
                            "si'" if r["da_rivedere"] else "", anonimo.testo_app(r["motivo"])))
        tr = d.trasferimenti.get(mid)
        if tr:
            altro.append((em, "trasferimento", tr["stato"], tr["fiducia"] or "",
                          anonimo.etichetta("M", tr["controparte_mov_id"]) if tr["controparte_mov_id"] in d.movs else "",
                          anonimo.etichetta("CONTO", tr["conto_destinazione_id"])))
        co = d.contenitori.get(mid)
        if co:
            altro.append((em, "addebito carta", co["stato"], "", "righe %s, differenza %s" % (co["righe"] or 0, euro(co["differenza_cent"] or 0)),
                          "ciclo %s" % anonimo.data(co["ciclo"])))
        dec = conn.execute("SELECT l1, l2, natura FROM decisioni WHERE mov_id = ?", (mid,)).fetchone()
        if dec:
            altro.append((em, "decisione dell'utente", dec["natura"] or "", "", anonimo.categoria(dec["l1"], dec["l2"]), ""))
        for p in conn.execute("SELECT importo_cent, l1, l2 FROM ripartizioni WHERE mov_id = ? ORDER BY n", (mid,)):
            altro.append((em, "parte della suddivisione", "", "", euro(p["importo_cent"]), anonimo.categoria(p["l1"], p["l2"])))
    t += ["Movimenti:", "", _tabella(["Mov.", "Conto", "Data", "Importo", "Comm.", "Imposta", "Tipo", "Codice", "Esercente",
                                      "Controparte", "IBAN", "MCC", "Ciclo carta", "Descrizione"], righe),
          "Come contano (righe di analisi):", "",
          _tabella(["Mov.", "Componente", "Data", "Conto", "Importo", "Natura", "Categoria", "Fonte", "Rivedere", "Motivo"], analisi)]
    if altro:
        t += ["Abbinamenti e decisioni:", "", _tabella(["Mov.", "Cosa", "Stato", "Fiducia", "Dettaglio", "Altro"], altro)]
    return "\n".join(t)


def report(conn, anomalie, scelte=()):
    """Testo del report: riepilogo e, se scelti, i casi anonimizzati. Restituisce (testo, transcodifica CSV).
    Le etichette nuove vengono salvate nel database."""
    scelte = list(scelte)[:MASSIMO_CASI]
    anonimo = Anonimo(conn)
    t = [riepilogo(conn, anomalie), "## 2. Casi scelti", ""]
    if scelte:
        d = _Dati(conn)
        t += ["Importi veri. Date spostate indietro di un numero fisso di anni (giorno e mese restano). Nomi, conti, "
              "IBAN e identificativi sostituiti da etichette; delle descrizioni restano le sole parole della banca.", ""]
        t += [_caso(conn, d, anonimo, n, a) for n, a in enumerate(scelte, 1)]
    else:
        t.append("Nessun caso scelto.\n")
    anonimo.salva()
    return "\n".join(t), anonimo.transcodifica()
