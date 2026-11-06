"""Import dei file: lettura, deduplica, livello raw, saldi e quadratura."""
import hashlib
import json
import re
from collections import Counter

from . import configurazione as cfg
from .chiavi import chiave, togli_nomi
from .db import leggi_impostazione, ora, scrivi_impostazione, transazione
from .iban import normalizza as normalizza_iban
from .lettore import leggi
from .numeri import FormatoNonValido, a_centesimi, a_data


def _parti(nomi):
    parti = []
    for n in nomi or []:
        parti.extend(p for p in re.split(r"[\s,]+", n) if len(p) >= 2)
    return parti


def titolari(conn):
    """Parole dei nomi dei titolari dei conti."""
    return _parti(leggi_impostazione(conn, "titolari", []))


def familiari(conn):
    """Parole dei nomi dei familiari che versano sul conto comune senza essere titolari dei conti caricati."""
    return _parti(leggi_impostazione(conn, "familiari", []))


def nomi_riservati(conn):
    """Nomi da togliere dalle descrizioni e da non far mai uscire: titolari e familiari."""
    return titolari(conn) + familiari(conn)


def anteprima(conn, nome_file, dati: bytes):
    """Legge il file senza salvarlo: profilo, metadati, conteggi, conto suggerito."""
    fl = leggi(nome_file, nomi_riservati(conn), dati)
    m = fl.metadati
    suggerito = None
    for campo in ("iban", "numero"):
        if m.get(campo):
            valore = normalizza_iban(m[campo]) if campo == "iban" else m[campo].strip()
            r = conn.execute("SELECT id FROM conti WHERE %s = ?" % campo, (valore,)).fetchone()
            if r:
                suggerito = r[0]
                break
    if suggerito is None:
        r = conn.execute("SELECT id FROM conti WHERE profilo = ? ORDER BY id", (fl.profilo["id"],)).fetchall()
        if len(r) == 1:
            suggerito = r[0][0]
    if suggerito is None:
        # stessa banca e stesso tipo di conto, anche se esportato in un altro formato (es. ING in Excel e in CSV)
        # (vale anche per un conto registrato a mano con la banca indicata)
        r = conn.execute("SELECT id FROM conti WHERE lower(banca) = lower(?) AND tipo = ? AND chiuso_il IS NULL ORDER BY id",
                         (fl.profilo.get("banca"), fl.profilo.get("tipo_conto", "conto"))).fetchall()
        if len(r) == 1:
            suggerito = r[0][0]
    if suggerito is None and fl.profilo.get("banca"):
        # conto registrato a mano, ancora senza movimenti, con il nome della banca nel nome (es. "Conto ING")
        banca = fl.profilo["banca"].lower()
        r = [x[0] for x in conn.execute("SELECT id, nome FROM conti WHERE importato = 0 AND tipo = ? AND chiuso_il IS NULL",
                                        (fl.profilo.get("tipo_conto", "conto"),))
             if re.search(r"\b%s\b" % re.escape(banca), x[1].lower())]
        if len(r) == 1:
            suggerito = r[0]
    gia = conn.execute("SELECT count(*) FROM import_file WHERE hash_file = ?", (fl.hash_file,)).fetchone()[0]
    return fl, suggerito, gia > 0


def nome_conto_esistente(conn, nome):
    """Id del conto con lo stesso nome (maiuscole e spazi a parte), o None."""
    r = conn.execute("SELECT id FROM conti WHERE lower(trim(nome)) = lower(trim(?))", (nome or "",)).fetchone()
    return r[0] if r else None


def uso_conto(conn, conto_id):
    """Cosa sparirebbe eliminando il conto."""
    q = lambda sql: conn.execute(sql, (conto_id,)).fetchone()[0]
    return {"movimenti": q("SELECT count(*) FROM movimenti WHERE conto_id = ?"),
            "file": q("SELECT count(*) FROM import_file WHERE conto_id = ?"),
            "carte": [r[0] for r in conn.execute("SELECT nome FROM conti WHERE conto_addebito_id = ?", (conto_id,))]}


def elimina_conto(conn, conto_id):
    """Elimina un conto con i suoi movimenti, i file importati e le decisioni prese sui suoi movimenti
    (categorie, suddivisioni, etichette, natura della spesa, abbinamenti). Restano le conoscenze generali:
    esercenti confermati e regole, che valgono per tutti i conti. Il ricalcolo va lanciato dopo."""
    if not conn.execute("SELECT 1 FROM conti WHERE id = ?", (conto_id,)).fetchone():
        raise ValueError("conto %s non trovato" % conto_id)
    uso = uso_conto(conn, conto_id)
    ids = [r[0] for r in conn.execute("SELECT id FROM movimenti WHERE conto_id = ?", (conto_id,))]
    conn.execute("BEGIN")
    try:
        for i in range(0, len(ids), 500):
            parte = ids[i:i + 500]
            segni = ",".join("?" * len(parte))
            for tab in ("decisioni", "ripartizioni", "etichette_movimento", "attributi_movimento",
                        "destinazioni_movimenti"):
                conn.execute("DELETE FROM %s WHERE mov_id IN (%s)" % (tab, segni), parte)
            conn.execute("DELETE FROM abbinamenti_manuali WHERE mov_a IN (%s) OR mov_b IN (%s)" % (segni, segni), parte * 2)
            conn.execute("DELETE FROM abbinamenti_paypal WHERE addebito_id IN (%s) OR paypal_id IN (%s)" % (segni, segni),
                         parte * 2)
        for tab in ("analisi", "trasferimenti", "contenitori"):       # derivate: il ricalcolo le ricostruisce
            conn.execute("DELETE FROM %s WHERE mov_id IN (SELECT id FROM movimenti WHERE conto_id = ?)" % tab, (conto_id,))
        conn.execute("DELETE FROM movimenti WHERE conto_id = ?", (conto_id,))
        conn.execute("DELETE FROM righe_raw WHERE import_id IN (SELECT id FROM import_file WHERE conto_id = ?)", (conto_id,))
        conn.execute("DELETE FROM saldi WHERE conto_id = ?", (conto_id,))
        conn.execute("DELETE FROM import_file WHERE conto_id = ?", (conto_id,))
        conn.execute("UPDATE conti SET conto_addebito_id = NULL WHERE conto_addebito_id = ?", (conto_id,))
        conn.execute("DELETE FROM conti WHERE id = ?", (conto_id,))
        conn.execute("DELETE FROM candidati_invio WHERE stato <> 'inviato' AND chiave NOT IN "
                     "(SELECT chiave FROM movimenti WHERE chiave IS NOT NULL)")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return uso


def crea_conto(conn, nome, profilo, tipo, perimetro, banca=None, iban=None, numero=None, conto_addebito_id=None, importato=1):
    cur = conn.execute(
        "INSERT INTO conti(nome, banca, profilo, tipo, perimetro, iban, numero, conto_addebito_id, importato, creato_il) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (nome, banca, profilo, tipo, perimetro, normalizza_iban(iban) or None, numero or None, conto_addebito_id, importato, ora()))
    return cur.lastrowid


def _id_movimento(conto_id, m, occorrenza):
    if m.get("id_banca"):
        return "B-%d-%s" % (conto_id, m["id_banca"])
    base = "|".join(str(x) for x in (conto_id, m["data"], m.get("data_contabile"), m.get("importo_cent"),
                                      m.get("fee_cent"), m.get("tax_cent"), m.get("descrizione"), occorrenza))
    return "H-" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def _saldo(testo):
    if not testo:
        return None
    s = re.sub(r"[^\d,.\-+]", "", str(testo))
    try:
        return a_centesimi(s, "italiano")
    except FormatoNonValido:
        return None


def importa(conn, conto_id, nome_file, dati: bytes):
    """Importa un file su un conto. Restituisce un report (dict)."""
    fl = leggi(nome_file, nomi_riservati(conn), dati)
    conn.execute("BEGIN")
    try:
        # un conto registrato a mano (Conti e copertura) diventa importato al primo file: prende formato e banca
        conn.execute("UPDATE conti SET importato = 1, profilo = coalesce(profilo, ?), banca = coalesce(banca, ?) "
                     "WHERE id = ?", (fl.profilo["id"], fl.profilo.get("banca"), conto_id))
        conto = conn.execute("SELECT * FROM conti WHERE id = ?", (conto_id,)).fetchone()
        cur = conn.execute(
            "INSERT INTO import_file(conto_id, nome_file, hash_file, profilo, importato_il, righe_dati, righe_scartate) "
            "VALUES (?,?,?,?,?,?,?)",
            (conto_id, nome_file, fl.hash_file, fl.profilo["id"], ora(), len(fl.movimenti), len(fl.scartate)))
        imp_id = cur.lastrowid
        occ = Counter()
        nuove = dup = 0
        date = []
        for m in fl.movimenti:
            firma = (m["data"], m.get("data_contabile"), m.get("importo_cent"), m.get("descrizione"))
            occ[firma] += 1
            mid = _id_movimento(conto_id, m, occ[firma])
            raw = conn.execute("INSERT INTO righe_raw(import_id, n_riga, contenuto, mov_id) VALUES (?,?,?,?)",
                               (imp_id, m["n_riga"], json.dumps(m["grezzo"], ensure_ascii=False), mid)).lastrowid
            date.append(m["data"])
            if conn.execute("SELECT 1 FROM movimenti WHERE id = ?", (mid,)).fetchone():
                dup += 1
                continue
            conn.execute(
                "INSERT INTO movimenti(id, conto_id, import_id, raw_id, data, data_contabile, data_valuta, data_registrazione, ora, "
                "importo_cent, fee_cent, tax_cent, importo_orig, divisa_orig, cambio, codice, tipo, descrizione, esercente, "
                "intermediario, chiave, paese, mcc, controparte_nome, controparte_iban, isin, nome_titolo, quantita, prezzo, ciclo_carta) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, conto_id, imp_id, raw, m["data"], m.get("data_contabile"), m.get("data_valuta"), m.get("data_registrazione"),
                 m.get("ora"), m.get("importo_cent"), m.get("fee_cent", 0), m.get("tax_cent", 0), m.get("importo_orig"),
                 m.get("divisa_orig"), m.get("cambio"), m.get("codice"), m["tipo"], m.get("descrizione"), m.get("esercente"),
                 m.get("intermediario"), m.get("chiave"), m.get("paese"), m.get("mcc"), m.get("controparte_nome"),
                 m.get("controparte_iban"), m.get("isin"), m.get("nome_titolo"), m.get("quantita"), m.get("prezzo"),
                 m.get("ciclo_carta")))
            nuove += 1

        md = fl.metadati
        avvisi = list(fl.avvisi)
        titolare_aggiunto = aggiungi_titolare(conn, md.get("titolare") or "")
        quadratura = "non disponibile"
        # aggiorna anagrafica con i metadati se mancanti
        if md.get("iban") and not conto["iban"]:
            conn.execute("UPDATE conti SET iban = ? WHERE id = ?", (normalizza_iban(md["iban"]), conto_id))
        if md.get("numero") and not conto["numero"]:
            conn.execute("UPDATE conti SET numero = ? WHERE id = ?", (md["numero"].strip(), conto_id))
        # saldi e quadratura
        somma = sum((m.get("importo_cent") or 0) + m.get("fee_cent", 0) + m.get("tax_cent", 0) for m in fl.movimenti)
        si, sf = _saldo(md.get("saldo_iniziale")), _saldo(md.get("saldo_finale"))
        if si is not None and sf is not None:
            quadratura = "OK" if si + somma == sf else "NON quadra (differenza %+.2f EUR)" % ((sf - si - somma) / 100)
            per_da = a_data(md["periodo_da"], "%d/%m/%Y")[0] if md.get("periodo_da") else min(date, default=None)
            per_a = a_data(md["periodo_a"], "%d/%m/%Y")[0] if md.get("periodo_a") else max(date, default=None)
            conn.execute("INSERT INTO saldi VALUES (?,?,?,?,?)", (conto_id, per_da, si, "iniziale", imp_id))
            conn.execute("INSERT INTO saldi VALUES (?,?,?,?,?)", (conto_id, per_a, sf, "finale", imp_id))
        elif md.get("saldo_alla_data") and md.get("data_saldo"):
            s_cent = _saldo(md["saldo_alla_data"])
            d_saldo = a_data(md["data_saldo"], "%d/%m/%Y")[0]
            prec = conn.execute("SELECT data, saldo_cent FROM saldi WHERE conto_id = ? AND data < ? ORDER BY data DESC LIMIT 1",
                                (conto_id, d_saldo)).fetchone()
            conn.execute("INSERT INTO saldi VALUES (?,?,?,?,?)", (conto_id, d_saldo, s_cent, "alla_data", imp_id))
            if prec:
                tra = conn.execute(
                    "SELECT coalesce(sum(coalesce(importo_cent,0) + fee_cent + tax_cent), 0) FROM movimenti "
                    "WHERE conto_id = ? AND coalesce(data_contabile, data) > ? AND coalesce(data_contabile, data) <= ?",
                    (conto_id, prec[0], d_saldo)).fetchone()[0]
                quadratura = ("OK rispetto al saldo del %s" % prec[0] if prec[1] + tra == s_cent
                              else "NON quadra rispetto al saldo del %s" % prec[0])
            else:
                quadratura = "saldo registrato: si verifica dal prossimo import"
        conn.execute("UPDATE import_file SET nuove=?, duplicate=?, data_min=?, data_max=?, quadratura=?, avvisi=? WHERE id=?",
                     (nuove, dup, min(date, default=None), max(date, default=None), quadratura,
                      json.dumps(avvisi, ensure_ascii=False), imp_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"import_id": imp_id, "profilo": fl.profilo["nome"], "righe": len(fl.movimenti), "nuove": nuove,
            "duplicate": dup, "scartate": fl.scartate, "quadratura": quadratura, "avvisi": avvisi,
            "periodo": (min(date, default=None), max(date, default=None)),
            "titolare_aggiunto": md.get("titolare") if titolare_aggiunto else None,
            "nomi_ripuliti": ripulisci_nomi(conn) if titolare_aggiunto else 0}


def aggiungi_titolare(conn, nome):
    """Aggiunge un titolare letto dai metadati del file (es. 'Intestazione' di ING), se non c'e' gia'."""
    nome = " ".join(str(nome).split())
    if len(nome) < 3:
        return False
    attuali = leggi_impostazione(conn, "titolari", []) or []
    if any(nome.upper() == a.upper() for a in attuali):
        return False
    scrivi_impostazione(conn, "titolari", attuali + [nome.title()])
    return True


def ripulisci_nomi(conn):
    """Toglie i nomi dei titolari in testa agli esercenti gia' importati: serve quando i titolari
    vengono indicati dopo l'import. Restituisce il numero di movimenti corretti."""
    nomi = nomi_riservati(conn)
    if not nomi:
        return 0
    with transazione(conn):
        return _ripulisci_nomi(conn, nomi)


def _ripulisci_nomi(conn, nomi):
    profili = cfg.profili()
    n = 0
    for conto in conn.execute("SELECT id, profilo FROM conti WHERE profilo IS NOT NULL").fetchall():
        tipi = (profili.get(conto["profilo"]) or {}).get("titolari_davanti_esercente", [])
        if not tipi:
            continue
        for m in conn.execute("SELECT id, esercente FROM movimenti WHERE conto_id = ? AND esercente IS NOT NULL AND tipo IN (%s)"
                              % ",".join("?" * len(tipi)), (conto["id"], *tipi)).fetchall():
            nuovo = togli_nomi(m["esercente"], nomi) or None
            if nuovo != m["esercente"]:
                conn.execute("UPDATE movimenti SET esercente = ?, chiave = ? WHERE id = ?", (nuovo, chiave(nuovo), m["id"]))
                n += 1
    conn.execute("DELETE FROM candidati_invio WHERE stato <> 'inviato' AND chiave NOT IN "
                 "(SELECT chiave FROM movimenti WHERE chiave IS NOT NULL)")
    return n


def elenco_import(conn):
    return [dict(r) for r in conn.execute(
        "SELECT f.id, f.importato_il, f.nome_file, c.nome AS conto, f.data_min, f.data_max, f.nuove, f.duplicate, "
        "f.quadratura, f.conto_id FROM import_file f JOIN conti c ON c.id = f.conto_id ORDER BY f.id DESC")]


def annulla_import(conn, import_id, elimina_conto=False):
    """Toglie un import. I movimenti presenti anche in un altro file dello stesso conto restano,
    collegati a quel file; gli altri vengono cancellati con le decisioni prese su di loro.
    Il ricalcolo va lanciato dopo."""
    f = conn.execute("SELECT * FROM import_file WHERE id = ?", (import_id,)).fetchone()
    if f is None:
        raise ValueError("import %s non trovato" % import_id)
    conto_id = f["conto_id"]
    conn.execute("BEGIN")
    try:
        tolti = mantenuti = 0
        for (mid,) in conn.execute("SELECT id FROM movimenti WHERE import_id = ?", (import_id,)).fetchall():
            alt = conn.execute("SELECT id, import_id FROM righe_raw WHERE mov_id = ? AND import_id <> ? "
                               "ORDER BY import_id LIMIT 1", (mid, import_id)).fetchone()
            if alt:
                conn.execute("UPDATE movimenti SET import_id = ?, raw_id = ? WHERE id = ?", (alt["import_id"], alt["id"], mid))
                mantenuti += 1
            else:
                conn.execute("DELETE FROM movimenti WHERE id = ?", (mid,))
                conn.execute("DELETE FROM decisioni WHERE mov_id = ?", (mid,))
                conn.execute("DELETE FROM ripartizioni WHERE mov_id = ?", (mid,))
                conn.execute("DELETE FROM etichette_movimento WHERE mov_id = ?", (mid,))
                conn.execute("DELETE FROM attributi_movimento WHERE mov_id = ?", (mid,))
                conn.execute("DELETE FROM destinazioni_movimenti WHERE mov_id = ?", (mid,))
                conn.execute("DELETE FROM abbinamenti_manuali WHERE mov_a = ? OR mov_b = ?", (mid, mid))
                conn.execute("DELETE FROM abbinamenti_paypal WHERE addebito_id = ? OR paypal_id = ?", (mid, mid))
                tolti += 1
        conn.execute("DELETE FROM saldi WHERE import_id = ?", (import_id,))
        conn.execute("DELETE FROM righe_raw WHERE import_id = ?", (import_id,))
        conn.execute("DELETE FROM import_file WHERE id = ?", (import_id,))
        # file importati prima della 0.1.1: le righe duplicate non sono collegate ai movimenti
        da_ricaricare = [r[0] for r in conn.execute(
            "SELECT DISTINCT f.nome_file FROM import_file f WHERE f.conto_id = ? AND f.data_max >= ? AND f.data_min <= ? "
            "AND EXISTS (SELECT 1 FROM righe_raw r WHERE r.import_id = f.id AND r.mov_id IS NULL)",
            (conto_id, f["data_min"] or "", f["data_max"] or "9999"))]
        conto_eliminato = None
        if elimina_conto and not conn.execute("SELECT 1 FROM import_file WHERE conto_id = ?", (conto_id,)).fetchone():
            nome = conn.execute("SELECT nome FROM conti WHERE id = ?", (conto_id,)).fetchone()[0]
            conn.execute("UPDATE conti SET conto_addebito_id = NULL WHERE conto_addebito_id = ?", (conto_id,))
            conn.execute("DELETE FROM saldi WHERE conto_id = ?", (conto_id,))
            conn.execute("DELETE FROM conti WHERE id = ?", (conto_id,))
            conto_eliminato = nome
        conn.execute("DELETE FROM candidati_invio WHERE stato <> 'inviato' AND chiave NOT IN "
                     "(SELECT chiave FROM movimenti WHERE chiave IS NOT NULL)")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"file": f["nome_file"], "tolti": tolti, "mantenuti": mantenuti,
            "conto_eliminato": conto_eliminato, "da_ricaricare": da_ricaricare}
