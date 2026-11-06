"""Normalizzazione di numeri e date secondo il formato dichiarato nel profilo.

Importi in centesimi interi; quantita', prezzi e cambi come Decimal (testo).
Mai virgola mobile: i numeri Excel passano dalla loro rappresentazione decimale.
"""
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

try:
    from zoneinfo import ZoneInfo
    ROMA = ZoneInfo("Europe/Rome")
except Exception:  # pragma: no cover
    ROMA = None


class FormatoNonValido(ValueError):
    pass


def a_decimal(valore, formato: str):
    """Restituisce Decimal o None (vuoto). formato: italiano | punto | inglese | excel."""
    if valore is None:
        return None
    if isinstance(valore, bool):
        raise FormatoNonValido(repr(valore))
    if isinstance(valore, int):
        return Decimal(valore)
    if isinstance(valore, float):
        # repr() da' la rappresentazione decimale piu' corta che identifica il float (es. -1251.63)
        return Decimal(repr(valore))
    s = str(valore).strip().replace(" ", "").replace(" ", "")
    s = s.lstrip("'")              # alcune banche (es. Credit Agricole) scrivono '-4,99 per forzare il testo
    if s in ("", "-"):
        return None
    if s.startswith("+"):
        s = s[1:]
    if formato == "italiano":
        if not re.fullmatch(r"-?\d{1,3}(\.\d{3})*(,\d+)?|-?\d+(,\d+)?", s):
            raise FormatoNonValido(s)
        s = s.replace(".", "").replace(",", ".")
    elif formato == "inglese":
        # 1,234.56 (separatore delle migliaia: virgola)
        if not re.fullmatch(r"-?\d{1,3}(,\d{3})*(\.\d+)?|-?\d+(\.\d+)?", s):
            raise FormatoNonValido(s)
        s = s.replace(",", "")
    elif formato in ("punto", "excel"):
        if not re.fullmatch(r"-?\d+(\.\d+)?", s):
            raise FormatoNonValido(s)
    else:
        raise FormatoNonValido("formato numeri sconosciuto: " + formato)
    try:
        return Decimal(s)
    except InvalidOperation:
        raise FormatoNonValido(s)


def a_centesimi(valore, formato: str):
    """Decimal -> centesimi interi. Solleva FormatoNonValido se ci sono cifre non nulle oltre i centesimi."""
    d = a_decimal(valore, formato)
    if d is None:
        return None
    c = d * 100
    if c != c.to_integral_value():
        # una cella Excel calcolata con una formula puo' valere 12.300000000000001: e' 12,30, non un errore
        if isinstance(valore, float) and not isinstance(valore, bool) and \
                abs(c - c.to_integral_value()) < Decimal("0.000001"):
            return int(c.to_integral_value())
        raise FormatoNonValido("cifre oltre i centesimi: %s" % d)
    return int(c)


def testo_decimal(valore, formato: str):
    d = a_decimal(valore, formato)
    return None if d is None else format(d.normalize(), "f")


def a_data(valore, formato: str):
    """Restituisce (data ISO 'AAAA-MM-GG', ora 'HH:MM' o None)."""
    if valore is None or (isinstance(valore, str) and not valore.strip()):
        return None, None
    if isinstance(valore, datetime):
        return valore.date().isoformat(), None
    if isinstance(valore, date):
        return valore.isoformat(), None
    s = str(valore).strip()
    if formato == "iso_utc":
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if ROMA:
            dt = dt.astimezone(ROMA)
        return dt.date().isoformat(), dt.strftime("%H:%M")
    if formato == "iso":
        return date.fromisoformat(s[:10]).isoformat(), None
    try:
        return datetime.strptime(s, formato).date().isoformat(), None
    except ValueError:
        raise FormatoNonValido("data '%s' non nel formato %s" % (s, formato))


def euro(cent) -> str:
    """Formatta centesimi in euro all'italiana."""
    if cent is None:
        return ""
    segno = "-" if cent < 0 else ""
    c = abs(int(cent))
    intero = "{:,}".format(c // 100).replace(",", ".")
    return "%s%s,%02d €" % (segno, intero, c % 100)
