"""Natura della spesa (attributo) ed etichette.

Natura della spesa: un solo valore per ogni uscita (necessaria, svago, superflua). Viene dalla sottocategoria
(valori di partenza in config/attributi.yaml, modifiche dell'utente nell'impostazione "attributo_l2") e si
puo' cambiare sul singolo movimento (tabella attributi_movimento). Si ricalcola nella colonna analisi.attributo.

Etichette: nomi liberi (es. "Vacanza Puglia 2025", "Casa Macherio") attaccati ai movimenti, zero o piu' per
movimento, per sommare spese di categorie diverse legate alla stessa cosa.

Tutto sta in tabelle proprie: spegnendo le sezioni in Impostazioni (sezione_etichette, sezione_natura) i dati
restano e il resto dell'app non cambia.
"""
from . import configurazione as cfg
from .db import leggi_impostazione, ora, scrivi_impostazione, transazione

CHIAVE_PREDEFINITI = "attributo_l2"


def etichette_attive(conn):
    """Sezione Etichette accesa (Impostazioni > Sezioni facoltative)."""
    return bool(leggi_impostazione(conn, "sezione_etichette", leggi_impostazione(conn, "funzioni_attributi", True)))


def natura_attiva(conn):
    """Natura della spesa accesa (Impostazioni > Sezioni facoltative)."""
    return bool(leggi_impostazione(conn, "sezione_natura", leggi_impostazione(conn, "funzioni_attributi", True)))


def attiva(conn):
    """Almeno una delle due: in Movimenti compare il riquadro per i movimenti selezionati."""
    return etichette_attive(conn) or natura_attiva(conn)


def valori():
    """{codice: etichetta}, nell'ordine di config/attributi.yaml."""
    return cfg.attributi()["valori"]


def predefiniti(conn):
    """{(l1, l2): codice} in uso: quelli di partenza (config/attributi.yaml, per sottocategoria) con sopra le
    modifiche dell'utente, salvate per categoria e sottocategoria ('' = nessun valore)."""
    tax = cfg.tassonomia()
    out = {}
    for l2, v in cfg.attributi()["predefiniti"].items():
        cat = tax.risolvi(l2)
        if cat:
            out[cat] = v
    for k, v in (leggi_impostazione(conn, CHIAVE_PREDEFINITI, {}) or {}).items():
        cat = cfg.da_chiave_cat(k)
        if cat[0] is None:                       # salvato da versioni precedenti, solo sottocategoria
            cat = tax.risolvi(cat[1])
        if not cat:
            continue
        if v:
            out[tuple(cat)] = v
        else:
            out.pop(tuple(cat), None)
    return out


def imposta_predefinito(conn, cat, valore):
    """cat: (l1, l2)."""
    cat = cfg.come_cat(cat, obbligatoria=True)
    if valore and valore not in valori():
        raise ValueError("valore non ammesso: %s" % valore)
    mod = leggi_impostazione(conn, CHIAVE_PREDEFINITI, {}) or {}
    mod.pop(cat[1], None)                        # eventuale voce salvata con la sola sottocategoria
    mod[cfg.chiave_cat(cat)] = valore or ""
    scrivi_impostazione(conn, CHIAVE_PREDEFINITI, mod)


def per_movimento(conn):
    return {r[0]: r[1] for r in conn.execute("SELECT mov_id, valore FROM attributi_movimento")}


def imposta_movimento(conn, mov_ids, valore):
    """Natura della spesa scelta sui movimenti indicati; None la toglie (torna quella della sottocategoria)."""
    if valore and valore not in valori():
        raise ValueError("valore non ammesso: %s" % valore)
    with transazione(conn):
        for mid in mov_ids:
            if valore:
                conn.execute("INSERT INTO attributi_movimento VALUES (?,?,?) ON CONFLICT(mov_id) DO UPDATE SET "
                             "valore = excluded.valore, deciso_il = excluded.deciso_il", (mid, valore, ora()))
            else:
                conn.execute("DELETE FROM attributi_movimento WHERE mov_id = ?", (mid,))


# --- etichette -------------------------------------------------------------------------------
def etichette(conn):
    return [dict(r) for r in conn.execute(
        "SELECT e.id, e.nome, count(em.mov_id) AS movimenti FROM etichette e "
        "LEFT JOIN etichette_movimento em ON em.etichetta_id = e.id GROUP BY e.id ORDER BY e.nome")]


def crea_etichetta(conn, nome):
    nome = " ".join((nome or "").split())
    if not nome:
        raise ValueError("il nome dell'etichetta e' vuoto")
    r = conn.execute("SELECT id FROM etichette WHERE nome = ?", (nome,)).fetchone()
    if r:
        return r[0]
    return conn.execute("INSERT INTO etichette(nome, creata_il) VALUES (?, ?)", (nome, ora())).lastrowid


def rinomina_etichetta(conn, etichetta_id, nome):
    nome = " ".join((nome or "").split())
    if not nome:
        raise ValueError("il nome dell'etichetta e' vuoto")
    altra = conn.execute("SELECT id FROM etichette WHERE nome = ? AND id <> ?", (nome, etichetta_id)).fetchone()
    if altra:
        raise ValueError("esiste gia' un'etichetta \"%s\"" % nome)
    conn.execute("UPDATE etichette SET nome = ? WHERE id = ?", (nome, etichetta_id))


def elimina_etichetta(conn, etichetta_id):
    with transazione(conn):
        conn.execute("DELETE FROM etichette_movimento WHERE etichetta_id = ?", (etichetta_id,))
        conn.execute("DELETE FROM etichette WHERE id = ?", (etichetta_id,))


def assegna(conn, mov_ids, etichetta_id):
    with transazione(conn):
        conn.executemany("INSERT OR IGNORE INTO etichette_movimento VALUES (?, ?)", [(m, etichetta_id) for m in mov_ids])
    return len(mov_ids)


def togli(conn, mov_ids, etichetta_id):
    with transazione(conn):
        conn.executemany("DELETE FROM etichette_movimento WHERE mov_id = ? AND etichetta_id = ?",
                         [(m, etichetta_id) for m in mov_ids])


def etichette_di(conn, mov_ids):
    """{mov_id: [nomi]} per i movimenti indicati."""
    if not mov_ids:
        return {}
    out = {}
    for i in range(0, len(mov_ids), 500):
        parte = list(mov_ids[i:i + 500])
        for r in conn.execute("SELECT em.mov_id, e.nome FROM etichette_movimento em JOIN etichette e ON e.id = em.etichetta_id "
                              "WHERE em.mov_id IN (%s) ORDER BY e.nome" % ",".join("?" * len(parte)), parte):
            out.setdefault(r[0], []).append(r[1])
    return out


def movimenti_nel_periodo(conn, da, a, conti=None, solo_uscite=True):
    """Movimenti tra due date (estremi inclusi), per assegnare un'etichetta in blocco (es. una vacanza)."""
    cond, par = ["m.data >= ?", "m.data <= ?"], [da, a]
    if conti:
        cond.append("m.conto_id IN (%s)" % ",".join("?" * len(conti)))
        par.extend(conti)
    if solo_uscite:
        cond.append("EXISTS (SELECT 1 FROM analisi x WHERE x.mov_id = m.id AND x.natura IN ('uscita','rimborso'))")
    return [r[0] for r in conn.execute("SELECT m.id FROM movimenti m WHERE " + " AND ".join(cond) + " ORDER BY m.data", par)]
