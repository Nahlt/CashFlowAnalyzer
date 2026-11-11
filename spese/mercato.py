"""Prezzi di mercato da Yahoo Finance (facoltativo, spento finche' non lo si accende in Impostazioni).

Cosa esce dal PC: il codice ISIN di ogni titolo in portafoglio (per trovare il simbolo di borsa) e il simbolo
(per scaricare le chiusure giornaliere). Mai quote, importi, nomi o date dei movimenti: il primo scarico parte da
una data fissa uguale per tutti (INIZIO), i successivi dall'ultimo prezzo gia' salvato, che e' un dato pubblico.
Ogni richiesta resta nel registro delle richieste (motore "yahoo").

I prezzi si salvano in euro: le quotazioni in altra valuta si convertono con il cambio giornaliero di Yahoo
({VALUTA}EUR=X); quelle in centesimi (GBp, ZAc, ILA) si dividono prima per 100.
"""
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .db import leggi_impostazione, ora, scrivi_impostazione, transazione

CHIAVE_ATTIVO = "mercato_yahoo"
CHIAVE_ULTIMO = "mercato_aggiornato_il"
CHIAVE_COPERTURA = "mercato_coperto_dal"     # {isin: primo giorno per cui i prezzi sono stati chiesti e salvati}
SEI = Decimal("0.000001")
MOTORE = "yahoo"
INIZIO = date(2000, 1, 1)           # primo scarico: data fissa, non legata ai propri movimenti
SOVRAPPOSIZIONE = 7                 # giorni riscaricati a ogni aggiornamento (chiusure corrette dopo)
CENTESIMI = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ZAC": "ZAR", "ILA": "ILS"}
# borse con quotazione in euro, nell'ordine preferito (Xetra e Francoforte per prime: dove trattano i broker tedeschi)
SUFFISSI_EURO = (".DE", ".F", ".MI", ".AS", ".PA", ".SG", ".MU", ".BE", ".DU", ".HM", ".HA", ".VI", ".MC",
                 ".BR", ".LS", ".IR", ".HE")


def attivo(conn):
    return bool(leggi_impostazione(conn, CHIAVE_ATTIVO, False))


def base_url():
    return os.environ.get("SPESE_YAHOO_BASE", "https://query2.finance.yahoo.com").rstrip("/")


class Http:
    """Accesso di rete minimo; nei test si passa un oggetto con lo stesso metodo get(percorso, parametri)."""

    def get(self, percorso, parametri):
        import requests
        r = requests.get(base_url() + percorso, params=parametri, timeout=(5, 20),
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


def _registra(conn, simbolo, richiesta, esito):
    conn.execute("INSERT INTO registro_invii(ts, motore, modello, richiesta, risposta, esito) VALUES (?,?,?,?,?,?)",
                 (ora(), MOTORE, simbolo, richiesta, "", esito))


# --- simboli -------------------------------------------------------------------------------
def scegli_simbolo(quotazioni):
    """Tra le quotazioni trovate per un ISIN preferisce una borsa in euro; altrimenti la prima."""
    validi = [q for q in quotazioni or [] if q.get("symbol") and q.get("quoteType") != "OPTION"]
    if not validi:
        return None

    def rango(q):
        s = q["symbol"].upper()
        for i, suf in enumerate(SUFFISSI_EURO):
            if s.endswith(suf):
                return i
        return len(SUFFISSI_EURO)
    return min(enumerate(validi), key=lambda x: (rango(x[1]), x[0]))[1]


def cerca_simbolo(conn, isin, http=None):
    http = http or Http()
    par = {"q": isin, "quotesCount": 10, "newsCount": 0, "listsCount": 0, "enableFuzzyQuery": "false"}
    dati = http.get("/v1/finance/search", par) or {}
    q = scegli_simbolo(dati.get("quotes"))
    _registra(conn, q["symbol"] if q else None, "search q=%s" % isin, q["symbol"] if q else "nessun simbolo")
    return q


def imposta_simbolo(conn, isin, simbolo):
    """Simbolo indicato a mano (vuoto: si torna alla ricerca automatica). I prezzi si riscaricano."""
    simbolo = (simbolo or "").strip().upper() or None
    with transazione(conn):
        conn.execute("DELETE FROM prezzi_mercato WHERE isin = ?", (isin,))
        conn.execute("INSERT INTO titoli_mercato(isin, simbolo, scelto) VALUES (?,?,?) ON CONFLICT(isin) DO UPDATE SET "
                     "simbolo = excluded.simbolo, scelto = excluded.scelto, valuta = NULL, nome = NULL, borsa = NULL, "
                     "aggiornato_il = NULL, errore = NULL", (isin, simbolo, 1 if simbolo else 0))
        copertura = leggi_impostazione(conn, CHIAVE_COPERTURA, {}) or {}
        if copertura.pop(isin, None) is not None:
            scrivi_impostazione(conn, CHIAVE_COPERTURA, copertura)
        scrivi_impostazione(conn, CHIAVE_ULTIMO, None)


def titoli(conn):
    return {r["isin"]: dict(r) for r in conn.execute("SELECT * FROM titoli_mercato")}


# --- serie storiche ------------------------------------------------------------------------
def _epoca(d):
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def serie(simbolo, dal, http=None, al=None):
    """Chiusure giornaliere dal giorno `dal` (date). Restituisce (meta, [(data iso, chiusura)])."""
    http = http or Http()
    al = al or date.today() + timedelta(days=1)
    dati = http.get("/v8/finance/chart/%s" % simbolo, {"period1": _epoca(dal), "period2": _epoca(al),
                                                       "interval": "1d", "events": "div,splits"})
    ris = ((dati or {}).get("chart") or {}).get("result") or []
    if not ris:
        err = ((dati or {}).get("chart") or {}).get("error") or {}
        raise ValueError(err.get("description") or "simbolo non trovato su Yahoo Finance")
    r = ris[0]
    meta = r.get("meta") or {}
    scarto = int(meta.get("gmtoffset") or 0)
    tempi = r.get("timestamp") or []
    chiusure = (((r.get("indicators") or {}).get("quote") or [{}])[0] or {}).get("close") or []
    punti = {}
    for t, v in zip(tempi, chiusure):
        if v is None:
            continue
        g = datetime.fromtimestamp(int(t) + scarto, tz=timezone.utc).date().isoformat()
        punti[g] = Decimal(str(v))                  # decimale esatto del numero ricevuto; a parita' di giorno l'ultimo
    return meta, sorted(punti.items())


def _cambi(conn, valuta, http, serve_dal):
    """Euro per 1 unita' di `valuta`, giorno per giorno: (tutti i cambi, cambi nuovi da salvare). Anche qui il primo
    scarico parte da INIZIO, cosi' la richiesta non dice nulla sui propri movimenti; si salvano pero' solo i giorni
    che servono (da `serve_dal`). Se ne servono di piu' vecchi di quelli salvati, si riscarica."""
    if valuta == "EUR":
        return None, []
    primo, ultimo = conn.execute("SELECT MIN(data), MAX(data) FROM cambi_mercato WHERE valuta = ?", (valuta,)).fetchone()
    salvati = {d: Decimal(str(v)) for d, v in conn.execute("SELECT data, tasso FROM cambi_mercato WHERE valuta = ?", (valuta,))}
    copre = primo and primo <= (serve_dal + timedelta(days=SOVRAPPOSIZIONE)).isoformat()
    nuovi = []
    if not copre or ultimo < date.today().isoformat():
        inizio = date.fromisoformat(ultimo) - timedelta(days=SOVRAPPOSIZIONE) if copre else INIZIO
        simbolo = "%sEUR=X" % valuta
        _, punti = serie(simbolo, inizio, http)
        _registra(conn, simbolo, "chart %s dal %s" % (simbolo, inizio.isoformat()), "%d cambi" % len(punti))
        prima = [x for x in punti if x[0] < serve_dal.isoformat()][-1:]     # l'ultimo cambio prima serve ancora
        nuovi = prima + [(d, v) for d, v in punti if d >= serve_dal.isoformat()]
        salvati.update(nuovi)
    return sorted(salvati.items()), nuovi


def in_euro(punti, cambi):
    """Converte le chiusure con il cambio dello stesso giorno o, se manca, dell'ultimo giorno precedente."""
    if cambi is None:
        return punti
    out, i, tasso = [], 0, None
    cambi = [(d, t) for d, t in cambi]
    for d, v in punti:
        while i < len(cambi) and cambi[i][0] <= d:
            tasso = cambi[i][1]
            i += 1
        t = tasso if tasso is not None else (cambi[0][1] if cambi else None)
        if t is not None:
            out.append((d, Decimal(str(v)) * Decimal(str(t))))
    return out


RETE = "rete"


class NonRaggiungibile(Exception):
    """Errore di rete o rifiuto del servizio: inutile provare gli altri titoli."""


def di_rete(e):
    if isinstance(e, (NonRaggiungibile, ConnectionError, TimeoutError)):
        return True
    try:
        import requests
    except ImportError:                                 # pragma: no cover
        return False
    if isinstance(e, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return e.response.status_code in (401, 403, 429) or e.response.status_code >= 500
    return False


def motivo_rete(e):
    """Spiegazione breve di un errore di rete (senza indirizzi ne' parametri della richiesta)."""
    stato = getattr(getattr(e, "response", None), "status_code", None)
    if stato == 429:
        return "troppe richieste, Yahoo chiede di aspettare"
    if stato in (401, 403):
        return "accesso rifiutato da Yahoo (HTTP %d)" % stato
    if stato:
        return "errore del servizio (HTTP %d)" % stato
    nome = e.__class__.__name__.lower()
    if "timeout" in nome:
        return "nessuna risposta in tempo"
    return "connessione assente o rifiutata"


def da_aggiornare(conn):
    return attivo(conn) and leggi_impostazione(conn, CHIAVE_ULTIMO) != date.today().isoformat()


def aggiorna(conn, isin_dal, http=None, avanzamento=None):
    """Scarica i prezzi mancanti per {isin: primo giorno che serve (date)}. Restituisce {isin: errore} per i titoli
    non riusciti. Il primo giorno serve solo per non salvare prezzi inutili: la richiesta parte comunque da INIZIO.

    Per ogni titolo prima si scarica (nessuna scrittura tranne il registro), poi si salva tutto in una sola
    transazione: migliaia di righe salvate una per una, ognuna con il suo commit su disco, richiedono minuti.
    avanzamento(fatti, totale, simbolo) si chiama prima di ogni titolo e alla fine."""
    http = http or Http()
    noti = titoli(conn)
    errori = {}
    copertura = leggi_impostazione(conn, CHIAVE_COPERTURA, {}) or {}
    elenco = sorted(isin_dal.items())
    for n, (isin, serve_dal) in enumerate(elenco):
        t = noti.get(isin) or {}
        if avanzamento:
            avanzamento(n, len(elenco), t.get("simbolo") or isin)
        try:
            simbolo, trovato = t.get("simbolo"), None
            if not simbolo and not t.get("scelto"):
                trovato = cerca_simbolo(conn, isin, http)
                if not trovato:
                    raise ValueError("nessun simbolo trovato per questo ISIN: indicalo a mano")
                simbolo = trovato["symbol"]
            if not simbolo:
                continue                                # escluso a mano
            primo_salvato, ultimo = conn.execute("SELECT MIN(data), MAX(data) FROM prezzi_mercato WHERE isin = ?",
                                                 (isin,)).fetchone()
            # i prezzi salvati partono dal primo giorno che serviva al momento dello scarico: se ora ne serve
            # uno precedente (importato uno storico piu' vecchio) si riscarica tutto dalla data fissa
            coperto_dal = copertura.get(isin) or primo_salvato
            completo = bool(ultimo and coperto_dal
                            and coperto_dal <= (serve_dal + timedelta(days=SOVRAPPOSIZIONE)).isoformat())
            punti, cambi_nuovi, valuta, valuta_base = [], [], t.get("valuta"), None
            if not completo or ultimo < date.today().isoformat():
                inizio = date.fromisoformat(ultimo) - timedelta(days=SOVRAPPOSIZIONE) if completo else INIZIO
                meta, punti = serie(simbolo, inizio, http)
                valuta = meta.get("currency") or "EUR"
                if valuta in CENTESIMI:
                    punti, valuta_base = [(d, Decimal(str(v)) / 100) for d, v in punti], CENTESIMI[valuta]
                else:
                    valuta_base = valuta.upper()
                primo = (serve_dal - timedelta(days=SOVRAPPOSIZIONE)).isoformat()
                punti = [(d, v) for d, v in punti if d >= primo]
                _registra(conn, simbolo, "chart %s dal %s" % (simbolo, inizio.isoformat()), "%d prezzi" % len(punti))
                if punti:
                    cambi, cambi_nuovi = _cambi(conn, valuta_base, http, date.fromisoformat(punti[0][0]))
                    convertiti = in_euro(punti, cambi)
                    if not convertiti:
                        raise ValueError("cambio %s/EUR non disponibile" % valuta_base)
                    punti = convertiti
            conn.execute("BEGIN")
            try:
                if trovato:
                    conn.execute("INSERT INTO titoli_mercato(isin, simbolo, nome, borsa) VALUES (?,?,?,?) ON CONFLICT(isin) "
                                 "DO UPDATE SET simbolo = excluded.simbolo, nome = excluded.nome, borsa = excluded.borsa",
                                 (isin, simbolo, trovato.get("longname") or trovato.get("shortname"),
                                  trovato.get("exchDisp") or trovato.get("exchange")))
                conn.executemany("INSERT OR REPLACE INTO cambi_mercato(valuta, data, tasso) VALUES (?,?,?)",
                                 [(valuta_base, d, _testo(v)) for d, v in cambi_nuovi])
                conn.executemany("INSERT OR REPLACE INTO prezzi_mercato(isin, data, prezzo) VALUES (?,?,?)",
                                 [(isin, d, _testo(v)) for d, v in punti])
                if not completo:
                    copertura[isin] = min(serve_dal.isoformat(), copertura.get(isin) or "9999")
                    scrivi_impostazione(conn, CHIAVE_COPERTURA, copertura)
                conn.execute("UPDATE titoli_mercato SET valuta = ?, aggiornato_il = ?, errore = NULL WHERE isin = ?",
                             (valuta, ora(), isin))
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        except Exception as e:                          # un titolo che non va non ferma gli altri...
            msg = str(e) or e.__class__.__name__
            if di_rete(e):                              # ...ma senza rete (o con Yahoo che rifiuta) ci si ferma
                errori[RETE] = "Yahoo Finance non raggiungibile (%s): riprova piu' tardi." % motivo_rete(e)
                break
            errori[isin] = msg
            conn.execute("INSERT INTO titoli_mercato(isin, errore) VALUES (?,?) ON CONFLICT(isin) DO UPDATE SET "
                         "errore = excluded.errore", (isin, msg[:300]))
    if avanzamento:
        avanzamento(len(elenco), len(elenco), "")
    if RETE not in errori and (not errori or len(errori) < len(isin_dal)):
        scrivi_impostazione(conn, CHIAVE_ULTIMO, date.today().isoformat())
    return errori


# --- lettura -------------------------------------------------------------------------------
def _testo(v):
    """Prezzo o cambio come testo decimale, con al piu' sei cifre dopo la virgola."""
    return format(Decimal(str(v)).quantize(SEI).normalize(), "f")


def prezzi(conn, isin_lista=None):
    """{isin: [(data, prezzo € come Decimal)]} in ordine di data."""
    sql, par = "SELECT isin, data, prezzo FROM prezzi_mercato", []
    if isin_lista is not None:
        if not isin_lista:
            return {}
        sql += " WHERE isin IN (%s)" % ",".join("?" * len(isin_lista))
        par = list(isin_lista)
    out = {}
    for isin, d, v in conn.execute(sql + " ORDER BY isin, data", par):
        out.setdefault(isin, []).append((d, Decimal(str(v))))
    return out


def prezzo_al(serie_prezzi, giorno):
    """Ultimo prezzo disponibile non oltre `giorno` (iso): (data, prezzo) o None."""
    scelto = None
    for d, v in serie_prezzi or []:
        if d > giorno:
            break
        scelto = (d, v)
    return scelto
