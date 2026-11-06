"""Sessione che sopravvive al ricaricamento della pagina.

Streamlit crea una sessione nuova a ogni ricaricamento: senza altro, si tornerebbe alla passphrase. Dopo
l'accesso il browser riceve un cookie con un codice casuale (token); il server tiene in memoria, solo
finche' resta acceso, la passphrase e l'impronta dei codici validi. A un ricaricamento con un codice valido
la pagina riapre il database da sola.

Cosa NON succede: la passphrase non viene mai scritta su disco ne' nel cookie. Se il server si riavvia
(o il PC si spegne) la memoria si svuota e serve di nuovo la passphrase. "Esci" svuota la memoria e
invalida tutti i codici, anche delle altre schede. Dopo il periodo di inattivita' configurato (config/
impostazioni.yaml, sessione.minuti_inattivita) succede lo stesso.
"""
import hashlib
import secrets
import threading
import time

_lock = threading.Lock()
# generazione: cresce a ogni cambio di passphrase, cosi' le altre schede sanno di dover riaprire il database
_stato = {"passphrase": None, "impronte": set(), "ultima": 0.0, "generazione": 0}


def _impronta(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def apri(passphrase):
    """Dopo un accesso riuscito: ricorda la passphrase in memoria e restituisce un nuovo codice per il cookie."""
    token = secrets.token_urlsafe(32)
    with _lock:
        _stato["passphrase"] = passphrase
        _stato["impronte"].add(_impronta(token))
        _stato["ultima"] = time.time()
    return token


def stato(token, durata_s):
    """'valida', 'scaduta' (troppa inattivita': la memoria viene svuotata) o 'sconosciuta'."""
    with _lock:
        if not token or _stato["passphrase"] is None or _impronta(token) not in _stato["impronte"]:
            return "sconosciuta"
        if time.time() - _stato["ultima"] > durata_s:
            _svuota()
            return "scaduta"
        return "valida"


def passphrase():
    with _lock:
        return _stato["passphrase"]


def generazione():
    with _lock:
        return _stato["generazione"]


def aggiorna_passphrase(nuova):
    """Dopo un cambio di passphrase: i codici restano validi, ma le schede aperte devono riaprire il database
    con la passphrase nuova (la loro connessione usa ancora la vecchia chiave). Restituisce la generazione."""
    with _lock:
        if _stato["passphrase"] is not None:
            _stato["passphrase"] = nuova
        _stato["generazione"] += 1
        _stato["ultima"] = time.time()
        return _stato["generazione"]


def tocca():
    """Un'azione dell'utente: rinnova il periodo di inattivita'."""
    with _lock:
        _stato["ultima"] = time.time()


def secondi_rimasti(durata_s):
    with _lock:
        if _stato["passphrase"] is None:
            return 0
        return durata_s - (time.time() - _stato["ultima"])


def chiudi():
    """Esci: dimentica passphrase e codici (vale per tutte le schede aperte)."""
    with _lock:
        _svuota()


def _svuota():
    _stato["passphrase"] = None
    _stato["impronte"].clear()
