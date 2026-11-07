"""Avvio di CashFlow Analyzer su Linux e Windows: server sul solo computer locale (127.0.0.1), icona
nell'area di notifica e browser.

    python avvia.py                avvia server e icona (se gia' attivi riapre solo il browser)
    python avvia.py --riavvia      riavvia il server (le schede aperte tornano alla richiesta della passphrase)
    python avvia.py --ferma        ferma server e icona
    python avvia.py --ferma-server ferma solo il server (lo usa l'icona)
    python avvia.py --senza-icona  avvia senza icona nell'area di notifica

Va eseguito con il Python dell'ambiente .venv creato dall'installazione: lo fanno avvia.sh (Linux),
avvia.cmd e il collegamento nel menu Start (Windows). Usa solo la libreria standard.
"""
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parent
WINDOWS = sys.platform == "win32"
PORTA = os.environ.get("SPESE_PORTA", "8501")
URL = "http://127.0.0.1:%s" % PORTA

sys.path.insert(0, str(APP))
from spese.percorsi import cartella_dati  # noqa: E402  (stessa cartella dei dati che usa l'app)

DATI = cartella_dati()
PID = DATI / "server.pid"
PID_ICONA = DATI / "icona.pid"
LOG = DATI / "server.log"
STOP_ICONA = DATI / "icona.stop"          # su Windows chiede all'icona di chiudersi da sola
# su Linux l'icona usa il Python di sistema, che su Linux Mint ha gia' le librerie grafiche (GTK)
PY_SISTEMA = os.environ.get("SPESE_PYTHON_ICONA", "/usr/bin/python3")


def _python_venv(finestra=False):
    """Python dell'ambiente .venv. Su Windows pythonw.exe non apre la finestra del terminale."""
    if WINDOWS:
        return APP / ".venv" / "Scripts" / ("pythonw.exe" if finestra else "python.exe")
    return APP / ".venv" / "bin" / "python"


# --- processi ------------------------------------------------------------------------------------------
# Dopo un riavvio del PC il numero salvato nel file pid puo' appartenere a un altro programma: non va
# considerato ne' fermato. Su Linux si controlla la riga di comando del processo; su Windows l'ora di
# creazione del processo, salvata nel file insieme al numero.

def _ora_creazione(pid):
    """Ora di creazione del processo su Windows (intero), None se il processo non esiste."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        tempi = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(h, *[ctypes.byref(t) for t in tempi]):
            return None
        codice = wintypes.DWORD()
        if k32.GetExitCodeProcess(h, ctypes.byref(codice)) and codice.value != 259:   # 259 = STILL_ACTIVE
            return None
        return (tempi[0].dwHighDateTime << 32) | tempi[0].dwLowDateTime
    finally:
        k32.CloseHandle(h)


def scrivi_pid(file, pid):
    file.write_text("%d %d\n" % (pid, _ora_creazione(pid) or 0) if WINDOWS else "%d\n" % pid)


def vivo(file, atteso):
    """Pid del processo del file se e' vivo ed e' proprio il nostro (atteso: testo nella riga di comando,
    usato su Linux), altrimenti None."""
    try:
        parti = file.read_text().split()
        pid = int(parti[0])
    except (OSError, ValueError, IndexError):
        return None
    if WINDOWS:
        ora = _ora_creazione(pid)
        return pid if ora is not None and len(parti) > 1 and str(ora) == parti[1] else None
    try:
        riga = Path("/proc/%d/cmdline" % pid).read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return None
    return pid if atteso in riga else None


def avviso(testo):
    """Messaggio all'utente: sul terminale o, su Windows senza terminale (collegamento del menu Start), in una
    finestra di dialogo."""
    if sys.stderr is not None:
        print(testo, file=sys.stderr)
    elif WINDOWS:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, testo, "CashFlow Analyzer", 0x30)


def risponde():
    try:
        urllib.request.urlopen(URL + "/_stcore/health", timeout=1)
        return True
    except Exception:
        return False


def _in_background(argomenti, uscita):
    """Avvia un processo staccato da questo, che resta in vita quando l'avviatore termina.

    Windows: niente finestre. Il python.exe di .venv e' un piccolo lanciatore che avvia a sua volta il Python
    vero: con DETACHED_PROCESS il lanciatore resterebbe senza console e Windows aprirebbe per il Python vero
    una finestra nuova e visibile. CREATE_NO_WINDOW da' invece al processo una console nascosta, che i
    processi figli ereditano; la finestra e' anche chiesta nascosta (SW_HIDE)."""
    opzioni = {"stdin": subprocess.DEVNULL, "stdout": uscita, "stderr": subprocess.STDOUT, "cwd": str(APP)}
    if WINDOWS:
        opzioni["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        avvio = subprocess.STARTUPINFO()
        avvio.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        avvio.wShowWindow = 0                                     # SW_HIDE
        opzioni["startupinfo"] = avvio
    else:
        opzioni["start_new_session"] = True
    return subprocess.Popen([str(a) for a in argomenti], **opzioni)


def _termina(pid):
    try:
        os.kill(pid, signal.SIGTERM)                      # su Windows: TerminateProcess
    except OSError:
        pass


# --- server --------------------------------------------------------------------------------------------
def ferma_server(messaggi=True):
    pid = vivo(PID, "streamlit")
    if pid:
        _termina(pid)
        for _ in range(40):
            if not vivo(PID, "streamlit"):
                break
            time.sleep(0.25)
        if vivo(PID, "streamlit") and not WINDOWS:
            os.kill(pid, signal.SIGKILL)
        if messaggi:
            print("Server fermato.")
    elif messaggi:
        print("Il server non e' in esecuzione.")
    PID.unlink(missing_ok=True)


def installata():
    if not _python_venv().exists():
        return False
    pacchetti = APP.glob(".venv/Lib/site-packages/streamlit" if WINDOWS else ".venv/lib/python3*/site-packages/streamlit")
    return any(p.is_dir() for p in pacchetti)


def avvia_server():
    if not installata():
        avviso("App non installata: lancia prima %s" % ("install.cmd" if WINDOWS else "./install.sh"))
        return False
    if vivo(PID, "streamlit"):
        return True
    with open(LOG, "w", encoding="utf-8") as log:
        # "python -m streamlit" invece dell'eseguibile streamlit: funziona anche se la cartella e' stata spostata
        proc = _in_background([_python_venv(), "-m", "streamlit", "run", "app.py", "--server.port", PORTA,
                               "--server.address", "127.0.0.1"], log)
    scrivi_pid(PID, proc.pid)
    for _ in range(80):
        if risponde():
            return True
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    avviso("Il server non risponde: vedi %s" % LOG)
    return False


# --- icona e browser -----------------------------------------------------------------------------------
def avvia_icona():
    if WINDOWS:
        if vivo(PID_ICONA, "icona_windows.py"):
            return
        if not any(APP.glob(".venv/Lib/site-packages/pystray")):
            avviso("Icona nell'area di notifica non disponibile: ripeti l'installazione (install.cmd).")
            return
        STOP_ICONA.unlink(missing_ok=True)
        with open(DATI / "icona.log", "w", encoding="utf-8") as log:
            _in_background([_python_venv(finestra=True), APP / "icona_windows.py"], log)
        return                                            # il file pid lo scrive l'icona stessa
    if vivo(PID_ICONA, "tray.py"):
        return
    ok = subprocess.run([PY_SISTEMA, "-c", "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk"],
                        capture_output=True).returncode == 0 if Path(PY_SISTEMA).exists() else False
    if not ok:
        print("Icona nell'area di notifica non disponibile (mancano le librerie GTK: "
              "sudo apt install python3-gi gir1.2-gtk-3.0).", file=sys.stderr)
        return
    with open(DATI / "icona.log", "w", encoding="utf-8") as log:
        proc = _in_background([PY_SISTEMA, APP / "tray.py"], log)
    PID_ICONA.write_text("%d\n" % proc.pid)


def ferma_icona():
    pid = vivo(PID_ICONA, "icona_windows.py" if WINDOWS else "tray.py")
    if pid and WINDOWS:
        # chiusa dall'icona stessa: terminata dall'esterno lascerebbe il disegno nell'area di notifica
        STOP_ICONA.write_text("1")
        for _ in range(30):
            if not vivo(PID_ICONA, "icona_windows.py"):
                break
            time.sleep(0.25)
        pid = vivo(PID_ICONA, "icona_windows.py")
        STOP_ICONA.unlink(missing_ok=True)
    if pid:
        _termina(pid)
    PID_ICONA.unlink(missing_ok=True)


def apri_browser():
    print("CashFlow Analyzer: %s" % URL)
    try:
        if WINDOWS:
            os.startfile(URL)
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", URL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        else:
            import webbrowser
            webbrowser.open(URL)
    except OSError:
        print("Apri il browser all'indirizzo %s" % URL)


# --- comandi -------------------------------------------------------------------------------------------
def main(argomenti):
    if not WINDOWS:
        os.umask(0o077)
    os.environ["SPESE_PORTA"] = PORTA
    os.environ["SPESE_DATI"] = str(DATI)
    comando = argomenti[0] if argomenti else ""
    if comando == "--ferma":
        ferma_server()
        ferma_icona()
    elif comando == "--ferma-server":
        ferma_server()
    elif comando == "--riavvia":
        ferma_server(messaggi=False)
        if not avvia_server():
            return 1
        print("Server riavviato su %s." % URL)
    elif comando in ("", "--senza-icona"):
        if not avvia_server():
            return 1
        if comando == "":
            avvia_icona()
        apri_browser()
    else:
        avviso("Uso: avvia [--riavvia | --ferma | --ferma-server | --senza-icona]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
