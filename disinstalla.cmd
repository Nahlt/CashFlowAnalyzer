@echo off
rem Disinstalla CashFlow Analyzer da Windows (doppio clic). I dati restano, salvo "disinstalla.cmd -AncheDati".
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0disinstalla.ps1" %*
set ESITO=%ERRORLEVEL%
if "%~1"=="" pause
exit /b %ESITO%
