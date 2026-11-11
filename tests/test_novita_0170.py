"""Novita' della 0.17.0 nella pagina "Dove vanno i soldi": "Da classificare" in rosso, mese scelto su tutta la
pagina, esercenti principali come grafico che filtra i movimenti, tabella dei movimenti uguale a quella della
pagina Movimenti. Tutti i dati sono sintetici (fixture `caricato`)."""
import json

import pytest

from spese import analisi as an

TUTTI = an.Filtri("tutti")


def _vuota():
    from spese.ui import grafici
    assert grafici.mostra


@pytest.fixture(scope="module")
def grafici():
    """Il componente dei grafici (streamlit-echarts) si registra solo con Streamlit avviato: fuori da un'AppTest
    l'import di spese.ui.grafici fallisce. Importato una volta dentro un'AppTest, resta disponibile."""
    from streamlit.testing.v1 import AppTest
    AppTest.from_function(_vuota).run()
    from spese.ui import grafici as g
    return g


# --- dati --------------------------------------------------------------------------------------
def test_movimenti_solo_uscite_come_il_grafico(caricato):
    """Difetto della 0.16.2: l'elenco dei movimenti di una categoria prendeva tutte le righe con quella categoria.
    Sotto "Da classificare" comparivano anche giroconti e addebiti riepilogativi della carta, che il grafico non
    conta. Con solo_uscite l'elenco somma esattamente quanto la barra."""
    c, _, _ = caricato
    rip = an.ripartizione(c, TUTTI, "l1")
    assert "Da classificare" in list(rip["voce"])
    for voce, importo, n in zip(rip["voce"], rip["importo"], rip["movimenti"]):
        mv = an.movimenti(c, TUTTI, voce, solo_uscite=True)
        assert len(mv) == n, voce
        assert -int(mv["importo_cent"].sum()) == importo, voce
        assert set(mv["natura"]) <= {"uscita", "rimborso"}
    # senza il filtro (come nella 0.16.2) "Da classificare" ha righe in piu' che non sono uscite
    tutte = an.movimenti(c, TUTTI, "Da classificare")
    assert len(tutte) > int(rip.loc[rip["voce"] == "Da classificare", "movimenti"].iloc[0])
    assert {"trasferimento_interno", "contenitore"} & set(tutte["natura"])


def test_filtro_esercente_come_il_grafico(caricato):
    """Ogni barra del grafico degli esercenti filtra i movimenti che la compongono, ne' piu' ne' meno."""
    c, _, _ = caricato
    for voce in an.ripartizione(c, TUTTI, "l1")["voce"]:
        es = an.esercenti_categoria(c, TUTTI, voce)
        assert not es.empty
        for nome, importo, n in zip(es["esercente"], es["importo"], es["movimenti"]):
            mv = an.movimenti(c, TUTTI, voce, solo_uscite=True, esercente=nome)
            assert len(mv) == n, (voce, nome)
            assert -int(mv["importo_cent"].sum()) == importo, (voce, nome)


def test_filtro_esercente_e_mese(caricato):
    c, _, _ = caricato
    mesi = list(an.flussi_mensili(c, TUTTI)["mese"])
    for mese in mesi:
        fm = an.con_mese(TUTTI, mese)
        for voce in an.ripartizione(c, fm, "l1")["voce"]:
            for nome in an.esercenti_categoria(c, fm, voce)["esercente"]:
                mv = an.movimenti(c, fm, voce, solo_uscite=True, esercente=nome)
                assert not mv.empty and all(d[:7] == mese for d in mv["data"])


# --- grafici ------------------------------------------------------------------------------------
def _colori(opz, ordine):
    return dict(zip(ordine, [d["itemStyle"]["color"] for d in opz["series"][0]["data"]]))


def test_da_classificare_in_rosso(grafici):
    voci, valori = ["CASA", "Da classificare", "VIAGGI"], [7000, 5000, 3000]
    col = _colori(*grafici.barre_orizzontali(voci, valori, rosse=("Da classificare",)))
    assert col == {"CASA": grafici.BLU, "Da classificare": grafici.ROSSO, "VIAGGI": grafici.BLU}
    # scelta un'altra categoria: tutte tenui, "Da classificare" in rosso tenue
    col = _colori(*grafici.barre_orizzontali(voci, valori, "CASA", rosse=("Da classificare",)))
    assert col == {"CASA": grafici.BLU, "Da classificare": grafici.ROSSO_TENUE, "VIAGGI": grafici.TENUE}
    col = _colori(*grafici.barre_orizzontali(voci, valori, "Da classificare", rosse=("Da classificare",)))
    assert col["Da classificare"] == grafici.ROSSO and col["CASA"] == grafici.TENUE
    # senza rosse (sottocategorie, etichette, esercenti) nulla cambia
    assert set(_colori(*grafici.barre_orizzontali(voci, valori)).values()) == {grafici.BLU}


def test_spesa_per_mese_suggerisce_il_clic(grafici):
    mesi = ["2026-01", "2026-02", "2026-03"]
    js = str(grafici.serie_mensile(mesi, [100, 200, 300])["tooltip"]["formatter"].js_code)
    assert "Clic per vedere solo questo mese" in js and "=== null" in js
    js = str(grafici.serie_mensile(mesi, [100, 200, 300], "2026-02")["tooltip"]["formatter"].js_code)
    assert json.dumps(grafici.etichetta_mese("2026-02")) in js
    barre = grafici.serie_mensile(mesi, [100, 200, 300], "2026-02")["series"][0]["data"]
    assert [b["itemStyle"]["color"] for b in barre] == [grafici.TENUE, grafici.BLU, grafici.TENUE]


# --- pagina (AppTest) ---------------------------------------------------------------------------
def _pagina():
    """Script dell'AppTest: apre lo stesso database della fixture e disegna la pagina indicata."""
    import os

    import streamlit as st
    from spese import analisi, db, percorsi
    from spese.tassonomia_utente import carica
    from spese.ui import pagine_analisi, pagine_dati
    if "conn" not in st.session_state:
        st.session_state["conn"] = db.apri(percorsi.file_database(), "passphrase di prova")
        carica(st.session_state["conn"])
    st.session_state["filtri_attivi"] = analisi.Filtri("tutti")
    {"ripartizione": pagine_analisi.ripartizione, "movimenti": pagine_dati.movimenti}[os.environ["PAGINA_PROVA"]]()


@pytest.fixture()
def pagina(caricato, monkeypatch):
    from streamlit.testing.v1 import AppTest

    def apri(nome, **stato):
        monkeypatch.setenv("PAGINA_PROVA", nome)
        at = AppTest.from_function(_pagina, default_timeout=60)
        for k, v in stato.items():
            at.session_state[k] = v
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        return at
    return caricato[0], apri


def _metriche(at):
    return [(m.label, m.value) for m in at.metric]


def test_pagina_ordine_delle_sezioni(pagina):
    c, apri = pagina
    at = apri("ripartizione")
    titoli = [s.value for s in at.subheader]
    pos = [next(i for i, t in enumerate(titoli) if t.startswith(x))
           for x in ("Categorie", "Spesa per mese", "Esercenti principali")]
    assert pos == sorted(pos)
    assert any(t.endswith("· sottocategorie") for t in titoli)
    espansi = [e.label for e in at.expander]
    assert espansi[0] == "Tabella delle categorie" and espansi[1].startswith("Movimenti")
    assert not any(e.label.startswith("Esercenti") for e in at.expander)        # ora e' un grafico, non una tabella
    assert [m[0] for m in _metriche(at)][:1] == ["Uscite del periodo"]


def test_pagina_mese_scelto_su_tutta_la_pagina(pagina, grafici):
    c, apri = pagina
    mesi = list(an.flussi_mensili(c, TUTTI)["mese"])
    rip = {m: an.ripartizione(c, an.con_mese(TUTTI, m), "l1") for m in mesi}
    mese = max((m for m in mesi if not rip[m].empty), key=lambda m: len(rip[m]))
    voce = next(v for v in rip[mese]["voce"] if v != "Da classificare")
    at = apri("ripartizione", rip_mese=mese, rip_l1=voce, rip_l2=None)
    met = _metriche(at)
    lungo = grafici.mese_lungo(mese)
    nome = voce.capitalize() if voce.isupper() else voce
    from spese.numeri import euro
    assert met[0] == ("Uscite di %s" % lungo, euro(int(rip[mese]["importo"].sum())))
    nel_mese = int(rip[mese].loc[rip[mese]["voce"] == voce, "importo"].iloc[0])
    assert met[1] == ("%s · %s" % (nome, lungo), euro(nel_mese))
    serie = an.serie_categoria(c, TUTTI, voce)
    media = round(int(serie["importo"].sum()) / len(mesi))
    assert met[2] == ("%s · media mensile del periodo" % nome, euro(media))
    scarto = nel_mese - media
    assert at.metric[2].proto.delta == "%s%s nel mese" % ("+" if scarto > 0 else "", euro(scarto))
    # anche esercenti e movimenti sono del solo mese
    assert any(s.value.startswith("Esercenti principali di %s" % lungo) for s in at.subheader)
    mov = next(e for e in at.expander if e.label.startswith("Movimenti"))
    assert mov.label.startswith("Movimenti di %s" % lungo)
    righe = mov.dataframe[0].value
    assert len(righe) and all(d[:7] == mese for d in righe["Data"])


def test_pagina_movimenti_come_nella_pagina_movimenti(pagina):
    """Stesse colonne della pagina Movimenti; sotto "Da classificare" solo le uscite contate nel grafico."""
    c, apri = pagina
    colonne = list(apri("movimenti").dataframe[0].value.columns)
    at = apri("ripartizione", rip_l1="Da classificare", rip_l2=None)
    mov = next(e for e in at.expander if e.label.startswith("Movimenti"))
    righe = mov.dataframe[0].value
    assert list(righe.columns) == colonne
    rip = an.ripartizione(c, TUTTI, "l1")
    assert len(righe) == int(rip.loc[rip["voce"] == "Da classificare", "movimenti"].iloc[0])
    assert set(righe["Conta come"]) <= {"Uscita", "Rimborso"}


def test_pagina_filtro_esercente(pagina):
    c, apri = pagina
    voce = an.ripartizione(c, TUTTI, "l1")["voce"].iloc[0]
    es = an.esercenti_categoria(c, TUTTI, voce)
    nome, n = es["esercente"].iloc[0], int(es["movimenti"].iloc[0])
    at = apri("ripartizione", rip_l1=voce, rip_l2=None)
    at.session_state["rip_eser"] = nome                 # come un clic sulla barra
    at.run()
    mov = next(e for e in at.expander if e.label.startswith("Movimenti"))
    assert mov.label.endswith("· %s" % nome)
    assert len(mov.dataframe[0].value) == n
    assert any(b.label.startswith("Esercente: %s" % nome) for b in mov.button)
    # cambiando categoria il filtro sull'esercente si toglie
    altra = next(v for v in an.ripartizione(c, TUTTI, "l1")["voce"] if v != voce)
    at.session_state["rip_l1"] = altra
    at.run()
    assert "rip_eser" not in at.session_state
