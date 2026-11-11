"""Pagine operative: import, revisione, movimenti."""

import pandas as pd
import streamlit as st

from .. import analisi as an
from .. import attributi as attr
from .. import configurazione as cfg
from .. import importa as imp
from .. import llm, servizi
from ..db import leggi_impostazione, transazione
from ..ricalcolo import GIORNI_CANDIDATI, MOTIVO_PAYPAL, candidati_paypal, cerca_pagamenti_paypal
from ..catena import scomponi_proposta
from ..llm import MODELLI_CLAUDE
from ..lettore import FileNonRiconosciuto
from ..numeri import euro
from ..ricalcolo import riepilogo
from .comune import (NATURE, chiave_tabella, conn, esegui_ricalcolo, etichetta_cat, filtri_sidebar, opzioni_categorie,
                     testo)

TIPI_CONTO = {"conto": "Conto corrente", "carta": "Carta di credito", "broker": "Conto titoli / broker",
              "deposito": "Conto deposito"}


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def importa():
    st.title("Importa estratti conto")
    st.caption("I file restano sul tuo PC: vengono letti, normalizzati e salvati solo nel database cifrato.")
    files = st.file_uploader("File esportati dalla banca (CSV o Excel)", type=["csv", "xlsx", "txt"],
                             accept_multiple_files=True)
    c = conn()
    if not leggi_impostazione(c, "titolari", []):
        st.warning("Prima di importare indica i titolari dei conti in **Impostazioni**: i loro nomi vengono tolti "
                   "dalle descrizioni (Credem li mette davanti all'esercente) e bloccano l'invio a Claude. "
                   "Se li aggiungi dopo, l'app ripulisce anche i movimenti gia' importati.",
                   icon=":material/person_alert:")
    visti = set()
    for f in files or []:
        with st.container(border=True):
            dati = f.getvalue()
            try:
                fl, suggerito, gia = imp.anteprima(c, f.name, dati)
            except FileNonRiconosciuto as e:
                st.error("**%s**: %s. Formati gestiti: Credem, Credit Agricole, Fineco (Excel), ING conto (Excel o CSV), ING carta di credito, Trade Republic, PayPal (report attivita' in CSV)." % (f.name, e))
                continue
            except Exception as e:
                st.error("**%s**: errore di lettura (%s)" % (f.name, e))
                continue
            # report e scelte fatte per questo file si riconoscono dall'impronta del contenuto, non dalla
            # posizione nell'elenco o dal nome: togliendo un file dall'elenco, conto e conferme degli altri non
            # si spostano, e un file diverso con lo stesso nome non eredita il report del precedente
            k = fl.hash_file[:16]
            if k in visti:
                st.info("**%s**: stesso contenuto di un file gia' in questo elenco, saltato." % f.name)
                continue
            visti.add(k)
            chiave_rep = "report_%s" % k
            st.markdown("**%s** · %s · %d movimenti letti, %d righe scartate" % (
                f.name, fl.profilo["nome"], len(fl.movimenti), len(fl.scartate)))
            for a in fl.avvisi:
                st.warning(a)
            if chiave_rep in st.session_state:
                for a in st.session_state[chiave_rep].get("avvisi") or []:
                    if a not in fl.avvisi:              # avvisi nati con l'import (quelli del file sono qui sopra)
                        st.warning(a)
                _mostra_report(st.session_state[chiave_rep])
                continue
            if gia:
                dove = ", ".join(nome for _, nome in imp.conti_del_file(c, fl.hash_file))
                st.info("Questo file risulta gia' importato nel conto %s: importandolo di nuovo nello stesso conto "
                        "le righe gia' presenti vengono ignorate." % dove)
            # anche i conti registrati a mano in Conti e copertura: al primo file diventano importati
            conti = c.execute("SELECT id, nome, importato, tipo FROM conti WHERE chiuso_il IS NULL OR id = ? "
                              "ORDER BY importato DESC, nome", (suggerito or 0,)).fetchall()
            nomi = {r["id"]: r["nome"] + ("" if r["importato"] else " (registrato a mano, ancora senza movimenti)")
                    for r in conti}
            opzioni = list(nomi) + [0]
            indice = opzioni.index(suggerito) if suggerito in opzioni else len(opzioni) - 1
            scelta = st.selectbox("Conto a cui appartiene il file", opzioni, index=indice, key="conto_%s" % k,
                                  format_func=lambda x: nomi.get(x, "Nuovo conto…"),
                                  help="I movimenti del file vengono registrati su questo conto. "
                                       "Proposto in base a IBAN o numero di conto letti nel file.")
            nuovo = {}
            if scelta == 0:
                a, b, cc = st.columns(3)
                nuovo["nome"] = a.text_input("Nome del conto", value=fl.profilo["banca"], key="nome_%s" % k)
                tipi = list(TIPI_CONTO)
                nuovo["tipo"] = b.selectbox("Tipo", tipi, index=tipi.index(fl.profilo.get("tipo_conto", "conto")),
                                            format_func=TIPI_CONTO.get, key="tipo_%s" % k)
                nuovo["perimetro"] = cc.selectbox("Perimetro", ["personale", "comune"], key="per_%s" % k,
                                                  format_func=lambda x: "Personale" if x == "personale" else "Conto comune")
                if nuovo["tipo"] == "carta":
                    altri = {r["id"]: r["nome"] for r in c.execute("SELECT id, nome FROM conti WHERE tipo = 'conto'")}
                    if altri:
                        # proposto: il conto della stessa banca che ha gli addebiti della carta di credito
                        prop = c.execute(
                            "SELECT co.id FROM conti co WHERE co.tipo = 'conto' AND lower(coalesce(co.banca, '')) = ? "
                            "ORDER BY EXISTS (SELECT 1 FROM movimenti m WHERE m.conto_id = co.id AND "
                            "m.tipo = 'addebito_carta_credito') DESC, co.id LIMIT 1",
                            ((fl.profilo.get("banca") or "").lower(),)).fetchone()
                        opz = [None] + list(altri)
                        nuovo["addebito"] = st.selectbox(
                            "Conto su cui viene addebitata la carta", opz, index=opz.index(prop[0]) if prop else 0,
                            format_func=lambda x: altri.get(x, "Non indicato"), key="add_%s" % k,
                            help="Serve ad abbinare l'addebito mensile della carta sul conto alle singole spese della "
                                 "carta, cosi' non si contano due volte.")
            # un estratto di carta in un conto che non e' una carta (o il contrario) fa contare due volte le spese
            tipo_scelto = nuovo["tipo"] if scelta == 0 else next(r["tipo"] for r in conti if r["id"] == scelta)
            avviso = imp.avviso_tipo_conto(fl.profilo, tipo_scelto)
            # lo stesso file gia' in un altro conto: la deduplica vale dentro un conto, qui i movimenti raddoppiano
            altrove = imp.avviso_file_altrove(c, fl.hash_file, scelta or None)
            via_libera = True
            for testo_avviso in (avviso, altrove):
                if testo_avviso:
                    st.warning(testo_avviso)
            if avviso or altrove:
                via_libera = st.checkbox("Importa comunque in questo conto", key="forza_%s" % k)
            if st.button("Importa", key="imp_%s" % k, type="primary", disabled=not via_libera):
                if scelta == 0:
                    if not nuovo["nome"].strip():
                        st.error("Indica il nome del conto.")
                        st.stop()
                    if imp.nome_conto_esistente(c, nuovo["nome"]):
                        st.error("Esiste gia' un conto chiamato \"%s\": sceglilo nell'elenco \"Conto a cui appartiene "
                                 "il file\" invece di crearne uno nuovo." % nuovo["nome"].strip())
                        st.stop()
                    scelta = imp.crea_conto(c, nuovo["nome"].strip(), fl.profilo["id"], nuovo["tipo"], nuovo["perimetro"],
                                            fl.profilo["banca"], fl.metadati.get("iban"), fl.metadati.get("numero"),
                                            nuovo.get("addebito"))
                prima = riepilogo(c)
                with st.spinner("Import e ricalcolo…"):
                    try:
                        rep = imp.importa(c, scelta, f.name, dati)
                    except Exception:
                        if nuovo:                       # import non riuscito: il conto appena creato non resta vuoto
                            c.execute("DELETE FROM conti WHERE id = ? AND NOT EXISTS (SELECT 1 FROM import_file "
                                      "WHERE conto_id = ?)", (scelta, scelta))
                        raise
                    dopo = esegui_ricalcolo()
                rep["prima"], rep["dopo"] = prima, dopo
                st.session_state[chiave_rep] = rep
                st.rerun()
    _storico_import(c)


def _storico_import(c):
    st.subheader("File importati")
    elenco = imp.elenco_import(c)
    if not elenco:
        st.caption("Nessun file importato.")
        return
    df = pd.DataFrame(elenco)
    df["importato_il"] = df["importato_il"].str[:16].str.replace("T", " ")
    st.dataframe(df[["importato_il", "nome_file", "conto", "data_min", "data_max", "nuove", "duplicate", "quadratura"]],
                 hide_index=True, width="stretch",
                 column_config={"importato_il": "Importato il", "nome_file": "File", "conto": "Conto",
                                "data_min": "Dal", "data_max": "Al", "nuove": "Righe nuove",
                                "duplicate": "Gia' presenti", "quadratura": "Quadratura"})
    if msg := st.session_state.pop("esito_annulla", None):
        st.success(msg)
    with st.expander("Annulla un import (file sbagliato o caricato sul conto sbagliato)"):
        etich = {r["id"]: "%s · %s · %s – %s (%s)" % (r["nome_file"], r["conto"], r["data_min"], r["data_max"],
                                                        r["importato_il"][:16].replace("T", " ")) for r in elenco}
        scelto = st.selectbox("File da togliere", list(etich), format_func=etich.get, key="annulla_scelta")
        r = next(x for x in elenco if x["id"] == scelto)
        unico = sum(1 for x in elenco if x["conto_id"] == r["conto_id"]) == 1
        st.caption("Vengono tolti i movimenti arrivati con questo file e le decisioni prese su di essi. "
                   "I movimenti presenti anche in un altro file dello stesso conto restano.")
        elimina_conto = unico and st.checkbox("Elimina anche il conto \"%s\": non ha altri file" % r["conto"],
                                              key="annulla_conto")
        conferma = st.checkbox("Confermo: togli questo import", key="annulla_ok")
        if st.button("Annulla l'import", disabled=not conferma):
            with st.spinner("Annullo e ricalcolo…"):
                esito = imp.annulla_import(c, scelto, elimina_conto=elimina_conto)
                esegui_ricalcolo()
            msg = "Import di %s annullato: %d movimenti tolti" % (esito["file"], esito["tolti"])
            if esito["mantenuti"]:
                msg += ", %d mantenuti perche' presenti in un altro file" % esito["mantenuti"]
            if esito["conto_eliminato"]:
                msg += "; conto \"%s\" eliminato" % esito["conto_eliminato"]
            msg += "."
            if esito["da_ricaricare"]:
                msg += (" Ricarica questi file dello stesso conto per ripristinare eventuali righe in comune: %s."
                        % ", ".join(esito["da_ricaricare"]))
            if not esito["conto_eliminato"]:
                msg += " Controlla IBAN e numero del conto in Conti e copertura, se li aveva presi da questo file."
            st.session_state["esito_annulla"] = msg
            for k in [k for k in st.session_state if k.startswith("report_")] + ["annulla_ok", "annulla_conto"]:
                st.session_state.pop(k, None)
            st.rerun()


def _mostra_report(r):
    st.success("Importate %d righe nuove (%d gia' presenti) · periodo %s – %s" % (
        r["nuove"], r["duplicate"], r["periodo"][0], r["periodo"][1]), icon=":material/check_circle:")
    st.markdown("Quadratura dei saldi: **%s**" % r["quadratura"])
    if r.get("titolare_aggiunto"):
        st.info("Titolare letto dal file e aggiunto in Impostazioni: **%s**%s" % (
            r["titolare_aggiunto"].title(),
            " (nome tolto da %d movimenti gia' importati)" % r["nomi_ripuliti"] if r.get("nomi_ripuliti") else ""))
    if r["scartate"]:
        with st.expander("%d righe scartate" % len(r["scartate"])):
            st.dataframe(pd.DataFrame(r["scartate"], columns=["Riga", "Motivo"]), hide_index=True)
    p, d = r.get("prima") or {}, r.get("dopo") or {}
    novita = []
    ab = d.get("trasferimenti", {}).get("abbinato", 0) - p.get("trasferimenti", {}).get("abbinato", 0)
    if ab:
        novita.append("%+d trasferimenti abbinati" % ab)
    det = d.get("contenitori", {}).get("dettagliato", 0) - p.get("contenitori", {}).get("dettagliato", 0)
    if det:
        novita.append("%+d addebiti della carta ora dettagliati" % det)
    riv = d.get("da_rivedere", 0) - p.get("da_rivedere", 0)
    novita.append("%+d movimenti da rivedere (totale %d)" % (riv, d.get("da_rivedere", 0)))
    st.markdown("Dopo il ricalcolo: " + "; ".join(novita))


# ---------------------------------------------------------------------------
# Revisione
# ---------------------------------------------------------------------------
def _conferma_proposte(n, senza, diverse, nomi, chiave, cosa="la categoria proposta",
                       coda="Per dare a tutti la stessa categoria usa la scelta qui sotto."):
    """Pulsante per confermare insieme le voci selezionate, ognuna con la propria categoria (proposta o gia'
    presente). nomi: (singolare, plurale). Restituisce True se premuto."""
    x, y = st.columns([2, 3], vertical_alignment="center")
    premuto = x.button("Conferma %s di %d %s" % (cosa, n, nomi[0] if n == 1 else nomi[1]),
                       key=chiave, type="primary" if diverse > 1 else "secondary")
    note = ["ognuno con la sua (%d categorie diverse)" % diverse if diverse > 1 else "tutti nella stessa categoria"]
    if senza:
        note.append("%d senza proposta rest%s da classificare" % (senza, "a" if senza == 1 else "ano"))
    y.caption("; ".join(note).capitalize() + ". " + coda)
    return premuto


def _cat_da_proposta(p):
    c = scomponi_proposta(testo(p))["cat"]
    return tuple(c) if c and c[0] else None


def _come_cat(x):
    """Categoria iniziale di una scelta: coppia, oppure sottocategoria da risolvere (es. 'Contributo ricevuto')."""
    if not x:
        return None
    if isinstance(x, (tuple, list)):
        return tuple(x) if x[1] else None
    return cfg.tassonomia().risolvi(x)


def _scegli_categoria(etichetta, key, proposta=None, sezioni=("uscite", "entrate", "risparmio"), iniziale=None):
    """Scelta della categoria: restituisce la coppia (categoria, sottocategoria) o None."""
    opz = opzioni_categorie(sezioni)
    ini = _come_cat(iniziale) or _cat_da_proposta(proposta)
    idx = opz.index(ini) if ini in opz else None
    return st.selectbox(etichetta, opz, index=idx, format_func=etichetta_cat, key=key, placeholder="Scegli la categoria")


def revisione():
    st.title("Revisione")
    st.caption("Tutti i conti e i perimetri. Ogni decisione si applica subito e ricalcola le analisi.")
    c = conn()
    t1, t2, t_pp, t3 = st.tabs(["Esercenti da classificare", "Bonifici e trasferimenti", "Addebiti PayPal",
                                "Altri movimenti da rivedere"])

    with t1:
        _esercenti_da_classificare(c)

    with t2:
        # coppie proposte
        coppie = c.execute(
            "SELECT t.mov_id, t.controparte_mov_id, m.data, m.importo_cent, ca.nome AS conto, "
            "m2.data AS data2, cb.nome AS conto2 FROM trasferimenti t JOIN movimenti m ON m.id = t.mov_id "
            "JOIN movimenti m2 ON m2.id = t.controparte_mov_id JOIN conti ca ON ca.id = m.conto_id "
            "JOIN conti cb ON cb.id = m2.conto_id WHERE t.stato NOT IN ('abbinato','contributo') "
            "AND t.controparte_mov_id IS NOT NULL AND m.importo_cent < 0").fetchall()
        if coppie:
            st.subheader("Possibili trasferimenti tra tuoi conti")
            for k, r in enumerate(coppie):
                a, b, cc = st.columns([6, 1, 1])
                a.markdown("%s · %s → %s (%s) · **%s**" % (r["data"], r["conto"], r["conto2"], r["data2"],
                                                          euro(-r["importo_cent"])))
                if b.button("Sì", key="si_%d" % k):
                    servizi.decidi_abbinamento(c, r["mov_id"], r["controparte_mov_id"], "si")
                    esegui_ricalcolo("Trasferimento confermato")
                    st.rerun()
                if cc.button("No", key="no_%d" % k):
                    servizi.decidi_abbinamento(c, r["mov_id"], r["controparte_mov_id"], "no")
                    esegui_ricalcolo("Coppia scartata")
                    st.rerun()
                with a.expander("Dettaglio dei due movimenti"):
                    _tabella_movimenti(c, "m.id IN (?, ?)", [r["mov_id"], r["controparte_mov_id"]], "det_cp_%d" % k)
        familiari = _spese_familiari(c)
        # beneficiari a proprio nome senza contropartita
        prop = c.execute(
            "SELECT t.beneficiario, max(m.controparte_nome) AS nome, count(*) AS n, sum(m.importo_cent) AS tot, "
            "sum(m.importo_cent > 0) AS n_in, coalesce(sum(CASE WHEN m.importo_cent > 0 THEN m.importo_cent END), 0) AS tot_in, "
            "group_concat(DISTINCT co.nome) AS conti "
            "FROM trasferimenti t JOIN movimenti m ON m.id = t.mov_id JOIN conti co ON co.id = m.conto_id "
            "WHERE t.stato = 'proposto_proprio' AND t.beneficiario IS NOT NULL GROUP BY t.beneficiario ORDER BY n DESC").fetchall()
        if prop:
            st.subheader("Trasferimenti a tuo nome senza contropartita")
            st.caption("Bonifici da o verso un conto intestato a te che non risulta tra quelli caricati. Una risposta "
                       "per beneficiario vale per tutti i suoi movimenti, in entrata e in uscita. I versamenti sul conto "
                       "comune non compaiono qui: sono entrate del conto comune (Contributo ricevuto).")
            esiti = {"conto": "Un mio conto gia' censito (lo scelgo qui sotto)",
                     "non_caricato": "Un mio conto non ancora caricato (giroconto)",
                     "non_tracciato": "Un mio conto che non traccio (risparmio: accantonamento o rientro)",
                     "altra_persona": "Un'altra persona: sono entrate e uscite vere"}
            miei = {r["id"]: r for r in c.execute("SELECT id, nome, tipo, banca FROM conti WHERE tipo <> 'carta' ORDER BY nome")}
            for k, r in enumerate(prop):
                with st.container(border=True):
                    conto = (" · conto …%s" % r["beneficiario"][-4:]) if r["beneficiario"].startswith("IBAN:") else ""
                    n_out, tot_out = r["n"] - r["n_in"], r["tot"] - r["tot_in"]
                    parti = []
                    if r["n_in"]:
                        parti.append("%d in entrata (%s)" % (r["n_in"], euro(r["tot_in"])))
                    if n_out:
                        parti.append("%d in uscita (%s)" % (n_out, euro(tot_out)))
                    st.markdown("**%s**%s · %s · su %s" % (r["nome"] or "Senza nome", conto, " · ".join(parti), r["conti"]))
                    with st.expander("Dettaglio dei %d movimenti" % r["n"]):
                        scelti = _tabella_movimenti(
                            c, "m.id IN (SELECT mov_id FROM trasferimenti WHERE beneficiario = ? AND stato = "
                               "'proposto_proprio')", [r["beneficiario"]], "det_pp_%d" % k, selezione=True)
                        st.caption("Seleziona delle righe per indicare il conto solo per quei movimenti (es. se a tuo "
                                   "nome hai piu' conti e i bonifici vanno a conti diversi).")
                    scelta = st.radio("Che conto c'e' dall'altra parte?", list(esiti), format_func=esiti.get,
                                      horizontal=True, index=None, key="dest_%d" % k)
                    conto_dest, ambito = None, "tutti"
                    if scelta == "conto":
                        a1, a2 = st.columns(2)
                        conto_dest = a1.selectbox(
                            "Quale conto", list(miei), index=None, placeholder="Scegli il conto", key="destc_%d" % k,
                            format_func=lambda i: "%s · %s" % (miei[i]["nome"], TIPI_CONTO.get(miei[i]["tipo"], miei[i]["tipo"])))
                        if scelti:
                            ambito = a2.radio("Vale per", ["selezionati", "tutti"], key="desta_%d" % k, format_func={
                                "selezionati": "Solo i %d movimenti selezionati nel dettaglio" % len(scelti),
                                "tutti": "Tutti i movimenti di questo beneficiario, anche futuri"}.get)
                        else:
                            a2.caption("Vale per tutti i movimenti di questo beneficiario, anche quelli importati in "
                                       "futuro. Per indicarlo solo per alcuni, selezionali nel dettaglio.")
                        st.caption("L'app cerca la contropartita su quel conto come se il bonifico riportasse il suo "
                                   "IBAN: se la trova li abbina, altrimenti tratta il bonifico come un trasferimento "
                                   "verso quel conto (risparmio se e' un conto deposito o un broker).")
                        if r["beneficiario"].startswith("IBAN:"):
                            st.caption("Questi bonifici riportano l'IBAN …%s: se e' quello del conto scelto, conviene "
                                       "scriverlo nel conto in **Conti e copertura**, cosi' l'app lo riconosce da sola "
                                       "anche negli altri bonifici." % r["beneficiario"][-4:])
                    pronto = scelta and (scelta != "conto" or conto_dest is not None)
                    if pronto and st.button("Salva", key="sdest_%d" % k):
                        if scelta != "conto":
                            servizi.decidi_destinazione(c, r["beneficiario"], scelta)
                        elif ambito == "selezionati":
                            servizi.decidi_conto_destinazione(c, conto_dest, mov_ids=scelti)
                        else:
                            servizi.decidi_conto_destinazione(c, conto_dest, beneficiario=r["beneficiario"])
                        esegui_ricalcolo("Destinazione salvata")
                        st.rerun()
        # bonifici a/da terzi senza categoria
        terzi = c.execute(
            "SELECT a.chiave, max(m.controparte_nome) AS nome, count(*) AS n, sum(a.importo_cent) AS tot, "
            "sum(a.importo_cent > 0) AS positivi, "
            "coalesce(sum(CASE WHEN a.importo_cent > 0 THEN a.importo_cent END), 0) AS tot_in, "
            "min(a.perimetro) AS per_min, max(a.perimetro) AS per_max, group_concat(DISTINCT co.nome) AS conti "
            "FROM analisi a JOIN movimenti m ON m.id = a.mov_id JOIN conti co ON co.id = a.conto_id "
            "JOIN trasferimenti t ON t.mov_id = a.mov_id WHERE t.stato = 'terzi' AND a.l2 IS NULL "
            "AND a.componente = 'principale' GROUP BY a.chiave ORDER BY n DESC").fetchall()
        if terzi:
            st.subheader("Bonifici con altre persone o aziende")
            st.caption("Scegli la categoria: vale per tutti i bonifici con lo stesso beneficiario. I bonifici in entrata "
                       "contano gia' come entrate; con una categoria di spesa (es. un amico che restituisce la sua parte "
                       "di una cena) diventano un rimborso. Se e' un tuo familiare che alimenta il conto comune, indicalo in "
                       "**Impostazioni > Familiari**: i suoi versamenti diventano contributi in automatico.")
            for k, r in enumerate(terzi):
                if not r["chiave"]:
                    continue
                with st.container(border=True):
                    n_out, tot_out = r["n"] - r["positivi"], r["tot"] - r["tot_in"]
                    parti = []
                    if r["positivi"]:
                        parti.append("%d in entrata (%s)" % (r["positivi"], euro(r["tot_in"])))
                    if n_out:
                        parti.append("%d in uscita (%s)" % (n_out, euro(tot_out)))
                    st.markdown("**%s** · %s · su %s" % (r["nome"] or "Senza nome", " · ".join(parti), r["conti"]))
                    proposta = None
                    if r["positivi"] == r["n"]:
                        proposta = "Contributo ricevuto" if r["per_min"] == r["per_max"] == "comune" else "Altre entrate"
                    with st.expander("Dettaglio dei %d movimenti" % r["n"]):
                        _tabella_movimenti(c, "a.chiave = ? AND a.l2 IS NULL AND m.id IN (SELECT mov_id FROM trasferimenti "
                                              "WHERE stato = 'terzi')", [r["chiave"]], "det_tz_%d" % k)
                    cat = _scegli_categoria("Categoria", "terzi_%d_%s" % (k, r["chiave"]), iniziale=proposta)
                    if cat and st.button("Applica", key="aterzi_%d" % k):
                        servizi.crea_regola(c, r["chiave"], cat, "chiave", "esatto")
                        esegui_ricalcolo("Regola creata")
                        st.rerun()
        if not (coppie or prop or terzi or familiari):
            st.success("Nessun bonifico da rivedere.", icon=":material/done_all:")
        _conti_indicati(c)

    with t_pp:
        if not _addebiti_paypal(c):
            st.success("Nessun addebito PayPal da ricondurre al report PayPal.", icon=":material/done_all:")
    with t3:
        altri = pd.DataFrame(an.altri_da_rivedere(c, MOTIVO_PAYPAL))
        if altri.empty:
            st.success("Nessun altro movimento da rivedere.", icon=":material/done_all:")
        else:
            giro_a = st.session_state.get("altri_giro", 0)
            # categoria da confermare: quella gia' presente, altrimenti la proposta
            attuale = [(a1, a2) if isinstance(a2, str) else _cat_da_proposta(p)
                       for a1, a2, p in zip(altri["l1"], altri["l2"], altri["proposta"])]
            vista = pd.DataFrame({"Data": altri["data"], "Conto": altri["conto"],
                                  "Importo": [euro(v) for v in altri["importo_cent"]],
                                  "Conta come": [NATURE.get(n, n) for n in altri["natura"]],
                                  "Categoria": [etichetta_cat(x) if x and isinstance(a2, str) else ""
                                                for x, a2 in zip(attuale, altri["l2"])],
                                  "Proposta": [etichetta_cat(x) if x and not isinstance(a2, str) else ""
                                               for x, a2 in zip(attuale, altri["l2"])],
                                  "Motivo": altri["motivo"].fillna(""), "Descrizione": altri["descrizione"].fillna("")})
            st.caption("Seleziona una riga per modificarla, o piu' righe per confermarle insieme cosi' come sono.")
            sel = st.dataframe(vista, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
                               key=chiave_tabella("sel_altri_%d" % giro_a, altri["mov_id"]), height=min(460, 38 + 35 * len(vista)))
            righe_a = sel.selection.rows if sel else []
            if righe_a:
                # si confermano la categoria presente o proposta; senza categoria, solo i movimenti che non ne
                # hanno bisogno (trasferimenti interni, esclusi)
                conferme = []
                for i in righe_a:
                    nat, cat = altri.iloc[i]["natura"], attuale[i]
                    if cat or nat in ("trasferimento_interno", "escluso", "contenitore"):
                        conferme.append((altri.iloc[i]["mov_id"], cat, nat))
                if len(righe_a) > 1 or conferme:
                    if conferme and _conferma_proposte(len(conferme), len(righe_a) - len(conferme),
                                                       len({x[1] or x[2] for x in conferme}),
                                                       ("movimento", "movimenti"), "altri_conf_%d" % giro_a,
                                                       "la categoria presente o proposta",
                                                       "Per cambiarla seleziona una riga sola."):
                        with transazione(c):
                            for mid, cat, nat in conferme:
                                servizi.decidi_movimento(c, mid, cat, nat)
                        st.session_state["altri_giro"] = giro_a + 1
                        esegui_ricalcolo("%d movimenti confermati" % len(conferme))
                        st.rerun()
                if len(righe_a) == 1:
                    _modifica_movimento(altri.iloc[righe_a[0]], "altri")


DETTAGLIO_SQL = (
    "SELECT m.id AS mov_id, m.data, co.nome AS conto, m.importo_cent, m.tipo, m.controparte_nome, m.controparte_iban, "
    "m.esercente, m.descrizione, f.nome_file, f.importato_il, a.natura, a.l1, a.l2, a.chiave, "
    "(d.mov_id IS NOT NULL AND d.l2 IS NOT NULL) AS scelta_sul_movimento "
    "FROM movimenti m JOIN conti co ON co.id = m.conto_id LEFT JOIN import_file f ON f.id = m.import_id "
    "LEFT JOIN analisi a ON a.mov_id = m.id AND a.componente = 'principale' "
    "LEFT JOIN decisioni d ON d.mov_id = m.id ")


def _addebiti_paypal(c):
    """Addebiti PayPal su conti e carte senza la ricarica corrispondente nel report PayPal: si riconducono a un
    pagamento del report (stesso importo, nome dell'esercente simile) o si segnano come spese normali.
    Restituisce quanti sono."""
    righe = pd.DataFrame([dict(r) for r in c.execute(
        "SELECT a.mov_id, a.data, co.nome AS conto, a.importo_cent, m.esercente, m.descrizione "
        "FROM analisi a JOIN movimenti m ON m.id = a.mov_id JOIN conti co ON co.id = a.conto_id "
        "WHERE a.componente = 'principale' AND a.da_rivedere = 1 AND a.motivo = ? ORDER BY a.data DESC",
        [MOTIVO_PAYPAL])])
    if righe.empty:
        _riconduzioni_paypal(c)
        return 0
    st.caption("Addebiti PayPal sui tuoi conti o carte per cui nel report PayPal non c'e' una ricarica dello stesso "
               "importo entro una settimana (la banca puo' registrarli qualche giorno dopo). Se la spesa e' anche nel "
               "report, va ricondotta al suo pagamento: l'addebito non conta piu' e resta la spesa del report, con il "
               "nome vero dell'esercente. Le proposte cercano lo stesso importo entro %d giorni e un nome simile; per gli "
               "addebiti SEPA di PayPal, che non riportano l'esercente, la somiglianza non e' valutabile e conta "
               "l'unico pagamento con lo stesso importo." % GIORNI_CANDIDATI)
    cand = candidati_paypal(c, list(righe["mov_id"]))
    prop = {k: (v[0] if v and v[0].get("proposta") else None) for k, v in cand.items()}

    def descr(x):
        return "%s · %s · %s" % (x["nome"] or "Senza nome", _data_it(x["data"]),
                                 ("%+d giorn%s" % (x["giorni"], "o" if abs(x["giorni"]) == 1 else "i")) if x["giorni"] else "stesso giorno")
    vista = pd.DataFrame({
        "Data": righe["data"], "Conto": righe["conto"],
        "Addebito": [testo(e) or testo(d) for e, d in zip(righe["esercente"], righe["descrizione"])],
        "Importo": [euro(v) for v in righe["importo_cent"]],
        "Proposta nel report PayPal": [descr(prop[k]) if prop.get(k) else
                                       ("%d possibili, da scegliere" % len(cand.get(k, [])) if cand.get(k) else
                                        "nessun pagamento con questo importo")
                                       for k in righe["mov_id"]],
        "Somiglianza del nome": [_somiglianza_it(prop[k])
                                 if prop.get(k) else "" for k in righe["mov_id"]]})
    giro = st.session_state.get("pp_giro", 0)
    st.caption("Seleziona una riga per scegliere il pagamento, o piu' righe per accettare le proposte.")
    ev = st.dataframe(vista, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
                      key=chiave_tabella("pp_sel_%d" % giro, righe["mov_id"]), height=min(400, 38 + 35 * len(vista)))
    scelte = [i for i in (getattr(getattr(ev, "selection", None), "rows", []) or []) if i < len(righe)]

    def fatto(msg):
        st.session_state["pp_giro"] = giro + 1
        esegui_ricalcolo(msg)
        st.rerun()
    if len(scelte) > 1:
        con_prop = [righe["mov_id"].iloc[i] for i in scelte if prop.get(righe["mov_id"].iloc[i])]
        st.markdown("**%d addebiti selezionati**, %d con una proposta" % (len(scelte), len(con_prop)))
        if st.button("Riconduci %d addebiti alle proposte" % len(con_prop), type="primary", disabled=not con_prop,
                     key="pp_tutte_%d" % giro):
            with transazione(c):
                for k in con_prop:
                    servizi.riconduci_paypal(c, k, prop[k]["id"])
            fatto("%d addebiti PayPal ricondotti al report" % len(con_prop))
    elif len(scelte) == 1:
        k = righe["mov_id"].iloc[scelte[0]]
        r = righe.iloc[scelte[0]]
        lista = cand.get(k, [])
        st.markdown("**%s** · %s · %s · %s" % (testo(r["esercente"]) or testo(r["descrizione"]), r["conto"],
                                               _data_it(r["data"]), euro(r["importo_cent"])))
        if lista:
            opz = {x["id"]: x for x in lista}
            scelto = st.radio("Pagamento nel report PayPal (stesso importo)", list(opz), key="pp_scelta_%d_%s" % (giro, k),
                              index=0 if prop.get(k) else None,
                              format_func=lambda i: "%s · somiglianza %s" % (descr(opz[i]), _somiglianza_it(opz[i])))
        else:
            scelto = None
            st.caption("Nel report PayPal non c'e' un pagamento con questo importo entro %d giorni: se il report copre "
                       "quel periodo, e' probabilmente una spesa che non passa dal report (per esempio pagata con "
                       "PayPal da un altro conto)." % GIORNI_CANDIDATI)
        x, y = st.columns(2)
        if x.button("Riconduci a questo pagamento", type="primary", disabled=scelto is None, key="pp_ok_%d" % giro):
            servizi.riconduci_paypal(c, k, scelto)
            fatto("Addebito ricondotto al report PayPal")
        if y.button("Non e' nel report: e' una spesa normale", key="pp_no_%d" % giro,
                    help="L'addebito resta una spesa del conto e l'avviso sparisce"):
            servizi.riconduci_paypal(c, k, None)
            fatto("Addebito segnato come spesa normale")
        with st.expander("Non trovi il pagamento? Cerca in tutto il report PayPal", icon=":material/search:"):
            st.caption("Qui compaiono tutte le uscite del report, di qualunque tipo e importo, entro i giorni scelti. "
                       "Se l'importo e' diverso (es. pagamento in valuta), conta quello del report.")
            u, v = st.columns([3, 2])
            cerca = u.text_input("Nome o descrizione contiene", key="pp_cerca_%d_%s" % (giro, k),
                                 placeholder="es. una parte del nome dell'esercente")
            giorni = v.slider("Entro quanti giorni", 1, 120, 30, key="pp_gg_%d_%s" % (giro, k))
            trovati = cerca_pagamenti_paypal(c, k, cerca, giorni)
            if not trovati:
                st.caption("Nessuna uscita nel report PayPal con questi criteri.")
            else:
                tv = pd.DataFrame({
                    "Data": [_data_it(x["data"]) for x in trovati], "Nome": [x["nome"] or "" for x in trovati],
                    "Importo": [euro(x["importo_cent"]) for x in trovati],
                    "Differenza": ["" if not x["differenza"] else euro(x["differenza"]) for x in trovati],
                    "Giorni": [x["giorni"] for x in trovati],
                    "Somiglianza": [_somiglianza_it(x) for x in trovati],
                    "Gia' abbinato a": [x["gia_abbinato"] or "" for x in trovati],
                    "Descrizione": [x["descrizione"] or "" for x in trovati]})
                ev2 = st.dataframe(tv, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                                   key=chiave_tabella("pp_trova_%d_%s" % (giro, k), [x["id"] for x in trovati]),
                                   height=min(320, 38 + 35 * len(tv)))
                sel2 = [i for i in (getattr(getattr(ev2, "selection", None), "rows", []) or []) if i < len(trovati)]
                if sel2 and trovati[sel2[0]]["gia_abbinato"]:
                    st.caption("Questo pagamento e' gia' abbinato a un altro addebito: ricondurlo qui toglie "
                               "quell'abbinamento, e l'altro addebito tornera' da controllare.")
                if st.button("Riconduci al pagamento selezionato", disabled=not sel2, key="pp_ok2_%d" % giro):
                    servizi.riconduci_paypal(c, k, trovati[sel2[0]]["id"])
                    fatto("Addebito ricondotto al report PayPal")
    _riconduzioni_paypal(c)
    return len(righe)


def _riconduzioni_paypal(c):
    """Riconduzioni fatte a mano: elenco e possibilita' di toglierle."""
    righe = [dict(r) for r in c.execute(
        "SELECT x.addebito_id, a.data, co.nome AS conto, a.importo_cent, coalesce(a.esercente, a.descrizione) AS addebito, "
        "p.data AS data_pp, p.esercente AS nome_pp FROM abbinamenti_paypal x JOIN movimenti a ON a.id = x.addebito_id "
        "JOIN conti co ON co.id = a.conto_id LEFT JOIN movimenti p ON p.id = x.paypal_id ORDER BY a.data DESC")]
    if not righe:
        return
    with st.expander("Addebiti PayPal ricondotti a mano (%d)" % len(righe), icon=":material/link:"):
        ev = st.dataframe(pd.DataFrame({
            "Data": [r["data"] for r in righe], "Conto": [r["conto"] for r in righe],
            "Addebito": [r["addebito"] or "" for r in righe], "Importo": [euro(r["importo_cent"]) for r in righe],
            "Nel report PayPal": [("%s · %s" % (r["nome_pp"] or "Senza nome", _data_it(r["data_pp"])))
                                  if r["data_pp"] else "non c'e': spesa normale" for r in righe]}),
            hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
            key=chiave_tabella("pp_fatti", [r["addebito_id"] for r in righe]))
        scelte = [righe[i] for i in (getattr(getattr(ev, "selection", None), "rows", []) or []) if i < len(righe)]
        if st.button("Togli i selezionati" if scelte else "Seleziona le righe da togliere", disabled=not scelte,
                     key="pp_togli"):
            with transazione(c):
                for r in scelte:
                    servizi.togli_riconduzione_paypal(c, r["addebito_id"])
            esegui_ricalcolo("Riconduzioni tolte: gli addebiti tornano da controllare")
            st.rerun()


def _somiglianza_it(x):
    if x["livello"] == "non valutabile":
        return "non valutabile"
    return "%s (%d%%)" % (x["livello"], round(100 * x["somiglianza"]))


def _data_it(d):
    return "%s/%s/%s" % (d[8:10], d[5:7], d[:4]) if d else ""


def _conti_indicati(c):
    """Conti indicati a mano per i bonifici a proprio nome senza IBAN: elenco e possibilita' di toglierli."""
    righe = [dict(r) for r in c.execute(
        "SELECT 'b' AS ambito, d.beneficiario AS rif, co.nome AS conto, "
        "(SELECT max(m.controparte_nome) FROM trasferimenti t JOIN movimenti m ON m.id = t.mov_id "
        " WHERE t.beneficiario = d.beneficiario) AS chi, NULL AS data, NULL AS importo_cent "
        "FROM destinazioni_conti d JOIN conti co ON co.id = d.conto_id "
        "UNION ALL SELECT 'm', d.mov_id, co.nome, m.controparte_nome, m.data, m.importo_cent "
        "FROM destinazioni_movimenti d JOIN conti co ON co.id = d.conto_id JOIN movimenti m ON m.id = d.mov_id "
        "ORDER BY 1, 5 DESC")]
    if not righe:
        return
    with st.expander("Conti indicati a mano per i bonifici (%d)" % len(righe), icon=":material/account_balance:"):
        ev = st.dataframe(pd.DataFrame({
            "Vale per": ["tutti i movimenti di questo beneficiario" if r["ambito"] == "b" else "un movimento"
                         for r in righe],
            "Controparte": [r["chi"] or r["rif"].split(":", 1)[-1] for r in righe],
            "Data": [r["data"] or "" for r in righe],
            "Importo": [euro(r["importo_cent"]) if r["importo_cent"] is not None else "" for r in righe],
            "Conto indicato": [r["conto"] for r in righe]}),
            hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
            key=chiave_tabella("conti_indicati", [r["rif"] for r in righe]))
        scelte = [righe[i] for i in (getattr(getattr(ev, "selection", None), "rows", []) or []) if i < len(righe)]
        if st.button("Togli i selezionati" if scelte else "Seleziona le righe da togliere", disabled=not scelte,
                     key="togli_indicati"):
            with transazione(c):
                for r in scelte:
                    if r["ambito"] == "b":
                        c.execute("DELETE FROM destinazioni_conti WHERE beneficiario = ?", (r["rif"],))
                    else:
                        c.execute("DELETE FROM destinazioni_movimenti WHERE mov_id = ?", (r["rif"],))
            esegui_ricalcolo("Indicazioni tolte: i bonifici tornano da rivedere")
            st.rerun()


def _tabella_movimenti(c, condizione, parametri, chiave, selezione=False):
    """Tabella con i riferimenti di ogni movimento: data, conto, importo, controparte, IBAN, descrizione
    completa e file da cui arriva. Serve a decidere sapendo di quali movimenti si tratta. Con selezione=True le
    righe si selezionano e si restituiscono gli id dei movimenti scelti."""
    det = pd.DataFrame([dict(r) for r in c.execute(DETTAGLIO_SQL + "WHERE " + condizione + " ORDER BY m.data DESC",
                                                   parametri)])
    if det.empty:
        st.caption("Nessun movimento.")
        return []
    extra = {"on_select": "rerun", "selection_mode": "multi-row"} if selezione else {}
    ev = st.dataframe(pd.DataFrame({
        "Data": det["data"], "Conto": det["conto"], "Importo": [euro(v) for v in det["importo_cent"]],
        "Controparte": det["controparte_nome"].fillna(det["esercente"]).fillna(""),
        "IBAN controparte": det["controparte_iban"].fillna(""), "Descrizione": det["descrizione"].fillna(""),
        "Ora conta come": [NATURE.get(n, n or "") + ((" · " + etichetta_cat((a, l))) if isinstance(l, str) else "")
                           for n, a, l in zip(det["natura"], det["l1"], det["l2"])],
        "Caricamento": [("%s (%s)" % (f, d[:10])) if isinstance(f, str) else "" for f, d in
                        zip(det["nome_file"], det["importato_il"].fillna(""))]}),
        hide_index=True, width="stretch", height=min(400, 38 + 35 * len(det)),
        key=chiave_tabella(chiave, det["mov_id"]), **extra)
    if not selezione:
        return []
    righe = list(getattr(getattr(ev, "selection", None), "rows", []) or [])
    return [det["mov_id"].iloc[i] for i in righe if i < len(det)]


RESTITUISCE = "__restituzione__"


def _opzioni_spesa():
    """Sottocategorie di spesa piu' la restituzione di contributo (che riduce i contributi ricevuti)."""
    return [RESTITUISCE] + opzioni_categorie(("uscite",))


def _etichetta_spesa(v):
    if v == RESTITUISCE:
        return "Restituzione di contributo (riduce i contributi ricevuti)"
    return etichetta_cat(v)


def _stato_spesa(r):
    if r["parti"]:
        return "Suddiviso in %d parti" % r["parti"]
    if r["natura"] == "entrata" and r["l2"] == servizi.RESTITUZIONE:
        return "Restituzione di contributo"
    return etichetta_cat((r["l1"], r["l2"])) if isinstance(r["l2"], str) else ""


def _spese_familiari(c):
    """Bonifici dal conto comune ai familiari: spese domiciliate sul loro conto, da categorizzare per causale."""
    righe = servizi.spese_tramite_familiari(c)
    if not righe:
        return False
    st.subheader("Spese pagate tramite un familiare")
    st.caption("Bonifici dal conto comune a un familiare il cui conto non analizzi: rimborsano spese domiciliate sul "
               "suo conto (rata dell'auto, assicurazione...) e contano come uscite. Scegli la categoria per uno o piu' "
               "bonifici, suddividi un bonifico che copre piu' spese, oppure segnalo come restituzione di contributo. "
               "Per quelli che si ripetono usa una regola sulla causale.")
    df = pd.DataFrame(righe)
    df["stato"] = [_stato_spesa(r) for r in righe]
    df["da_fare"] = df["stato"] == ""
    # proposta: stesso familiare e stesso importo di un bonifico gia' classificato
    note = {}
    for r in righe:
        if r["l2"] and not r["parti"] and r["fonte"] in ("utente", "regola utente"):
            note.setdefault((r["familiare"], r["importo_cent"]), ((r["l1"], r["l2"]), r["natura"], r["data"]))
    proposte = []
    for _, r in df.iterrows():
        nota = note.get((r["familiare"], r["importo_cent"])) if r["da_fare"] else None
        proposte.append("%s (come il %s)" % ("Restituzione di contributo" if nota[1] == "entrata" else etichetta_cat(nota[0]),
                                             nota[2])
                        if nota else "")
    df["proposta"] = proposte
    a, b, d = st.columns([1, 1, 2])
    solo = a.toggle("Solo da classificare", value=bool(df["da_fare"].any()), key="fam_solo")
    senza_prop = b.toggle("Solo senza proposta", key="fam_senza_prop")
    d.caption("%d bonifici, %d da classificare" % (len(df), int(df["da_fare"].sum())))
    f = (df[df["da_fare"]] if solo else df)
    if senza_prop:
        f = f[f["proposta"] == ""]
    f = f.reset_index(drop=True)
    if f.empty:
        st.success("Tutti i bonifici ai familiari hanno una categoria.", icon=":material/done_all:")
    giro = st.session_state.get("fam_giro", 0)
    scelti = pd.DataFrame()
    if not f.empty:
        vista = pd.DataFrame({"Data": f["data"], "Familiare": f["familiare"].fillna(""),
                              "Importo": [euro(v) for v in f["importo_cent"]], "Causale": f["causale"].fillna(""),
                              "Categoria": f["stato"], "Proposta": f["proposta"]})
        sel = st.dataframe(vista, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
                           key=chiave_tabella("fam_sel_%d_%d_%d" % (giro, int(solo), int(senza_prop)), f["mov_id"]), height=min(420, 38 + 35 * len(vista)))
        if sel and sel.selection.rows:
            scelti = f.iloc[sel.selection.rows]

    def fatto(messaggio):
        st.session_state["fam_giro"] = giro + 1
        esegui_ricalcolo(messaggio)
        st.rerun()

    if not scelti.empty:
        n = len(scelti)
        da_note = [(r["mov_id"], note[(r["familiare"], r["importo_cent"])]) for _, r in scelti.iterrows()
                   if r["proposta"] and (r["familiare"], r["importo_cent"]) in note]
        if da_note:
            if _conferma_proposte(len(da_note), n - len(da_note),
                                  len({"R" if nat == "entrata" else cat for _, (cat, nat, _) in da_note}),
                                  ("bonifico", "bonifici"), "fam_conf_prop_%d" % giro):
                with transazione(c):
                    for mid, (cat, nat, _) in da_note:
                        if nat == "entrata":
                            servizi.decidi_restituzione(c, mid)
                        else:
                            servizi.decidi_movimento(c, mid, cat)
                fatto("%d proposte confermate" % len(da_note))
        opzioni = _opzioni_spesa()
        proposte = {p for p in scelti["proposta"]}
        iniziale = None
        if len(proposte) == 1 and next(iter(proposte)):
            k = (scelti.iloc[0]["familiare"], scelti.iloc[0]["importo_cent"])
            cat_n, nat, _ = note[k]
            iniziale = RESTITUISCE if nat == "entrata" else cat_n
        with st.form("fam_cat_%d" % giro):
            st.markdown("**%d bonifici selezionati** · %s" % (n, euro(int(scelti["importo_cent"].sum()))))
            scelta = st.selectbox("Categoria", opzioni, format_func=_etichetta_spesa, placeholder="Scegli la categoria",
                                  index=opzioni.index(iniziale) if iniziale in opzioni else None,
                                  key="fam_l2_%d_%s" % (giro, abs(hash(tuple(scelti["mov_id"]))) % 10 ** 8))
            x, y = st.columns(2)
            applica = x.form_submit_button("Applica ai %d bonifici selezionati" % n if n > 1 else "Applica", type="primary")
            annulla = y.form_submit_button("Togli la categoria scelta a mano")
            if applica:
                if not scelta:
                    st.error("Scegli la categoria.")
                else:
                    with transazione(c):
                        for mid in scelti["mov_id"]:
                            if scelta == RESTITUISCE:
                                servizi.decidi_restituzione(c, mid)
                            else:
                                servizi.decidi_movimento(c, mid, scelta)
                    fatto("%d bonifici classificati" % n)
            if annulla:
                with transazione(c):
                    for mid in scelti["mov_id"]:
                        servizi.annulla_decisione(c, mid)
                fatto("Categoria tolta")
        if n == 1:
            _suddivisione(c, scelti.iloc[0], giro, fatto)
        with st.expander("Dettaglio dei bonifici selezionati", expanded=False):
            ids = list(scelti["mov_id"])
            _tabella_movimenti(c, "m.id IN (%s)" % ",".join("?" * len(ids)), ids, "fam_det_%d" % giro)

    _ricorrenze_familiari(c, df, giro, fatto)
    _regola_causale(c, giro, fatto)
    return True


def _suddivisione(c, r, giro, fatto):
    """Un bonifico che copre piu' spese: parti con importo, categoria e causale, che devono fare il totale."""
    mid, totale = r["mov_id"], int(r["importo_cent"])
    esistenti = servizi.ripartizione(c, mid)
    with st.expander("Suddividi questo bonifico in piu' spese" + (" (gia' suddiviso)" if esistenti else ""),
                     expanded=bool(esistenti)):
        st.caption("Bonifico di %s. Indica le parti: la somma deve essere esattamente il totale." % euro(-totale))
        quante = st.number_input("Numero di parti", min_value=2, max_value=8, value=max(2, len(esistenti)),
                                 key="fam_np_%s" % mid)
        opzioni = _opzioni_spesa()
        with st.form("fam_split_%s_%d" % (mid, giro)):
            parti = []
            for i in range(int(quante)):
                e = esistenti[i] if i < len(esistenti) else {}
                x, y, z = st.columns([1, 2, 2])
                importo = x.number_input("Importo € (parte %d)" % (i + 1), min_value=0.0, step=0.01, format="%.2f",
                                         value=abs(e.get("importo_cent", 0)) / 100, key="fam_imp_%s_%d" % (mid, i))
                l2 = e.get("l2")
                iniziale = RESTITUISCE if l2 == servizi.RESTITUZIONE else _come_cat((e.get("l1"), l2) if l2 else None)
                cat = y.selectbox("Categoria (parte %d)" % (i + 1), opzioni, format_func=_etichetta_spesa,
                                  index=opzioni.index(iniziale) if iniziale in opzioni else None,
                                  placeholder="Scegli la categoria", key="fam_cat_%s_%d" % (mid, i))
                causale = z.text_input("Causale (parte %d)" % (i + 1), value=e.get("causale") or "",
                                       key="fam_cau_%s_%d" % (mid, i))
                parti.append((importo, cat, causale))
            x, y = st.columns(2)
            salva = x.form_submit_button("Salva la suddivisione", type="primary")
            togli = y.form_submit_button("Elimina la suddivisione", disabled=not esistenti)
            if salva:
                segno = -1 if totale < 0 else 1
                if any(cat is None for _, cat, _ in parti):
                    st.error("Scegli la categoria di ogni parte.")
                else:
                    try:
                        servizi.salva_ripartizione(c, mid, [
                            (segno * round(imp * 100), servizi.RESTITUZIONE if cat == RESTITUISCE else cat, cau)
                            for imp, cat, cau in parti])
                        fatto("Bonifico suddiviso in %d parti" % len(parti))
                    except ValueError as err:
                        st.error("Suddivisione non salvata: %s." % err)
            if togli:
                servizi.elimina_ripartizione(c, mid)
                fatto("Suddivisione eliminata")


def _ricorrenze_familiari(c, df, giro, fatto):
    gruppi = (df[df["parti"] == 0].groupby(["familiare", "importo_cent"], dropna=False)
              .agg(n=("mov_id", "count"), da_fare=("da_fare", "sum"), primo=("data", "min"), ultimo=("data", "max"))
              .reset_index())
    gruppi = gruppi[gruppi["n"] >= 2].sort_values(["da_fare", "n"], ascending=False)
    if gruppi.empty:
        return
    with st.expander("Bonifici che si ripetono (stesso familiare, stesso importo): %d gruppi" % len(gruppi)):
        st.caption("Una categoria per tutto il gruppo. Per quelli futuri conviene una regola sulla causale, qui sotto.")
        opzioni = _opzioni_spesa()
        for k, g in enumerate(gruppi.itertuples()):
            x, y, z = st.columns([3, 3, 1])
            x.markdown("**%d × %s** a %s · dal %s al %s%s" % (
                g.n, euro(-g.importo_cent), g.familiare or "?", g.primo, g.ultimo,
                (" · %d da classificare" % g.da_fare) if g.da_fare else ""))
            scelta = y.selectbox("Categoria del gruppo", opzioni, format_func=_etichetta_spesa, index=None,
                                 placeholder="Scegli la categoria", key="fam_grp_%d_%d" % (giro, k),
                                 label_visibility="collapsed")
            if z.button("Applica", key="fam_grp_ok_%d_%d" % (giro, k), disabled=not scelta):
                ids = df[(df["familiare"] == g.familiare) & (df["importo_cent"] == g.importo_cent) & (df["parti"] == 0)]["mov_id"]
                with transazione(c):
                    for mid in ids:
                        if scelta == RESTITUISCE:
                            servizi.decidi_restituzione(c, mid)
                        else:
                            servizi.decidi_movimento(c, mid, scelta)
                fatto("%d bonifici classificati" % len(ids))


def _regola_causale(c, giro, fatto):
    with st.expander("Regola sulla causale (per i bonifici futuri)"):
        st.caption("Ogni movimento la cui descrizione contiene il testo indicato prende questa categoria, anche in futuro. "
                   "Le tue scelte sui singoli bonifici restano prioritarie.")
        x, y = st.columns([2, 3])
        testo_r = x.text_input("La causale contiene", placeholder="es. RATA AUTO", key="fam_rx_%d" % giro)
        cat = y.selectbox("Categoria", opzioni_categorie(("uscite",)), index=None,
                          format_func=_etichetta_spesa, placeholder="Scegli la categoria", key="fam_rxc_%d" % giro)
        if testo(testo_r):
            n = servizi.conta_regola(c, testo(testo_r))
            st.caption("Si applicherebbe a %d movimenti in tutto." % n)
        if st.button("Crea la regola", key="fam_rx_ok_%d" % giro, disabled=not (testo(testo_r) and cat)):
            servizi.crea_regola(c, testo(testo_r), cat, "descrizione", "contiene", priorita=20)
            fatto("Regola creata: \"%s\" → %s" % (testo(testo_r), etichetta_cat(cat)))


def _esercenti_da_classificare(c):
    caricamenti = imp.elenco_import(c)
    etichette = {r["id"]: "%s · %s · %s – %s (importato il %s)" % (r["nome_file"], r["conto"], r["data_min"], r["data_max"],
                                                                   r["importato_il"][:10]) for r in caricamenti}
    a, b, d = st.columns([2, 3, 1.3], vertical_alignment="bottom")
    cerca = a.text_input("Cerca esercente", key="rev_cerca", placeholder="Parte del nome, es. LIDO o MILANO")
    scelti_imp = b.multiselect("Caricamento", list(etichette), format_func=etichette.get, key="rev_imp",
                               placeholder="Tutti i file importati")
    senza_prop = d.toggle("Solo senza proposta", key="rev_senza_prop",
                          help="Mostra solo gli esercenti per cui l'app non ha nessun suggerimento")
    filtro, par = "", []
    if scelti_imp:
        filtro = " AND m.import_id IN (%s)" % ",".join("?" * len(scelti_imp))
        par = list(scelti_imp)
    df = pd.DataFrame([dict(r) for r in c.execute(
        "SELECT a.chiave, count(*) AS movimenti, -sum(a.importo_cent) AS totale, max(a.proposta) AS proposta, "
        "max(m.esercente) AS nome, group_concat(DISTINCT m.esercente) AS nomi, "
        "group_concat(DISTINCT co.nome) AS conti, count(DISTINCT m.import_id) AS n_file, max(ce.modello) AS modello "
        "FROM analisi a JOIN movimenti m ON m.id = a.mov_id JOIN conti co ON co.id = a.conto_id "
        "LEFT JOIN cache_esercenti ce ON ce.chiave = a.chiave "
        "WHERE a.componente = 'principale' AND a.l2 IS NULL "
        "AND a.natura IN ('uscita','rimborso') AND m.tipo IN ('pagamento_carta','addebito_sepa') AND a.chiave <> ''" + filtro +
        " GROUP BY a.chiave ORDER BY movimenti DESC, totale DESC", par)])
    if df.empty:
        st.success("Nessun esercente da classificare%s." % (" in questi caricamenti" if scelti_imp else ""),
                   icon=":material/done_all:")
        return
    tutti = len(df)
    if testo(cerca):
        # si cerca sia nella chiave (cifre -> #, punteggiatura tolta) sia nel nome come appare nei movimenti
        from ..chiavi import chiave as chiave_di
        t, tk = testo(cerca).upper(), chiave_di(testo(cerca))
        trova = [t in k.upper() or t in testo(n).upper() or (tk and tk in k) for k, n in zip(df["chiave"], df["nomi"])]
        df = df[trova].reset_index(drop=True)
    if senza_prop:
        df = df[[not testo(p) for p in df["proposta"]]].reset_index(drop=True)
    filtrati = testo(cerca) or senza_prop
    st.caption("%d esercenti senza categoria%s. Seleziona una o piu' righe: la categoria scelta vale per tutti i "
               "movimenti degli esercenti selezionati, anche futuri. Puoi anche usare la pagina "
               "**Classificazione automatica**." % (tutti, (", %d con questi filtri" % len(df)) if filtrati else ""))
    if df.empty:
        return
    # il contatore nella chiave azzera la selezione dopo ogni conferma (e quando cambia la ricerca)
    giro = st.session_state.get("rev_giro", 0)
    chiave_tab = chiave_tabella("sel_es_%d" % giro, (testo(cerca).upper(), tuple(scelti_imp), senza_prop)
                                + tuple(df["chiave"]))
    # proposta, fonte, modello e confidenza in colonne separate: si ordinano cliccando l'intestazione
    parti = [scomponi_proposta(p) for p in df["proposta"]]
    vista = pd.DataFrame({
        "Esercente": [testo(n) or k for n, k in zip(df["nome"], df["chiave"])],
        "Conto": df["conti"], "Movimenti": df["movimenti"], "Totale": df["totale"] / 100,
        "Proposta": [cfg.etichetta(x["cat"]) if x["cat"] else "" for x in parti], "Fonte": [x["fonte"] or "" for x in parti],
        "Modello": [MODELLI_CLAUDE.get(testo(m), testo(m)) if x["motore"] in ("claude", "modello_locale") else ""
                    for x, m in zip(parti, df["modello"])],
        "Confidenza": pd.to_numeric(pd.Series([x["confidenza"] for x in parti], dtype="float64"))})
    sel = st.dataframe(vista, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
                       key=chiave_tab, height=min(460, 38 + 35 * len(vista)),
                       column_config={
                           "Totale": st.column_config.NumberColumn("Totale", format="euro"),
                           "Confidenza": st.column_config.NumberColumn("Confidenza", format="%.2f",
                                                                      help="Quanto il modello, lo storico o la "
                                                                           "somiglianza sono sicuri della proposta"),
                           "Fonte": st.column_config.TextColumn("Fonte", help="Da dove viene la proposta"),
                           "Modello": st.column_config.TextColumn("Modello", help="Il modello che ha risposto")})
    righe = sel.selection.rows if sel else []
    if not righe:
        return
    scelti = df.iloc[righe]
    proposte = {_cat_da_proposta(p) for p in scelti["proposta"]}
    proposta = next(iter(proposte)) if len(proposte) == 1 else None
    con_prop = [(k, _cat_da_proposta(p)) for k, p in zip(scelti["chiave"], scelti["proposta"]) if _cat_da_proposta(p)]
    if con_prop:
        # ogni esercente prende la propria proposta: si confermano insieme voci di categorie diverse
        if _conferma_proposte(len(con_prop), len(scelti) - len(con_prop), len({x for _, x in con_prop}),
                              ("esercente", "esercenti"), "conf_prop_%d" % giro):
            with transazione(c):
                for k, cat in con_prop:
                    servizi.conferma_esercente(c, k, cat)
            st.session_state["rev_giro"] = giro + 1
            esegui_ricalcolo("%d proposte confermate" % len(con_prop))
            st.rerun()
    x, y = st.columns([2, 3], vertical_alignment="center")
    if x.button("Classifica con l'AI %d esercent%s" % (len(scelti), "e" if len(scelti) == 1 else "i"),
                key="ai_sel_%d" % giro, icon=":material/smart_toy:"):
        llm.imposta_selezione(c, list(scelti["chiave"]))
        st.session_state["ambito_auto"] = "scelti"
        st.session_state["rev_giro"] = giro + 1
        pagina = (st.session_state.get("_pagine") or {}).get("automatica")
        if pagina is not None:
            st.switch_page(pagina)
        st.rerun()
    y.caption("Li porta in Classificazione automatica: modello locale o Claude, con l'anteprima di sempre e le "
              "risposte da confermare.")
    with st.form("f_esercenti_%d" % giro):
        if len(scelti) == 1:
            st.markdown("**%s** · %d movimenti" % (testo(scelti.iloc[0]["nome"]) or scelti.iloc[0]["chiave"],
                                                   scelti.iloc[0]["movimenti"]))
        else:
            st.markdown("**%d esercenti selezionati** · %d movimenti" % (len(scelti), int(scelti["movimenti"].sum())))
        l2 = _scegli_categoria("Categoria", "cat_es_%d_%s" % (giro, abs(hash(tuple(scelti["chiave"]))) % 10 ** 8),
                               None, ("uscite",), iniziale=proposta)
        etichetta = ("Conferma per tutti i movimenti di questo esercente" if len(scelti) == 1 else
                     "Applica questa categoria a tutti i %d esercenti selezionati" % len(scelti))
        if st.form_submit_button(etichetta, type="primary"):
            if not l2:
                st.error("Scegli la categoria.")
            else:
                with transazione(c):
                    for k in scelti["chiave"]:
                        servizi.conferma_esercente(c, k, l2)
                st.session_state["rev_giro"] = giro + 1
                esegui_ricalcolo("%s: %d esercenti classificati" % (etichetta_cat(l2), len(scelti)))
                st.rerun()
    st.markdown("**Movimenti degli esercenti selezionati**%s" % (" (solo dei caricamenti scelti)" if scelti_imp else ""))
    chiavi = list(scelti["chiave"])
    _movimenti_con_eccezioni(c, "a.chiave IN (%s)%s" % (",".join("?" * len(chiavi)), filtro), chiavi + par, giro)


def _movimenti_con_eccezioni(c, condizione, parametri, giro):
    """Movimenti degli esercenti selezionati. Se ne selezioni alcuni puoi dare a loro una categoria diversa da
    quella dell'esercente (una spesa con un'altra giustificazione): la scelta sul movimento vale sempre di piu'."""
    det = pd.DataFrame([dict(r) for r in c.execute(DETTAGLIO_SQL + "WHERE " + condizione + " ORDER BY m.data DESC",
                                                   parametri)])
    if det.empty:
        st.caption("Nessun movimento.")
        return
    st.caption("Per dare a uno o piu' movimenti una categoria diversa da quella dell'esercente, selezionali qui: "
               "la scelta sul singolo movimento prevale sulla categoria dell'esercente, anche in futuro.")
    vista = pd.DataFrame({
        "Data": det["data"], "Conto": det["conto"], "Esercente": det["chiave"].fillna(""),
        "Importo": [euro(v) for v in det["importo_cent"]],
        "Categoria": [("%s (scelta sul movimento)" % l if s else l) if isinstance(l, str) else ""
                      for l, s in zip([etichetta_cat((a, b)) if isinstance(b, str) else None
                                       for a, b in zip(det["l1"], det["l2"])], det["scelta_sul_movimento"])],
        "Descrizione": det["descrizione"].fillna(""),
        "Caricamento": [("%s (%s)" % (f, d[:10])) if isinstance(f, str) else "" for f, d in
                        zip(det["nome_file"], det["importato_il"].fillna(""))]})
    chiave = "det_es_%d_%s" % (giro, abs(hash(tuple(det["mov_id"]))) % 10 ** 8)
    sel = st.dataframe(vista, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
                       key=chiave, height=min(400, 38 + 35 * len(vista)))
    righe = sel.selection.rows if sel else []
    if not righe:
        return
    scelti = det.iloc[righe]
    with st.form("f_eccezione_%s" % chiave):
        st.markdown("**%d movimenti selezionati** · %s" % (len(scelti), euro(int(scelti["importo_cent"].sum()))))
        l2 = _scegli_categoria("Categoria solo per questi movimenti", "cat_ecc_%s_%s" % (chiave, len(scelti)), None,
                               ("uscite",))
        x, y = st.columns(2)
        applica = x.form_submit_button("Applica solo ai movimenti selezionati", type="primary")
        togli = y.form_submit_button("Togli la scelta sul movimento", disabled=not scelti["scelta_sul_movimento"].any())
        if applica:
            if not l2:
                st.error("Scegli la categoria.")
            else:
                with transazione(c):
                    for mid in scelti["mov_id"]:
                        servizi.decidi_movimento(c, mid, l2)
                st.session_state["rev_giro"] = giro + 1
                esegui_ricalcolo("%s per %d movimenti" % (etichetta_cat(l2), len(scelti)))
                st.rerun()
        if togli:
            with transazione(c):
                for mid in scelti["mov_id"]:
                    servizi.annulla_decisione(c, mid)
            st.session_state["rev_giro"] = giro + 1
            esegui_ricalcolo("Tornano alla categoria dell'esercente")
            st.rerun()


def _modifica_movimento(r, prefisso):
    c = conn()
    with st.form("f_mov_" + prefisso):
        st.markdown("**%s** · %s · %s" % (r["data"], euro(r["importo_cent"]), testo(r.get("descrizione"))))
        # come conta: secondo la categoria (la sua sezione dice se e' spesa, entrata o risparmio; una spesa con
        # importo positivo e' un rimborso), oppure non conta. Il contenitore si offre solo se lo e' gia'.
        modi = {"categoria": "Secondo la categoria (spesa, entrata o risparmio)",
                "trasferimento_interno": "Trasferimento tra miei conti (non conta)",
                "escluso": "Escluso dalle analisi (non conta)"}
        if r["natura"] == "contenitore":
            modi["contenitore"] = "Contenitore: il dettaglio e' altrove (non conta)"
        attuale = r["natura"] if r["natura"] in modi else "categoria"
        modo = st.radio("Come conta", list(modi), index=list(modi).index(attuale), format_func=modi.get,
                        key="modo_%s_%s" % (prefisso, r["mov_id"]))
        l2 = _scegli_categoria("Categoria (se conta secondo la categoria)", "cat_%s_%s" % (prefisso, r["mov_id"]),
                               None, iniziale=(r["l1"], r["l2"]) if testo(r.get("l2")) else None)
        natura = cfg.natura_da_categoria(l2, "uscita") if modo == "categoria" else modo
        esercente = testo(r.get("chiave")) if r.get("tipo") in ("pagamento_carta", "addebito_sepa") else ""
        tutti = False
        if esercente:
            tutti = st.checkbox("Vale per tutti i movimenti di questo esercente, anche futuri (%s)" % esercente,
                                value=not testo(r.get("l2")), key="tutti_%s_%s" % (prefisso, r["mov_id"]),
                                help="Come confermare l'esercente in Revisione. Senza spunta la categoria vale solo "
                                     "per questo movimento.")
        a, b = st.columns(2)
        if a.form_submit_button("Salva", type="primary"):
            if modo == "categoria" and not l2:
                st.error("Scegli la categoria, oppure indica che il movimento non conta.")
                st.stop()
            if tutti and natura == "uscita" and l2:
                servizi.conferma_esercente(c, esercente, l2)
                # una scelta precedente sul solo movimento prevarrebbe sulla categoria dell'esercente
                servizi.annulla_decisione(c, r["mov_id"])
                esegui_ricalcolo("Esercente classificato: %s" % etichetta_cat(l2))
            else:
                servizi.decidi_movimento(c, r["mov_id"], l2 if modo == "categoria" else None, natura)
                esegui_ricalcolo("Movimento aggiornato")
            st.rerun()
        if b.form_submit_button("Annulla una decisione precedente"):
            servizi.annulla_decisione(c, r["mov_id"])
            esegui_ricalcolo("Decisione annullata")
            st.rerun()


# ---------------------------------------------------------------------------
# Movimenti
# ---------------------------------------------------------------------------
def movimenti():
    f = filtri_sidebar()
    st.title("Movimenti")
    a, b = st.columns([3, 1])
    cerca = a.text_input("Cerca nella descrizione, esercente o controparte")
    solo = b.toggle("Solo da rivedere")
    df = an.movimenti(conn(), f, testo=cerca or None, solo_rivedere=solo)
    if df.empty:
        st.info("Nessun movimento con questi filtri.")
        return
    eti_on, nat_on = attr.etichette_attive(conn()), attr.natura_attiva(conn())
    prova = eti_on or nat_on
    vista = df.assign(Importo=[euro(v) for v in df["importo_cent"]],
                      **{"Conta come": [NATURE.get(n, n) for n in df["natura"]]},
                      Rivedere=["sì" if x else "" for x in df["da_rivedere"]],
                      Motivo=[testo(mo) if x else "" for x, mo in zip(df["da_rivedere"], df["motivo"])])
    colonne = ["data", "conto", "esercente", "controparte_nome", "Importo", "Conta come", "l1", "l2", "fonte", "Rivedere",
               "Motivo", "descrizione"]
    if nat_on:
        valori = attr.valori()
        vista["Natura della spesa"] = [valori.get(testo(v), "") for v in df["attributo"]]
        colonne.append("Natura della spesa")
    if eti_on:
        eti = attr.etichette_di(conn(), list(dict.fromkeys(df["mov_id"])))
        vista["Etichette"] = [", ".join(eti.get(m, [])) for m in df["mov_id"]]
        colonne.append("Etichette")
    cosa = " e ".join(x for x, on in (("etichette", eti_on), ("natura della spesa", nat_on)) if on)
    st.caption(("Le %d righe piu' recenti: restringi periodo o ricerca per vedere le altre" % len(df)
                if len(df) >= an.LIMITE_MOVIMENTI else "%d righe" % len(df))
               + " (le commissioni e le imposte di un movimento compaiono come righe separate)."
               + (" Seleziona piu' righe per assegnare %s a tutte insieme." % cosa if prova else ""))
    giro = st.session_state.get("mov_giro", 0)
    vista[colonne] = vista[colonne].astype(object).where(vista[colonne].notna(), "")     # niente "None" nelle celle vuote
    sel = st.dataframe(vista[colonne].rename(columns={"data": "Data", "conto": "Conto", "esercente": "Esercente",
                                                      "controparte_nome": "Controparte", "l1": "Categoria",
                                                      "l2": "Sottocategoria", "fonte": "Fonte",
                                                      "descrizione": "Descrizione"}),
                       hide_index=True, width="stretch", on_select="rerun",
                       selection_mode="multi-row" if prova else "single-row",
                       key=chiave_tabella("sel_mov_%d" % giro, df["mov_id"]), height=520)
    righe = sel.selection.rows if sel else []
    if len(righe) == 1:
        _modifica_movimento(df.iloc[righe[0]], "mov")
    if prova and righe:
        _etichette_e_natura(df.iloc[righe], giro, eti_on, nat_on)


def _etichette_e_natura(scelti, giro, eti_on=True, nat_on=True):
    """Etichette e/o natura della spesa sui movimenti selezionati (secondo le sezioni accese)."""
    c = conn()
    ids = list(dict.fromkeys(scelti["mov_id"]))
    titolo = " e ".join(x for x, on in (("Etichette", eti_on), ("natura della spesa", nat_on)) if on)
    st.markdown("**%s** · %d moviment%s selezionat%s" %
                (titolo, len(ids), "o" if len(ids) == 1 else "i", "o" if len(ids) == 1 else "i"))
    eti = attr.etichette(c)
    nomi = {e["id"]: e["nome"] for e in eti}
    presenti = attr.etichette_di(c, ids)
    sui_scelti = sorted({n for v in presenti.values() for n in v})
    a, b = st.columns(2)
    aggiungi, nuova, togli, natura = [], "", [], ""
    if eti_on:
        with a:
            aggiungi = st.multiselect("Aggiungi etichette", list(nomi), format_func=nomi.get, key="mov_et_add_%d" % giro,
                                      placeholder="Scegli tra quelle esistenti" if nomi else "Nessuna etichetta ancora")
            nuova = st.text_input("…o crea una nuova etichetta", key="mov_et_new_%d" % giro,
                                  placeholder="es. Vacanza Puglia 2025")
            togli = st.multiselect("Togli etichette", sui_scelti, key="mov_et_del_%d" % giro,
                                   placeholder="Scegli" if sui_scelti else "Nessuna sui selezionati")
    if nat_on:
        with (b if eti_on else a):
            valori = attr.valori()
            opz = ["", "sottocategoria"] + list(valori)
            etich = {"": "Non cambiare", "sottocategoria": "Come la sottocategoria (togli la scelta)", **valori}
            natura = st.radio("Natura della spesa", opz, format_func=etich.get, key="mov_nat_%d" % giro)
    if st.button("Applica ai selezionati", type="primary", key="mov_et_ok_%d" % giro,
                 disabled=not (aggiungi or nuova.strip() or togli or natura)):
        try:
            if nuova.strip():
                aggiungi = list(aggiungi) + [attr.crea_etichetta(c, nuova)]
        except ValueError as e:
            st.error(str(e).capitalize() + ".")
            return
        for eid in aggiungi:
            attr.assegna(c, ids, eid)
        per_nome = {v: k for k, v in nomi.items()}
        for n in togli:
            attr.togli(c, ids, per_nome[n])
        st.session_state["mov_giro"] = giro + 1
        if natura:
            attr.imposta_movimento(c, ids, None if natura == "sottocategoria" else natura)
            esegui_ricalcolo("Aggiornati %d movimenti" % len(ids))
        else:
            st.toast("Aggiornati %d movimenti" % len(ids))
        st.rerun()
