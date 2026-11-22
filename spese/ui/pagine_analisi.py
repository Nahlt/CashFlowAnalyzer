"""Pagine di analisi: andamento mensile, dove vanno i soldi (con il dettaglio per categoria), etichette."""
import pandas as pd
import streamlit as st

from .. import analisi as an
from .. import attributi as attr
from ..db import leggi_impostazione
from ..numeri import euro
from . import grafici
from .comune import GRIGIO, chiave_tabella, colore_segno, conn, etichetta_cat, filtri_sidebar, intestazione, testo


def _nessun_dato():
    st.info("Nessun dato per questi conti e questo periodo. Importa un estratto conto dalla pagina **Importa**.",
            icon=":material/upload_file:")


def _vai_al_mese(mese):
    """Dal grafico dell'andamento: apre "Dove vanno i soldi" filtrato su quel mese (il periodo resta quello scelto)."""
    st.session_state["rip_mese"] = mese
    pagina = (st.session_state.get("_pagine") or {}).get("ripartizione")
    if pagina is not None:
        st.switch_page(pagina)


def andamento():
    f = filtri_sidebar()
    intestazione("Andamento mensile", f, "Entrate e uscite al netto dei rimborsi, mese per mese")
    c = conn()
    df = an.flussi_mensili(c, f)
    if df.empty:
        return _nessun_dato()
    ent, usc = int(df["entrate"].sum()), int(df["uscite"].sum())
    netto = ent - usc
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entrate", euro(ent))
    c2.metric("Uscite (al netto dei rimborsi)", euro(usc))
    c3.metric("Differenza entrate uscite", euro(netto),
              help="Entrate meno uscite del periodo. Non e' il saldo dei conti, che trovi nella pagina Saldo dei conti.")
    c4.metric("Tasso di risparmio", ("%d%%" % round(100 * netto / ent)) if ent else "—")
    mesi = list(df["mese"])
    with st.container(key="scheda_and_linee"):
        st.subheader("Entrate e uscite per mese")
        st.caption("Passa il mouse su un mese per vedere dove sono andati i soldi; clicca per aprire il dettaglio.")
        i = grafici.mostra(grafici.linee_entrate_uscite(df, an.anteprima_mensile(c, f)), "and_linee", 360, clic="x")
    if i is not None and 0 <= i < len(mesi):
        _vai_al_mese(mesi[i])
    with st.container(key="scheda_and_saldo"):
        st.subheader("Differenza entrate uscite per mese")
        i = grafici.mostra(grafici.barre_saldo(df), "and_saldo", 260, clic="x")
    if i is not None and 0 <= i < len(mesi):
        _vai_al_mese(mesi[i])
    risp = int(df["risparmio"].sum())
    if risp:
        st.caption("Accantonato nel periodo (acquisti di titoli e trasferimenti verso conti di risparmio, al netto dei "
                   "rientri): **%s**. Non rientra nelle uscite." % euro(risp))
    with st.expander("Tabella dei valori"):
        t = pd.DataFrame({"Mese": [grafici.etichetta_mese(m) for m in df["mese"]],
                          "Entrate": [euro(v) for v in df["entrate"]], "Uscite": [euro(v) for v in df["uscite"]],
                          "Differenza entrate uscite": [euro(v) for v in df["netto"]],
                          "Tasso di risparmio": ["—" if pd.isna(t) else "%d%%" % round(100 * t) for t in df["tasso"]],
                          "Risparmio": [euro(v) for v in df["risparmio"]]})
        st.dataframe(t, hide_index=True, width="stretch")


def _mesi_del_periodo(c, f):
    """Tutti i mesi del periodo scelto (anche quelli senza spese nella categoria), per l'asse del grafico."""
    df = an.flussi_mensili(c, f)
    return list(df["mese"]) if not df.empty else []


def _mese_scelto(mese):
    """Clic sul grafico della spesa per mese: filtra la pagina su quel mese; sullo stesso mese toglie il filtro."""
    if st.session_state.get("rip_mese") == mese:
        st.session_state.pop("rip_mese", None)
    else:
        st.session_state["rip_mese"] = mese


def ripartizione():
    """Dove vanno i soldi: categorie in alto (cliccabili, "Da classificare" in rosso) con la loro tabella; sotto,
    sottocategorie della categoria scelta (30%) e spesa per mese con media mobile (70%), poi esercenti principali
    e movimenti. Il mese scelto (clic sulla spesa per mese o dall'andamento) vale per tutta la pagina; il grafico
    per mese resta sull'intero periodo, con il mese in evidenza, per poterne scegliere un altro."""
    f = filtri_sidebar()
    intestazione("Dove vanno i soldi", f, "Uscite al netto dei rimborsi · clicca una categoria per il dettaglio, "
                                          "un mese per vedere solo quello")
    c = conn()
    mesi_periodo = _mesi_del_periodo(c, f)
    mese = st.session_state.get("rip_mese")
    if mese and mese not in mesi_periodo:
        mese = None
        st.session_state.pop("rip_mese", None)
    fm = an.con_mese(f, mese) if mese else f
    if mese:
        a, b = st.columns([1, 4], vertical_alignment="center")
        if a.button("%s  ✕" % grafici.mese_lungo(mese).capitalize(), icon=":material/filter_alt:", key="rip_mese_x",
                    help="Togli il filtro sul mese"):
            st.session_state.pop("rip_mese", None)
            st.rerun()
        b.caption("Tutta la pagina mostra il solo mese scelto. Il grafico della spesa per mese resta sull'intero "
                  "periodo: clicca un altro mese per cambiarlo, lo stesso per togliere il filtro.")
    df = an.ripartizione(c, fm, "l1")
    if df.empty:
        return _nessun_dato()
    tot = int(df["importo"].sum())
    voci = list(df["voce"])
    l1 = st.session_state.get("rip_l1")
    if l1 not in voci:
        l1 = next((v for v in voci if v != "Da classificare"), voci[0])
        st.session_state["rip_l1"], st.session_state["rip_l2"] = l1, None
    l2 = st.session_state.get("rip_l2")
    nome_l1 = l1.capitalize() if l1.isupper() else l1
    serie = an.serie_categoria(c, f, l1, l2)
    valori = dict(zip(serie["mese"], serie["importo"])) if not serie.empty else {}
    totale = int(sum(valori.values()))
    n_mesi = len(mesi_periodo) or 1
    media = round(totale / n_mesi)
    voce = l2 or nome_l1
    k0, k1, k2 = st.columns(3)
    if mese:
        nel_mese = int(valori.get(mese, 0))
        k0.metric("Uscite di %s" % grafici.mese_lungo(mese), euro(tot))
        k1.metric("%s · %s" % (voce, grafici.mese_lungo(mese)), euro(nel_mese))
        scarto = nel_mese - media
        k2.metric("%s · media mensile del periodo" % voce, euro(media),
                  delta="%s%s nel mese" % ("+" if scarto > 0 else "", euro(scarto)),
                  delta_color="inverse" if scarto else "off")
    else:
        k0.metric("Uscite del periodo", euro(tot))
        k1.metric("%s · totale nel periodo" % voce, euro(totale))
        k2.metric("%s · media mensile" % voce, euro(media))

    with st.container(key="scheda_categorie"):
        st.subheader("Categorie" + (" · %s" % grafici.mese_lungo(mese) if mese else ""))
        st.caption("Trasferimenti tra conti, risparmio e addebiti riepilogativi della carta sono esclusi. In rosso le "
                   "uscite ancora senza categoria: si sistemano in **Revisione** o qui sotto, in Movimenti.")
        opz, ordine = grafici.barre_orizzontali(voci, list(df["importo"]), l1, rosse=("Da classificare",))
        i = grafici.mostra(opz, "rip_l1_graf", max(220, 30 * len(voci) + 30), clic="y")
    if i is not None and 0 <= i < len(ordine) and ordine[i] != l1:
        st.session_state["rip_l1"], st.session_state["rip_l2"] = ordine[i], None
        st.rerun()
    with st.expander("Tabella delle categorie"):
        t = df.copy()
        t["Quota"] = ["%d%%" % round(100 * v / tot) for v in t["importo"]] if tot else ""
        t["Importo"] = [euro(v) for v in t["importo"]]
        st.dataframe(t[["voce", "Importo", "Quota", "movimenti"]].rename(columns={"voce": "Categoria", "movimenti": "Movimenti"}),
                     hide_index=True, width="stretch")

    sx, dx = st.columns([3, 7])
    with sx, st.container(key="scheda_sottocategorie"):
        st.subheader("%s · sottocategorie" % nome_l1)
        i = None
        if l1 == "Da classificare":
            sotto = pd.DataFrame()
            st.caption("Voci ancora senza categoria: si sistemano in **Revisione** o in Movimenti, qui sotto.")
        else:
            sotto = an.ripartizione(c, fm, "l2", l1)
        if not sotto.empty:
            if l2 not in list(sotto["voce"]):
                l2 = st.session_state["rip_l2"] = None
            st.caption("Clic per filtrare, di nuovo per togliere")
            opz2, ordine2 = grafici.barre_orizzontali(list(sotto["voce"]), list(sotto["importo"]), l2, etichette_sopra=True)
            i = grafici.mostra(opz2, "rip_l2_graf_%s" % l1, max(180, 46 * len(sotto) + 20), clic="y")
    if i is not None and 0 <= i < len(ordine2):
        st.session_state["rip_l2"] = None if ordine2[i] == l2 else ordine2[i]
        st.rerun()
    with dx, st.container(key="scheda_serie"):
        st.subheader("Spesa per mese · %s" % voce)
        st.caption("Clic su un mese per vedere solo quello, di nuovo per togliere il filtro")
        i = grafici.mostra(grafici.serie_mensile(mesi_periodo, [valori.get(m, 0) for m in mesi_periodo], mese),
                           "rip_serie", 300, clic="x")
    if i is not None and 0 <= i < len(mesi_periodo):
        _mese_scelto(mesi_periodo[i])
        st.rerun()

    dove = (" di %s" % grafici.mese_lungo(mese) if mese else "") + " · %s" % voce
    eser = _esercenti_principali(c, fm, l1, l2, mese, dove)
    _movimenti_della_voce(c, fm, l1, l2, eser, dove)
    if attr.natura_attiva(c):
        _natura_spesa(f, fm, mese)


def _esercenti_principali(c, fm, l1, l2, mese, dove):
    """Grafico dei primi esercenti della voce scelta. Clic su una barra: filtra i movimenti su quell'esercente
    (di nuovo per togliere). Il filtro vale per la voce e il mese in cui e' stato scelto. Restituisce
    l'esercente scelto o None."""
    contesto = (l1, l2, mese, tuple(fm.conti), fm.perimetro, fm.da, fm.a)
    if st.session_state.get("rip_eser_ctx") != contesto:
        st.session_state.pop("rip_eser", None)
        st.session_state["rip_eser_ctx"] = contesto
    es = an.esercenti_categoria(c, fm, l1, l2)
    eser = st.session_state.get("rip_eser")
    if eser is not None and eser not in list(es["esercente"]):
        eser = None
        st.session_state.pop("rip_eser", None)
    with st.container(key="scheda_esercenti"):
        st.subheader("Esercenti principali" + dove)
        if es.empty:
            st.caption("Nessun esercente.")
            return None
        st.caption("I primi %d per importo. Clic su un esercente per filtrare i movimenti qui sotto, di nuovo per "
                   "togliere il filtro." % len(es))
        nomi = [testo(e) or "Senza nome" for e in es["esercente"]]
        opz, ordine = grafici.barre_orizzontali(nomi, list(es["importo"]),
                                                None if eser is None else (testo(eser) or "Senza nome"))
        i = grafici.mostra(opz, "rip_eser_graf", max(200, 30 * len(es) + 30), clic="y")
    if i is not None and 0 <= i < len(ordine):
        scelto = list(es["esercente"])[nomi.index(ordine[i])]
        if scelto == eser:
            st.session_state.pop("rip_eser", None)
        else:
            st.session_state["rip_eser"] = scelto
        st.rerun()
    return eser


def _movimenti_della_voce(c, fm, l1, l2, eser, dove):
    """Movimenti della voce scelta (solo uscite e rimborsi, come nei grafici), con la stessa tabella e la stessa
    classificazione della pagina Movimenti. Riquadro chiuso all'inizio; resta aperto se lo apri."""
    filtro = "" if eser is None else " · %s" % (testo(eser) or "Senza nome")
    with st.expander("Movimenti" + dove + filtro, key="rip_mov_aperto"):
        if eser is not None:
            if st.button("Esercente: %s  ✕" % (testo(eser) or "Senza nome"), icon=":material/filter_alt:",
                         key="rip_eser_x", help="Togli il filtro sull'esercente"):
                st.session_state.pop("rip_eser", None)
                st.rerun()
        mv = an.movimenti(c, fm, l1, l2, solo_uscite=True, esercente=eser)
        if mv.empty:
            st.caption("Nessun movimento.")
        else:
            from .pagine_dati import tabella_movimenti
            tabella_movimenti(mv, "rip", altezza=min(520, 38 + 35 * len(mv)), restringi="scegli un mese o un esercente")


def _natura_spesa(f, fm=None, mese=None):
    """Quote per natura della spesa (del mese scelto, se c'e') e andamento sull'intero periodo."""
    with st.container(key="scheda_natura"):
        _natura_spesa_dentro(f, fm or f, mese)


def _natura_spesa_dentro(f, fm, mese=None):
    st.subheader("Natura della spesa" + (" · %s" % grafici.mese_lungo(mese) if mese else ""))
    df = an.per_attributo(conn(), f)
    if df.empty:
        return
    valori = attr.valori()
    serie = [(k, v) for k, v in valori.items()] + [("", "Non definita")]
    quote = an.per_attributo(conn(), fm) if mese else df
    tot_per = quote.groupby("attributo")["importo"].sum().to_dict() if not quote.empty else {}
    tot = sum(tot_per.values())
    cols = st.columns(len(serie))
    for col, (k, nome) in zip(cols, serie):
        v = int(tot_per.get(k, 0))
        col.metric(nome, euro(v))
        if tot:
            col.caption("%d%% delle uscite" % round(100 * v / tot))
    st.caption(("Quote del mese scelto; il grafico resta sull'intero periodo. " if mese else "")
               + "Viene dalla sottocategoria (si cambia in Regole e tassonomia, scheda Natura della spesa) e si puo' "
               "scegliere sul singolo movimento nella pagina Movimenti. Sezione facoltativa: si spegne in Impostazioni.")
    larga = df.pivot_table(index="mese", columns="attributo", values="importo", aggfunc="sum", fill_value=0).reset_index()
    presenti = [(k, n) for k, n in serie if k in larga.columns]
    colori = [grafici.COLORI_NATURA[list(valori).index(k) % len(grafici.COLORI_NATURA)] if k in valori else GRIGIO
              for k, _ in presenti]
    grafici.mostra(grafici.barre_impilate(list(larga["mese"]), [(n, list(larga[k]), col) for (k, n), col in zip(presenti, colori)]),
                   "rip_natura", 320)
    with st.expander("Tabella per mese"):
        t = pd.DataFrame({"Mese": [grafici.etichetta_mese(m) for m in larga["mese"]]})
        for k, n in presenti:
            t[n] = [euro(v) for v in larga[k]]
        st.dataframe(t, hide_index=True, width="stretch")


def etichette():
    """Totali per etichetta (es. una vacanza, un progetto), gestione e assegnazione per periodo."""
    f = filtri_sidebar()
    intestazione("Etichette", f, "Spese legate alla stessa cosa (una vacanza, un progetto), anche di categorie diverse")
    c = conn()
    t1, t2, t3 = st.tabs(["Riepilogo", "Assegna per periodo", "Gestisci"])
    with t1:
        df = an.per_etichetta(c, f)
        if df.empty:
            st.info("Nessun movimento etichettato in questo perimetro e periodo. Le etichette si assegnano nella "
                    "pagina Movimenti (selezionando una o piu' righe) o qui, nella scheda Assegna per periodo. "
                    "Per una vacanza pagata da piu' conti scegli tutti quei conti nei filtri.")
        else:
            vista = pd.DataFrame({"Etichetta": df["nome"], "Uscite": [euro(v) for v in df["uscite"]],
                                  "Entrate": [euro(v) if v else "" for v in df["entrate"]], "Movimenti": df["movimenti"],
                                  "Dal": df["dal"], "Al": df["al"]})
            st.dataframe(vista, hide_index=True, width="stretch")
            nomi = dict(zip(df["id"], df["nome"]))
            scelta = st.selectbox("Dettaglio di un'etichetta", list(nomi), format_func=nomi.get, key="et_dett")
            cat = an.etichetta_categorie(c, f, int(scelta))
            if len(cat) > 1:
                opz, _ = grafici.barre_orizzontali(list(cat["voce"]), list(cat["importo"]))
                grafici.mostra(opz, "et_cat_%s" % scelta, max(200, 30 * len(cat) + 30))
            mv = an.etichetta_movimenti(c, f, int(scelta))
            if not mv.empty:
                valori = attr.valori()
                st.dataframe(pd.DataFrame({"Data": mv["data"], "Ora": mv["ora"].fillna(""), "Conto": mv["conto"],
                                           "Voce": mv["voce"],
                                           "Importo": [euro(v) for v in mv["importo_cent"]],
                                           "Categoria": [etichetta_cat((a, b)) if isinstance(b, str) else ""
                                                         for a, b in zip(mv["l1"], mv["l2"])],
                                           "Natura della spesa": [valori.get(testo(v), "") for v in mv["attributo"]]}),
                             hide_index=True, width="stretch")
    with t2:
        _assegna_periodo(c)
    with t3:
        _gestisci_etichette(c)


def _scegli_o_crea(c, chiave):
    el = attr.etichette(c)
    nomi = {e["id"]: e["nome"] for e in el}
    opzioni = list(nomi) + ["nuova"]
    s = st.selectbox("Etichetta", opzioni, format_func=lambda i: "Nuova etichetta…" if i == "nuova" else nomi[i],
                     key=chiave + "_sel")
    nuovo = st.text_input("Nome della nuova etichetta", key=chiave + "_nome", placeholder="es. Vacanza Puglia 2025") \
        if s == "nuova" else None
    return s, nuovo


def _assegna_periodo(c):
    st.caption("Utile per una vacanza o un trasloco: prende le uscite tra due date, ne mostri l'elenco, togli quelle "
               "che non c'entrano e assegni l'etichetta alle altre.")
    giro = st.session_state.get("et_giro", 0)
    a, b = st.columns(2)
    da = a.date_input("Dal", value=None, format="DD/MM/YYYY", key="et_da_%d" % giro)
    al = b.date_input("Al", value=None, format="DD/MM/YYYY", key="et_al_%d" % giro)
    conti = c.execute("SELECT id, nome FROM conti WHERE importato = 1 ORDER BY nome").fetchall()
    nomi_conti = {r["id"]: r["nome"] for r in conti}
    scelti_conti = st.multiselect("Conti", list(nomi_conti), format_func=nomi_conti.get, placeholder="Tutti i conti",
                                  key="et_conti_%d" % giro)
    if not (da and al):
        return
    if al < da:
        st.error("La data finale viene prima di quella iniziale.")
        return
    ids = attr.movimenti_nel_periodo(c, da.isoformat(), al.isoformat(), scelti_conti or None)
    if not ids:
        st.info("Nessuna uscita in questo periodo.")
        return
    righe = c.execute("SELECT m.id, m.data, c.nome AS conto, coalesce(m.esercente, m.controparte_nome, m.descrizione) AS voce, "
                      "m.importo_cent, a.l1, a.l2 FROM movimenti m JOIN conti c ON c.id = m.conto_id "
                      "LEFT JOIN analisi a ON a.mov_id = m.id AND a.componente IN ('principale','parte') "
                      "WHERE m.id IN (%s) GROUP BY m.id ORDER BY m.data" % ",".join("?" * len(ids)), ids).fetchall()
    gia = attr.etichette_di(c, ids)
    df = pd.DataFrame({"Includi": True, "Data": [r["data"] for r in righe], "Conto": [r["conto"] for r in righe],
                       "Voce": [r["voce"] for r in righe], "Importo": [euro(r["importo_cent"]) for r in righe],
                       "Categoria": [etichetta_cat((r["l1"], r["l2"])) for r in righe],
                       "Etichette gia' presenti": [", ".join(gia.get(r["id"], [])) for r in righe]})
    st.caption("%d uscite nel periodo: togli la spunta a quelle da lasciare fuori." % len(df))
    ed = st.data_editor(df, hide_index=True, width="stretch", disabled=[x for x in df.columns if x != "Includi"],
                        key=chiave_tabella("et_ed_%d" % giro, [r["id"] for r in righe]), height=min(420, 38 + 35 * len(df)))
    scelti = [r["id"] for r, inc in zip(righe, ed["Includi"]) if inc]
    s, nuovo = _scegli_o_crea(c, "et_per_%d" % giro)
    if st.button("Assegna l'etichetta a %d movimenti" % len(scelti), type="primary", disabled=not scelti, key="et_ok_%d" % giro):
        try:
            eid = attr.crea_etichetta(c, nuovo) if s == "nuova" else int(s)
        except ValueError as e:
            st.error(str(e).capitalize() + ".")
            return
        attr.assegna(c, scelti, eid)
        st.session_state["et_giro"] = giro + 1
        st.toast("Etichetta assegnata a %d movimenti" % len(scelti))
        st.rerun()


def _gestisci_etichette(c):
    el = attr.etichette(c)
    with st.form("et_nuova", clear_on_submit=True):
        nome = st.text_input("Nuova etichetta", placeholder="es. Casa Macherio, Vacanza Puglia 2025")
        if st.form_submit_button("Crea"):
            try:
                attr.crea_etichetta(c, nome)
                st.rerun()
            except ValueError as e:
                st.error(str(e).capitalize() + ".")
    if not el:
        return
    st.caption("Rinomina scrivendo nella colonna Nome; spunta Elimina per togliere un'etichetta (i movimenti restano, "
               "perdono solo l'etichetta).")
    df = pd.DataFrame({"id": [e["id"] for e in el], "Nome": [e["nome"] for e in el],
                       "Movimenti": [e["movimenti"] for e in el], "Elimina": False})
    ed = st.data_editor(df, hide_index=True, width="stretch", disabled=["Movimenti"], column_config={"id": None},
                        key=chiave_tabella("et_gest", [(e["id"], e["nome"]) for e in el]))
    if st.button("Salva le modifiche", key="et_salva"):
        errori = []
        for (_, prima), (_, dopo) in zip(df.iterrows(), ed.iterrows()):
            if dopo["Elimina"]:
                attr.elimina_etichetta(c, int(prima["id"]))
            elif testo(dopo["Nome"]) != prima["Nome"]:
                try:
                    attr.rinomina_etichetta(c, int(prima["id"]), testo(dopo["Nome"]))
                except ValueError as e:
                    errori.append(str(e).capitalize() + ".")
        for e in errori:
            st.error(e)
        if not errori:
            st.rerun()



# --- portafoglio titoli -------------------------------------------------------------------
PF_ATTUALIZZATA = "Analisi attualizzata a oggi"
PF_PERIODO = "Analisi nel periodo selezionato"


def portafoglio():
    from dataclasses import replace
    from datetime import date

    from .. import mercato
    from .. import portafoglio as pf
    c = conn()
    f = filtri_sidebar()
    con_prezzi = mercato.attivo(c)
    oggi = date.today().isoformat()
    # all'apertura sempre attualizzata; la scelta resta finche' si resta sulla pagina
    attualizzata = st.session_state.get("pf_modalita", PF_ATTUALIZZATA) != PF_PERIODO
    da, a = (None, None) if attualizzata else (f.da, f.a)
    intestazione("Portafoglio", replace(f, da=None, a=None) if attualizzata else f, "Costo medio ponderato · " + (
        "prezzi di mercato da Yahoo Finance" if con_prezzi else
        "solo dati importati (i prezzi di mercato si accendono in Impostazioni)"))
    st.radio("Analisi", [PF_ATTUALIZZATA, PF_PERIODO], key="pf_modalita", horizontal=True,
             label_visibility="collapsed")
    r = pf.calcola(c, f.conti or None, da, a)
    if not r["posizioni"] and not r["vendite"]:
        st.info("Nessun movimento con ISIN nei conti scelti (oggi li riporta Trade Republic).", icon=":material/info:")
        return
    giorno = oggi if attualizzata else min(a or "9999", oggi)      # prezzi e grafici non vanno oltre oggi
    if attualizzata:
        st.caption("Tutta la storia dei conti scelti, dall'inizio a oggi, con l'ultimo prezzo disponibile: il "
                   "periodo dei filtri non si applica. Le posizioni vendute compaiono come chiuse.")
    elif a:
        st.caption("Posizioni possedute nel periodo, con il prezzo al %s. Quelle vendute dopo il %s contano come "
                   "ancora possedute." % (_data_breve(giorno), _data_breve(a)))
    prezzi = {}
    if con_prezzi:
        _aggiorna_prezzi(c, r)
        _avviso_non_aggiornati(c, r)
        prezzi = mercato.prezzi(c, list(r["passi"]))
    pf.valorizza(r["posizioni"], prezzi, giorno)
    # periodo: le posizioni chiuse prima del periodo non si mostrano, salvo con l'interruttore
    chiuse_prima = [] if attualizzata else pf.chiuse_prima(r["posizioni"], da)
    includi = bool(chiuse_prima) and bool(st.session_state.get("pf_chiuse_prima"))
    nascoste = set() if includi else {p["isin"] for p in chiuse_prima}
    mostrate = [p for p in r["posizioni"] if p["isin"] not in nascoste]
    aperte = [p for p in mostrate if p["aperta"]]
    t = pf.totali(mostrate)
    tot = {k: sum(x.get(k, 0) for x in r["anni"].values())
           for k in ("plus", "minus", "dividendi", "imposte", "commissioni", "versato", "disinvestito")}

    k = st.columns(4)
    k[0].metric("Capitale investito", euro(t["capitale"]),
                help="Costo delle %d posizioni aperte %s (prezzi pagati piu' commissioni)"
                     % (len(aperte), "oggi" if attualizzata else "alla fine del periodo"))
    if con_prezzi and t["valore"] is not None:
        k[1].metric("Valore di mercato", euro(t["valore"]),
                    help="Quote delle posizioni aperte x ultimo prezzo disponibile %s."
                         % ("" if attualizzata else "alla fine del periodo")
                         + (" I titoli senza prezzo contano al costo." if t["senza_prezzo"] else ""))
        k[2].metric("Rendimento posizioni aperte", _pct(t["rendimento_aperte"]) if t["rendimento_aperte"] is not None
                    else "–", delta=euro_tondo(t["latente"]) if t["latente"] is not None else None,
                    help="Plus/minus latente delle posizioni aperte (valore meno costo) sul loro costo, prima delle "
                         "imposte.")
        k[3].metric("Rendimento complessivo", _pct(t["rendimento_complessivo"])
                    if t["rendimento_complessivo"] is not None else "–",
                    delta=euro_tondo(t["complessivo"]) if t["complessivo"] is not None else None,
                    help="Plus/minus latenti delle posizioni aperte + plus/minus realizzate con le vendite + dividendi "
                         "lordi, sul costo di tutte le quote comprate (tenute e vendute). Prima delle imposte. Vale "
                         "per le posizioni mostrate, aperte e chiuse, dal primo acquisto "
                         + ("a oggi." if attualizzata else "alla fine del periodo.")
                         + (" Esclusi %d titoli senza prezzo o con il costo incompleto." % t["esclusi_complessivo"]
                            if t["esclusi_complessivo"] else ""))
    else:
        k[1].metric("Posizioni aperte", len(aperte))
    nel = "" if attualizzata else " nel periodo"
    k = st.columns(4)
    k[0].metric("Realizzato" + nel, euro(tot["plus"] + tot["minus"]),
                help="Plusvalenze meno minusvalenze delle vendite%s, prima delle imposte" % nel)
    k[1].metric("Dividendi" + nel, euro(tot["dividendi"]), help="Lordi; le ritenute sono tra le imposte")
    k[2].metric("Imposte e commissioni", euro(tot["imposte"] + tot["commissioni"]),
                help="Imposte trattenute dalla banca su vendite e dividendi, piu' commissioni"
                     + (", su tutta la storia" if attualizzata else ", nel periodo"))
    chiuse = len(mostrate) - len(aperte)
    k[3].metric("Posizioni", "%d apert%s" % (len(aperte), "a" if len(aperte) == 1 else "e"),
                help="%d chius%s mostrat%s" % (chiuse, "a" if chiuse == 1 else "e", "a" if chiuse == 1 else "e"))

    avvisi = [(p, x) for p in r["posizioni"] for x in p["avvisi"]]
    if avvisi:
        with st.expander("%d controlli da guardare (storico incompleto o costo da indicare)" % len(avvisi),
                         icon=":material/warning:"):
            for p, x in avvisi:
                st.markdown("- **%s** (%s): %s" % (p["titolo"], p["isin"], x))
            _partenza(c, sorted({p["isin"]: p["titolo"] for p, _ in avvisi}.items()))

    t1, t2, t3, t4 = st.tabs(["Posizioni", "Vendite", "Per anno", "Capitale investito nel tempo"])
    with t1:
        if chiuse_prima:
            st.toggle("Includi le %d posizioni chiuse prima del %s" % (len(chiuse_prima), _data_breve(da)),
                      key="pf_chiuse_prima",
                      help="Posizioni vendute del tutto prima dell'inizio del periodo: di partenza non si mostrano. "
                           "Incluse, contano nel rendimento complessivo con il loro realizzato e i dividendi.")
        if not mostrate:
            st.caption("Nessuna posizione nel periodo.")
        else:
            _posizioni(r, mostrate, prezzi, con_prezzi, da, giorno, attualizzata, {p["isin"] for p in chiuse_prima})
    with t2:
        v = r["vendite"]
        if not v:
            st.caption("Nessuna vendita" + nel + ".")
        else:
            st.dataframe(pd.DataFrame({
                "Data": [x["data"] for x in v], "Titolo": [x["titolo"] for x in v],
                "Quote": [float(x["quote"]) for x in v], "Ricavo netto": [x["ricavo"] / 100 for x in v],
                "Costo": [x["costo"] / 100 for x in v],
                "Plus/minus": [x["plus"] / 100 if x["plus"] is not None else None for x in v],
                "Imposte": [x["imposte"] / 100 for x in v], "Commissioni": [x["commissioni"] / 100 for x in v],
                "Note": ["costo incompleto" if x["incompleto"] else "" for x in v]}),
                hide_index=True, width="stretch", height=min(520, 38 + 35 * len(v)),
                column_config={c_: st.column_config.NumberColumn(format="euro")
                               for c_ in ("Ricavo netto", "Costo", "Plus/minus", "Imposte", "Commissioni")}
                | {"Quote": st.column_config.NumberColumn(format="%.6f")})
            st.caption("Ricavo netto: incasso della vendita meno la commissione. Plus/minus: ricavo netto meno il "
                       "costo medio delle quote vendute, prima delle imposte. Le imposte sono quelle trattenute "
                       "dalla banca.")
    with t3:
        righe = [{"Anno": anno, "Versato": x.get("versato", 0) / 100, "Disinvestito": x.get("disinvestito", 0) / 100,
                  "Plusvalenze": x.get("plus", 0) / 100, "Minusvalenze": x.get("minus", 0) / 100,
                  "Netto realizzato": (x.get("plus", 0) + x.get("minus", 0)) / 100,
                  "Dividendi": x.get("dividendi", 0) / 100, "Imposte": x.get("imposte", 0) / 100,
                  "Commissioni": x.get("commissioni", 0) / 100} for anno, x in r["anni"].items()]
        if righe:
            st.dataframe(pd.DataFrame(righe), hide_index=True, width="stretch",
                         column_config={k_: st.column_config.NumberColumn(format="euro") for k_ in righe[0] if k_ != "Anno"})
    with t4:
        totale = _totale_giornaliero(r["passi"], prezzi, da, giorno) if prezzi else []
        if totale:
            serie = [{"nome": "Valore di mercato", "colore": grafici.CATEGORIALI[0], "punti": [(d, x / 100) for d, _, x in totale]},
                     {"nome": "Capitale investito", "colore": GRIGIO, "tratteggio": True,
                      "punti": [(d, x / 100) for d, x, _ in totale]}]
            grafici.mostra(grafici.linee_tempo(serie), "pf_capitale_valore", 360)
            st.caption("Capitale investito: costo delle posizioni aperte, cresce con gli acquisti e cala con le vendite. "
                       "Valore di mercato: quote x prezzo di chiusura del giorno (i titoli senza prezzo contano al "
                       "costo). Rotellina o pizzico per ingrandire un tratto.")
        else:
            mesi = _mesi_continui(r["mesi"], da, giorno)
            if mesi:
                opz = grafici.linea_mensile([m for m, _ in mesi], [x for _, x in mesi], "Capitale investito")
                grafici.mostra(opz, "pf_capitale", 340)
                st.caption("Costo delle posizioni aperte a fine mese: cresce con gli acquisti, cala con le vendite "
                           "(della parte di costo venduta). Non e' il valore di mercato.")
    if con_prezzi:
        _simboli(c, r)


def _avviso_non_aggiornati(c, r):
    """Avviso fisso in cima alla pagina finche' qualche titolo in portafoglio non ha i prezzi aggiornati."""
    from .. import mercato
    falliti = [i for i in mercato.non_aggiornati(c) if i in r["passi"]]
    if not falliti:
        return
    nomi = {p["isin"]: p["titolo"] for p in r["posizioni"]}
    st.warning("Non e' stato possibile aggiornare i prezzi di: %s. Per %s valore e rendimenti usano l'ultimo prezzo "
               "salvato; il motivo e' in «Simboli di borsa e prezzi», in fondo alla pagina. Si riprova da solo "
               "riaprendo l'app, oppure subito con «Aggiorna i prezzi ora»."
               % (", ".join("%s (%s)" % (nomi.get(i, i), i) for i in falliti),
                  "questo titolo" if len(falliti) == 1 else "questi titoli"), icon=":material/sync_problem:")


def euro_tondo(cent):
    """Euro interi con segno, per le etichette brevi (es. +2.650 €)."""
    v = int(round(cent / 100))
    return ("+" if v >= 0 else "-") + "{:,}".format(abs(v)).replace(",", ".") + " €"


def _pct(x):
    return ("+" if x >= 0 else "") + ("%.1f%%" % (x * 100)).replace(".", ",")


def _isin_da_aggiornare(r):
    from datetime import date
    return {isin: date.fromisoformat(passi[0][0]) for isin, passi in r["passi"].items() if passi}


def _aggiorna_prezzi(c, r, forza=False):
    """Una volta al giorno, all'apertura della pagina (e con il pulsante): scarica i prezzi mancanti. Se oggi un
    tentativo e' gia' stato fatto ma qualche titolo non e' andato, si riprovano solo quelli."""
    from datetime import date

    from .. import mercato
    oggi = date.today().isoformat()
    if not forza and (not mercato.da_aggiornare(c) or st.session_state.get("_pf_tentato") == oggi):
        return
    st.session_state["_pf_tentato"] = oggi             # una prova al giorno: senza rete non si riprova a ogni clic
    isin_dal = _isin_da_aggiornare(r)
    if not forza:
        isin_dal = mercato.da_scaricare(c, isin_dal)
        if not isin_dal:
            return
    barra = st.progress(0.0, text="Scarico i prezzi di mercato da Yahoo Finance…")
    errori = mercato.aggiorna(c, isin_dal, avanzamento=lambda i, n, sim: barra.progress(i / max(n, 1), text=(
        "Scarico i prezzi di mercato da Yahoo Finance… titolo %d di %d (%s)" % (i + 1, n, sim) if i < n else
        "Prezzi di mercato aggiornati")))
    barra.empty()
    if mercato.RETE in errori:
        st.warning(errori[mercato.RETE], icon=":material/cloud_off:")


def _posizioni(r, righe, prezzi, con_prezzi, da, giorno, attualizzata, chiuse_prima):
    """Tabella delle posizioni (aperte e chiuse) e grafico dell'andamento delle righe selezionate."""
    from .. import portafoglio as pf
    cent = lambda v: v / 100 if v is not None else None
    vecchio = {p["isin"]: attualizzata and pf.prezzo_vecchio(p, giorno) for p in righe}

    def nota(p):
        n = []
        if p["costo_da_indicare"]:
            n.append("costo da indicare")
        elif p["avvisi"]:
            n.append("da controllare")
        if p["realizzato"] is None:
            n.append("vendite con costo incompleto")
        if vecchio[p["isin"]]:
            n.append("prezzo vecchio")
        return "; ".join(n)
    dati = {"Titolo": [p["titolo"] for p in righe], "ISIN": [p["isin"] for p in righe],
            "Stato": [pf.stato(p) for p in righe],
            "Quote": [float(p["quote"]) if p["aperta"] else None for p in righe],
            "Costo medio": [float(p["costo_medio"]) if p["costo_medio"] is not None else None for p in righe],
            "Capitale investito": [p["costo"] / 100 if p["aperta"] else None for p in righe]}
    conf = {"Quote": st.column_config.NumberColumn(format="%.6f"),
            "Costo medio": st.column_config.NumberColumn(format="euro"),
            "Capitale investito": st.column_config.NumberColumn(format="euro"),
            "Realizzato": st.column_config.NumberColumn(format="euro", help="Plus/minus delle vendite del titolo, "
                                                        "prima delle imposte"),
            "Dividendi": st.column_config.NumberColumn(format="euro", help="Lordi")}
    if con_prezzi:
        dati |= {"Prezzo": [p.get("prezzo") for p in righe],
                 "Del": [_data_breve(p.get("data_prezzo")) for p in righe],
                 "Valore": [cent(p.get("valore")) for p in righe],
                 "Plus/minus latente": [cent(p.get("latente")) for p in righe],
                 "Rendimento": [p["rendimento"] * 100 if p.get("rendimento") is not None else None for p in righe]}
        conf |= {"Prezzo": st.column_config.NumberColumn(format="euro", help="Ultima chiusura disponibile, in euro"),
                 "Valore": st.column_config.NumberColumn(format="euro"),
                 "Plus/minus latente": st.column_config.NumberColumn(format="euro", help="Valore meno costo, prima delle imposte"),
                 "Rendimento": st.column_config.NumberColumn(format="%.1f%%", help="Della parte aperta: plus/minus "
                                                             "latente sul costo")}
    dati |= {"Realizzato": [cent(p["realizzato"]) if p["vendite_totali"] else None for p in righe],
             "Dividendi": [p["dividendi"] / 100 for p in righe]}
    if con_prezzi:
        dati["Rend. complessivo"] = [p["rendimento_complessivo"] * 100 if p.get("rendimento_complessivo") is not None
                                     else None for p in righe]
        conf["Rend. complessivo"] = st.column_config.NumberColumn(
            format="%.1f%%", help="Latente + realizzato + dividendi lordi, sul costo di tutte le quote comprate")
    dati |= {"Dal": [p["primo"] or "" for p in righe], "Note": [nota(p) for p in righe]}
    tabella = pd.DataFrame(dati)
    # guadagni verdi, perdite rosse: plus/minus latente, realizzato (anche delle chiuse) e i due rendimenti
    tabella = tabella.style.map(colore_segno, subset=[c_ for c_ in ("Plus/minus latente", "Rendimento", "Realizzato",
                                                                    "Rend. complessivo") if c_ in tabella])
    ev = st.dataframe(tabella, hide_index=True, width="stretch", height=min(520, 38 + 35 * len(righe)),
                      column_config=conf, on_select="rerun", selection_mode="multi-row",
                      key=chiave_tabella("pf_posizioni", [p["isin"] for p in righe]))
    if any(vecchio.values()):
        st.caption("Prezzo vecchio: l'ultima chiusura salvata ha piu' di 5 giorni. Controlla il simbolo in «Simboli di "
                   "borsa e prezzi» o aggiorna i prezzi.")
    scelte = list(getattr(getattr(ev, "selection", None), "rows", []) or []) or [0]
    if len(scelte) > len(grafici.CATEGORIALI):
        st.caption("Nel grafico entrano al massimo %d posizioni insieme: mostro le prime." % len(grafici.CATEGORIALI))
        scelte = scelte[:len(grafici.CATEGORIALI)]
    scelte = sorted(scelte)
    # colore fisso per titolo: segue l'ordine della tabella, non la selezione
    colori, liberi = {}, [x for i, x in enumerate(grafici.CATEGORIALI) if i not in scelte]
    for i in scelte:
        colori[i] = grafici.CATEGORIALI[i] if i < len(grafici.CATEGORIALI) else liberi.pop(0)

    st.markdown("**Andamento nel tempo** · %s" % (
        righe[scelte[0]]["titolo"] if len(scelte) == 1 else "%d posizioni" % len(scelte)))
    valutabili = [i for i in scelte if prezzi.get(righe[i]["isin"])]
    misure = ["Valore", "Rendimento %", "Capitale investito"] if valutabili else ["Capitale investito"]
    if st.session_state.get("pf_misura") not in misure:
        st.session_state.pop("pf_misura", None)            # scelta di prima non disponibile per queste posizioni
    misura = st.segmented_control("Misura", misure, default=misure[0], key="pf_misura",
                                  label_visibility="collapsed") or misure[0]
    # una posizione chiusa prima del periodo si vede per intero, dall'acquisto alla vendita
    da_grafico = None if any(righe[i]["isin"] in chiuse_prima for i in scelte) else da
    serie = []
    for i in scelte:
        p = righe[i]
        punti = pf.andamento(r["passi"].get(p["isin"], []), prezzi.get(p["isin"]) if misura != "Capitale investito"
                             else None, da_grafico, giorno, interrompi=True)
        nome = p["titolo"] if len(p["titolo"]) <= 32 else p["titolo"][:31] + "…"
        if misura == "Capitale investito":
            serie.append({"nome": nome, "colore": colori[i], "punti": [(d, cent(x)) for d, _, x, _ in punti]})
        elif i not in valutabili:
            continue
        elif misura == "Valore":
            serie.append({"nome": nome, "colore": colori[i], "punti": [(d, cent(v)) for d, _, _, v in punti]})
            if len(scelte) == 1:
                serie.append({"nome": "Capitale investito", "colore": GRIGIO, "tratteggio": True,
                              "punti": [(d, cent(x)) for d, _, x, _ in punti]})
        else:
            if p["costo_da_indicare"]:
                continue
            serie.append({"nome": nome, "colore": colori[i],
                          "punti": [(d, (v - x) / x * 100 if v is not None and x else None) for d, _, x, v in punti]})
    if not serie or not any(v is not None for s in serie for _, v in s["punti"]):
        st.caption("Nessun dato da mostrare per la scelta fatta.")
        return
    grafici.mostra(grafici.linee_tempo(serie, percento=misura == "Rendimento %", gradino=misura == "Capitale investito"),
                   "pf_andamento", 360)
    note = ["Seleziona una o piu' righe della tabella per vedere o confrontare le posizioni."]
    if any(not righe[i]["aperta"] for i in scelte):
        note.append("Le posizioni chiuse si fermano al giorno della vendita.")
    mancano = [righe[i]["titolo"] for i in scelte if i not in valutabili]
    if misura != "Capitale investito" and mancano:
        note.append("Senza prezzi: %s." % ", ".join(mancano))
    if misura == "Rendimento %":
        note.append("Rendimento: valore meno costo, sul costo delle quote tenute quel giorno (senza dividendi e "
                    "plusvalenze gia' realizzate).")
    elif not con_prezzi:
        note.append("Con i prezzi di mercato (Impostazioni) si vedono anche valore e rendimento.")
    st.caption(" ".join(note))


def _data_breve(d):
    return "%s/%s/%s" % (d[8:10], d[5:7], d[:4]) if d else ""


def _totale_giornaliero(passi, prezzi, da, a):
    """Somma giorno per giorno di costo e valore delle posizioni: [(data, costo_cent, valore_cent)]. I titoli senza
    prezzo contano al costo."""
    from .. import portafoglio as pf
    per_titolo = {isin: {d: (x, v) for d, _, x, v in pf.andamento(ps, prezzi.get(isin), da, a)}
                  for isin, ps in passi.items()}
    giorni = sorted({d for ser in per_titolo.values() for d in ser})
    ultimo = {}
    out = []
    for g in giorni:
        for isin, ser in per_titolo.items():
            if g in ser:
                ultimo[isin] = ser[g]
        costo = sum(x for x, _ in ultimo.values())
        valore = sum(v if v is not None else x for x, v in ultimo.values())
        out.append((g, costo, valore))
    return out


def _simboli(c, r):
    from .. import mercato
    noti = mercato.titoli(c)
    nomi = {p["isin"]: p["titolo"] for p in r["posizioni"]}
    elenco = sorted(r["passi"], key=lambda i: nomi.get(i, i))
    falliti = set(mercato.non_aggiornati(c)) & set(elenco)
    errori = sum(1 for i in elenco if (noti.get(i) or {}).get("errore") or i in falliti)
    with st.expander("Simboli di borsa e prezzi" + (" · %d da sistemare" % errori if errori else ""),
                     icon=":material/query_stats:", expanded=False):
        ultimi = {i: (ser[-1][0] if ser else None) for i, ser in mercato.prezzi(c, elenco).items()}
        st.dataframe(pd.DataFrame({
            "Titolo": [nomi.get(i, i) for i in elenco], "ISIN": elenco,
            "Simbolo": [(noti.get(i) or {}).get("simbolo") or "" for i in elenco],
            "Borsa": [(noti.get(i) or {}).get("borsa") or "" for i in elenco],
            "Valuta": [(noti.get(i) or {}).get("valuta") or "" for i in elenco],
            "Ultimo prezzo": [_data_breve(ultimi.get(i)) for i in elenco],
            "Problema": [(noti.get(i) or {}).get("errore") or
                         ("prezzi non aggiornati: Yahoo Finance non raggiungibile" if i in falliti else "")
                         for i in elenco]}),
            hide_index=True, width="stretch")
        st.caption("Il simbolo si cerca da solo con l'ISIN, preferendo una borsa in euro. Se e' sbagliato o manca, "
                   "indicalo come su finance.yahoo.com (es. VWCE.DE, NVD.DE, AAPL); vuoto torna alla ricerca "
                   "automatica. I prezzi in altra valuta si convertono in euro con il cambio del giorno.")
        with st.form("pf_simbolo"):
            a, b, d = st.columns([3, 1.6, 1.4], vertical_alignment="bottom")
            isin = a.selectbox("Titolo", elenco, format_func=lambda i: "%s · %s" % (nomi.get(i, i), i))
            simbolo = b.text_input("Simbolo Yahoo", placeholder="es. VWCE.DE")
            if d.form_submit_button("Usa questo simbolo", width="stretch"):
                mercato.imposta_simbolo(c, isin, simbolo)
                c.commit()
                st.session_state.pop("_pf_tentato", None)
                st.rerun()
        x, y = st.columns([1, 3], vertical_alignment="center")
        if x.button("Aggiorna i prezzi ora", icon=":material/refresh:"):
            _aggiorna_prezzi(c, r, forza=True)
            st.rerun()
        ult = leggi_impostazione(c, mercato.CHIAVE_ULTIMO)
        tent = leggi_impostazione(c, mercato.CHIAVE_TENTATIVO)
        testo_ = "Prezzi aggiornati per tutti i titoli: %s." % (_data_breve(ult) if ult else "mai")
        if falliti:
            testo_ += " Ultimo tentativo%s: non aggiornat%s %d titol%s (vedi Problema)." % (
                " il " + _data_breve(tent) if tent else "", "o" if len(falliti) == 1 else "i", len(falliti),
                "o" if len(falliti) == 1 else "i")
        y.caption(testo_ + " Si aggiorna da solo una volta al giorno aprendo questa pagina.")


def _mesi_continui(mesi, da, a):
    """Riempie i mesi senza movimenti con il valore del mese prima, dentro il periodo scelto."""
    if not mesi:
        return []
    valori = dict(mesi)
    primo = max(mesi[0][0], (da or mesi[0][0])[:7])
    ultimo = (a or mesi[-1][0])[:7]
    out, cur, val = [], primo, 0
    prima = [v for m, v in mesi if m < primo]
    val = prima[-1] if prima else 0
    while cur <= ultimo:
        val = valori.get(cur, val)
        out.append((cur, val))
        y, m = int(cur[:4]), int(cur[5:7]) + 1
        cur = "%04d-%02d" % (y + (m > 12), 1 if m > 12 else m)
    return out


def _partenza(c, titoli):
    from .. import portafoglio as pf
    st.markdown("**Posizione di partenza** — se gli estratti iniziano dopo il primo acquisto, o per le quote "
                "arrivate senza costo: quote e costo che avevi a quella data. Per le quote arrivate senza costo "
                "(migrazione) usa la data della migrazione: il costo indicato vale per quelle quote, che non "
                "vengono contate una seconda volta.")
    attuali = pf.partenze(c)
    with st.form("pf_partenza"):
        a, b, d, e = st.columns([3, 1.3, 1.2, 1.3])
        isin = a.selectbox("Titolo", [i for i, _ in titoli], format_func=dict(titoli).get)
        p = attuali.get(isin, {})
        data = b.date_input("Alla data", format="DD/MM/YYYY",
                            value=pd.to_datetime(p["data"]).date() if p.get("data") else None)
        quote = d.number_input("Quote", min_value=0.0, step=1.0, format="%.6f", value=float(p.get("quote", 0)))
        costo = e.number_input("Costo totale €", min_value=0.0, step=10.0, format="%.2f",
                               value=p.get("costo_cent", 0) / 100)
        if st.form_submit_button("Salva la posizione di partenza (quote 0 la toglie)"):
            if quote > 0 and not data:
                st.error("Indica la data.")
            else:
                pf.imposta_partenza(c, isin, data.isoformat() if data else None, quote, round(costo * 100))
                st.rerun()
