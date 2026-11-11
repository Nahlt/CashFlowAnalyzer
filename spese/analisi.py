"""Query per le viste di analisi. Entrate = natura entrata; uscite = uscite al netto dei rimborsi."""
from dataclasses import dataclass, field

import pandas as pd

USCITE = "('uscita','rimborso')"
LIMITE_MOVIMENTI = 2000


PERIMETRI = {"personale": "Personale", "comune": "Comune", "tutti": "Tutti"}


@dataclass
class Filtri:
    """Cosa analizzare. conti: i conti scelti (perimetro composto); se vuoto vale il perimetro: 'personale',
    'comune' o 'tutti'. Quando l'insieme comprende entrambi i lati di un contributo (dal conto personale al
    conto comune) quel contributo si annulla: sono soldi che passano da un conto all'altro, non entrate o
    uscite. Se il conto comune non e' tra quelli scelti, il versamento resta una spesa del conto personale."""
    perimetro: str = "personale"
    da: str = None
    a: str = None
    conti: list = field(default_factory=list)

    def _netto(self, alias, conti=None):
        dentro, par = "", []
        if conti:
            dentro = " AND mc.conto_id IN (%s)" % ",".join("?" * len(conti))
            par = list(conti)
        # solo i contributi abbinati (fiducia indicata): una contropartita solo proposta non annulla nulla,
        # altrimenti sparirebbe un lato e l'altro resterebbe
        # lo stesso per i contributi verso un tuo conto importato che non copre ancora quella data (fiducia
        # "attesa"): l'altro lato non c'e' ancora, ma il conto e' tra quelli guardati
        attesa, par2 = "", []
        if conti:
            attesa = " AND t2.conto_destinazione_id IN (%s)" % ",".join("?" * len(conti))
            par2 = list(conti)
        return ("NOT EXISTS (SELECT 1 FROM trasferimenti t JOIN movimenti mc ON mc.id = t.controparte_mov_id "
                "WHERE t.mov_id = %s.mov_id AND t.stato = 'contributo' AND t.fiducia IS NOT NULL "
                "AND t.fiducia <> 'attesa'%s) "
                "AND NOT EXISTS (SELECT 1 FROM trasferimenti t2 WHERE t2.mov_id = %s.mov_id "
                "AND t2.stato = 'contributo' AND t2.fiducia = 'attesa'%s)"
                % (alias, dentro, alias, attesa)), par + par2

    def where(self, alias="a"):
        if self.conti:
            cond = ["%s.conto_id IN (%s)" % (alias, ",".join("?" * len(self.conti)))]
            par = list(self.conti)
            n, p = self._netto(alias, self.conti)
            cond.append(n)
            par += p
        elif self.perimetro == "tutti":
            n, par = self._netto(alias)
            cond = [n]
        elif self.perimetro in ("personale", "comune"):
            cond, par = ["%s.perimetro = ?" % alias], [self.perimetro]
        else:
            cond, par = ["0"], []                      # nessun conto scelto
        if self.da:
            cond.append("%s.data >= ?" % alias)
            par.append(self.da)
        if self.a:
            cond.append("%s.data <= ?" % alias)
            par.append(self.a)
        return " AND ".join(cond), par


def _df(conn, sql, par):
    righe = conn.execute(sql, par).fetchall()
    return pd.DataFrame([dict(r) for r in righe]) if righe else pd.DataFrame()


def periodo_disponibile(conn, conti=None):
    if conti:
        r = conn.execute("SELECT min(data), max(data) FROM analisi WHERE conto_id IN (%s)" % ",".join("?" * len(conti)),
                         list(conti)).fetchone()
    else:
        r = conn.execute("SELECT min(data), max(data) FROM analisi").fetchone()
    return r[0], r[1]


def flussi_mensili(conn, f: Filtri):
    w, par = f.where()
    df = _df(conn, f"""
        SELECT a.mese,
               coalesce(sum(CASE WHEN a.natura = 'entrata' THEN a.importo_cent END), 0) AS entrate,
               coalesce(-sum(CASE WHEN a.natura IN {USCITE} THEN a.importo_cent END), 0) AS uscite,
               coalesce(-sum(CASE WHEN a.natura = 'risparmio' THEN a.importo_cent END), 0) AS risparmio
        FROM analisi a WHERE {w} GROUP BY a.mese ORDER BY a.mese""", par)
    if df.empty:
        return df
    df["netto"] = df["entrate"] - df["uscite"]
    df["tasso"] = df.apply(lambda r: (r["netto"] / r["entrate"]) if r["entrate"] else None, axis=1)
    return df


def ripartizione(conn, f: Filtri, livello="l1", l1=None):
    w, par = f.where()
    extra = ""
    if l1:
        extra = " AND a.l1 = ?"
        par = par + [l1]
    col = "a.l1" if livello == "l1" else "a.l2"
    return _df(conn, f"""
        SELECT coalesce({col}, 'Da classificare') AS voce, -sum(a.importo_cent) AS importo, count(*) AS movimenti
        FROM analisi a WHERE {w} AND a.natura IN {USCITE}{extra}
        GROUP BY 1 HAVING importo <> 0 ORDER BY importo DESC""", par)


def _cond_cat(l1, l2=None):
    if l2:
        # la sottocategoria da sola non basta: "Varie" puo' stare sotto piu' categorie
        if l1 and l1 != "Da classificare":
            return "a.l1 = ? AND a.l2 = ?", [l1, l2]
        return "a.l2 = ?", [l2]
    if l1 == "Da classificare":
        return "a.l1 IS NULL", []
    return "a.l1 = ?", [l1]


def serie_categoria(conn, f: Filtri, l1, l2=None):
    w, par = f.where()
    cond, p2 = _cond_cat(l1, l2)
    return _df(conn, f"""
        SELECT a.mese, -sum(a.importo_cent) AS importo, count(*) AS movimenti
        FROM analisi a WHERE {w} AND a.natura IN {USCITE} AND {cond}
        GROUP BY a.mese ORDER BY a.mese""", par + p2)


def esercenti_categoria(conn, f: Filtri, l1, l2=None, limite=15):
    w, par = f.where()
    cond, p2 = _cond_cat(l1, l2)
    return _df(conn, f"""
        SELECT coalesce(m.esercente, m.controparte_nome, m.tipo) AS esercente, -sum(a.importo_cent) AS importo,
               count(*) AS movimenti
        FROM analisi a JOIN movimenti m ON m.id = a.mov_id
        WHERE {w} AND a.natura IN {USCITE} AND {cond}
        GROUP BY 1 ORDER BY importo DESC LIMIT {int(limite)}""", par + p2)


def movimenti(conn, f: Filtri, l1=None, l2=None, testo=None, solo_rivedere=False, limite=LIMITE_MOVIMENTI):
    w, par = f.where()
    cond = []
    if l1 or l2:
        c, p = _cond_cat(l1, l2)
        cond.append(c)
        par.extend(p)
    if testo:
        cond.append("(m.descrizione LIKE ? OR m.esercente LIKE ? OR m.controparte_nome LIKE ?)")
        par.extend(["%" + testo + "%"] * 3)
    if solo_rivedere:
        cond.append("a.da_rivedere = 1")
    extra = (" AND " + " AND ".join(cond)) if cond else ""
    return _df(conn, f"""
        SELECT a.mov_id, a.data, c.nome AS conto, a.componente, a.importo_cent, a.natura, a.l1, a.l2, a.fonte,
               a.da_rivedere, a.motivo, a.proposta, a.attributo, a.chiave, m.tipo, m.esercente, m.controparte_nome, m.descrizione
        FROM analisi a JOIN movimenti m ON m.id = a.mov_id JOIN conti c ON c.id = a.conto_id
        WHERE {w}{extra} ORDER BY a.data DESC, a.id DESC LIMIT {int(limite)}""", par)


def copertura(conn, mesi=12):
    """Matrice mese x conto con il numero di movimenti importati."""
    df = _df(conn, """SELECT substr(m.data,1,7) AS mese, c.nome AS conto, count(*) AS n
                      FROM movimenti m JOIN conti c ON c.id = m.conto_id
                      WHERE c.importato = 1 AND c.chiuso_il IS NULL GROUP BY 1, 2""", [])
    if df.empty:
        return df
    t = df.pivot(index="mese", columns="conto", values="n").fillna(0).astype(int).sort_index(ascending=False)
    return t.head(mesi)


# --- natura della spesa ed etichette (sezioni facoltative)----------------------------------
def per_attributo(conn, f: Filtri):
    """Uscite al netto dei rimborsi per natura della spesa ('' = non definita), per mese."""
    w, par = f.where()
    return _df(conn, f"""
        SELECT a.mese, coalesce(a.attributo, '') AS attributo, -sum(a.importo_cent) AS importo, count(*) AS movimenti
        FROM analisi a WHERE {w} AND a.natura IN {USCITE} GROUP BY 1, 2 ORDER BY 1""", par)


def per_etichetta(conn, f: Filtri):
    """Una riga per etichetta: uscite nette, entrate, movimenti, primo e ultimo giorno."""
    w, par = f.where()
    return _df(conn, f"""
        SELECT e.id, e.nome,
               coalesce(-sum(CASE WHEN a.natura IN {USCITE} THEN a.importo_cent END), 0) AS uscite,
               coalesce(sum(CASE WHEN a.natura = 'entrata' THEN a.importo_cent END), 0) AS entrate,
               count(DISTINCT a.mov_id) AS movimenti, min(a.data) AS dal, max(a.data) AS al
        FROM etichette e JOIN etichette_movimento em ON em.etichetta_id = e.id
        JOIN analisi a ON a.mov_id = em.mov_id
        WHERE {w} GROUP BY e.id ORDER BY uscite DESC""", par)


def etichetta_categorie(conn, f: Filtri, etichetta_id):
    w, par = f.where()
    return _df(conn, f"""
        SELECT coalesce(a.l1, 'Da classificare') AS l1, coalesce(a.l2, 'Da classificare') AS voce,
               -sum(a.importo_cent) AS importo, count(*) AS movimenti
        FROM analisi a JOIN etichette_movimento em ON em.mov_id = a.mov_id
        WHERE {w} AND em.etichetta_id = ? AND a.natura IN {USCITE}
        GROUP BY 1, 2 HAVING importo <> 0 ORDER BY importo DESC""", par + [etichetta_id])


def etichetta_movimenti(conn, f: Filtri, etichetta_id):
    w, par = f.where()
    return _df(conn, f"""
        SELECT a.data, c.nome AS conto, coalesce(m.esercente, m.controparte_nome, m.descrizione) AS voce,
               a.importo_cent, a.natura, a.l1, a.l2, a.attributo
        FROM analisi a JOIN etichette_movimento em ON em.mov_id = a.mov_id
        JOIN movimenti m ON m.id = a.mov_id JOIN conti c ON c.id = a.conto_id
        WHERE {w} AND em.etichetta_id = ? ORDER BY a.data, a.id""", par + [etichetta_id])


def anteprima_mensile(conn, f: Filtri, quante=5):
    """{mese: [(categoria, euro), ...]}: le prime categorie di spesa di ogni mese (per l'anteprima dell'andamento)."""
    w, par = f.where()
    out = {}
    for r in conn.execute(f"""
        SELECT a.mese, coalesce(a.l1, 'Da classificare') AS voce, -sum(a.importo_cent) AS importo
        FROM analisi a WHERE {w} AND a.natura IN {USCITE} GROUP BY 1, 2 HAVING importo > 0
        ORDER BY a.mese, importo DESC""", par):
        lista = out.setdefault(r["mese"], [])
        if len(lista) < quante:
            lista.append((r["voce"], round(r["importo"] / 100, 2)))
    return out


def con_mese(f: Filtri, mese):
    """Gli stessi filtri ristretti a un mese (AAAA-MM), dentro il periodo scelto."""
    import calendar
    from dataclasses import replace
    a, m = int(mese[:4]), int(mese[5:7])
    da, al = "%s-01" % mese, "%s-%02d" % (mese, calendar.monthrange(a, m)[1])
    return replace(f, da=max(da, f.da or da), a=min(al, f.a or al))


def altri_da_rivedere(conn, motivo_paypal):
    """Movimenti da rivedere che non hanno una scheda loro in Revisione (esercenti, bonifici, addebiti PayPal):
    la scheda "Altri movimenti". Ci sono anche i bonifici con terzi di cui la banca non riporta ne' nome ne'
    IBAN: senza un beneficiario la scheda dei bonifici non puo' raggrupparli, e resterebbero invisibili."""
    return [dict(r) for r in conn.execute(
        "SELECT a.mov_id, a.data, co.nome AS conto, a.importo_cent, a.natura, a.l1, a.l2, a.proposta, a.motivo, "
        "m.descrizione, m.tipo, a.chiave "
        "FROM analisi a JOIN movimenti m ON m.id = a.mov_id JOIN conti co ON co.id = a.conto_id "
        "LEFT JOIN trasferimenti t ON t.mov_id = a.mov_id "
        "WHERE a.da_rivedere = 1 AND a.componente = 'principale' AND coalesce(a.motivo, '') <> ? "
        "AND NOT (a.l2 IS NULL AND m.tipo IN ('pagamento_carta','addebito_sepa') AND coalesce(a.chiave, '') <> '') "
        "AND coalesce(t.stato, '') NOT IN ('proposto_proprio','spesa_familiare') "
        "AND NOT (coalesce(t.stato, '') = 'terzi' AND coalesce(a.chiave, '') <> '') "
        "AND coalesce(t.controparte_mov_id, '') = '' ORDER BY a.data DESC", [motivo_paypal])]
