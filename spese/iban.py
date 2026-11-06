"""IBAN: normalizzazione e controllo di coerenza (ISO 13616).

normalizza: toglie tutti gli spazi (anche interni) e i trattini, maiuscolo.
verifica: struttura (paese + 2 cifre di controllo + codice alfanumerico), lunghezza attesa per il paese
e cifra di controllo modulo 97. Restituisce il motivo dell'errore, o None se l'IBAN e' coerente.
"""
import re

# lunghezza dell'IBAN per paese (registro SWIFT); per i paesi non elencati vale il limite generale 15-34
LUNGHEZZE = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22, "BR": 29,
    "CH": 21, "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24,
    "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27, "GT": 28, "HR": 21,
    "HU": 28, "IE": 22, "IL": 23, "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LI": 21,
    "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30,
    "NL": 18, "NO": 15, "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24,
    "SE": 24, "SI": 19, "SK": 24, "SM": 27, "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}


def normalizza(testo):
    """'it60 x054 2811-1010 0000 0123 456' -> 'IT60X0542811101000000123456'. Vuoto -> ''."""
    if not isinstance(testo, str):
        return ""
    return re.sub(r"[\s\-]+", "", testo).upper()


def verifica(iban):
    """Motivo dell'errore per un IBAN gia' normalizzato, oppure None se e' coerente."""
    if not re.fullmatch(r"[A-Z0-9]+", iban):
        return "contiene caratteri non ammessi (solo lettere e cifre)"
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", iban):
        return "deve iniziare con il codice del paese (2 lettere) e 2 cifre di controllo"
    paese = iban[:2]
    attesa = LUNGHEZZE.get(paese)
    if attesa and len(iban) != attesa:
        return "un IBAN %s ha %d caratteri, questo ne ha %d" % (paese, attesa, len(iban))
    if not attesa and not 15 <= len(iban) <= 34:
        return "lunghezza non valida (%d caratteri)" % len(iban)
    numero = "".join(str(int(ch, 36)) for ch in iban[4:] + iban[:4])
    if int(numero) % 97 != 1:
        return "la cifra di controllo non torna: probabilmente un carattere e' sbagliato o scambiato"
    return None


def controlla(testo):
    """(iban normalizzato o None se vuoto, motivo dell'errore o None)."""
    n = normalizza(testo)
    if not n:
        return None, None
    return n, verifica(n)
