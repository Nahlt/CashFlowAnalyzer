# CashFlow Analyzer

Di [Nahlt](https://github.com/Nahlt).

App locale per analizzare le entrate e le uscite di una famiglia a partire dagli estratti conto esportati dalle
banche, per Linux e Windows. Gira tutta sul proprio computer, nel browser su `127.0.0.1`, con il database cifrato (SQLCipher).

I dati restano sul PC. Verso l'esterno possono uscire solo, e solo se lo scegli:
- i **nomi degli esercenti** da classificare, verso Claude, dopo un'anteprima e una conferma esplicita: mai
  importi, date o nomi di persone, e mai nulla dell'area salute;
- i **codici ISIN e i simboli di borsa** dei titoli in portafoglio, verso Yahoo Finance, se accendi i prezzi di
  mercato.

## Cosa fa

| Area | Funzioni |
| --- | --- |
| Import | Estratti di Credem, Crédit Agricole, ING (conto in Excel o CSV, carta di credito), Trade Republic, PayPal. Formati descritti da profili YAML, duplicati scartati, quadratura con i saldi |
| Classificazione | Tassonomia a due livelli modificabile; catena di regole, codici MCC, marchi (Name Suggestion Index), storico e somiglianza; modello locale (Ollama) o Claude con anteprima e risposte da confermare |
| Abbinamenti | Trasferimenti tra i propri conti, contributi al conto comune, addebito mensile della carta di credito con le sue spese, addebiti PayPal con il report PayPal: ogni spesa conta una volta sola |
| Revisione | Esercenti da classificare, bonifici e trasferimenti, addebiti PayPal da ricondurre, altri movimenti da rivedere |
| Analisi | Andamento mensile, dove vanno i soldi, perimetri personale / comune / combinazioni di conti, etichette, natura della spesa |
| Portafoglio | Costo medio ponderato, plusvalenze realizzate, dividendi; prezzi giornalieri da Yahoo Finance (facoltativi) con valore, rendimento e andamento nel tempo delle posizioni |

## Requisiti

- Linux (provato su Linux Mint 22) oppure Windows 10 o 11 a 64 bit
- Python 3.11 o successivo: se manca, l'installazione propone di installarlo (su Windows con winget o
  dall'installatore ufficiale di python.org, solo per l'utente e senza permessi di amministratore)
- Facoltativi: [Ollama](https://ollama.com) per il modello locale, una chiave API di Anthropic per Claude

## Installazione e avvio

Scarica `CashFlowAnalyzer-X.Y.Z.zip` (o `.tar.gz`) dall'ultima [release](https://github.com/Nahlt/CashFlowAnalyzer/releases)
e scompattalo, oppure clona il repository.

**Linux**

```bash
cd CashFlowAnalyzer-X.Y.Z
./install.sh          # controlla Python, crea .venv, installa le dipendenze, aggiunge la voce al menu
./avvia.sh            # avvia il server e apre il browser su http://127.0.0.1:8501
```

**Windows**: doppio clic su `install.cmd`, poi avvio dal menu Start (**CashFlow Analyzer**) o con `avvia.cmd`.

Al primo avvio si sceglie la passphrase che cifra il database: non e' recuperabile. I dati stanno in
`~/.local/share/spese-app` su Linux e in `%LOCALAPPDATA%\CashFlowAnalyzer` su Windows (o nella cartella
indicata da `SPESE_DATI`).

Il manuale completo, con il primo utilizzo e tutte le funzioni, e' in [MANUALE.md](MANUALE.md).

## Struttura

| Percorso | Contenuto |
| --- | --- |
| `app.py` | Accesso, menu, barra laterale |
| `avvia.py` | Avviatore per Linux e Windows: server, icona nell'area di notifica, browser |
| `install.sh`, `install.cmd`, `install.ps1` | Installazione su Linux e Windows, con il controllo di Python |
| `spese/` | Lettura dei formati, import, catena di categorizzazione, ricalcolo e abbinamenti, privacy, modelli, portafoglio, prezzi di mercato |
| `spese/ui/` | Pagine dell'interfaccia (Streamlit) e grafici (ECharts) |
| `config/` | Profili dei formati bancari, tassonomia, tipi di movimento, regole, impostazioni |
| `sorgenti/` | Dati di origine (codici MCC, marchi) da cui `strumenti/` genera parte di `config/` |
| `tests/` | Test automatici su file sintetici e dati inventati |

## Test

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
```

L'azione *Test* di GitHub ripete test, installazione, avvio e arresto su Linux e Windows a ogni push.

## Pubblicare una versione

1. Aggiorna `VERSIONE` in `spese/__init__.py` e aggiungi la sezione `## X.Y.Z – ...` in `CHANGELOG.md`.
2. Unisci le modifiche in `main`, poi crea e pubblica il tag:
   ```bash
   git tag v0.14.0
   git push origin v0.14.0
   ```
   In alternativa, su GitHub: *Releases* > *Draft a new release* > nuovo tag `v0.14.0` > *Publish*.
3. L'azione *Release* esegue i test su Linux e Windows e allega alla release `CashFlowAnalyzer-X.Y.Z.zip`, il
   `.tar.gz` (stesso contenuto, per Linux e Windows) e `SHA256SUMS.txt`.

## Licenza

Copyright (C) 2026 Nahlt.

Il codice del progetto e' distribuito con licenza GNU GPL v3 (`LICENSE`). Alcuni dati di configurazione
derivano da altri progetti e restano sotto la propria licenza, in particolare PolyForm Noncommercial 1.0.0
per le parti riprese da Spendif.ai: i dettagli sono in [NOTICE.md](NOTICE.md).
