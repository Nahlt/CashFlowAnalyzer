@echo off
rem Installa CashFlow Analyzer su Windows (doppio clic). Il lavoro lo fa install.ps1: controlla Python e lo
rem installa se manca (dopo conferma), crea l'ambiente .venv e aggiunge l'app al menu Start.
rem   install.cmd [-Si] [-SoloDownload]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set ESITO=%ERRORLEVEL%
if "%~1"=="" pause
exit /b %ESITO%
