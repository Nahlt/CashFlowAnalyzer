"""Grafici ECharts (componente streamlit-echarts, ECharts incluso nel pacchetto: nulla viene caricato da internet).

Regole comuni: un solo asse dei valori, tratti sottili (linee 2px, marcatori 8px, barre con estremita'
arrotondate di 4px), colori per ruolo (blu, arancio, acqua; grigio per media e "non definita"), etichette
in euro con il formato italiano, animazione di apertura e di aggiornamento. I grafici cliccabili
restituiscono l'indice della categoria o del mese cliccato (anche cliccando nello spazio vuoto della riga).
"""
import json

import streamlit as st
from streamlit_echarts import JsCode, st_echarts

from .comune import ACQUA, ARANCIO, BLU, GRIGIO

MESI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
MESI_LUNGHI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
               "ottobre", "novembre", "dicembre"]
TENUE = "#a9c8ee"            # barre non selezionate quando ce n'e' una scelta
ROSSO = "#e34948"            # voci da sistemare (es. "Da classificare")
ROSSO_TENUE = "#f2a7a6"      # la stessa, quando e' scelta un'altra voce

EURO = JsCode("function(v){ if (v == null || isNaN(v)) return ''; return Number(v).toLocaleString('it-IT', "
              "{minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: 'always'}) + ' €'; }")
EURO_PRIMO = JsCode("function(v){ if (Array.isArray(v)) v = v[0]; if (v == null || isNaN(v)) return ''; "
                    "return Number(v).toLocaleString('it-IT', {minimumFractionDigits: 2, maximumFractionDigits: 2, "
                    "useGrouping: 'always'}) + ' €'; }")
EURO_TONDO = JsCode("function(v){ return Math.round(Number(v)).toLocaleString('it-IT', {useGrouping: 'always'}) + ' €'; }")
ASSE_EURO = JsCode("function(v){ var a = Math.abs(v); if (a >= 1000) return (v / 1000).toLocaleString('it-IT', "
                   "{maximumFractionDigits: 1}) + 'k €'; return v.toLocaleString('it-IT', {useGrouping: 'always'}) + ' €'; }")

# clic in un punto qualsiasi dell'area del grafico: restituisce l'indice di categoria sull'asse x o y
_CLIC = {
    "x": "function(params){ var p = [params.offsetX, params.offsetY]; if (!chart.containPixel('grid', p)) return; "
         "var v = chart.convertFromPixel({gridIndex: 0}, p); return {indice: Math.round(v[0]), t: Date.now()}; }",
    "y": "function(params){ var p = [params.offsetX, params.offsetY]; if (!chart.containPixel('grid', p)) return; "
         "var v = chart.convertFromPixel({gridIndex: 0}, p); return {indice: Math.round(v[1]), t: Date.now()}; }",
}

ANIMAZIONE = {"animationDuration": 700, "animationDurationUpdate": 500, "animationEasing": "cubicOut",
              "animationEasingUpdate": "cubicInOut"}


def etichetta_mese(m):
    a, mm = m.split("-")
    return "%s %s" % (MESI[int(mm) - 1], a[2:])


def mese_lungo(m):
    a, mm = m.split("-")
    return "%s %s" % (MESI_LUNGHI[int(mm) - 1], a)


def mostra(opzioni, chiave, altezza=340, clic=None):
    """Disegna il grafico. Con clic='x' o 'y' restituisce l'indice cliccato (una volta sola per clic), o None."""
    eventi = {"zr:click": _CLIC[clic]} if clic else None
    r = st_echarts(opzioni, height="%dpx" % altezza, key=chiave, events=eventi, replace_merge="series")
    if not clic or not r:
        return None
    ev = r.get("chart_event") if hasattr(r, "get") else getattr(r, "chart_event", None)
    if not ev or ev.get("indice") is None:
        return None
    if st.session_state.get("_clic_" + chiave) == ev.get("t"):
        return None                              # stesso clic gia' gestito
    st.session_state["_clic_" + chiave] = ev.get("t")
    return int(ev["indice"])


def _base(legenda=True, griglia=None):
    o = {**ANIMAZIONE, "backgroundColor": "transparent",
         "textStyle": {"fontFamily": "Inter, system-ui, sans-serif"},
         "grid": {"left": 8, "right": 16, "top": 36 if legenda else 12, "bottom": 8, "containLabel": True, **(griglia or {})},
         "tooltip": {"trigger": "axis", "valueFormatter": EURO, "axisPointer": {"type": "shadow"}}}
    if legenda:
        o["legend"] = {"top": 0, "left": 0, "icon": "roundRect", "itemWidth": 14, "itemHeight": 8}
    return o


def _asse_mesi(mesi, rotazione=True):
    return {"type": "category", "data": [etichetta_mese(m) for m in mesi],
            "axisTick": {"show": False}, "axisLabel": {"rotate": 45 if rotazione and len(mesi) > 14 else 0}}


def _asse_valori():
    return {"type": "value", "axisLabel": {"formatter": ASSE_EURO}, "splitLine": {"lineStyle": {"opacity": 0.5}}}


def linee_entrate_uscite(df, anteprima=None):
    """Entrate e uscite per mese. anteprima: {mese: [(categoria, euro), ...]} mostrata nel riquadro al passaggio
    del mouse (le prime categorie di spesa del mese, come in "Dove vanno i soldi")."""
    o = _base()
    o["xAxis"] = {**_asse_mesi(list(df["mese"])), "boundaryGap": False}
    o["yAxis"] = _asse_valori()
    o["tooltip"] = {"trigger": "axis", "axisPointer": {"type": "line"}, "confine": True, "enterable": False}
    if anteprima is not None:
        dati = {etichetta_mese(m): v for m, v in anteprima.items()}
        o["tooltip"]["formatter"] = JsCode(
            "function(p){ var A = %s; var f = function(v){ return Number(v).toLocaleString('it-IT', "
            "{minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: 'always'}) + ' €'; };"
            "var m = p[0].name; var h = '<div style=\"font-weight:600;margin-bottom:4px\">' + m + '</div>';"
            "p.forEach(function(s){ h += s.marker + s.seriesName + ': <b>' + f(s.value) + '</b><br>'; });"
            "var a = A[m]; if (a && a.length) { var mx = a[0][1];"
            "h += '<div style=\"margin-top:8px;font-weight:600\">Dove vanno i soldi</div>';"
            "a.forEach(function(x){ h += '<div style=\"display:flex;align-items:center;gap:6px;font-size:12px;margin-top:2px\">'"
            "+ '<span style=\"width:130px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis\">' + x[0] + '</span>'"
            "+ '<span style=\"display:inline-block;height:8px;border-radius:0 4px 4px 0;background:%s;width:' + "
            "Math.max(2, Math.round(90 * x[1] / mx)) + 'px\"></span><span>' + f(x[1]) + '</span></div>'; });"
            "h += '<div style=\"font-size:11px;opacity:.65;margin-top:6px\">Clic per aprire il dettaglio del mese</div>'; }"
            "return h; }" % (json.dumps(dati, ensure_ascii=False), BLU))
    o["series"] = [
        {"type": "line", "name": nome, "data": [round(v / 100, 2) for v in df[col]], "symbol": "circle",
         "symbolSize": 8, "lineStyle": {"width": 2}, "itemStyle": {"color": colore},
         "emphasis": {"focus": "series", "scale": 1.4}}
        for col, nome, colore in (("entrate", "Entrate", BLU), ("uscite", "Uscite", ARANCIO))]
    return o


def barre_saldo(df):
    o = _base(legenda=False)
    o["xAxis"] = _asse_mesi(list(df["mese"]))
    o["yAxis"] = _asse_valori()
    o["series"] = [{"type": "bar", "name": "Differenza entrate uscite", "barMaxWidth": 28, "data": [
        {"value": round(v / 100, 2), "itemStyle": {"color": BLU if v >= 0 else ARANCIO,
                                                     "borderRadius": [4, 4, 0, 0] if v >= 0 else [0, 0, 4, 4]}}
        for v in df["netto"]]}]
    o["tooltip"]["formatter"] = JsCode(
        "function(p){ var v = p[0].value; return '<b>' + p[0].name + '</b><br>Differenza entrate uscite: ' + Number(v).toLocaleString("
        "'it-IT', {minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: 'always'}) + ' €<div style=\"font-size:11px;opacity:.65;"
        "margin-top:4px\">Clic per aprire il dettaglio del mese</div>'; }")
    return o


def _colore_barra(voce, selezionata, rosse):
    acceso = selezionata is None or voce == selezionata
    if voce in rosse:
        return ROSSO if acceso else ROSSO_TENUE
    return BLU if acceso else TENUE


def barre_orizzontali(voci, valori_cent, selezionata=None, etichette_sopra=False, rosse=(), budget=None):
    """Barre orizzontali su una traccia grigia, la piu' grande in alto. Restituisce (opzioni,
    voci_nell_ordine_dell_asse): l'indice di un clic si riferisce alla seconda lista.
    etichette_sopra: il nome sopra la barra invece che a sinistra (per le colonne strette).
    rosse: voci disegnate in rosso invece che in blu (es. "Da classificare").
    budget: {voce: importo in centesimi} disegnato come un segno verticale sulla barra della voce."""
    coppie = list(zip(voci, valori_cent))[::-1]          # ECharts disegna la prima categoria in basso
    ordine = [v for v, _ in coppie]
    o = _base(legenda=False, griglia={"right": 70, "left": 4 if etichette_sopra else 8, "top": 18 if etichette_sopra else 6})
    o["tooltip"] = {"trigger": "axis", "axisPointer": {"type": "none"}, "valueFormatter": EURO}
    o["xAxis"] = {**_asse_valori(), "show": False}
    etichetta = {"width": 170, "overflow": "truncate", "color": "#2d3644"}
    if etichette_sopra:
        etichetta = {"inside": True, "verticalAlign": "bottom", "padding": [0, 0, 16, 0], "color": "#2d3644",
                     "width": 260, "overflow": "truncate"}
    # le categorie della tassonomia sono in maiuscolo: nel grafico "Svago e cultura" si legge meglio
    mostrate = [v.capitalize() if v.isupper() else v for v in ordine]
    o["yAxis"] = {"type": "category", "data": mostrate, "axisTick": {"show": False}, "axisLine": {"show": False},
                  "axisLabel": etichetta}
    o["series"] = [{"type": "bar", "barMaxWidth": 10 if etichette_sopra else 14, "showBackground": True,
                    "backgroundStyle": {"color": "#f0f3f7", "borderRadius": 4}, "data": [
        {"value": round(c / 100, 2), "itemStyle": {
            "color": _colore_barra(v, selezionata, rosse), "borderRadius": [0, 4, 4, 0]}}
        for v, c in coppie],
        "label": {"show": True, "position": "right", "formatter": JsCode(
            "function(p){ return Math.round(p.value).toLocaleString('it-IT', {useGrouping: 'always'}) + ' €'; }")}}]
    if budget:
        punti = [[round(budget[v] / 100, 2), mostrate[i]] for i, v in enumerate(ordine) if budget.get(v)]
        if punti:
            o["series"].append({"type": "scatter", "name": "Budget del mese", "data": punti, "symbol": "rect",
                                "symbolSize": [3, 16 if not etichette_sopra else 12], "z": 5,
                                "itemStyle": {"color": "#2d3644"}, "tooltip": {"valueFormatter": EURO_PRIMO}})
            o["xAxis"]["max"] = JsCode("function(v){ return v.max * 1.05; }")
    return o, ordine


def serie_mensile(mesi, valori_cent, mese_evidenziato=None, budget_cent=None):
    """Spesa del mese (barre) e media mobile a 3 mesi (linea grigia). Si disegna con mostra(..., clic="x"):
    l'indice restituito e' quello del mese in `mesi`. budget_cent: importo del budget della voce per ogni mese
    (None nei mesi prima del suo inizio), disegnato come linea a gradini."""
    v = [round(x / 100, 2) for x in valori_cent]
    media = [round(sum(v[max(0, i - 2):i + 1]) / len(v[max(0, i - 2):i + 1]), 2) for i in range(len(v))]
    o = _base()
    o["xAxis"] = _asse_mesi(mesi)
    o["yAxis"] = _asse_valori()
    o["series"] = [
        {"type": "bar", "name": "Spesa del mese", "barMaxWidth": 28, "data": [
            {"value": x, "itemStyle": {"color": BLU if (not mese_evidenziato or m == mese_evidenziato) else TENUE,
                                        "borderRadius": [4, 4, 0, 0]}} for m, x in zip(mesi, v)],
         "itemStyle": {"color": BLU}},
        {"type": "line", "name": "Media mobile 3 mesi", "data": media, "symbol": "none", "smooth": False,
         "lineStyle": {"width": 2, "color": GRIGIO}, "itemStyle": {"color": GRIGIO}}]
    if budget_cent and any(b is not None for b in budget_cent):
        # a gradini; con un mese solo una linea non si vede: un segno orizzontale sul mese
        uno = sum(1 for b in budget_cent if b is not None) == 1
        o["series"].append({"type": "line", "name": "Budget", "step": "middle", "symbol": "rect" if uno else "none",
                            "symbolSize": [22, 2], "showSymbol": uno, "connectNulls": False,
                            "data": [None if b is None else round(b / 100, 2) for b in budget_cent],
                            "lineStyle": {"width": 2, "type": "dashed", "color": ROSSO}, "itemStyle": {"color": ROSSO}})
    o["tooltip"]["formatter"] = JsCode(
        "function(p){ var f = function(v){ return Number(v).toLocaleString('it-IT', {minimumFractionDigits: 2, "
        "maximumFractionDigits: 2, useGrouping: 'always'}) + ' €'; }; var h = '<b>' + p[0].name + '</b><br>';"
        "p.forEach(function(s){ if (s.value == null) return; h += s.marker + s.seriesName + ': <b>' + f(s.value) + '</b><br>'; });"
        "var t = p[0].name === %s ? 'Clic per togliere il filtro sul mese' : 'Clic per vedere solo questo mese';"
        "return h + '<div style=\"font-size:11px;opacity:.65;margin-top:4px\">' + t + '</div>'; }"
        % json.dumps(etichetta_mese(mese_evidenziato) if mese_evidenziato else None))
    return o


def barre_impilate(mesi, serie):
    """serie: [(nome, valori_cent, colore)], impilate per mese."""
    o = _base()
    o["xAxis"] = _asse_mesi(mesi)
    o["yAxis"] = _asse_valori()
    o["series"] = [{"type": "bar", "name": nome, "stack": "totale", "barMaxWidth": 28,
                    "data": [round(x / 100, 2) for x in valori],
                    "itemStyle": {"color": colore, "borderColor": "rgba(255,255,255,0.85)", "borderWidth": 1}}
                   for nome, valori, colore in serie]
    return o


COLORI_NATURA = [BLU, ARANCIO, ACQUA]


def linea_mensile(mesi, valori_cent, nome):
    """Una sola serie per mese (es. capitale investito): linea sottile, area tenue, niente legenda."""
    o = _base(legenda=False)
    o["xAxis"] = {**_asse_mesi(mesi), "boundaryGap": False}
    o["yAxis"] = _asse_valori()
    o["tooltip"] = {"trigger": "axis", "valueFormatter": EURO, "axisPointer": {"type": "line"}}
    o["series"] = [{"type": "line", "name": nome, "data": [round(v / 100, 2) for v in valori_cent], "symbol": "circle",
                    "symbolSize": 6, "showSymbol": len(mesi) <= 24, "lineStyle": {"width": 2, "color": BLU},
                    "itemStyle": {"color": BLU}, "areaStyle": {"color": BLU, "opacity": 0.08}}]
    return o


# palette categoriale (ordine fisso: il colore segue l'entita', non il rango)
CATEGORIALI = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]

PERCENTO = JsCode("function(v){ if (v == null || isNaN(v)) return ''; return (Number(v) >= 0 ? '+' : '') + "
                  "Number(v).toLocaleString('it-IT', {minimumFractionDigits: 1, maximumFractionDigits: 1}) + '%'; }")
ASSE_PERCENTO = JsCode("function(v){ return v.toLocaleString('it-IT', {maximumFractionDigits: 1}) + '%'; }")
DATA_ASSE = JsCode("function(v){ var d = new Date(v); var m = ['gen','feb','mar','apr','mag','giu','lug','ago','set',"
                   "'ott','nov','dic']; return (d.getDate() === 1 && d.getMonth() === 0) ? String(d.getFullYear()) : "
                   "m[d.getMonth()] + ' ' + String(d.getFullYear()).slice(2); }")


def linee_tempo(serie, percento=False, gradino=False):
    """Linee su asse del tempo (giorni). serie: [{"nome", "punti": [(data iso, valore)], "colore", "tratteggio"}].
    Valori in euro (o in % con percento=True). Legenda solo con due o piu' serie."""
    o = _base(legenda=len(serie) > 1)
    o.setdefault("legend", {"show": False})        # stesso grafico con 1 o piu' serie: la legenda va tolta esplicitamente
    o["legend"].setdefault("show", True)
    formato = PERCENTO if percento else EURO
    o["tooltip"] = {"trigger": "axis", "valueFormatter": formato, "axisPointer": {"type": "line"}, "confine": True}
    o["xAxis"] = {"type": "time", "axisLabel": {"formatter": DATA_ASSE, "hideOverlap": True},
                  "axisTick": {"show": False}, "splitLine": {"show": False}}
    o["yAxis"] = {"type": "value", "scale": not percento,
                  "axisLabel": {"formatter": ASSE_PERCENTO if percento else ASSE_EURO},
                  "splitLine": {"lineStyle": {"opacity": 0.5}}}
    o["dataZoom"] = [{"type": "inside", "filterMode": "none"}]
    o["series"] = []
    for s in serie:
        lunga = len(s["punti"]) > 60
        o["series"].append({
            "type": "line", "name": s["nome"], "data": [[d, None if v is None else round(v, 2)] for d, v in s["punti"]],
            "showSymbol": not lunga, "symbol": "circle", "symbolSize": 8, "step": "end" if gradino else False,
            "sampling": "lttb" if lunga else None, "connectNulls": False,
            "lineStyle": {"width": 2, "color": s["colore"], "type": "dashed" if s.get("tratteggio") else "solid"},
            "itemStyle": {"color": s["colore"]}, "emphasis": {"focus": "series"}})
    if percento:
        o["series"][0]["markLine"] = {"silent": True, "symbol": "none", "label": {"show": False},
                                      "lineStyle": {"color": GRIGIO, "type": "solid", "width": 1}, "data": [{"yAxis": 0}]}
    return o


GIORNO_ASSE = JsCode("function(v){ var d = new Date(v); var m = ['gen','feb','mar','apr','mag','giu','lug','ago','set',"
                     "'ott','nov','dic']; return d.getDate() + ' ' + m[d.getMonth()] + (d.getMonth() === 0 && "
                     "d.getDate() === 1 ? ' ' + d.getFullYear() : ''); }")
GIORNO_SUGGERIMENTO = JsCode(
    "function(p){ var f = function(v){ return Number(v).toLocaleString('it-IT', {minimumFractionDigits: 2, "
    "maximumFractionDigits: 2, useGrouping: 'always'}) + ' €'; }; var d = new Date(p[0].value[0]);"
    "var h = '<b>' + d.toLocaleDateString('it-IT', {weekday: 'short', day: 'numeric', month: 'long', year: 'numeric'})"
    " + '</b><br>'; p.forEach(function(s){ if (s.value[1] != null) h += s.marker + s.seriesName + ': <b>' + "
    "f(s.value[1]) + '</b><br>'; }); return h; }")


def saldo_e_uscite(df, giorno_evidenziato=None):
    """Saldo a fine giornata (linea blu, asse a sinistra) e uscite del giorno (barre arancio, asse a destra) per la
    pagina "Saldo dei conti". Eccezione alla regola dell'asse unico: saldo e uscite hanno ordini di grandezza
    diversi (migliaia contro decine di euro) e con un asse solo le barre sparirebbero. df: colonne giorno, saldo,
    uscite (centesimi). giorno_evidenziato: il giorno scelto in modalita' giorno, segnato con una linea verticale."""
    o = _base()
    o["tooltip"] = {"trigger": "axis", "formatter": GIORNO_SUGGERIMENTO, "axisPointer": {"type": "line"},
                    "confine": True}
    giorni = list(df["giorno"])
    lunga = len(giorni) > 92
    o["xAxis"] = {"type": "time", "axisLabel": {"formatter": DATA_ASSE if lunga else GIORNO_ASSE, "hideOverlap": True},
                  "axisTick": {"show": False}, "splitLine": {"show": False}}
    o["yAxis"] = [{"type": "value", "scale": True, "axisLabel": {"formatter": ASSE_EURO},
                   "splitLine": {"lineStyle": {"opacity": 0.5}}},
                  {"type": "value", "axisLabel": {"formatter": ASSE_EURO}, "splitLine": {"show": False}}]
    o["dataZoom"] = [{"type": "inside", "filterMode": "none"}]
    # gradino a meta' tra due giorni: il saldo cambia nel giorno della barra delle uscite, non in quello dopo
    saldo = {"type": "line", "name": "Saldo a fine giornata", "yAxisIndex": 0, "step": "middle",
             "data": [[g, None if pd_nullo(v) else round(v / 100, 2)] for g, v in zip(giorni, df["saldo"])],
             "showSymbol": False, "symbol": "circle", "symbolSize": 8, "z": 3,
             "lineStyle": {"width": 2, "color": BLU}, "itemStyle": {"color": BLU},
             "areaStyle": {"color": BLU, "opacity": 0.06}}
    if giorno_evidenziato:
        saldo["markLine"] = {"silent": True, "symbol": "none", "label": {"show": False},
                             "lineStyle": {"color": GRIGIO, "type": "dashed", "width": 1},
                             "data": [{"xAxis": giorno_evidenziato}]}
        saldo["markPoint"] = {"symbol": "circle", "symbolSize": 10, "label": {"show": False},
                              "itemStyle": {"color": BLU, "borderColor": "#fff", "borderWidth": 2},
                              "data": [{"coord": [giorno_evidenziato, v[1]]} for v in saldo["data"]
                                       if v[0] == giorno_evidenziato and v[1] is not None]}
    uscite = {"type": "bar", "name": "Uscite del giorno", "yAxisIndex": 1, "barMaxWidth": 12 if lunga else 18,
              "data": [[g, round(v / 100, 2) if v else None] for g, v in zip(giorni, df["uscite"])],
              "itemStyle": {"color": ARANCIO, "opacity": 0.75, "borderRadius": [3, 3, 0, 0]}}
    o["series"] = [uscite, saldo]
    o["legend"]["data"] = ["Saldo a fine giornata", "Uscite del giorno"]
    return o


def pd_nullo(v):
    """None o NaN (pandas porta i None delle colonne intere a NaN)."""
    return v is None or v != v


# ---------------------------------------------------------------- budget

AMBRA = "#eda100"            # budget a rischio (previsto oltre il budget)
AMBRA_TESTO = "#8a5a00"
ROSSO_TESTO = "#b3261e"
COLORI_STATO = {"in_linea": BLU, "a_rischio": AMBRA, "superato": ROSSO}
TRATTEGGIO = {"symbol": "rect", "dashArrayX": [1, 0], "dashArrayY": [3, 3], "rotation": -0.785}


def barre_budget(righe):
    """Saturazione dei budget in percentuale: barra piena per lo speso, tratteggiata e piu' chiara per il
    previsto fino a fine mese, linea al 100%. Colore, etichetta e testo dicono lo stato (in linea, a rischio,
    superato). righe: [{nome, sotto (bool), stato, saturazione, saturazione_prevista, info (html del riquadro)}]
    dall'alto in basso. Si disegna con mostra(..., clic="y"): l'indice e' quello di `righe`."""
    r = list(righe)[::-1]
    massimo = max([100] + [x["saturazione"] or 0 for x in r] + [x["saturazione_prevista"] or 0 for x in r])
    o = _base(legenda=False, griglia={"right": 150, "top": 24})
    o["xAxis"] = {"type": "value", "min": 0, "max": max(120, int(massimo * 1.08 // 10 + 1) * 10),
                  "axisLabel": {"formatter": "{value}%"}, "splitLine": {"lineStyle": {"opacity": 0.4}}}
    o["yAxis"] = {"type": "category", "axisTick": {"show": False}, "axisLine": {"show": False},
                  "data": [("   › " + x["nome"].split(" › ", 1)[1]) if x["sotto"] else x["nome"] for x in r],
                  "axisLabel": {"width": 190, "overflow": "truncate", "color": "#2d3644"}}
    o["tooltip"] = {"trigger": "axis", "axisPointer": {"type": "shadow"}, "confine": True, "formatter": JsCode(
        "function(p){ return p[0].data.info + '<div style=\"font-size:11px;opacity:.65;margin-top:4px\">"
        "Clic per il dettaglio del mese</div>'; }")}
    speso, previsto = [], []
    for x in r:
        colore = COLORI_STATO[x["stato"]]
        s_ = x["saturazione"] or 0
        p_ = x["saturazione_prevista"]
        if x["stato"] == "superato":
            testo = "%d%% superato" % s_
        elif x["stato"] == "a_rischio":
            testo = "%d%% → %d%% a rischio" % (s_, p_)
        else:
            testo = "%d%%" % s_ + (" → %d%%" % p_ if p_ is not None else "")
        colore_testo = {"superato": ROSSO_TESTO, "a_rischio": AMBRA_TESTO}.get(x["stato"], "#2d3644")
        speso.append({"value": s_, "info": x["info"], "itemStyle": {"color": colore,
                      "borderRadius": [0, 4, 4, 0] if p_ is None or p_ <= s_ else 0}})
        previsto.append({"value": max(0, (p_ or 0) - s_), "info": x["info"],
                         "itemStyle": {"color": colore + "73", "borderRadius": [0, 4, 4, 0],
                                       "decal": {**TRATTEGGIO, "color": "rgba(255,255,255,0.6)"}},
                         "label": {"show": True, "position": "right", "formatter": testo, "color": colore_testo,
                                   "fontWeight": 600 if x["stato"] != "in_linea" else 400}})
    o["series"] = [
        {"type": "bar", "name": "Speso", "stack": "b", "barMaxWidth": 16, "data": speso,
         "markLine": {"silent": True, "symbol": "none", "data": [{"xAxis": 100}],
                      "lineStyle": {"color": "#5f5e5a", "type": "dashed", "width": 1.5},
                      "label": {"formatter": "100%", "position": "end", "color": "#5f5e5a"}}},
        {"type": "bar", "name": "Previsto", "stack": "b", "barMaxWidth": 16, "data": previsto}]
    return o


def cumulato_budget(giorni, reale, stimato, tipico, importo_cent, segni=()):
    """Spesa cumulata del mese giorno per giorno: speso (linea piena), speso + previsto (tratteggiata), curva
    tipica dei mesi passati (grigia), budget (linea rossa). segni: [(giorno, testo)] fin dove arrivano i dati
    di ciascun conto. Valori in centesimi."""
    e = lambda v: None if v is None else round(v / 100, 2)
    o = _base()
    o["xAxis"] = {"type": "category", "data": [str(d) for d in range(1, giorni + 1)], "boundaryGap": False,
                  "axisTick": {"show": False}}
    o["yAxis"] = _asse_valori()
    o["tooltip"] = {"trigger": "axis", "valueFormatter": EURO, "axisPointer": {"type": "line"}}
    o["series"] = []
    if tipico:
        o["series"].append({"type": "line", "name": "Curva tipica", "data": [e(v) for v in tipico], "symbol": "none",
                            "lineStyle": {"width": 2, "color": "#b4b2a9"}, "itemStyle": {"color": "#b4b2a9"}})
    o["series"].append({"type": "line", "name": "Speso", "data": [e(v) for v in reale], "symbol": "none",
                        "lineStyle": {"width": 2.5, "color": BLU}, "itemStyle": {"color": BLU},
                        "markLine": {"silent": True, "symbol": "none", "label": {"position": "insideEndTop"},
                                     "lineStyle": {"color": GRIGIO, "type": "dotted"},
                                     "data": [{"xAxis": str(g), "label": {"formatter": t, "color": GRIGIO}}
                                              for g, t in segni]}})
    if stimato:
        o["series"].append({"type": "line", "name": "Speso + previsto", "data": [e(v) for v in stimato],
                            "symbol": "none", "lineStyle": {"width": 2.5, "type": "dashed", "color": AMBRA},
                            "itemStyle": {"color": AMBRA}})
    o["series"].append({"type": "line", "name": "Budget", "data": [e(importo_cent)] * giorni, "symbol": "none",
                        "lineStyle": {"width": 1.5, "color": ROSSO}, "itemStyle": {"color": ROSSO}})
    return o
