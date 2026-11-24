"""Formati di import dell'utente (0.22.0): procedura guidata per un file che nessun formato riconosce (pagina
Importa) e pagina "Formati di import" in Configurazione (formati dell'utente, causali senza tipo, formati
dell'app).

La procedura parte da proposte automatiche (spese/formati.py) che l'utente conferma o corregge, passo per passo; a
ogni passo il file si rilegge con le scelte del momento e si vede quanti movimenti escono. Le scelte stanno in
st.session_state["fmt_proc"]; cambiare separatore, codifica, foglio o riga d'intestazione rifa' le proposte dei
passi successivi (le chiavi dei loro widget cambiano con il contatore `gen`).
"""
import copy
from pathlib import Path

import pandas as pd
import streamlit as st

from .. import configurazione as cfg
from .. import estrattori, formati, servizi
from .. import importa as imp
from ..lettore import FileNonRiconosciuto, fogli_excel
from ..numeri import euro
from .comune import conn

STATO = "fmt_proc"
PASSI = ["Intestazione", "Colonne", "Formati", "Tipi di movimento", "Dettagli dalla descrizione",
         "Conto e anteprima"]
RIGHE_VISTE = 30
NESSUNA = "(nessuna)"
SEPARATORI = {";": "Punto e virgola ( ; )", ",": "Virgola ( , )", "\t": "Tabulazione", "|": "Barra verticale ( | )"}
CODIFICHE = {"utf-8-sig": "UTF-8", "cp1252": "Windows (cp1252)", "latin-1": "Latin-1"}


# ---------------------------------------------------------------------------
# Avvio e stato
# ---------------------------------------------------------------------------
def avvia(nome_file, dati, origine, base=None):
    """Apre la procedura per un file. base: id del formato di cui si introduce una nuova versione."""
    if base:
        scelte = formati.scelte_da_profilo(cfg.profili()[base], nome_file, dati)
        scelte["nome"] = scelte["nome"] + " (nuova versione)"
    else:
        scelte = formati.proponi_scelte(nome_file, dati)
    st.session_state[STATO] = {"nome_file": nome_file, "dati": dati, "origine": origine, "base": base, "passo": 1,
                               "gen": 0, "scelte": scelte, "proposte": copy.deepcopy(scelte)}


def attiva(origine):
    p = st.session_state.get(STATO)
    return bool(p) and p["origine"] == origine


def chiudi():
    st.session_state.pop(STATO, None)


def _rigenera(proc, separatore=None, codifica=None, foglio=None, riga=None):
    """Nuove proposte dopo un cambio di separatore, codifica, foglio o riga d'intestazione (nome, banca e tipo di
    conto restano)."""
    vecchie = proc["scelte"]
    s = formati.proponi_scelte(proc["nome_file"], proc["dati"], separatore, codifica, foglio, riga)
    s.update({k: vecchie[k] for k in ("nome", "banca", "tipo_conto")})
    proc["scelte"], proc["proposte"] = s, copy.deepcopy(s)
    proc["gen"] += 1


def _k(proc, nome):
    return "fmt_%d_%s" % (proc["gen"], nome)


def _righe(proc):
    s = proc["scelte"]
    return formati.leggi_file(proc["nome_file"], proc["dati"], s["separatore"], s["codifica"], s["foglio"])[0]


def _prova(proc, c):
    try:
        return formati.prova(proc["nome_file"], proc["dati"], proc["scelte"], imp.nomi_riservati(c)), None
    except FileNonRiconosciuto:
        return None, "con questa intestazione il file non si legge"
    except Exception as e:                    # una scelta incoerente (es. una colonna di testo come importo)
        return None, str(e)


def _conteggio(fl, errore):
    """Quanti movimenti escono con le scelte del momento."""
    if fl is None:
        st.warning("Nessun movimento letto: %s." % errore, icon=":material/warning:")
        return
    n, s = len(fl.movimenti), len(fl.scartate)
    testo = "**%d %s · %d %s**" % (n, "movimento letto" if n == 1 else "movimenti letti", s,
                                   "riga scartata" if s == 1 else "righe scartate")
    if s:
        testo += " (%s)" % "; ".join("riga %d: %s" % x for x in fl.scartate[:3]) + ("…" if s > 3 else "")
    if n == 0 or fl.avvisi:
        st.warning(testo + ("" if n else ". Controlla le scelte: probabilmente la riga d'intestazione o una colonna "
                                         "non sono quelle giuste."), icon=":material/warning:")
        for a in fl.avvisi:
            st.warning(a)
    else:
        st.success(testo, icon=":material/check_circle:")


def _proposto(proc, chiave, valore):
    """' · proposto' accanto all'opzione proposta in automatico."""
    p = proc["proposte"]
    for k in chiave.split("."):
        p = (p or {}).get(k) if isinstance(p, dict) else None
    return " · proposto" if valore is not None and p == valore else ""


# ---------------------------------------------------------------------------
# Procedura
# ---------------------------------------------------------------------------
def procedura(c):
    """Disegna la procedura guidata (nella pagina che l'ha aperta)."""
    proc = st.session_state[STATO]
    titolo = "Nuovo formato di import"
    if proc["base"]:
        titolo = "Nuova versione di «%s»" % cfg.profili()[proc["base"]].get("nome", proc["base"])
    with st.container(border=True, key="scheda_formato"):
        st.subheader(titolo)
        st.caption("File d'esempio: %s. Il file non viene salvato: serve solo a descrivere il formato." %
                   proc["nome_file"])
        n = proc["passo"]
        st.progress(n / len(PASSI), text="Passo %d di %d · %s" % (n, len(PASSI), PASSI[n - 1]))
        ok = [_passo_intestazione, _passo_colonne, _passo_formati, _passo_tipi, _passo_dettagli,
              _passo_conto][n - 1](proc, c)
        a, b, cc = st.columns([1, 1, 2])
        if a.button("Annulla", key="fmt_annulla", width="stretch"):
            chiudi()
            st.rerun()
        if n > 1 and b.button("← Indietro", key="fmt_indietro", width="stretch"):
            proc["passo"] -= 1
            st.rerun()
        if n < len(PASSI) and cc.button("Avanti: %s →" % PASSI[n].lower(), key="fmt_avanti", type="primary",
                                        disabled=not ok, width="stretch"):
            proc["passo"] += 1
            st.rerun()


def _passo_intestazione(proc, c):
    s = proc["scelte"]
    st.markdown("#### Dove sono i nomi delle colonne?")
    st.caption("Le righe sopra l'intestazione sono la testa del file (conto, periodo, saldo): le userai all'ultimo "
               "passo.")
    excel = s["formato"] == "xlsx"
    col = st.columns(3)
    if excel:
        fogli = fogli_excel(proc["dati"])
        foglio = col[0].selectbox("Foglio", list(range(len(fogli))), index=min(s["foglio"], len(fogli) - 1),
                                  format_func=lambda i: fogli[i], key=_k(proc, "foglio"))
        if foglio != s["foglio"]:
            _rigenera(proc, foglio=foglio)
            st.rerun()
    else:
        sep = col[0].selectbox("Separatore" + _proposto(proc, "separatore", s["separatore"]), list(SEPARATORI),
                               index=list(SEPARATORI).index(s["separatore"]) if s["separatore"] in SEPARATORI else 0,
                               format_func=SEPARATORI.get, key=_k(proc, "sep"))
        cod = col[1].selectbox("Codifica" + _proposto(proc, "codifica", s["codifica"]), list(CODIFICHE),
                               index=list(CODIFICHE).index(s["codifica"]) if s["codifica"] in CODIFICHE else 0,
                               format_func=CODIFICHE.get, key=_k(proc, "cod"),
                               help="Se le lettere accentate si vedono male, prova un'altra codifica.")
        if sep != s["separatore"] or cod != s["codifica"]:
            _rigenera(proc, separatore=sep, codifica=cod)
            st.rerun()
    righe = _righe(proc)
    quante = min(len(righe), formati.lettore.RIGHE_TESTA)
    anteprime = {i: " | ".join(x for x in (formati.testo(v) for v in righe[i]) if x) or "(vuota)"
                 for i in range(quante)}
    opz = list(range(quante))
    h = s["riga"]
    scelta = col[2 if not excel else 1].selectbox(
        "Riga d'intestazione" + _proposto(proc, "riga", h), opz, index=h if h in opz else None,
        format_func=lambda i: "Riga %d — %s" % (i + 1, anteprime[i][:60] + ("…" if len(anteprime[i]) > 60 else "")),
        placeholder="Scegli la riga con i nomi delle colonne", key=_k(proc, "riga"))
    if scelta is not None and scelta != h:
        _rigenera(proc, s["separatore"], s["codifica"], s["foglio"], riga=scelta)
        st.rerun()
    if h is None:
        st.info("Non ho trovato l'intestazione: indica tu la riga con i nomi delle colonne.",
                icon=":material/help:")
    elif proc["proposte"]["riga"] == h and not proc["gen"]:
        st.info("Ho proposto la **riga %d**. Se non e' quella con i nomi delle colonne, sceglila nell'elenco." % (h + 1),
                icon=":material/lightbulb:")
    larghezza = max((len(r) for r in righe[:RIGHE_VISTE]), default=0)
    lettere = [chr(65 + i) if i < 26 else "C%d" % (i + 1) for i in range(larghezza)]
    tab = pd.DataFrame([[formati.testo(r[i]) if i < len(r) else "" for i in range(larghezza)]
                        for r in righe[:RIGHE_VISTE]], columns=lettere, index=range(1, min(len(righe), RIGHE_VISTE) + 1))
    tab.index.name = "Riga"

    def colora(r):
        if h is None:
            return [""] * len(r)
        if r.name == h + 1:
            return ["background-color: #e6f0fb; font-weight: 600"] * len(r)
        return ["color: #5b6573; background-color: #f7f9fb"] * len(r) if r.name <= h else [""] * len(r)
    st.dataframe(tab.style.apply(colora, axis=1), width="stretch", height=min(38 + 35 * len(tab), 460))
    if h is None:
        return False
    nomi = [n for n in formati.intestazione(righe, h) if n]
    st.markdown("**Colonne trovate:** %s" % (" · ".join(nomi) or "nessuna"))
    fl, err = _prova(proc, c)
    _conteggio(fl, err)
    return len(nomi) >= 2 and bool(fl and fl.movimenti)


def _passo_colonne(proc, c):
    s = proc["scelte"]
    righe = _righe(proc)
    nomi = [n for n in formati.intestazione(righe, s["riga"]) if n]
    primo = {n: (formati.valori(righe, s["riga"], n)[:1] or [""])[0] for n in nomi}
    st.markdown("#### Cosa c'e' in ogni colonna")
    st.caption("Accanto a ogni colonna vedi il primo valore del file. Esercente, controparte, IBAN, ora e data "
               "dell'operazione si estraggono dalla descrizione al passo 5.")
    modi = {"una": "Una colonna con il segno (es. −45,90 e 1.850,00)", "due": "Due colonne: uscite ed entrate"}
    s["modo_importo"] = st.radio("Importo", list(modi), index=list(modi).index(s["modo_importo"]),
                                 format_func=lambda m: modi[m] + _proposto(proc, "modo_importo", m),
                                 key=_k(proc, "modo"), horizontal=True)
    campi = ["data_contabile", "data_valuta", "data_operazione"]
    campi += ["importo_uscite", "importo_entrate"] if s["modo_importo"] == "due" else ["importo"]
    campi += ["descrizione", "causale"]
    obbligatori = {"data_contabile", "descrizione", "importo", "importo_uscite", "importo_entrate"}
    sinistra, destra = st.columns([3, 2])
    with sinistra:
        for campo in campi:
            opz = [None] + nomi
            v = s["colonne"].get(campo)
            nota = " (obbligatoria)" if campo in obbligatori else ""
            s["colonne"][campo] = st.selectbox(
                formati.CAMPI_COLONNE[campo] + nota, opz, index=opz.index(v) if v in opz else 0,
                format_func=lambda n, campo=campo: NESSUNA if n is None else "%s · %s%s" % (
                    n, formati.testo(primo[n])[:40] or "vuota nella prima riga",
                    _proposto(proc, "colonne." + campo, n)),
                key=_k(proc, "col_" + campo))
    mancano = [formati.CAMPI_COLONNE[k] for k in campi if k in obbligatori and not s["colonne"].get(k)]
    fl, err = _prova(proc, c)
    with destra:
        st.markdown("**Come verranno letti**")
        if fl and fl.movimenti:
            st.dataframe(pd.DataFrame([{"Data contabile": m["data_contabile"], "Importo": euro(m["importo_cent"]),
                                        "Descrizione": m["descrizione"], "Causale": m["codice"]}
                                       for m in fl.movimenti[:6]]), hide_index=True, width="stretch")
    if mancano:
        st.warning("Da scegliere: %s." % ", ".join(mancano))
    _conteggio(fl, err)
    return not mancano and bool(fl and fl.movimenti)


def _passo_formati(proc, c):
    s = proc["scelte"]
    righe = _righe(proc)
    h = s["riga"]
    st.markdown("#### Date e importi")
    a, b = st.columns(2)
    date_val = formati.valori(righe, h, s["colonne"]["data_contabile"])
    prove = formati.prova_date(date_val)
    conta = {f: (letti, tot) for f, letti, tot in prove}
    ambigue = formati.date_ambigue(prove)
    opz = [f for f, _, _ in prove]
    with a:
        s["date"] = st.selectbox(
            "Formato delle date" + ("" if ambigue else _proposto(proc, "date", s["date"])), opz,
            index=None if ambigue and not proc.get("date_scelta") else (opz.index(s["date"]) if s["date"] in opz
                                                                         else 0),
            format_func=lambda f: "%s · %d date su %d" % (formati.esempio_data(f), *conta[f]),
            placeholder="Scegli: il file si puo' leggere in piu' modi", key=_k(proc, "date"))
        if ambigue:
            st.warning("Tutte le date si possono leggere sia giorno/mese sia mese/giorno (es. %s): scegli tu."
                       % (formati.testo(date_val[0]) if date_val else ""), icon=":material/help:")
        proc["date_scelta"] = s["date"] is not None
    importi = [v for k in ("importo", "importo_uscite", "importo_entrate")
               if (s["modo_importo"] == "due") == (k != "importo") and s["colonne"].get(k)
               for v in formati.valori(righe, h, s["colonne"][k])]
    with b:
        if importi and all(isinstance(v, (int, float)) for v in importi):
            st.markdown("**Separatore dei decimali**")
            st.caption("Le celle dell'Excel sono gia' numeri: non serve sceglierlo.")
        else:
            pd_ = {d: (letti, tot) for d, letti, tot in formati.prova_decimali(importi)}
            amb = formati.decimali_ambigui(importi)
            migliaia = {"virgola": "punto, oppure nessuno", "punto": "virgola, oppure nessuno"}
            s["decimali"] = st.selectbox(
                "Separatore dei decimali" + ("" if amb else _proposto(proc, "decimali", s["decimali"])),
                list(formati.DECIMALI), index=list(formati.DECIMALI).index(s["decimali"]),
                format_func=lambda d: "%s · %s · %d importi su %d" % (
                    d.capitalize(), "1.850,00" if d == "virgola" else "1,850.00", *pd_.get(d, (0, 0))),
                key=_k(proc, "dec"))
            st.caption("Separatore delle migliaia: %s." % migliaia[s["decimali"]])
            if amb:
                st.warning("Alcuni importi (es. %s) cambiano valore secondo il separatore: controlla la scelta."
                           % next(v for v in importi if isinstance(v, str)), icon=":material/help:")
        if s["modo_importo"] == "una":
            s["inverti_segno"] = st.checkbox("Inverti il segno (le spese sono scritte in positivo)",
                                             value=s["inverti_segno"], key=_k(proc, "segno"))
            fl0, _ = _prova(dict(proc, scelte=dict(s, inverti_segno=False)), c)
            positivi = [m for m in (fl0.movimenti if fl0 else []) if (m["importo_cent"] or 0) > 0]
            if fl0 and fl0.movimenti and len(positivi) >= 0.8 * len(fl0.movimenti) and not s["inverti_segno"]:
                st.caption("Quasi tutti gli importi sono positivi: se sono spese (es. estratto di una carta), "
                           "inverti il segno.")
        else:
            st.caption("Con uscite ed entrate in due colonne le uscite diventano negative da sole.")
    fl, err = _prova(proc, c)
    if fl and fl.movimenti:
        st.markdown("**Controllo: come verra' letto il file**")
        st.dataframe(pd.DataFrame([{"Riga": m["n_riga"], "Nel file": " · ".join(x for x in m["grezzo"] if x)[:90],
                                    "Data": m["data_contabile"], "Importo": euro(m["importo_cent"])}
                                   for m in fl.movimenti[:5]]), hide_index=True, width="stretch")
    _conteggio(fl, err)
    return s["date"] is not None and bool(fl and fl.movimenti)


def _passo_tipi(proc, c):
    s = proc["scelte"]
    righe = _righe(proc)
    st.markdown("#### Che cosa vuol dire ogni movimento")
    st.caption("Il tipo decide come conta il movimento: una spesa con carta passa dalla classificazione per "
               "esercente, un bonifico si abbina agli altri conti e ai familiari. «Altro» va in Revisione.")
    tipi = list(formati.TIPI_LEGGIBILI)
    causale = s["colonne"].get("causale")
    if causale:
        elenco = formati.causali_del_file(righe, s["riga"], causale, s["colonne"].get("descrizione"))
        da_scegliere = [x for x, _, _ in elenco if not s["tipi"].get(x)]
        if da_scegliere:
            st.warning("%d causali da scegliere: %s. Se le lasci cosi', quei movimenti restano «Altro» e vanno in "
                       "Revisione." % (len(da_scegliere), ", ".join(da_scegliere[:5])), icon=":material/help:")
        for i, (nome, n, esempio) in enumerate(elenco):
            a, b = st.columns([3, 2])
            a.markdown("**%s** · %d %s  \n%s" % (nome, n, "movimento" if n == 1 else "movimenti", esempio[:90]))
            v = s["tipi"].get(nome)
            s["tipi"][nome] = b.selectbox(
                "Tipo per %s" % nome, tipi, index=tipi.index(v) if v in tipi else None,
                format_func=lambda t, nome=nome: formati.TIPI_LEGGIBILI[t] + _proposto(proc, "tipi." + nome, t),
                placeholder="Scegli un tipo…", key=_k(proc, "tipo_%d" % i), label_visibility="collapsed")
        s["tipo_altre"] = st.selectbox("Causali che non sono in questo file (per i prossimi file)", tipi,
                                       index=tipi.index(s["tipo_altre"]), format_func=formati.TIPI_LEGGIBILI.get,
                                       key=_k(proc, "tipo_altre"),
                                       help="Una causale nuova si puo' anche aggiungere dopo, in Formati di import.")
    else:
        st.info("Il file non ha una colonna con la causale: scegli un tipo per le uscite e uno per le entrate. "
                "L'esercente si prende dalla descrizione.", icon=":material/info:")
        a, b = st.columns(2)
        s["tipo_uscite"] = a.selectbox("Tipo per le uscite", tipi, index=tipi.index(s["tipo_uscite"]),
                                       format_func=formati.TIPI_LEGGIBILI.get, key=_k(proc, "tipo_uscite"))
        s["tipo_entrate"] = b.selectbox("Tipo per le entrate", tipi, index=tipi.index(s["tipo_entrate"]),
                                        format_func=formati.TIPI_LEGGIBILI.get, key=_k(proc, "tipo_entrate"))
    _conteggio(*_prova(proc, c))
    return True


def _passo_dettagli(proc, c):
    s = proc["scelte"]
    righe = _righe(proc)
    nomi = [n for n in formati.intestazione(righe, s["riga"]) if n]
    st.markdown("#### Da quale campo estraggo queste informazioni?")
    st.caption("Per ogni informazione scegli la colonna: l'app prova le regole che conosce, in ordine, e usa la "
               "prima che trova qualcosa. Dove nessuna regola trova l'esercente, si usa tutta la descrizione.")
    cache = {}
    titolari = imp.nomi_riservati(c)
    for info, spec in estrattori.INFORMAZIONI.items():
        e = s["estrattori"].setdefault(info, {"colonna": s["colonne"].get("descrizione"), "regole": []})
        with st.container(border=True):
            st.markdown("**%s** · %s" % (spec["nome"], spec["descrizione"]))
            a, b = st.columns([1, 2])
            opz = [None] + nomi
            e["colonna"] = a.selectbox("Colonna di partenza", opz,
                                       index=opz.index(e.get("colonna")) if e.get("colonna") in opz else 0,
                                       format_func=lambda n: NESSUNA if n is None else n,
                                       key=_k(proc, "est_col_" + info))
            regole = estrattori.regole_di(info)
            e["regole"] = b.multiselect("Regole, in ordine", [r.nome for r in regole],
                                        default=[n for n in estrattori.nomi_regole(e) if n in estrattori.PER_NOME],
                                        format_func=lambda n: estrattori.PER_NOME[n].etichetta,
                                        key=_k(proc, "est_reg_" + info), placeholder="Nessuna")
            e.pop("regola", None)
            if not e["colonna"] or not e["regole"]:
                st.caption("Nessuna regola: %s" % ("si usa tutta la descrizione." if info == "esercente"
                                                   else "l'informazione resta vuota."))
                continue
            if e["colonna"] not in cache:
                cache[e["colonna"]] = formati.righe_per_estrattori(proc["nome_file"], proc["dati"], s, e["colonna"],
                                                                   titolari)
            trovati, tot, esempi = estrattori.prova_regole(info, e["regole"], cache[e["colonna"]])
            st.caption("Trovato in **%d su %d** movimenti." % (trovati, tot))
            for da, val in esempi:
                st.markdown("<small>%s → <b>%s</b></small>" % (_html(da[:80]), _html(val)), unsafe_allow_html=True)
    _conteggio(*_prova(proc, c))
    return True


def _html(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _passo_conto(proc, c):
    s = proc["scelte"]
    righe = _righe(proc)
    st.markdown("#### Il formato")
    a, b, cc = st.columns(3)
    s["nome"] = a.text_input("Nome del formato", value=s["nome"], key=_k(proc, "nome"),
                             placeholder="es. %s – Lista movimenti (%s)" % (s["banca"] or "Banca",
                                                                         Path(proc["nome_file"]).suffix[1:].upper()))
    s["banca"] = b.text_input("Banca", value=s["banca"], key=_k(proc, "banca"),
                              help="Serve a proporre il conto quando importi un file.")
    tipi_conto = list(formati.TIPI_CONTO)
    s["tipo_conto"] = cc.selectbox("Tipo di conto", tipi_conto, index=tipi_conto.index(s["tipo_conto"]),
                                   format_func=formati.TIPI_CONTO.get, key=_k(proc, "tipo_conto"))
    testa = formati.testa(righe, s["riga"])
    if testa:
        st.markdown("#### Testa del file")
        st.caption("Con il periodo l'app sa fin dove arriva il file (copertura per i budget); con il saldo controlla "
                   "la quadratura dei saldi.")
        campi = [None] + list(formati.CAMPI_TESTA)
        for n, etichetta, valore in testa:
            x, y = st.columns([3, 2])
            x.markdown("Riga %d · %s" % (n, ("<b>%s</b> %s" % (_html(etichetta), _html(valore or ""))) if etichetta
                                          else _html(valore or "")), unsafe_allow_html=True)
            v = s["testa"].get(n)
            s["testa"][n] = y.selectbox("Significato della riga %d" % n, campi, index=campi.index(v) if v in campi else 0,
                                        format_func=lambda k, n=n: "Non usare" if k is None else
                                        formati.CAMPI_TESTA[k] + _proposto(proc, "testa.%d" % n, k),
                                        key=_k(proc, "testa_%d" % n), label_visibility="collapsed")
    fl, err = _prova(proc, c)
    st.markdown("#### Anteprima dei movimenti")
    if fl:
        md = fl.metadati
        dettagli = []
        if md.get("periodo_a"):
            dettagli.append("copertura del file fino al **%s** (dal periodo)" % md["periodo_a"])
        if md.get("saldo_alla_data") or md.get("saldo_finale"):
            saldo = imp._saldo(md.get("saldo_alla_data") or md.get("saldo_finale"))
            dettagli.append("saldo **%s**%s" % (euro(saldo) if saldo is not None else "non leggibile",
                                                (" al %s" % md["data_saldo"]) if md.get("data_saldo") else ""))
        if md.get("numero") or md.get("iban"):
            dettagli.append("conto %s" % (md.get("iban") or md.get("numero")))
        if dettagli:
            st.markdown("Dalla testa del file: " + " · ".join(dettagli) + ".")
        uscite = sum(m["importo_cent"] or 0 for m in fl.movimenti if (m["importo_cent"] or 0) < 0)
        entrate = sum(m["importo_cent"] or 0 for m in fl.movimenti if (m["importo_cent"] or 0) > 0)
        k = st.columns(4)
        k[0].metric("Movimenti letti", len(fl.movimenti))
        k[1].metric("Righe scartate", len(fl.scartate))
        k[2].metric("Uscite", euro(uscite))
        k[3].metric("Entrate", euro(entrate))
        st.dataframe(pd.DataFrame([{
            "Data": m["data"], "Tipo": formati.TIPI_LEGGIBILI.get(m["tipo"], m["tipo"]),
            "Esercente o controparte": m.get("esercente") or m.get("controparte_nome") or m.get("controparte_iban") or "",
            "Importo": euro(m["importo_cent"]), "Descrizione": m["descrizione"]} for m in fl.movimenti]),
            hide_index=True, width="stretch", height=min(38 + 35 * len(fl.movimenti), 420))
        if fl.scartate:
            st.caption("Righe scartate: " + "; ".join("riga %d: %s" % x for x in fl.scartate[:10]))
    _conteggio(fl, err)
    profilo = formati.componi(s, righe, proc["nome_file"])
    errori = formati.valida(profilo)
    for e in errori:
        st.error("Non si puo' salvare: %s." % e)
    altri = formati.conflitti(profilo, escludi=proc["base"])
    if altri:
        st.warning("Anche questi formati riconoscerebbero gli stessi file: %s. I formati dell'app vengono provati "
                   "per primi; tra i tuoi, il primo salvato." % ", ".join(n for _, n in altri),
                   icon=":material/warning:")
    if proc["base"]:
        st.caption("Salvando, «%s» viene ritirato: non riconosce piu' i file nuovi e resta per gli import gia' fatti."
                   % cfg.profili()[proc["base"]].get("nome"))
    if st.button("Salva il formato" + (" e continua l'import" if proc["origine"] == "importa" else ""),
                 type="primary", disabled=bool(errori) or not (fl and fl.movimenti), key="fmt_salva"):
        fid = servizi.salva_formato(c, profilo, sostituisce=proc["base"])
        st.session_state["fmt_esito"] = "Formato «%s» salvato%s." % (
            profilo["nome"], ": ora il file si importa come gli altri" if proc["origine"] == "importa" else "")
        st.session_state["fmt_ultimo"] = fid
        chiudi()
        st.rerun()
    return False


# ---------------------------------------------------------------------------
# Pagina Importa: file non riconosciuto
# ---------------------------------------------------------------------------
def elenco_formati_testo():
    """'Credem, ING ...' per il messaggio di file non riconosciuto."""
    app = ["Credem", "Crédit Agricole", "Fineco (Excel)", "ING conto (Excel o CSV)", "ING carta di credito",
           "Trade Republic", "PayPal (report attivita' in CSV)"]
    tuoi = [p["nome"] for p in cfg.profili().values() if p.get("utente") and p.get("stato") != "ritirato"]
    return "Formati dell'app: %s.%s" % (", ".join(app), (" Formati tuoi: %s." % ", ".join(tuoi)) if tuoi else "")


def file_non_riconosciuto(nome_file, dati, chiave):
    """Errore e pulsante per configurare il formato (pagina Importa)."""
    st.error("**%s**: nessun formato riconosce le colonne di questo file. %s" % (nome_file, elenco_formati_testo()))
    st.markdown("**Vuoi insegnare all'app a leggere questo file?** In sei passi indichi dove sono date, importi e "
                "descrizioni: a ogni passo l'app propone una risposta e tu la confermi o la correggi.")
    if st.button("Configura questo formato", key="fmt_avvia_%s" % chiave, icon=":material/tune:"):
        avvia(nome_file, dati, "importa")
        st.rerun()


def esito():
    if msg := st.session_state.pop("fmt_esito", None):
        st.success(msg, icon=":material/check_circle:")


# ---------------------------------------------------------------------------
# Pagina Formati di import
# ---------------------------------------------------------------------------
def formati_import():
    st.title("Formati di import")
    st.caption("Come l'app legge gli export delle banche. I tuoi formati restano nel database cifrato e nel backup.")
    c = conn()
    esito()
    if attiva("formati"):
        procedura(c)
        return
    _nuovo_formato()
    _tuoi_formati(c)
    _causali(c)
    _formati_app(c)


def _nuovo_formato():
    with st.expander("Nuovo formato da un file d'esempio", icon=":material/add:"):
        f = st.file_uploader("File esportato dalla banca (CSV o Excel)", type=["csv", "xlsx", "txt"],
                             key="fmt_nuovo_file")
        if f is not None:
            try:
                from ..lettore import leggi
                fl = leggi(f.name, (), f.getvalue())
                st.info("Questo file e' gia' riconosciuto da «%s»: puoi importarlo dalla pagina Importa. Se il formato "
                        "e' cambiato, introduci le modifiche come nuova versione." % fl.profilo.get("nome"))
            except FileNonRiconosciuto:
                pass
            except Exception:
                pass
            if st.button("Configura il formato", key="fmt_nuovo_avvia", type="primary"):
                avvia(f.name, f.getvalue(), "formati")
                st.rerun()


def _tuoi_formati(c):
    st.subheader("I tuoi formati")
    st.caption("Un formato non si modifica: le modifiche diventano una nuova versione e la vecchia viene ritirata. "
               "Si elimina solo un formato che nessun import usa.")
    elenco = formati.elenco(c)
    if not elenco:
        st.caption("Nessun formato tuo. Si crea dalla pagina Importa, quando carichi un file che l'app non "
                   "riconosce, oppure qui sopra.")
        return
    nomi = {f["id"]: f["nome"] for f in elenco}
    st.dataframe(pd.DataFrame([{
        "Nome": f["nome"], "Tipo di conto": formati.TIPI_CONTO.get(f["tipo_conto"], f["tipo_conto"]),
        "Stato": "Attivo" if f["stato"] == "attivo" else "Ritirato" + (
            "" if not any(x["sostituisce"] == f["id"] for x in elenco) else
            " · sostituito da «%s»" % next(x["nome"] for x in elenco if x["sostituisce"] == f["id"])),
        "Usato da": ("%d %s · %d %s" % (f["conti"], "conto" if f["conti"] == 1 else "conti", f["import"],
                                         "import") if f["import"] else "nessun import"),
        "Creato il": (f["creato_il"] or "")[:10]} for f in elenco]), hide_index=True, width="stretch")
    scelto = st.selectbox("Formato", [f["id"] for f in elenco], format_func=nomi.get, key="fmt_scelto",
                          index=next((i for i, f in enumerate(elenco) if f["id"] == st.session_state.get(
                              "fmt_ultimo")), 0))
    f = next(x for x in elenco if x["id"] == scelto)
    p = f["profilo"]
    with st.container(border=True, key="scheda_formato_scelto"):
        st.markdown("**%s** · %s · colonne: %s" % (f["nome"], formati.TIPI_CONTO.get(p.get("tipo_conto")),
                                                    " · ".join(p.get("firma") or [])))
        if f["sostituisce"] in nomi:
            st.caption("Nuova versione di «%s»." % nomi[f["sostituisce"]])
        a, b, cc = st.columns(3)
        with a:
            nuovo = st.file_uploader("File d'esempio per la nuova versione", type=["csv", "xlsx", "txt"],
                                     key="fmt_versione_file_%s" % scelto)
            if st.button("Introduci modifiche come nuova versione", key="fmt_versione_%s" % scelto,
                         disabled=nuovo is None, width="stretch"):
                avvia(nuovo.name, nuovo.getvalue(), "formati", base=scelto)
                st.rerun()
        with b:
            if f["stato"] == "attivo":
                if st.button("Ritira", key="fmt_ritira_%s" % scelto, width="stretch",
                             help="Non riconosce piu' i file nuovi; resta per gli import gia' fatti."):
                    servizi.ritira_formato(c, scelto)
                    st.session_state["fmt_esito"] = "Formato «%s» ritirato." % f["nome"]
                    st.rerun()
            elif st.button("Riattiva", key="fmt_riattiva_%s" % scelto, width="stretch"):
                servizi.riattiva_formato(c, scelto)
                st.session_state["fmt_esito"] = "Formato «%s» di nuovo attivo." % f["nome"]
                st.rerun()
        with cc:
            conferma = st.checkbox("Confermo l'eliminazione", key="fmt_elimina_ok_%s" % scelto,
                                   disabled=bool(f["import"]))
            if st.button("Elimina", key="fmt_elimina_%s" % scelto, width="stretch",
                         disabled=bool(f["import"]) or not conferma,
                         help=("Usato da %d import: annullali prima (pagina Importa), oppure ritira il formato."
                               % f["import"]) if f["import"] else None):
                servizi.elimina_formato(c, scelto)
                st.session_state["fmt_esito"] = "Formato «%s» eliminato." % f["nome"]
                st.rerun()
        if f["import"]:
            st.caption("Per correggere un formato configurato male: annulla i suoi import, eliminalo, crealo di nuovo "
                       "e reimporta i file.")


def _causali(c):
    st.subheader("Causali senza tipo")
    st.caption("Causali che un formato non conosce, viste nei tuoi import: quei movimenti sono «Altro» e vanno in "
               "Revisione. Il tipo che scegli vale per i prossimi import; i movimenti gia' importati restano come sono.")
    profili = {k: p for k, p in cfg.profili().items() if cfg.accetta_causali(p)}
    conteggi = {k: formati.causali_senza_tipo(c, k) for k in profili}
    aggiunte = {k: formati.causali_aggiunte(c, k) for k in profili}
    ordine = sorted(profili, key=lambda k: (not conteggi[k], not aggiunte[k], profili[k].get("utente", False),
                                            profili[k].get("nome", k)))
    scelto = st.selectbox("Formato", ordine, key="fmt_causali_formato", format_func=lambda k: "%s%s%s" % (
        profili[k].get("nome", k), " · tuo" if profili[k].get("utente") else " · dell'app",
        (" · %d senza tipo" % len(conteggi[k])) if conteggi[k] else ""))
    tipi = [t for t in formati.TIPI_LEGGIBILI if t != "altro"]
    if not conteggi[scelto]:
        st.caption("Nessuna causale senza tipo negli import di questo formato.")
    for i, (causale, n, esempio) in enumerate(conteggi[scelto]):
        a, b, cc = st.columns([3, 2, 1])
        a.markdown("**%s** · %d %s  \n%s" % (_html(causale), n, "movimento" if n == 1 else "movimenti",
                                            _html(esempio[:90])), unsafe_allow_html=True)
        tipo = b.selectbox("Tipo per %s" % causale, tipi, index=None, format_func=formati.TIPI_LEGGIBILI.get,
                           placeholder="Scegli un tipo…", key="fmt_caus_tipo_%s_%d" % (scelto, i),
                           label_visibility="collapsed")
        if cc.button("Aggiungi", key="fmt_caus_agg_%s_%d" % (scelto, i), disabled=tipo is None, width="stretch"):
            servizi.aggiungi_causale(c, scelto, causale, tipo)
            st.session_state["fmt_esito"] = "«%s» sara' «%s» dai prossimi import." % (
                causale, formati.TIPI_LEGGIBILI[tipo])
            st.rerun()
    if aggiunte[scelto]:
        st.markdown("**Gia' aggiunte**")
        for j, (causale, tipo, quando, superata) in enumerate(aggiunte[scelto]):
            a, b = st.columns([5, 1])
            a.markdown("«%s» → %s, dal %s%s" % (_html(causale), formati.TIPI_LEGGIBILI.get(tipo, tipo),
                                                (quando or "")[:10],
                                                " · superata: ora la traduce il formato dell'app" if superata else ""),
                       unsafe_allow_html=True)
            if b.button("Togli", key="fmt_caus_togli_%s_%d" % (scelto, j), width="stretch"):
                servizi.togli_causale(c, scelto, causale)
                st.rerun()


def _formati_app(c):
    app = cfg.profili_app()
    with st.expander("Formati dell'app (%d) · sola lettura" % len(app)):
        for k, p in app.items():
            n = len(formati.causali_aggiunte(c, k))
            st.markdown("- %s%s" % (p.get("nome", k), (" · %d %s" % (n, "causale aggiunta" if n == 1
                                                                      else "causali aggiunte")) if n else ""))
