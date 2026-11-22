"""0.21.1: nel grafico cumulato del budget i conti che finiscono lo stesso giorno hanno un solo segno."""
from test_novita_0210 import _apri


def _segni():
    import streamlit as st
    from spese.ui import grafici, pagine_budget
    cal = {"giorni": 30, "conti": {1: {"giorno": 20}, 2: {"giorno": 20}, 3: {"giorno": 20}, 4: {"giorno": 14},
                                   5: {"giorno": 30}, 6: {"giorno": 0}}}
    nomi = {1: "Credem comune", 2: "Carta ING", 3: "Credem personale", 4: "Fineco", 5: "Coperto", 6: "Fermo"}
    segni = pagine_budget.segni_copertura(cal, nomi)
    st.session_state["prova_segni"] = segni
    o = grafici.cumulato_budget(30, [0] * 30, [0] * 30, None, 10000, segni)
    linee = next(s for s in o["series"] if s["name"] == "Speso")["markLine"]["data"]
    st.session_state["prova_linee"] = [(x["xAxis"], x["label"]["formatter"], x["label"]["distance"]) for x in linee]
    st.session_state["prova_riquadro"] = str(o["tooltip"]["formatter"])


def test_un_segno_per_giorno_con_i_conti_nel_riquadro(conn):
    s = _apri(_segni).session_state
    # coperto per tutto il mese o fermo a prima del mese: nessun segno
    assert s["prova_segni"] == [(14, ["Fineco"]), (20, ["Carta ING", "Credem comune", "Credem personale"])]
    assert s["prova_linee"] == [("14", "dati fino al 14 · Fineco", 4), ("20", "dati fino al 20 · 3 conti", 18)]
    assert "Carta ING" in s["prova_riquadro"] and "Dati fino a qui" in s["prova_riquadro"]
