#!/usr/bin/env bash
# Installa l'app in un ambiente Python isolato (.venv) e crea l'icona nel menu.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
APP="$PWD"

PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "Serve Python 3.11 o successivo (Linux Mint 22 ha il 3.12)." >&2
  exit 1
fi
if ! "$PY" -m venv --help >/dev/null 2>&1 || ! "$PY" -c 'import ensurepip' 2>/dev/null; then
  echo "Manca il modulo venv. Installalo con:" >&2
  echo "  sudo apt install python3-venv" >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creo l'ambiente Python in $APP/.venv ..."
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip
echo "Installo le dipendenze (qualche minuto la prima volta) ..."
.venv/bin/python -m pip install --quiet -r requirements.txt
.venv/bin/python -c 'import sqlcipher3, streamlit; print("Dipendenze OK")'

chmod +x avvia.sh
DESK="$HOME/.local/share/applications"
mkdir -p "$DESK"
rm -f "$DESK/spese-famiglia.desktop"            # voce del menu delle versioni fino alla 0.9 (vecchio nome)
cat > "$DESK/cashflow-analyzer.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=CashFlow Analyzer
Comment=Analisi locale di entrate e uscite
Exec="$APP/avvia.sh"
Icon=$APP/assets/icona.png
Terminal=false
Categories=Office;Finance;
DESKTOP
chmod +x "$DESK/cashflow-analyzer.desktop"
update-desktop-database "$DESK" >/dev/null 2>&1 || true

if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
  echo "Nota: per l'icona nell'area di notifica servono le librerie GTK di sistema:"
  echo "  sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-xapp-1.0"
fi
chmod +x tray.py

echo
echo "Installazione completata."
echo "Avvio: menu > Ufficio > CashFlow Analyzer, oppure $APP/avvia.sh"
echo "I dati (cifrati) stanno in ${SPESE_DATI:-$HOME/.local/share/spese-app}"
