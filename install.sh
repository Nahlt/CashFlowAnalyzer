#!/usr/bin/env bash
# Installa l'app in un ambiente Python isolato (.venv) e crea l'icona nel menu.
# Se manca Python 3.11 o successivo (o il modulo venv) propone il comando della distribuzione e lo esegue
# dopo conferma.  ./install.sh [--si]   (--si: risponde si' alle domande)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
APP="$PWD"

SI=0
[ "${1:-}" = "--si" ] && SI=1          # risponde si' alle domande

conferma() {
  [ "$SI" = 1 ] && return 0
  read -r -p "$1 [S/n] " r
  [ -z "$r" ] || [[ "$r" =~ ^[sSyY] ]]
}

# Python adatto: 3.11 o successivo, con i moduli venv ed ensurepip
adatto() {
  command -v "$1" >/dev/null 2>&1 &&
    "$1" -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

trova_python() {
  local p
  for p in "${PYTHON:-}" python3 python3.13 python3.12 python3.11; do
    [ -n "$p" ] && adatto "$p" && { echo "$p"; return 0; }
  done
  return 1
}

# Comando per installare Python con il gestore di pacchetti della distribuzione (vuoto se sconosciuta)
comando_installazione() {
  local id="" simili=""
  if [ -r /etc/os-release ]; then
    id="$(. /etc/os-release; echo "${ID:-}")"
    simili="$(. /etc/os-release; echo "${ID_LIKE:-}")"
  fi
  case " $id $simili " in
    *" debian "*|*" ubuntu "*|*" linuxmint "*)
      local v
      if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        v="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
        echo "sudo apt install -y python3-venv python$v-venv"
      else
        # distribuzione con Python di serie troppo vecchio (es. Mint 21, Ubuntu 22.04): versione piu' recente
        # dai pacchetti ufficiali, se c'e'
        for v in 3.13 3.12 3.11; do
          if apt-cache show "python$v-venv" >/dev/null 2>&1; then
            echo "sudo apt install -y python$v python$v-venv"
            return 0
          fi
        done
      fi ;;
    *" fedora "*|*" rhel "*|*" centos "*) echo "sudo dnf install -y python3" ;;
    *" arch "*) echo "sudo pacman -S --needed python" ;;
    *" suse "*|*" opensuse "*) echo "sudo zypper install -y python3" ;;
  esac
}

if ! PY="$(trova_python)"; then
  echo "Serve Python 3.11 o successivo con il modulo venv (Linux Mint 22 ha il 3.12)."
  CMD="$(comando_installazione)"
  if [ -z "$CMD" ]; then
    echo "Installalo con il gestore di pacchetti della tua distribuzione e rilancia ./install.sh." >&2
    exit 1
  fi
  echo "Comando per installarlo (chiede la password di amministratore):"
  echo "  $CMD"
  if ! conferma "Lo eseguo adesso?"; then
    echo "Installazione annullata: esegui il comando e rilancia ./install.sh." >&2
    exit 1
  fi
  $CMD
  if ! PY="$(trova_python)"; then
    echo "Python adatto ancora non trovato: installalo a mano e rilancia ./install.sh." >&2
    exit 1
  fi
fi
echo "Python: $(command -v "$PY") ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"

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
