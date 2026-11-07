#!/usr/bin/env bash
# Avvia l'app sul solo computer locale (127.0.0.1), l'icona nell'area di notifica e il browser.
#   ./avvia.sh                avvia server e icona (se gia' attivi riapre solo il browser)
#   ./avvia.sh --riavvia      riavvia il server (le schede aperte tornano alla richiesta della passphrase)
#   ./avvia.sh --ferma        ferma server e icona
#   ./avvia.sh --ferma-server ferma solo il server (lo usa l'icona)
#   ./avvia.sh --senza-icona  avvia senza icona nell'area di notifica
# Il lavoro lo fa avvia.py, lo stesso avviatore usato su Windows.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
if [ ! -x .venv/bin/python ]; then
  echo "App non installata: lancia prima ./install.sh" >&2
  exit 1
fi
exec .venv/bin/python avvia.py "$@"
