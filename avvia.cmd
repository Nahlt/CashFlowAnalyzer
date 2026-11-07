@echo off
rem Avvia CashFlow Analyzer (server, icona nell'area di notifica e browser). Stesse opzioni di avvia.py:
rem   avvia.cmd [--riavvia | --ferma | --ferma-server | --senza-icona]
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo App non installata: lancia prima install.cmd
  exit /b 1
)
".venv\Scripts\python.exe" avvia.py %*
