"""Tassonomia modificabile dall'interfaccia.

La tassonomia di partenza sta in config/tassonomia.yaml (parte del programma). Le modifiche dell'utente
si salvano nel database cifrato (impostazione "tassonomia_utente"), cosi' sopravvivono agli aggiornamenti
del programma, e all'accesso diventano la tassonomia in uso (configurazione.imposta_tassonomia).

Le sottocategorie richiamate dal programma (tipi di movimento, regole, codici MCC, marchi, OpenStreetMap,
codice dei trasferimenti) sono "predefinite": si possono spostare sotto un'altra categoria della stessa
sezione, ma non rinominare, eliminare o cambiare di sezione. Le altre sono libere; rinominarle aggiorna
anche conferme, regole e decisioni gia' salvate.

La categoria e' sempre la coppia (categoria, sottocategoria): la stessa sottocategoria puo' stare sotto
categorie diverse (CASA · Varie e ACQUISTI · Varie), e ogni voce salvata indica entrambe.
"""
import json

import yaml

from . import configurazione as cfg
from .db import leggi_impostazione, scrivi_impostazione

SEZIONI = ("uscite", "entrate", "risparmio")
CHIAVE = "tassonomia_utente"
# richiamate per nome nel codice (ricalcolo dei trasferimenti, risposta di ripiego dei modelli)
NEL_CODICE = {"Varie", "Contributo versato", "Contributo ricevuto", "Accantonamento", "Rientro dal risparmio"}


def predefinita() -> dict:
    """Tassonomia del file di configurazione, nella forma {sezione: {L1: [L2]}, solo_locale: [...]}."""
    t = cfg._yaml("tassonomia.yaml")
    return {**{s: {l1: list(v) for l1, v in t[s].items()} for s in SEZIONI}, "solo_locale": list(t.get("solo_locale", []))}


def protette() -> set:
    """Sottocategorie usate dalle configurazioni del programma o dal codice."""
    out = set(NEL_CODICE)

    def raccogli(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("l2", "l2_default") and isinstance(v, str):
                    out.add(v)
                raccogli(v)
        elif isinstance(o, list):
            for x in o:
                raccogli(x)
    raccogli(cfg._yaml("tipi_movimento.yaml"))
    out.update(r.l2 for r in cfg.regole_esercenti())
    out.update(r["l2"] for r in cfg.mcc().values() if r.get("l2"))
    out.update(m["l2"] for _, m in cfg.marchi() if m.get("l2"))
    osm = yaml.safe_load((cfg.CONFIG / "osm_tag_categorie.yaml").read_text(encoding="utf-8")) or {}
    out.update(v for v in (osm.get("tag") or {}).values() if v)
    return out


def attuale(conn) -> dict:
    return leggi_impostazione(conn, CHIAVE) or predefinita()


def carica(conn):
    """All'accesso: rende attiva la tassonomia salvata dall'utente (o quella del file)."""
    t = leggi_impostazione(conn, CHIAVE)
    cfg.imposta_tassonomia(_completa(t) if t else None)
    completa_categorie(conn)


def _completa(t):
    """Una versione nuova del programma puo' aggiungere sottocategorie predefinite: se mancano nella
    tassonomia dell'utente si aggiungono sotto la loro categoria di partenza."""
    t = json.loads(json.dumps(t))
    presenti = {l2 for s in SEZIONI for v in t.get(s, {}).values() for l2 in v}
    base = predefinita()
    for s in SEZIONI:
        for l1, voci in base[s].items():
            for l2 in voci:
                if l2 not in presenti:
                    t.setdefault(s, {}).setdefault(l1, []).append(l2)
    t.setdefault("solo_locale", base["solo_locale"])
    return t


def righe(conn):
    """Una riga per sottocategoria, nell'ordine della tassonomia, con quante voci salvate la usano.
    'originale' identifica la riga (categoria|sottocategoria) per riconoscere spostamenti e rinomine."""
    t = attuale(conn)
    prot = protette()
    uso = utilizzo(conn)
    tax = cfg.tassonomia()
    out = []
    for s in SEZIONI:
        for l1, voci in t[s].items():
            for l2 in voci:
                # predefinita: la voce a cui le configurazioni arrivano chiamandola per nome (se lo stesso nome sta
                # sotto piu' categorie, solo quella di partenza; le altre sono libere)
                pred = l2 in prot and tax.risolvi(l2) in (None, (l1, l2))
                out.append({"sezione": s, "l1": l1, "l2": l2, "originale": cfg.chiave_cat((l1, l2)),
                            "predefinita": pred, "in_uso": uso.get((l1, l2), 0)})
    return out


def utilizzo(conn) -> dict:
    """{(l1, l2): quante conferme di esercenti, regole, decisioni e suddivisioni la usano}."""
    completa_categorie(conn)
    uso = {}
    for tab in ("cache_esercenti", "regole_utente", "decisioni", "ripartizioni"):
        for l1, l2, n in conn.execute("SELECT l1, l2, count(*) FROM %s WHERE l2 IS NOT NULL GROUP BY l1, l2" % tab):
            uso[(l1, l2)] = uso.get((l1, l2), 0) + n
    return uso


TABELLE_CON_CATEGORIA = ("cache_esercenti", "regole_utente", "decisioni", "ripartizioni", "prove_risultati")


def completa_categorie(conn):
    """Voci salvate con la sola sottocategoria (versioni fino alla 0.6): si aggiunge la categoria, presa dalla
    tassonomia in uso. Idempotente; le voci ambigue restano da completare finche' la tassonomia non decide."""
    tax = cfg.tassonomia()
    for tab in TABELLE_CON_CATEGORIA:
        for (l2,) in conn.execute("SELECT DISTINCT l2 FROM %s WHERE l1 IS NULL AND l2 IS NOT NULL" % tab).fetchall():
            cat = tax.risolvi(l2)
            if cat:
                conn.execute("UPDATE %s SET l1 = ? WHERE l1 IS NULL AND l2 = ?" % tab, (cat[0], l2))


def _rinomina_dati(conn, vecchia, nuova):
    """vecchia, nuova: (l1, l2). Conferme, regole, decisioni, suddivisioni, prove e natura della spesa seguono."""
    for tab in TABELLE_CON_CATEGORIA:
        conn.execute("UPDATE %s SET l1 = ?, l2 = ? WHERE l1 = ? AND l2 = ?" % tab, (*nuova, *vecchia))
    mod = leggi_impostazione(conn, "attributo_l2", {}) or {}
    k_old, k_new = cfg.chiave_cat(vecchia), cfg.chiave_cat(nuova)
    if k_old in mod:
        mod[k_new] = mod.pop(k_old)
        scrivi_impostazione(conn, "attributo_l2", mod)


def _testo(v):
    """Le celle vuote dell'editor arrivano come None o NaN (float): valgono testo vuoto."""
    return " ".join(v.split()) if isinstance(v, str) else ""


def _segui_area_riservata(aree, prima, t, rinomine):
    """Se la categoria dell'area solo locale (SALUTE) viene rinominata, il blocco degli invii la segue: le sue
    sottocategorie stanno ora tutte sotto un'unica categoria con un altro nome."""
    nuove_l1 = {l1 for s in SEZIONI for l1 in t[s]}
    out = []
    for area in aree:
        if area.upper() in nuove_l1:
            out.append(area)
            continue
        destinazioni = {rinomine.get(k, cfg.da_chiave_cat(k))[0] for k, r in prima.items()
                        if r["l1"].upper() == area.upper()}
        destinazioni &= nuove_l1
        out.append(destinazioni.pop() if len(destinazioni) == 1 else area)
    return list(dict.fromkeys(out))


def salva(conn, nuove_righe):
    """Valida e salva la tassonomia modificata. nuove_righe: dict con sezione, l1, l2, originale
    (originale vuoto per le righe aggiunte). La stessa sottocategoria puo' stare sotto categorie diverse
    (CASA · Varie e ACQUISTI · Varie), non due volte nella stessa. Restituisce (errori, messaggi): se ci sono
    errori non salva nulla."""
    prima = {r["originale"]: r for r in righe(conn)}
    uso = utilizzo(conn)
    errori, rinomine, viste = [], {}, set()
    t = {s: {} for s in SEZIONI}
    for n, r in enumerate(nuove_righe, 1):
        l1 = _testo(r.get("l1")).upper()
        l2 = _testo(r.get("l2"))
        sez = _testo(r.get("sezione"))
        orig = _testo(r.get("originale"))
        if not (l1 or l2 or orig or sez):
            continue                                  # riga aggiunta e lasciata vuota
        if sez not in SEZIONI:
            errori.append("Riga %d: scegli la sezione (uscite, entrate o risparmio)." % n)
            continue
        if not l1 or not l2:
            errori.append("Riga %d: servono sia la categoria sia la sottocategoria." % n)
            continue
        if (l1, l2.lower()) in viste:
            errori.append("\"%s\" compare due volte." % cfg.etichetta((l1, l2)))
            continue
        viste.add((l1, l2.lower()))
        if orig and orig in prima:
            p = prima[orig]
            if p["predefinita"] and l2 != p["l2"]:
                errori.append("\"%s\" e' predefinita (la usano regole e configurazioni): si puo' spostare, "
                              "non rinominare." % p["l2"])
                continue
            if p["predefinita"] and sez != p["sezione"]:
                errori.append("\"%s\" e' predefinita: non puo' cambiare sezione." % p["l2"])
                continue
            if (l1, l2) != (p["l1"], p["l2"]):
                rinomine[orig] = (l1, l2)
        t[sez].setdefault(l1, []).append(l2)
    tenute = {_testo(r.get("originale")) for r in nuove_righe} - {""}
    for orig, r in prima.items():
        if orig in tenute:
            continue
        cat = (r["l1"], r["l2"])
        if r["predefinita"] and not any(x == r["l2"] for s in SEZIONI for v in t[s].values() for x in v):
            errori.append("\"%s\" e' predefinita: non si puo' eliminare." % r["l2"])
        elif uso.get(cat):
            errori.append("\"%s\" e' usata da %d voci salvate: prima uniscila a un'altra sottocategoria "
                          "(scegli Elimina e sposta le sue voci su un'altra)." % (cfg.etichetta(cat), uso[cat]))
    if errori:
        return errori, []
    nuova = {**t, "solo_locale": _segui_area_riservata(attuale(conn).get("solo_locale", ["SALUTE"]), prima, t, rinomine)}
    conn.execute("BEGIN")
    try:
        # prima su nomi provvisori: due voci che si scambiano il nome non si sovrappongono
        provvisori = {orig: ("~provvisoria %d" % i, cfg.da_chiave_cat(orig)[1]) for i, orig in enumerate(rinomine)}
        for orig, tmp in provvisori.items():
            _rinomina_dati(conn, cfg.da_chiave_cat(orig), tmp)
        for orig, tmp in provvisori.items():
            _rinomina_dati(conn, tmp, rinomine[orig])
        scrivi_impostazione(conn, CHIAVE, nuova)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    carica(conn)
    messaggi = ["\"%s\" diventa \"%s\"" % (cfg.etichetta(cfg.da_chiave_cat(k)), cfg.etichetta(v))
                for k, v in rinomine.items()]
    return [], messaggi


def unisci(conn, da, verso):
    """da, verso: (l1, l2). Sposta tutto cio' che usa `da` su `verso` e toglie `da` dalla tassonomia."""
    da, verso = cfg.come_cat(da, obbligatoria=True), cfg.come_cat(verso, obbligatoria=True)
    if da == verso:
        raise ValueError("scegli due sottocategorie diverse")
    t = attuale(conn)
    stesse = [x for s in SEZIONI for v in t[s].values() for x in v if x == da[1]]
    if da[1] in protette() and len(stesse) <= 1:
        raise ValueError("\"%s\" e' predefinita e non si puo' eliminare" % da[1])
    if not any(verso[1] in v for s in SEZIONI for l1, v in t[s].items() if l1 == verso[0]):
        raise ValueError("\"%s\" non esiste" % cfg.etichetta(verso))
    conn.execute("BEGIN")
    try:
        _rinomina_dati(conn, da, verso)
        for s in SEZIONI:
            for l1 in list(t[s]):
                if l1 == da[0]:
                    t[s][l1] = [x for x in t[s][l1] if x != da[1]]
                if not t[s][l1]:
                    del t[s][l1]
        scrivi_impostazione(conn, CHIAVE, t)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    carica(conn)


def ripristina(conn):
    """Torna alla tassonomia del file. Rifiuta se qualche voce salvata usa una categoria che li' non c'e'."""
    base = {(l1, l2) for s in SEZIONI for l1, v in predefinita()[s].items() for l2 in v}
    orfane = {cat: n for cat, n in utilizzo(conn).items() if cat not in base}
    if orfane:
        raise ValueError("queste sottocategorie aggiunte o spostate da te sono ancora usate: %s. Uniscile prima a "
                         "una predefinita." % ", ".join("%s (%d)" % (cfg.etichetta(c), n)
                                                       for c, n in sorted(orfane.items(), key=lambda x: str(x[0]))))
    conn.execute("DELETE FROM impostazioni WHERE chiave = ?", (CHIAVE,))
    carica(conn)


def esporta_yaml(conn) -> str:
    """La tassonomia in uso nello stesso formato di config/tassonomia.yaml, pronta per sostituirlo."""
    from datetime import date
    t = attuale(conn)
    dati = {"versione": 1}
    for s in SEZIONI:
        dati[s] = {l1: list(v) for l1, v in t[s].items() if v}
    dati["solo_locale"] = list(t.get("solo_locale", ["SALUTE"]))
    testa = ("# Tassonomia esportata da CashFlow Analyzer il %s.\n"
             "# Stesso formato di config/tassonomia.yaml: per renderla quella di partenza sostituisci quel file.\n"
             % date.today().isoformat())
    return testa + yaml.safe_dump(dati, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120)
