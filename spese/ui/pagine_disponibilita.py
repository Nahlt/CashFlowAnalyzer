"""Analisi disponibilita': saldo di un conto alla volta, a un giorno o lungo un periodo, con i movimenti."""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from .. import analisi as an
from .. import disponibilita as dsp
from ..numeri import euro
from . import grafici
from .comune import NATURE, _ricorda, _tieni, conn, intestazione, testo

MODI = {"giorno": "Giorno", "periodo": "Periodo"}
GIORNI_CONTESTO = 30          # in modalita' giorno: grafico e confronto sui 30 giorni che finiscono col giorno scelto


def _con_segno(cent):
    return ("+" if cent > 0 else "") + euro(cent)


def _intervallo_valido(v, d0, d1):
    return isinstance(v, (list, tuple)) and len(v) == 2 and all(d0 <= x <= d1 for x in v)


def saldo_conti():
    """Un conto alla volta. Modalita' giorno: saldo a fine giornata, movimenti del giorno, confronto con 30 giorni
    prima; grafico dei 30 giorni che finiscono col giorno scelto. Modalita' periodo: saldo a inizio e fine periodo,
    saldo minimo; grafico del saldo e delle uscite giorno per giorno. In basso i movimenti con il saldo dopo
    ciascuno. I filtri della barra laterale non valgono qui: si guarda un conto per volta."""
    c = conn()
    elenco = dsp.conti_con_saldo(c)
    intestazione("Saldo dei conti", None, "Saldo a fine giornata, calcolato dai saldi noti e dai movimenti importati")
    if not elenco:
        st.info("Nessun conto importato di cui calcolare il saldo. Importa un estratto conto dalla pagina **Importa**.",
                icon=":material/upload_file:")
        return
    nomi = {r["id"]: r["nome"] for r in elenco}
    for k in ("disp_conto", "disp_modo", "disp_giorno", "disp_periodo"):
        _ricorda(k)
    if st.session_state.get("disp_conto") not in nomi:
        st.session_state["disp_conto"] = next(iter(nomi))
    if st.session_state.get("disp_modo") not in MODI:
        st.session_state["disp_modo"] = "periodo"
    a, b, d = st.columns([2, 1, 2], vertical_alignment="bottom")
    conto = a.selectbox("Conto", list(nomi), format_func=nomi.get, key="disp_conto")
    b.segmented_control("Vista", list(MODI), format_func=MODI.get, key="disp_modo")
    modo = st.session_state.get("disp_modo") or "periodo"
    s = dsp.Saldi(c, conto)
    oggi = date.today()
    primo, ultimo = dsp.primo_movimento(c, conto), dsp.ultimo_movimento(c, conto)
    inizi = [x for x in (primo, s.ancore[0].giorno if s.ancore else None) if x]
    d0 = min(date.fromisoformat(min(inizi)), oggi) if inizi else oggi
    if modo == "giorno":
        v = st.session_state.get("disp_giorno")
        if not isinstance(v, date) or not d0 <= v <= oggi:
            st.session_state["disp_giorno"] = oggi
        giorno = d.date_input("Giorno", min_value=d0, max_value=oggi, format="DD/MM/YYYY", key="disp_giorno")
        da = a_ = giorno.isoformat()
    else:
        if not _intervallo_valido(st.session_state.get("disp_periodo"), d0, oggi):
            # il periodo scelto in Impostazioni, ristretto ai giorni di cui si conosce il saldo (fino a oggi)
            voce, mese_in_corso = an.leggi_periodo_predefinito(c)
            p0, p1, _ = an.periodo_predefinito(voce, mese_in_corso, oggi, d0, oggi)
            st.session_state["disp_periodo"] = (p0, p1)
        sel = d.date_input("Periodo", min_value=d0, max_value=oggi, format="DD/MM/YYYY", key="disp_periodo")
        if not (isinstance(sel, (list, tuple)) and len(sel) == 2):
            st.caption("Scegli anche la data di fine del periodo.")
            return
        da, a_ = sel[0].isoformat(), sel[1].isoformat()
    for k in ("disp_conto", "disp_modo", "disp_giorno", "disp_periodo"):
        _tieni(k)

    if not s.calcolabile:
        st.info("Per **%s** non c'e' ancora nessun saldo noto. Inserisci il saldo di un giorno in **Conti e "
                "copertura**: da li' si calcola il saldo di ogni altro giorno." % nomi[conto], icon=":material/info:")
        pagina = (st.session_state.get("_pagine") or {}).get("conti")
        if pagina is not None:
            st.page_link(pagina, label="Imposta il saldo in Conti e copertura", icon=":material/account_balance:")
        return
    _avvisi(s, ultimo, da, a_)
    if modo == "giorno":
        _vista_giorno(s, da, d0.isoformat())
    else:
        _vista_periodo(s, da, a_)
    _movimenti(c, s, conto, da, a_, modo)


def _avvisi(s, ultimo, da, a):
    if ultimo is None:
        st.caption("Questo conto non ha movimenti importati: il saldo resta quello inserito.")
    elif a > ultimo:
        st.caption("Movimenti importati fino al %s: dopo quella data il saldo non tiene conto di movimenti non ancora "
                   "importati." % dsp.data_it(ultimo))
    for p, q, scarto in s.scarti():
        if scarto and p.giorno <= a and q.giorno >= _prima(da):
            st.warning("Tra il saldo del %s (%s) e quello del %s (%s) c'e' uno scarto di %s: in quel tratto manca o "
                       "avanza qualcosa (un estratto non importato, un movimento non contabilizzato) e il grafico fa "
                       "un salto. I saldi noti sono in **Conti e copertura**."
                       % (dsp.data_it(p.giorno), p.fonte.lower(), dsp.data_it(q.giorno), q.fonte.lower(),
                          _con_segno(scarto)), icon=":material/warning:")


def _prima(giorno, giorni=1):
    return (date.fromisoformat(giorno) - timedelta(days=giorni)).isoformat()


def _vista_giorno(s, giorno, inizio_dati):
    """inizio_dati: primo giorno con un dato del conto (movimento o saldo noto). Prima il saldo sarebbe solo
    un'estrapolazione: grafico e confronto non ci vanno."""
    saldo = s.saldo(giorno)
    entrate, uscite, n = s.per_giorno.get(giorno, (0, 0, 0))
    giorno_prima = _prima(giorno, GIORNI_CONTESTO)
    prima = s.saldo(giorno_prima) if giorno_prima >= inizio_dati else None
    c1, c2, c3 = st.columns(3)
    c1.metric("Saldo a fine giornata", euro(saldo), help="Saldo contabile dopo i movimenti contabilizzati il %s."
              % dsp.data_it(giorno))
    c2.metric("Movimenti del giorno", _con_segno(entrate - uscite),
              "%d moviment%s" % (n, "o" if n == 1 else "i"), delta_color="off", delta_arrow="off",
              help="Entrate %s, uscite %s." % (euro(entrate), euro(uscite)))
    if prima is None:
        c3.metric("Rispetto a %d giorni prima" % GIORNI_CONTESTO, "—", "dati dal %s" % dsp.data_it(inizio_dati),
                  delta_color="off", delta_arrow="off")
    else:
        c3.metric("Rispetto a %d giorni prima" % GIORNI_CONTESTO, _con_segno(saldo - prima),
                  "saldo del %s: %s" % (dsp.data_it(giorno_prima), euro(prima)), delta_color="off", delta_arrow="off")
    with st.container(key="scheda_disp_grafico"):
        st.subheader("Saldo nei %d giorni fino al %s" % (GIORNI_CONTESTO, dsp.data_it(giorno)))
        df = s.serie(max(_prima(giorno, GIORNI_CONTESTO - 1), inizio_dati), giorno)
        grafici.mostra(grafici.saldo_e_uscite(df, giorno), "disp_grafico_giorno", 340)


def _vista_periodo(s, da, a):
    inizio, fine = s.saldo_iniziale(da), s.saldo(a)
    df = s.serie(da, a)
    i_min = int(df["saldo"].astype("int64").idxmin())
    c1, c2, c3 = st.columns(3)
    c1.metric("Saldo a inizio periodo", euro(inizio), help="Prima dei movimenti del %s." % dsp.data_it(da))
    c2.metric("Saldo a fine periodo", euro(fine), _con_segno(fine - inizio),
              help="Dopo i movimenti del %s. Sotto, la variazione nel periodo." % dsp.data_it(a))
    c3.metric("Saldo minimo", euro(int(df["saldo"].iloc[i_min])), "il %s" % dsp.data_it(df["giorno"].iloc[i_min]),
              delta_color="off", delta_arrow="off", help="Il saldo a fine giornata piu' basso del periodo.")
    with st.container(key="scheda_disp_grafico"):
        st.subheader("Saldo e uscite giorno per giorno")
        st.caption("Uscite: tutto cio' che fa scendere il saldo (spese, addebito della carta, bonifici verso altri "
                   "conti, acquisti di titoli), con commissioni e imposte. Rotellina del mouse per ingrandire.")
        grafici.mostra(grafici.saldo_e_uscite(df), "disp_grafico_periodo", 360)


def _movimenti(c, s, conto, da, a, modo):
    df = dsp.movimenti_con_saldo(c, conto, da, a, s)
    st.subheader("Movimenti del giorno" if modo == "giorno" else "Movimenti del periodo")
    if df.empty:
        st.caption("Nessun movimento contabilizzato in questo %s." % ("giorno" if modo == "giorno" else "periodo"))
        return
    st.caption("%s, dal piu' recente, alla data contabile. Il saldo a fine giornata e' esatto; nello stesso "
               "giorno l'ordine e' quello dell'estratto importato. Per cambiare la classificazione usa la pagina "
               "**Movimenti**." % ("1 movimento" if len(df) == 1 else "%d movimenti" % len(df)))
    vista = pd.DataFrame({
        "Contabilizzato": [dsp.data_it(x) for x in df["data_contabile"]],
        "Operazione": [dsp.data_it(x) for x in df["data"]],
        "Ora": [testo(x) for x in df["ora"]],
        "Esercente o controparte": [testo(x) for x in df["esercente"]],
        "Importo": [euro(int(v)) for v in df["importo_cent"]],
        "Saldo dopo": [euro(None if grafici.pd_nullo(v) else int(v)) for v in df["saldo_dopo"]],
        "Conta come": [NATURE.get(testo(n), testo(n)) for n in df["natura"]],
        "Categoria": [testo(x) for x in df["l1"]],
        "Sottocategoria": [testo(x) for x in df["l2"]],
        "Descrizione": [testo(x) for x in df["descrizione"]],
    })
    st.dataframe(vista, hide_index=True, width="stretch", height=min(520, 38 + 35 * len(vista)), key="disp_movimenti")
