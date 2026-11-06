"""Database locale cifrato (SQLCipher) e schema.

Livelli:
- raw: file importati e righe originali, mai modificati
- normalizzato: movimenti nello schema unico (deterministico dal raw)
- conoscenza: conti, regole, decisioni, cache, invii (persistenti)
- derivato: tabella analisi, trasferimenti, contenitori (ricalcolati da zero)

Importi in centesimi interi (esatti); quantita' e prezzi come testo Decimal.
"""
import json
import os
from contextlib import contextmanager
from datetime import datetime

try:
    import sqlcipher3 as sqlite
    CIFRATO = True
except ImportError:  # solo per sviluppo/test: senza cifratura
    import sqlite3 as sqlite
    CIFRATO = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS impostazioni (chiave TEXT PRIMARY KEY, valore TEXT);

CREATE TABLE IF NOT EXISTS conti (
    id INTEGER PRIMARY KEY,
    nome TEXT UNIQUE NOT NULL,
    banca TEXT,
    profilo TEXT,
    tipo TEXT NOT NULL DEFAULT 'conto' CHECK (tipo IN ('conto','carta','broker','deposito')),
    perimetro TEXT NOT NULL DEFAULT 'personale' CHECK (perimetro IN ('comune','personale')),
    iban TEXT,
    numero TEXT,
    conto_addebito_id INTEGER REFERENCES conti(id),
    importato INTEGER NOT NULL DEFAULT 1,
    chiuso_il TEXT,
    creato_il TEXT
);

CREATE TABLE IF NOT EXISTS import_file (
    id INTEGER PRIMARY KEY,
    conto_id INTEGER NOT NULL REFERENCES conti(id),
    nome_file TEXT, hash_file TEXT, profilo TEXT, importato_il TEXT,
    righe_dati INTEGER, righe_scartate INTEGER, nuove INTEGER, duplicate INTEGER,
    data_min TEXT, data_max TEXT, quadratura TEXT, avvisi TEXT
);

CREATE TABLE IF NOT EXISTS righe_raw (
    id INTEGER PRIMARY KEY,
    import_id INTEGER NOT NULL REFERENCES import_file(id),
    n_riga INTEGER, contenuto TEXT,
    mov_id TEXT                         -- movimento che la riga ha creato o ritrovato (duplicato)
);

CREATE TABLE IF NOT EXISTS saldi (
    conto_id INTEGER, data TEXT, saldo_cent INTEGER, tipo TEXT, import_id INTEGER
);

CREATE TABLE IF NOT EXISTS movimenti (
    id TEXT PRIMARY KEY,
    conto_id INTEGER NOT NULL REFERENCES conti(id),
    import_id INTEGER, raw_id INTEGER,
    data TEXT NOT NULL,                 -- data usata per le analisi (operazione se nota)
    data_contabile TEXT, data_valuta TEXT, data_registrazione TEXT, ora TEXT,
    importo_cent INTEGER,               -- componente principale (NULL = assente)
    fee_cent INTEGER NOT NULL DEFAULT 0,
    tax_cent INTEGER NOT NULL DEFAULT 0,
    importo_orig TEXT, divisa_orig TEXT, cambio TEXT,
    codice TEXT, tipo TEXT NOT NULL,
    descrizione TEXT,                   -- solo locale
    esercente TEXT, intermediario TEXT, chiave TEXT, paese TEXT, mcc TEXT,
    controparte_nome TEXT, controparte_iban TEXT,
    isin TEXT, nome_titolo TEXT, quantita TEXT, prezzo TEXT,
    ciclo_carta TEXT, avviso TEXT
);
CREATE INDEX IF NOT EXISTS ix_mov_conto_data ON movimenti(conto_id, data);
CREATE INDEX IF NOT EXISTS ix_mov_chiave ON movimenti(chiave);

CREATE TABLE IF NOT EXISTS regole_utente (
    id INTEGER PRIMARY KEY,
    campo TEXT NOT NULL DEFAULT 'chiave' CHECK (campo IN ('chiave','descrizione')),
    modo TEXT NOT NULL DEFAULT 'esatto' CHECK (modo IN ('esatto','contiene','regex')),
    pattern TEXT NOT NULL, l2 TEXT NOT NULL, natura TEXT,
    priorita INTEGER NOT NULL DEFAULT 10, creata_il TEXT,
    l1 TEXT                             -- categoria: la chiave e' sempre la coppia (l1, l2)
);

CREATE TABLE IF NOT EXISTS decisioni (
    mov_id TEXT PRIMARY KEY, l2 TEXT, natura TEXT, nota TEXT, decisa_il TEXT,
    l1 TEXT
);

CREATE TABLE IF NOT EXISTS ripartizioni (
    -- un movimento suddiviso in parti, ciascuna con importo, sottocategoria e causale; le parti sommano al movimento
    id INTEGER PRIMARY KEY, mov_id TEXT NOT NULL, n INTEGER NOT NULL, importo_cent INTEGER NOT NULL,
    l2 TEXT NOT NULL, causale TEXT, creata_il TEXT,
    l1 TEXT
);
CREATE INDEX IF NOT EXISTS ix_ripartizioni_mov ON ripartizioni(mov_id);

CREATE TABLE IF NOT EXISTS etichette (
    id INTEGER PRIMARY KEY, nome TEXT UNIQUE NOT NULL COLLATE NOCASE, creata_il TEXT
);
CREATE TABLE IF NOT EXISTS etichette_movimento (
    mov_id TEXT NOT NULL, etichetta_id INTEGER NOT NULL REFERENCES etichette(id) ON DELETE CASCADE,
    PRIMARY KEY (mov_id, etichetta_id)
);
CREATE TABLE IF NOT EXISTS attributi_movimento (
    -- natura della spesa scelta sul singolo movimento (prevale su quella della sottocategoria)
    mov_id TEXT PRIMARY KEY, valore TEXT NOT NULL, deciso_il TEXT
);

CREATE TABLE IF NOT EXISTS cache_esercenti (
    chiave TEXT PRIMARY KEY, l2 TEXT, motore TEXT, confidenza REAL,
    esercente_normalizzato TEXT, aggiornata_il TEXT,
    modello TEXT,                       -- modello che ha dato la categoria (es. claude-opus-5-5, qwen2.5:3b)
    l1 TEXT
);

-- prove di classificazione: le risposte del modello locale o di Claude restano qui finche' non le applichi,
-- e restano anche dopo, per confrontare modelli diversi sugli stessi esercenti
CREATE TABLE IF NOT EXISTS prove (
    id INTEGER PRIMARY KEY, ts TEXT, motore TEXT, modello TEXT,
    origine TEXT,                       -- coda (esercenti da classificare) | confronto (gia' classificati)
    stato TEXT NOT NULL DEFAULT 'da_confermare' CHECK (stato IN ('da_confermare','applicata','non_applicata')),
    chiusa_il TEXT
);

CREATE TABLE IF NOT EXISTS prove_risultati (
    prova_id INTEGER NOT NULL REFERENCES prove(id) ON DELETE CASCADE,
    chiave TEXT NOT NULL, testo TEXT,
    l2 TEXT, confidenza REAL, esercente_normalizzato TEXT,   -- NULL: nessuna risposta valida
    applicato INTEGER NOT NULL DEFAULT 0,
    l1 TEXT,
    PRIMARY KEY (prova_id, chiave)
);

CREATE TABLE IF NOT EXISTS abbinamenti_manuali (
    mov_a TEXT, mov_b TEXT, esito TEXT CHECK (esito IN ('si','no')), deciso_il TEXT,
    PRIMARY KEY (mov_a, mov_b)
);

CREATE TABLE IF NOT EXISTS destinazioni (
    beneficiario TEXT PRIMARY KEY,
    esito TEXT CHECK (esito IN ('non_caricato','non_tracciato','altra_persona')),
    deciso_il TEXT
);

CREATE TABLE IF NOT EXISTS candidati_invio (
    chiave TEXT PRIMARY KEY, testo TEXT, paese TEXT, direzione TEXT,
    stato TEXT NOT NULL DEFAULT 'in_attesa'
        CHECK (stato IN ('in_attesa','approvato','escluso','inviato','bloccato')),
    motivo TEXT, modificato INTEGER NOT NULL DEFAULT 0, aggiornato_il TEXT
);

CREATE TABLE IF NOT EXISTS registro_invii (
    id INTEGER PRIMARY KEY, ts TEXT, motore TEXT, modello TEXT,
    richiesta TEXT, risposta TEXT, esito TEXT
);

-- bonifici a proprio nome senza IBAN: il conto (gia' censito) dall'altra parte, indicato a mano
CREATE TABLE IF NOT EXISTS destinazioni_conti (            -- per beneficiario: vale per tutti i suoi movimenti
    beneficiario TEXT PRIMARY KEY, conto_id INTEGER NOT NULL REFERENCES conti(id) ON DELETE CASCADE, deciso_il TEXT
);
CREATE TABLE IF NOT EXISTS destinazioni_movimenti (        -- per singolo movimento: prevale sul beneficiario
    mov_id TEXT PRIMARY KEY, conto_id INTEGER NOT NULL REFERENCES conti(id) ON DELETE CASCADE, deciso_il TEXT
);

-- addebito PayPal su un conto o una carta ricondotto a mano a un pagamento del report PayPal
-- (paypal_id NULL: l'addebito non e' nel report, e' una spesa normale)
CREATE TABLE IF NOT EXISTS abbinamenti_paypal (
    addebito_id TEXT PRIMARY KEY, paypal_id TEXT, deciso_il TEXT
);

-- prezzi di mercato (sezione Portafoglio, facoltativa): solo dati pubblici scaricati da Yahoo Finance
CREATE TABLE IF NOT EXISTS titoli_mercato (
    isin TEXT PRIMARY KEY, simbolo TEXT, valuta TEXT, nome TEXT, borsa TEXT,
    scelto INTEGER NOT NULL DEFAULT 0,      -- 1: simbolo indicato a mano (non si cerca piu')
    aggiornato_il TEXT, errore TEXT
);
CREATE TABLE IF NOT EXISTS prezzi_mercato (
    isin TEXT NOT NULL, data TEXT NOT NULL, prezzo REAL NOT NULL,   -- chiusura del giorno, in euro
    PRIMARY KEY (isin, data)
);
CREATE TABLE IF NOT EXISTS cambi_mercato (
    valuta TEXT NOT NULL, data TEXT NOT NULL, tasso REAL NOT NULL,   -- euro per 1 unita' di valuta
    PRIMARY KEY (valuta, data)
);

-- livello derivato: ricostruito a ogni ricalcolo
CREATE TABLE IF NOT EXISTS analisi (
    id INTEGER PRIMARY KEY,
    mov_id TEXT, componente TEXT, data TEXT, mese TEXT,
    conto_id INTEGER, perimetro TEXT, importo_cent INTEGER,
    natura TEXT, l1 TEXT, l2 TEXT, fonte TEXT, confidenza REAL,
    da_rivedere INTEGER NOT NULL DEFAULT 0, motivo TEXT, proposta TEXT, chiave TEXT,
    attributo TEXT                      -- natura della spesa (necessaria, svago, superflua) per uscite e rimborsi
);
CREATE INDEX IF NOT EXISTS ix_analisi_mese ON analisi(perimetro, mese);

CREATE TABLE IF NOT EXISTS trasferimenti (
    mov_id TEXT PRIMARY KEY, controparte_mov_id TEXT, stato TEXT, fiducia TEXT,
    conto_destinazione_id INTEGER, beneficiario TEXT
);

CREATE TABLE IF NOT EXISTS contenitori (
    mov_id TEXT PRIMARY KEY, stato TEXT, righe INTEGER, differenza_cent INTEGER, ciclo TEXT
);
"""


class PassphraseErrata(Exception):
    pass


def ora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _quota(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def apri(percorso, passphrase: str):
    """Apre (o crea) il database cifrato. Solleva PassphraseErrata se la chiave non e' valida."""
    nuovo = not os.path.exists(percorso)
    conn = sqlite.connect(str(percorso), check_same_thread=False, isolation_level=None)
    if CIFRATO:
        if not passphrase:
            raise PassphraseErrata("passphrase vuota")
        conn.execute("PRAGMA key = " + _quota(passphrase))
        try:
            conn.execute("PRAGMA cipher_log_level = NONE")
        except Exception:
            pass
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except Exception as e:
        conn.close()
        raise PassphraseErrata(str(e))
    conn.row_factory = sqlite.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migra(conn)
    if nuovo:
        try:
            os.chmod(percorso, 0o600)
        except OSError:
            pass
    return conn


def _migra(conn):
    """Aggiunte di colonne a database creati da versioni precedenti (idempotente)."""
    aggiunte = {"analisi": [("chiave", "TEXT"), ("attributo", "TEXT")], "righe_raw": [("mov_id", "TEXT")],
                "cache_esercenti": [("modello", "TEXT"), ("l1", "TEXT")], "regole_utente": [("l1", "TEXT")],
                "decisioni": [("l1", "TEXT")], "ripartizioni": [("l1", "TEXT")], "prove_risultati": [("l1", "TEXT")]}
    for tabella, colonne in aggiunte.items():
        esistenti = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % tabella)}
        for nome, tipo in colonne:
            if nome not in esistenti:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (tabella, nome, tipo))
    conn.execute("CREATE INDEX IF NOT EXISTS ix_raw_mov ON righe_raw(mov_id)")
    # IBAN dei conti salvati con spazi o in minuscolo (versioni fino alla 0.2.1)
    from .iban import normalizza as normalizza_iban
    for cid, iban in conn.execute("SELECT id, iban FROM conti WHERE iban IS NOT NULL").fetchall():
        if normalizza_iban(iban) != iban:
            conn.execute("UPDATE conti SET iban = ? WHERE id = ?", (normalizza_iban(iban) or None, cid))
    # righe importate prima della 0.1.1: si collega almeno la riga che ha creato il movimento
    conn.execute("UPDATE righe_raw SET mov_id = (SELECT m.id FROM movimenti m WHERE m.raw_id = righe_raw.id) "
                 "WHERE mov_id IS NULL AND EXISTS (SELECT 1 FROM movimenti m WHERE m.raw_id = righe_raw.id)")


def cambia_passphrase(conn, nuova: str):
    if CIFRATO:
        conn.execute("PRAGMA rekey = " + _quota(nuova))


@contextmanager
def transazione(conn):
    """Raccoglie piu' scritture in un solo commit. Il database lavora in autocommit: senza, ogni riga e' una
    scrittura su disco a se' (con centinaia di righe, secondi o minuti). Se una transazione e' gia' aperta si
    resta dentro quella."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    if conn.in_transaction:
        conn.execute("COMMIT")


def leggi_impostazione(conn, chiave, predefinito=None):
    r = conn.execute("SELECT valore FROM impostazioni WHERE chiave = ?", (chiave,)).fetchone()
    return json.loads(r[0]) if r else predefinito


def scrivi_impostazione(conn, chiave, valore):
    conn.execute("INSERT INTO impostazioni(chiave, valore) VALUES (?, ?) "
                 "ON CONFLICT(chiave) DO UPDATE SET valore = excluded.valore", (chiave, json.dumps(valore)))


def backup(conn, destinazione):
    """Copia del file cifrato (stessa passphrase). Il database lavora senza transazioni aperte
    tra un'operazione e l'altra, quindi la copia del file e' consistente."""
    import shutil
    dbfile = conn.execute("PRAGMA database_list").fetchone()[2]
    shutil.copy2(dbfile, destinazione)
    try:
        os.chmod(destinazione, 0o600)
    except OSError:
        pass
