"""Azioni dell'utente sulla conoscenza persistente. Ogni azione e' seguita da un ricalcolo."""
import csv
import io
import re

from . import configurazione as cfg
from .db import ora, transazione


def decidi_movimento(conn, mov_id, cat=None, natura=None, nota=None):
    """cat: (categoria, sottocategoria), oppure None per decidere solo la natura. Con una categoria e una natura
    che conta, la natura viene dalla sezione della categoria."""
    cat = cfg.come_cat(cat)
    if cat and natura:
        natura = cfg.natura_da_categoria(cat, natura)
    conn.execute("INSERT INTO decisioni(mov_id, l1, l2, natura, nota, decisa_il) VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(mov_id) DO UPDATE SET l1=excluded.l1, l2=excluded.l2, natura=excluded.natura, "
                 "nota=excluded.nota, decisa_il=excluded.decisa_il",
                 (mov_id, cat[0] if cat else None, cat[1] if cat else None, natura, nota, ora()))


def annulla_decisione(conn, mov_id):
    conn.execute("DELETE FROM decisioni WHERE mov_id = ?", (mov_id,))


def crea_regola(conn, pattern, cat, campo="chiave", modo="esatto", natura=None, priorita=10):
    if modo == "regex":
        re.compile(pattern)
    cat = cfg.come_cat(cat, obbligatoria=True)
    conn.execute("INSERT INTO regole_utente(campo, modo, pattern, l1, l2, natura, priorita, creata_il) "
                 "VALUES (?,?,?,?,?,?,?,?)", (campo, modo, pattern, cat[0], cat[1], natura, priorita, ora()))


def elimina_regola(conn, regola_id):
    conn.execute("DELETE FROM regole_utente WHERE id = ?", (regola_id,))


def conferma_esercente(conn, chiave, cat):
    """Categoria (l1, l2) confermata per un esercente: vale come regola e alimenta la somiglianza."""
    cat = cfg.come_cat(cat, obbligatoria=True)
    conn.execute("INSERT INTO cache_esercenti(chiave, l1, l2, motore, confidenza, esercente_normalizzato, aggiornata_il) "
                 "VALUES (?,?,?,?,?,?,?) ON CONFLICT(chiave) DO UPDATE SET l1=excluded.l1, "
                 "l2=excluded.l2, motore='utente', confidenza=1.0, aggiornata_il=excluded.aggiornata_il, modello=NULL",
                 (chiave, cat[0], cat[1], "utente", 1.0, None, ora()))


def aggiorna_esercente(conn, chiave, cat=None, nome=None):
    """Modifica fatta a mano su un esercente gia' classificato: diventa una conferma dell'utente."""
    r = conn.execute("SELECT l1, l2, esercente_normalizzato FROM cache_esercenti WHERE chiave = ?", (chiave,)).fetchone()
    cat = cfg.come_cat(cat or ((r["l1"], r["l2"]) if r and r["l2"] else None), obbligatoria=True)
    nome = nome if nome is not None else (r["esercente_normalizzato"] if r else None)
    conn.execute("INSERT INTO cache_esercenti(chiave, l1, l2, motore, confidenza, esercente_normalizzato, aggiornata_il) "
                 "VALUES (?,?,?,?,?,?,?) ON CONFLICT(chiave) DO UPDATE SET l1=excluded.l1, "
                 "l2=excluded.l2, motore='utente', confidenza=1.0, esercente_normalizzato=excluded.esercente_normalizzato, "
                 "aggiornata_il=excluded.aggiornata_il, modello=NULL", (chiave, cat[0], cat[1], "utente", 1.0, nome or None, ora()))


def esercenti_classificati(conn):
    """Esercenti con una categoria in cache (modello locale, Claude o confermata da te), con quanti movimenti
    ne dipendono. Sono quelli che si possono rimettere in classificazione."""
    return [dict(r) for r in conn.execute(
        "SELECT ce.chiave, ce.l1, ce.l2, ce.motore, ce.modello, ce.confidenza, ce.esercente_normalizzato, ce.aggiornata_il, "
        "count(a.id) AS movimenti FROM cache_esercenti ce "
        "LEFT JOIN analisi a ON a.chiave = ce.chiave AND a.componente = 'principale' "
        "GROUP BY ce.chiave ORDER BY ce.aggiornata_il DESC, ce.chiave")]


def riapri_esercenti(conn, chiavi):
    """Toglie la categoria automatica (o confermata) agli esercenti indicati e li rimette tra quelli da
    classificare: tornano disponibili sia per il modello locale sia per la coda di Claude, anche se gia'
    inviati. Le risposte precedenti restano nel registro delle richieste. Il ricalcolo va lanciato dopo."""
    chiavi = [k for k in dict.fromkeys(chiavi) if k]
    with transazione(conn):
        for i in range(0, len(chiavi), 500):
            parte = chiavi[i:i + 500]
            segni = ",".join("?" * len(parte))
            conn.execute("DELETE FROM cache_esercenti WHERE chiave IN (%s)" % segni, parte)
            conn.execute("DELETE FROM candidati_invio WHERE chiave IN (%s)" % segni, parte)
    return len(chiavi)


def decidi_destinazione(conn, beneficiario, esito):
    with transazione(conn):
        conn.execute("INSERT INTO destinazioni VALUES (?,?,?) ON CONFLICT(beneficiario) DO UPDATE SET "
                     "esito=excluded.esito, deciso_il=excluded.deciso_il", (beneficiario, esito, ora()))
        conn.execute("DELETE FROM destinazioni_conti WHERE beneficiario = ?", (beneficiario,))


def decidi_conto_destinazione(conn, conto_id, beneficiario=None, mov_ids=None):
    """Bonifici a proprio nome senza IBAN della controparte: dall'altra parte c'e' un conto gia' censito.
    Con `beneficiario` vale per tutti i suoi movimenti (e sostituisce la risposta generica), con `mov_ids` solo
    per quei movimenti. Il ricalcolo lo tratta come se l'IBAN fosse quello del conto: cerca la contropartita li'."""
    with transazione(conn):
        if beneficiario:
            conn.execute("DELETE FROM destinazioni WHERE beneficiario = ?", (beneficiario,))
            conn.execute("INSERT INTO destinazioni_conti VALUES (?,?,?) ON CONFLICT(beneficiario) DO UPDATE SET "
                         "conto_id=excluded.conto_id, deciso_il=excluded.deciso_il", (beneficiario, int(conto_id), ora()))
        for mid in mov_ids or []:
            conn.execute("INSERT INTO destinazioni_movimenti VALUES (?,?,?) ON CONFLICT(mov_id) DO UPDATE SET "
                         "conto_id=excluded.conto_id, deciso_il=excluded.deciso_il", (mid, int(conto_id), ora()))


def riconduci_paypal(conn, addebito_id, paypal_id):
    """Addebito PayPal su un conto o una carta ricondotto a un pagamento del report PayPal (l'addebito diventa un
    contenitore, resta la spesa del report). paypal_id None: l'addebito non e' nel report, e' una spesa normale.
    Un pagamento del report copre un solo addebito: se era gia' ricondotto a un altro, quella scelta si toglie e
    l'altro addebito torna da controllare."""
    with transazione(conn):
        if paypal_id is not None:
            conn.execute("DELETE FROM abbinamenti_paypal WHERE paypal_id = ? AND addebito_id <> ?", (paypal_id, addebito_id))
        conn.execute("INSERT INTO abbinamenti_paypal VALUES (?,?,?) ON CONFLICT(addebito_id) DO UPDATE SET "
                     "paypal_id=excluded.paypal_id, deciso_il=excluded.deciso_il", (addebito_id, paypal_id, ora()))


def togli_riconduzione_paypal(conn, addebito_id):
    conn.execute("DELETE FROM abbinamenti_paypal WHERE addebito_id = ?", (addebito_id,))


def decidi_abbinamento(conn, mov_a, mov_b, esito):
    a, b = sorted((mov_a, mov_b))
    conn.execute("INSERT INTO abbinamenti_manuali VALUES (?,?,?,?) ON CONFLICT(mov_a, mov_b) DO UPDATE SET "
                 "esito=excluded.esito, deciso_il=excluded.deciso_il", (a, b, esito, ora()))


def aggiorna_conto(conn, conto_id, **campi):
    """Aggiorna i campi ammessi di un conto. L'IBAN si salva senza spazi e in maiuscolo; se non e' coerente
    solleva ValueError e non scrive nulla."""
    from .iban import controlla as controlla_iban
    ammessi = {"nome", "tipo", "perimetro", "iban", "numero", "conto_addebito_id", "chiuso_il", "banca"}
    if "iban" in campi:
        iban, errore = controlla_iban(campi["iban"])
        attuale = conn.execute("SELECT iban FROM conti WHERE id = ?", (conto_id,)).fetchone()
        invariato = attuale is not None and iban == controlla_iban(attuale[0])[0]
        if errore and not invariato:       # un IBAN gia' salvato e non toccato non blocca le altre modifiche
            raise ValueError("IBAN non valido: %s" % errore)
        campi["iban"] = iban
    with transazione(conn):
        for k, v in campi.items():
            if k in ammessi:
                conn.execute("UPDATE conti SET %s = ? WHERE id = ?" % k, (v if v != "" else None, conto_id))


def candidato(conn, chiave, stato=None, testo=None):
    if testo is not None:
        # il testo corretto a mano si salva gia' normalizzato come una chiave (cifre -> #): e' quello che parte.
        # Svuotato, torna la chiave dell'esercente
        from .privacy import testo_in_uscita
        testo = testo_in_uscita(testo) or chiave
        conn.execute("UPDATE candidati_invio SET testo=?, modificato=1, aggiornato_il=? WHERE chiave=?", (testo, ora(), chiave))
    if stato:
        conn.execute("UPDATE candidati_invio SET stato=?, aggiornato_il=? WHERE chiave=?", (stato, ora(), chiave))


# ---------------------------------------------------------------------------
# Export del livello gold
# ---------------------------------------------------------------------------
GOLD = """
SELECT a.id AS riga_id, a.mov_id, a.componente, a.data, a.mese, c.nome AS conto, c.banca, c.tipo AS tipo_conto,
       a.perimetro, a.importo_cent, replace(printf('%.2f', a.importo_cent / 100.0), '.', ',') AS importo_eur, a.natura, a.l1, a.l2, a.fonte,
       a.confidenza, a.da_rivedere, m.tipo AS tipo_movimento, m.esercente, m.paese, m.mcc
FROM analisi a JOIN conti c ON c.id = a.conto_id JOIN movimenti m ON m.id = a.mov_id
ORDER BY a.data, a.id
"""


def export_csv(conn):
    righe = conn.execute(GOLD).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    if righe:
        w.writerow(righe[0].keys())
    for r in righe:
        w.writerow([("%.2f" % v).replace(".", ",") if isinstance(v, float) else v for v in r])
    return buf.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# Spese pagate tramite un familiare: restituzione e suddivisione dei bonifici
# ---------------------------------------------------------------------------
def _euro(cent):
    from .numeri import euro
    return euro(cent)


RESTITUZIONE = "Contributo ricevuto"     # una restituzione di contributo e' un contributo ricevuto negativo


def decidi_restituzione(conn, mov_id):
    """Il bonifico restituisce un contributo: riduce i contributi ricevuti invece di essere una spesa."""
    decidi_movimento(conn, mov_id, RESTITUZIONE, natura="entrata")


def salva_ripartizione(conn, mov_id, parti):
    """parti: lista di (importo_cent, categoria (l1, l2), causale). Le parti devono essere almeno due, avere lo stesso segno del
    movimento e sommare esattamente al suo importo; ogni sottocategoria deve esistere nella tassonomia.
    Una parte con sottocategoria 'Contributo ricevuto' e' una restituzione di contributo."""
    m = conn.execute("SELECT importo_cent FROM movimenti WHERE id = ?", (mov_id,)).fetchone()
    if m is None:
        raise ValueError("movimento non trovato")
    totale = m["importo_cent"]
    if len(parti) < 2:
        raise ValueError("servono almeno due parti")
    valide = []
    for n, (importo, cat, c) in enumerate(parti, 1):
        importo = int(importo)
        if importo == 0:
            raise ValueError("la parte %d ha importo zero" % n)
        if (importo < 0) != (totale < 0):
            raise ValueError("la parte %d ha segno diverso dal bonifico" % n)
        try:
            cat = cfg.come_cat(cat, obbligatoria=True)
        except ValueError:
            raise ValueError("la parte %d non ha una sottocategoria valida" % n)
        valide.append((importo, cat, (c or "").strip() or None))
    parti = valide
    somma = sum(p[0] for p in parti)
    if somma != totale:
        scarto = abs(totale) - abs(somma)
        raise ValueError("le parti sommano a %s, il bonifico e' di %s: %s" % (
            _euro(abs(somma)), _euro(abs(totale)),
            ("mancano %s" % _euro(scarto)) if scarto > 0 else ("ci sono %s in piu'" % _euro(-scarto))))
    with transazione(conn):
        conn.execute("DELETE FROM ripartizioni WHERE mov_id = ?", (mov_id,))
        conn.executemany("INSERT INTO ripartizioni(mov_id, n, importo_cent, l1, l2, causale, creata_il) VALUES (?,?,?,?,?,?,?)",
                         [(mov_id, n, i, cat[0], cat[1], c, ora()) for n, (i, cat, c) in enumerate(parti, 1)])


def elimina_ripartizione(conn, mov_id):
    conn.execute("DELETE FROM ripartizioni WHERE mov_id = ?", (mov_id,))


def ripartizione(conn, mov_id):
    return [dict(r) for r in conn.execute("SELECT n, importo_cent, l1, l2, causale FROM ripartizioni WHERE mov_id = ? ORDER BY n",
                                          (mov_id,))]


def conta_regola(conn, pattern, campo="descrizione", modo="contiene"):
    """Quanti movimenti toccherebbe una regola, prima di crearla."""
    col = "m.descrizione" if campo == "descrizione" else "m.chiave"
    if modo == "contiene":
        return conn.execute("SELECT count(*) FROM movimenti m WHERE upper(%s) LIKE ?" % col,
                            ("%" + pattern.upper() + "%",)).fetchone()[0]
    rx = re.compile(pattern if modo == "regex" else "^" + re.escape(pattern) + "$", re.I)
    return sum(1 for (t,) in conn.execute("SELECT %s FROM movimenti m" % col) if t and rx.search(t))


def causale_bonifico(descrizione, nome=None):
    """La causale scritta da chi ha disposto il bonifico, senza diciture della banca, nome e codici.
    'BON. ISTANTANEO A DEBITO Mario Rossi Rif. 0001...-008 rata auto' -> 'rata auto';
    'BONIFICO A DEBITO A FAVORE DI Mario Rossi telepass RIF=0303...IT' -> 'telepass'."""
    t = descrizione or ""
    if nome:
        ultimo = None
        for p in re.split(r"[\s,]+", nome):
            if len(p) >= 2:
                for mt in re.finditer(r"\b%s\b" % re.escape(p), t, re.I):
                    ultimo = max(ultimo or 0, mt.end())
        if ultimo:
            t = t[ultimo:]
    t = re.sub(r"(?i)^.*?\bA (?:DEBITO|CREDITO)(?: A FAVORE DI)?\b", "", t) if not nome else t
    t = re.sub(r"(?i)\bRIF\b[.=:]?", " ", t)
    t = re.sub(r"\S*\d\S*", " ", t)                  # codici di riferimento e numeri di operazione
    return re.sub(r"\s+", " ", t).strip(" *-.,;")


def spese_tramite_familiari(conn):
    """Bonifici dal conto comune ai familiari, uno per riga, con la categoria attuale e le eventuali parti.
    'familiare' e' il nome indicato in Impostazioni: negli estratti la causale e' spesso attaccata al nome del
    destinatario ('Mario Rossi rata auto'), quindi il testo grezzo non basta a raggruppare."""
    from .db import leggi_impostazione
    nomi = [n for n in (leggi_impostazione(conn, "familiari", []) or []) if n.strip()]

    def chi(testo):
        t = (testo or "").upper()
        migliore = None
        for n in nomi:
            parti = [p for p in re.split(r"[\s,]+", n.upper()) if len(p) >= 2]
            trovate = sum(1 for p in parti if re.search(r"\b%s\b" % re.escape(p), t))
            if trovate and (migliore is None or trovate > migliore[0]):
                migliore = (trovate, n)
        return migliore[1] if migliore else (testo or "")
    out = [dict(r) for r in conn.execute(
        "SELECT m.id AS mov_id, m.data, co.nome AS conto, m.controparte_nome AS familiare, m.importo_cent, m.descrizione, "
        "t.beneficiario, a.l1, a.l2, a.natura, a.fonte, a.proposta, "
        "(SELECT count(*) FROM ripartizioni r WHERE r.mov_id = m.id) AS parti "
        "FROM trasferimenti t JOIN movimenti m ON m.id = t.mov_id JOIN conti co ON co.id = m.conto_id "
        "LEFT JOIN analisi a ON a.mov_id = m.id AND a.componente = 'principale' "
        "WHERE t.stato = 'spesa_familiare' ORDER BY m.data DESC")]
    for r in out:
        r["controparte"] = r["familiare"]
        r["familiare"] = chi(r["familiare"])
        r["causale"] = causale_bonifico(r["descrizione"], r["familiare"])
    return out
