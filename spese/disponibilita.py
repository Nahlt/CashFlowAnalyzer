"""Saldo dei conti a ogni data, ricavato da un saldo noto e dai movimenti importati.

Un saldo noto ("ancora") e' il saldo a fine giornata di un giorno: inserito a mano dall'utente o letto da un
estratto (saldo iniziale e finale di ING, saldo alla data di Credem e Fineco). Il saldo di un altro giorno si
ottiene dall'ancora piu' vicina aggiungendo o togliendo i movimenti contabilizzati in mezzo. I movimenti si
contano alla data contabile (quella con cui la banca calcola il saldo) e comprendono commissioni e imposte;
contano tutti, anche quelli esclusi dalle analisi, perche' il saldo e' quello che c'e' sul conto.

I saldi stanno nella tabella `saldi`, gia' usata dagli import: quelli inseriti a mano hanno tipo 'manuale' e
nessun import collegato (si scrivono con `servizi.imposta_saldo`). Annullare un import non li tocca; eliminare il
conto li cancella.
"""
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd


# conti per cui il saldo ha senso: la carta di credito ha solo lo speso non ancora addebitato
TIPI_CON_SALDO = ("conto", "broker")
DATA_CONTABILE = "coalesce(m.data_contabile, m.data)"
IMPORTO = "(coalesce(m.importo_cent, 0) + m.fee_cent + m.tax_cent)"

FONTI = {"manuale": "Inserito a mano", "iniziale": "Estratto (saldo iniziale)", "finale": "Estratto (saldo finale)",
         "alla_data": "Estratto (saldo alla data)"}


def _giorno(d):
    return d if isinstance(d, str) else d.isoformat()


def _prima(giorno, giorni=1):
    return (date.fromisoformat(giorno) - timedelta(days=giorni)).isoformat()


def conti_con_saldo(conn):
    """Conti importati di cui si puo' calcolare il saldo (conti correnti, broker, PayPal), nell'ordine dei filtri."""
    return conn.execute("SELECT id, nome, tipo, perimetro FROM conti WHERE importato = 1 AND tipo IN (%s) "
                        "ORDER BY perimetro DESC, nome" % ",".join("?" * len(TIPI_CON_SALDO)),
                        TIPI_CON_SALDO).fetchall()


def ultimo_movimento(conn, conto_id):
    """Data contabile dell'ultimo movimento importato del conto (None se non ce ne sono)."""
    return conn.execute("SELECT max(%s) FROM movimenti m WHERE m.conto_id = ?" % DATA_CONTABILE,
                        (conto_id,)).fetchone()[0]


def primo_movimento(conn, conto_id):
    return conn.execute("SELECT min(%s) FROM movimenti m WHERE m.conto_id = ?" % DATA_CONTABILE,
                        (conto_id,)).fetchone()[0]


# --- lettura ----------------------------------------------------------------------------------------------------
@dataclass
class Ancora:
    giorno: str          # il saldo vale a fine giornata di questo giorno
    saldo_cent: int
    tipo: str            # manuale, iniziale, finale, alla_data
    data: str            # data scritta nell'estratto o inserita (il saldo iniziale vale per il giorno prima)

    @property
    def fonte(self):
        return FONTI.get(self.tipo, self.tipo)

    @property
    def fonte_estesa(self):
        """Come fonte; per il saldo iniziale dice anche la data dell'estratto (che e' il giorno dopo)."""
        if self.tipo == "iniziale":
            return "Estratto (saldo iniziale del %s)" % data_it(self.data)
        return self.fonte


def ancore(conn, conto_id):
    """Saldi noti del conto, uno per giorno, in ordine di data. Il saldo iniziale di un estratto e' quello prima
    dei movimenti del primo giorno, cioe' la fine del giorno precedente. Nello stesso giorno vince il saldo
    inserito a mano; tra due estratti, il piu' recente."""
    per_giorno = {}
    righe = conn.execute("SELECT data, saldo_cent, tipo, coalesce(import_id, 0) AS imp FROM saldi "
                         "WHERE conto_id = ? AND data IS NOT NULL AND saldo_cent IS NOT NULL "
                         "ORDER BY (tipo = 'manuale'), imp", (conto_id,)).fetchall()
    for r in righe:                                   # le righe dopo prevalgono: prima gli estratti, ultimi i manuali
        giorno = _prima(r["data"]) if r["tipo"] == "iniziale" else r["data"]
        per_giorno[giorno] = Ancora(giorno, int(r["saldo_cent"]), r["tipo"], r["data"])
    return [per_giorno[g] for g in sorted(per_giorno)]


class Saldi:
    """Calcolo del saldo di un conto a qualunque data. Si legge una volta il totale dei movimenti per giorno;
    il saldo di un giorno D dall'ancora A e': saldo(A) + movimenti fino a D - movimenti fino ad A."""

    def __init__(self, conn, conto_id):
        self.conto_id = conto_id
        self.ancore = ancore(conn, conto_id)
        righe = conn.execute(
            "SELECT %s AS giorno, "
            "sum(CASE WHEN coalesce(m.importo_cent, 0) > 0 THEN m.importo_cent ELSE 0 END "
            "    + CASE WHEN m.fee_cent > 0 THEN m.fee_cent ELSE 0 END + CASE WHEN m.tax_cent > 0 THEN m.tax_cent ELSE 0 END) AS entrate, "
            "sum(CASE WHEN coalesce(m.importo_cent, 0) < 0 THEN -m.importo_cent ELSE 0 END "
            "    + CASE WHEN m.fee_cent < 0 THEN -m.fee_cent ELSE 0 END + CASE WHEN m.tax_cent < 0 THEN -m.tax_cent ELSE 0 END) AS uscite, "
            "count(*) AS n FROM movimenti m WHERE m.conto_id = ? GROUP BY giorno ORDER BY giorno"
            % DATA_CONTABILE, (conto_id,)).fetchall()
        self.giorni = [r["giorno"] for r in righe]
        self.per_giorno = {r["giorno"]: (int(r["entrate"]), int(r["uscite"]), int(r["n"])) for r in righe}
        self._cumulato, tot = [], 0
        for g in self.giorni:
            e, u, _ = self.per_giorno[g]
            tot += e - u
            self._cumulato.append(tot)

    @property
    def calcolabile(self):
        return bool(self.ancore)

    def _fino_a(self, giorno):
        """Somma dei movimenti contabilizzati fino al giorno compreso."""
        i = bisect_right(self.giorni, giorno)
        return self._cumulato[i - 1] if i else 0

    def ancora_per(self, giorno):
        """L'ancora piu' vicina al giorno; a pari distanza quella inserita a mano, poi quella prima."""
        d = date.fromisoformat(giorno)
        return min(self.ancore, key=lambda a: (abs((date.fromisoformat(a.giorno) - d).days), a.tipo != "manuale",
                                               a.giorno > giorno))

    def saldo(self, giorno):
        """Saldo a fine giornata (None se il conto non ha nessun saldo noto)."""
        giorno = _giorno(giorno)
        if not self.ancore:
            return None
        a = self.ancora_per(giorno)
        return a.saldo_cent + self._fino_a(giorno) - self._fino_a(a.giorno)

    def saldo_iniziale(self, giorno):
        """Saldo prima dei movimenti del giorno (fine del giorno precedente)."""
        return self.saldo(_prima(_giorno(giorno)))

    def serie(self, da, a):
        """Una riga per giorno da `da` ad `a` compresi: giorno, saldo a fine giornata, entrate, uscite e numero
        di movimenti del giorno (in centesimi)."""
        da, a = _giorno(da), _giorno(a)
        g, fine, righe = date.fromisoformat(da), date.fromisoformat(a), []
        while g <= fine:
            s = g.isoformat()
            e, u, n = self.per_giorno.get(s, (0, 0, 0))
            righe.append({"giorno": s, "saldo": self.saldo(s), "entrate": e, "uscite": u, "movimenti": n})
            g += timedelta(days=1)
        return pd.DataFrame(righe, columns=["giorno", "saldo", "entrate", "uscite", "movimenti"])

    def scarti(self):
        """Confronto tra saldi noti consecutivi: per ciascuno (dal secondo), quanto differisce da quello che si
        ottiene dal saldo precedente piu' i movimenti in mezzo. Uno scarto diverso da zero vuol dire che tra le
        due date manca (o avanza) qualcosa: un estratto non importato, un movimento non contabilizzato."""
        out = []
        for p, q in zip(self.ancore, self.ancore[1:]):
            atteso = p.saldo_cent + self._fino_a(q.giorno) - self._fino_a(p.giorno)
            out.append((p, q, q.saldo_cent - atteso))
        return out


def movimenti_con_saldo(conn, conto_id, da, a, saldi=None):
    """Movimenti del conto contabilizzati tra da e a, dal piu' recente, con il saldo dopo ciascuno. Il saldo a
    fine giornata e' esatto; dentro lo stesso giorno l'ordine e' quello del file importato."""
    saldi = saldi or Saldi(conn, conto_id)
    righe = conn.execute(
        "SELECT m.id, %s AS data_contabile, m.data, m.ora, m.descrizione, "
        "coalesce(m.esercente, m.controparte_nome) AS esercente, m.tipo, %s AS importo_cent, "
        "(SELECT a.natura FROM analisi a WHERE a.mov_id = m.id ORDER BY a.id LIMIT 1) AS natura, "
        "(SELECT a.l1 FROM analisi a WHERE a.mov_id = m.id ORDER BY a.id LIMIT 1) AS l1, "
        "(SELECT a.l2 FROM analisi a WHERE a.mov_id = m.id ORDER BY a.id LIMIT 1) AS l2 "
        "FROM movimenti m WHERE m.conto_id = ? AND %s BETWEEN ? AND ? "
        "ORDER BY %s, coalesce(m.raw_id, 0), m.rowid" % (DATA_CONTABILE, IMPORTO, DATA_CONTABILE, DATA_CONTABILE),
        (conto_id, _giorno(da), _giorno(a))).fetchall()
    df = pd.DataFrame([dict(r) for r in righe],
                      columns=["id", "data_contabile", "data", "ora", "descrizione", "esercente", "tipo", "importo_cent",
                               "natura", "l1", "l2"])
    if df.empty:
        df["saldo_dopo"] = []
        return df
    dopo = []
    for giorno, gruppo in df.groupby("data_contabile", sort=False):
        fine = saldi.saldo(giorno)
        resto = int(gruppo["importo_cent"].sum())
        for v in gruppo["importo_cent"]:
            resto -= int(v)
            dopo.append(None if fine is None else fine - resto)   # fine giornata meno quello che arriva dopo
    df["saldo_dopo"] = dopo
    return df.iloc[::-1].reset_index(drop=True)


def data_it(giorno):
    """2026-10-31 -> 31/10/2026."""
    return "%s/%s/%s" % (giorno[8:10], giorno[5:7], giorno[:4]) if giorno else ""


def importo_da_testo(testo):
    """Saldo scritto dall'utente ("1.234,56", "-50", "1234.56") in centesimi; None se non e' un importo."""
    from .numeri import FormatoNonValido, a_centesimi
    s = (testo or "").replace("€", "").replace("EUR", "").strip()
    if not s:
        return None
    for formato in ("italiano", "punto"):
        try:
            v = a_centesimi(s, formato)
        except FormatoNonValido:
            continue
        if v is not None:
            return int(v)
    return None
