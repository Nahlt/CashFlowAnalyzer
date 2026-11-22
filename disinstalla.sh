#!/usr/bin/env bash
# Disinstalla l'app.
#   ./disinstalla.sh               toglie programma installato (ambiente .venv), icona del menu e chiave di Claude.
#                                  I DATI RESTANO in ~/.local/share/CashFlowAnalyzer (spese-app fino alla 0.20.1).
#   ./disinstalla.sh --anche-dati  cancella anche database, backup e log (chiede conferma scritta).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
APP="$PWD"
# cartella dei dati: la stessa dell'app (spese/percorsi.py). Quella vecchia (spese-app, fino alla 0.20.1) si
# sposta da sola al primo avvio: se non e' ancora successo, i dati sono li'
BASE="${XDG_DATA_HOME:-$HOME/.local/share}"
if [ -n "${SPESE_DATI:-}" ]; then
  DATI="$SPESE_DATI"
elif [ ! -f "$BASE/CashFlowAnalyzer/spese.db" ] && [ -f "$BASE/spese-app/spese.db" ]; then
  DATI="$BASE/spese-app"
else
  DATI="$BASE/CashFlowAnalyzer"
fi
DESK="$HOME/.local/share/applications/cashflow-analyzer.desktop"
DESK_VECCHIO="$HOME/.local/share/applications/spese-famiglia.desktop"   # nome fino alla 0.9

ANCHE_DATI=0
case "${1:-}" in
  "") ;;
  --anche-dati) ANCHE_DATI=1 ;;
  *) echo "Uso: $0 [--anche-dati]" >&2; exit 1 ;;
esac

# 1. Ferma il server, se in esecuzione (solo se il numero salvato e' davvero il nostro processo)
vivo() {
  [ -f "$1" ] || return 1
  local p riga
  p="$(cat "$1" 2>/dev/null)"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null || return 1
  riga="$(tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null)" || return 1
  [[ "$riga" == *"$2"* ]]
}
if vivo "$DATI/server.pid" streamlit; then
  kill "$(cat "$DATI/server.pid")" && echo "Server fermato."
fi
rm -f "$DATI/server.pid"
if vivo "$DATI/icona.pid" tray.py; then
  kill "$(cat "$DATI/icona.pid")" && echo "Icona nell'area di notifica chiusa."
fi
rm -f "$DATI/icona.pid"

# 2. Chiave API di Claude nel portachiavi (serve il Python dell'app, quindi prima di togliere .venv)
if [ -x .venv/bin/python ] && [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
  .venv/bin/python - <<'PY' || echo "Chiave di Claude: portachiavi non raggiungibile, eventualmente toglila da 'Password e chiavi' (voce CashFlowAnalyzer o spese-app)."
import keyring, keyring.errors
tolte = 0
for nome in ("CashFlowAnalyzer", "spese-app"):     # spese-app: nome fino alla 0.20.1
    try:
        keyring.delete_password(nome, "anthropic")
        tolte += 1
    except keyring.errors.PasswordDeleteError:
        pass
print("Chiave di Claude tolta dal portachiavi." if tolte else "Nessuna chiave di Claude nel portachiavi.")
PY
fi

# 3. Icona del menu e ambiente Python
if [ -f "$DESK" ] || [ -f "$DESK_VECCHIO" ]; then
  rm -f "$DESK" "$DESK_VECCHIO"
  update-desktop-database "$(dirname "$DESK")" >/dev/null 2>&1 || true
  echo "Icona del menu tolta."
fi
rm -rf .venv
find . -name __pycache__ -type d -prune -exec rm -rf {} +
echo "Ambiente Python tolto."

# 4. Dati, solo su richiesta esplicita
if [ "$ANCHE_DATI" = 1 ] && [ -d "$DATI" ]; then
  echo
  echo "ATTENZIONE: verranno cancellati database, backup e log in:"
  echo "  $DATI"
  ls -1 "$DATI" 2>/dev/null | sed 's/^/    /'
  echo "Non si possono recuperare. Gli eventuali export CSV salvati altrove non vengono toccati."
  read -r -p "Scrivi CANCELLA per confermare: " risposta
  if [ "$risposta" = "CANCELLA" ]; then
    rm -rf -- "$DATI"
    echo "Dati cancellati."
  else
    echo "Dati non cancellati."
  fi
elif [ -d "$DATI" ]; then
  echo
  echo "I dati restano in $DATI (database cifrato e backup)."
  echo "Reinstallando con ./install.sh li ritrovi con la stessa passphrase."
fi

echo
echo "Per finire puoi cancellare la cartella del programma:"
echo "  rm -rf \"$APP\""
