"""Budget mensili per categoria o sottocategoria: speso del mese, previsione a fine mese, stato e storico.

Ogni budget e' la terna (categoria, sottocategoria facoltativa, perimetro) con un importo valido da un mese in
poi; un cambio d'importo vale dal suo mese, i mesi prima restano valutati con l'importo di allora.

Regole del conteggio (diverse da "Dove vanno i soldi" solo per i rimborsi):
- speso = uscite della categoria nel perimetro del budget; "Da classificare" non entra in nessun budget;
- un rimborso di un esercente conta nel mese (e nel giorno) della spesa che restituisce: stesso esercente, stessa
  categoria, spesa fino a GIORNI_RIMBORSO giorni prima e non piu' piccola del rimborso. Se la spesa non si trova,
  il rimborso non conta e si elenca a parte. I rimborsi da persone (bonifici classificati con una categoria di
  spesa) contano nel giorno in cui arrivano;
- previsione, conto per conto: speso fino al giorno in cui arrivano i dati del conto (la copertura dei suoi
  import) piu' la mediana, sugli ultimi MESI_STORIA mesi interi coperti, della spesa del conto dal giorno dopo
  fino a fine mese. Una spesa fatta prima del solito si conta due volte (gia' spesa e ancora nella mediana):
  limite accettato.
"""
import statistics
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta

from . import configurazione as cfg
from .analisi import Filtri
from .importa import FONTI_COPERTURA, coperture

PERIMETRI_BUDGET = {"tutti": "Tutti", "comune": "Comune", "personale": "Personale"}
MESI_STORIA = 12
GIORNI_RIMBORSO = 90
GIORNI_AVVISO_COPERTURA = 7          # un conto fermo da piu' giorni di cosi' rispetto a oggi va aggiornato

IN_LINEA, A_RISCHIO, SUPERATO = "in_linea", "a_rischio", "superato"


# ---------------------------------------------------------------- mesi

def mese_di(d):
    return (d.isoformat() if isinstance(d, date) else str(d))[:7]


def sposta_mese(mese, n):
    a, m = int(mese[:4]), int(mese[5:7])
    t = a * 12 + (m - 1) + n
    return "%04d-%02d" % (t // 12, t % 12 + 1)


def giorni_del_mese(mese):
    return monthrange(int(mese[:4]), int(mese[5:7]))[1]


def primo_giorno(mese):
    return date(int(mese[:4]), int(mese[5:7]), 1)


def ultimo_giorno(mese):
    return date(int(mese[:4]), int(mese[5:7]), giorni_del_mese(mese))


def valida_mese(mese):
    if not isinstance(mese, str) or len(mese) != 7 or mese[4] != "-":
        raise ValueError("il mese va indicato come AAAA-MM")
    primo_giorno(mese)
    return mese


# ---------------------------------------------------------------- budget salvati

def categorie_di_spesa():
    """{categoria: [sottocategorie]} della sezione uscite, nell'ordine della tassonomia: le sole che si possono
    mettere a budget."""
    tax = cfg.tassonomia()
    out = {}
    for l1, l2 in tax.per_sezione("uscite"):
        out.setdefault(l1, []).append(l2)
    return out


def valida_voce(l1, l2=None):
    """(l1, l2) di un budget: la categoria della sezione uscite, la sottocategoria (facoltativa) sotto di lei."""
    cat = categorie_di_spesa()
    if not l1 or l1 not in cat:
        raise ValueError("scegli una categoria di spesa")
    if l2 and l2 not in cat[l1]:
        raise ValueError("\"%s\" non e' una sottocategoria di %s" % (l2, l1))
    return l1, (l2 or None)


def elenco(conn):
    """I budget salvati: [{l1, l2, perimetro, importi: [(dal_mese, importo_cent)] in ordine di mese}], nell'ordine
    dei perimetri, poi della tassonomia (la categoria prima delle sue sottocategorie)."""
    tax = cfg.tassonomia()
    pos_l1 = {l1: i for i, l1 in enumerate(tax.ordine_l1)}
    pos_cat = {c: i for i, c in enumerate(tax.voci)}
    per = {}
    for r in conn.execute("SELECT l1, l2, perimetro, dal_mese, importo_cent FROM budget ORDER BY dal_mese"):
        k = (r["l1"], r["l2"], r["perimetro"])
        per.setdefault(k, []).append((r["dal_mese"], r["importo_cent"]))
    ordine_per = list(PERIMETRI_BUDGET)
    chiavi = sorted(per, key=lambda k: (ordine_per.index(k[2]), pos_l1.get(k[0], 999), k[1] is not None,
                                         pos_cat.get((k[0], k[1]), 999), k[1] or ""))
    return [{"l1": k[0], "l2": k[1], "perimetro": k[2], "importi": per[k]} for k in chiavi]


def importo_nel_mese(b, mese):
    """Importo valido nel mese (l'ultimo cambio fino a quel mese), None se il budget parte dopo."""
    valido = None
    for dal, cent in b["importi"]:
        if dal <= mese:
            valido = cent
    return valido


def nome_voce(l1, l2=None):
    nome = l1.capitalize() if l1 and l1.isupper() else l1
    return "%s › %s" % (nome, l2) if l2 else nome


# ---------------------------------------------------------------- spese che contano per i budget

def voci_di_spesa(conn, perimetro, da, a):
    """Spese del perimetro con data effettiva tra da e a (giorni ISO), per i budget. Restituisce (voci,
    rimborsi_non_attribuiti). voce: {data, conto_id, l1, l2, spesa (centesimi, positiva per una spesa,
    negativa per un rimborso), mov_id, rimborso_del (giorno del rimborso se spostato sulla spesa)}.
    I rimborsi non attribuiti sono quelli arrivati tra da e a."""
    inizio = (date.fromisoformat(da) - timedelta(days=GIORNI_RIMBORSO)).isoformat()
    w, par = Filtri(perimetro=perimetro, da=inizio, a=a).where("a")
    righe = conn.execute(
        "SELECT a.mov_id, a.data, a.conto_id, a.l1, a.l2, a.importo_cent, a.natura, a.chiave, "
        "coalesce(m.esercente, m.controparte_nome, m.descrizione) AS nome, "
        "EXISTS (SELECT 1 FROM trasferimenti t WHERE t.mov_id = a.mov_id) AS da_persona "
        "FROM analisi a LEFT JOIN movimenti m ON m.id = a.mov_id "
        "WHERE %s AND a.natura IN ('uscita', 'rimborso') AND a.l1 IS NOT NULL ORDER BY a.data, a.id" % w,
        par).fetchall()
    spese = [r for r in righe if r["importo_cent"] < 0]
    residuo = {id(r): -r["importo_cent"] for r in spese}
    per_voce = defaultdict(list)
    for r in spese:
        if r["chiave"]:
            per_voce[(r["chiave"], r["l1"], r["l2"])].append(r)
    voci, non_attribuiti = [], []
    for r in righe:
        if r["importo_cent"] < 0:
            voci.append({"data": r["data"], "conto_id": r["conto_id"], "l1": r["l1"], "l2": r["l2"],
                         "spesa": -r["importo_cent"], "mov_id": r["mov_id"], "rimborso_del": None})
            continue
        rimborso = r["importo_cent"]
        if r["da_persona"]:
            voci.append({"data": r["data"], "conto_id": r["conto_id"], "l1": r["l1"], "l2": r["l2"],
                         "spesa": -rimborso, "mov_id": r["mov_id"], "rimborso_del": None})
            continue
        limite = (date.fromisoformat(r["data"]) - timedelta(days=GIORNI_RIMBORSO)).isoformat()
        trovata = None
        for s in reversed(per_voce.get((r["chiave"], r["l1"], r["l2"]), []) if r["chiave"] else []):
            if s["data"] > r["data"]:
                continue
            if s["data"] < limite:
                break
            if residuo[id(s)] >= rimborso:
                trovata = s
                break
        if trovata is None:
            if r["data"] >= da:
                non_attribuiti.append({"data": r["data"], "conto_id": r["conto_id"], "l1": r["l1"], "l2": r["l2"],
                                       "importo": rimborso, "nome": r["nome"], "mov_id": r["mov_id"]})
            continue
        residuo[id(trovata)] -= rimborso
        voci.append({"data": trovata["data"], "conto_id": r["conto_id"], "l1": r["l1"], "l2": r["l2"],
                     "spesa": -rimborso, "mov_id": r["mov_id"], "rimborso_del": r["data"]})
    voci = [v for v in voci if da <= v["data"] <= a]
    voci.sort(key=lambda v: v["data"])
    return voci, non_attribuiti


def da_classificare(conn, perimetro, mese):
    """Uscite del mese senza categoria (al netto dei rimborsi), che non entrano in nessun budget."""
    w, par = Filtri(perimetro=perimetro, da=primo_giorno(mese).isoformat(), a=ultimo_giorno(mese).isoformat()).where("a")
    r = conn.execute("SELECT coalesce(-sum(a.importo_cent), 0), count(*) FROM analisi a WHERE %s "
                     "AND a.natura IN ('uscita', 'rimborso') AND a.l1 IS NULL" % w, par).fetchone()
    return int(r[0]), int(r[1])


# ---------------------------------------------------------------- copertura dei conti

def copertura_conti(conn, perimetri, oggi):
    """Conti che contano per i budget dei perimetri indicati (importati, non chiusi, conti e carte) con il
    giorno fin dove arrivano i loro dati: [{conto_id, nome, perimetro, giorno, fonte, fonte_testo, indietro}],
    indietro = giorni di ritardo rispetto a oggi, se piu' di GIORNI_AVVISO_COPERTURA."""
    cop = coperture(conn)
    perimetri = set(perimetri)
    out = []
    for c in conn.execute("SELECT id, nome, perimetro FROM conti WHERE importato = 1 AND chiuso_il IS NULL "
                          "AND tipo IN ('conto', 'carta') ORDER BY perimetro, nome"):
        if "tutti" not in perimetri and c["perimetro"] not in perimetri:
            continue
        giorno, fonte = cop.get(c["id"], (None, None))
        ritardo = (oggi - date.fromisoformat(giorno)).days if giorno else None
        out.append({"conto_id": c["id"], "nome": c["nome"], "perimetro": c["perimetro"], "giorno": giorno,
                    "fonte": fonte, "fonte_testo": FONTI_COPERTURA.get(fonte, "nessun import"),
                    "indietro": ritardo if ritardo is not None and ritardo > GIORNI_AVVISO_COPERTURA else None})
    return out


def _inizio_conti(conn):
    """Primo mese con movimenti di ogni conto: i mesi prima non sono storia (la spesa zero non e' reale)."""
    return {r[0]: r[1][:7] for r in conn.execute("SELECT conto_id, min(data) FROM movimenti GROUP BY conto_id")
            if r[1]}


# ---------------------------------------------------------------- calcolo di un budget in un mese

def _della_voce(voci, l1, l2):
    return [v for v in voci if v["l1"] == l1 and (l2 is None or v["l2"] == l2)]


def calcola(voci, mese, giorni_coperti, inizio_conti):
    """Speso e previsione di un budget nel mese, dalle sue voci (gia' filtrate sulla categoria, del mese e dei
    MESI_STORIA mesi prima). giorni_coperti: {conto_id: giorno ISO fin dove arrivano i dati, o None}.
    Restituisce {speso, previsto, previsione (bool), giorni: N, reale: [cumulato per giorno o None],
    stimato: [cumulato speso + previsto o None], tipico: [mediana del cumulato nei mesi passati], conti:
    {conto_id: {speso, resto, giorno, mesi}}}."""
    n = giorni_del_mese(mese)
    inizio_m, fine_m = primo_giorno(mese), ultimo_giorno(mese)
    storia = [sposta_mese(mese, -k) for k in range(MESI_STORIA, 0, -1)]
    per_conto_mese = defaultdict(lambda: defaultdict(lambda: [0] * (n + 1)))   # conto -> mese -> giorno -> spesa
    for v in voci:
        m = v["data"][:7]
        if m != mese and m not in storia:
            continue
        g = min(int(v["data"][8:10]), n)                # il 31 di un mese lungo vale l'ultimo giorno del mese
        per_conto_mese[v["conto_id"]][m][g] += v["spesa"]
    conti = {}
    for c, mesi in per_conto_mese.items():
        cop = giorni_coperti.get(c)
        if cop is None or date.fromisoformat(cop) >= fine_m:
            g_c = n
        elif date.fromisoformat(cop) < inizio_m:
            g_c = 0
        else:
            g_c = date.fromisoformat(cop).day
        validi = [h for h in storia if h >= inizio_conti.get(c, h)
                  and (cop is None or date.fromisoformat(cop) >= ultimo_giorno(h))]
        curve = []
        for h in validi:
            giorni = mesi.get(h, [0] * (n + 1))
            curve.append([sum(giorni[g_c + 1:d + 1]) for d in range(n + 1)])
        resto = [round(statistics.median([cv[d] for cv in curve])) if curve and d > g_c else 0 for d in range(n + 1)]
        reale = mesi.get(mese, [0] * (n + 1))
        conti[c] = {"giorno": g_c, "speso": sum(reale), "resto": resto[n] if g_c < n else 0, "mesi": len(validi),
                    "reale": reale, "curva": resto}
    speso = sum(x["speso"] for x in conti.values())
    previsto = speso + sum(x["resto"] for x in conti.values())
    g_min = min([x["giorno"] for x in conti.values()] or [n])
    previsione = g_min < n
    cumulato = [0] * (n + 1)
    for x in conti.values():
        t = 0
        for d in range(1, n + 1):
            t += x["reale"][d]
            cumulato[d] += t
    reale = [cumulato[d] if d <= g_min else None for d in range(1, n + 1)]
    stimato = None
    if previsione:
        stimato = []
        for d in range(1, n + 1):
            if d < g_min:
                stimato.append(None)
                continue
            tot = 0
            for x in conti.values():
                tot += sum(x["reale"][1:min(d, x["giorno"]) + 1]) + (x["curva"][d] if d > x["giorno"] else 0)
            stimato.append(tot)
    tipico_mesi = []
    for h in storia:
        if not any(h >= inizio_conti.get(c, h) for c in per_conto_mese):
            continue
        t, curva = 0, []
        for d in range(1, n + 1):
            t += sum(per_conto_mese[c][h][d] for c in per_conto_mese if h in per_conto_mese[c])
            curva.append(t)
        tipico_mesi.append(curva)
    tipico = [round(statistics.median([cv[d] for cv in tipico_mesi])) for d in range(n)] if tipico_mesi else None
    return {"speso": speso, "previsto": previsto, "previsione": previsione, "giorni": n, "reale": reale,
            "stimato": stimato, "tipico": tipico,
            "conti": {c: {k: x[k] for k in ("giorno", "speso", "resto", "mesi")} for c, x in conti.items()}}


def stato(importo, speso, previsto):
    if speso > importo:
        return SUPERATO
    if previsto > importo:
        return A_RISCHIO
    return IN_LINEA


def _percento(parte, totale):
    return round(100 * parte / totale) if totale else None


# ---------------------------------------------------------------- la pagina

def analizza(conn, mese, oggi=None, con_storico=True):
    """Tutto cio' che serve alla pagina Budget per un mese: {mese, budget: [...], coperture, da_classificare,
    rimborsi_non_attribuiti, avvisi}. Ogni budget: {l1, l2, perimetro, nome, importo, speso, previsto,
    previsione, stato, saturazione, saturazione_prevista, sopra (euro oltre il budget, gia' spesi o previsti),
    calcolo (il dettaglio di `calcola`), storico: [(mese, importo, speso, previsto o None)]}."""
    oggi = oggi or date.today()
    valida_mese(mese)
    tutti = elenco(conn)
    attivi = [b for b in tutti if importo_nel_mese(b, mese) is not None]
    perimetri = sorted({b["perimetro"] for b in attivi}, key=list(PERIMETRI_BUDGET).index)
    cop = coperture(conn)
    giorni_coperti = {c: g for c, (g, _) in cop.items()}
    inizio_conti = _inizio_conti(conn)
    cat_esistenti = categorie_di_spesa()
    mesi_storico = [sposta_mese(mese, -k) for k in range(MESI_STORIA - 1, -1, -1)] if con_storico else [mese]
    da = primo_giorno(sposta_mese(mese, -(MESI_STORIA + (MESI_STORIA - 1 if con_storico else 0)))).isoformat()
    a = ultimo_giorno(mese).isoformat()
    voci_per, non_attr, da_class = {}, [], {}
    for p in perimetri:
        voci_per[p], na = voci_di_spesa(conn, p, da, a)
        visti = {x["mov_id"] for x in non_attr}            # lo stesso conto sta in piu' perimetri
        non_attr += [dict(x, perimetro=p) for x in na if x["data"][:7] == mese and x["mov_id"] not in visti]
        da_class[p] = da_classificare(conn, p, mese)
    risultati = []
    for b in attivi:
        importo = importo_nel_mese(b, mese)
        voci = _della_voce(voci_per[b["perimetro"]], b["l1"], b["l2"])
        cal = calcola(voci, mese, giorni_coperti, inizio_conti)
        st_ = stato(importo, cal["speso"], cal["previsto"])
        storico = []
        for m in mesi_storico:
            imp_m = importo_nel_mese(b, m)
            if imp_m is None:
                storico.append((m, None, None, None))
                continue
            if m == mese:
                storico.append((m, imp_m, cal["speso"], cal["previsto"] if cal["previsione"] else None))
                continue
            c_m = calcola(voci, m, giorni_coperti, inizio_conti)
            storico.append((m, imp_m, c_m["speso"], c_m["previsto"] if c_m["previsione"] else None))
        mancante = b["l1"] not in cat_esistenti or (b["l2"] and b["l2"] not in cat_esistenti.get(b["l1"], []))
        risultati.append({
            "l1": b["l1"], "l2": b["l2"], "perimetro": b["perimetro"], "nome": nome_voce(b["l1"], b["l2"]),
            "importo": importo, "importi": b["importi"], "speso": cal["speso"], "previsto": cal["previsto"],
            "previsione": cal["previsione"], "stato": st_,
            "saturazione": _percento(cal["speso"], importo),
            "saturazione_prevista": _percento(cal["previsto"], importo) if cal["previsione"] else None,
            "sopra": (cal["speso"] - importo) if st_ == SUPERATO else (cal["previsto"] - importo) if st_ == A_RISCHIO else 0,
            "categoria_mancante": bool(mancante), "calcolo": cal, "storico": storico})
    avvisi = []
    for p in perimetri:
        for r in risultati:
            if r["perimetro"] != p or r["l2"]:
                continue
            figli = [x for x in risultati if x["perimetro"] == p and x["l1"] == r["l1"] and x["l2"]]
            somma = sum(x["importo"] for x in figli)
            if figli and somma > r["importo"]:
                avvisi.append("%s (%s): i budget delle sottocategorie sommano a %s, piu' del budget della categoria "
                              "(%s)." % (r["nome"], PERIMETRI_BUDGET[p], _euro(somma), _euro(r["importo"])))
    return {"mese": mese, "budget": risultati, "tutti": tutti, "perimetri": perimetri,
            "coperture": copertura_conti(conn, perimetri, oggi) if perimetri else [],
            "da_classificare": da_class, "rimborsi_non_attribuiti": non_attr, "avvisi": avvisi}


def _euro(cent):
    from .numeri import euro
    return euro(cent)


def budget_della_voce(conn, l1, l2, perimetro):
    """Importi del budget di una voce in un perimetro, [(dal_mese, importo_cent)], o None: per la linea del
    budget nel grafico della spesa per mese di "Dove vanno i soldi"."""
    for b in elenco(conn):
        if b["l1"] == l1 and b["l2"] == l2 and b["perimetro"] == perimetro:
            return b["importi"]
    return None
