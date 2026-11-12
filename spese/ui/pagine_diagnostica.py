"""Diagnostica (modalita' di debug): controlli di coerenza sui dati veri, report anonimizzato e guida."""
import pandas as pd
import streamlit as st

from .. import diagnostica as dg
from ..numeri import euro
from ..percorsi import RADICE
from .comune import chiave_tabella, conn

GUIDA = RADICE / "DIAGNOSTICA.md"
IN_BREVE = ("La diagnostica controlla sui tuoi dati le regole che devono valere sempre (un movimento conta una "
            "volta sola, i trasferimenti si annullano, i totali delle pagine coincidono...) e prepara un report di "
            "testo da leggere e, se vuoi, inviare a chi sviluppa l'app. Non usa la rete. Il riepilogo contiene solo "
            "conteggi; i casi che scegli tu contengono gli importi veri, con nomi, conti e IBAN sostituiti da "
            "etichette e le date spostate di alcuni anni.")


def collegamento_guida(etichetta="Come funziona la diagnostica: guida completa"):
    pagina = (st.session_state.get("_pagine") or {}).get("guida-diagnostica")
    if pagina is not None:
        st.page_link(pagina, label=etichetta, icon=":material/menu_book:")


def guida():
    """Pagina di manuale della diagnostica: il file DIAGNOSTICA.md del programma, leggibile senza rete."""
    try:
        st.markdown(GUIDA.read_text(encoding="utf-8"))
    except OSError:
        st.title("Guida alla diagnostica")
        st.warning("Il file DIAGNOSTICA.md non e' nella cartella del programma.")
    if not dg.attiva(conn()):
        st.info("La modalita' di debug e' spenta: si accende in Impostazioni > Modalita' di debug.")


def _valore(tipo, valore, conti):
    if tipo == "euro":
        return euro(valore)
    if tipo == "conto":
        return conti.get(valore, "?")
    return str(valore)


def _elenco(c, anomalie):
    """Una riga per caso, con i dati veri del movimento principale: si vede solo qui."""
    conti = {r["id"]: r["nome"] for r in c.execute("SELECT id, nome FROM conti")}
    ids = sorted({a.movimenti[0] for a in anomalie if a.movimenti})
    movs = {}
    for i in range(0, len(ids), 500):
        blocco = ids[i:i + 500]
        for r in c.execute("SELECT id, data, conto_id, importo_cent, esercente, controparte_nome, descrizione FROM movimenti "
                           "WHERE id IN (%s)" % ",".join("?" * len(blocco)), blocco):
            movs[r["id"]] = r
    righe = []
    for a in anomalie:
        m = movs.get(a.movimenti[0]) if a.movimenti else None
        righe.append({"Gravita'": a.gravita, "Controllo": a.codice, "Data": m["data"] if m else "",
                      "Conto": conti.get(m["conto_id"], "") if m else "",
                      "Importo": euro(m["importo_cent"]) if m and m["importo_cent"] is not None else "",
                      "Esercente o controparte": (m["esercente"] or m["controparte_nome"] or "") if m else "",
                      "Dettaglio": "; ".join("%s: %s" % (n, _valore(t, v, conti)) for n, t, v in a.dati),
                      "Movimenti": len(a.movimenti), "Descrizione": (m["descrizione"] or "") if m else ""})
    return pd.DataFrame(righe)


def diagnostica():
    st.title("Diagnostica")
    c = conn()
    st.caption(IN_BREVE)
    collegamento_guida()
    if st.button("Esegui i controlli", type="primary", icon=":material/troubleshoot:", key="diag_esegui"):
        with st.spinner("Ricalcolo due volte e controllo i dati…"):
            st.session_state["diag_anomalie"] = dg.esegui(c)
        for k in ("diag_report", "diag_transcodifica"):
            st.session_state.pop(k, None)
    anomalie = st.session_state.get("diag_anomalie")
    if anomalie is None:
        st.info("Premi **Esegui i controlli**. I controlli leggono i dati e rifanno il ricalcolo: non cambiano "
                "movimenti, categorie o decisioni.")
        return

    per = {g: sum(1 for a in anomalie if a.gravita == g) for g in ("errore", "avviso", "info")}
    k = st.columns(3)
    k[0].metric("Errori", per["errore"], help="Una regola del programma non e' rispettata: e' un difetto da segnalare.")
    k[1].metric("Avvisi", per["avviso"], help="Probabili errori nei conti: da guardare uno per uno.")
    k[2].metric("Informazioni", per["info"], help="Situazioni note, utili per capire i numeri.")
    with st.container(key="scheda_diag_controlli"):
        st.subheader("Controlli")
        conta = {}
        for a in anomalie:
            conta[a.codice] = conta.get(a.codice, 0) + 1
        st.dataframe(pd.DataFrame([{"Controllo": cod, "Gravita'": v[0], "Casi": conta.get(cod, 0), "Cosa controlla": v[1],
                                    "In dettaglio": v[2]} for cod, v in dg.CONTROLLI.items()]),
                     hide_index=True, width="stretch")

    with st.container(key="scheda_diag_casi"):
        st.subheader("Casi trovati")
        if not anomalie:
            st.success("Nessun caso: tutti i controlli sono passati.")
            scelte = []
        else:
            codici = [cod for cod in dg.CONTROLLI if cod in conta]
            filtro = st.multiselect("Controlli da mostrare", codici, default=[], placeholder="Tutti", key="diag_filtro")
            mostrate = [a for a in anomalie if not filtro or a.codice in filtro]
            st.caption("Qui vedi i dati veri: restano sul tuo PC. Seleziona i casi da mettere nel report (al massimo %d)."
                       % dg.MASSIMO_CASI)
            sel = st.dataframe(_elenco(c, mostrate), hide_index=True, width="stretch", on_select="rerun",
                               selection_mode="multi-row", height=min(460, 38 + 35 * len(mostrate)),
                               key=chiave_tabella("diag_sel", [a.id for a in mostrate]))
            scelte = [mostrate[i] for i in (sel.selection.rows if sel else []) if i < len(mostrate)]
            if len(scelte) > dg.MASSIMO_CASI:
                st.warning("Hai selezionato %d casi: nel report entrano i primi %d." % (len(scelte), dg.MASSIMO_CASI))
                scelte = scelte[:dg.MASSIMO_CASI]

    with st.container(key="scheda_diag_report"):
        st.subheader("Report da inviare")
        quanti = {0: "nessun caso (non ne hai selezionati)", 1: "il caso selezionato, con gli importi veri"}.get(
            len(scelte), "i %d casi selezionati, con gli importi veri" % len(scelte))
        st.caption("Il report contiene sempre il riepilogo (solo conteggi) e, in piu', %s. Leggilo prima di inviarlo: "
                   "e' esattamente il testo dell'anteprima." % quanti)
        if st.button("Prepara il report", key="diag_prepara", icon=":material/description:"):
            st.session_state["diag_report"], st.session_state["diag_transcodifica"] = dg.report(c, anomalie, scelte)
        testo = st.session_state.get("diag_report")
        if testo:
            a, b = st.columns(2)
            a.download_button("Scarica il report (da inviare)", testo.encode("utf-8"), "diagnostica-report.md",
                              "text/markdown", type="primary", icon=":material/download:", key="diag_scarica")
            b.download_button("Scarica la transcodifica (resta sul PC)", st.session_state["diag_transcodifica"].encode("utf-8"),
                              "diagnostica-transcodifica-NON-INVIARE.csv", "text/csv", icon=":material/key:",
                              key="diag_scarica_tc",
                              help="Corrispondenza tra le etichette del report (ESERCENTE 12, M 3...) e i valori veri. "
                                   "Serve a te per leggere il report e le risposte: non inviarla.")
            with st.expander("Anteprima del report", expanded=True):
                st.code(testo, language="markdown", wrap_lines=False)
