# Installa CashFlow Analyzer su Windows: controlla Python (e lo installa se manca, dopo conferma), crea
# l'ambiente isolato .venv con le dipendenze e aggiunge "CashFlow Analyzer" al menu Start.
# Si lancia con install.cmd (doppio clic). Non servono permessi di amministratore.
#   install.cmd                 installazione normale (chiede conferma prima di installare Python)
#   install.cmd -Si             risponde si' alle domande (installazione non presidiata)
#   install.cmd -SoloDownload   installa Python scaricandolo da python.org anche se c'e' winget
#   -ProvaInstallazione         (per i test automatici) installa Python anche se ce n'e' gia' uno
param(
    [switch]$Si,
    [switch]$SoloDownload,
    [switch]$ProvaInstallazione
)
# "Continue": in Windows PowerShell 5.1 con "Stop" un programma esterno che scrive sullo standard error
# (pip, winget, py) interromperebbe lo script anche quando riesce. Gli errori si controllano con $LASTEXITCODE.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
Set-Location -LiteralPath $PSScriptRoot
$APP = $PSScriptRoot

# Versione di Python installata se manca. Per aggiornarla: versione, indirizzo e SHA-256 dalla pagina della
# release su python.org (riga "Windows installer (64-bit)").
$PY_VERSIONE = "3.13.17"
$PY_URL = "https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe"
$PY_SHA256 = "5da012101ba2e17113a9a1cd901e9ce1441086f2b7ecb5de06ec94163899a190"
$WINGET_ID = "Python.Python.3.13"

function Scrivi($testo) { Write-Host $testo }
function Errore($testo) { Write-Host $testo -ForegroundColor Red; exit 1 }

function Conferma($domanda) {
    if ($Si) { return $true }
    $r = Read-Host "$domanda [S/n]"
    return ($r -eq "" -or $r -match "^[sSyY]")
}

# I file scaricati da internet sono marcati come "provenienti da internet": li sblocco perche' Windows non
# chieda conferma a ogni avvio.
Get-ChildItem -LiteralPath $APP -Recurse -File -ErrorAction SilentlyContinue |
    Unblock-File -ErrorAction SilentlyContinue

# --- 1. Python ------------------------------------------------------------------------------------
# Accetta Python da 3.11 a 3.14 (le dipendenze hanno i pacchetti per Windows fino al 3.14) con venv.
# Esclude il Python del Microsoft Store: devia le scritture nella cartella AppData verso una copia privata.
$CONTROLLO = "import sys, venv, ensurepip; v = sys.version_info; print('%d.%d|%s' % (v[0], v[1], sys.executable))"

function Prova-Python($comando, $argomenti) {
    try {
        $out = & $comando @argomenti -c $CONTROLLO 2>&1 | Where-Object { $_ -is [string] }
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $parti = ("$out".Trim()) -split "\|", 2
        $v = [version]$parti[0]
        if ($v -lt [version]"3.11" -or $v -ge [version]"3.15") { return $null }
        if ($parti[1] -match "\\WindowsApps\\") { return $null }
        return $parti[1]
    } catch {
        return $null
    }
}

function Trova-Python {
    $prove = @(
        @("py", @("-3.13")), @("py", @("-3.12")), @("py", @("-3.11")), @("py", @("-3.14")),
        @("python", @()), @("python3", @())
    )
    foreach ($p in $prove) {
        if (Get-Command $p[0] -ErrorAction SilentlyContinue) {
            $exe = Prova-Python $p[0] $p[1]
            if ($exe) { return $exe }
        }
    }
    # appena installato, il PATH di questa finestra non e' ancora aggiornato: cerco nelle cartelle standard
    $cartelle = @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles")
    foreach ($c in $cartelle) {
        foreach ($n in @("Python313", "Python312", "Python311", "Python314")) {
            $exe = Join-Path (Join-Path $c $n) "python.exe"
            if (Test-Path -LiteralPath $exe) {
                $r = Prova-Python $exe @()
                if ($r) { return $r }
            }
        }
    }
    return $null
}

function Trova-Winget {
    if (Get-Command winget -ErrorAction SilentlyContinue) { return $true }
    # su un PC nuovo winget si registra dopo il primo accesso: si puo' chiedere la registrazione (Microsoft)
    try {
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop
    } catch { }
    return [bool](Get-Command winget -ErrorAction SilentlyContinue)
}

function Installa-Con-Winget {
    Scrivi "Installo Python 3.13 con winget (solo per il tuo utente)..."
    & winget install -e --id $WINGET_ID --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    return ($LASTEXITCODE -eq 0)
}

function Installa-Da-PythonOrg {
    $file = Join-Path $env:TEMP "python-$PY_VERSIONE-amd64.exe"
    Scrivi "Scarico Python $PY_VERSIONE da python.org..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-WebRequest -Uri $PY_URL -OutFile $file -UseBasicParsing -ErrorAction Stop
    } catch {
        Errore "Download di Python non riuscito ($($_.Exception.Message)). Controlla la connessione e riprova."
    }
    $impronta = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLower()
    if ($impronta -ne $PY_SHA256) {
        Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
        Errore "L'impronta del file scaricato non corrisponde a quella di python.org ($impronta): installazione annullata."
    }
    Scrivi "Impronta SHA-256 verificata. Installo Python $PY_VERSIONE (solo per il tuo utente)..."
    $p = Start-Process -FilePath $file -Wait -PassThru -ErrorAction Stop -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1", "InstallLauncherAllUsers=0",
        "Include_test=0", "Shortcuts=0", "AssociateFiles=0")
    Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
    return ($p.ExitCode -eq 0 -or $p.ExitCode -eq 3010)      # 3010: riuscito, riavvio consigliato
}

$PY = $null
if (-not $ProvaInstallazione) { $PY = Trova-Python }
if ($PY) {
    Scrivi "Python trovato: $PY"
} else {
    Scrivi "Python 3.11 o successivo non e' installato (serve per CashFlow Analyzer)."
    if (-not (Conferma "Installo Python 3.13 adesso, solo per il tuo utente e senza permessi di amministratore?")) {
        Errore "Installazione annullata. Puoi installare Python da https://www.python.org/downloads/windows/ e rilanciare install.cmd."
    }
    $ok = $false
    if (-not $SoloDownload -and (Trova-Winget)) {
        $ok = Installa-Con-Winget
        if (-not $ok) { Scrivi "winget non e' riuscito: provo con l'installatore di python.org." }
    } elseif (-not $SoloDownload) {
        Scrivi "winget non e' disponibile: uso l'installatore di python.org."
    }
    if (-not $ok) { $ok = Installa-Da-PythonOrg }
    $PY = Trova-Python
    if (-not $ok -or -not $PY) {
        Errore "Python non risulta installato. Installalo da https://www.python.org/downloads/windows/ e rilancia install.cmd."
    }
    Scrivi "Python installato: $PY"
}

# --- 2. Ambiente .venv e dipendenze ---------------------------------------------------------------
$VPY = Join-Path $APP ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $VPY) {
    & $VPY -c "import sys" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Scrivi "L'ambiente .venv non funziona (Python spostato o disinstallato): lo ricreo."
        Remove-Item -LiteralPath (Join-Path $APP ".venv") -Recurse -Force
    }
}
if (-not (Test-Path -LiteralPath $VPY)) {
    Scrivi "Creo l'ambiente Python in $APP\.venv ..."
    & $PY -m venv (Join-Path $APP ".venv")
    if ($LASTEXITCODE -ne 0) { Errore "Creazione dell'ambiente .venv non riuscita." }
}
& $VPY -m pip install --quiet --disable-pip-version-check --upgrade pip
Scrivi "Installo le dipendenze (qualche minuto la prima volta) ..."
& $VPY -m pip install --quiet --disable-pip-version-check -r (Join-Path $APP "requirements.txt")
if ($LASTEXITCODE -ne 0) { Errore "Installazione delle dipendenze non riuscita." }
& $VPY -c "import sqlcipher3, streamlit, keyring, pystray; print('Dipendenze OK')"
if ($LASTEXITCODE -ne 0) { Errore "Le dipendenze non si caricano: rilancia install.cmd." }

# --- 3. Collegamento nel menu Start ---------------------------------------------------------------
$programmi = [Environment]::GetFolderPath("Programs")
$lnk = Join-Path $programmi "CashFlow Analyzer.lnk"
$shell = New-Object -ComObject WScript.Shell
$c = $shell.CreateShortcut($lnk)
# Il collegamento usa il pythonw.exe del Python installato ("home" in .venv\pyvenv.cfg), non il lanciatore
# .venv\Scripts\pythonw.exe: con Windows Terminal come terminale predefinito il lanciatore apriva una scheda
# del terminale. avvia.py usa solo la libreria standard; -s esclude le librerie personali dell'utente.
$pyw = $null
# pyvenv.cfg e' scritto in UTF-8: senza -Encoding Windows PowerShell 5.1 lo legge in ANSI e un nome utente
# con lettere accentate darebbe un percorso sbagliato
foreach ($riga in Get-Content -Encoding UTF8 (Join-Path $APP ".venv\pyvenv.cfg")) {
    if ($riga -match '^\s*home\s*=\s*(.+?)\s*$') { $pyw = Join-Path $Matches[1] "pythonw.exe" }
}
if (-not $pyw -or -not (Test-Path -LiteralPath $pyw)) { $pyw = Join-Path $APP ".venv\Scripts\pythonw.exe" }
$c.TargetPath = $pyw
$c.Arguments = '-s "' + (Join-Path $APP "avvia.py") + '"'
$c.WorkingDirectory = $APP
$c.IconLocation = (Join-Path $APP "assets\icona.ico") + ",0"
$c.Description = "Analisi locale di entrate e uscite"
$c.Save()

$dati = & $VPY -c "from spese.percorsi import cartella_predefinita; print(cartella_predefinita())"
Scrivi ""
Scrivi "Installazione completata."
Scrivi "Avvio: menu Start > CashFlow Analyzer, oppure avvia.cmd in questa cartella."
Scrivi "I dati (cifrati) stanno in $dati"
exit 0
