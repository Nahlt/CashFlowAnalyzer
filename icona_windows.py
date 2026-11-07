"""Icona di CashFlow Analyzer nell'area di notifica di Windows (pystray).

La avvia avvia.py con pythonw.exe dell'ambiente .venv. Menu: apri nel browser (anche con un clic
sull'icona), riavvia o avvia il server, ferma (server e icona). Ogni 5 secondi controlla se il server
risponde e aggiorna stato e suggerimento; si chiude da sola se avvia.py --ferma lo chiede.
"""
import os
import sys
import threading

import pystray
from PIL import Image

import avvia

NOME = "CashFlow Analyzer"


class Icona:
    def __init__(self):
        self.attivo = False
        self.occupato = None                  # testo dell'operazione in corso, o None
        self.fine = threading.Event()
        menu = pystray.Menu(
            pystray.MenuItem(lambda _: self.stato(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Apri nel browser", self.apri, default=True, enabled=lambda _: self.attivo and not self.occupato),
            pystray.MenuItem(lambda _: "Riavvia" if self.attivo else "Avvia", self.riavvia,
                             enabled=lambda _: not self.occupato),
            pystray.MenuItem("Ferma", self.ferma, enabled=lambda _: not self.occupato),
        )
        immagine = Image.open(avvia.APP / "assets" / "icona.png")
        self.icona = pystray.Icon("cashflow-analyzer", immagine, NOME, menu)

    def stato(self):
        if self.occupato:
            return self.occupato
        return ("In esecuzione su 127.0.0.1:%s" % avvia.PORTA) if self.attivo else "Server fermo"

    def aggiorna(self):
        self.icona.title = "%s · %s" % (NOME, self.stato())
        self.icona.update_menu()

    def sorveglia(self, icona):
        icona.visible = True
        while not self.fine.is_set():
            if avvia.STOP_ICONA.exists():
                avvia.STOP_ICONA.unlink(missing_ok=True)
                self.esci()
                return
            if not self.occupato:
                self.attivo = avvia.risponde()
                self.aggiorna()
            self.fine.wait(5)

    def _in_corso(self, testo, lavoro):
        def esegui():
            self.occupato = testo
            self.aggiorna()
            try:
                lavoro()
            finally:
                self.occupato = None
                self.attivo = avvia.risponde()
                self.aggiorna()
        threading.Thread(target=esegui, daemon=True).start()

    def apri(self, *_):
        avvia.apri_browser()

    def riavvia(self, *_):
        era_attivo = self.attivo

        def lavoro():
            avvia.ferma_server(messaggi=False)
            if avvia.avvia_server():
                self.icona.notify("Server riavviato: nelle schede aperte inserisci di nuovo la passphrase." if era_attivo
                                  else "Server avviato su %s." % avvia.URL, NOME)
            else:
                self.icona.notify("Avvio non riuscito: vedi %s" % avvia.LOG, NOME)
        self._in_corso("Riavvio in corso…" if era_attivo else "Avvio in corso…", lavoro)

    def ferma(self, *_):
        def lavoro():
            avvia.ferma_server(messaggi=False)
            self.icona.notify("Server fermato. Per riaprirlo usa il menu Start: CashFlow Analyzer.", NOME)
            self.esci()
        self._in_corso("Arresto in corso…", lavoro)

    def esci(self):
        self.fine.set()
        avvia.PID_ICONA.unlink(missing_ok=True)
        self.icona.stop()


def main():
    if avvia.vivo(avvia.PID_ICONA, "icona_windows.py"):
        return 0                              # un'altra icona e' gia' attiva
    avvia.scrivi_pid(avvia.PID_ICONA, os.getpid())
    icona = Icona()
    icona.icona.run(setup=icona.sorveglia)
    return 0


if __name__ == "__main__":
    sys.exit(main())
