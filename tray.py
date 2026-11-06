#!/usr/bin/env python3
"""Icona di CashFlow Analyzer nell'area di notifica (Linux Mint e altri desktop).

Si avvia con avvia.sh usando il Python di sistema, che su Mint ha gia' GTK: non dipende dall'ambiente
.venv dell'app. Menu: apri nel browser, riavvia il server, ferma (server e icona). Ogni 5 secondi
controlla se il server risponde e aggiorna stato, suggerimento e voci del menu.

Sistema predefinito: area di notifica classica (Gtk.StatusIcon), verificata dopo 3 secondi; se il desktop
non la offre si passa a XApp.StatusIcon e poi ad AppIndicator.
"""
import os
import signal
import subprocess
import sys
import urllib.request
import warnings
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

# Gtk.StatusIcon e' deprecato in GTK 3 ma e' l'area di notifica che Cinnamon mostra sempre
warnings.filterwarnings("ignore", category=DeprecationWarning)

APP = Path(__file__).resolve().parent
AVVIA = str(APP / "avvia.sh")
PORTA = os.environ.get("SPESE_PORTA", "8501")
URL = "http://127.0.0.1:%s" % PORTA
DATI = Path(os.environ.get("SPESE_DATI") or Path.home() / ".local/share/spese-app")
ICONA = str(APP / "assets" / "icona.png")
NOME = "CashFlow Analyzer"


def server_attivo():
    try:
        urllib.request.urlopen(URL + "/_stcore/health", timeout=1)
        return True
    except Exception:
        return False


def notifica(testo):
    try:
        subprocess.Popen(["notify-send", "-i", ICONA, NOME, testo], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass


class Icona:
    def __init__(self):
        self.occupato = False          # riavvio o arresto in corso
        self.attivo = None
        self.menu = Gtk.Menu()
        self.voce_stato = Gtk.MenuItem(label="Controllo in corso…")
        self.voce_stato.set_sensitive(False)
        self.voce_apri = Gtk.MenuItem(label="Apri nel browser")
        self.voce_riavvia = Gtk.MenuItem(label="Riavvia")
        self.voce_ferma = Gtk.MenuItem(label="Ferma")
        self.voce_apri.connect("activate", lambda *_: self.apri())
        self.voce_riavvia.connect("activate", lambda *_: self.riavvia())
        self.voce_ferma.connect("activate", lambda *_: self.ferma())
        for v in (self.voce_stato, Gtk.SeparatorMenuItem(), self.voce_apri, self.voce_riavvia, self.voce_ferma):
            self.menu.append(v)
        self.menu.show_all()
        self.tipo = self._crea_icona()
        self.aggiorna()
        GLib.timeout_add_seconds(5, self.aggiorna)

    # --- icona: tre sistemi, dal piu' adatto a Mint al piu' generico --------------------------
    def _crea_icona(self):
        """Predefinito: area di notifica classica (Gtk.StatusIcon), che su Linux Mint con Cinnamon si vede
        sempre. Se dopo 3 secondi non risulta inserita nel pannello (desktop senza area classica) si passa
        a XApp e poi ad AppIndicator. SPESE_ICONA=gtk|xapp|appindicator forza un sistema."""
        forzato = os.environ.get("SPESE_ICONA", "").lower()
        if forzato == "xapp":
            return self._icona_xapp() or self._icona_appindicator() or self._icona_gtk()
        if forzato == "appindicator":
            return self._icona_appindicator() or self._icona_gtk()
        tipo = self._icona_gtk()
        if forzato != "gtk":
            GLib.timeout_add_seconds(3, self._verifica_gtk)
        return tipo

    def _verifica_gtk(self):
        if self.tipo == "gtk" and not self.icona.is_embedded():
            self.icona.set_visible(False)
            self.tipo = self._icona_xapp() or self._icona_appindicator() or self._icona_gtk()
            print("Area di notifica classica non disponibile: uso %s" % self.tipo, flush=True)
            self.attivo = None
            self.aggiorna()
        else:
            print("Icona nell'area di notifica classica (GTK)", flush=True)
        return False

    def _icona_xapp(self):
        try:
            gi.require_version("XApp", "1.0")
            from gi.repository import XApp
        except (ValueError, ImportError):
            return None
        self.icona = XApp.StatusIcon()
        self.icona.set_name("cashflow-analyzer")
        self.icona.set_icon_name(ICONA)
        self.icona.set_primary_menu(self.menu)
        self.icona.set_secondary_menu(self.menu)
        GLib.timeout_add_seconds(3, self._stato_xapp)
        return "xapp"

    def _stato_xapp(self):
        if self.tipo == "xapp":
            nomi = {0: "nativa (pannello del desktop)", 1: "di ripiego (area di notifica classica)", 2: "NON SUPPORTATA"}
            print("Stato icona XApp: %s" % nomi.get(int(self.icona.get_state()), self.icona.get_state()), flush=True)
        return False

    def _icona_appindicator(self):
        for nome in ("AyatanaAppIndicator3", "AppIndicator3"):
            try:
                gi.require_version(nome, "0.1")
                mod = __import__("gi.repository." + nome, fromlist=[nome])
                self.icona = mod.Indicator.new("cashflow-analyzer", ICONA, mod.IndicatorCategory.APPLICATION_STATUS)
                self.icona.set_status(mod.IndicatorStatus.ACTIVE)
                self.icona.set_menu(self.menu)
                return "appindicator"
            except (ValueError, ImportError):
                continue
        return None

    def _icona_gtk(self):
        self.icona = Gtk.StatusIcon.new_from_file(ICONA)
        self.icona.connect("popup-menu", lambda i, b, t: self.menu.popup(None, None, Gtk.StatusIcon.position_menu, i, b, t))
        self.icona.connect("activate", lambda *_: self.menu.popup(None, None, Gtk.StatusIcon.position_menu, self.icona,
                                                                    1, Gtk.get_current_event_time()))
        return "gtk"

    def _suggerimento(self, testo):
        if self.tipo == "xapp":
            self.icona.set_tooltip_text(testo)
        elif self.tipo == "appindicator":
            self.icona.set_title(testo)
        else:
            self.icona.set_tooltip_text(testo)

    # --- stato -------------------------------------------------------------------------------
    def aggiorna(self):
        if self.occupato:
            return True
        attivo = server_attivo()
        if attivo != self.attivo:
            self.attivo = attivo
            stato = ("In esecuzione su 127.0.0.1:%s" % PORTA) if attivo else "Server fermo"
            self.voce_stato.set_label(stato)
            self.voce_apri.set_sensitive(attivo)
            self.voce_riavvia.set_sensitive(True)
            self.voce_ferma.set_sensitive(True)
            self.voce_riavvia.set_label("Riavvia" if attivo else "Avvia")
            self._suggerimento("%s · %s" % (NOME, stato))
        return True

    def _occupato(self, testo):
        self.occupato = True
        self.voce_stato.set_label(testo)
        self._suggerimento("%s · %s" % (NOME, testo))
        for v in (self.voce_apri, self.voce_riavvia, self.voce_ferma):
            v.set_sensitive(False)

    def _libero(self):
        self.occupato = False
        self.attivo = None
        self.voce_ferma.set_sensitive(True)
        self.aggiorna()

    # --- azioni ------------------------------------------------------------------------------
    def apri(self):
        subprocess.Popen(["xdg-open", URL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def riavvia(self):
        era_attivo = self.attivo
        self._occupato("Riavvio in corso…" if era_attivo else "Avvio in corso…")
        proc = subprocess.Popen([AVVIA, "--riavvia"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        def finito(pid, codice):
            out = (proc.stdout.read() or "").strip()
            self._libero()
            if codice == 0:
                notifica("Server riavviato: nelle schede aperte inserisci di nuovo la passphrase." if era_attivo
                         else "Server avviato su %s." % URL)
            else:
                notifica("Riavvio non riuscito: %s" % (out.splitlines()[-1] if out else "vedi server.log"))
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, proc.pid, finito)

    def ferma(self):
        self._occupato("Arresto in corso…")
        proc = subprocess.Popen([AVVIA, "--ferma-server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def finito(pid, codice):
            notifica("Server fermato. Per riaprirlo usa il menu delle applicazioni o ./avvia.sh.")
            esci()
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, proc.pid, finito)


def esci(*_):
    try:
        (DATI / "icona.pid").unlink(missing_ok=True)
    finally:
        Gtk.main_quit()


def gia_attiva():
    """Pid di un'altra icona gia' in esecuzione, o None. Due copie si contendono lo stesso nome sul bus del
    desktop e la seconda non compare ("XAppStatusIcon: lost or could not acquire presence on dbus")."""
    try:
        pid = int((DATI / "icona.pid").read_text().strip())
    except (OSError, ValueError):
        return None
    if pid == os.getpid():
        return None
    try:
        riga = Path("/proc/%d/cmdline" % pid).read_bytes()
    except OSError:
        return None
    return pid if b"tray.py" in riga else None


def main():
    altra = gia_attiva()
    if altra:
        print("L'icona di %s e' gia' attiva (processo %d): non ne avvio un'altra." % (NOME, altra))
        return 0
    DATI.mkdir(parents=True, exist_ok=True)
    (DATI / "icona.pid").write_text(str(os.getpid()))
    icona = Icona()
    print("Icona avviata: sistema %s, file %s" % (icona.tipo, ICONA), flush=True)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, esci)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, esci)
    Gtk.main()


if __name__ == "__main__":
    sys.exit(main())
