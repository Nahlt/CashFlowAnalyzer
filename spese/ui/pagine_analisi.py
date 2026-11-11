"""Pagine di analisi: andamento mensile, dove vanno i soldi (con il dettaglio per categoria), etichette."""
import pandas as pd
import streamlit as st

from .. import analisi as an
from .. import attributi as attr
from ..db import leggi_impostazione
from ..numeri import euro
from . import grafici
from .comune import GRIGIO, chiave_tabella, conn, etichetta_cat, filtri_sidebar, intestazione, testo


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
    c3.metric("Saldo netto", euro(netto))
    c4.metric("Tasso di risparmio", ("%d%%" % round(100 * netto / ent)) if ent else "—")
    mesi = list(df["mese"])
    with st.container(key="scheda_and_linee"):
        st.subheader("Entrate e uscite per mese")
        st.caption("Passa il mouse su un mese per vedere dove sono andati i soldi; clicca per aprire il dettaglio.")
        i = grafici.mostra(grafici.linee_entrate_uscite(df, an.anteprima_mensile(c, f)), "and_linee", 360, clic="x")
    if i is not None and 0 <= i < len(mesi):
        _vai_al_mese(mesi[i])
    with st.container(key="scheda_and_saldo"):
        st.subheader("Saldo netto per mese")
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
                          "Saldo netto": [euro(v) for v in df["netto"]],
                          "Tasso di risparmio": ["—" if pd.isna(t) else "%d%%" % round(100 * t) for t in df["tasso"]],
                          "Risparmio": [euro(v) for v in df["risparmio"]]})
        st.dataframe(t, hide_index=True, width="stretch")


def _mesi_del_periodo(c, f):
    """Tutti i mesi del periodo scelto (anche quelli senza spese nella categoria), per l'asse del grafico."""
    df = an.flussi_mensili(c, f)
    return list(df["mese"]) if not df.empty else []


def ripartizione():
    """Dove vanno i soldi: categorie in alto (cliccabili); sotto, sottocategorie della categoria scelta (30%) e
    spesa per mese con media mobile (70%). Il mese scelto dall'andamento filtra categorie e sottocategorie;
    grafico per mese, totale e media restano sull'intero periodo."""
    f = filtri_sidebar()
    intestazione("Dove vanno i soldi", f, "Uscite al netto dei rimborsi · clicca una categoria per il dettaglio")
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
        b.caption("Categorie e sottocategorie del solo mese scelto. Il grafico per mese, il totale e la media "
                  "restano sull'intero periodo.")
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
    k0, k1, k2 = st.columns(3)
    k0.metric("Uscite di %s" % grafici.mese_lungo(mese) if mese else "Uscite del periodo", euro(tot))
    k1.metric("%s · totale nel periodo" % (l2 or nome_l1), euro(totale))
    k2.metric("%s · media mensile" % (l2 or nome_l1), euro(round(totale / n_mesi)))

    with st.container(key="scheda_categorie"):
        st.subheader("Categorie" + (" · %s" % grafici.mese_lungo(mese) if mese else ""))
        st.caption("Trasferimenti tra conti, risparmio e addebiti riepilogativi della carta sono esclusi.")
        opz, ordine = grafici.barre_orizzontali(voci, list(df["importo"]), l1)
        i = grafici.mostra(opz, "rip_l1_graf", max(220, 30 * len(voci) + 30), clic="y")
    if i is not None and 0 <= i < len(ordine) and ordine[i] != l1:
        st.session_state["rip_l1"], st.session_state["rip_l2"] = ordine[i], None
        st.rerun()

    sx, dx = st.columns([3, 7])
    with sx, st.container(key="scheda_sottocategorie"):
        st.subheader("%s · sottocategorie" % nome_l1)
        i = None
        if l1 == "Da classificare":
            sotto = pd.DataFrame()
            st.caption("Voci ancora senza categoria: si sistemano in **Revisione**.")
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
        st.subheader("Spesa per mese" + (" · %s" % l2 if l2 else " · %s" % nome_l1))
        grafici.mostra(grafici.serie_mensile(mesi_periodo, [valori.get(m, 0) for m in mesi_periodo], mese),
                       "rip_serie", 300)

    with st.expander("Tabella delle categorie"):
        t = df.copy()
        t["Quota"] = ["%d%%" % round(100 * v / tot) for v in t["importo"]] if tot else ""
        t["Importo"] = [euro(v) for v in t["importo"]]
        st.dataframe(t[["voce", "Importo", "Quota", "movimenti"]].rename(columns={"voce": "Categoria", "movimenti": "Movimenti"}),
                     hide_index=True, width="stretch")
    dove = (" di %s" % grafici.mese_lungo(mese) if mese else "") + (" · %s" % (l2 or nome_l1))
    with st.expander("Esercenti principali" + dove):
        es = an.esercenti_categoria(c, fm, l1, l2)
        if es.empty:
            st.caption("Nessun esercente.")
        else:
            es["Importo"] = [euro(v) for v in es["importo"]]
            st.dataframe(es[["esercente", "Importo", "movimenti"]].rename(columns={"esercente": "Esercente", "movimenti": "Movimenti"}),
                         hide_index=True, width="stretch")
    with st.expander("Movimenti" + dove):
        mv = an.movimenti(c, fm, l1, l2)
        if mv.empty:
            st.caption("Nessun movimento.")
        else:
            st.dataframe(pd.DataFrame({"Data": mv["data"], "Conto": mv["conto"],
                                       "Esercente": [testo(e) or testo(n) for e, n in zip(mv["esercente"], mv["controparte_nome"])],
                                       "Importo": [euro(v) for v in mv["importo_cent"]], "Sottocategoria": mv["l2"].fillna(""),
                                       "Fonte": mv["fonte"].fillna("")}), hide_index=True, width="stretch")
    if attr.natura_attiva(c):
        _natura_spesa(f)


def _natura_spesa(f):
    """Quote e andamento per natura della spesa (sull'intero periodo)."""
    with st.container(key="scheda_natura"):
        _natura_spesa_dentro(f)


def _natura_spesa_dentro(f):
    st.subheader("Natura della spesa")
    df = an.per_attributo(conn(), f)
    if df.empty:
        return
    valori = attr.valori()
    serie = [(k, v) for k, v in valori.items()] + [("", "Non definita")]
    tot_per = df.groupby("attributo")["importo"].sum().to_dict()
    tot = sum(tot_per.values())
    cols = st.columns(len(serie))
    for col, (k, nome) in zip(cols, serie):
        v = int(tot_per.get(k, 0))
        col.metric(nome, euro(v))
        if tot:
            col.caption("%d%% delle uscite" % round(100 * v / tot))
    st.caption("Viene dalla sottocategoria (si cambia in Regole e tassonomia, scheda Natura della spesa) e si puo' "
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
                st.dataframe(pd.DataFrame({"Data": mv["data"], "Conto": mv["conto"], "Voce": mv["voce"],
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
def portafoglio():
    from datetime import date

    from .. import mercato
    from .. import portafoglio as pf
    c = conn()
    f = filtri_sidebar()
    con_prezzi = mercato.attivo(c)
    intestazione("Portafoglio", f, "Costo medio ponderato · " + (
        "prezzi di mercato da Yahoo Finance" if con_prezzi else
        "solo dati importati (i prezzi di mercato si accendono in Impostazioni)"))
    r = pf.calcola(c, f.conti or None, f.da, f.a)
    if not r["posizioni"] and not r["vendite"]:
        st.info("Nessun movimento con ISIN nei conti scelti (oggi li riporta Trade Republic).", icon=":material/info:")
        return
    giorno = min(f.a or "9999", date.today().isoformat())      # prezzi e grafici non vanno oltre oggi
    prezzi = {}
    if con_prezzi:
        _aggiorna_prezzi(c, r)
        prezzi = mercato.prezzi(c, list(r["passi"]))
        pf.valorizza(r["posizioni"], prezzi, giorno)
    aperte = [p for p in r["posizioni"] if p["aperta"]]
    valutate = [p for p in aperte if p.get("valore") is not None]
    tot = {k: sum(a.get(k, 0) for a in r["anni"].values())
           for k in ("plus", "minus", "dividendi", "imposte", "commissioni", "versato", "disinvestito")}
    investito = sum(p["costo"] for p in aperte)
    k = st.columns(5)
    k[0].metric("Capitale investito", euro(investito),
                help="Costo delle %d posizioni aperte alla fine del periodo (prezzi pagati piu' commissioni)" % len(aperte))
    if valutate:
        valore = sum(p["valore"] for p in valutate) + sum(p["costo"] for p in aperte if p.get("valore") is None)
        latente = [p["latente"] for p in valutate if p["latente"] is not None]
        base = sum(p["costo"] for p in valutate if p["latente"] is not None)
        k[1].metric("Valore di mercato", euro(valore),
                    delta=("%s · %s" % (_pct(sum(latente) / base), euro_tondo(sum(latente)))) if latente and base else None,
                    help="Quote x ultimo prezzo disponibile alla fine del periodo. Variazione: plus/minus latente "
                         "rispetto al costo, prima delle imposte."
                         + (" I titoli senza prezzo contano al costo." if len(valutate) < len(aperte) else ""))
    else:
        k[1].metric("Posizioni aperte", len(aperte))
    k[2].metric("Realizzato nel periodo", euro(tot["plus"] + tot["minus"]),
                help="Plusvalenze meno minusvalenze delle vendite del periodo, prima delle imposte")
    k[3].metric("Dividendi nel periodo", euro(tot["dividendi"]), help="Lordi; le ritenute sono tra le imposte")
    k[4].metric("Imposte e commissioni", euro(tot["imposte"] + tot["commissioni"]),
                help="Imposte trattenute dalla banca su vendite e dividendi, piu' commissioni")

    avvisi = [(p, a) for p in r["posizioni"] for a in p["avvisi"]]
    if avvisi:
        with st.expander("%d controlli da guardare (storico incompleto o costo da indicare)" % len(avvisi),
                         icon=":material/warning:"):
            for p, a in avvisi:
                st.markdown("- **%s** (%s): %s" % (p["titolo"], p["isin"], a))
            _partenza(c, sorted({p["isin"]: p["titolo"] for p, _ in avvisi}.items()))

    t1, t2, t3, t4 = st.tabs(["Posizioni aperte", "Vendite", "Per anno", "Capitale investito nel tempo"])
    with t1:
        if not aperte:
            st.caption("Nessuna posizione aperta alla fine del periodo.")
        else:
            _posizioni_aperte(r, aperte, prezzi, con_prezzi, f.da, giorno)
    with t2:
        v = r["vendite"]
        if not v:
            st.caption("Nessuna vendita nel periodo.")
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
        righe = [{"Anno": anno, "Versato": a.get("versato", 0) / 100, "Disinvestito": a.get("disinvestito", 0) / 100,
                  "Plusvalenze": a.get("plus", 0) / 100, "Minusvalenze": a.get("minus", 0) / 100,
                  "Netto realizzato": (a.get("plus", 0) + a.get("minus", 0)) / 100,
                  "Dividendi": a.get("dividendi", 0) / 100, "Imposte": a.get("imposte", 0) / 100,
                  "Commissioni": a.get("commissioni", 0) / 100} for anno, a in r["anni"].items()]
        if righe:
            st.dataframe(pd.DataFrame(righe), hide_index=True, width="stretch",
                         column_config={k_: st.column_config.NumberColumn(format="euro") for k_ in righe[0] if k_ != "Anno"})
    with t4:
        totale = _totale_giornaliero(r["passi"], prezzi, f.da, giorno) if prezzi else []
        if totale:
            serie = [{"nome": "Valore di mercato", "colore": grafici.CATEGORIALI[0], "punti": [(d, v / 100) for d, _, v in totale]},
                     {"nome": "Capitale investito", "colore": GRIGIO, "tratteggio": True,
                      "punti": [(d, x / 100) for d, x, _ in totale]}]
            grafici.mostra(grafici.linee_tempo(serie), "pf_capitale_valore", 360)
            st.caption("Capitale investito: costo delle posizioni aperte, cresce con gli acquisti e cala con le vendite. "
                       "Valore di mercato: quote x prezzo di chiusura del giorno (i titoli senza prezzo contano al "
                       "costo). Rotellina o pizzico per ingrandire un tratto.")
        else:
            mesi = _mesi_continui(r["mesi"], f.da, giorno)
            if mesi:
                opz = grafici.linea_mensile([m for m, _ in mesi], [v for _, v in mesi], "Capitale investito")
                grafici.mostra(opz, "pf_capitale", 340)
                st.caption("Costo delle posizioni aperte a fine mese: cresce con gli acquisti, cala con le vendite "
                           "(della parte di costo venduta). Non e' il valore di mercato.")
    if con_prezzi:
        _simboli(c, r)


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
    """Una volta al giorno, all'apertura della pagina (e con il pulsante): scarica i prezzi mancanti."""
    from datetime import date

    from .. import mercato
    oggi = date.today().isoformat()
    if not forza and (not mercato.da_aggiornare(c) or st.session_state.get("_pf_tentato") == oggi):
        return
    st.session_state["_pf_tentato"] = oggi             # una prova al giorno: senza rete non si riprova a ogni clic
    isin_dal = _isin_da_aggiornare(r)
    barra = st.progress(0.0, text="Scarico i prezzi di mercato da Yahoo Finance…")
    errori = mercato.aggiorna(c, isin_dal, avanzamento=lambda i, n, sim: barra.progress(i / max(n, 1), text=(
        "Scarico i prezzi di mercato da Yahoo Finance… titolo %d di %d (%s)" % (i + 1, n, sim) if i < n else
        "Prezzi di mercato aggiornati")))
    barra.empty()
    if mercato.RETE in errori:
        st.warning(errori[mercato.RETE], icon=":material/cloud_off:")
    elif errori:
        st.warning("Prezzi non scaricati per %d titol%s: vedi «Simboli di borsa» in fondo alla pagina."
                   % (len(errori), "o" if len(errori) == 1 else "i"), icon=":material/warning:")


def _posizioni_aperte(r, aperte, prezzi, con_prezzi, da, giorno):
    from .. import portafoglio as pf
    dati = {"Titolo": [p["titolo"] for p in aperte], "ISIN": [p["isin"] for p in aperte],
            "Quote": [float(p["quote"]) for p in aperte],
            "Costo medio": [float(p["costo_medio"]) if p["costo_medio"] is not None else None for p in aperte],
            "Capitale investito": [p["costo"] / 100 for p in aperte]}
    conf = {"Quote": st.column_config.NumberColumn(format="%.6f"),
            "Costo medio": st.column_config.NumberColumn(format="euro"),
            "Capitale investito": st.column_config.NumberColumn(format="euro"),
            "Dividendi": st.column_config.NumberColumn(format="euro")}
    if con_prezzi:
        dati |= {"Prezzo": [p.get("prezzo") for p in aperte],
                 "Del": [_data_breve(p.get("data_prezzo")) for p in aperte],
                 "Valore": [p["valore"] / 100 if p.get("valore") is not None else None for p in aperte],
                 "Plus/minus latente": [p["latente"] / 100 if p.get("latente") is not None else None for p in aperte],
                 "Rendimento": [p["rendimento"] * 100 if p.get("rendimento") is not None else None for p in aperte]}
        conf |= {"Prezzo": st.column_config.NumberColumn(format="euro", help="Ultima chiusura disponibile, in euro"),
                 "Valore": st.column_config.NumberColumn(format="euro"),
                 "Plus/minus latente": st.column_config.NumberColumn(format="euro", help="Valore meno costo, prima delle imposte"),
                 "Rendimento": st.column_config.NumberColumn(format="%.1f%%", help="Plus/minus latente sul costo")}
    dati |= {"Dal": [p["primo"] or "" for p in aperte], "Dividendi": [p["dividendi"] / 100 for p in aperte],
             "Note": ["costo da indicare" if p["costo_da_indicare"] else ("da controllare" if p["avvisi"] else "")
                      for p in aperte]}
    ev = st.dataframe(pd.DataFrame(dati), hide_index=True, width="stretch", height=min(520, 38 + 35 * len(aperte)),
                      column_config=conf, on_select="rerun", selection_mode="multi-row",
                      key=chiave_tabella("pf_posizioni", [p["isin"] for p in aperte]))
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
        aperte[scelte[0]]["titolo"] if len(scelte) == 1 else "%d posizioni" % len(scelte)))
    valutabili = [i for i in scelte if prezzi.get(aperte[i]["isin"])]
    misure = ["Valore", "Rendimento %", "Capitale investito"] if valutabili else ["Capitale investito"]
    if st.session_state.get("pf_misura") not in misure:
        st.session_state.pop("pf_misura", None)            # scelta di prima non disponibile per queste posizioni
    misura = st.segmented_control("Misura", misure, default=misure[0], key="pf_misura",
                                  label_visibility="collapsed") or misure[0]
    serie = []
    for i in scelte:
        p = aperte[i]
        punti = pf.andamento(r["passi"].get(p["isin"], []), prezzi.get(p["isin"]) if misura != "Capitale investito"
                             else None, da, giorno)
        nome = p["titolo"] if len(p["titolo"]) <= 32 else p["titolo"][:31] + "…"
        if misura == "Capitale investito":
            serie.append({"nome": nome, "colore": colori[i], "punti": [(d, x / 100) for d, _, x, _ in punti]})
        elif i not in valutabili:
            continue
        elif misura == "Valore":
            serie.append({"nome": nome, "colore": colori[i],
                          "punti": [(d, v / 100 if v is not None else None) for d, _, _, v in punti]})
            if len(scelte) == 1:
                serie.append({"nome": "Capitale investito", "colore": GRIGIO, "tratteggio": True,
                              "punti": [(d, x / 100) for d, _, x, _ in punti]})
        else:
            if p["costo_da_indicare"]:
                continue
            serie.append({"nome": nome, "colore": colori[i],
                          "punti": [(d, (v - x) / x * 100 if v is not None and x > 0 else None) for d, _, x, v in punti]})
    if not serie:
        st.caption("Nessun dato da mostrare per la scelta fatta.")
        return
    grafici.mostra(grafici.linee_tempo(serie, percento=misura == "Rendimento %", gradino=misura == "Capitale investito"),
                   "pf_andamento", 360)
    note = ["Seleziona una o piu' righe della tabella per vedere o confrontare le posizioni."]
    mancano = [aperte[i]["titolo"] for i in scelte if i not in valutabili]
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
    errori = sum(1 for i in elenco if (noti.get(i) or {}).get("errore"))
    with st.expander("Simboli di borsa e prezzi" + (" · %d da sistemare" % errori if errori else ""),
                     icon=":material/query_stats:", expanded=False):
        ultimi = {i: (ser[-1][0] if ser else None) for i, ser in mercato.prezzi(c, elenco).items()}
        st.dataframe(pd.DataFrame({
            "Titolo": [nomi.get(i, i) for i in elenco], "ISIN": elenco,
            "Simbolo": [(noti.get(i) or {}).get("simbolo") or "" for i in elenco],
            "Borsa": [(noti.get(i) or {}).get("borsa") or "" for i in elenco],
            "Valuta": [(noti.get(i) or {}).get("valuta") or "" for i in elenco],
            "Ultimo prezzo": [_data_breve(ultimi.get(i)) for i in elenco],
            "Problema": [(noti.get(i) or {}).get("errore") or "" for i in elenco]}),
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
        y.caption("Ultimo aggiornamento: %s. Si aggiorna da solo una volta al giorno aprendo questa pagina."
                  % (_data_breve(ult) if ult else "mai"))


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
