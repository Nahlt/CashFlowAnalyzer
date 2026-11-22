"""Pagina Budget: budget mensili per categoria o sottocategoria, un mese alla volta."""
from datetime import date

import pandas as pd
import streamlit as st

from .. import budget as bd
from .. import servizi
from ..numeri import euro
from . import grafici
from .comune import conn, intestazione

ICONE_STATO = {bd.SUPERATO: ":material/error:", bd.A_RISCHIO: ":material/warning:"}


def _oggi():
    return date.today()


def _mesi_scelta(c, oggi):
    """Mesi da scegliere: dal primo mese con movimenti (o dal primo budget, se viene prima) fino al mese in corso."""
    primo = c.execute("SELECT min(mese) FROM analisi").fetchone()[0]
    inizi = [b["importi"][0][0] for b in bd.elenco(c)]
    corrente = bd.mese_di(oggi)
    candidati = [m for m in [primo] + inizi if m and m <= corrente]
    da = min(candidati) if candidati else corrente
    da = max(da, bd.sposta_mese(corrente, -60))            # al massimo cinque anni indietro
    mesi = []
    m = da
    while m <= corrente:
        mesi.append(m)
        m = bd.sposta_mese(m, 1)
    return mesi[::-1]


def budget():
    """Budget mensili: copertura dei conti, stato dei budget del mese (barre di saturazione con speso e
    previsto), dettaglio del budget scelto (cumulato del mese), storico di 12 mesi, aggiunta e gestione."""
    c = conn()
    oggi = _oggi()
    intestazione("Budget", None, "Mese per mese: quanto hai speso di ogni budget e quanto è previsto a fine mese")
    mesi = _mesi_scelta(c, oggi)
    if st.session_state.get("bud_mese") not in mesi:
        st.session_state["bud_mese"] = mesi[0]
    a, b = st.columns([1, 3], vertical_alignment="bottom")
    mese = a.selectbox("Mese", mesi, key="bud_mese", format_func=lambda m: grafici.mese_lungo(m).capitalize())
    tutti = bd.elenco(c)
    if not tutti:
        _vuota(c, mese)
        return
    r = bd.analizza(c, mese, oggi)
    if not r["budget"]:
        b.caption("Nessun budget attivo in questo mese: i budget salvati partono più avanti.")
        _aggiungi(c, mese, aperto=False)
        _gestisci(c, tutti, mese)
        return
    corrente = mese == bd.mese_di(oggi)
    b.caption("Previsione a fine mese: speso fino al giorno in cui arrivano i dati di ogni conto, più la mediana "
              "della spesa negli stessi giorni degli ultimi %d mesi." % bd.MESI_STORIA if corrente or
              any(x["previsione"] for x in r["budget"]) else "Mese chiuso: lo speso a confronto con il budget.")
    _coperture(r)
    _indicatori(r, mese, oggi)
    scelto = _barre(r)
    _note(r, mese)
    _dettaglio(c, r, scelto, mese)
    _storico(r)
    _aggiungi(c, mese, aperto=False)
    _gestisci(c, tutti, mese)


# ---------------------------------------------------------------- pagina vuota

def _vuota(c, mese):
    with st.container(key="scheda_budget_vuota", border=True):
        st.markdown("#### Comincia dal tuo primo budget")
        st.markdown("Scegli una categoria di spesa (o una sua sottocategoria), il perimetro dei conti e l'importo "
                    "del mese. Ogni mese vedrai quanto ne hai già speso e quanto è previsto a fine mese, con un avviso "
                    "quando lo superi o stai per superarlo. Non serve metterne uno per ogni categoria.")
    _aggiungi(c, mese, aperto=True)


def _aggiungi(c, mese, aperto):
    categorie = bd.categorie_di_spesa()
    with st.expander("Aggiungi un budget", expanded=aperto, icon=":material/add:"):
        with st.container(key="scheda_aggiungi_budget"):
            a, b = st.columns(2)
            l1 = a.selectbox("Categoria", list(categorie), key="bud_n_l1", format_func=bd.nome_voce)
            l2 = b.selectbox("Sottocategoria", [None] + categorie.get(l1, []), key="bud_n_l2",
                             format_func=lambda x: "Tutta la categoria" if x is None else x)
            a, b, d = st.columns([2, 1, 1])
            perimetro = a.segmented_control("Perimetro", list(bd.PERIMETRI_BUDGET), key="bud_n_perimetro",
                                            format_func=bd.PERIMETRI_BUDGET.get, default="tutti",
                                            help="I conti su cui si conta la spesa: Tutti somma i conti personali e "
                                                 "il conto comune (i versamenti dall'uno all'altro si annullano).")
            importo = b.number_input("Importo al mese (€)", min_value=1, step=10, value=None, key="bud_n_importo",
                                     placeholder="es. 400")
            dal = d.selectbox("Dal mese", _mesi_avanti(mese), key="bud_n_dal",
                              format_func=lambda m: grafici.mese_lungo(m).capitalize())
            if st.button("Aggiungi il budget", icon=":material/add:", key="bud_n_salva", type="primary"):
                if not importo:
                    st.error("Indica l'importo del mese.")
                    return
                try:
                    servizi.aggiungi_budget(c, (l1, l2), perimetro or "tutti", int(round(importo * 100)), dal)
                except ValueError as e:
                    st.error(str(e).capitalize() + ".")
                    return
                st.session_state.pop("bud_n_importo", None)
                st.toast("Budget aggiunto: %s" % bd.nome_voce(l1, l2))
                st.rerun()


def _mesi_avanti(mese):
    """Mesi da cui può partire un budget: da un anno prima del mese mostrato a tre mesi dopo; si propone il mese
    mostrato."""
    mesi = [bd.sposta_mese(mese, k) for k in range(-12, 4)]
    return [mese] + [m for m in mesi if m != mese]


# ---------------------------------------------------------------- stato del mese

def _coperture(r):
    """Fin dove arrivano i dati di ogni conto: dopo quel giorno la spesa del conto si stima."""
    cop = r["coperture"]
    if not cop:
        return
    pezzi = []
    for x in cop:
        g = "%s/%s" % (x["giorno"][8:10], x["giorno"][5:7]) if x["giorno"] else "—"
        titolo = "%s: %s" % (x["fonte_testo"].capitalize(), g if x["giorno"] else "nessun import")
        nome = x["nome"].replace("<", "&lt;")
        classe = "bud-cop-avviso" if x["indietro"] else "bud-cop"
        pezzi.append('<span class="%s" title="%s">%s%s %s</span>' % (classe, titolo.replace('"', "&quot;"),
                                                                      "⚠ " if x["indietro"] else "", nome, g))
    st.html('<div class="bud-coperture" style="font-size:0.9rem;color:#4b5563;margin:2px 0 6px">'
            '<b>Dati fino a:</b> %s</div>' % " · ".join(pezzi))
    indietro = [x for x in cop if x["indietro"]]
    if indietro:
        nomi = ", ".join("%s (dal %s/%s)" % (x["nome"], x["giorno"][8:10], x["giorno"][5:7]) for x in indietro)
        st.warning("Per %s la previsione parte da giorni fa: importa un estratto più recente per renderla più "
                   "precisa." % nomi, icon=":material/update:")


def _indicatori(r, mese, oggi):
    n = {s: sum(1 for x in r["budget"] if x["stato"] == s) for s in (bd.SUPERATO, bd.A_RISCHIO, bd.IN_LINEA)}
    k = st.columns(4)
    k[0].metric("Superati", n[bd.SUPERATO])
    k[1].metric("A rischio", n[bd.A_RISCHIO], help="Sotto il budget, ma la previsione a fine mese lo supera.")
    k[2].metric("In linea", n[bd.IN_LINEA])
    if mese == bd.mese_di(oggi):
        resto = (bd.ultimo_giorno(mese) - oggi).days
        k[3].metric("Giorni alla fine del mese", resto)
    else:
        k[3].metric("Mese", "chiuso" if mese < bd.mese_di(oggi) else "futuro")


def _info(x):
    """Riquadro al passaggio del mouse su una barra."""
    righe = ["<b>%s</b> · %s" % (x["nome"], bd.PERIMETRI_BUDGET[x["perimetro"]]),
             "Budget: <b>%s</b>" % euro(x["importo"]),
             "Speso: <b>%s</b> (%d%%)" % (euro(x["speso"]), x["saturazione"] or 0)]
    if x["previsione"]:
        righe.append("Previsto a fine mese: <b>%s</b> (%d%%)" % (euro(x["previsto"]), x["saturazione_prevista"] or 0))
    if x["stato"] == bd.SUPERATO:
        righe.append('<span style="color:%s">Superato di %s</span>' % (grafici.ROSSO_TESTO, euro(x["sopra"])))
    elif x["stato"] == bd.A_RISCHIO:
        righe.append('<span style="color:%s">Previsto oltre il budget di %s</span>' % (grafici.AMBRA_TESTO,
                                                                                         euro(x["sopra"])))
    return "<br>".join(righe)


def _barre(r):
    """Una scheda per perimetro con le barre di saturazione, i budget delle sottocategorie sotto la loro
    categoria, i piu' saturi in alto. Restituisce il budget scelto (clic o primo della lista)."""
    scelto = st.session_state.get("bud_scelto")
    chiavi = [(x["l1"], x["l2"], x["perimetro"]) for x in r["budget"]]
    if scelto not in chiavi:
        # all'apertura il budget messo peggio: superato, poi a rischio, poi il piu' saturo
        peso = {bd.SUPERATO: 2, bd.A_RISCHIO: 1, bd.IN_LINEA: 0}
        primo = max(r["budget"], key=lambda x: (peso[x["stato"]], x["saturazione_prevista"] or x["saturazione"] or 0))
        scelto = (primo["l1"], primo["l2"], primo["perimetro"])
        st.session_state["bud_scelto"] = scelto
    for p in r["perimetri"]:
        del_perimetro = [x for x in r["budget"] if x["perimetro"] == p]
        if not del_perimetro:
            continue
        ordinati = _ordina(del_perimetro)
        with st.container(key="scheda_budget_%s" % p, border=True):
            titolo = "Budget del mese"
            if len(r["perimetri"]) > 1 or p != "tutti":
                titolo += " · %s" % bd.PERIMETRI_BUDGET[p]
            st.markdown("#### %s" % titolo)
            primo = [x for x in del_perimetro if not x["l2"] or not any(
                y["l1"] == x["l1"] and not y["l2"] for y in del_perimetro)]
            imp, sp = sum(x["importo"] for x in primo), sum(x["speso"] for x in primo)
            prev = sum(x["previsto"] for x in primo)
            testo = "Speso %s su %s di budget" % (euro(sp), euro(imp))
            if any(x["previsione"] for x in primo):
                testo += " · previsto a fine mese %s" % euro(prev)
            st.caption(testo + ". Barra piena: speso; tratteggiata: previsto fino a fine mese. Clic su un budget "
                               "per il dettaglio.")
            righe = [{"nome": x["nome"], "sotto": bool(x["l2"]) and any(
                y["l1"] == x["l1"] and not y["l2"] for y in del_perimetro), "stato": x["stato"],
                "saturazione": x["saturazione"], "saturazione_prevista": x["saturazione_prevista"],
                "info": _info(x)} for x in ordinati]
            i = grafici.mostra(grafici.barre_budget(righe), "bud_barre_%s" % p, 40 * len(righe) + 50, clic="y")
        if i is not None:
            # l'asse del grafico parte dal basso
            j = len(ordinati) - 1 - i
            if 0 <= j < len(ordinati):
                x = ordinati[j]
                st.session_state["bud_scelto"] = (x["l1"], x["l2"], x["perimetro"])
                st.rerun()
    return st.session_state["bud_scelto"]


def _ordina(voci):
    """Categorie dalla piu' satura; i budget di sottocategoria subito sotto la loro categoria (se ha un budget),
    altrimenti tra le categorie."""
    sat = lambda x: max(x["saturazione"] or 0, x["saturazione_prevista"] or 0)
    teste = [x for x in voci if not x["l2"]]
    figli = [x for x in voci if x["l2"]]
    con_testa = {x["l1"] for x in teste}
    radici = sorted(teste + [x for x in figli if x["l1"] not in con_testa], key=sat, reverse=True)
    out = []
    for x in radici:
        out.append(x)
        if not x["l2"]:
            out += sorted([y for y in figli if y["l1"] == x["l1"]], key=sat, reverse=True)
    return out


def _note(r, mese):
    for a in r["avvisi"]:
        st.info(a, icon=":material/info:")
    for x in r["budget"]:
        if x["categoria_mancante"]:
            st.warning("%s non c'è più nella tassonomia: il budget non conta più nulla. Eliminalo in \"Gestisci i "
                       "budget\"." % x["nome"], icon=":material/warning:")
    dc = [(p, v) for p, v in r["da_classificare"].items() if v[0] > 0]
    if dc:
        testo = " · ".join("%s%s (%d movimenti)" % (("%s: " % bd.PERIMETRI_BUDGET[p]) if len(dc) > 1 else "",
                                                    euro(v[0]), v[1]) for p, v in dc)
        pagina = (st.session_state.get("_pagine") or {}).get("revisione")
        a, b = st.columns([4, 1], vertical_alignment="center")
        a.caption("Spese di %s ancora da classificare, che non contano in nessun budget: %s."
                  % (grafici.mese_lungo(mese), testo))
        if pagina is not None:
            with b:
                st.page_link(pagina, label="Revisione", icon=":material/rule:")
    na = r["rimborsi_non_attribuiti"]
    if na:
        titolo = "1 rimborso non attribuito a una spesa" if len(na) == 1 else \
            "%d rimborsi non attribuiti a una spesa" % len(na)
        with st.expander(titolo, icon=":material/undo:"):
            st.caption("Rimborsi di un esercente di cui non si trova la spesa (stesso esercente e categoria, entro "
                       "%d giorni): non contano in nessun budget." % bd.GIORNI_RIMBORSO)
            nomi = {x["id"]: x["nome"] for x in conn().execute("SELECT id, nome FROM conti")}
            st.dataframe(pd.DataFrame({
                "Data": ["%s/%s/%s" % (x["data"][8:10], x["data"][5:7], x["data"][:4]) for x in na],
                "Esercente": [x["nome"] or "" for x in na],
                "Categoria": [bd.nome_voce(x["l1"], x["l2"]) for x in na],
                "Importo": [x["importo"] / 100 for x in na],
                "Conto": [nomi.get(x["conto_id"], "") for x in na]}), hide_index=True, width="stretch",
                column_config={"Importo": st.column_config.NumberColumn(format="euro")})


# ---------------------------------------------------------------- dettaglio del budget scelto

def _dettaglio(c, r, scelto, mese):
    x = next((y for y in r["budget"] if (y["l1"], y["l2"], y["perimetro"]) == scelto), None)
    if x is None:
        return
    cal = x["calcolo"]
    with st.container(key="scheda_budget_dettaglio", border=True):
        a, b = st.columns([3, 1], vertical_alignment="bottom")
        a.markdown("#### %s · %s" % (x["nome"], grafici.mese_lungo(mese)))
        a.caption("Perimetro: %s" % bd.PERIMETRI_BUDGET[x["perimetro"]])
        if b.button("Vedi i movimenti", icon=":material/bar_chart:", key="bud_vai",
                    help="Apre \"Dove vanno i soldi\" su questa voce, questo mese e questo perimetro"):
            _vai_alla_ripartizione(c, x, mese)
        k = st.columns(4)
        k[0].metric("Budget", euro(x["importo"]))
        k[1].metric("Speso", euro(x["speso"]), delta="%d%% del budget" % (x["saturazione"] or 0), delta_color="off",
                    delta_arrow="off")
        if x["previsione"]:
            k[2].metric("Previsto a fine mese", euro(x["previsto"]),
                        delta="%d%% del budget" % (x["saturazione_prevista"] or 0), delta_color="off",
                        delta_arrow="off")
        else:
            k[2].metric("Previsto a fine mese", "—", help="Il mese è coperto dai dati di tutti i conti.")
        margine = x["importo"] - (x["previsto"] if x["previsione"] else x["speso"])
        if x["previsione"]:
            etichetta = "Margine previsto" if margine >= 0 else "Previsto oltre il budget"
        else:
            etichetta = "Margine" if margine >= 0 else "Oltre il budget"
        k[3].metric(etichetta, euro(abs(margine)))
        if x["stato"] == bd.SUPERATO:
            st.error("Budget superato di %s." % euro(x["sopra"]), icon=ICONE_STATO[bd.SUPERATO])
        elif x["stato"] == bd.A_RISCHIO:
            st.warning("A rischio: la previsione a fine mese supera il budget di %s." % euro(x["sopra"]),
                       icon=ICONE_STATO[bd.A_RISCHIO])
        nomi = {y["id"]: y["nome"] for y in c.execute("SELECT id, nome FROM conti")}
        segni = []
        for cid, d in sorted(cal["conti"].items(), key=lambda kv: kv[1]["giorno"]):
            if d["giorno"] < cal["giorni"] and d["giorno"] > 0:
                segni.append((d["giorno"], nomi.get(cid, "")))
        grafici.mostra(grafici.cumulato_budget(cal["giorni"], cal["reale"], cal["stimato"], cal["tipico"],
                                               x["importo"], segni), "bud_cumulato", 300)
        if cal["previsione"] and cal["conti"]:
            st.caption("Conto per conto: speso fino al giorno dei dati, più la mediana della spesa dei giorni che "
                       "restano negli ultimi mesi interi. Una spesa fatta prima del solito si conta due volte "
                       "(è già nello speso ed è ancora nella mediana).")
            st.dataframe(pd.DataFrame({
                "Conto": [nomi.get(cid, "") for cid in cal["conti"]],
                "Dati fino al giorno": [d["giorno"] if d["giorno"] < cal["giorni"] else "fine mese"
                                        for d in cal["conti"].values()],
                "Speso": [d["speso"] / 100 for d in cal["conti"].values()],
                "Previsto per il resto": [d["resto"] / 100 for d in cal["conti"].values()],
                "Mesi usati": [d["mesi"] for d in cal["conti"].values()]}), hide_index=True, width="stretch",
                column_config={"Speso": st.column_config.NumberColumn(format="euro"),
                               "Previsto per il resto": st.column_config.NumberColumn(format="euro"),
                               "Dati fino al giorno": st.column_config.TextColumn()})


def _vai_alla_ripartizione(c, x, mese):
    """Apre "Dove vanno i soldi" sulla voce del budget, nel suo perimetro, con il mese filtrato. Il periodo dei
    filtri diventa l'anno che finisce con quel mese: il grafico della spesa per mese mostra la linea del budget."""
    dmin, dmax = c.execute("SELECT min(data), max(data) FROM analisi").fetchone()
    da, a = bd.primo_giorno(bd.sposta_mese(mese, -(bd.MESI_STORIA - 1))), bd.ultimo_giorno(mese)
    if dmax and a > date.fromisoformat(dmax):
        a = date.fromisoformat(dmax)
    if dmin and da < date.fromisoformat(dmin):
        da = date.fromisoformat(dmin)
    for k, v in (("f_perimetro", x["perimetro"]), ("f_periodo", (da, a))):
        st.session_state[k] = v
        st.session_state["_tieni_" + k] = v
    st.session_state.pop("f_conti", None)
    st.session_state.pop("_tieni_f_conti", None)
    st.session_state["rip_l1"], st.session_state["rip_l2"], st.session_state["rip_mese"] = x["l1"], x["l2"], mese
    pagina = (st.session_state.get("_pagine") or {}).get("ripartizione")
    if pagina is not None:
        st.switch_page(pagina)


# ---------------------------------------------------------------- storico

def _cella(imp, speso, previsto):
    if imp is None:
        return "—"
    t = "%d%%" % round(100 * speso / imp)
    if previsto is not None:
        t += " → %d%%" % round(100 * previsto / imp)
    return t


def _colore_cella(imp, speso, previsto):
    if imp is None:
        return "color: #9ca3af"
    if speso > imp:
        return "color: %s; font-weight: 600" % grafici.ROSSO_TESTO
    if previsto is not None and previsto > imp:
        return "color: %s; font-weight: 600" % grafici.AMBRA_TESTO
    return ""


def _storico(r):
    with st.expander("Storico degli ultimi %d mesi" % bd.MESI_STORIA, icon=":material/history:"):
        st.caption("Quanto di ogni budget è stato speso mese per mese, dal più recente. In rosso i mesi oltre il "
                   "budget, in ambra il mese in corso se la previsione lo supera. I rimborsi contano nel mese della spesa che restituiscono.")
        # il mese piu' recente a sinistra: resta in vista anche quando la tabella scorre
        mesi = [m for m, _, _, _ in r["budget"][0]["storico"]][::-1]
        colonne = [grafici.etichetta_mese(m) for m in mesi]
        dati, stili = [], []
        for x in _ordina(r["budget"]):
            nome = "%s · %s" % (x["nome"], bd.PERIMETRI_BUDGET[x["perimetro"]])
            dati.append([nome] + [_cella(i, s, p) for _, i, s, p in x["storico"][::-1]])
            stili.append([""] + [_colore_cella(i, s, p) for _, i, s, p in x["storico"][::-1]])
        df = pd.DataFrame(dati, columns=["Budget"] + colonne)
        stile = pd.DataFrame(stili, columns=df.columns)
        st.dataframe(df.style.apply(lambda _: stile, axis=None), hide_index=True, width="stretch")


# ---------------------------------------------------------------- gestione

def _gestisci(c, tutti, mese):
    with st.expander("Gestisci i budget", icon=":material/edit:"):
        with st.container(key="scheda_gestisci_budget"):
            chiavi = [(b["l1"], b["l2"], b["perimetro"]) for b in tutti]
            per = {k: b for k, b in zip(chiavi, tutti)}
            k = st.selectbox("Budget", chiavi, key="bud_g_scelto",
                             format_func=lambda k: "%s · %s" % (bd.nome_voce(k[0], k[1]), bd.PERIMETRI_BUDGET[k[2]]))
            b = per[k]
            st.dataframe(pd.DataFrame({"Dal mese": [grafici.mese_lungo(m).capitalize() for m, _ in b["importi"]],
                                       "Importo al mese": [v / 100 for _, v in b["importi"]]}), hide_index=True,
                         column_config={"Importo al mese": st.column_config.NumberColumn(format="euro")})
            st.markdown("**Cambia l'importo**")
            x, y, z = st.columns([1, 1, 1], vertical_alignment="bottom")
            nuovo = x.number_input("Nuovo importo al mese (€)", min_value=1, step=10, value=None, key="bud_g_importo")
            dal = y.selectbox("Dal mese", _mesi_avanti(mese), key="bud_g_dal",
                              format_func=lambda m: grafici.mese_lungo(m).capitalize(),
                              help="I mesi prima restano valutati con l'importo di allora.")
            if z.button("Cambia l'importo", key="bud_g_cambia", icon=":material/save:"):
                if not nuovo:
                    st.error("Indica il nuovo importo.")
                else:
                    try:
                        servizi.cambia_importo_budget(c, k[:2], k[2], int(round(nuovo * 100)), dal)
                    except ValueError as e:
                        st.error(str(e).capitalize() + ".")
                    else:
                        st.session_state.pop("bud_g_importo", None)
                        st.toast("Importo cambiato da %s" % grafici.mese_lungo(dal))
                        st.rerun()
            if len(b["importi"]) > 1:
                x, y = st.columns([2, 1], vertical_alignment="bottom")
                cambio = x.selectbox("Togli un cambio d'importo", [m for m, _ in b["importi"][1:]], key="bud_g_togli_mese",
                                     format_func=lambda m: grafici.mese_lungo(m).capitalize())
                if y.button("Togli il cambio", key="bud_g_togli", icon=":material/undo:"):
                    servizi.togli_cambio_budget(c, k[:2], k[2], cambio)
                    st.toast("Da %s vale di nuovo l'importo precedente" % grafici.mese_lungo(cambio))
                    st.rerun()
            st.markdown("**Elimina**")
            x, y = st.columns([2, 1], vertical_alignment="bottom")
            conferma = x.checkbox("Elimina anche lo storico di questo budget", key="bud_g_conferma")
            if y.button("Elimina il budget", key="bud_g_elimina", icon=":material/delete:", disabled=False):
                if not conferma:
                    st.error("Spunta la conferma per eliminare il budget.")
                else:
                    servizi.elimina_budget(c, k[:2], k[2])
                    st.session_state.pop("bud_g_scelto", None)
                    st.session_state.pop("bud_g_conferma", None)
                    st.toast("Budget eliminato")
                    st.rerun()
