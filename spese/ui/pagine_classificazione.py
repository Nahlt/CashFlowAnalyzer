"""Classificazione automatica (modello locale e Claude con approvazione) e regole."""
import pandas as pd
import streamlit as st

from .. import configurazione as cfg
from .. import attributi as attr
from .. import llm, servizi
from ..db import leggi_impostazione, scrivi_impostazione, transazione
from .comune import chiave_tabella, conn, esegui_ricalcolo, scarica_modello_locale, testo

MODI = {"locale": "Prima il modello locale, poi Claude solo per le voci incerte",
        "claude": "Direttamente Claude"}


MOTORI = {"modello_locale": "Modello locale", "claude": "Claude", "utente": "Confermata da te"}
FONTI = {"utente": "Confermata da te", "regola utente": "Tua regola", "storico": "Storico delle tue scelte",
         "regola": "Regola", "mcc": "Codice MCC", "marchio": "Marchio", "tipo movimento": "Tipo di movimento",
         "modello_locale": "Modello locale", "claude": "Claude"}


def _nome_motore(motore, modello):
    if motore == "claude":
        return "%s" % llm.MODELLI_CLAUDE.get(modello, "Claude · %s" % modello)
    return "Modello locale · %s" % modello


def _quando(ts):
    return "%s/%s %s" % (ts[8:10], ts[5:7], ts[11:16]) if ts else ""


def _etichetta_prova(p):
    stato = {"da_confermare": "da confermare", "applicata": "applicata", "non_applicata": "non applicata"}[p["stato"]]
    return "#%d · %s · %s · %d esercenti · %s%s" % (
        p["id"], _quando(p["ts"]), _nome_motore(p["motore"], p["modello"]), p["voci"], stato,
        " · gia' classificati" if p["origine"] == "confronto" else "")


def _scegli_modello_locale(c, chiave):
    """Modello locale da usare (l'elenco si cambia in Impostazioni); la scelta resta come predefinita."""
    if st.session_state.get("modello_installato"):
        st.success("Modello %s installato." % st.session_state.pop("modello_installato"))
    elenco = llm.modelli_locali(c)
    attuale = llm.ollama_config(c)["modello"]
    if len(elenco) == 1:
        st.caption("Ollama attivo · modello locale: `%s`" % attuale)
        return attuale
    with st.columns([1, 2])[0]:
        mod = st.selectbox("Modello locale", elenco, index=elenco.index(attuale), key=chiave,
                           help="L'elenco si cambia in Impostazioni > Modello locale")
    if mod != attuale:
        scrivi_impostazione(c, "ollama_modello", mod)
    return mod


def _scegli_modello_claude(c, chiave, salva=True):
    """Modello di Claude da usare; con salva=True la scelta diventa quella predefinita (anche in Impostazioni)."""
    attuale = llm.modello_claude(c)
    opz = llm.modelli_claude(c)
    mod = st.selectbox("Modello di Claude", opz, index=opz.index(attuale), key=chiave,
                       format_func=lambda m: llm.MODELLI_CLAUDE.get(m, m),
                       help="L'elenco si cambia in Impostazioni > Claude")
    if salva and mod != attuale:
        scrivi_impostazione(c, "claude_modello", mod)
    return mod


def _ritenta(c):
    st.caption("Scegli gli esercenti gia' classificati da rimettere in gioco: perdono la categoria e tornano tra "
               "quelli da classificare, pronti per il modello locale o per la coda di Claude (anche se gia' inviati). "
               "Le risposte precedenti restano nel registro qui sotto.")
    df = pd.DataFrame(servizi.esercenti_classificati(c))
    if df.empty:
        st.caption("Nessun esercente classificato dal modello locale o da Claude.")
        return
    a, b, d = st.columns([2, 2, 2])
    motori = a.multiselect("Classificati da", list(MOTORI), default=["modello_locale", "claude"],
                           format_func=MOTORI.get, key="rit_motori")
    df["cat"] = [cfg.etichetta((a, x)) if isinstance(x, str) else "" for a, x in zip(df["l1"], df["l2"])]
    categorie = b.multiselect("Categoria", sorted(df["cat"].unique()), placeholder="Tutte", key="rit_cat")
    cerca = d.text_input("Cerca esercente", key="rit_cerca")
    f = df[df["motore"].isin(motori)]
    if categorie:
        f = f[f["cat"].isin(categorie)]
    if cerca.strip():
        f = f[f["chiave"].str.contains(cerca.strip(), case=False, regex=False)]
    if f.empty:
        st.caption("Nessun esercente con questi filtri.")
        return
    tutte = st.checkbox("Seleziona tutti i %d esercenti filtrati" % len(f), key="rit_tutte")
    vista = pd.DataFrame({"Riapri": tutte, "Esercente": f["chiave"], "Categoria": f["cat"],
                          "Classificato da": f["motore"].map(MOTORI), "Modello": f["modello"].fillna(""),
                          "Confidenza": f["confidenza"].round(2), "Movimenti": f["movimenti"]})
    ed = st.data_editor(vista, hide_index=True, width="stretch", height=min(420, 38 + 35 * len(vista)),
                        disabled=["Esercente", "Categoria", "Classificato da", "Modello", "Confidenza", "Movimenti"],
                        column_config={"Riapri": st.column_config.CheckboxColumn("Riapri", width="small")},
                        key="rit_ed_%d_%d_%s" % (len(f), int(tutte), hash(tuple(f["chiave"])) % 10 ** 8))
    scelte = list(ed.loc[ed["Riapri"], "Esercente"])
    confermate = int((f[f["chiave"].isin(scelte)]["motore"] == "utente").sum())
    if confermate:
        st.warning("%d degli esercenti scelti li avevi confermati tu: perderai anche quella conferma." % confermate)
    if st.button("Rimetti in classificazione %d esercenti" % len(scelte), disabled=not scelte, key="rit_btn"):
        n = servizi.riapri_esercenti(c, scelte)
        st.session_state.pop("rit_tutte", None)
        esegui_ricalcolo("%d esercenti rimessi in classificazione" % n)
        st.rerun()


def automatica():
    st.title("Classificazione automatica")
    c = conn()
    tolte = llm.pulisci_coda(c)
    if tolte:
        st.toast("%d voci tolte dalla coda: nel frattempo hanno avuto una categoria" % tolte)
    aperte = llm.chiavi_in_prova(c)
    tutte = llm.chiavi_da_classificare(c)
    voci = [v for v in tutte if v["chiave"] not in aperte]
    st.markdown("Esercenti ancora senza categoria: **%d** (gli stessi della scheda *Esercenti da classificare* "
                "in Revisione)%s." % (len(tutte), ", di cui %d con una risposta da confermare" % (len(tutte) - len(voci))
                                      if len(tutte) > len(voci) else ""))
    if st.session_state.get("prova_msg"):
        st.success(st.session_state.pop("prova_msg"), icon=":material/fact_check:")
    solo = _selezione_revisione(c, len(tutte), aperte)
    _prove_aperte(c, solo)
    if solo is not None:
        voci = [v for v in voci if v["chiave"] in solo]

    st.subheader("1. Con cosa iniziare")
    modo = leggi_impostazione(c, "prima_fase", "locale")
    scelto = st.radio("Con cosa iniziare", list(MODI), index=list(MODI).index(modo) if modo in MODI else 0,
                      format_func=MODI.get, horizontal=True, key="prima_fase", label_visibility="collapsed")
    if scelto != modo:
        scrivi_impostazione(c, "prima_fase", scelto)
        modo = scelto
    st.caption("**Modello locale**: non esce nulla dal PC, ma sul mini PC servono alcuni minuti ogni 20 esercenti. "
               "**Direttamente Claude**: pochi secondi per lotto e in genere piu' preciso, ma usa il credito API; "
               "esce solo il nome dell'esercente, dopo che hai visto l'anteprima e premuto Invia. Le voci "
               "dell'area salute e quelle con dati identificativi non escono mai.")

    if modo == "locale":
        st.subheader("2. Modello locale")
        _sezione_locale(c, voci, "Classifica %d esercenti con il modello locale" % len(voci))
        st.subheader("3. Claude per le voci rimaste incerte")
    else:
        st.subheader("2. Claude")
    _sezione_claude(c, solo)
    if modo == "claude":
        bloccate = {r[0] for r in c.execute("SELECT chiave FROM candidati_invio WHERE stato = 'bloccato'")}
        solo_locali = [v for v in voci if v["chiave"] in bloccate]
        if solo_locali:
            with st.expander("%d voci che non possono uscire: modello locale" % len(solo_locali)):
                st.caption("Bloccate dal controllo di sicurezza: falle classificare dal modello locale oppure "
                           "confermale a mano in **Revisione**.")
                _sezione_locale(c, solo_locali, "Classifica %d voci bloccate con il modello locale" % len(solo_locali))

    st.divider()
    st.subheader("Strumenti")
    with st.expander("Prova un modello su esercenti gia' classificati (per confrontare l'accuratezza)"):
        _prova_confronto(c)
    with st.expander("Confronto tra prove"):
        _confronto(c)
    with st.expander("Rimetti in classificazione esercenti gia' classificati"):
        _ritenta(c)
    _registro(c)


def _selezione_revisione(c, n_tutti, aperte=()):
    """Esercenti scelti in Revisione: si puo' lavorare solo su quelli (predefinito) o su tutti. Restituisce
    l'insieme delle chiavi, o None per tutti."""
    scelti = llm.selezione(c)
    if not scelti:
        if leggi_impostazione(c, llm.CHIAVE_SELEZIONE, None):
            llm.imposta_selezione(c, [])                 # tutti classificati nel frattempo: selezione finita
        return None
    with st.container(border=True):
        a, b = st.columns([4, 1], vertical_alignment="center")
        quali = a.radio("Su quali esercenti lavorare", ["scelti", "tutti"], horizontal=True, key="ambito_auto",
                        format_func={"scelti": "Solo i %d scelti in Revisione" % len(scelti),
                                     "tutti": "Tutti gli esercenti da classificare (%d)" % n_tutti}.get)
        if b.button("Togli la selezione", key="togli_sel"):
            llm.imposta_selezione(c, [])
            st.session_state.pop("ambito_auto", None)
            st.rerun()
        in_attesa = len(scelti & set(aperte))
        if in_attesa and quali == "scelti":
            st.caption("%d degli esercenti scelti hanno gia' una risposta da confermare: la trovi qui sotto, "
                       "filtrata sugli esercenti scelti." % in_attesa)
        with st.expander("Esercenti scelti"):
            st.caption(", ".join(sorted(scelti)))
    return scelti if quali == "scelti" else None


def _sezione_locale(c, voci, etichetta):
    ok, info = llm.ollama_disponibile(c)
    oc = llm.ollama_config(c)
    if not ok:
        st.info("Ollama non risponde su %s. Installalo e avvialo per usare il modello locale (vedi MANUALE.md)."
                % oc["url"], icon=":material/memory:")
        return
    sigla = abs(hash(etichetta)) % 10 ** 6
    modello = _scegli_modello_locale(c, "mod_locale_%d" % sigla)
    presente = llm.installato(modello, info)
    if not presente:
        scarica_modello_locale(c, modello, "scarica_%s_%d" % (modello, sigla))
    if voci and presente and st.button(etichetta, type="primary", key="btn_locale_%d" % len(voci)):
        barra = st.progress(0.0, text="Classificazione in corso…")
        try:
            pid, ris = llm.classifica_locale(c, [{"chiave": v["chiave"], "testo": v["chiave"], "paese": v["paese"],
                                                  "tipo": "pagamento"} for v in voci],
                                             lambda f, t: barra.progress(f / t, text="%d di %d" % (f, t)))
            st.session_state["prova_msg"] = ("Modello locale: %d risposte su %d esercenti. Controllale qui sopra e "
                                             "applica quelle che ti convincono." % (len(ris), len(voci)))
            st.rerun()
        except Exception as e:
            st.error("Errore del modello locale: %s. Le risposte gia' arrivate sono nella prova qui sopra." % e)


def _sezione_claude(c, solo=None):
    """solo: chiavi a cui limitarsi (esercenti scelti in Revisione), None = tutti."""
    if not leggi_impostazione(c, "titolari", []):
        st.warning("Invio a Claude disattivato: indica prima i titolari dei conti in **Impostazioni**. Servono al "
                   "controllo di sicurezza per non far uscire i vostri nomi.", icon=":material/lock:")
        return
    # a) mettere in coda
    stima = llm.stima_coda(c, solo)
    nuove = len(stima["nuove"])
    parti = []
    if nuove:
        b = stima["bloccate"]
        parti.append("**%d** da mettere in coda" % nuove + (" (%d %s dal controllo di sicurezza, non %s)" % (
            b, "bloccata" if b == 1 else "bloccate", "uscira'" if b == 1 else "usciranno") if b else ""))
    if stima["in_coda"]:
        parti.append("%d gia' in coda" % stima["in_coda"])
    if stima.get("in_prova"):
        parti.append("%d con una risposta da confermare" % stima["in_prova"])
    st.caption(("Esercenti scelti in Revisione per Claude: " if solo is not None else "Esercenti senza categoria per Claude: ")
               + (" · ".join(parti) if parti else "nessuno da aggiungere") + ".")
    if nuove and st.button("Metti in coda %d esercenti" % nuove, key="btn_prepara"):
        llm.prepara_candidati(c, solo)
        st.rerun()
    if stima["senza_risposta"]:
        k = len(stima["senza_risposta"])
        a, b = st.columns([3, 1], vertical_alignment="center")
        a.caption("%d voci gia' inviate a Claude sono rimaste senza categoria (nessuna risposta valida o confidenza "
                  "bassa: in Revisione le trovi con la proposta di Claude). Puoi confermarle li' o rimetterle in coda." % k)
        if b.button("Rimetti in coda", key="btn_rimetti"):
            llm.rimetti_in_coda(c, stima["senza_risposta"])
            st.rerun()

    # b) controllare e inviare
    cand = pd.DataFrame([dict(r) for r in c.execute(
        "SELECT chiave, testo, paese, stato, motivo FROM candidati_invio WHERE stato IN ('approvato','escluso','bloccato') "
        "ORDER BY chiave")])
    if solo is not None and not cand.empty:
        cand = cand[cand["chiave"].isin(solo)].reset_index(drop=True)
    if cand.empty:
        if not nuove:
            st.info("La coda e' vuota." if solo is None else "Nessuno degli esercenti scelti e' in coda.",
                    icon=":material/inbox:")
        return
    coda = cand[cand["stato"] != "bloccato"].reset_index(drop=True)
    bloccati = cand[cand["stato"] == "bloccato"]
    if not coda.empty:
        st.caption("Togli la spunta alle voci che preferisci classificare a mano e, se serve, accorcia il testo da "
                   "inviare (es. togliendo la citta'). Le scelte si salvano quando premi Invia.")
        giro = st.session_state.get("coda_giro", 0)
        vista = pd.DataFrame({"Invia": coda["stato"] == "approvato", "Esercente": coda["chiave"],
                              "Testo da inviare": coda["testo"]})
        ed = st.data_editor(vista, hide_index=True, width="stretch", disabled=["Esercente"], key=chiave_tabella("ed_coda_%d" % giro, coda["chiave"]),
                            height=min(460, 38 + 35 * len(vista)),
                            column_config={"Invia": st.column_config.CheckboxColumn("Invia", width="small"),
                                           "Testo da inviare": st.column_config.TextColumn("Testo da inviare (modificabile)")})
        scelte = []
        for (_, r), (_, e) in zip(coda.iterrows(), ed.iterrows()):
            if e["Invia"]:
                scelte.append({"chiave": r["chiave"], "testo": testo(e["Testo da inviare"]) or r["chiave"],
                               "paese": r["paese"]})
        lotti = llm.lotti_claude(c, scelte=scelte) if scelte else []
        n_voci = sum(len(l[0]) for l in lotti)
        with st.expander("Anteprima esatta di cio' che verrebbe inviato (%d voci in %d richieste)" % (n_voci, len(lotti))):
            for k, (_, _, utente) in enumerate(lotti, 1):
                if len(lotti) > 1:
                    st.markdown("Richiesta %d di %d" % (k, len(lotti)))
                st.code(utente, language="text")
        m1, m2 = st.columns([1, 2], vertical_alignment="bottom")
        with m1:
            modello = _scegli_modello_claude(c, "mod_claude_coda")
        if not llm.chiave_api():
            st.warning("Manca la chiave API di Claude: inseriscila in **Impostazioni**.")
        elif m2.button("Invia %d voci a %s" % (n_voci, llm.MODELLI_CLAUDE.get(modello, modello)), type="primary",
                       disabled=not n_voci, key="btn_invia"):
            # salva spunte e testi, poi invia quelle spuntate
            with transazione(c):
                for (_, r), (_, e) in zip(coda.iterrows(), ed.iterrows()):
                    nuovo = testo(e["Testo da inviare"])
                    servizi.candidato(c, r["chiave"], "approvato" if e["Invia"] else "escluso",
                                      nuovo if nuovo and nuovo != testo(r["testo"]) else None)
            st.session_state["coda_giro"] = giro + 1
            barra = st.progress(0.0, text="Invio a Claude…")
            try:
                pid, ris = llm.invia_claude(c, modello=modello, solo=solo, avanzamento=lambda f, t: barra.progress(
                    f / t, text="Richiesta %d di %d" % (f, t)))
                st.session_state["prova_msg"] = ("%s: %d risposte su %d esercenti. Controllale qui sopra e applica "
                                                 "quelle che ti convincono." % (llm.MODELLI_CLAUDE.get(modello, modello),
                                                                                 len(ris), n_voci))
                st.rerun()
            except Exception as e:
                st.error("Invio interrotto: %s. Le risposte dei lotti gia' tornati sono nella prova qui sopra; puoi "
                         "riprovare con le voci rimaste." % e)
    if not bloccati.empty:
        with st.expander("%d voci bloccate dal controllo di sicurezza (non escono)" % len(bloccati)):
            st.dataframe(bloccati[["chiave", "motivo"]].rename(columns={"chiave": "Esercente", "motivo": "Motivo"}),
                         hide_index=True, width="stretch")


def _prove_aperte(c, solo=None):
    aperte = llm.prove(c, "da_confermare")
    if solo is not None:
        aperte = [p for p in aperte if any(r["chiave"] in solo and not r["applicato"]
                                           for r in llm.risultati_prova(c, p["id"]))]
    if not aperte:
        return
    st.subheader("Risposte da confermare")
    st.caption("Le risposte non sono ancora applicate. Togli la spunta a quelle che non ti convincono e applica le "
               "altre; quelle sotto la soglia di confidenza diventano proposte in Revisione. Se non applichi, la "
               "prova resta comunque nel confronto tra prove.")
    for p in aperte:
        _prova_da_confermare(c, p, solo)


def _prova_da_confermare(c, p, solo=None):
    soglia = cfg.impostazioni()["modello_locale"]["soglia_confidenza"]
    ris = pd.DataFrame(llm.risultati_prova(c, p["id"]))
    if ris.empty:
        llm.chiudi_prova(c, p["id"])
        return
    ris = ris[ris["applicato"] == 0].reset_index(drop=True)          # quelle gia' applicate non si ripropongono
    filtrata = solo is not None
    if filtrata:
        ris = ris[ris["chiave"].isin(solo)].reset_index(drop=True)
    if ris.empty:
        return
    att = llm.categoria_attuale(c, set(ris["chiave"]))
    confronto = p["origine"] == "confronto"
    with st.container(border=True):
        st.markdown("**%s** · %s · %s%s" % (_nome_motore(p["motore"], p["modello"]), _quando(p["ts"]),
                                            "prova su esercenti gia' classificati" if confronto else "esercenti da classificare",
                                            " · solo gli esercenti scelti in Revisione" if filtrata else ""))
        validi = ris["l2"].notna()
        risposte = [(a, b) if isinstance(b, str) else None for a, b in zip(ris["l1"], ris["l2"])]
        attuali = [att.get(k, (None, None))[0] for k in ris["chiave"]]
        diverse = [bool(r) and a is not None and r != a for r, a in zip(risposte, attuali)]
        k = st.columns(4)
        k[0].metric("Esercenti", len(ris))
        k[1].metric("Con risposta", int(validi.sum()))
        k[2].metric("Sicure (≥ %.2f)" % soglia, int((ris["confidenza"].fillna(0) >= soglia).sum()))
        if confronto:
            confrontabili = sum(1 for r, a in zip(risposte, attuali) if r and a)
            uguali = sum(1 for r, a in zip(risposte, attuali) if r and a and r == a)
            k[3].metric("Uguali alla categoria attuale", "%d su %d" % (uguali, confrontabili))
            st.caption("Applicare cambia solo gli esercenti classificati dal modello locale o da Claude: le tue "
                       "conferme, le tue regole, le regole e i codici MCC prevalgono comunque.")
        solo_div = confronto and st.toggle("Solo le risposte diverse dalla categoria attuale", key="div_%d" % p["id"])
        vista = pd.DataFrame({
            "Applica": [bool(r) and not confronto for r in risposte],
            "Esercente": ris["chiave"], "Categoria attuale": [cfg.etichetta(a) if a else "" for a in attuali],
            "Risposta": [cfg.etichetta(r) if r else "nessuna risposta valida" for r in risposte],
            "Confidenza": pd.to_numeric(ris["confidenza"], errors="coerce").astype("float64"),
            "Nome riconosciuto": ris["esercente_normalizzato"].fillna("")})
        if not confronto:
            vista = vista.drop(columns=["Categoria attuale"])
        if solo_div:
            vista = vista[diverse].reset_index(drop=True)
        ed = st.data_editor(vista, hide_index=True, width="stretch", height=min(420, 38 + 35 * max(1, len(vista))),
                            disabled=[x for x in vista.columns if x != "Applica"], key="ed_prova_%d_%d" % (p["id"], solo_div),
                            column_config={"Applica": st.column_config.CheckboxColumn("Applica", width="small"),
                                           "Confidenza": st.column_config.NumberColumn("Confidenza", format="%.2f")})
        scelte = [e for e, a, r in zip(ed["Esercente"], ed["Applica"], ed["Risposta"]) if a and r != "nessuna risposta valida"]
        b = st.columns([1, 1, 1.4, 2.6])
        if b[0].button("Applica %d risposte" % len(scelte), type="primary", disabled=not scelte, key="appl_%d" % p["id"]):
            n = llm.applica_prova(c, p["id"], scelte, chiudi=not filtrata)
            esegui_ricalcolo("%d categorie applicate" % n)
            st.rerun()
        if filtrata:
            b[1].caption("Le risposte degli altri esercenti restano in attesa: le vedi scegliendo *Tutti*.")
        elif b[1].button("Non applicare", key="nonappl_%d" % p["id"],
                         help="Non cambia nulla; la prova resta nel confronto tra prove"):
            llm.chiudi_prova(c, p["id"])
            st.rerun()
        if p["motore"] == "claude" and not confronto and not filtrata and b[2].button(
                "Non applicare e rimetti in coda", key="rimetti_%d" % p["id"],
                help="Le voci tornano spuntate nella coda di Claude: puoi mandarle a un altro modello e confrontare"):
            llm.chiudi_prova(c, p["id"], rimetti_in_coda_claude=True)
            st.rerun()


def _prova_confronto(c):
    st.caption("Manda a un modello esercenti che hanno gia' una categoria (confermata da te, da una regola, dal "
               "codice MCC...) e confronta le risposte con quella categoria: e' il modo per misurare l'accuratezza "
               "dei modelli. Nulla cambia finche' non applichi la prova. Verso Claude valgono gli stessi controlli "
               "della coda: le voci dell'area salute e quelle con dati identificativi non escono.")
    df = pd.DataFrame(llm.esercenti_con_categoria(c))
    if df.empty:
        st.caption("Nessun esercente con una categoria.")
        return
    a, b, d = st.columns([2, 2, 2])
    fonti = sorted(df["fonte"].dropna().unique(), key=lambda x: list(FONTI).index(x) if x in FONTI else 99)
    pred = [f for f in ("utente", "regola utente", "storico") if f in fonti] or fonti
    scelte_f = a.multiselect("Categoria data da", fonti, default=pred, format_func=lambda x: FONTI.get(x, x), key="cf_fonti")
    df["etichetta"] = [cfg.etichetta(x) for x in df["cat"]]
    categorie = b.multiselect("Categoria", sorted(df["etichetta"].unique()), placeholder="Tutte", key="cf_cat")
    cerca = d.text_input("Cerca esercente", key="cf_cerca")
    f = df[df["fonte"].isin(scelte_f)]
    if categorie:
        f = f[f["etichetta"].isin(categorie)]
    if cerca.strip():
        f = f[f["chiave"].str.contains(cerca.strip(), case=False, regex=False)]
    if f.empty:
        st.caption("Nessun esercente con questi filtri.")
        return
    tutte = st.checkbox("Seleziona tutti i %d esercenti filtrati" % len(f), key="cf_tutte")
    vista = pd.DataFrame({"Prova": tutte, "Esercente": f["chiave"], "Categoria attuale": f["etichetta"],
                          "Data da": f["fonte"].map(lambda x: FONTI.get(x, x)), "Movimenti": f["movimenti"]})
    ed = st.data_editor(vista, hide_index=True, width="stretch", height=min(380, 38 + 35 * len(vista)),
                        disabled=["Esercente", "Categoria attuale", "Data da", "Movimenti"],
                        column_config={"Prova": st.column_config.CheckboxColumn("Prova", width="small")},
                        key="cf_ed_%d_%d_%s" % (len(f), int(tutte), hash(tuple(f["chiave"])) % 10 ** 8))
    chiavi = list(ed.loc[ed["Prova"], "Esercente"])
    if not chiavi:
        st.caption("Spunta gli esercenti da mettere alla prova.")
        return
    motore = st.radio("Con quale modello", ["claude", "locale"], horizontal=True, key="cf_motore",
                      format_func={"claude": "Claude", "locale": "Modello locale"}.get)
    if motore == "locale":
        ok, info = llm.ollama_disponibile(c)
        oc = llm.ollama_config(c)
        if not ok:
            st.info("Ollama non risponde su %s." % oc["url"], icon=":material/memory:")
            return
        modello = _scegli_modello_locale(c, "cf_mod_locale")
        if not llm.installato(modello, info):
            scarica_modello_locale(c, modello, "cf_scarica_%s" % modello)
        elif st.button("Prova %d esercenti con %s" % (len(chiavi), modello), type="primary", key="cf_locale"):
            paesi = dict(zip(df["chiave"], df["paese"]))
            barra = st.progress(0.0, text="Classificazione in corso…")
            try:
                pid, ris = llm.classifica_locale(c, [{"chiave": k, "testo": k, "paese": paesi.get(k), "tipo": "pagamento"}
                                                     for k in chiavi],
                                                 lambda x, t: barra.progress(x / t, text="%d di %d" % (x, t)),
                                                 origine="confronto")
                st.session_state["prova_msg"] = "Prova #%d: %d risposte, le trovi in cima alla pagina." % (pid, len(ris))
                st.rerun()
            except Exception as e:
                st.error("Errore del modello locale: %s" % e)
        return
    if not leggi_impostazione(c, "titolari", []):
        st.warning("Invio a Claude disattivato: indica prima i titolari dei conti in **Impostazioni**.")
        return
    with st.columns([1, 2])[0]:
        modello = _scegli_modello_claude(c, "mod_claude_prova", salva=False)
    lotti = llm.lotti_confronto(c, chiavi)
    n_voci = sum(len(l[0]) for l in lotti)
    if n_voci < len(chiavi):
        st.caption("%d esercenti bloccati dal controllo di sicurezza: non escono." % (len(chiavi) - n_voci))
    with st.expander("Anteprima esatta di cio' che verrebbe inviato (%d voci in %d richieste)" % (n_voci, len(lotti))):
        for i, (_, _, utente) in enumerate(lotti, 1):
            if len(lotti) > 1:
                st.markdown("Richiesta %d di %d" % (i, len(lotti)))
            st.code(utente, language="text")
    if not llm.chiave_api():
        st.warning("Manca la chiave API di Claude: inseriscila in **Impostazioni**.")
    elif st.button("Invia %d voci a %s (prova)" % (n_voci, llm.MODELLI_CLAUDE.get(modello, modello)), type="primary",
                   disabled=not n_voci, key="cf_invia"):
        barra = st.progress(0.0, text="Invio a Claude…")
        try:
            pid, ris = llm.prova_claude(c, chiavi, modello, avanzamento=lambda x, t: barra.progress(
                x / t, text="Richiesta %d di %d" % (x, t)))
            st.session_state["prova_msg"] = "Prova #%d: %d risposte, le trovi in cima alla pagina." % (pid, len(ris))
            st.session_state.pop("cf_tutte", None)
            st.rerun()
        except Exception as e:
            st.error("Invio interrotto: %s. Le risposte dei lotti gia' tornati sono nella prova in cima alla pagina." % e)


def _confronto(c):
    tutte = llm.prove(c)
    if not tutte:
        st.caption("Nessuna prova ancora. Ogni classificazione con il modello locale o con Claude ne crea una.")
        return
    etichette = {p["id"]: _etichetta_prova(p) for p in tutte}
    scelte = st.multiselect("Prove da confrontare", list(etichette), default=list(etichette)[:2],
                            format_func=etichette.get, key="cmp_prove")
    if not scelte:
        return
    per_id = {p["id"]: p for p in tutte}
    tabella, riep = llm.confronto(c, scelte)
    soglia = cfg.impostazioni()["modello_locale"]["soglia_confidenza"]
    pct = lambda n, d: "%d su %d (%d%%)" % (n, d, round(100 * n / d)) if d else "–"
    st.dataframe(pd.DataFrame([{
        "Prova": "#%d" % r["prova"], "Modello": _nome_motore(per_id[r["prova"]]["motore"], per_id[r["prova"]]["modello"]),
        "Esercenti": r["voci"], "Con risposta": r["risposte"],
        "Confidenza media": r["confidenza_media"], "Sicure (≥ %.2f)" % soglia: r["sopra_soglia"],
        "Uguali alla categoria attuale": pct(r["uguali"], r["confrontabili"]),
        "Uguali alle tue scelte": pct(r["uguali_tue"], r["tue"])} for r in riep]),
        hide_index=True, width="stretch",
        column_config={"Confidenza media": st.column_config.NumberColumn(format="%.2f")})
    st.caption("*Le tue scelte*: esercenti confermati da te, tue regole e storico delle tue decisioni. La "
               "categoria attuale di un esercente classificato da un modello non e' una verifica: per misurare "
               "l'accuratezza usa le prove su esercenti gia' classificati da te o dalle regole.")
    diverse = st.toggle("Solo gli esercenti dove le risposte non coincidono", key="cmp_div")
    righe = []
    for t in tabella:
        r = {"Esercente": t["chiave"], "Categoria attuale": cfg.etichetta(t["attuale"]) if t["attuale"] else "",
             "Data da": FONTI.get(t["fonte"], t["fonte"] or "")}
        valori = set()
        for pid in scelte:
            x = t["risposte"].get(pid)
            r["#%d risposta" % pid] = (cfg.etichetta((x["l1"], x["l2"])) if x["l2"] else "nessuna") if x else ""
            r["#%d conf." % pid] = float(x["confidenza"]) if x and x["l2"] and x["confidenza"] is not None else float("nan")
            if x:
                valori.add((x["l1"], x["l2"]))
        if t["attuale"]:
            valori.add(t["attuale"])
        if not diverse or len(valori) > 1:
            righe.append(r)
    if righe:
        st.dataframe(pd.DataFrame(righe), hide_index=True, width="stretch", height=min(460, 38 + 35 * len(righe)),
                     column_config={"#%d conf." % pid: st.column_config.NumberColumn(format="%.2f") for pid in scelte})
    else:
        st.caption("Le risposte coincidono su tutti gli esercenti.")
    with st.popover("Elimina prove"):
        da_togliere = st.multiselect("Prove da eliminare", list(etichette), format_func=etichette.get, key="cmp_elim")
        st.caption("Le categorie gia' applicate restano; si perde solo lo storico delle risposte.")
        if st.button("Elimina %d prove" % len(da_togliere), disabled=not da_togliere, key="cmp_elim_btn"):
            for pid in da_togliere:
                llm.elimina_prova(c, pid)
            st.session_state.pop("cmp_prove", None)
            st.rerun()


def _registro(c):
    with st.expander("Registro delle richieste (modello locale e Claude)"):
        reg = pd.DataFrame([dict(r) for r in c.execute(
            "SELECT id, ts, motore, modello, esito FROM registro_invii ORDER BY id DESC LIMIT 200")])
        if reg.empty:
            st.caption("Nessuna richiesta.")
        else:
            st.dataframe(reg, hide_index=True, width="stretch")
            rid = st.selectbox("Vedi una richiesta", list(reg["id"]))
            r = c.execute("SELECT richiesta, risposta FROM registro_invii WHERE id = ?", (int(rid),)).fetchone()
            st.code(r["richiesta"], language="text")
            st.code(r["risposta"], language="json")


CAMPI = {"chiave": "Esercente", "descrizione": "Descrizione"}
SEZ = {"uscite": "Uscite", "entrate": "Entrate", "risparmio": "Risparmio"}


def regole():
    st.title("Regole e tassonomia")
    c = conn()
    nomi = ["Esercenti gia' classificati", "Regole create da te", "Tassonomia"]
    prova = attr.natura_attiva(c)
    schede = st.tabs(nomi + (["Natura della spesa"] if prova else []))
    with schede[0]:
        _esercenti_classificati(c)
    with schede[1]:
        _regole_utente(c)
    with schede[2]:
        _tassonomia(c)
    if prova:
        with schede[3]:
            _natura_predefinita(c)


def _natura_predefinita(c):
    """Natura della spesa di ogni sottocategoria di uscita."""
    st.caption("La natura della spesa di ogni uscita viene dalla sua sottocategoria. Qui scegli quella di partenza; "
               "sul singolo movimento la cambi nella pagina Movimenti, e quella scelta li' prevale.")
    valori = attr.valori()
    pred = attr.predefiniti(c)
    tax = cfg.tassonomia()
    righe = [cat for l1 in tax.ordine_l1 for cat in tax.per_sezione("uscite") if cat[0] == l1]
    etichette = {"": "—", **valori}
    df = pd.DataFrame({"Categoria": [r[0] for r in righe], "Sottocategoria": [r[1] for r in righe],
                       "Natura della spesa": [valori.get(pred.get(r, ""), "—") for r in righe]})
    ed = st.data_editor(df, hide_index=True, width="stretch", disabled=["Categoria", "Sottocategoria"],
                        column_config={"Natura della spesa": st.column_config.SelectboxColumn(
                            options=list(etichette.values()), required=True)},
                        key=chiave_tabella("nat_pred", [(r, pred.get(r)) for r in righe]), height=min(640, 38 + 35 * len(df)))
    if st.button("Salva", type="primary", key="nat_pred_salva"):
        per_etichetta = {v: k for k, v in etichette.items()}
        n = 0
        with transazione(c):
            for (_, prima), (_, dopo) in zip(df.iterrows(), ed.iterrows()):
                if prima["Natura della spesa"] != dopo["Natura della spesa"]:
                    attr.imposta_predefinito(c, (dopo["Categoria"], dopo["Sottocategoria"]),
                                             per_etichetta.get(testo(dopo["Natura della spesa"]), ""))
                    n += 1
        if n:
            esegui_ricalcolo("Natura della spesa aggiornata per %d sottocategorie" % n)
        st.rerun()


def _esercenti_classificati(c):
    from .comune import opzioni_categorie
    tax = cfg.tassonomia()
    df = pd.DataFrame(servizi.esercenti_classificati(c))
    if df.empty:
        st.caption("Ancora nessuno: si popolano con le conferme in revisione e con la classificazione automatica.")
        return
    st.caption("Modifica la categoria o il nome normalizzato e salva: la modifica vale per tutti i movimenti "
               "dell'esercente e diventa una tua conferma. Per farli riclassificare in automatico usa "
               "**Rimetti in classificazione** (o la prova su esercenti gia' classificati) in Classificazione automatica.")
    a, b = st.columns([3, 2])
    cerca = a.text_input("Cerca esercente", key="ec_cerca")
    motori = b.multiselect("Classificati da", list(MOTORI), default=list(MOTORI), format_func=MOTORI.get, key="ec_mot")
    f = df[df["motore"].isin(motori)]
    if testo(cerca):
        f = f[f["chiave"].str.contains(testo(cerca), case=False, regex=False)]
    if f.empty:
        st.caption("Nessun esercente con questi filtri.")
        return
    opzioni = [cfg.etichetta(x) for x in opzioni_categorie()]
    per_etichetta = {cfg.etichetta(x): x for x in opzioni_categorie()}
    vista = pd.DataFrame({
        "Esercente": f["chiave"],
        "Categoria": [cfg.etichetta((a, x)) if tax.valida(a, x) else None for a, x in zip(f["l1"], f["l2"])],
        "Nome normalizzato": f["esercente_normalizzato"].fillna(""),
        "Classificato da": f["motore"].map(MOTORI), "Confidenza": f["confidenza"].round(2),
        "Movimenti": f["movimenti"], "Aggiornato il": f["aggiornata_il"].fillna("").str[:16].str.replace("T", " ")})
    ed = st.data_editor(vista, hide_index=True, width="stretch", key=chiave_tabella("ec_ed_%d" % st.session_state.get("ec_giro", 0), f["chiave"]),
                        height=min(520, 38 + 35 * len(vista)),
                        disabled=["Esercente", "Classificato da", "Confidenza", "Movimenti", "Aggiornato il"],
                        column_config={"Categoria": st.column_config.SelectboxColumn("Categoria", options=opzioni,
                                                                                     required=True)})
    cambiate = [(r["Esercente"], per_etichetta.get(testo(r["Categoria"])), testo(r["Nome normalizzato"]))
                for (_, r), (_, o) in zip(ed.iterrows(), vista.iterrows())
                if testo(r["Categoria"]) and (testo(r["Categoria"]) != testo(o["Categoria"])
                                              or testo(r["Nome normalizzato"]) != testo(o["Nome normalizzato"]))]
    if st.button("Salva %d modifiche" % len(cambiate), type="primary", disabled=not cambiate, key="ec_salva"):
        with transazione(c):
            for chiave, cat, nome in cambiate:
                servizi.aggiorna_esercente(c, chiave, cat, nome=nome)
        st.session_state["ec_giro"] = st.session_state.get("ec_giro", 0) + 1
        esegui_ricalcolo("%d esercenti aggiornati" % len(cambiate))
        st.rerun()
    st.markdown("**Movimenti di un esercente**: per dare a qualche movimento una categoria diversa da quella "
                "dell'esercente.")
    scelto = st.selectbox("Esercente", list(f["chiave"]), index=None, placeholder="Scegli un esercente",
                          key="ec_mov_%d" % st.session_state.get("ec_giro", 0))
    if scelto:
        from .pagine_dati import _movimenti_con_eccezioni
        _movimenti_con_eccezioni(c, "a.chiave = ?", [scelto], st.session_state.get("rev_giro", 0))


def _regole_utente(c):
    from .comune import etichetta_cat, opzioni_categorie
    tax = cfg.tassonomia()
    df = pd.DataFrame([dict(r) for r in c.execute("SELECT id, campo, modo, pattern, l1, l2, priorita, creata_il FROM regole_utente "
                                                  "ORDER BY priorita DESC, id DESC")])
    if df.empty:
        st.caption("Nessuna regola. Le regole nascono dalla revisione dei bonifici o si aggiungono qui sotto.")
    else:
        st.dataframe(pd.DataFrame({"Id": df["id"], "Dove": df["campo"].map(CAMPI), "Come": df["modo"],
                                   "Testo": df["pattern"],
                                   "Categoria": [cfg.etichetta(tax.risolvi(x, a) or (a, x)) for a, x in zip(df["l1"], df["l2"])],
                                   "Priorita'": df["priorita"],
                                   "Creata il": df["creata_il"].fillna("").str[:16].str.replace("T", " ")}),
                     hide_index=True, width="stretch")
        rid = st.selectbox("Elimina una regola", [None] + list(df["id"]),
                           format_func=lambda x: "—" if x is None else "%s · %s" % (x, df.loc[df["id"] == x, "pattern"].iloc[0]))
        if rid and st.button("Elimina"):
            servizi.elimina_regola(c, int(rid))
            esegui_ricalcolo("Regola eliminata")
            st.rerun()
    with st.form("nuova_regola"):
        st.markdown("**Nuova regola**")
        a, b, d = st.columns([3, 1, 1])
        pattern = a.text_input("Testo da cercare")
        campo = b.selectbox("Dove", list(CAMPI), format_func=CAMPI.get)
        modo = d.selectbox("Come", ["contiene", "esatto", "regex"])
        t = st.selectbox("Categoria", opzioni_categorie(), format_func=etichetta_cat)
        if st.form_submit_button("Aggiungi") and pattern.strip():
            try:
                servizi.crea_regola(c, pattern.strip(), t, campo, modo, priorita=20)
                esegui_ricalcolo("Regola aggiunta")
                st.rerun()
            except Exception as e:
                st.error(str(e))


def _tassonomia(c):
    from .. import tassonomia_utente as tu
    st.caption("La stessa sottocategoria puo' stare sotto categorie diverse (es. CASA · Varie e ACQUISTI · Varie): "
               "ogni voce e' la coppia categoria + sottocategoria. "
               "Le sottocategorie **predefinite** sono usate da regole e configurazioni del programma: si possono "
               "spostare sotto un'altra categoria, non rinominare o eliminare. Rinominare una tua sottocategoria "
               "aggiorna anche conferme, regole e decisioni gia' salvate. Le modifiche restano nel database e "
               "sopravvivono agli aggiornamenti del programma.")
    if msg := st.session_state.pop("tax_msg", None):
        st.success(msg)
    giro = st.session_state.get("tax_giro", 0)

    def fatto(messaggio):
        st.session_state["tax_giro"] = giro + 1
        st.session_state["tax_msg"] = messaggio
        esegui_ricalcolo()
        st.rerun()

    base = pd.DataFrame(tu.righe(c))
    elenco = list(base.to_dict("records"))
    st.markdown("**Rinomina o sposta**: modifica le celle e salva.")
    ed = st.data_editor(
        pd.DataFrame({"Sezione": base["sezione"], "Categoria": base["l1"], "Sottocategoria": base["l2"],
                      "Predefinita": base["predefinita"], "In uso": base["in_uso"]}),
        hide_index=True, width="stretch", num_rows="fixed", height=560, key=chiave_tabella("tax_ed_%d" % giro, zip(base["l1"], base["l2"])),
        disabled=["Predefinita", "In uso"],
        column_config={"Sezione": st.column_config.SelectboxColumn("Sezione", options=list(SEZ), required=True),
                       "Categoria": st.column_config.TextColumn("Categoria", help="Scrivi un nome nuovo per creare "
                                                                "una categoria"),
                       "Sottocategoria": st.column_config.TextColumn("Sottocategoria"),
                       "Predefinita": st.column_config.CheckboxColumn("Predefinita"),
                       "In uso": st.column_config.NumberColumn("In uso", help="Conferme, regole e decisioni che la usano")})
    if st.button("Salva le modifiche alla tassonomia", type="primary", key="tax_salva"):
        nuove = [{"sezione": r["Sezione"], "l1": r["Categoria"], "l2": r["Sottocategoria"],
                  "originale": o["originale"]} for (_, r), o in zip(ed.iterrows(), elenco)]
        errori, messaggi = tu.salva(c, nuove)
        for e in errori:
            st.error(e)
        if not errori:
            fatto("Tassonomia salvata" + ((": " + "; ".join(messaggi)) if messaggi else "."))

    st.markdown("**Aggiungi una sottocategoria**")
    categorie = list(dict.fromkeys(base["l1"]))
    with st.form("tax_nuova_%d" % giro):
        x, y, z = st.columns([1, 2, 2])
        sezione = x.selectbox("Sezione", list(SEZ), format_func=SEZ.get)
        l1 = y.selectbox("Categoria", categorie + ["Nuova categoria…"])
        nuova_l1 = y.text_input("Nome della nuova categoria", placeholder="solo se hai scelto Nuova categoria…")
        l2 = z.text_input("Sottocategoria", placeholder="es. Lidi e impianti")
        if st.form_submit_button("Aggiungi"):
            cat = testo(nuova_l1) if l1 == "Nuova categoria…" else l1
            errori, _ = tu.salva(c, elenco + [{"sezione": sezione, "l1": cat, "l2": l2, "originale": ""}])
            for e in errori:
                st.error(e)
            if not errori:
                fatto("Aggiunta \"%s\" in %s." % (testo(l2), cat.upper()))

    st.markdown("**Elimina una sottocategoria**")
    # una predefinita si puo' eliminare solo se il suo nome resta sotto un'altra categoria
    nomi = {}
    for r in elenco:
        nomi[r["l2"]] = nomi.get(r["l2"], 0) + 1
    libere = [(r["l1"], r["l2"]) for r in elenco if not r["predefinita"] or nomi[r["l2"]] > 1]
    if not libere:
        st.caption("Solo le sottocategorie non predefinite si possono eliminare.")
    else:
        uso = {(r["l1"], r["l2"]): r["in_uso"] for r in elenco}
        x, y = st.columns(2)
        da = x.selectbox("Sottocategoria da eliminare", libere, key="tax_da_%d" % giro,
                         format_func=lambda v: "%s (in uso: %d)" % (cfg.etichetta(v), uso[v]) if uso[v] else cfg.etichetta(v))
        verso = None
        if uso[da]:
            verso = y.selectbox("Sposta le sue %d voci su" % uso[da], [(r["l1"], r["l2"]) for r in elenco
                                                                       if (r["l1"], r["l2"]) != da],
                                format_func=cfg.etichetta, key="tax_verso_%d" % giro)
        if st.button("Elimina", key="tax_elimina"):
            try:
                if verso:
                    tu.unisci(c, da, verso)
                    fatto("\"%s\" eliminata, voci spostate su \"%s\"." % (cfg.etichetta(da), cfg.etichetta(verso)))
                else:
                    errori, _ = tu.salva(c, [r for r in elenco if (r["l1"], r["l2"]) != da])
                    for e in errori:
                        st.error(e)
                    if not errori:
                        fatto("\"%s\" eliminata." % cfg.etichetta(da))
            except ValueError as e:
                st.error(str(e))

    st.download_button("Esporta la tassonomia (tassonomia.yaml)", tu.esporta_yaml(c), "tassonomia.yaml",
                       "application/x-yaml", icon=":material/download:",
                       help="Stesso formato di config/tassonomia.yaml: contiene solo categorie e sottocategorie, "
                            "nessun movimento")
    with st.expander("Ripristina la tassonomia di partenza"):
        st.caption("Torna alle categorie del programma. Non si puo' se qualche voce salvata usa una sottocategoria "
                   "aggiunta da te: prima eliminala spostandone le voci su una predefinita.")
        if st.button("Ripristina", key="tax_ripristina"):
            try:
                tu.ripristina(c)
                fatto("Tassonomia di partenza ripristinata.")
            except ValueError as e:
                st.error(str(e))
