"""Ricalcolo completo del livello derivato (tabelle analisi, trasferimenti, contenitori).

Si ricostruisce da zero a ogni import o decisione: i dati grezzi e le decisioni sono persistenti,
tutto il resto si deriva. Con poche migliaia di righe richiede meno di un secondo.
"""
import re
from collections import defaultdict
from datetime import date, timedelta

from . import configurazione as cfg
from .catena import Catena, chiave_beneficiario
from .db import leggi_impostazione, transazione


def _d(s):
    return date.fromisoformat(s)


def _iban(s):
    return re.sub(r"\s+", "", s.upper()) if s else None


# parole che accompagnano un nome senza farne parte ("SIG.RA ROSSI", "ROSSI MARIO E BIANCHI ANNA")
_CONTORNO = {"SIG", "SIGRA", "SIGNOR", "SIGNORA", "RA", "DOTT", "DOTTSSA", "SSA", "DR", "ED", "E"}


def _persone(nomi):
    """Le parole (almeno 2 lettere) del nome di ogni persona indicata in Impostazioni."""
    out = []
    for n in nomi or []:
        parole = [p.upper() for p in re.split(r"[\s,]+", n or "") if len(p) >= 2]
        if parole:
            out.append(parole)
    return out


def stessa_persona(persone, nome):
    """Il nome della controparte e' quello di una delle persone? Servono almeno due parole del suo nome
    ("ROSSI MARIO" per Mario Rossi, in qualunque ordine), oppure una sola se il nome indicato ha una parola
    o se la controparte non contiene altro ("ROSSI M."). Un omonimo di nome ("MARIO BIANCHI") non basta."""
    if not nome:
        return False
    testo = nome.upper()
    significative = {p for p in re.findall(r"[^\W\d_]+", testo) if len(p) >= 2} - _CONTORNO
    for parole in persone:
        trovate = {p for p in parole if re.search(r"(?<!\w)%s(?!\w)" % re.escape(p), testo)}
        if not trovate:
            continue
        if len(trovate) >= min(2, len(parole)) or significative <= set(parole):
            return True
    return False


class Contesto:
    def __init__(self, conn):
        self.conn = conn
        self.conti = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM conti")}
        self.iban_conto = {_iban(c["iban"]): cid for cid, c in self.conti.items() if c["iban"]}
        # per riconoscere i versamenti di titolari e familiari serve il nome intero (per la privacy, invece,
        # ogni singola parola del nome resta protetta: vedi privacy.controlla)
        self.titolari = _persone(leggi_impostazione(conn, "titolari", []))
        self.familiari = _persone(leggi_impostazione(conn, "familiari", []))
        self.copertura = {}
        for r in conn.execute("SELECT conto_id, min(data_min), max(data_max) FROM import_file GROUP BY conto_id"):
            if r[1]:
                self.copertura[r[0]] = (r[1], r[2])
        self.manuali = {}
        for r in conn.execute("SELECT * FROM abbinamenti_manuali"):
            self.manuali[(r["mov_a"], r["mov_b"])] = r["esito"]
            self.manuali[(r["mov_b"], r["mov_a"])] = r["esito"]
        self.destinazioni = {r["beneficiario"]: r["esito"] for r in conn.execute("SELECT * FROM destinazioni")}
        # conto gia' censito indicato a mano per i bonifici senza IBAN (per movimento o per beneficiario)
        self.conto_benef = {r[0]: r[1] for r in conn.execute("SELECT beneficiario, conto_id FROM destinazioni_conti")}
        self.conto_mov = {r[0]: r[1] for r in conn.execute("SELECT mov_id, conto_id FROM destinazioni_movimenti")}
        # addebiti PayPal ricondotti a mano a un pagamento del report (None: non e' nel report)
        self.paypal_manuali = {r[0]: r[1] for r in conn.execute("SELECT addebito_id, paypal_id FROM abbinamenti_paypal")}
        self.finestra = cfg.impostazioni()["trasferimenti"]["finestra_giorni"]

    def conto_indicato(self, m):
        """Conto dall'altra parte indicato a mano per un bonifico (None se non indicato o se e' lo stesso conto)."""
        if not (self.conto_mov or self.conto_benef):
            return None
        cid = self.conto_mov.get(m["id"])
        if cid is None:
            cid = self.conto_benef.get(chiave_beneficiario(m["controparte_iban"], m["controparte_nome"]))
        return cid if cid in self.conti and cid != m["conto_id"] else None

    def nome_titolare(self, nome):
        return stessa_persona(self.titolari, nome)

    def nome_familiare(self, nome):
        return stessa_persona(self.familiari, nome)

    def coperto(self, conto_id, giorno):
        c = self.copertura.get(conto_id)
        return bool(c and c[0] <= giorno <= c[1])


# contributo verso (o da) un tuo conto importato che non copre ancora quella data: l'altro lato arrivera' con il
# prossimo file. Guardando insieme i due conti il contributo si annulla come quelli abbinati (analisi.Filtri)
ATTESA = "attesa"


def _effetto(m):
    return (m["importo_cent"] or 0) + m["fee_cent"] + m["tax_cent"]


# ---------------------------------------------------------------------------
# Trasferimenti
# ---------------------------------------------------------------------------
def abbina_trasferimenti(ctx, movs):
    """Restituisce {mov_id: esito} per i movimenti di tipo trasferimento."""
    tipi = cfg.tipi_movimento()["tipi"]
    cand = [m for m in movs if tipi.get(m["tipo"], {}).get("natura") == "trasferimento" and m["importo_cent"]]
    neg = [m for m in cand if m["importo_cent"] < 0]
    pos = [m for m in cand if m["importo_cent"] > 0]
    per_importo = defaultdict(list)
    for b in pos:
        per_importo[b["importo_cent"]].append(b)

    coppie = []
    for a in neg:
        for b in per_importo.get(-a["importo_cent"], []):
            if b["conto_id"] == a["conto_id"]:
                continue
            gg = abs((_d(b["data"]) - _d(a["data"])).days)
            man = ctx.manuali.get((a["id"], b["id"]))
            if man == "no":
                continue
            if gg > ctx.finestra and man != "si":
                continue
            iban_ok = bool((_iban(a["controparte_iban"]) == _iban(ctx.conti[b["conto_id"]]["iban"]) and a["controparte_iban"]) or
                           (_iban(b["controparte_iban"]) == _iban(ctx.conti[a["conto_id"]]["iban"]) and b["controparte_iban"]) or
                           ctx.conto_indicato(a) == b["conto_id"] or ctx.conto_indicato(b) == a["conto_id"])
            # l'IBAN (o il conto indicato a mano) di un lato porta a un altro dei tuoi conti: non e' questa coppia
            altrove = False
            for x, y in ((a, b), (b, a)):
                dest = ctx.iban_conto.get(_iban(x["controparte_iban"])) if x["controparte_iban"] else None
                if dest is None:
                    dest = ctx.conto_indicato(x)
                if dest is not None and dest not in (x["conto_id"], y["conto_id"]):
                    altrove = True
            if altrove and man != "si":
                continue
            # un indizio per lato: tipo (giroconto, versamento dal proprio conto) o nome di un titolare. Due indizi
            # sullo stesso lato ("giroconto a favore di MARIO ROSSI") non dicono nulla dell'altro movimento
            lato = lambda x: bool(tipi[x["tipo"]].get("giroconto") or tipi[x["tipo"]].get("proprio")
                                  or ctx.nome_titolare(x["controparte_nome"]))
            prova = int(lato(a)) + int(lato(b))
            if man == "si" or iban_ok or prova >= 2:
                fiducia = "alta"
            elif prova == 1:
                fiducia = "media"
            elif gg <= 1:
                fiducia = "bassa"
            else:
                continue
            rango = {"alta": 0, "media": 1, "bassa": 2}[fiducia]
            coppie.append((0 if man == "si" else 1, rango, 0 if iban_ok else 1, gg, a, b, fiducia))
    coppie.sort(key=lambda x: x[:4])
    usati, esiti = set(), {}
    proposte = {}
    for _, _, _, gg, a, b, fiducia in coppie:
        if a["id"] in usati or b["id"] in usati:
            continue
        if fiducia != "alta":
            for x, y in ((a, b), (b, a)):
                proposte.setdefault(x["id"], (y, fiducia))
            continue
        usati.update((a["id"], b["id"]))
        ca, cb = ctx.conti[a["conto_id"]], ctx.conti[b["conto_id"]]
        for x, y, cx, cy in ((a, b, ca, cb), (b, a, cb, ca)):
            e = {"stato": "abbinato", "fiducia": fiducia, "controparte": y["id"], "conto_dest": y["conto_id"],
                 "da_rivedere": False, "motivo": None, "l2": None}
            if cy["tipo"] == "deposito" and cx["tipo"] not in ("broker", "deposito"):
                e["natura"] = "risparmio"
                e["l2"] = "Accantonamento" if x["importo_cent"] < 0 else "Rientro dal risparmio"
            elif cx["tipo"] in ("broker", "deposito") or cy["tipo"] in ("broker", "deposito"):
                # verso un broker di cui ci sono i movimenti: il risparmio sono gli acquisti di titoli, che contano
                # gia' come accantonamento. Il versamento che li finanzia e' un passaggio tra i tuoi conti
                e["natura"] = "trasferimento_interno"
            elif cx["perimetro"] == cy["perimetro"]:
                e["natura"] = "trasferimento_interno"
            else:
                # tra un conto personale e il conto comune: per il comune e' un contributo ricevuto, per il conto
                # personale un contributo versato. Il segno dice il verso: un bonifico dal comune al personale
                # e' una restituzione (contributo ricevuto negativo da una parte, rimborso del versato dall'altra)
                e["stato"] = "contributo"
                if cx["perimetro"] == "comune":
                    e["natura"], e["l2"] = "entrata", "Contributo ricevuto"
                else:
                    e["natura"], e["l2"] = "uscita", "Contributo versato"
            esiti[x["id"]] = e

    for m in cand:
        if m["id"] in esiti:
            continue
        benef = chiave_beneficiario(m["controparte_iban"], m["controparte_nome"])
        e = {"stato": None, "fiducia": None, "controparte": None, "conto_dest": None, "beneficiario": benef,
             "da_rivedere": False, "motivo": None, "l2": None}
        dest = ctx.iban_conto.get(_iban(m["controparte_iban"])) if m["controparte_iban"] else None
        if dest is None:
            dest = ctx.conto_indicato(m)
        scelta = ctx.destinazioni.get(benef) if benef else None
        if ctx.conto_mov.get(m["id"]) is not None:
            scelta = None                       # la scelta sul singolo movimento prevale su quella generica
        tipo = tipi[m["tipo"]]
        uscita = m["importo_cent"] < 0
        comune = ctx.conti[m["conto_id"]]["perimetro"] == "comune"
        di_famiglia = ctx.nome_titolare(m["controparte_nome"]) or ctx.nome_familiare(m["controparte_nome"])
        cd = ctx.conti.get(dest) if dest is not None and dest != m["conto_id"] else None
        if (comune and not uscita and (di_famiglia or (cd and cd["perimetro"] != "comune"))
                and not (cd and cd["tipo"] in ("broker", "deposito"))):
            # versamento sul conto comune da un titolare o da un familiare: per il conto comune e' un'entrata,
            # anche quando il conto da cui parte non e' caricato (se lo e', l'abbinamento sopra lo ha gia' gestito)
            e.update(stato="contributo", natura="entrata", l2="Contributo ricevuto", conto_dest=dest)
            if cd and cd["importato"] and ctx.coperto(dest, m["data"]):
                e.update(da_rivedere=True, motivo="Contributo da un tuo conto: la contropartita non c'e' nel periodo importato")
            elif cd and cd["importato"]:
                e["fiducia"] = ATTESA
        elif comune and uscita and cd is None and ctx.nome_familiare(m["controparte_nome"]):
            # bonifico dal conto comune a un familiare il cui conto non e' analizzato: rimborsa spese domiciliate sul
            # suo conto (rata dell'auto, assicurazione...). E' un'uscita vera; la categoria viene dalla causale
            # (decisione sul movimento, regola sulla descrizione o suddivisione in parti)
            e.update(stato="spesa_familiare", natura="uscita", senza_storico=True)
        elif scelta == "non_caricato":
            e.update(stato="da_verificare", natura="trasferimento_interno")
        elif scelta == "non_tracciato":
            e.update(stato="verso_risparmio", natura="risparmio", l2="Accantonamento" if uscita else "Rientro dal risparmio")
        elif scelta == "altra_persona":
            e.update(stato="terzi", natura="uscita" if uscita else "entrata")
        elif dest is not None and dest != m["conto_id"]:
            cd = ctx.conti[dest]
            e["conto_dest"] = dest
            mio = ctx.conti[m["conto_id"]]
            presente = cd["importato"] and ctx.coperto(dest, m["data"])
            if cd["tipo"] == "broker" and presente:
                e.update(stato="mancante", natura="trasferimento_interno", da_rivedere=True,
                         motivo="Trasferimento verso un tuo conto: la contropartita non c'e' nel periodo importato")
            elif cd["tipo"] in ("broker", "deposito"):
                e.update(stato="verso_risparmio", natura="risparmio", l2="Accantonamento" if uscita else "Rientro dal risparmio")
            elif cd["perimetro"] != mio["perimetro"] and mio["tipo"] not in ("broker", "deposito"):
                # tra un conto personale e il conto comune, come per le coppie abbinate: e' un contributo anche
                # quando l'altro lato non e' (ancora) tra i movimenti importati
                e["stato"] = "contributo"
                if mio["perimetro"] == "comune":
                    e.update(natura="entrata", l2="Contributo ricevuto")
                else:
                    e.update(natura="uscita", l2="Contributo versato")
                if presente:
                    e.update(da_rivedere=True, motivo="Contributo verso un tuo conto: la contropartita non c'e' nel periodo importato")
                elif cd["importato"]:
                    e["fiducia"] = ATTESA
            elif presente:
                e.update(stato="mancante", natura="trasferimento_interno", da_rivedere=True,
                         motivo="Trasferimento verso un tuo conto: la contropartita non c'e' nel periodo importato")
            else:
                e.update(stato="da_verificare", natura="trasferimento_interno")
        elif tipo.get("proprio"):
            e.update(stato="da_verificare", natura="trasferimento_interno")
        elif ctx.nome_titolare(m["controparte_nome"]) or tipo.get("giroconto"):
            e.update(stato="proposto_proprio", natura="trasferimento_interno", da_rivedere=True,
                     motivo="Trasferimento a tuo nome senza contropartita: conto non caricato, non tracciato o altra persona?")
        else:
            e.update(stato="terzi", natura="uscita" if uscita else "entrata")
        if m["id"] in proposte:
            y, fid = proposte[m["id"]]
            e["da_rivedere"] = True
            e["proposta_abbinamento"] = y["id"]
            e["motivo"] = "Possibile trasferimento con %s del %s (fiducia %s): confermi?" % (
                ctx.conti[y["conto_id"]]["nome"], y["data"], fid)
        esiti[m["id"]] = e
    return esiti


# ---------------------------------------------------------------------------
# PayPal: addebito sul conto o sulla carta <-> ricarica nel report PayPal
# ---------------------------------------------------------------------------
GIORNI_PAYPAL = 7
GIORNI_RICARICA = 3                 # ricarica e pagamento nel report: stesso importo, a pochi giorni
GIORNI_CANDIDATI = 60               # pagamenti PayPal proposti per un addebito da ricondurre a mano
MOTIVO_PAYPAL = ("Addebito PayPal senza la ricarica corrispondente nel report PayPal (che copre questa data): "
                 "se la spesa c'e' anche nel report, e' contata due volte")


MOTIVO_ACCREDITO_PAYPAL = ("Accredito PayPal senza l'uscita corrispondente nel report PayPal (che copre questa data): "
                           "se il rimborso c'e' anche nel report, e' contato due volte")


def _paypal(m):
    testo = " ".join(str(m.get(k) or "") for k in ("intermediario", "esercente", "descrizione")).upper()
    return "PAYPAL" in testo


def _uscita_paypal(m, conti_pp):
    """Uscita del report PayPal che un addebito in banca puo' finanziare: pagamenti a esercenti e a persone
    (anche i tipi che il profilo non riconosce, come "Pagamento generico"), non ricariche e prelievi."""
    return (m["conto_id"] in conti_pp and (m["importo_cent"] or 0) < 0
            and m["tipo"] not in ("ricarica", "prelievo_verso_conto"))


def _vicina(m, lista, usate, giorni):
    """Il movimento della lista con importo opposto, entro `giorni`, non ancora usato e piu' vicino di data."""
    c = [r for r in lista if r["id"] not in usate and r["importo_cent"] == -m["importo_cent"]
         and abs((_d(r["data"]) - _d(m["data"])).days) <= giorni]
    return min(c, key=lambda r: abs((_d(r["data"]) - _d(m["data"])).days)) if c else None


def abbina_paypal(ctx, movs):
    """Con il report PayPal caricato, la spesa compare due volte: nel report (pagamento all'esercente, con il nome
    vero) e sul conto o sulla carta che l'ha pagata ("PAYPAL *..." o addebito SEPA di PayPal). Ogni addebito di
    PayPal si abbina alla ricarica dello stesso importo nel report, entro una settimana: l'addebito diventa un
    contenitore (il dettaglio e' nel report), la ricarica un trasferimento interno. Resta solo il pagamento
    all'esercente. Un addebito PayPal senza ricarica, in un periodo coperto dal report, va controllato: in
    Revisione si riconduce a mano a un pagamento del report (vale prima dell'abbinamento automatico) o si indica
    che nel report non c'e'.

    Restituisce {mov_id: esito}; i pagamenti del report gia' coperti da un addebito hanno {"coperto": addebito}."""
    conti_pp = {cid for cid, c in ctx.conti.items() if (c.get("profilo") or "").startswith("paypal")}
    if not conti_pp:
        return {}
    ricariche = [m for m in movs if m["conto_id"] in conti_pp and m["tipo"] == "ricarica" and (m["importo_cent"] or 0) > 0]
    pagamenti = [m for m in movs if _uscita_paypal(m, conti_pp)]
    addebiti = [m for m in movs if m["conto_id"] not in conti_pp and (m["importo_cent"] or 0) < 0
                and m["tipo"] in ("pagamento_carta", "addebito_sepa") and _paypal(m)]
    date_pp = [m["data"] for m in movs if m["conto_id"] in conti_pp]
    dal, al = (min(date_pp), max(date_pp)) if date_pp else (None, None)
    per_id = {m["id"]: m for m in movs}
    esiti, usate, coperti = {}, set(), set()
    # 1. scelte fatte a mano in Revisione
    for a in addebiti:
        if a["id"] not in ctx.paypal_manuali:
            continue
        pid = ctx.paypal_manuali[a["id"]]
        p = per_id.get(pid) if pid else None
        if pid is None:
            esiti[a["id"]] = {"nel_report": False}         # spesa normale, nessun avviso
        elif p is not None and p["conto_id"] in conti_pp and pid in coperti:
            # lo stesso pagamento ricondotto a mano a due addebiti (dati salvati prima della 0.16.2): vale il
            # primo, l'altro torna da controllare invece di sparire dai conti
            esiti[a["id"]] = {"da_rivedere": True, "motivo": MOTIVO_PAYPAL}
        elif p is not None and p["conto_id"] in conti_pp:
            esiti[a["id"]] = {"natura": "contenitore", "abbinato": pid, "manuale": True}
            esiti[pid] = {"coperto": a["id"]}
            coperti.add(pid)
            r = _vicina(p, ricariche, usate, GIORNI_RICARICA)
            if r:
                usate.add(r["id"])
                esiti[r["id"]] = {"natura": "trasferimento_interno", "abbinato": a["id"]}
    # 2. abbinamento automatico addebito <-> ricarica
    for a in sorted(addebiti, key=lambda m: m["data"]):
        if a["id"] in esiti:
            continue
        r = _vicina(a, ricariche, usate, GIORNI_PAYPAL)
        if r:
            usate.add(r["id"])
            esiti[a["id"]] = {"natura": "contenitore", "abbinato": r["id"]}
            esiti[r["id"]] = {"natura": "trasferimento_interno", "abbinato": a["id"]}
            p = _vicina(r, pagamenti, coperti, GIORNI_RICARICA)
            if p:
                coperti.add(p["id"])
                esiti[p["id"]] = {"coperto": a["id"]}
        elif dal and dal <= a["data"] <= al:
            esiti[a["id"]] = {"da_rivedere": True, "motivo": MOTIVO_PAYPAL}
    # 3. il verso opposto: un rimborso (o un prelievo dal saldo) che PayPal riaccredita sul conto o sulla carta.
    # Nel report ci sono il rimborso dell'esercente e l'uscita verso la banca; in banca arriva "PAYPAL *..." in
    # accredito. Il rimborso e' uno solo, quello del report (con il nome dell'esercente): l'uscita dal conto
    # PayPal e l'accredito in banca sono un passaggio tra i tuoi conti
    prelievi = [m for m in movs if m["conto_id"] in conti_pp and m["tipo"] == "prelievo_verso_conto"
                and (m["importo_cent"] or 0) < 0]
    rimborsi = [m for m in movs if m["conto_id"] in conti_pp and m["tipo"] == "pagamento_carta"
                and (m["importo_cent"] or 0) > 0]
    accrediti = [m for m in movs if m["conto_id"] not in conti_pp and (m["importo_cent"] or 0) > 0
                 and m["tipo"] in ("pagamento_carta", "addebito_sepa", "bonifico", "versamento") and _paypal(m)]
    for a in sorted(accrediti, key=lambda m: m["data"]):
        if a["id"] in esiti:
            continue
        w = _vicina(a, prelievi, usate, GIORNI_PAYPAL)
        if w:
            usate.add(w["id"])
            esiti[a["id"]] = {"natura": "trasferimento_interno", "abbinato": w["id"]}
            esiti[w["id"]] = {"natura": "trasferimento_interno", "abbinato": a["id"]}
            r = _vicina(w, rimborsi, coperti, GIORNI_RICARICA)
            if r:
                coperti.add(r["id"])
                esiti[r["id"]] = {"coperto": a["id"]}
        elif dal and dal <= a["data"] <= al and a["tipo"] in ("pagamento_carta", "addebito_sepa"):
            esiti[a["id"]] = {"da_rivedere": True, "motivo": MOTIVO_ACCREDITO_PAYPAL}
    return esiti


def cerca_pagamenti_paypal(conn, addebito_id, testo=None, giorni=30):
    """Ricerca libera nel report PayPal per un addebito che le proposte non trovano: tutte le uscite del report
    (qualunque tipo, qualunque importo) entro `giorni` dall'addebito, eventualmente con `testo` nel nome o nella
    descrizione. Per ognuna: differenza di importo e di giorni, somiglianza del nome e, se e' gia' abbinata a un
    altro addebito, quale. Prima quelle con l'importo piu' vicino, poi le piu' vicine di data."""
    from .paypal import livello_somiglianza, nome_utile, somiglianza
    ctx = Contesto(conn)
    movs = [dict(r) for r in conn.execute("SELECT * FROM movimenti ORDER BY data, id")]
    per_id = {m["id"]: m for m in movs}
    a = per_id.get(addebito_id)
    if not a:
        return []
    esiti = abbina_paypal(ctx, movs)
    coperti = {k: e["coperto"] for k, e in esiti.items() if e.get("coperto") and e["coperto"] != addebito_id}
    conti_pp = {cid for cid, c in ctx.conti.items() if (c.get("profilo") or "").startswith("paypal")}
    cerca = (testo or "").strip().lower()
    nome_a = a["esercente"] or a["descrizione"] or ""
    con_nome = nome_utile(nome_a)
    out = []
    for p in movs:
        if p["conto_id"] not in conti_pp or (p["importo_cent"] or 0) >= 0 or p["tipo"] in ("ricarica", "prelievo_verso_conto"):
            continue
        gg = (_d(p["data"]) - _d(a["data"])).days
        if abs(gg) > giorni:
            continue
        nome = p["esercente"] or p["controparte_nome"] or ""
        if cerca and cerca not in (nome + " " + (p["descrizione"] or "")).lower():
            continue
        x = somiglianza(nome_a, nome) if con_nome else 0.0
        altro = per_id.get(coperti.get(p["id"]))
        out.append({"id": p["id"], "data": p["data"], "nome": nome, "descrizione": p["descrizione"],
                    "importo_cent": p["importo_cent"], "differenza": p["importo_cent"] - a["importo_cent"],
                    "giorni": gg, "somiglianza": x,
                    "livello": livello_somiglianza(x) if con_nome else "non valutabile",
                    "gia_abbinato": ("%s del %s" % (ctx.conti[altro["conto_id"]]["nome"], altro["data"])) if altro else None})
    out.sort(key=lambda c: (abs(c["differenza"]), abs(c["giorni"]), -c["somiglianza"]))
    return out


def candidati_paypal(conn, addebiti_id, giorni=GIORNI_CANDIDATI):
    """Per ogni addebito PayPal da ricondurre: i pagamenti del report con lo stesso importo, entro `giorni`, non
    gia' coperti da un altro addebito, dal piu' simile. {addebito: [{id, data, nome, descrizione, importo_cent,
    giorni, somiglianza, livello}]}. Il primo e' la proposta se e' abbastanza simile, o se e' l'unico vicino."""
    from .paypal import livello_somiglianza, nome_utile, somiglianza
    ctx = Contesto(conn)
    movs = [dict(r) for r in conn.execute("SELECT * FROM movimenti ORDER BY data, id")]
    esiti = abbina_paypal(ctx, movs)
    coperti = {k for k, e in esiti.items() if e.get("coperto") and e["coperto"] not in addebiti_id}
    conti_pp = {cid for cid, c in ctx.conti.items() if (c.get("profilo") or "").startswith("paypal")}
    pagamenti = [m for m in movs if _uscita_paypal(m, conti_pp) and m["id"] not in coperti]
    per_id = {m["id"]: m for m in movs}
    out = {}
    for aid in addebiti_id:
        a = per_id.get(aid)
        if not a:
            continue
        nome = a["esercente"] or a["descrizione"] or ""
        # addebiti SEPA di PayPal ("PAYPAL EUROPE S.A.R.L. ET CIE..."): il nome non dice nulla dell'esercente,
        # contano solo importo e data
        con_nome = nome_utile(nome)
        lista = []
        for p in pagamenti:
            gg = (_d(p["data"]) - _d(a["data"])).days
            if p["importo_cent"] != a["importo_cent"] or abs(gg) > giorni:
                continue
            x = somiglianza(nome, p["esercente"] or p["controparte_nome"] or "") if con_nome else 0.0
            lista.append({"id": p["id"], "data": p["data"], "nome": p["esercente"] or p["controparte_nome"],
                          "descrizione": p["descrizione"], "importo_cent": p["importo_cent"], "giorni": gg,
                          "somiglianza": x, "livello": livello_somiglianza(x) if con_nome else "non valutabile"})
        lista.sort(key=lambda c: (-c["somiglianza"], abs(c["giorni"])))
        if lista:
            vicini = [c for c in lista if abs(c["giorni"]) <= 15]
            primo = lista[0]
            unico = len(vicini) == 1 and vicini[0] is primo
            distacco = len(lista) == 1 or primo["somiglianza"] - lista[1]["somiglianza"] >= 0.1
            primo["proposta"] = (primo["somiglianza"] >= 0.35 and distacco) or unico or (not con_nome and len(lista) == 1)
        out[aid] = lista
    # uno stesso pagamento non si propone a due addebiti (es. due mesi dello stesso abbonamento): resta a quello
    # piu' simile e piu' vicino di data, l'altro va scelto a mano
    proposte = sorted(((aid, l[0]) for aid, l in out.items() if l and l[0].get("proposta")),
                      key=lambda x: (-x[1]["somiglianza"], abs(x[1]["giorni"])))
    gia = set()
    for aid, c in proposte:
        if c["id"] in gia:
            c["proposta"] = False
        gia.add(c["id"])
    return out


# ---------------------------------------------------------------------------
# Carta di credito: addebito mensile sul conto <-> righe della carta
# ---------------------------------------------------------------------------
FINESTRA_CARTA = 75             # giorni prima del ciclo in cui cercare le righe di un addebito della carta


def _giorno(r):
    return r["data_registrazione"] or r["data"]


def carte_per_conto(conti, movs):
    """{conto di addebito: [carte]} e {carta: conto} per le carte senza conto di addebito indicato, quando si
    capisce da soli: un solo conto con addebiti della carta di credito (a parita', quello della stessa banca)."""
    carte, dedotte = defaultdict(list), {}
    for cid, c in conti.items():
        if c["tipo"] == "carta" and c.get("conto_addebito_id"):
            carte[c["conto_addebito_id"]].append(cid)
    con_addebiti = sorted({m["conto_id"] for m in movs if m["tipo"] == "addebito_carta_credito"
                           and conti[m["conto_id"]]["tipo"] != "carta"})
    for cid, c in conti.items():
        if c["tipo"] != "carta" or c.get("conto_addebito_id"):
            continue
        banca = (c.get("banca") or "").strip().lower()
        stessa = [k for k in con_addebiti if banca and (conti[k].get("banca") or "").strip().lower() == banca]
        scelta = stessa or con_addebiti
        if len(scelta) == 1:
            carte[scelta[0]].append(cid)
            dedotte[cid] = scelta[0]
    return carte, dedotte


def _tratto_esatto(righe, obiettivo):
    """Righe consecutive (in ordine di data) la cui somma e' l'addebito: il tratto piu' lungo, poi il piu'
    recente. L'estratto della carta copre un periodo continuo, che puo' finire anche giorni prima dell'addebito
    (es. ING: il 10 del mese si addebitano le spese del mese prima)."""
    pref = [0]
    for r in righe:
        pref.append(pref[-1] + _effetto(r))
    meglio = None
    for i in range(len(righe)):
        for j in range(i + 1, len(righe) + 1):
            if pref[j] - pref[i] == obiettivo and (meglio is None or (j - i, j) > meglio[0]):
                meglio = ((j - i, j), i, j)
    return righe[meglio[1]:meglio[2]] if meglio else None


MOTIVO_RIGA_FUORI_POSTO = ("Spesa arrivata dall'estratto di una carta di credito ma registrata nel conto %s, che non "
                           "e' di tipo Carta: se lo stesso estratto e' anche in un conto di tipo Carta conta due "
                           "volte. Annulla l'import del file della carta in questo conto")


def _periodo_vicino(righe, ciclo, inizio, obiettivo):
    """Senza un tratto che torna al centesimo: le righe del mese prima del ciclo oppure quelle dopo `inizio` (il
    ciclo precedente), a seconda di quale somma si avvicina di piu' all'addebito."""
    mese_prima = (_d(ciclo).replace(day=1) - timedelta(days=1)).isoformat()[:7]
    periodi = [[r for r in righe if _giorno(r)[:7] == mese_prima], [r for r in righe if _giorno(r) > inizio]]
    periodi = [x for x in periodi if x]
    return min(periodi, key=lambda x: abs(sum(_effetto(r) for r in x) - obiettivo)) if periodi else []


def abbina_contenitori(ctx, movs):
    """Addebito mensile della carta di credito sul conto <-> righe della carta. Le righe restano le spese (ognuna
    con la sua categoria), l'addebito diventa un contenitore che non conta.

    1. Per ogni addebito, in ordine di ciclo, si cercano tra le righe della carta non ancora usate nei 75 giorni
       prima del ciclo quelle consecutive che sommano esattamente all'addebito. Vale anche per le righe "fuori
       posto" (estratto di carta in un conto che non e' una carta, o carta senza conto di addebito): l'addebito
       non conta e resta da rivedere con cosa fare. Ogni riga copre un solo addebito.
    2. Agli addebiti rimasti senza tratto esatto restano solo le righe che nessun addebito ha usato: il periodo
       piu' vicino (mese precedente o dal ciclo prima). Se spiegano meno dell'addebito, la differenza conta come
       spesa "Varie" da rivedere (componente "residuo"): un estratto parziale non fa sparire il resto.
    3. Senza righe l'addebito conta per intero come spesa "Varie", da rivedere.

    In ctx.righe_fuori_posto restano le righe di carta in un conto non di tipo Carta che nessun addebito ha
    usato: {mov_id: motivo}, da segnalare perche' possono essere doppioni."""
    esiti = {}
    addebiti = sorted([m for m in movs if m["tipo"] == "addebito_carta_credito"],
                      key=lambda m: (m["ciclo_carta"] or m["data"], m["conto_id"]))
    carte, _ = carte_per_conto(ctx.conti, movs)
    senza_conto = [cid for cid, c in ctx.conti.items() if c["tipo"] == "carta" and not c.get("conto_addebito_id")
                   and not any(cid in v for v in carte.values())]
    righe_carta = defaultdict(list)
    for m in movs:
        if ctx.conti[m["conto_id"]]["tipo"] == "carta":
            righe_carta[m["conto_id"]].append(m)
    # righe di carta che nessun addebito trova al loro posto ma che contano gia' come spese: arrivate da un
    # estratto di carta in un conto che non e' una carta, o in una carta senza conto di addebito
    profili_carta = [k for k, p in cfg.profili().items() if p.get("tipo_conto") == "carta"]
    da_file_carta = set()
    if profili_carta and getattr(ctx, "conn", None) is not None:
        da_file_carta = {r[0] for r in ctx.conn.execute(
            "SELECT DISTINCT r.mov_id FROM righe_raw r JOIN import_file f ON f.id = r.import_id WHERE f.profilo IN (%s)"
            % ",".join("?" * len(profili_carta)), profili_carta)}
    nel_conto_sbagliato = [m for m in movs if m["id"] in da_file_carta and ctx.conti[m["conto_id"]]["tipo"] != "carta"
                           and m["tipo"] != "addebito_carta_credito"]
    fuori_posto = sorted(nel_conto_sbagliato + [m for m in movs if m["conto_id"] in senza_conto],
                         key=lambda r: (_giorno(r), r["id"]))
    conti_con_addebiti = {m["conto_id"] for m in addebiti}
    for v in righe_carta.values():
        v.sort(key=lambda r: (_giorno(r), r["id"]))
    usate_righe = set()
    prec = {}                       # (conto di addebito, carta) -> ciclo dell'ultimo addebito
    usate = defaultdict(set)        # (conto di addebito, ciclo) -> carte gia' abbinate a un addebito

    def finestra(m):
        ciclo = m["ciclo_carta"] or m["data"]
        return ciclo, (_d(ciclo) - timedelta(days=FINESTRA_CARTA)).isoformat()

    def libere_di(m, ciclo):
        return [cid for cid in carte.get(m["conto_id"], []) if cid not in usate[(m["conto_id"], ciclo)]]

    def altrove_di(m, ciclo, dal):
        """Righe fuori posto che possono essere il dettaglio di questo addebito: quelle di una carta senza conto
        di addebito, e quelle finite in un conto non di tipo Carta se e' lo stesso conto dell'addebito (o un conto
        che non ha addebiti suoi). Una riga finita nel conto A non spiega un addebito del conto B."""
        return [r for r in fuori_posto if r["id"] not in usate_righe and dal < _giorno(r) <= ciclo
                and (ctx.conti[r["conto_id"]]["tipo"] == "carta" or r["conto_id"] == m["conto_id"]
                     or r["conto_id"] not in conti_con_addebiti)]

    def cosa_fare(righe):
        cosa = []
        for cid in sorted({r["conto_id"] for r in righe}):
            c = ctx.conti[cid]
            cosa.append("la carta %s non ha il conto di addebito: indicalo in Conti e copertura" % c["nome"]
                        if c["tipo"] == "carta" else
                        "le righe della carta sono nel conto %s, che non e' di tipo Carta: annulla l'import del "
                        "file della carta e reimportalo in un conto di tipo Carta" % c["nome"])
        return "; ".join(cosa)

    def segna(m, ciclo, scelte, righe):
        for cid in scelte:
            prec[(m["conto_id"], cid)] = ciclo
            usate[(m["conto_id"], ciclo)].add(cid)
        usate_righe.update(r["id"] for r in righe)

    # 1. tratti esatti, prima al loro posto poi fuori posto
    in_attesa = []
    for m in addebiti:
        ciclo, dal = finestra(m)
        obiettivo = _effetto(m)
        libere = libere_di(m, ciclo)
        disponibili = {cid: [r for r in righe_carta[cid] if r["id"] not in usate_righe and dal < _giorno(r) <= ciclo]
                       for cid in libere}
        # due carte addebitate sullo stesso conto: ogni addebito va alla carta le cui righe lo fanno tornare;
        # se nessuna torna da sola si provano tutte insieme
        scelte, righe = libere, None
        for cid in libere:
            t = _tratto_esatto(disponibili[cid], obiettivo)
            if t:
                scelte, righe = [cid], t
                break
        if righe is None and len(libere) > 1:
            insieme = sorted((r for cid in libere for r in disponibili[cid]), key=lambda r: (_giorno(r), r["id"]))
            righe = _tratto_esatto(insieme, obiettivo)
        if righe:
            segna(m, ciclo, scelte, righe)
            esiti[m["id"]] = {"stato": "dettagliato", "righe": len(righe), "differenza": 0, "ciclo": ciclo,
                              "natura": "contenitore", "da_rivedere": False, "motivo": None}
            continue
        altrove = _tratto_esatto(altrove_di(m, ciclo, dal), obiettivo)
        if altrove:
            usate_righe.update(r["id"] for r in altrove)
            esiti[m["id"]] = {"stato": "righe_altrove", "righe": 0, "differenza": None, "ciclo": ciclo,
                              "natura": "contenitore", "da_rivedere": True,
                              "motivo": "Addebito della carta di credito non contato: " + cosa_fare(altrove)}
            continue
        in_attesa.append(m)

    # 2. e 3. senza tratto esatto: solo le righe che nessun addebito ha usato
    for m in in_attesa:
        ciclo, dal = finestra(m)
        obiettivo = _effetto(m)
        libere = libere_di(m, ciclo)
        inizio = max([prec[(m["conto_id"], cid)] for cid in libere if (m["conto_id"], cid) in prec]
                     or [(_d(ciclo) - timedelta(days=31)).isoformat()])
        tutte = [r for cid in libere for r in righe_carta[cid] if r["id"] not in usate_righe and dal < _giorno(r) <= ciclo]
        righe, altrove = _periodo_vicino(tutte, ciclo, inizio, obiettivo), False
        if not righe:
            righe, altrove = _periodo_vicino(altrove_di(m, ciclo, dal), ciclo, inizio, obiettivo), True
        if righe:
            segna(m, ciclo, [] if altrove else libere, righe)
            diff = sum(_effetto(r) for r in righe) - obiettivo
            # righe che spiegano meno dell'addebito (stesso segno, somma piu' piccola): il resto e' spesa non dettagliata
            residuo = -diff if diff and (diff > 0) == (obiettivo < 0) and abs(diff) < abs(obiettivo) else 0
            if residuo:
                motivo = ("Le righe della carta importate spiegano solo %.2f EUR dell'addebito di %.2f EUR: la "
                          "differenza (%.2f EUR) conta come spesa \"Varie\" finche' non importi il resto dell'estratto"
                          % (abs(obiettivo - residuo) / 100, abs(obiettivo) / 100, abs(residuo) / 100))
            else:
                motivo = "Le righe della carta non sommano all'addebito (%+.2f EUR)" % (diff / 100)
            if altrove:
                motivo += "; " + cosa_fare(righe)
            esiti[m["id"]] = {"stato": "differenza", "righe": 0 if altrove else len(righe), "differenza": diff,
                              "ciclo": ciclo, "natura": "contenitore", "da_rivedere": True, "motivo": motivo,
                              "residuo": residuo}
        else:
            motivo = "Addebito della carta di credito senza dettaglio: importa l'estratto della carta"
            if senza_conto and not libere:
                motivo = ("Addebito della carta di credito senza dettaglio: la carta %s non ha il conto di addebito, "
                          "indicalo in Conti e copertura" % ", ".join(ctx.conti[c]["nome"] for c in senza_conto))
            esiti[m["id"]] = {"stato": "senza_dettaglio", "righe": 0, "differenza": None, "ciclo": ciclo,
                              "natura": "uscita", "l2": "Varie", "da_rivedere": True, "motivo": motivo}
    ctx.righe_fuori_posto = {r["id"]: MOTIVO_RIGA_FUORI_POSTO % ctx.conti[r["conto_id"]]["nome"]
                             for r in nel_conto_sbagliato if r["id"] not in usate_righe}
    return esiti


# ---------------------------------------------------------------------------
# Ricalcolo
# ---------------------------------------------------------------------------
def ricalcola(conn):
    """Lettura, calcolo e scrittura stanno in una sola transazione di scrittura: un'altra scheda che importa o
    ricalcola nello stesso momento aspetta la fine, invece di vedersi sovrascrivere le analisi con una
    fotografia vecchia dei movimenti."""
    with transazione(conn, immediata=True):
        _ricalcola(conn)
    return riepilogo(conn)


def _ricalcola(conn):
    from .tassonomia_utente import completa_categorie
    completa_categorie(conn)
    tax = cfg.tassonomia()

    def come_cat(x):
        """Categoria come coppia: le sottocategorie richiamate per nome (tipi di movimento, codice dei
        trasferimenti) si risolvono sulla tassonomia in uso."""
        if not x:
            return None
        if isinstance(x, (tuple, list)):
            return tuple(x) if x[1] else None
        return tax.risolvi(x)
    tm = cfg.tipi_movimento()
    tipi, comp_l2 = tm["tipi"], tm["componenti"]
    from .paypal import riallinea_ricariche
    riallinea_ricariche(conn)              # ricariche PayPal importate come entrate prima della 0.16.1
    ctx = Contesto(conn)
    movs = [dict(r) for r in conn.execute("SELECT * FROM movimenti ORDER BY data, id")]
    tr = abbina_trasferimenti(ctx, movs)
    co = abbina_contenitori(ctx, movs)
    pp = abbina_paypal(ctx, movs)
    fuori_posto = getattr(ctx, "righe_fuori_posto", {})
    catena = Catena(conn)
    from .attributi import per_movimento, predefiniti
    attr_l2, attr_mov = predefiniti(conn), per_movimento(conn)
    parti = defaultdict(list)
    for r in conn.execute("SELECT mov_id, importo_cent, l1, l2 FROM ripartizioni ORDER BY mov_id, n"):
        parti[r["mov_id"]].append((r["importo_cent"], tax.risolvi(r["l2"], r["l1"])))
    righe = []

    def riga(m, componente, importo, natura, esito_cat, fonte, conf, rivedere, motivo, proposta, chiave_x=None):
        cat = come_cat(esito_cat)
        # natura e categoria coerenti qualunque sia la fonte della categoria (decisione, regola, conferma
        # dell'esercente, modello): vale la sezione della categoria. Un'uscita con importo positivo e' un rimborso
        natura = cfg.natura_da_categoria(cat, natura)
        if natura == "uscita" and importo > 0:
            natura = "rimborso"
        attributo = None
        if natura in ("uscita", "rimborso"):
            attributo = attr_mov.get(m["id"]) or (attr_l2.get(cat) if cat else None)
        righe.append((m["id"], componente, m["data"], m["data"][:7], m["conto_id"], ctx.conti[m["conto_id"]]["perimetro"],
                      importo, natura, cat[0] if cat else None, cat[1] if cat else None, fonte, conf,
                      1 if rivedere else 0, motivo, proposta, chiave_x, attributo))

    per_id = {m["id"]: m for m in movs}
    for m in movs:
        coperto = (pp.get(m["id"]) or {}).get("coperto")
        if coperto in per_id:
            # spesa (o rimborso) del report PayPal pagata da un addebito sul conto o sulla carta: conta una
            # volta sola, con la data dell'addebito (quella dell'estratto della banca) e sul conto che l'ha
            # pagata. Cosi' resta nel perimetro giusto (comune o personale) e nelle analisi del solo conto
            m = dict(m, data=per_id[coperto]["data"], conto_id=per_id[coperto]["conto_id"])
        t = tipi.get(m["tipo"], tipi["altro"])
        natura = t["natura"]
        l2_tipo = t.get("l2")
        for v in t.get("varianti", []) or []:
            if v["_re"].search(m["descrizione"] or ""):
                l2_tipo = v["l2"]
        rivedere, motivo = bool(t.get("revisione")), None
        residuo = 0
        chiave_x = m["chiave"]
        chiave_cat = chiave_x
        catena_on = bool(t.get("catena"))
        if natura == "trasferimento":
            e = tr.get(m["id"], {"natura": "trasferimento_interno"})
            natura, l2_tipo = e["natura"], e.get("l2")
            rivedere, motivo = e.get("da_rivedere", False), e.get("motivo")
            chiave_x = e.get("beneficiario") or chiave_x
            catena_on = False
            # per i bonifici a un familiare valgono solo decisioni, regole sulla causale e suddivisioni: lo
            # storico del beneficiario mescolerebbe motivi diversi (rata, assicurazione, restituzioni)
            chiave_cat = None if e.get("senza_storico") else chiave_x
        elif natura == "contenitore":
            e = co.get(m["id"], {"natura": "contenitore"})
            natura, l2_tipo = e["natura"], e.get("l2")
            rivedere, motivo = e.get("da_rivedere", False), e.get("motivo")
            residuo = e.get("residuo") or 0
        if m["id"] in fuori_posto:
            rivedere, motivo = True, fuori_posto[m["id"]]
        if m["id"] in pp:
            e = pp[m["id"]]
            if e.get("natura"):
                natura, l2_tipo, catena_on = e["natura"], None, False
                rivedere, motivo = False, None
            elif e.get("motivo"):
                rivedere, motivo = True, e["motivo"]
        if m.get("avviso") and not rivedere:
            rivedere, motivo = True, m["avviso"]
        if natura == "da_definire":
            natura = "uscita" if (m["importo_cent"] or 0) < 0 else "entrata"
            rivedere, motivo = True, "Tipo di movimento non riconosciuto (codice %s)" % (m["codice"] or "-")
        dec = catena.decisioni.get(m["id"])
        if dec:
            # una tua decisione sul movimento chiude la revisione chiesta da tipo o trasferimento; resta solo
            # quella della categoria, se la decisione non ne indica una
            rivedere, motivo = False, None
        if dec and dec[1]:
            # decisioni salvate prima della 0.12 potevano avere natura e categoria incoerenti: vale la categoria
            natura = cfg.natura_da_categoria(come_cat(dec[0]), dec[1]) if dec[0] else dec[1]
            if natura in ("trasferimento_interno", "escluso", "contenitore"):
                l2_tipo = None

        pz = parti.get(m["id"])
        if pz and sum(x[0] for x in pz) == m["importo_cent"] and natura not in ("trasferimento_interno", "escluso", "contenitore"):
            # movimento suddiviso: una riga per parte, con la natura della sezione della sua sottocategoria
            # (una parte in "Contributo ricevuto" su un'uscita e' una restituzione: entrata negativa)
            for importo, cat in pz:
                nat = {"uscite": "uscita", "entrate": "entrata", "risparmio": "risparmio"}.get(tax.sezione(cat), natura)
                riga(m, "parte", importo, nat, cat, "utente", 1.0, False, None, None, chiave_x)
        elif m["importo_cent"] not in (None, 0):
            if natura in ("uscita", "entrata", "rimborso", "risparmio"):
                cat_tipo = come_cat(l2_tipo)
                if natura == "risparmio" and cat_tipo:
                    es = catena.classifica(m, chiave_cat, False, cat_tipo)
                else:
                    es = catena.classifica(m, chiave_cat, catena_on, cat_tipo)
                if natura == "risparmio" and not es.cat:
                    es.cat = come_cat("Accantonamento" if m["importo_cent"] < 0 else "Rientro dal risparmio")
                riga(m, "principale", m["importo_cent"], natura, es.cat, es.fonte, es.confidenza,
                     rivedere or es.da_rivedere, motivo or es.motivo, es.proposta, chiave_x)
            else:
                riga(m, "principale", m["importo_cent"], natura, None, None, None, rivedere, motivo, None, chiave_x)
        if residuo and natura == "contenitore":
            # addebito della carta spiegato solo in parte dalle righe importate: il resto e' spesa non dettagliata
            es = catena.classifica(m, None, False, come_cat("Varie"))
            riga(m, "residuo", residuo, "uscita", es.cat, es.fonte, es.confidenza, True, motivo, None)
        if m["fee_cent"]:
            riga(m, "commissione", m["fee_cent"], "uscita", comp_l2["commissione"], "tipo movimento", 1.0, False, None, None)
        if m["tax_cent"]:
            l2 = l2_tipo if m["tipo"] == "imposte" and l2_tipo else comp_l2["imposta"]
            riga(m, "imposta", m["tax_cent"], "uscita", l2, "tipo movimento", 1.0, False, None, None)

    conn.execute("DELETE FROM analisi")
    conn.executemany("INSERT INTO analisi(mov_id, componente, data, mese, conto_id, perimetro, importo_cent, natura, l1, l2, "
                     "fonte, confidenza, da_rivedere, motivo, proposta, chiave, attributo) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", righe)
    conn.execute("DELETE FROM trasferimenti")
    conn.executemany("INSERT INTO trasferimenti VALUES (?,?,?,?,?,?)",
                     [(k, e.get("controparte") or e.get("proposta_abbinamento"), e["stato"], e.get("fiducia"),
                       e.get("conto_dest"), e.get("beneficiario")) for k, e in tr.items()])
    conn.execute("DELETE FROM contenitori")
    conn.executemany("INSERT INTO contenitori VALUES (?,?,?,?,?)",
                     [(k, e["stato"], e["righe"], e["differenza"], e["ciclo"]) for k, e in co.items()])


def riepilogo(conn):
    r = {}
    r["righe"] = conn.execute("SELECT count(*) FROM analisi").fetchone()[0]
    r["da_rivedere"] = conn.execute("SELECT count(DISTINCT mov_id) FROM analisi WHERE da_rivedere = 1").fetchone()[0]
    r["trasferimenti"] = dict(conn.execute("SELECT stato, count(*) FROM trasferimenti GROUP BY stato").fetchall())
    r["contenitori"] = dict(conn.execute("SELECT stato, count(*) FROM contenitori GROUP BY stato").fetchall())
    r["fonti"] = dict(conn.execute("SELECT coalesce(fonte, 'nessuna'), count(*) FROM analisi WHERE natura IN "
                                   "('uscita','entrata','rimborso') GROUP BY 1").fetchall())
    return r
