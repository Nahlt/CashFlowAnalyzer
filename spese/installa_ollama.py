"""Installazione e avvio di Ollama (modello locale) dall'app, su Linux e Windows, e download dei modelli.

Parte solo su richiesta esplicita dell'utente, da Impostazioni o Classificazione automatica. Dalla rete
scarica il programma Ollama dalle fonti ufficiali e i modelli indicati (esce solo il loro nome): nessun
dato dell'utente.

- Linux: script ufficiale https://ollama.com/install.sh, eseguito come amministratore. La password la chiede
  la finestra di sistema (pkexec); senza desktop si usa sudo se non chiede password, altrimenti l'app mostra il
  comando da lanciare nel terminale. Il servizio di sistema riceve OLLAMA_NO_CLOUD=1.
- Windows: winget (pacchetto Ollama.Ollama, per il solo utente). Se winget manca o non riesce, l'installatore
  OllamaSetup.exe dell'ultima release su GitHub, eseguito solo se l'impronta SHA-256 corrisponde a quella
  pubblicata nella release e la firma digitale e' valida e di Ollama. Per l'utente si imposta
  OLLAMA_NO_CLOUD=1.

Il lavoro gira in un thread in background (un solo lavoro alla volta per tutto il server): la pagina ne
mostra l'avanzamento e puo' essere lasciata. Non usa il database: indirizzo e modelli arrivano gia' letti.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

WINDOWS = sys.platform == "win32"
URL_SCRIPT_LINUX = "https://ollama.com/install.sh"
URL_RELEASE = "https://api.github.com/repos/ollama/ollama/releases/latest"
WINGET_ID = "Ollama.Ollama"
COMANDO_LINUX = "curl -fsSL https://ollama.com/install.sh | sh"
SENZA_FINESTRA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class NonPossibile(Exception):
    """L'installazione non si puo' fare da qui: il messaggio dice cosa fare a mano."""

    def __init__(self, messaggio, comando=None):
        super().__init__(messaggio)
        self.comando = comando


def firma_di_ollama(firma):
    """La firma letta da PowerShell ("Stato|Soggetto del certificato") e' valida e il certificato e' intestato a
    Ollama? Si guarda il nome dell'intestatario (CN o O del soggetto), non una parola qualunque del soggetto:
    "CN=Not Ollama Fan Club" o un indirizzo con "ollama" dentro non bastano."""
    stato, _, soggetto = (firma or "").partition("|")
    if stato.strip() != "Valid":
        return False
    campi = re.findall(r'(?:^|,\s*)(CN|O)=("(?:[^"]|"")*"|[^,]*)', soggetto)
    nomi = {v.strip().strip('"').strip().lower() for _, v in campi}
    return any(n == "ollama" or re.fullmatch(r"ollama,? (inc\.?|incorporated|corp\.?|llc)", n) for n in nomi)


# --- stato di Ollama su questo PC ----------------------------------------------------------------------
def indirizzo_locale(url):
    """L'indirizzo configurato e' questo PC? Solo allora ha senso proporre di installare Ollama."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def risponde(url, timeout=2):
    import requests
    try:
        requests.get(url.rstrip("/") + "/api/tags", timeout=timeout).raise_for_status()
        return True
    except Exception:
        return False


def _cartella_windows():
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Programs" / "Ollama"


def eseguibile():
    """Percorso del programma ollama installato, o None."""
    trovato = shutil.which("ollama")
    if trovato:
        return Path(trovato)
    candidati = [_cartella_windows() / "ollama.exe"] if WINDOWS else [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
    return next((p for p in candidati if p.exists()), None)


def installato():
    return eseguibile() is not None


# --- comandi -------------------------------------------------------------------------------------------
def spezza_righe(buffer, fine=False):
    """Divide l'output di un comando in righe: ([(testo, sul_posto)], resto non ancora concluso).

    sul_posto = riga chiusa da un \\r da solo: winget e curl ridisegnano cosi' la barra di avanzamento, quindi la
    riga dopo la sostituisce. \\r\\n (fine riga di Windows) e \\n chiudono una riga normale. Un \\r in fondo al
    blocco resta nel resto, finche' non si sa se segue \\n (salvo a fine output)."""
    out, i = [], 0
    while True:
        cr, lf = buffer.find(b"\r", i), buffer.find(b"\n", i)
        j = min(k for k in (cr, lf, len(buffer)) if k >= 0)
        if j == len(buffer):
            break
        if j == cr:
            if j + 1 == len(buffer) and not fine:
                break
            if buffer[j + 1:j + 2] == b"\n":
                out.append((buffer[i:j], False))
                i = j + 2
            else:
                out.append((buffer[i:j], True))
                i = j + 1
        else:
            out.append((buffer[i:j], False))
            i = j + 1
    return out, buffer[i:]


def _pulisci(pezzo):
    testo = pezzo.decode("utf-8", "replace")
    return "".join(ch for ch in testo if ch == "\t" or ch >= " " and ch != "\x7f").strip()


def _esegui(argomenti, registra, env=None):
    """Esegue un comando senza finestre, riga per riga nel registro del lavoro. Restituisce il codice d'uscita.
    Le righe ridisegnate sul posto (barre di avanzamento) sostituiscono la precedente invece di accumularsi."""
    proc = subprocess.Popen([str(a) for a in argomenti], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env, creationflags=SENZA_FINESTRA)
    resto, sostituisci = b"", False

    def scrivi(parti):
        nonlocal sostituisci
        for pezzo, sul_posto in parti:
            testo = _pulisci(pezzo)
            if testo:
                registra(testo, sostituisci=sostituisci)
                sostituisci = sul_posto
            elif not sul_posto:
                sostituisci = False
    while True:
        blocco = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not blocco:
            break
        parti, resto = spezza_righe(resto + blocco)
        scrivi(parti)
    parti, resto = spezza_righe(resto, fine=True)
    scrivi(parti + [(resto, False)])
    return proc.wait()


def comando_amministratore():
    """Prefisso per eseguire come amministratore su Linux: [] se gia' root, pkexec con un desktop (finestra di
    sistema per la password), sudo se non chiede password. Altrimenti NonPossibile con il comando da terminale."""
    if os.geteuid() == 0:
        return []
    if shutil.which("pkexec") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return ["pkexec"]
    if shutil.which("sudo") and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
        return ["sudo", "-n"]
    raise NonPossibile("Per installare Ollama servono i permessi di amministratore e qui non si possono chiedere. "
                       "Esegui nel terminale il comando qui sotto, poi premi di nuovo Salva e verifica.",
                       COMANDO_LINUX)


SCRIPT_LINUX = """#!/bin/sh
# Eseguito come amministratore da CashFlow Analyzer: installa Ollama con lo script ufficiale e imposta
# OLLAMA_NO_CLOUD=1 sul servizio di sistema (niente modelli cloud).
set -e
if ! command -v zstd >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y zstd || true
fi
sh "$1"
if command -v systemctl >/dev/null 2>&1 && [ -f /etc/systemd/system/ollama.service ]; then
  mkdir -p /etc/systemd/system/ollama.service.d
  printf '[Service]\\nEnvironment="OLLAMA_NO_CLOUD=1"\\n' > /etc/systemd/system/ollama.service.d/cashflow-analyzer.conf
  systemctl daemon-reload
  systemctl enable ollama >/dev/null 2>&1 || true
  systemctl restart ollama
fi
"""


def _scarica(url, destinazione, registra, avanzamento=None):
    import requests
    with requests.get(url, stream=True, timeout=(10, 120)) as r:
        r.raise_for_status()
        totale = int(r.headers.get("content-length") or 0)
        fatti = 0
        ultimo = 0.0
        with open(destinazione, "wb") as f:
            for blocco in r.iter_content(1 << 20):
                f.write(blocco)
                fatti += len(blocco)
                if avanzamento and time.monotonic() - ultimo > 0.5:
                    ultimo = time.monotonic()
                    avanzamento(fatti, totale)
    if avanzamento:
        avanzamento(fatti, totale or fatti)
    registra("Scaricato %s (%.1f MB)" % (Path(destinazione).name, fatti / 1e6))


def impronta(file):
    h = hashlib.sha256()
    with open(file, "rb") as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


def impronta_pubblicata(testo, nome):
    """SHA-256 di `nome` da un file sha256sum.txt ('<impronta>  <nome>' per riga), o None."""
    for riga in testo.splitlines():
        parti = riga.split()
        if len(parti) >= 2 and parti[-1].lstrip("*./") == nome and len(parti[0]) == 64:
            return parti[0].lower()
    return None


def _installa_linux(lavoro):
    prefisso = comando_amministratore()
    lavoro.fase("Scarico lo script di installazione da ollama.com…")
    cartella = Path(tempfile.mkdtemp(prefix="cashflow-ollama-"))
    try:
        script = cartella / "install.sh"
        _scarica(URL_SCRIPT_LINUX, script, lavoro.registra)
        involucro = cartella / "cashflow-installa-ollama.sh"
        involucro.write_text(SCRIPT_LINUX, encoding="utf-8")
        os.chmod(involucro, 0o755)
        lavoro.fase("Installo Ollama (circa 1,5 GB, qualche minuto)" +
                    (": inserisci la password di amministratore nella finestra di sistema." if prefisso == ["pkexec"] else "…"))
        codice = _esegui(prefisso + ["/bin/sh", str(involucro), str(script)], lavoro.registra)
    finally:
        shutil.rmtree(cartella, ignore_errors=True)
    if codice in (126, 127) and prefisso == ["pkexec"]:
        raise NonPossibile("Password di amministratore non inserita o non accettata: Ollama non e' stato installato. "
                           "Riprova, oppure esegui nel terminale il comando qui sotto.", COMANDO_LINUX)
    if codice != 0:
        raise NonPossibile("Installazione di Ollama non riuscita (codice %d): vedi i dettagli. Puoi anche eseguire "
                           "nel terminale il comando qui sotto." % codice, COMANDO_LINUX)


def _no_cloud_windows():
    """OLLAMA_NO_CLOUD=1 tra le variabili dell'utente (come setx), letta da Ollama all'avvio."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "OLLAMA_NO_CLOUD", 0, winreg.REG_SZ, "1")
    try:                                                   # avvisa i programmi che le variabili sono cambiate
        import ctypes
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000, None)
    except Exception:
        pass


def _installa_windows(lavoro):
    if shutil.which("winget") and not os.environ.get("SPESE_OLLAMA_SENZA_WINGET"):
        lavoro.fase("Installo Ollama con winget (circa 1,5 GB, qualche minuto)…")
        codice = _esegui(["winget", "install", "-e", "--id", WINGET_ID, "--silent", "--accept-package-agreements",
                          "--accept-source-agreements", "--disable-interactivity"], lavoro.registra)
        if codice == 0 or installato():
            return
        lavoro.registra("winget non e' riuscito (codice %d): uso l'installatore della release su GitHub." % codice)
    import requests
    lavoro.fase("Cerco l'ultima versione di Ollama su GitHub…")
    rel = requests.get(URL_RELEASE, timeout=30, headers={"Accept": "application/vnd.github+json"}).json()
    risorse = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    if "OllamaSetup.exe" not in risorse or "sha256sum.txt" not in risorse:
        raise NonPossibile("Non trovo l'installatore di Ollama su GitHub. Installalo da https://ollama.com/download "
                           "e premi di nuovo Salva e verifica.")
    attesa = impronta_pubblicata(requests.get(risorse["sha256sum.txt"], timeout=30).text, "OllamaSetup.exe")
    cartella = Path(tempfile.mkdtemp(prefix="cashflow-ollama-"))
    try:
        setup = cartella / "OllamaSetup.exe"
        lavoro.fase("Scarico Ollama %s (circa 1,5 GB)…" % rel.get("tag_name", ""))
        _scarica(risorse["OllamaSetup.exe"], setup, lavoro.registra, lavoro.quota)
        if not attesa or impronta(setup) != attesa:
            raise NonPossibile("L'impronta dell'installatore scaricato non corrisponde a quella pubblicata: "
                               "installazione annullata. Installa Ollama da https://ollama.com/download.")
        # Windows PowerShell 5.1 senza il PSModulePath di un eventuale PowerShell 7: altrimenti non carica il
        # modulo che legge le firme
        # Il percorso passa in una variabile d'ambiente, non dentro il comando: un apostrofo nel nome utente
        # (C:\Users\D'Angelo\...) chiudeva la stringa e la verifica falliva sempre.
        env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
        env["CASHFLOW_SETUP"] = str(setup)
        esito = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                "$s = Get-AuthenticodeSignature -LiteralPath $env:CASHFLOW_SETUP; "
                                "'{0}|{1}' -f $s.Status, $s.SignerCertificate.Subject"],
                               capture_output=True, text=True, env=env, creationflags=SENZA_FINESTRA)
        firma = esito.stdout.strip()
        lavoro.registra("Firma dell'installatore: %s" % (firma or esito.stderr.strip()[:300]))
        if not firma_di_ollama(firma):
            raise NonPossibile("La firma digitale dell'installatore non e' valida o non e' di Ollama: installazione "
                               "annullata. Installa Ollama da https://ollama.com/download.")
        lavoro.fase("Installo Ollama (solo per il tuo utente)…")
        codice = _esegui([setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"], lavoro.registra)
        if codice != 0:
            raise NonPossibile("Installazione di Ollama non riuscita (codice %d). Installalo da "
                               "https://ollama.com/download." % codice)
    finally:
        shutil.rmtree(cartella, ignore_errors=True)


def avvia(lavoro):
    """Avvia Ollama installato ma fermo. Linux: il servizio di sistema (come amministratore) o, se manca,
    'ollama serve' dell'utente. Windows: l'app di Ollama nell'area di notifica."""
    env = dict(os.environ, OLLAMA_NO_CLOUD="1")
    if WINDOWS:
        app = _cartella_windows() / "ollama app.exe"
        if app.exists():
            subprocess.Popen([str(app)], env=env, creationflags=SENZA_FINESTRA, cwd=str(app.parent))
        else:
            subprocess.Popen([str(eseguibile()), "serve"], env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=SENZA_FINESTRA)
        return
    if Path("/etc/systemd/system/ollama.service").exists() and shutil.which("systemctl"):
        try:
            codice = _esegui(comando_amministratore() + ["systemctl", "start", "ollama"], lavoro.registra)
        except NonPossibile:
            codice = -1
        if codice == 0:
            return
        lavoro.registra("Avvio del servizio non riuscito (codice %d): avvio 'ollama serve'." % codice)
    subprocess.Popen([str(eseguibile()), "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def attendi(url, secondi, lavoro):
    lavoro.fase("Attendo che Ollama risponda…")
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        if risponde(url):
            return True
        time.sleep(2)
    return False


# --- lavoro in background ------------------------------------------------------------------------------
class Lavoro:
    def __init__(self, url, modelli, installa):
        self.url, self.modelli, self.installa = url, list(modelli), installa
        self.testo = "Preparazione…"
        self.percentuale = None
        self.righe = deque(maxlen=300)
        self.finito = False
        self.errore = None
        self.comando = None
        self.inizio = time.time()

    # chiamati dal thread
    def fase(self, testo):
        self.testo, self.percentuale = testo, None
        self.registra(testo)

    def registra(self, riga, sostituisci=False):
        """Aggiunge una riga al registro; con sostituisci=True prende il posto dell'ultima (barra ridisegnata)."""
        if sostituisci and self.righe:
            self.righe[-1] = riga
        else:
            self.righe.append(riga)

    def quota(self, fatti, totale):
        self.percentuale = min(1.0, fatti / totale) if totale else None

    def esegui(self):
        try:
            if self.installa and not installato():
                self.registra("Sistema: %s" % ("Windows" if WINDOWS else "Linux"))
                _installa_linux(self) if not WINDOWS else _installa_windows(self)
            if WINDOWS:
                _no_cloud_windows()
            if not risponde(self.url):
                if not installato():
                    raise NonPossibile("Ollama non risulta installato dopo l'installazione: vedi i dettagli.")
                if not attendi(self.url, 10, self):
                    self.fase("Avvio Ollama…")
                    avvia(self)
                    if not attendi(self.url, 90, self):
                        raise NonPossibile("Ollama e' installato ma non risponde su %s. Avvialo (su Windows dal menu "
                                           "Start, su Linux con 'sudo systemctl start ollama') e premi di nuovo Salva e "
                                           "verifica." % self.url)
            from . import llm
            for nome in self.modelli:
                self.fase("Scarico il modello %s…" % nome)
                llm.scarica_modello_url(self.url, nome, lambda stato, fatti, tot: (
                    setattr(self, "testo", "Modello %s: %s" % (nome, stato)), self.quota(fatti or 0, tot or 0)))
                self.registra("Modello %s installato." % nome)
            self.testo, self.percentuale = "Fatto: Ollama attivo e modelli installati.", 1.0
        except NonPossibile as e:
            self.errore, self.comando = str(e), e.comando
        except Exception as e:                            # rete, permessi, risposte inattese
            self.registra("Errore: %s" % e)
            import requests
            if isinstance(e, requests.RequestException):
                self.errore = ("Non riesco a scaricare da internet (%s): controlla la connessione e riprova." %
                               type(e).__name__)
            else:
                self.errore = "Operazione non riuscita: %s" % e
            if self.installa and not installato():
                if WINDOWS:
                    self.errore += " In alternativa installa Ollama da https://ollama.com/download e premi di nuovo Salva e verifica."
                else:
                    self.errore += " In alternativa esegui nel terminale il comando qui sotto, poi premi di nuovo Salva e verifica."
                    self.comando = COMANDO_LINUX
        finally:
            self.finito = True


_LAVORO = None
_BLOCCO = threading.Lock()


def lavoro():
    return _LAVORO


def avvia_lavoro(url, modelli, installa=True):
    """Fa partire installazione (se installa e Ollama manca), avvio e download dei modelli. Un solo lavoro
    alla volta: se ce n'e' uno in corso restituisce quello."""
    global _LAVORO
    with _BLOCCO:
        if _LAVORO and not _LAVORO.finito:
            return _LAVORO
        _LAVORO = Lavoro(url, modelli, installa)
        threading.Thread(target=_LAVORO.esegui, name="installa-ollama", daemon=True).start()
        return _LAVORO


def chiudi_lavoro():
    global _LAVORO
    with _BLOCCO:
        if _LAVORO and _LAVORO.finito:
            _LAVORO = None


def riepilogo(lav):
    """Per i test e i log: stato del lavoro in un dizionario."""
    return {"testo": lav.testo, "finito": lav.finito, "errore": lav.errore, "comando": lav.comando,
            "righe": list(lav.righe)}


if __name__ == "__main__":                              # prova da riga di comando (usata dai test su GitHub)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # la console di Windows non e' sempre UTF-8
    url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    lav = avvia_lavoro(url, sys.argv[1:] or ["qwen2.5:0.5b"])
    visto = 0
    while not lav.finito:
        time.sleep(2)
        righe = list(lav.righe)
        for r in righe[visto:]:
            print(r, flush=True)
        visto = len(righe)
    print(json.dumps({k: v for k, v in riepilogo(lav).items() if k != "righe"}, ensure_ascii=False))
    if lav.errore and os.environ.get("GITHUB_ACTIONS"):   # annotazione leggibile anche senza i log
        testo = "%s | ultime righe: %s" % (lav.errore, " / ".join(list(lav.righe)[-12:]))
        print("::error::" + testo.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))
    sys.exit(1 if lav.errore else 0)
