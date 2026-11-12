"""CashFlow Analyzer - avvio: streamlit run app.py (vedi avvia.py)."""
import os

import streamlit as st

from spese import AUTORE, VERSIONE, sessioni
from spese import configurazione as cfg
from spese.db import CIFRATO, PassphraseErrata, SchemaPiuRecente, apri
from spese.percorsi import file_database

st.set_page_config(page_title="CashFlow Analyzer", page_icon=":material/savings:", layout="wide")
# Stile "chiaro a schede" (colori e carattere in .streamlit/config.toml): fondo grigio chiaro, blocchi in schede
# bianche (contenitori con chiave "scheda_..."), barra laterale bianca con i filtri in un riquadro.
st.html("""<style>
[data-testid="stMainBlockContainer"] { padding-top: 1.6rem; padding-bottom: 2.5rem; max-width: 1640px; }
h1 { padding-top: 0 !important; padding-bottom: 0.15rem !important; letter-spacing: -0.01em; }
[data-testid="stMainBlockContainer"] [class*="st-key-scheda"] { background: #ffffff; border: 1px solid #e3e7ee;
  border-radius: 12px; padding: 16px 20px; }
[data-testid="stMainBlockContainer"] [class*="st-key-scheda"] h3 { padding-top: 0; }
[data-testid="stMetric"] { background: #ffffff; border: 1px solid #e3e7ee; border-radius: 12px; padding: 14px 18px; }
[data-testid="stMetricLabel"] p { color: #5b6575; }
[class*="st-key-scheda"] [data-testid="stMetric"] { border: 0; padding: 4px 0; background: transparent; }
[data-testid="stExpander"] details { background: #ffffff; border-radius: 10px; }
[data-testid="stForm"] { background: #ffffff; border-radius: 12px; }
.intestazione-destra { text-align: right; color: #5b6575; font-size: 0.85rem; padding-bottom: 0.6rem; }
/* barra laterale */
[data-testid="stSidebarHeader"] { height: 2.25rem; min-height: 2.25rem; padding-top: 0.4rem; padding-bottom: 0; margin-bottom: 0; }
[data-testid="stSidebarUserContent"] { padding-top: 0; padding-bottom: 1rem; }
[data-testid="stSidebarUserContent"] [data-testid="stVerticalBlock"] { gap: 0.35rem; }
/* piede della barra laterale: linea sopra il pulsante Esci (dentro il suo riquadro, senza sovrapposizioni) e versione */
.st-key-piede { border-top: 1px solid #e3e7ee; margin-top: 0.6rem; padding-top: 0.75rem; }
.versione { font-size: 11px; color: #6b7585; padding: 2px 2px 0; }
.marchio { display: flex; align-items: center; gap: 10px; margin: 0 0 6px; }
.marchio .logo { width: 32px; height: 32px; border-radius: 9px; background: #2a78d6; display: flex;
  align-items: center; justify-content: center; flex: none; color: #ffffff; font-weight: 700; font-size: 17px; }
section[data-testid="stSidebar"][data-collapsed="false"] { width: 320px !important; min-width: 320px; }
.marchio b { display: block; font-size: 15px; } .marchio span { font-size: 12px; color: #5b6575; }
.gruppo-menu { font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #6b7585;
  padding: 10px 8px 2px; }
.st-key-filtri { background: #f7f9fb; border: 1px solid #e3e7ee; border-radius: 12px; padding: 12px 12px 10px; }
.st-key-filtri h4 { font-size: 11px !important; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
  color: #5b6575; padding: 0 0 8px !important; }
.st-key-f_perimetro, .st-key-f_perimetro [data-testid="stButtonGroup"] { width: 100% !important; }
.st-key-f_perimetro [role="radiogroup"] { display: flex; width: 100%; max-width: 100% !important; flex-wrap: nowrap; }
.st-key-f_conti [data-tag] { background: #e6f0fb !important; color: #1f4f8f !important; }
.st-key-f_conti [data-tag] span, .st-key-f_conti [data-tag] button { color: #1f4f8f !important; }
.st-key-f_perimetro button { flex: 1 1 0; min-width: 0; padding-left: 0.25rem; padding-right: 0.25rem; }
[data-testid="stSidebarUserContent"] [data-testid="stPageLink"] a { padding: 0.2rem 0.5rem; border-radius: 8px; background: transparent; }
[data-testid="stSidebarUserContent"] [data-testid="stPageLink"] a:hover { background: #f0f3f7; }
.st-key-menu_attivo [data-testid="stPageLink"] a { background: #e6f0fb !important; }
.st-key-menu_attivo [data-testid="stPageLink"] a p { color: #1f4f8f; font-weight: 600; }
.st-key-avviso_rivedere [data-testid="stPageLink"] a { background: #fff4ec !important; padding: 0.45rem 0.6rem; }
.st-key-avviso_rivedere [data-testid="stPageLink"] a p, .st-key-avviso_rivedere [data-testid="stPageLink"] span { color: #9a4512 !important; font-weight: 500; }
</style>""")

# SPESE_MINUTI_SESSIONE sovrascrive il valore (utile per le prove)
MINUTI = float(os.environ.get("SPESE_MINUTI_SESSIONE") or (cfg.impostazioni().get("sessione") or {}).get("minuti_inattivita", 10080))
DURATA = int(MINUTI * 60)
COOKIE = "spese_token"     # codice casuale della sessione: ricaricando la pagina non serve la passphrase


def _durata_testo():
    if MINUTI >= 1440:
        g = round(MINUTI / 1440)
        return "%d giorn%s" % (g, "o" if g == 1 else "i")
    if MINUTI >= 120:
        return "%d ore" % round(MINUTI / 60)
    return "%d minuti" % round(MINUTI)


def _cookie(token):
    if token:
        valore = "%s; Max-Age=%d; path=/; SameSite=Strict" % (token, DURATA)
    else:
        valore = "; Max-Age=0; path=/; SameSite=Strict"
    st.html("<script>document.cookie = '%s=%s'; document.cookie = 'spese_sessione=; Max-Age=0; path=/';</script>"
            % (COOKIE, valore), unsafe_allow_javascript=True)


def chiudi_sessione(motivo):
    c = st.session_state.get("conn")
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
    if motivo == "uscita":
        sessioni.chiudi()            # vale per tutte le schede: il database torna protetto
    st.session_state.clear()
    st.session_state["motivo_uscita"] = motivo


def secondi_rimasti():
    return sessioni.secondi_rimasti(DURATA)


def accesso():
    # pagina di accesso: una scheda stretta al centro, con il marchio dell'app
    st.html('<style>[data-testid="stMainBlockContainer"] { max-width: 520px !important; padding-top: 12vh !important; }</style>'
            '<div class="marchio"><div class="logo" aria-hidden="true">€</div><div><b>CashFlow Analyzer</b>'
            '<span>Database cifrato, solo su questo PC</span></div></div>')
    st.title("Accesso")
    motivo = st.session_state.get("motivo_uscita")
    if motivo == "inattivita":
        st.warning("Sessione chiusa dopo %s di inattivita': il database e' di nuovo protetto. "
                   "Inserisci la passphrase per continuare." % _durata_testo(), icon=":material/lock_clock:")
    elif motivo == "uscita":
        st.info("Sei uscito: il database e' chiuso.", icon=":material/lock:")
    elif motivo == "chiusa_altrove":
        st.info("Il database e' stato chiuso da un'altra scheda (Esci).", icon=":material/lock:")
    elif st.context.cookies.get(COOKIE):
        st.warning("La sessione non e' piu' attiva: il server e' stato riavviato (o il PC spento) e la passphrase, "
                   "che sta solo in memoria, va inserita di nuovo.", icon=":material/lock_clock:")
    _cookie(None)
    nuovo = not file_database().exists()
    if not CIFRATO:
        st.error("Modulo di cifratura non installato (sqlcipher3). Rilancia l'installazione (install.sh su Linux, install.cmd su Windows).")
        st.stop()
    with st.form("accesso"):
        if nuovo:
            st.markdown("Primo avvio: scegli la passphrase che cifra il database. **Non e' recuperabile**: conservala con cura.")
            p1 = st.text_input("Passphrase", type="password")
            p2 = st.text_input("Ripeti la passphrase", type="password")
        else:
            p1 = st.text_input("Passphrase del database", type="password")
            p2 = p1
        if st.form_submit_button("Apri", type="primary"):
            if nuovo and (len(p1) < 8 or p1 != p2):
                st.error("Le passphrase non coincidono o sono troppo corte (minimo 8 caratteri).")
                st.stop()
            try:
                conn = apri(file_database(), p1)
                from spese.tassonomia_utente import carica
                carica(conn)
                with st.spinner("Ricalcolo delle analisi…"):
                    from spese.ricalcolo import ricalcola
                    st.session_state["ultimo_riepilogo"] = ricalcola(conn)
                st.session_state["conn"] = conn
                st.session_state["token"] = sessioni.apri(p1)
                st.session_state["generazione"] = sessioni.generazione()
                st.session_state.pop("motivo_uscita", None)
                st.rerun()
            except PassphraseErrata:
                st.error("Passphrase errata.")
            except SchemaPiuRecente as e:
                st.error(str(e).capitalize() + ".")
    st.caption("Versione %s · database: %s" % (VERSIONE, file_database()))


def _riapri(token):
    """Apre il database con la passphrase tenuta in memoria (pagina ricaricata, nuova scheda o passphrase
    cambiata da un'altra scheda). Se non riesce la sessione si chiude per tutte le schede."""
    generazione = sessioni.generazione()
    try:
        _conn = apri(file_database(), sessioni.passphrase())
        from spese.tassonomia_utente import carica as _carica
        _carica(_conn)
    except Exception:
        sessioni.chiudi()
        return False
    st.session_state["conn"] = _conn
    st.session_state["token"] = token
    st.session_state["generazione"] = generazione
    sessioni.tocca()
    return True


if "conn" in st.session_state:
    esito = sessioni.stato(st.session_state.get("token"), DURATA)
    if esito != "valida":
        chiudi_sessione("inattivita" if esito == "scaduta" else "chiusa_altrove")
    elif st.session_state.get("generazione") != sessioni.generazione():
        # passphrase cambiata in un'altra scheda: questa connessione usa ancora la chiave vecchia
        try:
            st.session_state["conn"].close()
        except Exception:
            pass
        if not _riapri(st.session_state["token"]):
            chiudi_sessione("chiusa_altrove")
    else:
        sessioni.tocca()                                       # ogni azione dell'utente rinnova la sessione
elif sessioni.stato(st.context.cookies.get(COOKIE), DURATA) == "valida":
    # pagina ricaricata (o nuova scheda) con il codice della sessione: si riapre il database senza passphrase
    _riapri(st.context.cookies.get(COOKIE))

from spese.ui import pagine_analisi, pagine_classificazione, pagine_dati, pagine_impostazioni  # noqa: E402

# (gruppo, funzione, titolo, icona, indirizzo)
PAGINE = [
    ("Analisi", pagine_analisi.andamento, "Andamento mensile", ":material/show_chart:", "andamento"),
    ("Analisi", pagine_analisi.ripartizione, "Dove vanno i soldi", ":material/bar_chart:", "ripartizione"),
    ("Analisi", pagine_analisi.etichette, "Etichette", ":material/sell:", "etichette"),
    ("Analisi", pagine_analisi.portafoglio, "Portafoglio", ":material/account_balance_wallet:", "portafoglio"),
    ("Dati", pagine_dati.importa, "Importa", ":material/upload_file:", "importa"),
    ("Dati", pagine_dati.revisione, "Revisione", ":material/rule:", "revisione"),
    ("Dati", pagine_dati.movimenti, "Movimenti", ":material/list:", "movimenti"),
    ("Classificazione", pagine_classificazione.automatica, "Classificazione automatica", ":material/smart_toy:", "automatica"),
    ("Configurazione", pagine_impostazioni.conti, "Conti e copertura", ":material/account_balance:", "conti"),
    ("Configurazione", pagine_classificazione.regole, "Regole e tassonomia", ":material/tune:", "regole"),
    ("Configurazione", pagine_impostazioni.impostazioni, "Impostazioni", ":material/settings:", "impostazioni"),
]

if "conn" not in st.session_state:
    # stesse pagine e indirizzi, tutte sulla richiesta di passphrase e senza menu: se la sessione scade su
    # una pagina, l'indirizzo resta valido e dopo l'accesso si torna li'
    st.navigation([st.Page(accesso, title=t, icon=":material/lock:", url_path=u, default=(k == 0))
                   for k, (_, _, t, _, u) in enumerate(PAGINE)], position="hidden").run()
    st.stop()


@st.fragment(run_every=30)
def sorveglia_sessione():
    """Gira da solo ogni 30 secondi senza contare come attivita': quando la sessione scade porta subito
    alla richiesta della passphrase, invece di lasciare una pagina che non salva piu' nulla."""
    resto = secondi_rimasti()
    if "conn" not in st.session_state or resto <= 0 or \
            sessioni.stato(st.session_state.get("token"), DURATA) != "valida":
        st.rerun(scope="app")
    if resto < 300:
        st.caption("Senza azioni la sessione si chiude tra %d min." % (resto // 60 + 1))


from spese import attributi, portafoglio  # noqa: E402
from spese.ui.comune import avviso_rivedere, mostra_filtri  # noqa: E402

pagine = {}
oggetti = []
for k, (gruppo, funzione, titolo, icona, indirizzo) in enumerate(PAGINE):
    if indirizzo == "etichette" and not attributi.etichette_attive(st.session_state["conn"]):
        continue                                        # sezione spenta in Impostazioni
    if indirizzo == "portafoglio" and not portafoglio.attiva(st.session_state["conn"]):
        continue
    pg = st.Page(funzione, title=titolo, icon=icona, url_path=indirizzo, default=(k == 0))
    pagine.setdefault(gruppo, []).append(pg)
    oggetti.append((indirizzo, pg))
corrente = st.navigation(pagine, position="hidden")
st.session_state["_pagine"] = dict(oggetti)          # per i clic sui grafici che portano a un'altra pagina
CON_FILTRI = {"andamento", "ripartizione", "etichette", "movimenti", "portafoglio"}

# barra laterale disegnata qui, uguale per tutte le pagine: filtri in alto (solo dove servono), poi il menu.
# Stando nel file principale, i filtri conservano la scelta passando da una pagina all'altra.
st.session_state.pop("filtri_attivi", None)
with st.sidebar:
    _cookie(st.session_state["token"])
    st.html('<div class="marchio"><div class="logo" aria-hidden="true">€</div><div><b>CashFlow Analyzer</b>'
            '<span>Database cifrato, solo locale</span></div></div>')
    if any(pg is corrente and u in CON_FILTRI for u, pg in oggetti):
        with st.container(key="filtri"):
            mostra_filtri()
    avviso_rivedere(st.session_state["_pagine"].get("revisione"))
    for gruppo, elenco in pagine.items():
        st.html('<div class="gruppo-menu">%s</div>' % gruppo)
        for pg in elenco:
            if pg is corrente:
                with st.container(key="menu_attivo"):
                    st.page_link(pg)
            else:
                st.page_link(pg)
    with st.container(key="piede"):
        sorveglia_sessione()
        if st.button("Esci e chiudi il database", icon=":material/logout:", key="esci"):
            chiudi_sessione("uscita")
            st.rerun()
        st.html('<div class="versione">CashFlow Analyzer %s · © 2026 %s</div>' % (VERSIONE, AUTORE))

corrente.run()
