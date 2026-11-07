"""Classificazione degli esercenti con il modello locale (Ollama) e con Claude, previa approvazione.

Il contratto di risposta e' lo stesso per entrambi i motori. Ogni risposta viene validata:
categoria presente in tassonomia e nome esercente composto da parole presenti nell'input.
"""
import json
import re

from . import configurazione as cfg
from .chiavi import contenimento
from .db import leggi_impostazione, ora, transazione
from .privacy import controlla

def schema_risposta():
    """Schema JSON della risposta, uguale per modello locale e Claude. Le sottocategorie sono un elenco
    chiuso: il motore non puo' inventarne. Compatibile con le risposte strutturate dell'API di Claude
    (additionalProperties false su ogni oggetto, nessun vincolo numerico)."""
    voci = [cfg.etichetta(c) for c in cfg.tassonomia().per_sezione("uscite")]
    return {
        "type": "object",
        "properties": {
            "risultati": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "idx": {"type": "integer"},
                        "categoria": {"type": "string", "enum": voci},
                        "esercente_normalizzato": {"type": "string"},
                        "confidenza": {"type": "number"},
                    },
                    "required": ["idx", "categoria", "esercente_normalizzato", "confidenza"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["risultati"],
        "additionalProperties": False,
    }


SISTEMA = (
    "Sei un classificatore di spese familiari italiane. Ricevi nomi di esercenti come compaiono negli estratti conto "
    "(abbreviati, con codici di punto vendita sostituiti da #). Per ciascuno scegli UNA voce dall'elenco fornito "
    "(categoria · sottocategoria, copiata esattamente), scrivi il nome commerciale normalizzato usando solo parole presenti nel testo, e una confidenza tra 0 e 1. "
    "Se non riconosci l'esercente usa confidenza bassa (sotto 0.5) invece di indovinare. Rispondi solo con il JSON richiesto."
)


def _elenco_categorie():
    tax = cfg.tassonomia()
    righe = []
    for c in tax.per_sezione("uscite"):
        righe.append(cfg.etichetta(c))
    return "\n".join(righe)


def richiesta(voci):
    """voci: lista di dict {chiave, testo, paese, tipo}."""
    items = [{"idx": i, "esercente": v["testo"], "paese": v.get("paese") or "", "tipo": v.get("tipo") or "pagamento"}
             for i, v in enumerate(voci)]
    utente = ("Categorie disponibili (categoria · sottocategoria):\n%s\n\nEsercenti da classificare:\n%s"
              % (_elenco_categorie(), json.dumps(items, ensure_ascii=False)))
    return SISTEMA, utente


def valida(voci, dati, contenimento_minimo):
    tax = cfg.tassonomia()
    out = {}
    for r in (dati or {}).get("risultati", []):
        try:
            i = int(r["idx"])
            v = voci[i]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        voce = (r.get("categoria") or r.get("l2") or "").strip()
        cat = tuple(voce.split(cfg.SEP, 1)) if cfg.SEP in voce else tax.risolvi(voce)
        conf = float(r.get("confidenza") or 0)
        nome = (r.get("esercente_normalizzato") or "").strip()
        if not cat or not tax.valida(cat) or tax.sezione(cat) != "uscite":
            continue
        if nome and contenimento(nome, v["testo"]) < contenimento_minimo:
            conf = 0.0            # il modello ha inventato il nome: va in revisione
        out[v["chiave"]] = (cat, max(0.0, min(1.0, conf)), nome)
    return out


def _salva(conn, risultati, motore, modello=None):
    """Scrive le categorie in memoria (cache degli esercenti). Le conferme fatte da te non si toccano."""
    with transazione(conn):
        _salva_righe(conn, risultati, motore, modello)


def _salva_righe(conn, risultati, motore, modello):
    for k, (cat, conf, nome) in risultati.items():
        r = conn.execute("SELECT motore FROM cache_esercenti WHERE chiave = ?", (k,)).fetchone()
        if r and r[0] == "utente":
            continue
        conn.execute("INSERT INTO cache_esercenti(chiave, l1, l2, motore, confidenza, esercente_normalizzato, aggiornata_il, "
                     "modello) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(chiave) DO UPDATE SET l1=excluded.l1, "
                     "l2=excluded.l2, motore=excluded.motore, confidenza=excluded.confidenza, "
                     "esercente_normalizzato=excluded.esercente_normalizzato, aggiornata_il=excluded.aggiornata_il, "
                     "modello=excluded.modello",
                     (k, cat[0], cat[1], motore, conf, nome, ora(), modello))


# ---------------------------------------------------------------------------
# Prove: le risposte restano in anteprima finche' non le applichi
# ---------------------------------------------------------------------------
# nomi leggibili dei modelli di Claude (quelli non elencati si mostrano con il loro identificativo)
MODELLI_CLAUDE = {"claude-haiku-5-5": "Claude Haiku 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
                  "claude-opus-5-5": "Claude Opus 5.5", "claude-fable-5-1": "Claude Fable 5.1"}
PREDEFINITI_CLAUDE = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]


def nuova_prova(conn, motore, modello, origine, voci):
    """Crea una prova con una riga per esercente (senza risposta finche' il motore non risponde)."""
    with transazione(conn):
        cur = conn.execute("INSERT INTO prove(ts, motore, modello, origine) VALUES (?,?,?,?)", (ora(), motore, modello, origine))
        pid = cur.lastrowid
        conn.executemany("INSERT OR IGNORE INTO prove_risultati(prova_id, chiave, testo) VALUES (?,?,?)",
                         [(pid, v["chiave"], v.get("testo") or v["chiave"]) for v in voci])
    return pid


def _registra(conn, prova_id, risultati):
    with transazione(conn):
        for k, (cat, conf, nome) in risultati.items():
            conn.execute("UPDATE prove_risultati SET l1 = ?, l2 = ?, confidenza = ?, esercente_normalizzato = ? "
                         "WHERE prova_id = ? AND chiave = ?", (cat[0], cat[1], conf, nome or None, prova_id, k))


def prove(conn, stato=None):
    """Elenco delle prove, dalla piu' recente, con i conteggi."""
    filtro = " WHERE p.stato = ?" if stato else ""
    return [dict(r) for r in conn.execute(
        "SELECT p.*, count(r.chiave) AS voci, count(r.l2) AS risposte, avg(r.confidenza) AS confidenza_media, "
        "sum(r.applicato) AS applicati FROM prove p LEFT JOIN prove_risultati r ON r.prova_id = p.id"
        + filtro + " GROUP BY p.id ORDER BY p.id DESC", [stato] if stato else [])]


def risultati_prova(conn, prova_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM prove_risultati WHERE prova_id = ? ORDER BY chiave", (prova_id,))]


def chiavi_in_prova(conn):
    """Esercenti con una risposta ancora da confermare: non si rimandano finche' la prova e' aperta."""
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT r.chiave FROM prove_risultati r JOIN prove p ON p.id = r.prova_id "
        "WHERE p.stato = 'da_confermare' AND r.applicato = 0")}


def applica_prova(conn, prova_id, chiavi=None, chiudi=True):
    """Applica le risposte della prova (tutte o solo le chiavi indicate). Le conferme fatte da te restano.
    Con chiudi=False la prova resta aperta per le altre risposte (es. applicate solo quelle degli esercenti
    scelti in Revisione). Restituisce quante categorie sono state scritte."""
    p = conn.execute("SELECT * FROM prove WHERE id = ?", (prova_id,)).fetchone()
    scelte = None if chiavi is None else set(chiavi)
    ris = {r["chiave"]: ((r["l1"], r["l2"]), r["confidenza"] or 0.0, r["esercente_normalizzato"])
           for r in risultati_prova(conn, prova_id) if r["l2"] and (scelte is None or r["chiave"] in scelte)}
    with transazione(conn):
        _salva(conn, ris, p["motore"], p["modello"])
        conn.executemany("UPDATE prove_risultati SET applicato = 1 WHERE prova_id = ? AND chiave = ?",
                         [(prova_id, k) for k in ris])
        aperte = conn.execute("SELECT count(*) FROM prove_risultati WHERE prova_id = ? AND applicato = 0",
                              (prova_id,)).fetchone()[0]
        if chiudi or not aperte:
            gia = conn.execute("SELECT count(*) FROM prove_risultati WHERE prova_id = ? AND applicato = 1",
                               (prova_id,)).fetchone()[0]
            conn.execute("UPDATE prove SET stato = ?, chiusa_il = ? WHERE id = ?",
                         ("applicata" if gia else "non_applicata", ora(), prova_id))
    return len(ris)


def chiudi_prova(conn, prova_id, rimetti_in_coda_claude=False):
    """La prova resta per il confronto ma non cambia nulla. Con rimetti_in_coda_claude le voci di una prova
    nata dalla coda tornano spuntate in coda, pronte per un altro modello."""
    conn.execute("UPDATE prove SET stato = 'non_applicata', chiusa_il = ? WHERE id = ?", (ora(), prova_id))
    if rimetti_in_coda_claude:
        chiavi = [r["chiave"] for r in risultati_prova(conn, prova_id)]
        with transazione(conn):
            conn.executemany("UPDATE candidati_invio SET stato = 'approvato', aggiornato_il = ? WHERE chiave = ? "
                             "AND stato = 'inviato'", [(ora(), k) for k in chiavi])


def elimina_prova(conn, prova_id):
    conn.execute("DELETE FROM prove_risultati WHERE prova_id = ?", (prova_id,))
    conn.execute("DELETE FROM prove WHERE id = ?", (prova_id,))


def categoria_attuale(conn, chiavi=None):
    """{chiave: ((l1, l2), fonte)}: la categoria che l'esercente ha oggi nelle analisi (la piu' frequente)."""
    out = {}
    for r in conn.execute(
            "SELECT chiave, l1, l2, fonte, count(*) AS n FROM analisi WHERE componente = 'principale' AND l2 IS NOT NULL "
            "AND chiave IS NOT NULL AND chiave <> '' GROUP BY chiave, l1, l2, fonte ORDER BY chiave, n DESC"):
        if r["chiave"] not in out and (chiavi is None or r["chiave"] in chiavi):
            out[r["chiave"]] = ((r["l1"], r["l2"]), r["fonte"])
    return out


def esercenti_con_categoria(conn):
    """Esercenti (carte e addebiti SEPA) che hanno gia' una categoria, da qualunque fonte: il banco di prova per
    confrontare i modelli con le tue conferme, le regole e i codici MCC."""
    att = categoria_attuale(conn)
    righe = conn.execute(
        "SELECT a.chiave, max(m.paese) AS paese, count(*) AS movimenti FROM analisi a JOIN movimenti m ON m.id = a.mov_id "
        "WHERE a.componente = 'principale' AND a.natura IN ('uscita','rimborso') "
        "AND m.tipo IN ('pagamento_carta','addebito_sepa') AND a.chiave IS NOT NULL AND a.chiave <> '' "
        "GROUP BY a.chiave ORDER BY movimenti DESC").fetchall()
    return [{"chiave": r["chiave"], "paese": r["paese"], "movimenti": r["movimenti"],
             "cat": att[r["chiave"]][0], "fonte": att[r["chiave"]][1]} for r in righe if r["chiave"] in att]


def chiavi_da_classificare(conn):
    """Chiavi di esercenti (carte e addebiti SEPA) ancora senza categoria."""
    righe = conn.execute(
        "SELECT a.chiave, max(m.paese) AS paese, max(m.tipo) AS tipo, count(*) AS n FROM analisi a "
        "JOIN movimenti m ON m.id = a.mov_id WHERE a.componente = 'principale' AND a.l2 IS NULL "
        "AND a.natura IN ('uscita','rimborso') AND m.tipo IN ('pagamento_carta','addebito_sepa') "
        "AND a.chiave IS NOT NULL AND a.chiave <> '' GROUP BY a.chiave ORDER BY n DESC").fetchall()
    return [dict(r) for r in righe]


# ---------------------------------------------------------------------------
# Modello locale (Ollama)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Elenchi dei modelli (Impostazioni): piu' nomi separati da punto e virgola
# ---------------------------------------------------------------------------
def separa_modelli(testo):
    """'claude-haiku-5-5; claude-opus-5-5' -> ['claude-haiku-5-5', 'claude-opus-5-5'] (vuoti e doppioni tolti)."""
    out = []
    for x in re.split(r"[;\n]+", testo or ""):
        x = x.strip()
        if x and x not in out:
            out.append(x)
    return out


def modello_cloud(nome):
    """I modelli 'cloud' di Ollama girano su server esterni: per il modello locale non sono ammessi."""
    n = (nome or "").lower()
    return n.endswith("-cloud") or n.endswith(":cloud")


def errore_nome_modello(nome, locale=False):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", nome or ""):
        return "\"%s\" non e' un nome di modello valido" % nome
    if locale and modello_cloud(nome):
        return ("\"%s\" e' un modello cloud di Ollama: gira fuori dal PC, quindi non si usa come modello locale"
                % nome)
    return None


def modelli_claude(conn):
    elenco = list(leggi_impostazione(conn, "claude_modelli", None) or PREDEFINITI_CLAUDE)
    attuale = leggi_impostazione(conn, "claude_modello", None)
    return elenco + ([attuale] if attuale and attuale not in elenco else [])


def modello_claude(conn):
    """Il modello di Claude in uso: l'ultimo scelto, se e' ancora nell'elenco, altrimenti il primo."""
    elenco = modelli_claude(conn)
    attuale = leggi_impostazione(conn, "claude_modello", None)
    return attuale if attuale in elenco else elenco[0]


def modelli_locali(conn):
    elenco = leggi_impostazione(conn, "ollama_modelli", None) or []
    attuale = leggi_impostazione(conn, "ollama_modello", None)
    if not elenco:
        elenco = [attuale or "qwen2.5:3b"]
    return elenco


def ollama_config(conn):
    elenco = modelli_locali(conn)
    attuale = leggi_impostazione(conn, "ollama_modello", None)
    return {"url": leggi_impostazione(conn, "ollama_url", "http://127.0.0.1:11434"),
            "modello": attuale if attuale in elenco else elenco[0]}


def ollama_disponibile(conn):
    """(True, nomi dei modelli installati) oppure (False, motivo). I modelli cloud non contano come installati."""
    import requests
    c = ollama_config(conn)
    try:
        r = requests.get(c["url"] + "/api/tags", timeout=3)
        nomi = [m["name"] for m in r.json().get("models", [])
                if not modello_cloud(m.get("name")) and not m.get("remote_host") and not m.get("remote_model")]
        return True, nomi
    except Exception as e:
        return False, str(e)


def installato(nome, installati):
    """'qwen2.5:3b' e' tra i modelli installati? Senza etichetta vale ':latest' ('llama3.2' = 'llama3.2:latest')."""
    nomi = set(installati or [])
    return nome in nomi or (":" not in nome and nome + ":latest" in nomi)


def scarica_modello(conn, nome, avanzamento=None):
    """Scarica e installa un modello in Ollama (dalla libreria di Ollama: esce solo il nome del modello).
    avanzamento(stato, completati, totale) viene chiamato man mano."""
    return scarica_modello_url(ollama_config(conn)["url"], nome, avanzamento)


def scarica_modello_url(url, nome, avanzamento=None):
    """Come scarica_modello, con l'indirizzo di Ollama gia' letto (serve all'installazione in background,
    che non usa il database)."""
    import requests
    errore = errore_nome_modello(nome, locale=True)
    if errore:
        raise ValueError(errore)
    with requests.post(url.rstrip("/") + "/api/pull", json={"model": nome, "stream": True}, stream=True,
                       timeout=(5, 600)) as r:
        r.raise_for_status()
        for riga in r.iter_lines():
            if not riga:
                continue
            d = json.loads(riga)
            if d.get("error"):
                raise RuntimeError(d["error"])
            if avanzamento:
                avanzamento(d.get("status", ""), d.get("completed"), d.get("total"))
    return True


def classifica_locale(conn, voci, avanzamento=None, origine="coda"):
    """Classifica con il modello locale. Le risposte vanno in una prova da confermare: restituisce
    (id della prova, risultati)."""
    import requests
    c = ollama_config(conn)
    imp = cfg.impostazioni()["modello_locale"]
    batch = imp["dimensione_batch"]
    tutti = {}
    errore = errore_nome_modello(c["modello"], locale=True)
    if errore:
        raise ValueError(errore)
    pid = nuova_prova(conn, "modello_locale", c["modello"], origine, voci)
    for i in range(0, len(voci), batch):
        parte = voci[i:i + batch]
        sistema, utente = richiesta(parte)
        corpo = {"model": c["modello"], "stream": False, "format": schema_risposta(), "options": {"temperature": 0},
                 "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": utente}]}
        r = requests.post(c["url"] + "/api/chat", json=corpo, timeout=900)
        r.raise_for_status()
        testo = r.json()["message"]["content"]
        try:
            dati = json.loads(testo)
        except json.JSONDecodeError:
            dati = {}
        ris = valida(parte, dati, imp["contenimento_minimo"])
        _registra(conn, pid, ris)
        conn.execute("INSERT INTO registro_invii(ts, motore, modello, richiesta, risposta, esito) VALUES (?,?,?,?,?,?)",
                     (ora(), "modello_locale", c["modello"], utente, testo, "%d/%d classificati" % (len(ris), len(parte))))
        tutti.update(ris)
        if avanzamento:
            avanzamento(min(i + batch, len(voci)), len(voci))
    return pid, tutti


# ---------------------------------------------------------------------------
# Claude: coda degli invii con approvazione
# ---------------------------------------------------------------------------
CHIAVE_SELEZIONE = "selezione_revisione"


def selezione(conn):
    """Esercenti scelti in Revisione per la classificazione automatica (ancora da classificare), o None."""
    scelte = leggi_impostazione(conn, CHIAVE_SELEZIONE, None)
    if not scelte:
        return None
    ancora = {v["chiave"] for v in chiavi_da_classificare(conn)}
    rimaste = [k for k in scelte if k in ancora]
    return set(rimaste) if rimaste else None


def imposta_selezione(conn, chiavi):
    from .db import scrivi_impostazione
    scrivi_impostazione(conn, CHIAVE_SELEZIONE, list(dict.fromkeys(chiavi or [])))


def pulisci_coda(conn):
    """Toglie dalla coda le voci che non sono piu' da classificare (classificate in Revisione, da una regola o dal
    modello locale, oppure arrivate da un import annullato). Le voci gia' inviate restano come storico."""
    ancora = {v["chiave"] for v in chiavi_da_classificare(conn)}
    via = [r[0] for r in conn.execute("SELECT chiave FROM candidati_invio WHERE stato <> 'inviato'") if r[0] not in ancora]
    with transazione(conn):
        conn.executemany("DELETE FROM candidati_invio WHERE chiave = ?", [(k,) for k in via])
    # le voci "in attesa" della versione precedente valgono come da inviare: si approvano con il pulsante di invio
    conn.execute("UPDATE candidati_invio SET stato = 'approvato' WHERE stato = 'in_attesa'")
    return len(via)


def stima_coda(conn, solo=None):
    """Cosa farebbe prepara_candidati, senza scrivere: chiavi nuove da mettere in coda (con quante bloccate dal
    controllo di sicurezza), quante sono gia' in coda e quante gia' inviate senza una categoria valida.
    solo: insieme di chiavi a cui limitarsi (gli esercenti scelti in Revisione)."""
    soglia = cfg.impostazioni()["modello_locale"]["soglia_confidenza"]
    stati = {r[0]: r[1] for r in conn.execute("SELECT chiave, stato FROM candidati_invio")}
    aperte = chiavi_in_prova(conn)
    nuove, bloccate, in_coda, senza_risposta, in_prova = [], 0, 0, [], 0
    for v in chiavi_da_classificare(conn):
        if solo is not None and v["chiave"] not in solo:
            continue
        if v["chiave"] in aperte:
            in_prova += 1                     # risposta in anteprima, da applicare o no
            continue
        c = conn.execute("SELECT confidenza, motore FROM cache_esercenti WHERE chiave = ?", (v["chiave"],)).fetchone()
        if c and (c["motore"] == "utente" or (c["confidenza"] or 0) >= soglia):
            continue
        s = stati.get(v["chiave"])
        if s == "inviato" or (c and c["motore"] == "claude" and not s):
            # gia' inviata: Claude non ha risposto o ha risposto con confidenza bassa (resta come proposta)
            senza_risposta.append(v["chiave"])
        elif s:
            in_coda += 1
        else:
            motivi = controlla(conn, v["chiave"], chiave=v["chiave"])
            nuove.append((v, motivi))
            bloccate += bool(motivi)
    return {"nuove": nuove, "bloccate": bloccate, "in_coda": in_coda, "senza_risposta": senza_risposta,
            "in_prova": in_prova}


def prepara_candidati(conn, solo=None):
    """Mette in coda le chiavi non risolte (o a bassa confidenza) per Claude, con il controllo di sicurezza.
    Entrano gia' spuntate per l'invio: nulla parte finche' non premi il pulsante, dopo l'anteprima."""
    st = stima_coda(conn, solo)
    for v, motivi in st["nuove"]:
        conn.execute("INSERT INTO candidati_invio(chiave, testo, paese, direzione, stato, motivo, aggiornato_il) "
                     "VALUES (?,?,?,?,?,?,?)", (v["chiave"], v["chiave"], v["paese"], "uscita",
                                                "bloccato" if motivi else "approvato", ", ".join(motivi) or None, ora()))
    return len(st["nuove"])


def rimetti_in_coda(conn, chiavi):
    paesi = {v["chiave"]: v["paese"] for v in chiavi_da_classificare(conn)}
    for k in chiavi:
        motivi = controlla(conn, k, chiave=k)
        conn.execute("INSERT INTO candidati_invio(chiave, testo, paese, direzione, stato, motivo, aggiornato_il) "
                     "VALUES (?,?,?,?,?,?,?) ON CONFLICT(chiave) DO UPDATE SET stato = excluded.stato, "
                     "motivo = excluded.motivo, aggiornato_il = excluded.aggiornato_il",
                     (k, k, paesi.get(k), "uscita", "bloccato" if motivi else "approvato", ", ".join(motivi) or None, ora()))


def _portachiavi(funzione, *args, timeout=4):
    """Chiamata al portachiavi di sistema con timeout: senza sessione grafica keyring puo' bloccarsi.
    Linux: portachiavi del desktop (Secret Service, via D-Bus). Windows: Gestione credenziali."""
    import os
    import sys
    import threading
    if sys.platform.startswith("linux") and not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        raise RuntimeError("portachiavi di sistema non disponibile in questa sessione")
    esito = {}

    def lavoro():
        try:
            import keyring
            esito["v"] = getattr(keyring, funzione)(*args)
        except Exception as e:
            esito["e"] = e
    t = threading.Thread(target=lavoro, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise RuntimeError("il portachiavi non risponde")
    if "e" in esito:
        raise esito["e"]
    return esito.get("v")


def chiave_api():
    import os
    try:
        k = _portachiavi("get_password", "spese-app", "anthropic")
        if k:
            return k
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY")


def salva_chiave_api(valore):
    _portachiavi("set_password", "spese-app", "anthropic", valore)


def lotti_claude(conn, dimensione=50, scelte=None, solo=None):
    """Voci approvate (o le `scelte`, righe con chiave, testo e paese), ricontrollate, divise nei lotti
    esattamente come verranno inviati. Senza titolari indicati non parte nulla: il controllo non saprebbe
    quali nomi bloccare."""
    from .importa import titolari
    if not titolari(conn):
        return []
    voci = []
    righe = scelte if scelte is not None else \
        conn.execute("SELECT * FROM candidati_invio WHERE stato = 'approvato' ORDER BY chiave").fetchall()
    for r in righe:
        if solo is not None and r["chiave"] not in solo:
            continue
        # ricontrollo: il testo puo' essere stato modificato, e l'esercente nel frattempo collocato nell'area
        # solo locale (es. una proposta di farmacia dal modello locale)
        motivi = controlla(conn, r["testo"], chiave=r["chiave"])
        if motivi:
            conn.execute("UPDATE candidati_invio SET stato='bloccato', motivo=? WHERE chiave=?",
                         (", ".join(motivi), r["chiave"]))
            continue
        voci.append({"chiave": r["chiave"], "testo": r["testo"], "paese": r["paese"], "tipo": "pagamento"})
    lotti = []
    for i in range(0, len(voci), dimensione):
        parte = voci[i:i + dimensione]
        sistema, utente = richiesta(parte)
        lotti.append((parte, sistema, utente))
    return lotti


def payload_claude(conn, limite=50):
    """Primo lotto da inviare: (voci, sistema, utente)."""
    lotti = lotti_claude(conn, limite)
    if lotti:
        return lotti[0]
    sistema, utente = richiesta([])
    return [], sistema, utente


def _invia_lotto(conn, client, modello, voci, sistema, utente, prova_id, segna_inviati=True):
    # risposta strutturata con schema JSON: funziona con tutti i modelli (Sonnet e Opus 5.5 rifiutano
    # invece l'uso forzato di uno strumento con tool_choice "tool")
    risposta = client.messages.create(
        model=modello, max_tokens=8192, system=sistema,
        messages=[{"role": "user", "content": utente}],
        output_config={"format": {"type": "json_schema", "schema": schema_risposta()}})
    testo = "".join(b.text for b in risposta.content if getattr(b, "type", "") == "text")
    if getattr(risposta, "stop_reason", None) == "max_tokens":
        raise RuntimeError("risposta di Claude troncata: riprova con meno voci")
    try:
        dati = json.loads(testo) if testo else {}
    except json.JSONDecodeError as e:
        raise RuntimeError("risposta di Claude non leggibile (%s)" % e)
    ris = valida(voci, dati, cfg.impostazioni()["modello_locale"]["contenimento_minimo"])
    _registra(conn, prova_id, ris)
    conn.execute("INSERT INTO registro_invii(ts, motore, modello, richiesta, risposta, esito) VALUES (?,?,?,?,?,?)",
                 (ora(), "claude", modello, utente, json.dumps(dati, ensure_ascii=False),
                  "%d/%d classificati (prova %d)" % (len(ris), len(voci), prova_id)))
    if segna_inviati:
        for v in voci:
            conn.execute("UPDATE candidati_invio SET stato='inviato', aggiornato_il=? WHERE chiave=?", (ora(), v["chiave"]))
    return ris


def verifica_modelli_claude(modelli, client=None):
    """Nomi che l'API di Claude non riconosce (serve la chiave; senza rete o chiave non segnala nulla)."""
    if client is None:
        chiave = chiave_api()
        if not chiave:
            return []
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=chiave, timeout=8, max_retries=0)
        except Exception:
            return []
    sconosciuti = []
    for m in modelli:
        try:
            client.models.retrieve(m)
        except Exception as e:
            if getattr(e, "status_code", None) == 404:
                sconosciuti.append(m)
    return sconosciuti


def _client(client):
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=chiave_api())
    return client


def invia_claude(conn, dimensione=50, avanzamento=None, client=None, modello=None, solo=None):
    """Invia tutte le voci approvate della coda, a lotti. Le risposte vanno in una prova da confermare
    (salvata lotto per lotto). Restituisce (id della prova, risultati), o (None, {}) se non c'e' nulla."""
    lotti = lotti_claude(conn, dimensione, solo=solo)
    if not lotti:
        return None, {}
    modello = modello or modello_claude(conn)
    client = _client(client)
    pid = nuova_prova(conn, "claude", modello, "coda", [v for l in lotti for v in l[0]])
    tutti = {}
    for k, (voci, sistema, utente) in enumerate(lotti):
        tutti.update(_invia_lotto(conn, client, modello, voci, sistema, utente, pid))
        if avanzamento:
            avanzamento(k + 1, len(lotti))
    return pid, tutti


def lotti_confronto(conn, chiavi, dimensione=50):
    """Lotti per una prova su esercenti gia' classificati: stesso controllo di sicurezza e stesso testo della
    coda (la chiave dell'esercente), senza passare dalla coda. Nulla parte senza titolari indicati."""
    paesi = {v["chiave"]: v["paese"] for v in esercenti_con_categoria(conn)}
    return lotti_claude(conn, dimensione, scelte=[{"chiave": k, "testo": k, "paese": paesi.get(k)} for k in chiavi])


def prova_claude(conn, chiavi, modello, dimensione=50, avanzamento=None, client=None):
    """Prova di confronto con Claude su esercenti gia' classificati: le categorie attuali non cambiano finche'
    non applichi la prova. Restituisce (id della prova, risultati)."""
    lotti = lotti_confronto(conn, chiavi, dimensione)
    if not lotti:
        return None, {}
    client = _client(client)
    pid = nuova_prova(conn, "claude", modello, "confronto", [v for l in lotti for v in l[0]])
    tutti = {}
    for k, (voci, sistema, utente) in enumerate(lotti):
        tutti.update(_invia_lotto(conn, client, modello, voci, sistema, utente, pid, segna_inviati=False))
        if avanzamento:
            avanzamento(k + 1, len(lotti))
    return pid, tutti


def confronto(conn, prove_id):
    """Una riga per esercente presente in almeno una delle prove: categoria attuale e risposta di ogni prova.
    E un riepilogo per prova: risposte, confidenza, concordanza con la categoria attuale (in generale e solo
    dove l'hai decisa tu)."""
    righe, riep = {}, []
    soglia = cfg.impostazioni()["modello_locale"]["soglia_confidenza"]
    tue = {"utente", "regola utente", "storico"}           # categorie che vengono da scelte tue
    for pid in prove_id:
        for r in risultati_prova(conn, pid):
            righe.setdefault(r["chiave"], {})[pid] = r
    att = categoria_attuale(conn, set(righe))
    for pid in prove_id:
        ris = [x[pid] for x in righe.values() if pid in x]
        con_l2 = [r for r in ris if r["l2"]]
        # una risposta gia' applicata e' diventata la categoria attuale: confrontarla con se stessa non dice nulla
        confrontabili = [r for r in con_l2 if r["chiave"] in att and not r["applicato"]]
        mie = [r for r in confrontabili if att[r["chiave"]][1] in tue]
        riep.append({
            "prova": pid, "voci": len(ris), "risposte": len(con_l2),
            "confidenza_media": (sum(r["confidenza"] or 0 for r in con_l2) / len(con_l2)) if con_l2 else None,
            "sopra_soglia": sum((r["confidenza"] or 0) >= soglia for r in con_l2),
            "confrontabili": len(confrontabili),
            "uguali": sum((r["l1"], r["l2"]) == att[r["chiave"]][0] for r in confrontabili),
            "tue": len(mie), "uguali_tue": sum((r["l1"], r["l2"]) == att[r["chiave"]][0] for r in mie)})
    tabella = [{"chiave": k, "attuale": att.get(k, (None, None))[0], "fonte": att.get(k, (None, None))[1],
                "risposte": v} for k, v in sorted(righe.items())]
    return tabella, riep
