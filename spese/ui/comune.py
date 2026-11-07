"""Funzioni condivise dalle pagine."""
from datetime import date

import streamlit as st

from .. import configurazione as cfg
from ..analisi import PERIMETRI, Filtri, periodo_disponibile
from ..numeri import euro
from ..ricalcolo import ricalcola

# palette categoriale validata (slot 1-2) e tinta sequenziale
BLU = "#2a78d6"
ARANCIO = "#eb6834"
ACQUA = "#1baf7a"
GRIGIO = "#8a8984"

NATURE = {
    "uscita": "Uscita", "entrata": "Entrata", "rimborso": "Rimborso", "risparmio": "Risparmio",
    "trasferimento_interno": "Trasferimento interno", "contenitore": "Contenitore (dettaglio altrove)",
    "escluso": "Escluso dalle analisi",
}


def conn():
    return st.session_state["conn"]


def esegui_ricalcolo(messaggio=None):
    r = ricalcola(conn())
    st.session_state["ultimo_riepilogo"] = r
    if messaggio:
        st.toast(messaggio)
    return r


def opzioni_categorie(sezioni=("uscite", "entrate", "risparmio")):
    """Le categorie (l1, l2) delle sezioni indicate, raggruppate per categoria nell'ordine della tassonomia."""
    tax = cfg.tassonomia()
    return [c for l1 in tax.ordine_l1 for c in tax.voci if c[0] == l1 and tax.sezione_di[c] in sezioni]


def etichetta_cat(t):
    return cfg.etichetta(t)


def _ricorda(chiave):
    """I filtri stanno nella barra laterale di tutte le pagine di analisi. Streamlit dimentica il valore di un
    widget quando una pagina non lo mostra (es. Importa): una copia a parte lo fa ritrovare tornando indietro."""
    if chiave not in st.session_state and "_tieni_" + chiave in st.session_state:
        st.session_state[chiave] = st.session_state["_tieni_" + chiave]


def _tieni(chiave):
    if chiave in st.session_state:
        st.session_state["_tieni_" + chiave] = st.session_state[chiave]


def _conti_del_perimetro(conti, perimetro):
    return [r["id"] for r in conti if perimetro == "tutti" or r["perimetro"] == perimetro]


def mostra_filtri(con_conti=True) -> Filtri:
    """Disegna i filtri nella barra laterale: perimetro (scorciatoie), conti scelti e periodo. La chiama app.py
    prima del menu, cosi' i filtri stanno in alto e la scelta resta passando da una pagina all'altra.
    I pulsanti Personale / Conto comune / Cumulativa selezionano i conti corrispondenti; la scelta dei conti
    si puo' poi cambiare a mano (perimetro composto, es. Credit Agricole + Trade Republic)."""
    c = conn()
    conti = c.execute("SELECT id, nome, perimetro FROM conti WHERE importato = 1 ORDER BY perimetro DESC, nome").fetchall()
    nomi = {r["id"]: r["nome"] for r in conti}
    st.markdown("#### Filtri")
    _ricorda("f_perimetro")
    _ricorda("f_conti")
    if "f_conti" not in st.session_state:
        st.session_state["f_perimetro"] = st.session_state.get("f_perimetro") or "personale"
        st.session_state["f_conti"] = _conti_del_perimetro(conti, st.session_state["f_perimetro"])
    st.session_state["f_conti"] = [i for i in st.session_state["f_conti"] if i in nomi]
    if st.session_state.get("f_perimetro") in PERIMETRI:
        # con un perimetro scelto (Personale, Comune, Tutti) i conti seguono il perimetro: anche un conto appena
        # importato (es. PayPal) entra subito nelle analisi
        st.session_state["f_conti"] = _conti_del_perimetro(conti, st.session_state["f_perimetro"])

    def preset():
        p = st.session_state.get("f_perimetro")
        if p:
            st.session_state["f_conti"] = _conti_del_perimetro(conti, p)

    def a_mano():
        scelti = set(st.session_state.get("f_conti") or [])
        st.session_state["f_perimetro"] = next((p for p in PERIMETRI if set(_conti_del_perimetro(conti, p)) == scelti
                                                and scelti), None)

    st.segmented_control("Perimetro", list(PERIMETRI), format_func=PERIMETRI.get, key="f_perimetro", on_change=preset)
    scelti = st.multiselect("Conti", list(nomi), format_func=nomi.get, key="f_conti", on_change=a_mano,
                            placeholder="Scegli almeno un conto")
    _tieni("f_perimetro")
    _tieni("f_conti")
    perimetro = st.session_state.get("f_perimetro")
    if not scelti:
        st.caption("Nessun conto scelto: le analisi sono vuote.")
    elif len(set(r["perimetro"] for r in conti if r["id"] in scelti)) > 1:
        st.caption("I versamenti tra i conti scelti (es. dal conto personale al conto comune) si annullano.")
    dmin, dmax = periodo_disponibile(c, scelti) if scelti else (None, None)
    da = a = None
    if dmin:
        d0, d1 = date.fromisoformat(dmin), date.fromisoformat(dmax)
        k = "f_periodo"
        _ricorda(k)
        v = st.session_state.get(k)
        if isinstance(v, (list, tuple)) and not all(d0 <= x <= d1 for x in v):
            del st.session_state[k]                    # periodo fuori dai dati dei conti scelti
        extra = {} if k in st.session_state else {"value": (d0, d1)}
        sel = st.date_input("Periodo", min_value=d0, max_value=d1, format="DD/MM/YYYY", key=k, **extra)
        _tieni(k)
        if isinstance(sel, (list, tuple)) and len(sel) == 2:
            da, a = sel[0].isoformat(), sel[1].isoformat()
    f = Filtri(perimetro or "composto", da, a, list(scelti) if scelti else [])
    if not scelti:
        f.perimetro = "nessuno"
    st.session_state["filtri_attivi"] = f
    return f


def filtri_sidebar(con_conti=True) -> Filtri:
    """Filtri scelti nella barra laterale per questa pagina (disegnati da app.py)."""
    f = st.session_state.get("filtri_attivi")
    if f is None:
        with st.sidebar:
            f = mostra_filtri(con_conti)
    return f


def avviso_rivedere(pagina_revisione=None):
    """Richiamo arancio nella barra laterale: porta alla Revisione."""
    n = conn().execute("SELECT count(DISTINCT mov_id) FROM analisi WHERE da_rivedere = 1").fetchone()[0]
    if n and pagina_revisione is not None:
        with st.container(key="avviso_rivedere"):
            st.page_link(pagina_revisione, label="%d movimenti da rivedere" % n, icon=":material/error:")


def intestazione(titolo, f=None, sotto=None):
    """Titolo della pagina; a destra conti e periodo analizzati."""
    a, b = st.columns([3, 2], vertical_alignment="bottom")
    with a:
        st.title(titolo)
        if sotto:
            st.caption(sotto)
    if f is not None:
        if f.perimetro in PERIMETRI:
            chi = PERIMETRI[f.perimetro]
        elif f.conti:
            chi = "%d cont%s" % (len(f.conti), "o" if len(f.conti) == 1 else "i")
        else:
            chi = "Nessun conto"
        quando = ""
        if f.da and f.a:
            d = lambda x: "%s/%s/%s" % (x[8:10], x[5:7], x[:4])
            quando = " · %s – %s" % (d(f.da), d(f.a))
        b.html('<div class="intestazione-destra">%s%s</div>' % (chi, quando))


def fmt_euro(cent):
    return euro(cent)


def chiave_tabella(prefisso, valori):
    """Chiave di una tabella con righe selezionabili che cambia quando cambiano le righe. Streamlit lega la
    selezione alla chiave anche se i dati cambiano (es. un filtro): resterebbero selezionate le stesse posizioni,
    cioe' righe diverse, o posizioni che non esistono piu' (errore)."""
    return "%s_%08d" % (prefisso, abs(hash(tuple(valori))) % 10 ** 8)


def testo(v):
    """Valore di una cella come testo: con pandas 3 le celle vuote arrivano come NaN (float), non come None."""
    return v.strip() if isinstance(v, str) else ""


def scarica_modello_locale(c, nome, chiave):
    """Il modello locale scelto non e' installato: chiede se scaricarlo e installarlo in Ollama."""
    from .. import llm
    st.warning("Il modello locale **%s** non e' installato in Ollama. Vuoi scaricarlo e installarlo? Di solito "
               "pesa qualche GB e richiede alcuni minuti; dalla rete esce solo il nome del modello." % nome,
               icon=":material/download:")
    if st.button("Scarica e installa %s" % nome, key=chiave):
        barra = st.progress(0.0, text="Download di %s…" % nome)

        def passo(stato, fatti, totale):
            if totale:
                barra.progress(min(1.0, (fatti or 0) / totale),
                               text="%s · %.1f di %.1f GB" % (stato, (fatti or 0) / 1e9, totale / 1e9))
            else:
                barra.progress(0.0, text=stato or "…")
        try:
            llm.scarica_modello(c, nome, passo)
        except Exception as e:
            st.error("Download non riuscito: %s" % e)
            return False
        st.session_state["modello_installato"] = nome
        st.rerun()
    return False


@st.fragment(run_every=2)
def _avanzamento_ollama():
    """Avanzamento dell'installazione di Ollama in background: si aggiorna da solo ogni 2 secondi."""
    from .. import installa_ollama as io
    lav = io.lavoro()
    if lav is None:
        return
    if not lav.finito:
        st.progress(lav.percentuale or 0.0, text=lav.testo)
        st.caption("Puoi cambiare pagina: l'installazione continua e la ritrovi qui.")
    elif lav.errore:
        st.error(lav.errore, icon=":material/error:")
        if lav.comando:
            st.code(lav.comando, language="bash")
        if st.button("Chiudi", key="ollama_chiudi_errore"):
            io.chiudi_lavoro()
            st.rerun()
    else:
        st.success("Ollama e' installato e attivo; modelli pronti: %s." % ", ".join(lav.modelli))
        if st.button("Continua", key="ollama_continua", type="primary"):
            io.chiudi_lavoro()
            st.session_state["verifica_ollama"] = True
            st.rerun()
    with st.expander("Dettagli dell'installazione"):
        st.code("\n".join(list(lav.righe)[-60:]) or "…", language="text")


def ollama_non_raggiungibile(c, dettaglio, chiave):
    """Ollama non risponde: messaggio chiaro e, se l'indirizzo e' questo PC, proposta di installarlo (o avviarlo)
    insieme ai modelli indicati in Impostazioni."""
    from .. import installa_ollama as io
    from .. import llm
    if io.lavoro() is not None:
        _avanzamento_ollama()
        return
    url = llm.ollama_config(c)["url"]
    modelli = llm.modelli_locali(c)
    elenco = ", ".join(modelli)
    if not io.indirizzo_locale(url):
        st.warning("Ollama non risponde all'indirizzo %s. Controlla che quel computer sia acceso e che Ollama sia "
                   "avviato, oppure indica l'indirizzo di questo PC (http://127.0.0.1:11434)." % url,
                   icon=":material/memory:")
    elif io.installato():
        st.warning("Ollama e' installato su questo PC ma non e' avviato.", icon=":material/memory:")
        if st.button("Avvia Ollama e controlla i modelli (%s)" % elenco, key="ollama_avvia_%s" % chiave):
            io.avvia_lavoro(url, modelli, installa=False)
            st.rerun()
    else:
        sistema = "Windows" if io.WINDOWS else "Linux"
        st.warning("Ollama non e' installato su questo PC: serve per classificare gli esercenti con il modello "
                   "locale, senza mandare nulla fuori dal computer. Vuoi installarlo adesso, insieme ai modelli "
                   "indicati (%s)?" % elenco, icon=":material/memory:")
        if io.WINDOWS:
            come = ("Su Windows si installa solo per il tuo utente, senza permessi di amministratore: con winget, "
                    "oppure con l'installatore ufficiale della release su GitHub, usato solo se impronta e firma "
                    "digitale sono quelle di Ollama.")
        else:
            come = ("Su Linux si usa lo script ufficiale di ollama.com, che installa Ollama come servizio di "
                    "sistema: serve la password di amministratore, chiesta da una finestra di sistema.")
        st.caption("Cosa succede: si scarica il programma Ollama dalle fonti ufficiali (circa 1,5 GB) e poi i "
                   "modelli indicati (qualche GB); dalla rete escono solo il nome dei modelli, nessun tuo dato. %s "
                   "I modelli cloud di Ollama restano esclusi (OLLAMA_NO_CLOUD=1). Ci vogliono alcuni minuti." % come)
        ok = st.checkbox("Si', installa Ollama e i modelli su questo PC (%s)" % sistema, key="ollama_ok_%s" % chiave)
        if st.button("Installa Ollama", key="ollama_installa_%s" % chiave, type="primary", disabled=not ok):
            io.avvia_lavoro(url, modelli, installa=True)
            st.rerun()
    if dettaglio:
        with st.expander("Dettagli tecnici"):
            st.code(str(dettaglio), language="text")
