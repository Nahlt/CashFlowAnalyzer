"""Portafoglio titoli (sezione facoltativa): posizioni, costo medio e plusvalenze realizzate dalle righe gia' importate.

Si usano solo i movimenti con ISIN (oggi Trade Republic; vale per ogni conto che li riporta):
- acquisto: + quote; il costo cresce dell'importo pagato piu' la commissione (oneri accessori nel costo);
- vendita: - quote; costo delle quote vendute = costo medio x quote; plusvalenza = ricavo al netto della
  commissione di vendita meno quel costo. Il costo medio delle quote rimaste non cambia;
- migrazione: righe di quote senza soldi. Le coppie che si annullano lo stesso giorno (cambio di ISIN interno) non
  contano; un ingresso netto di quote ha "costo da indicare";
- dividendo: rendita del titolo, con la ritenuta; la quantita' indicata dalla banca serve da controllo.
Criterio: costo medio ponderato, come nel regime amministrato. Le imposte sono quelle trattenute dalla banca: nessun
calcolo fiscale. I prezzi di mercato (facoltativi, modulo mercato) servono solo per valore e rendimenti delle
posizioni aperte: il calcolo del costo e delle plusvalenze realizzate non li usa.

Le posizioni di partenza (storico che inizia dopo il primo acquisto) si indicano a mano: impostazione
"portafoglio_partenze" = {isin: {"data", "quote", "costo_cent"}}.
"""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from .db import leggi_impostazione, scrivi_impostazione

CHIAVE_PARTENZE = "portafoglio_partenze"
CHIAVE_ATTIVA = "funzione_portafoglio"
ZERO = Decimal(0)
TOLLERANZA = Decimal("0.000001")
TIPI = ("acquisto_titoli", "vendita_titoli", "movimento_titoli", "dividendo")


def attiva(conn):
    return bool(leggi_impostazione(conn, CHIAVE_ATTIVA, True))


def partenze(conn):
    return leggi_impostazione(conn, CHIAVE_PARTENZE, {}) or {}


def imposta_partenza(conn, isin, data, quote, costo_cent, conto_id=None):
    """Posizione gia' presente prima dei movimenti importati. quote <= 0 la toglie. Con la stessa data di una
    migrazione (quote arrivate senza costo) ne indica il costo: quelle quote non si sommano due volte.
    conto_id: il conto a cui appartiene (senza, il conto in cui il titolo compare per primo)."""
    p = partenze(conn)
    q = Decimal(str(quote))
    if q <= 0:
        p.pop(isin, None)
    else:
        p[isin] = {"data": data, "quote": str(q), "costo_cent": int(costo_cent)}
        if conto_id is not None:
            p[isin]["conto_id"] = int(conto_id)
    scrivi_impostazione(conn, CHIAVE_PARTENZE, p)


def _q(v):
    try:
        return Decimal(str(v)) if v not in (None, "") else ZERO
    except Exception:
        return ZERO


_ORDINE_TIPO = {"partenza": 0, "movimento_titoli": 1, "acquisto_titoli": 2, "vendita_titoli": 3, "dividendo": 4}


def _eventi(conn, conti=None):
    filtro, par = "", []
    if conti:
        filtro = " AND m.conto_id IN (%s)" % ",".join("?" * len(conti))
        par = list(conti)
    righe = [dict(r) for r in conn.execute(
        "SELECT m.id, m.data, m.ora, m.tipo, m.isin, m.nome_titolo, m.quantita, m.importo_cent, m.fee_cent, m.tax_cent, "
        "m.conto_id FROM movimenti m WHERE m.isin IS NOT NULL AND m.tipo IN (%s)%s ORDER BY m.data, m.id"
        % (",".join("?" * len(TIPI)), filtro), list(TIPI) + par)]
    # migrazioni: si sommano per conto, titolo e giorno; le coppie che si annullano non contano
    netto = defaultdict(lambda: ZERO)
    for r in righe:
        if r["tipo"] == "movimento_titoli":
            netto[(r["conto_id"], r["isin"], r["data"])] += _q(r["quantita"])
    out, viste = [], set()
    for r in righe:
        if r["tipo"] == "movimento_titoli":
            k = (r["conto_id"], r["isin"], r["data"])
            if k in viste or abs(netto[k]) <= TOLLERANZA:
                continue
            viste.add(k)
            r = dict(r, quantita=str(netto[k]))
        out.append(r)
    return out


def _ordina(eventi):
    """Ordine in cui gli eventi sono avvenuti. Nello stesso giorno vale l'ora dell'operazione; se a qualche
    evento di quel titolo manca, prima gli ingressi (partenza, migrazioni, acquisti) e poi le vendite: l'id del
    movimento non dice nulla dell'ordine, e una vendita elaborata prima del suo acquisto lascia quote aperte."""
    con_ora = defaultdict(lambda: True)
    for e in eventi:
        if e["tipo"] != "partenza" and not e.get("ora"):
            con_ora[(e["isin"], e["data"])] = False

    def chiave(e):
        if e["tipo"] == "partenza":
            return (e["data"], 0, "", 0, "")
        ora = e.get("ora") or ""
        if con_ora[(e["isin"], e["data"])]:
            return (e["data"], 1, ora, _ORDINE_TIPO[e["tipo"]], str(e["id"]))
        return (e["data"], 1, "", _ORDINE_TIPO[e["tipo"]], str(e["id"]))
    return sorted(eventi, key=chiave)


def calcola(conn, conti=None, da=None, a=None):
    """Posizioni alla data `a` (tutto lo storico precedente serve per il costo medio), vendite e riepilogo annuale
    nel periodo [da, a]. Quote e costo medio si ricostruiscono per conto e titolo (ogni deposito ha il suo
    costo medio: la vendita delle quote di un conto non cambia con i conti scelti nei filtri) e si sommano per
    titolo solo alla fine."""
    nomi = {}
    pos = defaultdict(lambda: {"quote": ZERO, "costo": ZERO, "primo": None, "dividendi": 0, "rit_dividendi": 0,
                               "commissioni": 0, "senza_costo": ZERO, "avvisi": [], "avvisi_migrazione": [],
                               "partenza_libera": None, "realizzato": ZERO, "costo_venduto": ZERO,
                               "vendite_incomplete": False, "vendite": 0})
    vendite, anni = [], defaultdict(lambda: defaultdict(int))
    mesi = {}                       # mese -> capitale investito netto (costo delle posizioni aperte) a fine mese
    storia = defaultdict(list)      # (conto, isin) -> [(data, quote)] per il controllo dei dividendi
    passi = defaultdict(list)       # isin -> [(data, quote, costo_cent)] dopo ogni evento, per i grafici
    eventi = _eventi(conn, conti)
    primo_conto = {}
    for e in sorted(eventi, key=lambda e: (e["data"], str(e["id"]))):
        primo_conto.setdefault(e["isin"], e["conto_id"])
    for isin, p in partenze(conn).items():
        if conti and isin not in primo_conto:
            continue                    # titolo di un conto non scelto nei filtri
        # la posizione di partenza appartiene a un conto: quello indicato o, se non c'e', il conto in cui il
        # titolo compare per primo
        conto = p.get("conto_id")
        if conto is None or (conti and conto not in conti):
            conto = primo_conto.get(isin)
        eventi.append({"id": "partenza", "data": p["data"], "tipo": "partenza", "isin": isin, "nome_titolo": None,
                       "quantita": p["quote"], "importo_cent": -int(p["costo_cent"]), "fee_cent": 0, "tax_cent": 0,
                       "conto_id": conto, "ora": None})
    eventi = _ordina(eventi)

    def nel_periodo(d):
        return (not da or d >= da) and (not a or d <= a)

    for e in eventi:
        if a and e["data"] > a:
            break
        isin = e["isin"]
        k = (e["conto_id"], isin)
        if e.get("nome_titolo"):
            nomi[isin] = e["nome_titolo"]
        p = pos[k]
        storia[k].append((e["data"], p["quote"]))             # quote prima dell'evento
        q = _q(e["quantita"])
        imp, fee, tax = e["importo_cent"] or 0, e["fee_cent"] or 0, e["tax_cent"] or 0
        anno = anni[e["data"][:4]] if nel_periodo(e["data"]) else defaultdict(int)
        if e["tipo"] == "partenza":
            # il costo indicato a mano vale prima di tutto per le quote gia' arrivate senza costo (migrazione):
            # quelle non si aggiungono una seconda volta
            coperte = min(q, p["senza_costo"])
            p["senza_costo"] -= coperte
            p["quote"] += q - coperte
            p["costo"] += Decimal(-imp)
            p["primo"] = p["primo"] or e["data"]
            p["partenza_libera"] = {"data": e["data"], "quote": q - coperte}
            if p["senza_costo"] <= TOLLERANZA:
                p["senza_costo"], p["avvisi_migrazione"] = ZERO, []
        elif e["tipo"] == "acquisto_titoli":
            p["quote"] += q
            p["costo"] += Decimal(-imp - fee)
            p["commissioni"] += -fee
            p["primo"] = p["primo"] or e["data"]
            anno["versato"] += -imp - fee
            anno["commissioni"] += -fee
        elif e["tipo"] == "vendita_titoli":
            vendute = -q if q < 0 else q
            disponibili = p["quote"]
            incompleto = vendute > disponibili + TOLLERANZA or p["senza_costo"] > TOLLERANZA
            base = min(vendute, disponibili) if disponibili > 0 else ZERO
            costo_v = (p["costo"] * base / disponibili) if disponibili > 0 else ZERO
            ricavo = imp + fee
            plus = Decimal(ricavo) - costo_v
            if vendute > disponibili + TOLLERANZA:
                p["avvisi"].append("%s: vendute %s quote, ne risultavano %s (storico incompleto: indica la posizione "
                                   "di partenza)" % (e["data"], _fmt(vendute), _fmt(disponibili)))
            p["quote"] = max(ZERO, disponibili - vendute)
            p["costo"] -= costo_v
            # risultato delle vendite su tutta la storia fino alla fine del periodo (rendimento complessivo)
            p["vendite"] += 1
            p["costo_venduto"] += costo_v
            p["realizzato"] += plus
            p["vendite_incomplete"] = p["vendite_incomplete"] or incompleto
            if p["quote"] <= TOLLERANZA:
                p["quote"], p["costo"], p["senza_costo"], p["avvisi_migrazione"] = ZERO, ZERO, ZERO, []
            p["commissioni"] += -fee
            if nel_periodo(e["data"]):
                vendite.append({"data": e["data"], "isin": isin, "quote": vendute, "ricavo": ricavo,
                                "costo": _cent(costo_v), "plus": None if incompleto else _cent(plus),
                                "imposte": -tax, "commissioni": -fee, "incompleto": incompleto,
                                "conto_id": e["conto_id"]})
                anno["disinvestito"] += ricavo
                anno["commissioni"] += -fee
                anno["imposte"] += -tax
                if not incompleto:
                    anno["plus" if plus >= 0 else "minus"] += _cent(plus)
        elif e["tipo"] == "movimento_titoli":
            if q > 0:
                # quote arrivate senza costo. Se per questo titolo c'e' una posizione di partenza con la stessa
                # data, sono proprio queste: non si sommano una seconda volta
                libera = p["partenza_libera"]
                if libera and libera["data"] == e["data"] and libera["quote"] > TOLLERANZA:
                    assorbite = min(q, libera["quote"])
                    libera["quote"] -= assorbite
                    q -= assorbite
                if q > TOLLERANZA:
                    p["quote"] += q
                    p["senza_costo"] += q
                    p["avvisi_migrazione"].append(
                        "%s: entrate %s quote senza un costo (migrazione): indica la posizione di partenza con il "
                        "costo, con questa data" % (e["data"], _fmt(q)))
            elif p["quote"] > 0:
                uscite = min(-q, p["quote"])
                p["costo"] -= p["costo"] * uscite / p["quote"]
                p["quote"] -= uscite
        elif e["tipo"] == "dividendo":
            p["dividendi"] += imp
            p["rit_dividendi"] += -tax
            if nel_periodo(e["data"]):
                anno["dividendi"] += imp
                anno["imposte"] += -tax
            # il dividendo spetta a chi aveva le quote alla data di stacco, qualche giorno o settimana prima del
            # pagamento: va bene qualunque quantita' tenuta negli ultimi 60 giorni
            recenti = [x for d, x in storia[k] if d >= _giorni_prima(e["data"], 60)] + [p["quote"]]
            if q and all(abs(q - x) > Decimal("0.0001") for x in recenti):
                p["avvisi"].append("%s: il dividendo e' calcolato su %s quote, l'app ne ricostruisce %s"
                                   % (e["data"], _fmt(q), _fmt(p["quote"])))
        storia[k].append((e["data"], p["quote"]))
        del_titolo = [x for (_, i2), x in pos.items() if i2 == isin]
        passo = (e["data"], sum((x["quote"] for x in del_titolo), ZERO), _cent(sum((x["costo"] for x in del_titolo), ZERO)))
        if passi[isin] and passi[isin][-1][0] == e["data"]:
            passi[isin][-1] = passo
        else:
            passi[isin].append(passo)
        mesi[e["data"][:7]] = sum((x["costo"] for x in pos.values()), ZERO)
    # somma per titolo delle posizioni dei singoli conti
    per_titolo = {}
    for (_, isin), p in pos.items():
        t = per_titolo.setdefault(isin, {"quote": ZERO, "costo": ZERO, "primo": None, "dividendi": 0, "rit_dividendi": 0,
                                         "commissioni": 0, "senza_costo": ZERO, "avvisi": [], "realizzato": ZERO,
                                         "costo_venduto": ZERO, "vendite_incomplete": False, "vendite": 0})
        aperta = p["quote"] > TOLLERANZA
        t["quote"] += p["quote"] if aperta else ZERO
        t["costo"] += p["costo"] if aperta else ZERO
        t["senza_costo"] += p["senza_costo"] if aperta else ZERO
        t["primo"] = min(x for x in (t["primo"], p["primo"]) if x) if (t["primo"] or p["primo"]) else None
        for campo in ("dividendi", "rit_dividendi", "commissioni", "realizzato", "costo_venduto", "vendite"):
            t[campo] += p[campo]
        t["vendite_incomplete"] = t["vendite_incomplete"] or p["vendite_incomplete"]
        t["avvisi"] += p["avvisi"] + p["avvisi_migrazione"]
    posizioni = []
    for isin, p in per_titolo.items():
        aperta = p["quote"] > TOLLERANZA
        posizioni.append({
            "isin": isin, "titolo": nomi.get(isin, isin), "quote": p["quote"] if aperta else ZERO,
            "costo": _cent(p["costo"]) if aperta else 0,
            "costo_medio": (p["costo"] / p["quote"] / 100) if aperta and p["quote"] > p["senza_costo"] else None,
            "primo": p["primo"], "dividendi": p["dividendi"], "rit_dividendi": p["rit_dividendi"],
            "commissioni": p["commissioni"], "costo_da_indicare": p["senza_costo"] > TOLLERANZA,
            "aperta": aperta, "avvisi": sorted(p["avvisi"]),
            # tutta la storia fino alla fine del periodo: plus/minus delle vendite (None se una vendita ha il costo
            # incompleto), costo delle quote vendute, numero di vendite, giorno in cui la posizione si e' chiusa
            "realizzato": None if p["vendite_incomplete"] else _cent(p["realizzato"]),
            "costo_venduto": _cent(p["costo_venduto"]), "vendite_totali": p["vendite"],
            "chiusa_il": None if aperta else _chiusura(passi.get(isin))})
    for v in vendite:
        v["titolo"] = nomi.get(v["isin"], v["isin"])
    # prima le aperte (per capitale investito), poi le chiuse dalla piu' recente
    posizioni.sort(key=lambda x: (not x["aperta"], -x["costo"]))
    chiuse = sorted((p for p in posizioni if not p["aperta"]), key=lambda x: x["chiusa_il"] or "", reverse=True)
    posizioni = [p for p in posizioni if p["aperta"]] + chiuse
    return {"posizioni": posizioni, "vendite": vendite,
            "anni": {k: dict(v) for k, v in sorted(anni.items())},
            "mesi": sorted((m, _cent(v)) for m, v in mesi.items()),
            "passi": dict(passi)}


def _cent(v):
    """Centesimi interi da un valore Decimal, con arrotondamento commerciale (mezzo centesimo per eccesso)."""
    return int(Decimal(v).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _valore_cent(quote, prezzo):
    """Quote x prezzo in centesimi interi, tutto in decimale: nessun passaggio in virgola mobile."""
    return _cent(Decimal(str(quote)) * Decimal(str(prezzo)) * 100)


def _chiusura(passi):
    """Giorno in cui la posizione e' scesa a zero quote l'ultima volta (None se e' aperta o non ci sono passi). Un
    titolo che non ha mai avuto quote (solo una vendita, storico incompleto) si chiude al suo ultimo evento."""
    if not passi or passi[-1][1] > TOLLERANZA:
        return None
    chiusa, prima = None, ZERO
    for d, q, _ in passi:
        if q <= TOLLERANZA and prima > TOLLERANZA:
            chiusa = d
        prima = q
    return chiusa or passi[-1][0]


def valorizza(posizioni, prezzi_isin, giorno):
    """Aggiunge a ogni posizione aperta prezzo, data del prezzo, valore, plus/minus latente e rendimento, con
    l'ultimo prezzo non oltre `giorno`. prezzi_isin: {isin: [(data, prezzo €)]}. A tutte, aperte e chiuse, aggiunge il
    rendimento complessivo (vedi `complessivo`)."""
    from .mercato import prezzo_al
    for p in posizioni:
        pr = prezzo_al(prezzi_isin.get(p["isin"]), giorno) if p["aperta"] else None
        p["prezzo"], p["data_prezzo"] = (float(pr[1]), pr[0]) if pr else (None, None)     # float solo per mostrarlo
        p["valore"] = _valore_cent(p["quote"], pr[1]) if pr else None
        affidabile = pr and not p["costo_da_indicare"] and p["costo"] > 0
        p["latente"] = p["valore"] - p["costo"] if affidabile else None
        p["rendimento"] = p["latente"] / p["costo"] if affidabile else None
        p["complessivo"], p["base_complessivo"] = complessivo(p)
        p["rendimento_complessivo"] = (p["complessivo"] / p["base_complessivo"]
                                       if p["complessivo"] is not None and p["base_complessivo"] > 0 else None)
    return posizioni


def complessivo(p):
    """Rendimento complessivo di un titolo in centesimi, con la sua base: (plus/minus latente della parte aperta +
    plus/minus realizzate + dividendi lordi, costo delle quote tenute + costo delle quote vendute). Tutto prima delle
    imposte. (None, 0) se non si puo' calcolare: parte aperta senza prezzo o senza costo, vendite con il costo
    incompleto. Esempio: 10 quote comprate a 100 €, 5 vendute a 120 € (+100 €), le 5 rimaste valgono 110 € (+50 €):
    (15000, 100000), cioe' +15%."""
    realizzato = p.get("realizzato", 0)
    if realizzato is None:
        return None, 0
    if p["aperta"]:
        if p.get("latente") is None:
            return None, 0
        aperta, base_aperta = p["latente"], p["costo"]
    else:
        aperta, base_aperta = 0, 0
    return aperta + realizzato + p.get("dividendi", 0), base_aperta + p.get("costo_venduto", 0)


def totali(posizioni):
    """Indicatori delle posizioni mostrate. Capitale e valore solo delle aperte (quelle senza prezzo contano al
    costo); rendimento delle aperte sulle posizioni con un prezzo; rendimento complessivo su tutte quelle per cui si
    puo' calcolare (aperte e chiuse). Importi in centesimi, rendimenti come frazione (0,15 = +15%)."""
    aperte = [p for p in posizioni if p["aperta"]]
    con_latente = [p for p in aperte if p.get("latente") is not None]
    con_compl = [p for p in posizioni if p.get("complessivo") is not None]
    latente = sum(p["latente"] for p in con_latente)
    base_latente = sum(p["costo"] for p in con_latente)
    compl = sum(p["complessivo"] for p in con_compl)
    base_compl = sum(p["base_complessivo"] for p in con_compl)
    valutate = [p for p in aperte if p.get("valore") is not None]
    return {
        "capitale": sum(p["costo"] for p in aperte),
        "valore": (sum(p["valore"] for p in valutate) + sum(p["costo"] for p in aperte if p.get("valore") is None))
        if valutate else None,
        "senza_prezzo": len(aperte) - len(valutate),
        "latente": latente if con_latente else None,
        "rendimento_aperte": latente / base_latente if base_latente > 0 else None,
        "complessivo": compl if con_compl else None,
        "rendimento_complessivo": compl / base_compl if base_compl > 0 else None,
        "esclusi_complessivo": len(posizioni) - len(con_compl)}


def chiuse_prima(posizioni, da):
    """Posizioni vendute del tutto prima dell'inizio del periodo (nell'analisi per periodo non si mostrano, salvo
    richiesta). Quelle vendute dopo la fine del periodo non sono tra queste: alla fine del periodo erano aperte."""
    if not da:
        return []
    return [p for p in posizioni if not p["aperta"] and (p.get("chiusa_il") or "") < da]


def stato(p):
    """Testo della colonna Stato: aperta, venduta in parte, chiusa il gg/mm/aaaa."""
    if p["aperta"]:
        return "venduta in parte" if p.get("vendite_totali") else "aperta"
    d = p.get("chiusa_il")
    return "chiusa il %s/%s/%s" % (d[8:10], d[5:7], d[:4]) if d else "chiusa"


def prezzo_vecchio(p, giorno, giorni=5):
    """La posizione aperta ha un prezzo piu' vecchio di `giorni` giorni rispetto a `giorno` (iso)?"""
    d = p.get("data_prezzo")
    return bool(p["aperta"] and d and d < _giorni_prima(giorno, giorni))


def andamento(passi, prezzi=None, da=None, a=None, interrompi=False):
    """Serie giornaliera di una posizione: [(data, quote, costo_cent, valore_cent o None)].

    Con i prezzi: un punto per ogni giorno di borsa (la posizione e' quella dopo gli eventi del giorno). Senza: un
    punto per ogni evento. Si parte dal primo evento o da `da`, e si arriva ad `a` (o all'ultimo prezzo).

    interrompi (grafico di una posizione): la linea si ferma alla vendita di tutte le quote invece di proseguire a
    zero. Il giorno della chiusura vale le quote tenute fino a quel giorno al prezzo del giorno; se il titolo si
    ricompra, il tratto senza quote e' un buco (un punto con costo e valore None)."""
    if not passi:
        return []
    giorni = [d for d, _ in prezzi] if prezzi else [d for d, _, _ in passi]
    inizio = max(passi[0][0], da or passi[0][0])
    fine = a or (giorni[-1] if giorni else passi[-1][0])
    if not prezzi:
        giorni = sorted(set(giorni) | ({inizio} if inizio > passi[0][0] else set()) | {fine})
    valori = dict(prezzi or [])
    out, i, stato, ultimo_prezzo = [], 0, None, None
    chiusa = False                  # con interrompi: gia' messo il punto del giorno della vendita
    for d, v in sorted(valori.items()):
        if d < inizio:
            ultimo_prezzo = v
    for g in giorni:
        while i < len(passi) and passi[i][0] <= g:
            stato = passi[i]
            i += 1
        if g < inizio or g > fine or stato is None:
            continue
        ultimo_prezzo = valori.get(g, ultimo_prezzo)
        q, costo = stato[1], stato[2]
        if interrompi and q <= TOLLERANZA:
            if out and out[-1][2] is not None:
                if not chiusa:      # giorno della vendita: le quote tenute fino a ieri, al prezzo di oggi
                    pq, pc = out[-1][1], out[-1][2]
                    out.append((g, pq, pc, _valore_cent(pq, ultimo_prezzo)
                                if prezzi and ultimo_prezzo is not None else None))
                    chiusa = True
                else:               # tratto senza quote: un punto vuoto (buco nella linea)
                    out.append((g, ZERO, None, None))
            continue
        chiusa = False
        valore = _valore_cent(q, ultimo_prezzo) if prezzi and ultimo_prezzo is not None else None
        out.append((g, q, costo, valore))
    while out and out[-1][2] is None:                   # niente punti vuoti in coda
        out.pop()
    return out


def _giorni_prima(data, giorni):
    from datetime import date, timedelta
    return (date.fromisoformat(data) - timedelta(days=giorni)).isoformat()


def _fmt(q):
    s = format(q.normalize(), "f") if isinstance(q, Decimal) else str(q)
    return s.replace(".", ",")
