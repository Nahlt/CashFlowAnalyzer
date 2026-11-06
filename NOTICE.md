# Origine dei dati e licenze

| Componente | Origine | Licenza |
| --- | --- | --- |
| `sorgenti/mcc_codes.csv` | [greggles/mcc-codes](https://github.com/greggles/mcc-codes) | Pubblico dominio (Unlicense) |
| `sorgenti/nsi_marchi_spendifai.json` | Regole generate dal progetto [Spendif.ai](https://github.com/spendifai/spendif-ai) (`core/static_rules.json`) a partire dal [Name Suggestion Index](https://github.com/osmlab/name-suggestion-index) di OpenStreetMap | Dati NSI: ISC. File di Spendif.ai: PolyForm Noncommercial 1.0.0 |
| `config/intermediari.yaml` (voci "seed"), `config/sinonimi_colonne.yaml`, soglie in `config/impostazioni.yaml` | Riprese e adattate da Spendif.ai (`core/payment_intermediaries.json`, `core/classifier.py`, `config/system_settings.yaml`) | PolyForm Noncommercial 1.0.0 |
| Grafici: `streamlit-echarts` (dipendenza Python, include Apache ECharts) | [andfanilo/streamlit-echarts](https://github.com/andfanilo/streamlit-echarts), [Apache ECharts](https://echarts.apache.org) | MIT; ECharts: Apache 2.0 |
| `static/fonts/inter-latin-wght-normal.woff2` | Carattere [Inter](https://rsms.me/inter/) di Rasmus Andersson, dal pacchetto `@fontsource-variable/inter` | SIL Open Font License 1.1 (`static/fonts/Inter-OFL.txt`) |
| Prezzi di mercato (facoltativi, scaricati sul tuo PC solo se li accendi) | Yahoo Finance, interfaccia web pubblica non ufficiale | Dati per uso personale secondo i termini di Yahoo; non inclusi nel pacchetto |
| `config/marchi_nsi.json` | Generato da `strumenti/costruisci_configurazioni.py` a partire da `sorgenti/nsi_marchi_spendifai.json` | Come la sorgente: dati NSI ISC, regole Spendif.ai PolyForm Noncommercial 1.0.0 |
| Tutto il resto | Scritto per questo progetto | GNU GPL v3 (file `LICENSE`) |

La licenza GPL v3 del progetto vale per il codice scritto per questo progetto. I file indicati sopra con
un'altra licenza restano sotto la propria e non sono coperti dalla GPL.

PolyForm Noncommercial consente l'uso personale e non commerciale, modifiche comprese.
Se in futuro il progetto diventasse commerciale, le parti di origine Spendif.ai vanno sostituite:
i marchi si possono rigenerare direttamente dal Name Suggestion Index (licenza ISC).

Copyright Spendif.ai (c) 2024-2026 Luigi Corsaro.
