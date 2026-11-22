# CashFlow Analyzer 0.21.0 – Manuale

Di Nahlt, con licenza GNU GPL v3.

App locale per analizzare entrate e uscite della famiglia a partire dagli estratti delle banche
(Credem, Credit Agricole, Fineco, ING conto in Excel o CSV e carta di credito, Trade Republic, PayPal). Funziona su Linux e
Windows e gira tutta sul tuo computer, nel browser,
su `127.0.0.1`. Il database e' cifrato. All'esterno escono solo nomi di esercenti, e solo dopo la tua approvazione;
se accendi i prezzi di mercato del portafoglio, anche i codici ISIN e i simboli di borsa dei tuoi titoli.

## Installazione

Scarica il pacchetto della release (`CashFlowAnalyzer-X.Y.Z.zip` o `.tar.gz`, uguale per Linux e Windows) da
https://github.com/Nahlt/CashFlowAnalyzer/releases e scompattalo nella cartella in cui vuoi tenere il programma.

### Linux (provato su Linux Mint 22)

```bash
cd CashFlowAnalyzer-X.Y.Z
./install.sh
```

`install.sh` controlla che ci siano Python 3.11 o successivo e il modulo `venv`. Se mancano riconosce la
distribuzione (Mint, Ubuntu, Debian, Fedora, Arch, openSUSE), mostra il comando per installarli (per esempio
`sudo apt install -y python3-venv python3.12-venv`) e lo esegue solo se confermi; chiede la password di
amministratore. Poi crea un ambiente Python isolato in `.venv`, installa le dipendenze di `requirements.txt`
(versioni fissate) e aggiunge **CashFlow Analyzer** al menu, sotto Ufficio. Non tocca il Python di sistema.
Con `./install.sh --si` risponde si' alle domande.

### Windows 10 e 11

Doppio clic su **`install.cmd`** nella cartella scompattata. Non servono permessi di amministratore.

1. Cerca Python da 3.11 a 3.14. Il Python del Microsoft Store non viene usato, perche' devia le scritture nella
   cartella AppData verso una copia privata.
2. Se manca chiede conferma e installa **Python 3.13 solo per il tuo utente**:
   - con **winget**, il gestore di pacchetti di Microsoft incluso in Windows 11 e nei Windows 10 aggiornati; su
     un PC appena configurato prova prima a registrarlo;
   - se winget non c'e' o non riesce, scarica l'installatore ufficiale da python.org e lo esegue solo se la sua
     impronta SHA-256 corrisponde a quella scritta in `install.ps1`.
3. Crea l'ambiente `.venv`, installa le dipendenze e aggiunge **CashFlow Analyzer** al menu Start.

Se Windows avvisa che il file viene da internet, scegli *Ulteriori informazioni* > *Esegui comunque*: lo script
poi sblocca i file del programma. Opzioni da riga di comando: `install.cmd -Si` (risponde si' alle domande),
`install.cmd -SoloDownload` (installa Python da python.org anche se c'e' winget).

## Avvio

- Dal menu (Linux) o dal menu Start (Windows): **CashFlow Analyzer**. Da terminale: `./avvia.sh` su Linux,
  `avvia.cmd` su Windows, con le stesse opzioni. Il browser si apre su http://127.0.0.1:8501.
- Nell'area di notifica compare l'icona dell'app (tre barre su fondo blu). Il suo menu mostra lo stato del
  server e permette di **aprire** l'app nel browser, **riavviare** il server (o avviarlo, se e' fermo) e
  **fermare** tutto (server e icona). Dopo un riavvio le schede aperte chiedono di nuovo la passphrase.
- Da terminale: `--riavvia`, `--ferma` (server e icona), `--senza-icona`, per esempio `./avvia.sh --ferma` o
  `avvia.cmd --ferma`.
- Chiudere il browser non ferma il server.
- Su Windows l'icona sta nell'area di notifica accanto all'orologio (a volte nella freccia delle icone
  nascoste); un clic apre l'app nel browser. I messaggi stanno in `%LOCALAPPDATA%\CashFlowAnalyzer\icona.log`.
- Su Linux l'icona usa le librerie grafiche di sistema, gia' presenti su Linux Mint, e l'area di notifica classica;
  se il desktop non la offre passa da sola a XApp o AppIndicator. Se non compare:
  `sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-xapp-1.0`. Si puo' forzare il tipo di icona con
  `SPESE_ICONA=xapp ./avvia.sh` (oppure `gtk`, `appindicator`); i messaggi stanno in
  `~/.local/share/CashFlowAnalyzer/icona.log`.
- Al primo avvio scegli la passphrase del database (minimo 8 caratteri).
  **Non e' recuperabile**: senza passphrase i dati non si leggono. Conservala in un gestore di password.
- Dopo l'accesso ricaricare la pagina o aprire un'altra scheda non chiede la passphrase: il browser tiene un
  cookie con un codice casuale e il server ricorda la passphrase **solo in memoria** (mai su disco ne' nel
  cookie). La sessione si chiude con "Esci e chiudi il database" (per tutte le schede), dopo 7 giorni senza
  azioni o quando il server si riavvia (icona Riavvia, `./avvia.sh --riavvia`, PC spento): in quei casi serve
  di nuovo la passphrase. La durata si cambia in `config/impostazioni.yaml` (`sessione.minuti_inattivita`).
  Chi usa il tuo utente sul PC con la sessione aperta vede i dati: blocca lo schermo quando ti allontani.
- Per cambiare la passphrase in **Impostazioni** serve quella attuale, anche con la sessione aperta. Le altre
  schede aperte restano attive e passano da sole alla passphrase nuova. I backup creati prima del cambio restano protetti dalla passphrase precedente.

## Primo utilizzo

1. **Impostazioni**: inserisci i nomi dei titolari (es. "Mario Rossi"). Vengono tolti dalle descrizioni,
   bloccano l'invio a Claude di qualunque voce li contenga e servono a riconoscere i bonifici tra conti tuoi.
   L'intestatario scritto negli estratti ING e Fineco viene aggiunto da solo; se aggiungi un nome dopo l'import,
   l'app lo toglie anche dai movimenti gia' importati.
   Nello stesso riquadro indica i **familiari** che versano sul conto comune senza essere titolari dei conti
   caricati (es. la tua compagna): i loro bonifici sul conto comune diventano contributi e i loro nomi sono
   protetti come quelli dei titolari. I bonifici in entrata sul conto comune da un titolare o da un familiare
   contano sempre come entrate (Contributo ricevuto), anche se il conto da cui partono non e' caricato.
2. **Importa**: carica gli estratti. L'app riconosce il formato e propone il conto. Al primo file di ogni
   conto scegli nome, tipo e perimetro (personale o comune). Per la carta di credito indica il conto su cui
   viene addebitata (l'app propone quello della stessa banca). Le righe della carta sono le spese, ognuna con
   la sua categoria; l'addebito mensile sul conto diventa un contenitore che non conta. L'app cerca le righe
   consecutive della carta che sommano esattamente all'addebito, anche se l'estratto finisce giorni prima
   (ING addebita il 10 del mese le spese del mese prima). Se il conto di addebito non e' indicato e c'e' un
   solo conto con addebiti della carta (a parita', quello della stessa banca), lo usa da sola e lo dice in
   Conti e copertura. L'estratto della carta va importato in un conto di tipo **Carta**: se lo assegni a un
   conto di altro tipo l'app avvisa e importa solo con una conferma. Se le righe della carta sono comunque
   finite in un conto sbagliato, l'addebito mensile non conta (le spese contano gia' nel conto) e in Revisione
   il motivo dice come rimediare: annullare l'import e reimportare il file in un conto di tipo Carta.
3. **Conti e copertura**: registra l'IBAN dei conti, anche quelli che non importi (conto deposito,
   conto di un familiare). L'IBAN si puo' scrivere con gli spazi: viene salvato senza spazi e in maiuscolo,
   dopo un controllo di paese, lunghezza e cifra di controllo; se non torna, nulla viene salvato.
   La lista di controllo mostra i mesi mancanti per conto.
   In **Saldo dei conti** inserisci il saldo contabile di un conto a fine giornata di un giorno (oggi o una
   data scelta): da li' la pagina *Saldo dei conti* calcola il saldo di ogni altro giorno con i movimenti
   importati. La pagina mostra la data dell'ultimo movimento importato del conto: se il saldo e' piu' recente,
   i saldi dei giorni prima restano sbagliati finche' non importi i movimenti mancanti (poi si correggono da
   soli), quindi conviene usare il saldo dell'ultimo estratto. Un nuovo saldo nello stesso giorno sostituisce il
   precedente. In *Saldi noti* trovi anche i saldi letti dagli estratti (ING, Credem, Fineco) e lo scarto tra uno
   e il successivo; i saldi inseriti a mano si eliminano da li'. Le carte di credito non hanno saldo.
   Un conto aggiunto qui a mano compare poi nell'elenco dei conti in **Importa** (proposto in base a IBAN,
   banca o nome che contiene la banca) e al primo file diventa un conto importato.
   **Elimina un conto** lo toglie con i suoi movimenti, i file importati e le decisioni prese su quei movimenti;
   se ci sono movimenti chiede una conferma. Esercenti confermati e regole restano.
4. **Revisione** (quattro schede: *Esercenti da classificare*, *Bonifici e trasferimenti*, *Addebiti PayPal*,
   *Altri movimenti da rivedere*): conferma gli esercenti non riconosciuti, gli abbinamenti dei trasferimenti e la
   destinazione dei bonifici a tuo nome. Ogni risposta vale anche per i movimenti futuri. Per i bonifici a tuo
   nome senza IBAN della controparte puoi indicare un tuo **conto gia' censito**: per tutti i movimenti di quel
   beneficiario o solo per quelli selezionati nel dettaglio; l'app cerca la contropartita su quel conto come se
   il bonifico ne riportasse l'IBAN. Le indicazioni date si rivedono e si tolgono in *Conti indicati a mano per
   i bonifici*, in fondo alla stessa scheda. Un bonifico su cui hai gia' deciso in **Movimenti** (per esempio
   *Escluso dalle analisi*) non compare piu' nella scheda; tra i bonifici con i familiari restano quelli con una
   categoria, per poterla cambiare. Negli esercenti
   puoi cercare per nome (anche scritto come appare in Movimenti, con cifre e punteggiatura), filtrare per caricamento (file importato), selezionarne piu' di uno e applicare la
   stessa categoria a tutti; sotto compaiono i movimenti degli esercenti selezionati. Selezionando uno o piu'
   di quei movimenti puoi dargli una categoria diversa da quella dell'esercente (stesso negozio, spesa con
   un'altra giustificazione): la scelta sul movimento prevale sempre. Lo stesso si fa per gli esercenti gia'
   classificati in Regole e tassonomia.
   Le proposte che vedi dopo un import vengono da regole, codici MCC, marchi, tue conferme e risposte gia'
   salvate: nessun modello viene interpellato finche' non lo chiedi in Classificazione automatica. Per i bonifici e i
   trasferimenti ogni voce ha il dettaglio dei movimenti (data, conto, importo, controparte, IBAN, descrizione,
   file di provenienza).
   I bonifici dal conto comune a un **familiare** (indicato in Impostazioni, una persona per riga; maiuscole,
   spazi e ordine di nome e cognome non contano) il cui conto non analizzi contano come spese: rimborsano spese
   domiciliate sul suo conto. Nella sezione *Bonifici con i familiari* scegli la categoria per uno o piu'
   bonifici selezionati (si vede la causale), applichi una categoria a un gruppo che si ripete, crei una regola
   sulla causale per quelli futuri, **suddividi** un bonifico in piu' parti (importo, categoria e causale per
   parte, la somma deve fare il totale) oppure lo segni come **restituzione di contributo**, che riduce i
   contributi ricevuti invece di aumentare le spese.
   Nella stessa sezione ci sono i bonifici tra un familiare e i tuoi **conti personali**, nei due sensi (prestiti,
   regali, rimborsi di spese...). Le causali sono tutte diverse, quindi si classificano a mano: la categoria vale
   solo per i bonifici selezionati, l'app non la impara dal nome e non propone categorie per importi uguali. Per
   un bonifico ricevuto scegli un'entrata (per esempio *Regali ricevuti*) oppure la categoria della spesa che ti
   rimborsa: Giulia ti restituisce 150 € del supermercato, con *Alimentari > Supermercato* le spese alimentari
   scendono di 150 € e le entrate non salgono. Finche' non e' classificato conta come entrata (o uscita).
   All'apertura si vedono solo i bonifici da classificare senza proposta; quelli del conto comune con una
   proposta (stesso familiare e stesso importo di uno gia' classificato) si confermano tutti con **Conferma le N
   proposte**, oppure si vedono spegnendo *Solo senza proposta*.
5. **Classificazione automatica**: scegli con cosa iniziare.
   - *Prima il modello locale*: non esce nulla dal PC; le voci rimaste incerte vanno poi nella coda per Claude.
     Sul mini PC servono alcuni minuti ogni 20 esercenti.
   - *Direttamente Claude*: tutte le voci vanno nella coda, inviate a lotti di 50 con un solo clic. Piu' rapido,
     usa il credito API.
   La pagina dice prima quante voci entrerebbero in coda (e quante sono bloccate dal controllo di sicurezza).
   In coda le voci sono gia' spuntate: togli la spunta a quelle che classifichi a mano, accorci il testo se serve,
   guardi l'anteprima esatta e premi **Invia**. Nulla esce prima di quel clic. La coda si pulisce da sola: le
   voci classificate nel frattempo altrove (Revisione, regole, modello locale) o arrivate da un import annullato
   spariscono.
   Le voci dell'area salute e quelle con dati identificativi non escono mai: restano al modello locale o a te.
   Finche' non hai indicato i titolari in Impostazioni l'invio a Claude e' disattivato.
   Per rifare una classificazione venuta male, in fondo alla pagina (Strumenti) usa **Ritenta la classificazione**:
   filtri per motore, categoria o nome, selezioni gli esercenti e li rimetti tra quelli da classificare
   (tornano disponibili anche per Claude se erano gia' stati inviati).
6. **Analisi movimenti**. In cima alla barra laterale scegli i **conti** da analizzare, anche una combinazione qualsiasi
   (es. Credit Agricole + Trade Republic); i pulsanti *Personale*, *Comune* e *Tutti* selezionano i
   conti corrispondenti. I versamenti dal conto personale al conto comune si annullano solo se sono scelti
   entrambi. Conti e periodo restano passando da una pagina all'altra. All'apertura il periodo e' quello scelto
   in *Impostazioni > Periodo delle analisi*: intero periodo (di partenza), mese in corso, mese scorso, ultimi 3 o
   6 mesi, anno in corso, un anno (ultimi 12 mesi), due anni. Mese scorso e ultimi mesi sono mesi interi fino al
   mese scorso; con *Aggiungi mese in corso* arrivano a oggi. Il periodo si restringe ai movimenti presenti: se non
   ce n'e' nessuno (per esempio il mese in corso prima di importarlo) si apre l'ultimo mese con movimenti e il
   filtro lo dice. Poi si cambia come sempre dal filtro.
   - **Andamento mensile**: passando il mouse su un mese vedi le prime categorie di spesa; con un clic (anche sulla
     differenza entrate uscite) apri *Dove vanno i soldi* filtrato su quel mese, senza cambiare il periodo.
   - **Dove vanno i soldi**: in alto le categorie (clic per sceglierne una; in rosso *Da classificare*, le uscite
     ancora senza categoria) e, sotto, la loro tabella (comprimibile, chiusa all'inizio). Poi a sinistra le
     sottocategorie della categoria scelta (clic per filtrare, di nuovo per togliere) e a destra la spesa per mese
     con media mobile a 3 mesi.
     - **Clic su un mese** della spesa per mese (o dall'Andamento mensile): tutta la pagina mostra solo quel
       mese, cioe' categorie, sottocategorie, esercenti, movimenti e quote della natura della spesa. Il grafico
       per mese resta sull'intero periodo, con il mese evidenziato, per poterne scegliere un altro; un secondo clic
       sullo stesso mese (o il pulsante con la ✕) toglie il filtro. Gli indicatori in alto diventano: uscite del
       mese, spesa della voce scelta nel mese e media mensile del periodo con lo scarto del mese (in verde se hai
       speso meno della media, in rosso se di piu').
     - **Esercenti principali**: grafico a barre dei primi 15 esercenti della voce scelta. Un clic su una barra
       filtra i movimenti su quell'esercente, di nuovo per togliere il filtro; cambiando categoria,
       sottocategoria o mese il filtro si toglie da solo.
     - **Movimenti** (comprimibile, chiuso all'inizio): solo le uscite e i rimborsi della voce scelta, cioe'
       esattamente quelli che compongono la barra. La tabella e' la stessa della pagina Movimenti e funziona
       allo stesso modo: selezionando una riga la classifichi (anche per tutto l'esercente), selezionandone
       piu' d'una assegni etichette e natura della spesa. Dopo il salvataggio i grafici si aggiornano.
     - **Budget**: se la voce scelta ha un budget nel perimetro scelto (*Personale*, *Comune* o *Tutti*, non una
       scelta di singoli conti), la spesa per mese mostra il budget come linea rossa tratteggiata; con un mese
       filtrato le barre delle categorie e delle sottocategorie hanno un segno all'importo del budget. Qui i
       rimborsi contano nel mese in cui arrivano, nella pagina Budget nel mese della spesa (vedi sotto).
   - **Budget**: vedi la sezione *Budget mensili*.
   - I grafici sono ECharts (componente `streamlit-echarts`, incluso nel pacchetto: nulla viene scaricato da
     internet mentre usi l'app), con animazioni di apertura e di aggiornamento.
   - Aspetto "chiaro a schede": colori e carattere in `.streamlit/config.toml` (sezione `[theme]`), il resto in
     cima ad `app.py`. Il carattere Inter sta in `static/fonts/` e viene servito dall'app stessa.
   In **Movimenti** la colonna *Ora* riporta l'ora dell'operazione dove la banca la scrive (pagamenti con carta
   di ING, Credem e Crédit Agricole, tutti i movimenti PayPal e Trade Republic, i movimenti Fineco che la
   riportano nella descrizione): serve a ritrovare l'operazione. La stessa colonna c'e' nelle tabelle di Revisione,
   Etichette e Saldo dei conti. La colonna *Descrizione* riporta il testo originale del movimento (per PayPal il tipo di
   operazione, il nome e l'oggetto); la colonna *Motivo* dice perche' un movimento e' da rivedere (categoria da assegnare o da
   confermare, trasferimento senza contropartita, addebito della carta senza estratto, prelievo da
   giustificare...). Una tua decisione sul movimento chiude sempre la revisione. Per un pagamento con carta o un
   addebito SEPA puoi classificare da li' anche l'esercente ("Vale per tutti i movimenti di questo esercente"):
   la tua conferma prevale sulle regole del programma, sui codici MCC e sui marchi.
   *Come conta* un movimento: secondo la categoria (la sua sezione dice se e' spesa, entrata o risparmio; una
   spesa con importo positivo e' un rimborso), oppure non conta, come trasferimento tra tuoi conti o escluso
   dalle analisi.
   La **differenza entrate uscite** e' quello che resta delle entrate dopo le uscite nel periodo: non e' il saldo
   dei conti, che si trova in *Analisi disponibilità*.
7. **Analisi disponibilità**: la pagina **Saldo dei conti** mostra un conto alla volta (i filtri della barra
   laterale qui non valgono) e il **Portafoglio** (vedi sotto).
   - Il saldo e' quello a fine giornata, calcolato dal saldo noto piu' vicino (inserito in Conti e copertura o
     letto da un estratto) piu' o meno i movimenti contabilizzati in mezzo, alla **data contabile**, con
     commissioni e imposte. Contano tutti i movimenti, anche quelli esclusi dalle analisi.
   - Vista **Giorno**: saldo a fine giornata, movimenti del giorno e differenza rispetto a 30 giorni prima; il
     grafico mostra il saldo dei 30 giorni che finiscono con il giorno scelto.
   - Vista **Periodo** (all'apertura il periodo scelto in Impostazioni, fino a oggi): saldo a inizio periodo (prima dei movimenti del primo giorno), a fine periodo con la
     variazione, e saldo minimo con la sua data. Il grafico ha il saldo (linea, asse a sinistra) e le uscite del
     giorno (barre, asse a destra): tutto cio' che fa scendere il saldo, anche addebito della carta, bonifici
     verso altri conti e acquisti di titoli.
   - In basso i movimenti, dal piu' recente, con il saldo dopo ciascuno. Se due saldi noti non tornano tra loro
     (manca un estratto) la pagina lo segnala e il grafico fa un salto in quel punto.
8. **Regole e tassonomia** (sotto Configurazione): modifichi gli esercenti gia' classificati (sottocategoria e nome), le tue regole e
   la tassonomia. Le sottocategorie si aggiungono, rinominano, spostano ed eliminano; quelle *predefinite*,
   usate da regole e configurazioni del programma, si possono solo spostare. Le modifiche stanno nel database
   e restano anche dopo un aggiornamento del programma. "Esporta la tassonomia" scarica un `tassonomia.yaml` nello stesso
   formato di `config/tassonomia.yaml`.
9. **Natura della spesa ed etichette** (sezioni facoltative, ognuna si accende o spegne in Impostazioni >
   Sezioni facoltative).
   - *Natura della spesa*: ogni uscita e' necessaria, svago o superflua. Il valore viene dalla sottocategoria
     (Regole e tassonomia, scheda *Natura della spesa*; quelli di partenza stanno in `config/attributi.yaml`)
     e si cambia sul singolo movimento nella pagina **Movimenti**, dove prevale. In *Dove vanno i soldi* trovi
     i totali e le barre mensili per natura.
   - *Etichette*: nomi liberi (es. "Vacanza Puglia 2025") da attaccare ai movimenti, anche piu' d'una per
     movimento e di categorie diverse. Si assegnano in **Movimenti** selezionando una o piu' righe, oppure
     nella pagina **Etichette** per periodo (tutte le uscite tra due date, togliendo quelle che non c'entrano).
     La pagina Etichette mostra totali, ripartizione per categoria e movimenti di ogni etichetta.
   Stanno in tabelle proprie: spegnendo una sezione spariscono la sua pagina, le colonne e i grafici, e i dati
   restano nel database per quando la riaccendi.

Caricare un file mancante in seguito e' sempre possibile: i duplicati vengono scartati e tutto
viene ricalcolato (trasferimenti, carta di credito, categorie).

**File sbagliato?** In **Importa**, sotto "File importati", apri "Annulla un import", scegli il file
e conferma. Vengono tolti i movimenti arrivati con quel file e le decisioni prese su di essi; quelli
presenti anche in un altro file dello stesso conto restano. Se il conto era stato creato solo per quel
file, puoi eliminarlo insieme. Poi ricarica il file giusto.

## Modello locale (facoltativo)

Senza modello locale l'app funziona lo stesso: restano le regole, i codici MCC, i marchi, lo storico
e le tue conferme.

**Installazione dall'app (Linux e Windows).** In **Impostazioni > Modello locale** premi "Salva e verifica". Se
Ollama non risponde, l'app spiega cosa manca e, se l'indirizzo e' questo PC:
- Ollama non installato: chiede se installarlo insieme ai modelli scritti nella casella. Spunti la conferma e
  premi **Installa Ollama**. Su Linux usa lo script ufficiale di ollama.com come servizio di sistema e chiede la
  password di amministratore con una finestra di sistema; su Windows lo installa solo per il tuo utente con
  winget o con l'installatore ufficiale della release su GitHub (solo se impronta e firma digitale sono di
  Ollama). Poi scarica i modelli. Imposta anche `OLLAMA_NO_CLOUD=1`.
- Ollama installato ma fermo: **Avvia Ollama e controlla i modelli**.

L'installazione continua in background anche se cambi pagina. Nella pagina resta un riquadro con lo spinner e la
fase in corso (con la percentuale quando e' nota, per esempio nel download dei modelli) e, sotto, i dettagli
dell'installazione, aperti finche' il lavoro e' in corso o finisce con un errore.
Se non riesce, l'app mostra cosa fare a mano. Lo stesso avviso compare in **Classificazione automatica**.

**Installazione a mano** su Linux:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b
```

Su Windows: installatore da https://ollama.com/download (oppure `winget install -e --id Ollama.Ollama`), poi
`ollama pull qwen2.5:3b` dal prompt dei comandi. Per escludere i modelli cloud: `setx OLLAMA_NO_CLOUD 1` e
riavvia Ollama dall'icona nell'area di notifica.

Per escludere i modelli cloud di Ollama su Linux, aggiungi la variabile al servizio:

```bash
sudo systemctl edit ollama.service
#   [Service]
#   Environment="OLLAMA_NO_CLOUD=1"
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

In **Impostazioni > Modello locale** controlla l'indirizzo (`http://127.0.0.1:11434`) e scrivi i modelli che
vuoi usare, separati da punto e virgola (es. `qwen2.5:3b; llama3.2:3b`), poi "Salva e verifica". Se uno non e'
installato l'app propone di scaricarlo e installarlo (dalla rete esce solo il nome del modello). In
**Classificazione automatica** scegli quale usare. I modelli cloud di Ollama (nome che finisce con `cloud`)
non sono ammessi: girano fuori dal PC. Sul mini PC la classificazione richiede alcuni secondi per esercente:
conviene lanciarla dopo un import grande e lasciarla lavorare.

## Claude (facoltativo)

In **Impostazioni > Claude** scrivi i modelli che vuoi usare, separati da punto e virgola (predefiniti:
`claude-haiku-5-5; claude-sonnet-5-5; claude-opus-5-5`; si puo' aggiungere ad esempio `claude-fable-5-1`); al
salvataggio l'app controlla con Claude che i nomi esistano. Il modello si sceglie a ogni invio. Inserisci poi la
chiave API: viene salvata nel portachiavi di sistema
(su Linux GNOME Keyring, su Windows la Gestione credenziali), non nel database ne' in file di testo. In alternativa puoi avviare l'app con la
variabile `ANTHROPIC_API_KEY`.

Cosa viene inviato: solo la chiave esercente (es. `CRF EXP GAR# MILANO`, con le cifre sostituite da `#`)
e il paese. Mai importi, date, IBAN, numeri di carta, nomi dei titolari o voci dell'area salute.
Un controllo blocca le voci con dati sospetti prima dell'anteprima e di nuovo prima dell'invio.
Restano solo locali anche gli esercenti dal nome neutro che l'app ha gia' collocato, anche solo come
proposta, in una sottocategoria della salute (dal modello locale, da un marchio noto o dal codice MCC della
carta): si classificano con il modello locale o a mano in **Revisione**. Il blocco segue la categoria della
salute anche se la rinomini in **Regole e tassonomia**.
Il testo che correggi a mano nell'anteprima viene normalizzato come gli altri (maiuscole, cifre sostituite da
`#`) e ricontrollato: l'anteprima mostra esattamente cio' che parte.
Insieme agli esercenti la richiesta contiene l'elenco delle sottocategorie di spesa. Se il nome di una
contiene qualcosa di personale, in **Regole e tassonomia** spunta *Non inviare a Claude*: quel nome non viene
scritto nelle richieste (e Claude non potra' proporla). Di partenza nessuna e' esclusa.
Ogni richiesta resta nel registro degli invii.

## Fineco

Dall'area clienti Fineco esporta in **Excel** la lista movimenti del conto corrente per il periodo che ti serve
(file `Fineco_movements_AAAAMMGG.xlsx`). Si importano bonifici (istantanei e SEPA, in entrata e in uscita), canone,
interessi e imposte di bollo; un'altra voce resta da classificare in **Revisione**.

- Si importano solo i movimenti **contabilizzati**: quelli ancora da contabilizzare compaiono tra le righe
  scartate e si importano con l'estratto successivo.
- Fineco spezza la descrizione completa con spazi anche in mezzo a parole, date e IBAN ("TRADE REPUBL IC").
  L'app legge comunque beneficiario, ordinante e IBAN: l'IBAN si riunisce e si tiene solo se la cifra di
  controllo torna; un nome spezzato si ricompone quando i pezzi formano una parola dei nomi di titolari e
  familiari, cosi' i bonifici a tuo nome si riconoscono.
- L'estratto ha solo il saldo finale: vale come saldo alla fine del periodo, e la quadratura si verifica
  dall'import successivo.
- Interessi passivi vanno in *FINANZA · Interessi passivi*, canone in *Commissioni bancarie*, imposte di bollo
  (conto e dossier titoli) in *Bollo*.

## PayPal

Su paypal.com: **Attivita' > Scarica report** (o *Estratti conto > Report attivita'*), formato **CSV**, il
periodo che ti serve (fino a 12 mesi per file). Importalo su un conto "PayPal" (tipo conto, perimetro
personale o comune). Il report ha piu' righe per la stessa operazione: l'app tiene il pagamento all'esercente
con il suo nome vero, converte in euro i pagamenti in valuta usando le righe di conversione di PayPal, salta
autorizzazioni e operazioni negate, e tratta i pagamenti da o verso persone come bonifici (i loro nomi non
diventano esercenti e non vanno mai verso Claude). Email, indirizzi e telefono restano solo nel livello grezzo
del database cifrato.

La stessa spesa compare anche sul conto o sulla carta che l'ha pagata ("PAYPAL *..." o addebito SEPA di
PayPal): l'app abbina quell'addebito alla ricarica dello stesso importo nel report (entro una settimana), lo
tratta come contenitore e conta la spesa una volta sola. La ricarica (per esempio "Bonifico bancario sul
conto PayPal" o "Versamento generico con carta") e' un trasferimento interno: si riconosce dalla dicitura o,
se PayPal ne usa una nuova, dal codice di riferimento che la lega al pagamento. Le righe "Promemoria"
(autorizzazioni) non cambiano il saldo e non entrano. Un addebito PayPal senza ricarica nel periodo del
report (per esempio perche' la banca lo registra piu' di una settimana dopo) compare in **Revisione > Addebiti PayPal**, per tutti i conti e le carte. Per ognuno l'app
propone il pagamento del report con lo stesso importo (entro 60 giorni) e il nome dell'esercente piu' simile
(es. "PAYPAL *VEEPEE IT" e "VENTE PRIVEE.COM SA"). Puoi accettare la proposta, anche per piu' addebiti insieme,
sceglierne un'altra, oppure indicare che l'addebito non e' nel report (spesa normale). Se il pagamento non
compare tra quelli con lo stesso importo, *Cerca in tutto il report PayPal* mostra tutte le uscite del report entro
i giorni scelti, di qualunque tipo e importo, con la differenza di importo e l'eventuale addebito a cui sono gia'
abbinate. La spesa del report pagata da un addebito sul conto o sulla carta conta una volta sola, con la data
dell'addebito. Le scelte si rivedono e
si tolgono in *Addebiti PayPal ricondotti a mano*. Sono gestiti il report in italiano e quello in inglese.

## Portafoglio titoli

Pagina **Portafoglio** sotto Analisi disponibilità (sezione facoltativa: *Impostazioni > Sezioni facoltative*). Costo e
plusvalenze vengono solo dai movimenti con ISIN gia' importati (oggi Trade Republic).
- **Costo medio ponderato**, come nel regime amministrato: gli acquisti aumentano quote e costo (prezzo piu'
  commissione); una vendita toglie il costo medio delle quote vendute e da' la plusvalenza o minusvalenza
  (ricavo meno commissione, meno costo). Le imposte sono quelle trattenute dalla banca: nessun calcolo fiscale.
- **Due modi di guardare il portafoglio**, scelti in cima alla pagina (all'apertura sempre il primo):
  - **Analisi attualizzata a oggi**: tutta la storia dei conti scelti, con l'ultimo prezzo disponibile (il periodo
    dei filtri non si applica). Compaiono anche le posizioni vendute, con lo stato "chiusa il ...".
  - **Analisi nel periodo selezionato**: le posizioni possedute nel periodo, con il prezzo alla fine del periodo.
    Quelle vendute dopo la fine del periodo contano come ancora possedute; quelle vendute durante il periodo
    compaiono come chiuse. Quelle vendute del tutto prima del periodo non si mostrano: la pagina dice quante sono
    e l'interruttore *Includi le N posizioni chiuse prima del ...* le aggiunge.
- **Indicatori**: capitale investito e valore di mercato contano solo le posizioni aperte (quelle senza prezzo al
  costo). **Rendimento posizioni aperte**: plus/minus latente sul costo delle posizioni aperte. **Rendimento
  complessivo**: plus/minus latenti + plus/minus realizzate con le vendite + dividendi lordi, sul costo di tutte le
  quote comprate (tenute e vendute), prima delle imposte. Esempio: compri 10 quote a 100 euro, ne vendi 5 a 120
  (+100), le 5 rimaste valgono 110 (+50): aperte +10%, complessivo (100 + 50) / 1.000 = +15%. Nell'analisi per
  periodo il rendimento complessivo va dal primo acquisto alla fine del periodo; realizzato, dividendi, imposte e
  commissioni sono quelli del periodo.
- **Posizioni** (stato, quote, costo medio, capitale investito, prezzo e data del prezzo, valore, plus/minus latente,
  rendimento, realizzato, dividendi, rendimento complessivo), **Vendite** con plus/minus, imposte e commissioni,
  **Per anno**, e il **capitale investito nel tempo**. Plus/minus latente, realizzato e i due rendimenti sono
  verdi se in guadagno, rossi se in perdita. "Prezzo vecchio" nelle note: l'ultima chiusura salvata di
  una posizione aperta ha piu' di 5 giorni (simbolo da controllare o prezzi non aggiornati).
- **Andamento nel tempo**: sotto la tabella, un grafico a linee delle posizioni selezionate (una o piu' insieme,
  fino a 8; ogni titolo tiene sempre lo stesso colore). Misure: valore (con una sola posizione anche il costo,
  tratteggiato), rendimento % sul costo, capitale investito. Le posizioni vendute si fermano al giorno della
  vendita; un titolo venduto e poi ricomprato ha un buco nel tratto senza quote. Nell'analisi per periodo una
  posizione chiusa prima del periodo si vede per intero, dall'acquisto alla vendita.
- **Prezzi di mercato da Yahoo Finance** (facoltativi, spenti finche' non li accendi in *Impostazioni > Sezioni
  facoltative*): chiusure giornaliere dei titoli in portafoglio, per valore di mercato, plus/minus latente e
  rendimento. Escono dal PC solo i codici ISIN (per trovare il simbolo) e i simboli di borsa: mai quote, importi,
  nomi o date dei tuoi movimenti (il primo scarico parte da una data fissa, il 1/1/2000, uguale per tutti; i
  successivi dall'ultimo prezzo salvato). Ogni richiesta finisce nel registro delle richieste. Si aggiornano da
  soli una volta al giorno aprendo la pagina, o con *Aggiorna i prezzi ora*. Se qualche titolo non si aggiorna
  (simbolo sbagliato, Yahoo non raggiungibile) un avviso in cima alla pagina dice quali, l'aggiornamento non
  risulta fatto e, riaprendo l'app, si riprovano solo quei titoli. Il simbolo si cerca dall'ISIN
  preferendo una borsa in euro (Xetra, Francoforte, Milano...); se e' sbagliato si corregge in *Simboli di borsa
  e prezzi*. Quotazioni in altra valuta: convertite in euro con il cambio del giorno.
- **Controlli**: vendite di piu' quote di quelle importate (storico che inizia dopo il primo acquisto), quote
  arrivate senza costo, dividendi calcolati su una quantita' diversa. Si risolvono indicando una **posizione di
  partenza** (data, quote, costo). Per le quote arrivate senza costo (migrazione) usa la data della migrazione:
  il costo vale per quelle quote, che non vengono contate due volte.
- Quote e costo medio si calcolano per conto: se lo stesso titolo sta su due conti, la vendita delle quote di
  uno usa il costo di quel conto. Nello stesso giorno gli eventi seguono l'ora dell'operazione.
- Le altre analisi non cambiano: la vendita resta "Rientro dal risparmio" per intero.

## Budget mensili

Pagina **Budget** sotto Analisi movimenti. Un budget e' l'importo che vuoi spendere ogni mese per una categoria di
spesa (es. Alimentari) o per una sua sottocategoria (es. Alimentari › Supermercato), su un perimetro di conti. Non
serve metterne uno per ogni categoria: la pagina parte vuota e mostra solo quelli che aggiungi. I filtri della
barra laterale qui non si usano: si sceglie il **mese** in cima alla pagina (all'apertura il mese in corso).

- **Aggiungi un budget**: categoria, sottocategoria (o *Tutta la categoria*), perimetro, importo al mese e mese
  da cui vale. Il perimetro dice su quali conti si conta la spesa: *Personale* (i conti personali), *Comune* (il
  conto comune) o *Tutti* (tutti i conti; i versamenti dal conto personale al conto comune si annullano). La
  stessa voce puo' avere un budget per perimetro.
- **Cosa conta**: le uscite della voce nel perimetro, al netto dei rimborsi. *Da classificare* non entra in
  nessun budget: la pagina dice quanto vale nel mese e porta alla Revisione.
  - Un **rimborso di un esercente** conta nel mese della spesa che restituisce: la spesa piu' vicina dello stesso
    esercente e della stessa categoria, fino a 90 giorni prima e non piu' piccola del rimborso. Esempio: compri a
    settembre e il rimborso arriva a ottobre: settembre, a posteriori, scende; ottobre non cambia. Un reso nello
    stesso mese libera budget in quel mese.
  - Un **rimborso da una persona** (un bonifico classificato con una categoria di spesa, es. i 150 euro per il
    supermercato) conta nel giorno in cui arriva.
  - Un rimborso di un esercente di cui l'app non trova la spesa **non conta** e compare in *Rimborsi non
    attribuiti a una spesa*.
- **Previsione a fine mese**, conto per conto: lo speso fino al giorno in cui arrivano i dati del conto, piu' la
  **mediana** della spesa del conto dal giorno dopo fino a fine mese negli ultimi 12 mesi interi (meno, se la
  storia del conto e' piu' corta). Esempio: Credem ha i dati fino al 20, la carta fino al 5; per Credem si stima
  la spesa dal 21, per la carta dal 6. Una spesa fatta prima del solito (es. pagata il 18 invece che il 25) si
  conta due volte: e' gia' nello speso ed e' ancora nella mediana.
- **Fin dove arrivano i dati**: la riga *Dati fino a* sotto il mese, con il giorno di ogni conto (passa il mouse
  sul nome per vedere da dove viene). In ordine: la fine del periodo scritta nel file (Credem, ING conto,
  Fineco), la data dell'estrazione nel nome del file (Fineco, Crédit Agricole), la data di caricamento (carta ING,
  ING in CSV, Trade Republic, PayPal; se il file arriva a piu' di un mese prima del caricamento, l'ultimo
  movimento del file). Un conto fermo da piu' di 7 giorni ha un avviso: importa un estratto piu' recente. I
  pagamenti con carta non ancora contabilizzati quando scarichi il file arrivano nel file successivo con date gia'
  "coperte": la previsione puo' sottostimare di qualche giorno di spese con carta.
- **Barre di saturazione**: una scheda per perimetro, i budget piu' saturi in alto e quelli delle sottocategorie
  sotto la loro categoria. Barra piena: speso; tratteggiata e piu' chiara: previsto fino a fine mese; linea
  tratteggiata al 100%. Due segnali diversi, con colore, icona e testo:
  - **superato** (lo speso e' gia' oltre il budget): barra rossa e "superato";
  - **a rischio** (sotto il budget, ma il previsto lo supera): barra ambra e "a rischio".
- **Dettaglio**: con un clic su una barra (all'apertura il budget messo peggio) vedi budget, speso, previsto e
  margine, il grafico della spesa cumulata giorno per giorno (speso, speso + previsto, curva tipica dei mesi
  passati, budget, giorno dei dati di ogni conto) e la tabella conto per conto. *Vedi i movimenti* apre *Dove
  vanno i soldi* su quella voce, quel perimetro e quel mese.
- **Storico degli ultimi 12 mesi**: per ogni budget la percentuale spesa mese per mese, dal piu' recente; in rosso
  i mesi oltre il budget, in ambra il mese in corso se il previsto lo supera.
- **Gestisci i budget**: cambia l'importo da un mese in poi (i mesi prima restano valutati con l'importo di
  allora), togli un cambio d'importo, elimina un budget con il suo storico.
- Rinominando o unendo categorie e sottocategorie in *Regole e tassonomia* i budget seguono. Una sottocategoria con
  un budget non si elimina senza unirla a un'altra; unendo due sottocategorie che hanno entrambe un budget nello
  stesso perimetro resta quello della destinazione.

## Categorie e sottocategorie

Ogni voce e' la coppia categoria + sottocategoria: la stessa sottocategoria puo' stare sotto categorie diverse
(es. CASA · Varie e ACQUISTI · Varie) e conferme, regole, decisioni, suddivisioni, natura della spesa e prove
indicano sempre entrambe. Nelle tabelle e nelle scelte le voci compaiono come "CATEGORIA · Sottocategoria".
Le voci salvate dalle versioni precedenti (con la sola sottocategoria) si completano da sole all'accesso.

## Risposte da confermare e confronto tra modelli

Le risposte del modello locale e di Claude non si applicano piu' da sole: ogni classificazione crea una
**prova** che compare in cima a **Classificazione automatica** (*Risposte da confermare*), con la categoria
proposta, la confidenza e il nome riconosciuto. Togli la spunta a quelle che non ti convincono e premi
**Applica**; **Non applicare** non cambia nulla; per Claude **Non applicare e rimetti in coda** rimanda le stesse
voci in coda, pronte per un altro modello. Il modello di Claude (Haiku, Sonnet, Opus) si sceglie accanto al
pulsante di invio.

Per misurare l'accuratezza: **Strumenti > Prova un modello su esercenti gia' classificati**. Scegli esercenti che
hanno gia' una categoria (confermata da te, da una tua regola, dalle regole o dal codice MCC), mandali a un
modello e confronta: le categorie attuali non cambiano. In **Strumenti > Confronto tra prove** metti a confronto
due o piu' prove: per ognuna risposte, confidenza media, quante sono sopra la soglia e quante coincidono con la
categoria attuale e con le tue scelte; sotto, esercente per esercente, la risposta e la confidenza di ogni
prova. Valgono gli stessi controlli della coda: salute e dati identificativi non escono.

Da **Revisione > Esercenti da classificare** puoi selezionare alcuni esercenti e premere **Classifica con
l'AI**: si apre Classificazione automatica limitata a quelli (*Solo gli N scelti in Revisione*, oppure *Tutti*),
con modello locale o Claude come sempre. Le risposte gia' in attesa per quegli esercenti compaiono filtrate e si
possono applicare da sole; quelle degli altri restano in attesa. La selezione si svuota da sola quando sono
tutti classificati, o con *Togli la selezione*.

In **Revisione** puoi selezionare piu' righe e confermarle insieme ognuna con la propria categoria
(**Conferma la categoria proposta**): esercenti con proposte di categorie diverse, bonifici ai familiari
con la proposta "come il ...", e in *Altri movimenti da rivedere* la categoria gia' presente o proposta. Per dare a tutte la
stessa categoria resta la scelta della categoria.

In **Revisione > Esercenti da classificare** la proposta e' divisa in colonne: categoria, fonte (Claude,
modello locale, storico, somiglianza, marchio, codice MCC), modello e confidenza. Un clic sull'intestazione
ordina la tabella.

## Dove stanno i dati

| Cosa | Dove |
| --- | --- |
| Database cifrato | Linux: `~/.local/share/CashFlowAnalyzer/spese.db` (cartella con permessi 700; fino alla 0.20.1 `~/.local/share/spese-app`, spostata da sola al primo avvio). Windows: `%LOCALAPPDATA%\CashFlowAnalyzer\spese.db` (nel tuo profilo utente, non sincronizzata con OneDrive) |
| Backup cifrati | stessa cartella, `backup_AAAAMMGG_HHMMSS.db`, stessa passphrase |
| Log del server | stessa cartella, `server.log` (solo messaggi del server) |
| Chiave API di Claude | portachiavi di sistema (Linux) o Gestione credenziali (Windows) |
| Export CSV del gold | dove lo salvi tu: **e' in chiaro**, conservalo con cura |

Per usare un'altra cartella: `SPESE_DATI=/percorso ./avvia.sh` su Linux, `set SPESE_DATI=D:\percorso` e poi
`avvia.cmd` su Windows.
Le righe originali di ogni file restano nel database cifrato (livello grezzo), quindi dopo l'import
puoi cancellare le copie scaricate dalla banca.

## Aggiornamento

Sostituisci i file del programma mantenendo la cartella `.venv`, poi rilancia `./install.sh` (Linux) o
`install.cmd` (Windows): installa le dipendenze nuove.
I dati non stanno nella cartella del programma, quindi non si perdono.
Prima di aggiornare conviene creare un backup da **Impostazioni**.

Se sposti la cartella del programma (es. su un altro disco), l'avvio da terminale funziona anche dalla nuova
posizione; rilancia l'installazione una volta per aggiornare la voce del menu, che punta ancora al vecchio
percorso.

## Disinstallazione

```bash
./disinstalla.sh               # Linux: toglie ambiente Python, icona del menu e chiave di Claude; i dati restano
./disinstalla.sh --anche-dati  # cancella anche database, backup e log (chiede di scrivere CANCELLA)
```

Su Windows: doppio clic su `disinstalla.cmd` (i dati restano) oppure `disinstalla.cmd -AncheDati`. Python
installato per l'app non viene tolto: se non serve ad altro si disinstalla da *Impostazioni* > *App*.

Senza l'opzione per i dati il database cifrato resta nella sua cartella: reinstallando lo ritrovi
con la stessa passphrase. Gli export CSV salvati altrove non vengono toccati. Alla fine puoi cancellare
la cartella del programma. Ollama, se l'hai installato, si toglie a parte.

## Struttura

| Percorso | Contenuto |
| --- | --- |
| `app.py` | Accesso con passphrase e menu delle pagine |
| `avvia.py` | Avviatore per Linux e Windows (`avvia.sh`, `avvia.cmd` e il menu lo richiamano) |
| `install.sh`, `install.cmd` + `install.ps1` | Installazione su Linux e su Windows (con il controllo di Python) |
| `tray.py`, `icona_windows.py` | Icona nell'area di notifica su Linux (GTK) e su Windows |
| `spese/` | Lettura dei formati, import, catena di categorizzazione, ricalcolo, privacy, modelli |
| `spese/ui/` | Pagine dell'interfaccia e grafici (ECharts) |
| `config/profili/` | Un file YAML per formato bancario: firma, colonne (anche uscite ed entrate separate), codici, righe di saldo, formati di numeri e date |
| `config/tipi_movimento.yaml` | Tipi standard di movimento e loro natura (entrata, uscita, risparmio, trasferimento...) |
| `config/tassonomia.yaml` | 18 categorie, 93 sottocategorie (uscite, entrate, risparmio); l'area salute e' solo locale |
| `config/attributi.yaml` | Natura della spesa: valori e valore di partenza per ogni sottocategoria |
| `config/regole_esercenti_it.yaml` | Regole su marchi e parole chiave, con forza alta/bassa |
| `config/mcc_categorie.csv`, `marchi_nsi.json`, `osm_tag_categorie.yaml` | Generati da `sorgenti/` |
| `strumenti/` | Rigenerazione delle configurazioni, profilazione dei file, prova della catena |
| `tests/` | Test automatici con file sintetici (ING, Credem, Trade Republic, PayPal) e dati inventati |

Ordine della catena di categorizzazione: tua decisione sul movimento, tue regole, categoria del tipo
di movimento, esercenti confermati da te, regole forti, codice MCC, regole deboli, marchi, cache (modello
locale, Claude), storico, somiglianza con esercenti gia' noti, proposta da confermare. Regole e marchi si
cercano una volta per nome di esercente e poi si riusano finche' l'app resta aperta.

## Test

```bash
.venv/bin/pip install -r requirements-dev.txt          # Windows: .venv\Scripts\pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests                    # Windows: .venv\Scripts\python -m pytest -q tests
```

Su GitHub gli stessi test, l'installazione e l'avvio vengono provati su Linux e Windows a ogni modifica.

## Strumenti da riga di comando

```bash
.venv/bin/python strumenti/costruisci_configurazioni.py      # rigenera i file derivati e verifica la tassonomia
.venv/bin/python strumenti/prova_catena.py estratto.csv --nomi "Nome,Cognome"
.venv/bin/python strumenti/profila_movimenti.py estratto.csv  # report mascherato della struttura
```

Origine e licenze dei dati di configurazione: vedi `NOTICE.md`.
