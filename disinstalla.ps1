# Disinstalla CashFlow Analyzer da Windows.
#   disinstalla.cmd             toglie programma installato (ambiente .venv), collegamento del menu Start e
#                               chiave di Claude. I DATI RESTANO in %LOCALAPPDATA%\CashFlowAnalyzer.
#   disinstalla.cmd -AncheDati  cancella anche database, backup e log (chiede conferma scritta).
#   disinstalla.cmd -Si         senza domande ne' pausa finale (i dati restano)
# Python non viene disinstallato: si toglie, se non serve ad altro, da Impostazioni > App.
param([switch]$AncheDati, [switch]$Si)
$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot
$APP = $PSScriptRoot
$VPY = Join-Path $APP ".venv\Scripts\python.exe"
$DATI = $env:SPESE_DATI
if (-not $DATI) { $DATI = Join-Path $env:LOCALAPPDATA "CashFlowAnalyzer" }

# 1. Ferma server e icona, poi toglie la chiave di Claude dalla Gestione credenziali (serve il Python dell'app)
if (Test-Path -LiteralPath $VPY) {
    & $VPY (Join-Path $APP "avvia.py") --ferma
    & $VPY -c @"
import keyring, keyring.errors
try:
    keyring.delete_password('spese-app', 'anthropic')
    print('Chiave di Claude tolta dalla Gestione credenziali.')
except keyring.errors.PasswordDeleteError:
    print('Nessuna chiave di Claude nella Gestione credenziali.')
"@
}

# 2. Collegamento del menu Start e ambiente Python
$lnk = Join-Path ([Environment]::GetFolderPath("Programs")) "CashFlow Analyzer.lnk"
if (Test-Path -LiteralPath $lnk) {
    Remove-Item -LiteralPath $lnk -Force
    Write-Host "Collegamento del menu Start tolto."
}
if (Test-Path -LiteralPath (Join-Path $APP ".venv")) {
    Remove-Item -LiteralPath (Join-Path $APP ".venv") -Recurse -Force
}
Get-ChildItem -LiteralPath $APP -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Ambiente Python tolto."

# 3. Dati, solo su richiesta esplicita
if ($AncheDati -and -not $Si -and (Test-Path -LiteralPath $DATI)) {
    Write-Host ""
    Write-Host "ATTENZIONE: verranno cancellati database, backup e log in:"
    Write-Host "  $DATI"
    Get-ChildItem -LiteralPath $DATI | ForEach-Object { Write-Host ("    " + $_.Name) }
    Write-Host "Non si possono recuperare. Gli eventuali export CSV salvati altrove non vengono toccati."
    $r = Read-Host "Scrivi CANCELLA per confermare"
    if ($r -ceq "CANCELLA") {
        Remove-Item -LiteralPath $DATI -Recurse -Force
        Write-Host "Dati cancellati."
    } else {
        Write-Host "Dati non cancellati."
    }
} elseif (Test-Path -LiteralPath $DATI) {
    Write-Host ""
    Write-Host "I dati restano in $DATI (database cifrato e backup)."
    Write-Host "Reinstallando con install.cmd li ritrovi con la stessa passphrase."
}

Write-Host ""
Write-Host "Per finire puoi cancellare la cartella del programma: $APP"
exit 0
