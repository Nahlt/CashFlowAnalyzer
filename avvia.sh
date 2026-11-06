#!/usr/bin/env bash
# Avvia l'app sul solo computer locale (127.0.0.1), l'icona nell'area di notifica e il browser.
#   ./avvia.sh                avvia server e icona (se gia' attivi riapre solo il browser)
#   ./avvia.sh --riavvia      riavvia il server (le schede aperte tornano alla richiesta della passphrase)
#   ./avvia.sh --ferma        ferma server e icona
#   ./avvia.sh --ferma-server ferma solo il server (lo usa l'icona)
#   ./avvia.sh --senza-icona  avvia senza icona nell'area di notifica
set -euo pipefail
umask 077
cd "$(dirname "$(readlink -f "$0")")"
APP="$PWD"
PORTA="${SPESE_PORTA:-8501}"
URL="http://127.0.0.1:$PORTA"
DATI="${SPESE_DATI:-$HOME/.local/share/spese-app}"
mkdir -p "$DATI" && chmod 700 "$DATI"
PID="$DATI/server.pid"
PID_ICONA="$DATI/icona.pid"
LOG="$DATI/server.log"
# l'icona usa il Python di sistema, che su Linux Mint ha gia' le librerie grafiche (GTK)
PY_SISTEMA="${SPESE_PYTHON_ICONA:-/usr/bin/python3}"
export SPESE_PORTA="$PORTA" SPESE_DATI="$DATI"

# il processo del file pid e' vivo ed e' proprio il nostro ($2: testo atteso nella riga di comando). Dopo un
# riavvio del PC il numero salvato puo' appartenere a un altro programma: non va considerato ne' fermato.
vivo() {
  [ -f "$1" ] || return 1
  local p riga
  p="$(cat "$1" 2>/dev/null)"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null || return 1
  riga="$(tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null)" || return 1
  [[ "$riga" == *"$2"* ]]
}

risponde() {
  "$PY_SISTEMA" - "$URL" <<'PY' 2>/dev/null
import sys, urllib.request
urllib.request.urlopen(sys.argv[1] + "/_stcore/health", timeout=1)
PY
}

ferma_server() {
  if vivo "$PID" streamlit; then
    kill "$(cat "$PID")" 2>/dev/null || true
    for _ in $(seq 1 40); do vivo "$PID" streamlit || break; sleep 0.25; done
    vivo "$PID" streamlit && kill -9 "$(cat "$PID")" 2>/dev/null || true
    echo "Server fermato."
  else
    echo "Il server non e' in esecuzione."
  fi
  rm -f "$PID"
}

avvia_server() {
  if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import importlib.util as u, sys; sys.exit(not u.find_spec("streamlit"))' 2>/dev/null; then
    echo "App non installata: lancia prima ./install.sh" >&2
    exit 1
  fi
  vivo "$PID" streamlit && return 0
  # "python -m streamlit" invece di .venv/bin/streamlit: funziona anche se la cartella dell'app e' stata spostata
  nohup .venv/bin/python -m streamlit run app.py --server.port "$PORTA" --server.address 127.0.0.1 >"$LOG" 2>&1 &
  echo $! >"$PID"
  for _ in $(seq 1 80); do risponde && return 0; sleep 0.5; done
  echo "Il server non risponde: vedi $LOG" >&2
  return 1
}

avvia_icona() {
  vivo "$PID_ICONA" tray.py && return 0
  if ! "$PY_SISTEMA" -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
    echo "Icona nell'area di notifica non disponibile (mancano le librerie GTK: sudo apt install python3-gi gir1.2-gtk-3.0)." >&2
    return 0
  fi
  nohup "$PY_SISTEMA" "$APP/tray.py" >"$DATI/icona.log" 2>&1 &
  echo $! >"$PID_ICONA"
}

apri_browser() {
  echo "CashFlow Analyzer: $URL"
  ( xdg-open "$URL" >/dev/null 2>&1 & )      # in secondo piano: non deve bloccare lo script
}

case "${1:-}" in
  --ferma)
    ferma_server
    if vivo "$PID_ICONA" tray.py; then kill "$(cat "$PID_ICONA")" 2>/dev/null || true; fi
    rm -f "$PID_ICONA"
    ;;
  --ferma-server)
    ferma_server
    ;;
  --riavvia)
    ferma_server >/dev/null
    avvia_server && echo "Server riavviato su $URL."
    ;;
  --senza-icona)
    avvia_server
    apri_browser
    ;;
  "")
    avvia_server
    avvia_icona
    apri_browser
    ;;
  *)
    echo "Uso: $0 [--riavvia | --ferma | --ferma-server | --senza-icona]" >&2
    exit 1
    ;;
esac
