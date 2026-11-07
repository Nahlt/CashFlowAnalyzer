"""Conti e copertura, impostazioni, export e backup."""
from datetime import date, datetime

import pandas as pd
import streamlit as st

from .. import attributi as attr
from .. import analisi as an
from .. import importa as imp
from .. import llm, servizi, sessioni
from ..iban import controlla as controlla_iban
from ..db import backup, cambia_passphrase, leggi_impostazione, scrivi_impostazione
from ..percorsi import cartella_dati
from .comune import chiave_tabella, conn, esegui_ricalcolo, ollama_non_raggiungibile, scarica_modello_locale, testo
from .pagine_dati import TIPI_CONTO


def conti():
    st.title("Conti e copertura")
    c = conn()
    if msg := st.session_state.pop("conti_msg", None):
        st.success(msg)
    df = pd.DataFrame([dict(r) for r in c.execute(
        "SELECT id, nome, banca, tipo, perimetro, iban, numero, conto_addebito_id, importato, chiuso_il FROM conti ORDER BY id")])
    if df.empty:
        st.info("Nessun conto: si creano al primo import di ciascun file.")
    else:
        st.caption("Registra l'IBAN dei tuoi conti: e' il segnale piu' forte per riconoscere i trasferimenti tra conti tuoi.")
        nomi = dict(zip(df["id"], df["nome"]))
        from ..ricalcolo import carte_per_conto
        conti_d = {int(r["id"]): {"tipo": r["tipo"], "banca": r["banca"], "conto_addebito_id": r["conto_addebito_id"]
                                  if pd.notna(r["conto_addebito_id"]) else None} for _, r in df.iterrows()}
        movs_d = [{"conto_id": r[0], "tipo": "addebito_carta_credito"} for r in c.execute(
            "SELECT DISTINCT conto_id FROM movimenti WHERE tipo = 'addebito_carta_credito'")]
        _, dedotte = carte_per_conto(conti_d, movs_d)
        senza = [nomi[k] for k, v in conti_d.items() if v["tipo"] == "carta" and not v["conto_addebito_id"]
                 and k not in dedotte]
        for carta, conto in dedotte.items():
            st.caption("La carta **%s** non ha il conto di addebito: uso **%s**, l'unico con addebiti della carta di "
                       "credito. Indicalo nella colonna Addebitata su per esserne sicuro." % (nomi[carta], nomi[conto]))
        if senza:
            st.warning("Carte senza conto di addebito: %s. Indicalo nella colonna *Addebitata su*, altrimenti "
                       "l'addebito mensile della carta e le sue spese si contano due volte." % ", ".join(senza),
                       icon=":material/credit_card:")
        per_nome = {v: k for k, v in nomi.items()}
        df["addebito"] = [nomi.get(x) if pd.notna(x) else None for x in df["conto_addebito_id"]]
        for col in ("banca", "iban", "numero", "chiuso_il"):
            df[col] = df[col].fillna("")
        ed = st.data_editor(
            df[["id", "nome", "banca", "tipo", "perimetro", "iban", "numero", "addebito", "importato", "chiuso_il"]],
            hide_index=True, width="stretch", disabled=["id", "importato"], key=chiave_tabella("ed_conti", zip(df["id"], df["nome"])),
            column_config={
                "id": st.column_config.NumberColumn("Id", width="small"),
                "nome": "Nome", "banca": "Banca", "iban": "IBAN", "numero": "Numero",
                "tipo": st.column_config.SelectboxColumn("Tipo", options=list(TIPI_CONTO)),
                "perimetro": st.column_config.SelectboxColumn("Perimetro", options=["personale", "comune"]),
                "addebito": st.column_config.SelectboxColumn("Addebitata su (solo carte)", options=list(per_nome)),
                "importato": st.column_config.CheckboxColumn("Importato"),
                "chiuso_il": st.column_config.TextColumn("Chiuso il (AAAA-MM-GG)")})
        for nome_c, valore in zip(df["nome"], df["iban"]):
            errore = controlla_iban(valore)[1]
            if errore:
                st.warning("L'IBAN registrato per \"%s\" non sembra valido (%s): controllalo." % (nome_c, errore))
        if st.button("Salva le modifiche ai conti", type="primary"):
            # prima si controllano tutte le righe: con un errore non si salva nulla
            errori, ibans, nomi_visti = [], {}, set()
            originali = {int(k): v for k, v in zip(df["id"], df["iban"])}
            for _, r in ed.iterrows():
                nome_c = testo(r["nome"]) or "conto %d" % r["id"]
                if not testo(r["nome"]):
                    errori.append("Il nome del conto %d non puo' essere vuoto." % r["id"])
                elif testo(r["nome"]).lower() in nomi_visti:
                    errori.append("Il nome \"%s\" e' usato da due conti: ogni conto deve avere un nome diverso." % nome_c)
                nomi_visti.add(testo(r["nome"]).lower())
                chiuso = testo(r["chiuso_il"])
                if chiuso:
                    try:
                        date.fromisoformat(chiuso)
                    except ValueError:
                        errori.append("Data di chiusura di \"%s\" non valida: scrivila come AAAA-MM-GG (es. 2026-03-31)."
                                      % nome_c)
                iban, errore = controlla_iban(r["iban"])
                cambiato = iban != (controlla_iban(originali.get(int(r["id"])))[0])
                if errore and cambiato:          # si blocca solo un IBAN inserito o modificato adesso
                    errori.append("IBAN di \"%s\" non valido: %s." % (nome_c, errore))
                elif iban and iban in ibans.values():
                    errori.append("L'IBAN di \"%s\" e' gia' usato da un altro conto." % nome_c)
                ibans[int(r["id"])] = iban
            if errori:
                for e in errori:
                    st.error(e)
            else:
                # tutte le righe insieme: se una non si salva non resta nessuna modifica a meta'
                c.execute("BEGIN")
                try:
                    for _, r in ed.iterrows():
                        servizi.aggiorna_conto(c, int(r["id"]), nome=testo(r["nome"]), banca=testo(r["banca"]) or None,
                                               tipo=r["tipo"], perimetro=r["perimetro"], iban=ibans[int(r["id"])],
                                               numero=testo(r["numero"]) or None,
                                               conto_addebito_id=per_nome.get(testo(r["addebito"])),
                                               chiuso_il=testo(r["chiuso_il"]) or None)
                    c.execute("COMMIT")
                except Exception as e:
                    c.execute("ROLLBACK")
                    st.error("Modifiche non salvate: %s." % e)
                else:
                    st.session_state["conti_msg"] = "Conti aggiornati."
                    esegui_ricalcolo()
                    st.rerun()
    if not df.empty:
        _elimina_conto(c, df)
    with st.expander("Aggiungi un conto (anche uno che non importi: conto deposito, broker, conto di un familiare)"):
        with st.form("conto_non_importato"):
            a, b, d = st.columns(3)
            nome = a.text_input("Nome")
            tipo = b.selectbox("Tipo", ["deposito", "broker", "conto", "carta"], format_func=TIPI_CONTO.get)
            per = d.selectbox("Perimetro", ["personale", "comune"])
            e, f2 = st.columns([1, 2])
            banca = e.text_input("Banca", placeholder="es. ING, Credem")
            iban = f2.text_input("IBAN (riconosce i trasferimenti verso questo conto e il conto all'import)")
            if st.form_submit_button("Aggiungi") and nome.strip():
                iban_n, errore = controlla_iban(iban)
                if errore:
                    st.error("IBAN non valido: %s." % errore)
                elif imp.nome_conto_esistente(c, nome):
                    st.error("Esiste gia' un conto con questo nome.")
                elif iban_n and c.execute("SELECT 1 FROM conti WHERE iban = ?", (iban_n,)).fetchone():
                    st.error("Questo IBAN e' gia' registrato su un altro conto.")
                else:
                    imp.crea_conto(c, nome.strip(), None, tipo, per, banca=banca.strip() or None, iban=iban_n, importato=0)
                    esegui_ricalcolo("Conto aggiunto")
                    st.rerun()

    st.subheader("Lista di controllo")
    t = an.copertura(c, 12)
    if t.empty:
        st.caption("Nessun movimento importato.")
    else:
        st.caption("Movimenti importati per mese e conto. Uno zero negli ultimi mesi indica un estratto da caricare.")
        ultimi = {(date.today().replace(day=1) - pd.DateOffset(months=i)).strftime("%Y-%m") for i in range(1, 5)}

        def evidenzia(col):
            return ["background-color: rgba(235,104,52,0.18)" if (v == 0 and idx in ultimi) else "" for idx, v in col.items()]
        st.dataframe(t.style.apply(evidenzia, axis=0), width="stretch")


def _elimina_conto(c, df):
    with st.expander("Elimina un conto"):
        nomi = dict(zip(df["id"], df["nome"]))
        giro = st.session_state.get("elim_giro", 0)
        scelto = st.selectbox("Conto da eliminare", list(nomi), format_func=nomi.get, index=None,
                              placeholder="Scegli un conto", key="elim_conto_%d" % giro)
        if scelto is None:
            return
        uso = imp.uso_conto(c, int(scelto))
        conferma = True
        if uso["movimenti"] or uso["file"]:
            st.warning("Con **%s** vengono cancellati anche **%d movimenti** e **%d file importati**, con le "
                       "decisioni prese su quei movimenti (categorie scelte sul singolo movimento, suddivisioni, "
                       "etichette, abbinamenti). Restano esercenti confermati e regole, che valgono per tutti i conti. "
                       "Non si puo' annullare: prima conviene un backup (Impostazioni > Crea un backup cifrato)."
                       % (nomi[scelto], uso["movimenti"], uso["file"]), icon=":material/warning:")
            conferma = st.checkbox("Ho capito, elimina il conto e i suoi %d movimenti" % uso["movimenti"],
                                   key="elim_ok_%d" % giro)
        else:
            st.caption("Il conto non ha movimenti: si elimina solo la sua scheda.")
        if uso["carte"]:
            st.caption("Le carte addebitate su questo conto (%s) restano, senza conto di addebito." % ", ".join(uso["carte"]))
        if st.button("Elimina \"%s\"" % nomi[scelto], disabled=not conferma, key="elim_btn_%d" % giro):
            imp.elimina_conto(c, int(scelto))
            st.session_state["elim_giro"] = giro + 1
            st.session_state["conti_msg"] = "Conto \"%s\" eliminato%s." % (
                nomi[scelto], (" con %d movimenti" % uso["movimenti"]) if uso["movimenti"] else "")
            esegui_ricalcolo()
            st.rerun()


def impostazioni():
    st.title("Impostazioni")
    c = conn()
    st.subheader("Nomi e parole riservate")
    if not leggi_impostazione(c, "titolari", []):
        st.warning("Indica i titolari dei conti: finche' mancano, l'invio a Claude resta disattivato.")
    with st.form("privacy"):
        tit = st.text_area("Titolari dei conti (uno per riga): vengono tolti dalle descrizioni e servono a riconoscere "
                           "i trasferimenti a tuo nome", "\n".join(leggi_impostazione(c, "titolari", [])))
        fam = st.text_area("Familiari che versano sul conto comune senza essere titolari dei conti caricati (uno per "
                           "riga, es. la tua compagna): i loro bonifici sul conto comune diventano contributi e i loro "
                           "nomi vengono protetti come quelli dei titolari", "\n".join(leggi_impostazione(c, "familiari", [])))
        sens = st.text_area("Altre parole sensibili (bloccano l'invio a Claude)", "\n".join(leggi_impostazione(c, "parole_sensibili", [])))
        loc = st.text_area("Parole solo locali, oltre all'area salute (es. veterinario, avvocato)",
                           "\n".join(leggi_impostazione(c, "parole_solo_locale", [])))
        if st.form_submit_button("Salva", type="primary"):
            pulisci = lambda t: [x.strip() for x in t.splitlines() if x.strip()]
            scrivi_impostazione(c, "titolari", pulisci(tit))
            scrivi_impostazione(c, "familiari", pulisci(fam))
            scrivi_impostazione(c, "parole_sensibili", pulisci(sens))
            scrivi_impostazione(c, "parole_solo_locale", pulisci(loc))
            n = imp.ripulisci_nomi(c)
            esegui_ricalcolo("Impostazioni salvate" + (": nome del titolare tolto da %d movimenti" % n if n else ""))

    st.subheader("Sezioni facoltative")
    st.caption("Spegnendo una sezione spariscono la sua pagina, le colonne e i grafici collegati; i dati gia' "
               "inseriti restano nel database e ricompaiono riaccendendola.")
    from .. import mercato
    from .. import portafoglio as pf
    voci = [("Portafoglio titoli", "Pagina Portafoglio sotto Analisi: posizioni, costo medio, plusvalenze realizzate "
             "e, con i prezzi di mercato, valore e rendimenti.", pf.CHIAVE_ATTIVA, pf.attiva(c)),
            ("Etichette", "Etichette libere sui movimenti (es. un viaggio) e pagina Etichette sotto Analisi.",
             "sezione_etichette", attr.etichette_attive(c)),
            ("Natura della spesa", "Necessaria, svago o superflua per ogni uscita: colonne, grafico in Dove vanno i "
             "soldi e scheda in Regole e tassonomia.", "sezione_natura", attr.natura_attiva(c))]
    for nome, aiuto, chiave_imp, valore in voci:
        nuovo = st.toggle(nome, value=valore, help=aiuto, key="sez_" + chiave_imp)
        if nuovo != valore:
            scrivi_impostazione(c, chiave_imp, nuovo)
            st.rerun()
    if pf.attiva(c):
        yahoo = st.toggle("Prezzi di mercato da Yahoo Finance", value=mercato.attivo(c), key="sez_yahoo",
                          help="Scarica i prezzi giornalieri dei titoli in portafoglio per vedere valore e rendimenti.")
        if yahoo != mercato.attivo(c):
            scrivi_impostazione(c, mercato.CHIAVE_ATTIVO, yahoo)
            st.rerun()
        st.caption("Con i prezzi di mercato esce dal PC solo l'elenco dei titoli (codici ISIN e simboli di borsa) verso "
                   "Yahoo Finance: mai quote, importi, date o nomi. Ogni richiesta resta nel registro delle richieste "
                   "in Classificazione automatica.")

    st.subheader("Modello locale (Ollama)")
    oc = llm.ollama_config(c)
    if st.session_state.get("modello_installato"):
        st.success("Modello %s installato." % st.session_state.pop("modello_installato"))
    with st.form("ollama"):
        url = st.text_input("Indirizzo", oc["url"])
        mod = st.text_input("Modelli (separati da punto e virgola)", "; ".join(llm.modelli_locali(c)),
                            help="Es. qwen2.5:3b; llama3.2:3b. In Classificazione automatica scegli quale usare. "
                                 "I modelli cloud di Ollama non sono ammessi: girano fuori dal PC.")
        if st.form_submit_button("Salva e verifica"):
            elenco = llm.separa_modelli(mod)
            errori = [e for e in (llm.errore_nome_modello(m, locale=True) for m in elenco) if e]
            if not elenco:
                errori.append("Indica almeno un modello.")
            if errori:
                for e in errori:
                    st.error(e)
            else:
                scrivi_impostazione(c, "ollama_url", url.strip())
                scrivi_impostazione(c, "ollama_modelli", elenco)
                if leggi_impostazione(c, "ollama_modello", None) not in elenco:
                    scrivi_impostazione(c, "ollama_modello", elenco[0])
                st.session_state["verifica_ollama"] = True
    if st.session_state.get("verifica_ollama"):
        ok, info = llm.ollama_disponibile(c)
        if not ok:
            ollama_non_raggiungibile(c, info, "imp")
        else:
            mancanti = [m for m in llm.modelli_locali(c) if not llm.installato(m, info)]
            st.success("Ollama raggiungibile. Modelli installati: %s" % (", ".join(info) or "nessuno"))
            for m in mancanti:
                scarica_modello_locale(c, m, "scarica_imp_%s" % m)

    st.subheader("Claude")
    with st.form("claude"):
        presente = bool(llm.chiave_api())
        k = st.text_input("Chiave API (salvata nel portachiavi di sistema)", type="password",
                          placeholder="gia' configurata" if presente else "sk-ant-…")
        mod = st.text_input("Modelli (separati da punto e virgola)", "; ".join(llm.modelli_claude(c)),
                            help="Es. claude-haiku-5-5; claude-sonnet-5-5; claude-opus-5-5; claude-fable-5-1. "
                                 "In Classificazione automatica scegli quale usare per ogni invio.")
        if st.form_submit_button("Salva"):
            if k.strip():
                try:
                    llm.salva_chiave_api(k.strip())
                    st.success("Chiave salvata nel portachiavi.")
                except Exception as e:
                    st.error("Portachiavi non disponibile (%s). In alternativa avvia l'app con la variabile "
                             "ANTHROPIC_API_KEY." % e)
            elenco = llm.separa_modelli(mod)
            errori = [e for e in (llm.errore_nome_modello(m) for m in elenco) if e]
            if not elenco:
                errori.append("Indica almeno un modello.")
            for e in errori:
                st.error(e)
            if not errori:
                scrivi_impostazione(c, "claude_modelli", elenco)
                if leggi_impostazione(c, "claude_modello", None) not in elenco:
                    scrivi_impostazione(c, "claude_modello", elenco[0])
                with st.spinner("Verifico i nomi con Claude…"):
                    sconosciuti = llm.verifica_modelli_claude(elenco)
                if sconosciuti:
                    st.warning("Modelli salvati, ma Claude non riconosce: %s. Controlla il nome (l'elenco e' su "
                               "platform.claude.com/docs, sezione Models)." % ", ".join(sconosciuti))
                else:
                    st.success("Modelli salvati.")

    st.subheader("Export e backup")
    a, b, d = st.columns(3)
    a.download_button("Scarica i dati di analisi (CSV)", servizi.export_csv(c), "spese_gold.csv", "text/csv",
                      help="Livello gold per strumenti di BI. File in chiaro: conservalo con cura.")
    if b.button("Crea un backup cifrato"):
        dest = cartella_dati() / ("backup_%s.db" % datetime.now().strftime("%Y%m%d_%H%M%S"))
        backup(c, dest)
        st.success("Backup creato in %s (stessa passphrase)." % dest)
    if d.button("Ricalcola tutto"):
        r = esegui_ricalcolo("Ricalcolo completato")
        st.json(r)
    st.subheader("Passphrase del database")
    with st.form("pass"):
        n1 = st.text_input("Nuova passphrase", type="password")
        n2 = st.text_input("Ripeti", type="password")
        if st.form_submit_button("Cambia passphrase"):
            if len(n1) < 8 or n1 != n2:
                st.error("Le passphrase non coincidono o sono troppo corte (minimo 8 caratteri).")
            else:
                try:
                    cambia_passphrase(c, n1)
                except Exception as e:
                    st.error("Passphrase non cambiata: %s. Riprova tra qualche secondo." % e)
                else:
                    # la sessione in memoria e le altre schede aperte passano alla passphrase nuova
                    st.session_state["generazione"] = sessioni.aggiorna_passphrase(n1)
                    st.success("Passphrase cambiata. I backup creati finora restano protetti dalla passphrase "
                               "precedente.")
