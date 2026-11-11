# CashFlow Analyzer (già Spese di famiglia) – Registro delle modifiche

Le versioni piu' recenti sono in alto. Per ogni versione: novita', correzioni e cosa cambia aggiornando.
Questo file non fa parte del pacchetto: viene consegnato a parte a ogni nuova versione.

---

## 0.17.0 – 11 novembre 2026 · "Dove vanno i soldi" piu' interattiva

**Novita'** (pagina *Dove vanno i soldi*)
- **"Da classificare" in rosso** nel grafico delle categorie (rosso tenue quando e' scelta un'altra categoria),
  per vedere subito quanta spesa e' ancora senza categoria.
- **Clic su un mese** nel grafico della spesa per mese: tutta la pagina mostra solo quel mese (categorie,
  sottocategorie, esercenti, movimenti, quote della natura della spesa). Il grafico per mese resta sull'intero
  periodo con il mese evidenziato; un secondo clic sullo stesso mese toglie il filtro. Con un mese scelto gli
  indicatori mostrano la spesa della voce nel mese e la media mensile del periodo con lo scarto del mese.
- **Tabella delle categorie** spostata subito sotto il grafico delle categorie (comprimibile, chiusa all'inizio).
- **Esercenti principali come grafico a barre** (primi 15 per importo). Un clic su un esercente filtra i
  movimenti, di nuovo per togliere il filtro.
- **Movimenti con la stessa tabella della pagina Movimenti**: stesse colonne e stessa classificazione (una riga:
  come conta, categoria, anche per tutto l'esercente; piu' righe: etichette e natura della spesa). Il riquadro
  resta chiuso all'inizio e resta aperto se lo apri, anche cambiando esercente.

**Correzioni**
- Nell'elenco dei movimenti di *Dove vanno i soldi* comparivano anche righe che il grafico non conta: sotto
  "Da classificare" giroconti tra i tuoi conti, addebiti riepilogativi della carta ed esclusi (non hanno
  categoria); sotto una categoria le eventuali entrate con quella categoria. Ora l'elenco contiene solo uscite e
  rimborsi e somma esattamente quanto la barra. Esempio con i dati di prova: sotto "Da classificare" la barra
  contava 7 movimenti (174,99 €) e l'elenco ne mostrava 11, con 3 giroconti e un addebito della carta in piu'.
- Test: tolta un'importazione inutile segnalata da pyflakes.

**Aggiornando**
- Estrai sopra la cartella e riavvia. Il database non cambia e i numeri delle analisi restano gli stessi.
- 159 test (erano 150): nuovi test su dati sintetici per il rosso, il filtro per esercente e per mese, l'elenco
  dei movimenti e la pagina (AppTest).

---

## 0.16.2 – 11 novembre 2026 · correzioni dalla revisione del codice

Correzioni dei difetti trovati dalla revisione della 0.16.1 (due revisioni indipendenti, 36 segnalazioni).
Ogni correzione ha il suo test: 150 test in tutto (erano 90).

**Privacy verso Claude**
- **Un solo punto di controllo** (`privacy.uscita_claude`) per tutto cio' che esce: coda, prove di confronto,
  testo corretto a mano. Lo stesso controllo viene ripetuto subito prima della rete: un lotto diverso da
  quello dell'anteprima non parte.
- **Dati identificativi nel nome dell'esercente**: IBAN, codice fiscale, numero di carta, telefono ed email
  si cercavano sulla chiave, dove le cifre sono gia' `#`, quindi non scattavano mai. Ora si cercano sui nomi
  originali dei movimenti e, sulla chiave, nella forma mascherata (`RSSMRA#A#F#X`).
- **Testo corretto a mano**: viene normalizzato come una chiave (cifre in `#`) e ricontrollato; svuotando la
  cella torna la chiave dell'esercente. L'anteprima mostra esattamente cio' che parte.
- **Tassonomia**: nuova colonna *Non inviare a Claude* in Regole e tassonomia. Il nome delle sottocategorie
  spuntate non viene scritto nelle richieste a Claude. Di partenza nessuna e' esclusa. Il segno segue la voce
  se la rinomini e finisce nell'export YAML (`non_inviare`).
- **PayPal, "Pagamento da cellulare" senza oggetto**: e' denaro inviato a una persona, non una spesa presso un
  esercente. Il nome non diventa piu' un esercente.
- **Area salute**: elenco di parole molto piu' ampio (specialita' mediche, "dr", prelievi, ambulatori, alcuni
  centri noti), perche' molti estratti non hanno il codice MCC.
- **Ollama su un altro computer**: resta una tua scelta, ma ora Impostazioni e Classificazione automatica
  avvisano che i nomi degli esercenti vanno a quel computer.

**Carta di credito**
- **Addebito mensile azzerato per errore**: righe di un estratto finite in un conto non di tipo Carta
  azzeravano anche gli addebiti dei mesi dopo e quelli di altri conti. Ora ogni riga copre un solo addebito,
  solo se la somma torna al centesimo, e una riga finita nel conto A non spiega un addebito del conto B.
- **Estratto mancante o parziale**: senza un tratto esatto l'addebito prendeva le righe del ciclo successivo
  e sfasava tutti i mesi dopo. Ora prima si abbinano tutti i tratti esatti, poi gli addebiti rimasti usano
  solo le righe avanzate. Se le righe spiegano meno dell'addebito, la differenza conta come spesa "Varie" da
  rivedere (componente "residuo") finche' non importi il resto.
- **Stesso file su due conti**: la deduplica vale dentro un conto. L'import ora dice in quale conto il file e'
  gia', chiede la spunta *Importa comunque* se il conto e' un altro, e le righe di carta rimaste in un conto
  non di tipo Carta risultano da rivedere.

**Import**
- Le scelte fatte per un file (conto, *Importa comunque*) e il suo report sono legate al contenuto del file,
  non alla posizione nell'elenco o al nome: togliendo un file le scelte non passano a un altro, e un file
  diverso con lo stesso nome non mostra il report del precedente.
- Due righe con lo stesso identificativo della banca nello stesso file: la seconda resta scartata, ma l'import
  lo segnala.
- Annullando l'import fatto per sbaglio su un conto registrato a mano, il conto torna senza formato e banca.
- Fineco: aggiunte le causali piu' comuni (carta, prelievi, addebiti SEPA, stipendio, giroconto, titoli) e
  l'esercente dei pagamenti con carta. **Da confermare con un estratto reale**: una dicitura che non
  corrisponde lascia il movimento "altro", come prima.

**PayPal**
- La spesa del report coperta da un addebito in banca ora sta **sul conto che l'ha pagata**: resta nel
  perimetro giusto (comune o personale) e si vede anche guardando il solo conto in banca.
- **Rimborso riaccreditato sulla carta**: contava due volte (nel report e in banca). L'accredito in banca si
  abbina all'uscita dal conto PayPal e i due diventano un passaggio tra i tuoi conti; resta il rimborso del
  report. Un accredito PayPal senza uscita nel report, in un periodo coperto, va in revisione.
- **Stesso pagamento ricondotto a mano a due addebiti**: il secondo sostituisce il primo, che torna da
  controllare (prima sparivano entrambi dai conti).
- **Ricariche**: per i pagamenti in valuta il confronto e' sull'importo in euro; la ricarica deve essere degli
  stessi giorni del pagamento; uno storno senza nome e' un rimborso dell'esercente del pagamento, non una
  ricarica; una ricarica piu' piccola del pagamento resta un'entrata da rivedere.

**Trasferimenti, contributi, risparmio**
- Abbinamento automatico solo con un indizio **per lato**: "giroconto a favore di MARIO ROSSI" non basta piu' ad
  abbinare un bonifico qualunque dello stesso importo. Una coppia e' esclusa se l'IBAN di un lato porta a un
  altro dei tuoi conti.
- Versamento al conto comune (o dal comune) quando l'altro conto non copre ancora quella data: e' un
  contributo, non un trasferimento interno. Guardando i due conti insieme si annulla.
- Conto di tipo broker con i movimenti importati: il versamento e' un passaggio tra conti, il risparmio sono
  gli acquisti di titoli (prima contava due volte).
- **Natura e categoria coerenti** per ogni fonte: regole, conferme dell'esercente, decisioni senza natura.
- I bonifici con terzi senza nome ne' IBAN compaiono in Revisione > Altri movimenti.

**Portafoglio**
- Quote e costo medio per conto e titolo; eventi dello stesso giorno nell'ordine dell'ora (senza ora: prima
  acquisti, poi vendite); posizione di partenza con la data della migrazione ne indica il costo senza
  raddoppiare le quote.
- Prezzi e cambi salvati come testo decimale e calcoli in decimale, con arrotondamento commerciale.
- Importato uno storico piu' vecchio, i prezzi mancanti vengono scaricati.

**Database e sicurezza**
- Il ricalcolo sta in una sola transazione di scrittura: due schede non si sovrascrivono le analisi.
- Il backup tiene il blocco di scrittura durante la copia: se un'altra scheda sta scrivendo, rinuncia con un
  messaggio invece di copiare dati a meta'.
- Versione dello schema salvata nel database: un database usato con una versione piu' recente non si apre.
- Per cambiare la passphrase serve quella attuale.
- Scritture di piu' righe raccolte in transazioni; indici su `analisi`; export CSV preparato solo su richiesta.
- Windows: la verifica della firma di Ollama non si rompe piu' con un apostrofo nel nome utente e controlla
  l'intestatario del certificato; `pyvenv.cfg` letto in UTF-8.

**Non corretto**
- Il cookie di sessione resta leggibile da JavaScript: Streamlit non permette di impostare un cookie HttpOnly.
- Dati personali nella storia del repository (escluso da questo pacchetto per scelta).

**Aggiornando**
- Estrai sopra la cartella e riavvia. Al primo accesso il database si aggiorna da solo (prezzi di mercato,
  riconduzioni PayPal doppie). **Fai prima un backup**: il database aggiornato non e' fatto per tornare alla 0.16.1 (i prezzi di mercato
  sono salvati in un altro formato).
- I numeri possono cambiare: addebiti della carta prima azzerati tornano a contare; le spese PayPal passano
  dal conto PayPal al conto che le ha pagate; alcuni trasferimenti prima abbinati da soli tornano da confermare.
- La tassonomia esportata ha una voce in piu' (`non_inviare`).

---

## 0.16.1 – 10 novembre 2026 · ricariche PayPal, carta di credito nel conto sbagliato

**Correzioni**
- **Ricariche PayPal contate come entrate**: la ricarica "Versamento generico con carta" (PayPal prende i soldi
  dalla carta per pagare una spesa) non era riconosciuta e diventava un'entrata da classificare. Di
  conseguenza l'addebito della stessa spesa sul conto o sulla carta non si abbinava: le uscite e le entrate
  risultavano gonfiate dell'importo della spesa, o andava ricondotto a mano in Revisione.
  - La ricarica si riconosce anche dalla dicitura "Versamento" e, con qualunque dicitura, dal codice di
    riferimento che la lega al pagamento dello stesso importo nel report.
  - I report gia' importati si correggono da soli al primo ricalcolo (all'accesso): le ricariche diventano
    trasferimenti interni e gli addebiti in banca si abbinano. Le riconduzioni fatte a mano restano.
- **Estratto della carta di credito importato nel conto invece che in un conto di tipo Carta**: le spese
  della carta e l'addebito mensile contavano entrambi, cioe' due volte.
  - All'import l'app avvisa quando il tipo del conto scelto non e' quello del file (carta o conto) e importa
    solo con la spunta *Importa comunque in questo conto*.
  - Nel ricalcolo un addebito della carta non conta se nel suo periodo ci sono righe arrivate da un estratto
    di carta in un conto che non e' una carta, o in una carta senza conto di addebito: resta da rivedere con
    il motivo e con cosa fare. Prima contava come spesa "Varie" con il motivo, fuorviante, "importa
    l'estratto della carta", che ora resta solo quando le righe della carta mancano davvero.
- **PayPal, righe "Promemoria"** (autorizzazioni, nei report in italiano): scartate come le righe "Memo". Finora
  si scartavano solo grazie al tipo "Autorizzazione". Un impatto sul saldo che il profilo non conosce viene
  segnalato nel riepilogo dell'import.

**Novita'**
- **Movimenti**: nuova colonna *Descrizione* con il testo originale del movimento (per PayPal tipo di
  operazione, nome e oggetto).

**Aggiornando**
- Windows: ferma l'app, estrai la nuova versione sopra la cartella e avvia dal menu Start.
- Linux: estrai sopra la cartella e riavvia l'app. Non serve reinstallare.
- Al primo accesso le ricariche PayPal importate come entrate diventano trasferimenti interni: entrate e
  uscite dei mesi con spese PayPal possono calare.
- Se l'estratto di una carta e' in un conto che non e' di tipo Carta, in Revisione > Altri movimenti gli
  addebiti della carta lo segnalano: annulla l'import del file della carta e reimportalo in un conto di tipo
  Carta, indicando il conto di addebito.

---

## 0.16.0 – 7 novembre 2026 · Fineco, avanzamento dell'installazione di Ollama

**Novita'**
- **Fineco** come nuova fonte: lista movimenti del conto corrente in Excel (`Fineco_movements_AAAAMMGG.xlsx`).
  - Bonifici istantanei e SEPA (in entrata e in uscita), canone, interessi e imposte di bollo di conto e
    dossier titoli. Numero di conto e intestatario si leggono dalla testa del file; l'intestatario si aggiunge
    ai titolari come per ING.
  - Si importano solo i movimenti *Contabilizzati*: gli altri compaiono tra le righe scartate, con il motivo, e
    si importano con l'estratto successivo.
  - Fineco spezza la descrizione completa con spazi anche in mezzo a parole, date e IBAN. Beneficiario,
    ordinante e IBAN si leggono comunque: l'IBAN si riunisce ed e' tenuto solo se la cifra di controllo torna;
    un nome spezzato ("MARIO RO SSI") si ricompone con le parole dei nomi di titolari e familiari, cosi' i
    bonifici a tuo nome si riconoscono e quelli verso i tuoi conti si abbinano per IBAN.
  - Il saldo finale vale come saldo alla fine del periodo: la quadratura si verifica dall'import successivo.
- **Interessi passivi** in *FINANZA · Interessi passivi* invece che in *Commissioni bancarie*, per tutte le
  banche, quando la descrizione li nomina.

**Correzioni**
- **Installazione di Ollama**: la barra di avanzamento restava a zero per tutta l'installazione con winget,
  che non dice a che punto e'. Al suo posto c'e' un riquadro con lo spinner e la fase in corso (con la
  percentuale quando e' nota, per esempio nel download dei modelli) e il tempo trascorso. I dettagli
  dell'installazione sono dentro il riquadro, aperti finche' il lavoro e' in corso o finisce con un errore.
  - Nei dettagli, le barre di avanzamento di winget e curl non riempiono piu' il registro con centinaia di righe
    quasi uguali: ogni aggiornamento prende il posto del precedente.

**Aggiornando**
- Windows: ferma l'app, estrai la nuova versione sopra la cartella e avvia dal menu Start.
- Linux: estrai sopra la cartella e riavvia l'app. Non serve reinstallare.
- Al primo ricalcolo i movimenti con "interessi passivi" nella descrizione passano a *Interessi passivi*.

---

## 0.15.0 – 7 novembre 2026 · installazione di Ollama dall'app

**Novita'**
- **Ollama non raggiungibile: messaggio chiaro e installazione dall'app**, su Linux e Windows. Al posto
  dell'errore tecnico ("HTTPConnectionPool… WinError 10061…", ora tra i dettagli) l'app dice cosa manca:
  - Ollama non installato su questo PC: chiede se installarlo insieme ai modelli scritti nella casella
    (conferma con la spunta, poi **Installa Ollama**);
  - Ollama installato ma fermo: **Avvia Ollama e controlla i modelli**;
  - indirizzo di un altro computer: indica cosa controllare, senza proporre installazioni.
- **Come installa**:
  - **Linux**: script ufficiale di ollama.com, come servizio di sistema. La password di amministratore la chiede
    una finestra di sistema (pkexec); senza desktop l'app mostra il comando da lanciare nel terminale.
  - **Windows**: per il solo utente, senza permessi di amministratore, con winget. Se winget manca o non riesce,
    usa l'installatore della release ufficiale su GitHub, eseguito solo se l'impronta SHA-256 corrisponde a
    quella pubblicata e la firma digitale e' valida e di Ollama.
  - Poi avvia Ollama e scarica i modelli indicati. Imposta `OLLAMA_NO_CLOUD=1`: su Linux sul servizio, su
    Windows tra le variabili dell'utente.
- L'installazione gira in background: si puo' cambiare pagina, l'avanzamento e i dettagli restano visibili.
  Lo stesso avviso c'e' anche in **Classificazione automatica**.
- **Privacy**: dalla rete escono solo le richieste del programma Ollama e i nomi dei modelli; parte solo dopo
  la conferma esplicita.
- **Test su GitHub**: nuova azione *Ollama* che installa davvero Ollama e un modello piccolo su Linux, su
  Windows con winget e su Windows con l'installatore di GitHub. Parte quando cambia il codice
  dell'installazione.

**Aggiornando**
- Windows: ferma l'app, estrai la nuova versione sopra la cartella e avvia dal menu Start.
- Linux: estrai sopra la cartella e riavvia l'app. Non serve reinstallare.

---

## 0.14.2 – 7 novembre 2026 · Windows: icona senza finestra del terminale

**Correzioni**
- **Windows 11**: con Windows Terminal come terminale predefinito, all'avvio restava aperta una scheda del
  terminale con il percorso di `.venv\Scripts\pythonw.exe`; chiudendola si chiudeva l'icona (server e pagina
  continuavano a funzionare). La 0.14.1 aveva sistemato il server, non l'icona.
  - La scheda nasceva dal passaggio per `.venv\Scripts\pythonw.exe`, un piccolo lanciatore che avvia a sua
    volta il Python vero.
  - Ora l'icona parte direttamente con il `pythonw.exe` del Python installato (indicato in `.venv\pyvenv.cfg`),
    che non ha console, e aggiunge da se' le librerie di `.venv`. Se avesse comunque una console, la stacca.
  - Anche il collegamento del menu Start usa il `pythonw.exe` del Python installato.
  - Nel log dell'icona (`icona.log`) c'e' la riga con il Python usato e se aveva una console all'avvio.
- **Test su GitHub**: avviano l'app con lo stesso programma e gli stessi argomenti del collegamento del menu
  Start, e controllano che l'icona giri con il Python installato e senza console.

**Aggiornando**
- Windows: ferma l'app (Ferma dall'icona o `avvia.cmd --ferma`), estrai la nuova versione sopra la cartella e
  **rilancia `install.cmd`**, che aggiorna il collegamento del menu Start (pochi secondi: le dipendenze ci sono
  gia').
- Linux: nessun cambiamento.

---

## 0.14.1 – 7 novembre 2026 · Windows: avvio senza finestre

**Correzioni**
- **Windows**: avviando l'app dal menu Start restava aperta una finestra del terminale con il percorso di
  `.venv\Scripts\pythonw.exe`.
  - Il `python.exe` dell'ambiente `.venv` e' un piccolo lanciatore che avvia il Python vero. L'avviatore lo
    faceva partire senza console (`DETACHED_PROCESS`), e Windows apriva per il Python vero, cioe' per il server,
    una finestra nuova e visibile.
  - Ora server e icona partono con una console nascosta (`CREATE_NO_WINDOW`, finestra `SW_HIDE`), che anche il
    Python vero eredita: nessuna finestra all'avvio, al riavvio dall'icona o con `avvia.cmd`.
- **Test su GitHub**: dopo l'avvio completo, quello del collegamento del menu Start, controllano che non resti
  aperta nessuna finestra di Python.

**Aggiornando**
- Windows: estrai la nuova versione sopra la cartella attuale e avvia dal menu Start; non serve reinstallare.
- Linux: nessun cambiamento.

---

## 0.14.0 – 7 novembre 2026 · CashFlow Analyzer anche su Windows

**Novita'**
- **Windows 10 e 11**: l'app si installa e si usa come su Linux.
  - `install.cmd` (doppio clic) cerca Python da 3.11 a 3.14, escluso quello del Microsoft Store. Se manca chiede
    conferma e installa Python 3.13 solo per l'utente, senza permessi di amministratore: con winget, oppure,
    se winget non c'e' o non riesce, con l'installatore ufficiale di python.org, eseguito solo se la sua
    impronta SHA-256 corrisponde a quella scritta nello script.
  - Poi crea l'ambiente `.venv`, installa le dipendenze e aggiunge **CashFlow Analyzer** al menu Start.
  - Icona nell'area di notifica con lo stesso menu di Linux (apri, riavvia o avvia, ferma) e notifiche.
  - Dati in `%LOCALAPPDATA%\CashFlowAnalyzer`; chiave di Claude nella Gestione credenziali di Windows.
  - Database cifrato con `sqlcipher3-wheels`, la variante di SQLCipher con i pacchetti per Windows.
  - `disinstalla.cmd` toglie ambiente, collegamento e chiave; i dati restano, salvo `-AncheDati`.
- **Avviatore unico** `avvia.py` per i due sistemi: avvio, riavvio, arresto e apertura del browser.
  `avvia.sh` (Linux) e `avvia.cmd` (Windows) lo richiamano con le stesse opzioni di prima.
- **Linux, `install.sh`**: se mancano Python 3.11 o successivo o il modulo `venv`, riconosce la distribuzione
  (Mint, Ubuntu, Debian, Fedora, Arch, openSUSE), mostra il comando per installarli e lo esegue dopo conferma.
  Con `--si` risponde si' alle domande.
- **Test su GitHub**: a ogni push l'azione *Test* installa e prova l'app su Linux e su Windows (anche
  l'installazione di Python, sia con winget sia da python.org), con test automatici, avvio, riavvio e arresto.
  La release parte solo se questi test passano, e il pacchetto (`.zip` e `.tar.gz`) e' unico per i due sistemi.

**Correzioni**
- Portachiavi: il controllo della sessione D-Bus valeva su ogni sistema e su Windows avrebbe escluso la
  Gestione credenziali. Ora vale solo su Linux.

**Aggiornando**
- Linux: rilancia `./install.sh`. Database, dati e voce del menu restano gli stessi; l'icona dell'area di
  notifica e' quella di prima.
- Windows: prima installazione con `install.cmd`.

---

## 0.13.1 – 6 novembre 2026 · release automatiche su GitHub, dati di prova ripuliti

**Novita'**
- **Release su GitHub**: pubblicando un tag di versione (per esempio `v0.13.1`) GitHub esegue i test e crea
  la release con il pacchetto per Linux (`.tar.gz` e `.zip`), le impronte SHA-256 e le note prese da questo
  registro (`.github/workflows/release.yml`). Se il tag non corrisponde alla versione dell'app, la release
  non parte.
- Il `README.md` spiega come pubblicare una nuova versione.
- **Autore**: Nahlt e' indicato come autore e titolare del copyright in `README.md`, `NOTICE.md`, nel manuale e
  in fondo alla barra laterale, accanto alla versione.

**Correzioni**
- *SVAGO E CULTURA · Spese Barca*: la natura della spesa di partenza (`config/attributi.yaml`) usava ancora il
  vecchio nome della voce e non veniva applicata.
- Test: sostituiti con valori fittizi i codici di riferimento di tre bonifici e le ultime cifre di una carta
  presi da movimenti reali.

**Aggiornando**
- Nessun intervento: database e impostazioni restano come sono.

---

## 0.13.0 – 6 novembre 2026 · menu riordinato, tassonomia nel pacchetto, pubblicazione su GitHub

**Novita'**
- **Revisione** ha quattro schede:
  - *Esercenti da classificare*;
  - *Bonifici e trasferimenti*;
  - *Addebiti PayPal*, che prima era una sezione dentro *Altri movimenti*;
  - *Altri movimenti da rivedere*, che prima si chiamava *Altri movimenti*.
- **Regole e tassonomia** passa dal gruppo Classificazione al gruppo **Configurazione**, dopo Conti e copertura.
- **Versione dell'app** scritta in basso nella barra laterale, sotto il pulsante *Esci e chiudi il database*.
- **Tassonomia di partenza** (`config/tassonomia.yaml`) aggiornata con quella usata nella pratica: 18
  categorie e 93 sottocategorie (prima 16 e 84).
  - Nuove sottocategorie: *VIAGGI · Ristorante*, *SVAGO E CULTURA · Spese Barca*, *PERSONA ·
    Parrucchiere*, *FINANZA · Assicurazione Vita / Assicurazione Salute / Satispay*, *TASSE E PA ·
    Commercialista*, *LAVORO · Bar*, *AMICI · Rimborsi ad amici*.
  - Natura della spesa di partenza per le nuove voci (`config/attributi.yaml`): necessaria per Parrucchiere,
    Assicurazione Vita, Assicurazione Salute e Commercialista; svago per Ristorante e Spese Barca.
  - Chi ha gia' un database tiene la tassonomia salvata li': la nuova vale per i database nuovi e come
    riferimento per il ripristino.
- **Documentazione**:
  - il manuale diventa `MANUALE.md`, aggiornato a menu e schede nuove;
  - `README.md` e' ora la presentazione del progetto per GitHub;
  - `NOTICE.md` precisa che la GPL v3 del progetto non copre i dati con licenza PolyForm Noncommercial.

**Correzioni**
- **Pulsante *Esci e chiudi il database***: si sovrapponeva alla linea di separazione della barra laterale.
  Ora linea, pulsante e versione stanno in un riquadro proprio.
- **Addebiti PayPal**: lo stesso pagamento del report poteva essere proposto a due addebiti uguali (es. due mesi
  dello stesso abbonamento). Accettando le proposte in blocco, entrambi gli addebiti diventavano contenitori e
  una spesa spariva. Ora la proposta va solo all'addebito piu' simile e piu' vicino di data; l'altro si sceglie
  a mano.
- **Addebiti PayPal**: "-1 giorni" diventa "-1 giorno".
- **Salvataggi in un'unica transazione** anche per le suddivisioni e per la pulizia dei nomi dei titolari dagli
  esercenti dopo un import.
- **Dati personali tolti** dal codice e dai test, prima della pubblicazione su GitHub: nomi e un IBAN parziale
  sostituiti con dati inventati.

**Cosa cambia aggiornando**
- Il pacchetto contiene `MANUALE.md` (il manuale) e un nuovo `README.md` (presentazione del progetto).

---

## 0.12.0 – 6 novembre 2026 · "Come conta" al posto di Natura + Categoria

**Novita'**
- **Modifica di un movimento** (Movimenti e Revisione > Altri movimenti): al posto dei due campi Natura e
  Categoria c'e' *Come conta*, con tre scelte.
  - *Secondo la categoria*: la sezione della categoria dice se e' una spesa, un'entrata o un risparmio. Una
    spesa con importo positivo diventa da sola un rimborso, come prima.
  - *Trasferimento tra miei conti* e *Escluso dalle analisi*: il movimento non conta (le due scelte hanno lo
    stesso effetto sui totali, cambia solo l'etichetta).
  - Per un contenitore, come l'addebito di una carta o di PayPal gia' dettagliato altrove, c'e' anche
    *Contenitore*, preselezionato, cosi' non lo si fa contare per sbaglio.
  - La categoria attuale e' gia' selezionata. Salvare *secondo la categoria* senza sceglierne una da' un
    avviso.
- In Movimenti la colonna *Natura* si chiama *Conta come*.

**Correzioni**
- **Natura e categoria incoerenti**: per esempio un'uscita classificata *Stipendio* finiva tra le spese sotto
  ENTRATE, e un rimborso con una categoria di risparmio restava una spesa. Ora natura e categoria non possono
  piu' essere in disaccordo, ovunque si confermi un movimento, anche nelle conferme multiple in Revisione.
  - Anche le decisioni gia' salvate vengono corrette al ricalcolo.
- **Spesa PayPal pagata da un addebito in banca**: conta una volta sola, come prima, ma ora con la data
  dell'addebito sul conto o sulla carta invece che con quella del report PayPal. Cambia il mese quando le due
  date sono a cavallo di fine mese, per esempio PayPal 30/09 e Trade Republic 01/10: la spesa va in ottobre.

**Cosa cambia aggiornando**
- Al primo ricalcolo le decisioni con natura e categoria incoerenti si allineano alla categoria, e le spese
  PayPal coperte da un addebito prendono la data dell'addebito.

---

## 0.11.3 – 6 novembre 2026 · proposte PayPal piu' complete

**Correzioni**
- **Addebiti PayPal da ricondurre**: le proposte cercavano solo i pagamenti a esercenti. Per questo un
  pagamento a una persona o con un tipo che il profilo non riconosce come esercente (es. "Pagamento generico ·
  <nome della persona>") compariva solo nella ricerca libera, anche con somiglianza alta (94%). Ora le
  proposte considerano tutte le uscite del report tranne ricariche e prelievi.
  - Lo stesso vale nell'abbinamento automatico: quando una ricarica copre un pagamento, il pagamento coperto
    puo' essere anche uno di questi.
- **Addebiti SEPA di PayPal** ("SDD A : PAYPAL EUROPE S.A.R.L. ET CIE S.C.A ..."): il nome non riporta
  l'esercente, quindi la somiglianza era sempre bassa e la proposta non c'era quasi mai.
  - Ora per questi addebiti la somiglianza e' indicata come *non valutabile* e i candidati si ordinano per data.
  - Se c'e' un solo pagamento con lo stesso importo, viene proposto.

---

## 0.11.2 – 6 novembre 2026 · ricerca libera nel report PayPal

**Novita'**
- **Addebiti PayPal da ricondurre**: selezionando un addebito compare *Non trovi il pagamento? Cerca in tutto il
  report PayPal*.
  - Mostra tutte le uscite del report entro i giorni scelti (da 1 a 120, di partenza 30), di qualunque tipo e
    importo. Comprende anche i pagamenti che il report classifica come pagamenti a persone o con un tipo non
    riconosciuto. Si puo' filtrare per una parte del nome o della descrizione.
  - Per ognuna: importo, differenza rispetto all'addebito, distanza in giorni, somiglianza del nome e, se e' gia'
    abbinata in automatico a un altro addebito, quale.
  - *Riconduci al pagamento selezionato* vale anche con importo diverso, per esempio un pagamento in valuta con
    cambio diverso: conta l'importo del report.
  - Se il pagamento era gia' abbinato a un altro addebito, la scelta fatta a mano prevale e l'altro addebito
    torna da controllare.

---

## 0.11.1 – 6 novembre 2026 · carta di credito contata due volte

**Correzioni**
- **Spese della carta di credito contate due volte**: una volta con le righe dell'estratto della carta, una
  volta con l'addebito mensile sul conto ("Addebito della carta di credito senza dettaglio").
  - Causa principale: alla creazione del conto della carta, durante l'import, il conto di addebito era
    "Non indicato" di partenza. Senza quel collegamento l'addebito non si abbina mai alle righe della carta.
    - Ora l'import propone il conto della stessa banca che ha gli addebiti della carta.
    - Se il collegamento manca e c'e' un solo conto con addebiti della carta di credito (a parita', quello
      della stessa banca), il ricalcolo lo usa da solo.
    - Conti e copertura dice quale conto sta usando, o avvisa se non puo' capirlo. In quel caso anche il
      motivo in Revisione dice di indicare il conto di addebito.
  - Seconda causa: l'abbinamento cercava le righe nei 31 giorni prima dell'addebito. ING addebita il 10 del
    mese le spese del mese prima: si perdevano le spese dei primi giorni del mese e si prendevano quelle dei
    primi giorni del mese dell'addebito, che appartengono al ciclo dopo.
    - Ora, tra le righe della carta non ancora usate nei 75 giorni prima del ciclo, si cercano quelle
      consecutive che sommano esattamente all'addebito.
    - Se non ce ne sono, si prende il periodo che si avvicina di piu' (mese prima del ciclo o dal ciclo
      precedente) e l'addebito va controllato con la differenza.
    - Le righe gia' abbinate a un addebito non si riusano per il successivo.
  - Le righe della carta restano le spese, ognuna classificabile con la sua categoria, nel mese in cui sono
    state fatte; l'addebito sul conto diventa un contenitore che non conta.

**Cosa cambia aggiornando**
- Basta riaprire l'app: il ricalcolo riabbina da solo gli addebiti gia' importati. Conviene comunque indicare
  il conto di addebito della carta in Conti e copertura (colonna *Addebitata su*).

---

## 0.11.0 – 6 novembre 2026 · addebiti PayPal da ricondurre al report

**Novita'**
- **Revisione > Altri movimenti > Addebiti PayPal da ricondurre al report PayPal**: nuova sezione per gli
  addebiti PayPal ("PAYPAL *..." o addebito SEPA di PayPal) senza la ricarica corrispondente nel report entro una
  settimana. Capita quando la banca li registra con qualche giorno di ritardo. Vale per tutti i conti e le carte.
  - Per ogni addebito l'app cerca i pagamenti del report con lo stesso importo, entro 60 giorni, non gia'
    coperti da un altro addebito. Li ordina per somiglianza del nome dell'esercente: per esempio
    "PAYPAL *VEEPEE IT" e "VENTE PRIVEE.COM SA" danno somiglianza media; un esercente diverso con lo stesso
    importo, bassa.
  - La somiglianza ignora il prefisso PayPal, le forme societarie (SA, SRL, Ltd, GmbH...), i domini e le sigle
    di paese. Unisce sequenza comune, coppie di lettere in comune e parole quasi uguali.
  - Proposta: il pagamento piu' simile, se abbastanza simile e staccato dagli altri, oppure l'unico vicino di
    data. Somiglianza mostrata come alta, media o bassa, con la percentuale.
  - Una riga selezionata: scegli il pagamento tra quelli trovati, oppure *Non e' nel report: e' una spesa
    normale* (l'avviso sparisce). Piu' righe selezionate: *Riconduci alle proposte* in un colpo solo.
  - Ricondotto: l'addebito diventa un contenitore e resta la spesa del report, con il nome vero
    dell'esercente. La ricarica di quel pagamento non si usa piu' per altri addebiti.
  - *Addebiti PayPal ricondotti a mano*: elenco delle scelte fatte, per rivederle o toglierle.
  - Questi addebiti non compaiono piu' anche nella tabella generale degli altri movimenti.
- Annullare l'import del report PayPal o di un estratto, o eliminare un conto, toglie anche le riconduzioni che
  riguardano quei movimenti.

**Cosa cambia aggiornando**
- La tabella nuova (`abbinamenti_paypal`) si crea da sola all'apertura del database.

---

## 0.10.1 – 6 novembre 2026 · revisione del codice

**Correzioni**
- **Tabelle con righe selezionabili o modificabili** (Revisione, Movimenti, Portafoglio, Classificazione,
  Regole e tassonomia, Etichette, Conti). Streamlit legava la selezione e le modifiche non salvate alla
  posizione della riga, anche quando le righe cambiavano: cambiando un filtro o una ricerca restavano
  selezionate le stesse posizioni, cioe' righe diverse; se l'elenco si accorciava, la pagina andava in errore.
  Il caso piu' serio era *Esercenti gia' classificati*: modificare una categoria, cambiare filtro e poi salvare
  scriveva la modifica sull'esercente finito in quella posizione. Ora selezione e modifiche si azzerano quando
  cambiano le righe mostrate.
- **Salvataggi di molte righe in un'unica transazione**. Era lo stesso problema di lentezza dei prezzi di
  mercato, con effetti minori. Riguardava la coda e le risposte di Claude e del modello locale, l'applicazione
  delle prove, le conferme multiple in Revisione, le etichette e la natura della spesa su molti movimenti, e il
  salvataggio della natura predefinita e degli esercenti modificati.
  Se un salvataggio si interrompe a meta', non resta scritto niente: prima poteva restare scritta solo una
  parte delle righe.
- **Portafoglio**:
  - Le posizioni di partenza indicate a mano comparivano anche filtrando conti che non hanno quel titolo,
    per esempio solo il conto comune. Ora valgono solo per i conti scelti che lo contengono.
  - Lo scarico automatico dei prezzi si faceva una sola volta per sessione: lasciando l'app aperta, il giorno
    dopo non ripartiva. Ora riparte una volta al giorno.
  - Cambiando le posizioni selezionate, una misura scelta prima e non piu' disponibile (per esempio
    *Rendimento %* su titoli senza prezzi) poteva mandare in errore il grafico.
  - Gli errori di rete verso Yahoo sono spiegati in breve (per esempio "connessione assente o rifiutata",
    "troppe richieste"), senza l'indirizzo tecnico della richiesta.
- **Annullare un import o eliminare un conto** toglie anche le indicazioni *Un mio conto gia' censito* date su
  quei movimenti.
- **Movimenti**: l'elenco mostra al massimo 2.000 righe; quando le supera, la nota lo dice e suggerisce di
  restringere periodo o ricerca. Prima diceva solo "2000 righe".

---

## 0.10.0 – 6 novembre 2026 · nuovo nome e conto censito nei bonifici a tuo nome

**Novita'**
- **Nuovo nome: CashFlow Analyzer** (prima "Spese di famiglia"): titolo della pagina e della scheda del browser,
  intestazione nella barra laterale, icona nell'area di notifica, voce del menu, messaggi di avvio,
  intestazione della tassonomia esportata, README.
  - Restano invariati i nomi tecnici, per non perdere nulla: cartella dei dati (`~/.local/share/spese-app`),
    file del database, voce della chiave di Claude nel portachiavi, cartella del programma e variabili
    `SPESE_*`.
  - La voce del menu ora si chiama `cashflow-analyzer.desktop`: `install.sh` toglie quella vecchia
    (`spese-famiglia.desktop`), `disinstalla.sh` le toglie entrambe.
- **Revisione > Bonifici > Trasferimenti a tuo nome senza contropartita**: nuova risposta *Un mio conto gia'
  censito*, con la scelta del conto (carte di credito escluse).
  - Vale per tutti i movimenti del beneficiario (anche futuri) oppure solo per i movimenti selezionati nel
    dettaglio, che ora ha le righe selezionabili. La scelta sul singolo movimento prevale su quella per
    beneficiario.
  - Il ricalcolo tratta il bonifico come se riportasse l'IBAN di quel conto. Se trova la contropartita su
    quel conto (stesso importo, entro i giorni previsti), abbina i due movimenti. Altrimenti lo tratta come un
    trasferimento verso quel conto: risparmio se e' un deposito o un broker, contributo se e' il conto comune,
    da controllare se il conto e' caricato e copre quella data.
  - Se il gruppo riporta un IBAN, compare il suggerimento di scriverlo nel conto in Conti e copertura, cosi'
    l'app lo riconosce da sola.
  - Nuovo riquadro *Conti indicati a mano per i bonifici*, in fondo alla scheda, per rivedere e togliere le
    indicazioni date. Togliendole, i bonifici tornano da rivedere.
  - Una risposta generica (non caricato, non tracciato, altra persona) sostituisce il conto indicato per quel
    beneficiario, e viceversa. Eliminando un conto spariscono anche le indicazioni che lo riguardano.

**Cosa cambia aggiornando**
- Le tabelle nuove (`destinazioni_conti`, `destinazioni_movimenti`) si creano da sole all'apertura del
  database.
- Per aggiornare la voce del menu con il nuovo nome, rilancia `./install.sh` (veloce: le dipendenze ci sono
  gia'). Senza, il menu mostra ancora "Spese di famiglia", ma l'app funziona lo stesso.

---

## 0.9.1 – 6 novembre 2026 · correzione dello scarico dei prezzi

**Correzioni**
- **Scarico dei prezzi di mercato bloccato** (la barra si fermava a "4 di 16" e l'app non rispondeva). Il
  database lavora in autocommit: i prezzi di un titolo (fino a qualche migliaio di giorni) e i cambi venivano
  salvati una riga alla volta, ognuna con la sua scrittura su disco cifrato, e un solo titolo poteva richiedere
  minuti. Ora per ogni titolo si scarica prima tutto, poi si salva in una sola transazione: sui dati di prova lo
  scarico completo di 16 titoli passa da circa 7.000 scritture separate a 16 transazioni (meno di un secondo,
  rete esclusa).
- I cambi si salvano solo dal primo giorno che serve (la richiesta a Yahoo parte sempre dal 1/1/2000): prima si
  salvavano tutti i giorni dal 2000. Se poi serve un periodo piu' vecchio, si riscarica.
- La barra di avanzamento mostra il titolo in corso (es. "titolo 5 di 16 (VWCE.DE)") e avanza anche per i titoli
  gia' aggiornati, che prima la lasciavano ferma.
- Connessione: 5 secondi al massimo per collegarsi a Yahoo, 20 per ogni attesa di dati.

**Cosa cambia aggiornando**
- Se l'app e' rimasta bloccata, fermala e riavviala (`./avvia.sh --ferma`, poi di nuovo `./avvia.sh`): i prezzi
  gia' salvati restano e lo scarico riprende dai titoli mancanti.

---

## 0.9.0 – 6 novembre 2026 · portafoglio definitivo, prezzi di mercato, sezioni facoltative

**Novita'**
- **Sezioni facoltative** in Impostazioni, al posto di *Funzioni in prova*: tre interruttori separati.
  - *Portafoglio titoli*: pagina Portafoglio (ora definitiva, non piu' prototipo).
  - *Etichette*: pagina Etichette, colonna Etichette e assegnazione in Movimenti.
  - *Natura della spesa*: colonna e assegnazione in Movimenti, grafico in Dove vanno i soldi, scheda in Regole e
    tassonomia. Ora si puo' tenere una senza l'altra (prima erano un solo interruttore).
  - Spegnendo una sezione i dati restano nel database e ricompaiono riaccendendola.
- **Prezzi di mercato da Yahoo Finance** (Impostazioni > Sezioni facoltative, spento di partenza; compare solo con
  il portafoglio acceso).
  - Chiusure giornaliere dei titoli in portafoglio, salvate in euro nel database (tabelle `titoli_mercato`,
    `prezzi_mercato`, `cambi_mercato`); quotazioni in altra valuta convertite con il cambio del giorno, quelle in
    centesimi (GBp) divise per 100.
  - Cosa esce dal PC: i codici ISIN (per trovare il simbolo) e i simboli di borsa. Mai quote, importi, nomi o
    date dei movimenti: il primo scarico parte sempre dal 1/1/2000, i successivi dall'ultimo prezzo salvato.
    Ogni richiesta resta nel registro delle richieste (motore "yahoo").
  - Aggiornamento automatico una volta al giorno aprendo la pagina Portafoglio, piu' *Aggiorna i prezzi ora*.
    Senza rete (o con Yahoo che rifiuta) ci si ferma alla prima richiesta e si avvisa, senza bloccare la pagina.
  - Simbolo cercato dall'ISIN preferendo una borsa in euro (Xetra, Francoforte, Milano, Amsterdam, Parigi...);
    si corregge a mano in *Simboli di borsa e prezzi*, dove si vedono anche borsa, valuta, ultimo prezzo ed errori.
- **Portafoglio con i prezzi**: nelle posizioni aperte prezzo, data del prezzo, valore, plus/minus latente e
  rendimento %; nuovo indicatore *Valore di mercato* con la variazione sul costo (i titoli senza prezzo contano al
  costo). *Capitale investito nel tempo* mostra anche il valore di mercato giorno per giorno.
- **Andamento nel tempo delle posizioni**: grafico a linee sotto la tabella delle posizioni aperte. Si
  selezionano una o piu' righe (fino a 8 insieme); misure *Valore* (con una sola posizione anche il costo,
  tratteggiato), *Rendimento %* e *Capitale investito*. Ogni titolo ha un colore fisso legato alla sua riga, che
  non cambia selezionando altri titoli. Rotellina o pizzico per ingrandire un periodo.

**Cosa cambia aggiornando**
- Le tabelle nuove si creano da sole all'apertura del database.
- Chi aveva spento *Funzioni in prova* trova spente sia Etichette sia Natura della spesa; il portafoglio resta
  come era. I prezzi di mercato sono spenti finche' non li accendi.

---

## 0.8.0 – 6 novembre 2026 · prototipo del portafoglio

**Novita' (prototipo, da confermare)**
- Pagina **Portafoglio** sotto Analisi, accesa di partenza e spegnibile in *Impostazioni > Funzioni in prova*.
  Usa solo i movimenti con ISIN gia' importati e nessun prezzo di mercato.
  - Costo medio ponderato (regime amministrato): commissioni d'acquisto nel costo; plus/minusvalenza = ricavo
    meno commissione di vendita, meno costo medio delle quote vendute. Imposte = quelle trattenute dalla banca.
  - Indicatori del periodo: capitale investito, posizioni aperte, realizzato netto, dividendi, imposte e
    commissioni. Schede: posizioni aperte, vendite, riepilogo per anno, capitale investito mese per mese.
  - Le coppie di migrazione che si annullano (cambio di ISIN interno a Trade Republic) non contano; le quote
    arrivate senza soldi hanno "costo da indicare".
  - Controlli: vendite oltre le quote importate, quote senza costo, dividendi su una quantita' diversa da quella
    tenuta nei 60 giorni prima. Si correggono con una posizione di partenza (data, quote, costo).
- Le altre analisi non cambiano: la vendita resta "Rientro dal risparmio" per intero, plusvalenza compresa.

---

## 0.7.2 – 6 novembre 2026

**Novita'**
- **Da Revisione alla classificazione automatica**: in *Esercenti da classificare* selezioni gli esercenti e
  premi **Classifica con l'AI**; si apre Classificazione automatica limitata a quelli.
  - In alto si sceglie *Solo gli N scelti in Revisione* (predefinito) o *Tutti*; *Togli la selezione* la annulla.
    La selezione resta salvata e si svuota da sola quando gli esercenti scelti sono tutti classificati.
  - Modello locale: classifica solo gli esercenti scelti. Claude: mette in coda, mostra in anteprima e invia
    solo quelli; le altre voci gia' in coda restano li'.
  - Le risposte gia' in attesa per gli esercenti scelti compaiono filtrate e si possono applicare da sole: le
    risposte degli altri esercenti restano in attesa nella stessa prova.

**Correzioni**
- Una prova applicata solo in parte non ripropone le risposte gia' applicate.

---

## 0.7.1 – 6 novembre 2026

**Novita'**
- **Revisione: conferma delle proposte su piu' righe insieme, ognuna con la sua categoria.** Selezionando piu'
  righe compare *Conferma la categoria proposta di N ...*: ogni voce prende la propria proposta, quindi si
  confermano in un colpo voci di categorie diverse senza cambiarle. Le righe senza proposta restano da
  classificare (il pulsante lo dice).
  - *Esercenti da classificare*: conferma la proposta di ogni esercente (vale per tutti i suoi movimenti,
    come una conferma fatta a mano).
  - *Bonifici ai familiari*: conferma la proposta "come il ..." di ogni bonifico (anche le restituzioni).
  - *Altri movimenti*: ora a selezione multipla; conferma la categoria gia' presente o proposta (es. prelievi
    da giustificare, addebiti della carta) e chiude la revisione. Con una riga sola resta la modifica.
- Il pulsante che dava a tutte le righe la stessa categoria resta, con un'etichetta piu' chiara
  (*Applica questa categoria a tutti gli N esercenti selezionati*).
- *Altri movimenti* mostra anche come conta il movimento, la categoria attuale e la proposta.

---

## 0.7.0 – 6 novembre 2026

**Novita'**
- **PayPal** come nuova fonte: report attivita' in CSV (Attivita' > Scarica report), in italiano o in inglese.
  - Le righe della stessa operazione si ricompongono: il pagamento all'esercente tiene il nome vero
    (es. "Patreon Ireland Limited") e la descrizione dell'oggetto.
  - I pagamenti in valuta diventano in euro usando le righe "Conversione di valuta generica", con l'importo
    originale accanto. Un pagamento in valuta senza conversione in euro viene scartato con il motivo.
  - Autorizzazioni e blocchi (impatto "Memo") e operazioni negate o annullate non entrano.
  - Pagamenti da o verso persone valgono come bonifici: i nomi non diventano esercenti e non vanno mai verso
    Claude. Email, indirizzi e telefono restano solo nel livello grezzo del database cifrato.
  - **Spesa contata una volta**: l'addebito della stessa spesa sul conto o sulla carta ("PAYPAL *...", addebito
    SEPA di PayPal) si abbina alla ricarica dello stesso importo nel report entro 7 giorni e diventa un
    contenitore; la ricarica e' un trasferimento interno. Un addebito PayPal senza ricarica, in un periodo coperto
    dal report, finisce tra i movimenti da rivedere.
- **Categoria + sottocategoria come chiave in tutta l'app**: la stessa sottocategoria puo' stare sotto categorie
  diverse (CASA · Varie e ACQUISTI · Varie) senza conflitti. Conferme degli esercenti, regole, decisioni sui
  movimenti, suddivisioni, natura della spesa, prove dei modelli e filtri delle analisi usano la coppia; anche
  i modelli rispondono con la coppia. Rinominare o spostare una voce porta con se' solo i suoi dati.
- **Entrambi i livelli a video**: in Classificazione automatica (risposte da confermare, prove, confronto,
  rimetti in classificazione), in Revisione (colonna Proposta), in Regole e tassonomia e nelle etichette le
  categorie compaiono come "CATEGORIA · Sottocategoria".

**Correzioni**
- Con un perimetro scelto (Personale, Comune, Tutti) un conto appena importato non entrava nelle analisi finche'
  non si cambiava perimetro: ora i conti seguono sempre il perimetro.

**Aggiornando**: all'accesso le voci salvate con la sola sottocategoria si completano con la categoria della
tassonomia in uso; le proposte gia' calcolate si rigenerano al primo ricalcolo. Nella tassonomia e' "predefinita"
solo la voce a cui arrivano le regole del programma: le altre con lo stesso nome si possono rinominare o
eliminare.

---

## 0.6.1 – 6 novembre 2026

**Novita'**
- **Piu' modelli di Claude**: in *Impostazioni > Claude* il campo *Modelli* accetta un elenco separato da punto e
  virgola (predefinito `claude-haiku-5-5; claude-sonnet-5-5; claude-opus-5-5`; si puo' aggiungere ad esempio
  `claude-fable-5-1`). Al salvataggio l'app chiede all'API di Claude se ogni nome esiste e segnala quelli
  sconosciuti (serve la chiave API). Il modello si sceglie a ogni invio e nelle prove di confronto.
- **Piu' modelli locali**: in *Impostazioni > Modello locale* lo stesso elenco per Ollama (es.
  `qwen2.5:3b; llama3.2:3b`). In *Classificazione automatica* si sceglie quale usare.
- **Download dei modelli locali mancanti**: se un modello dell'elenco non e' installato, l'app chiede se
  scaricarlo e installarlo, con barra di avanzamento (dalla rete esce solo il nome del modello).

**Sicurezza**
- I modelli *cloud* di Ollama (nome che finisce con `cloud`) sono rifiutati come modello locale: girano su
  server esterni e farebbero uscire i nomi degli esercenti senza anteprima. Non contano tra gli installati e non
  vengono usati nemmeno se scritti direttamente nel database.

**Aggiornando**: l'elenco dei modelli di Claude parte dai tre predefiniti piu' quello usato finora; quello locale
dal modello configurato finora.

---

## 0.6.0 – 6 novembre 2026

**Novita'**
- **Risposte da confermare**: le classificazioni del modello locale e di Claude non si applicano piu' da sole.
  Ogni giro crea una *prova* che compare in cima a *Classificazione automatica* con categoria proposta,
  confidenza e nome riconosciuto. Si toglie la spunta alle risposte non convincenti e si applicano le altre;
  *Non applicare* non cambia nulla; per Claude *Non applicare e rimetti in coda* rimanda le stesse voci in coda
  per provarle con un altro modello.
- **Scelta del modello di Claude** accanto al pulsante di invio (Haiku, Sonnet, Opus).
- **Prova un modello su esercenti gia' classificati** (Strumenti): manda a un modello esercenti che hanno gia'
  una categoria (confermata da te, da una tua regola, dalle regole, dal codice MCC) senza cambiarla, per
  misurarne l'accuratezza. Stessi controlli di sicurezza della coda.
- **Confronto tra prove** (Strumenti): per ogni prova risposte, confidenza media, risposte sicure, coincidenze
  con la categoria attuale e con le tue scelte; sotto, esercente per esercente, risposta e confidenza di ogni
  modello, con filtro sulle risposte discordanti. Le prove si possono eliminare.
- **Revisione > Esercenti da classificare**: la proposta e' divisa in colonne *Proposta*, *Fonte* (Claude,
  modello locale, storico, somiglianza, marchio, codice MCC), *Modello* e *Confidenza*, ordinabili cliccando
  l'intestazione. Il *Totale* e' numerico (prima era testo e si ordinava in ordine alfabetico).
- La memoria degli esercenti registra anche il modello che ha dato la categoria; il registro delle richieste
  indica la prova.

**Correzioni**
- La categoria proposta "Assicurazioni (altre)" non veniva preselezionata (parentesi nel nome).
- Un esercente dal nome neutro gia' messo in una categoria della salute (da regola, marchio o scelta tua) resta
  solo locale anche nelle prove di confronto.

**Aggiornando**: i dati restano; le categorie date prima dai modelli non sono legate a una prova, quindi il
confronto parte dalle prove fatte da questa versione.

---

## 0.5.2 – 6 novembre 2026 · revisione completa del codice

**Privacy**
- L'area *solo locale* (SALUTE) della tassonomia non era applicata: bloccava solo il filtro per parole chiave.
  Ora resta sul PC anche un esercente dal nome neutro che l'app ha gia' collocato, anche solo come proposta,
  nella salute (modello locale, marchio, codice MCC). Il blocco segue la categoria se viene rinominata.
  Aggiunte parole chiave (dott, osteopata, pediatra, oculista, ...).

**Correzioni**
- **Cambio passphrase**: le altre schede aperte restavano con la chiave vecchia e andavano in errore; ricaricando
  si chiudeva la sessione per tutte. Ora si riaprono da sole. Il messaggio ricorda che i backup precedenti
  restano con la passphrase vecchia.
- **Bonifico dal conto comune a un conto personale** (abbinato): era contato al contrario (spesa per il comune,
  entrata per il personale). Ora e' una restituzione: contributo ricevuto negativo per il comune, rimborso del
  versato per il personale.
- **Riconoscimento di titolari e familiari**: bastava una parola del nome (un bonifico da un qualsiasi "Mario"
  diventava contributo). Ora servono due parole dello stesso nome, in qualunque ordine, o il nome abbreviato
  ("ROSSI M."). La protezione dei nomi verso l'esterno resta parola per parola.
- **Vista Tutti / perimetro composto**: un versamento sul comune con contropartita solo proposta spariva dai
  totali mentre l'altro lato restava. Ora si annullano solo i contributi confermati.
- **Due carte sullo stesso conto**: il secondo addebito dello stesso giorno restava senza dettaglio e finiva in
  "Varie" (spese contate due volte). Ora ogni addebito va alla carta le cui righe tornano.
- **Conti e copertura**: due conti con lo stesso nome davano un salvataggio a meta' e un errore; ora il doppione
  e' segnalato prima e il salvataggio e' tutto o niente. Controllata la data di chiusura.
- **Movimenti**: "Vale per tutti i movimenti di questo esercente" non si applicava al movimento stesso se aveva
  una scelta precedente.
- **Excel**: importi calcolati con formule (12,300000000000001) facevano scartare la riga; ora valgono 12,30.
- **avvia.sh / disinstalla.sh**: dopo un riavvio del PC il numero di processo salvato poteva appartenere a un
  altro programma (server non avviato, oppure un processo qualsiasi fermato). Ora si controlla che sia l'app.
  `avvia.sh` funziona anche dopo aver spostato la cartella del programma.
- Pulizia del codice (import e variabili inutilizzati).

**Aggiornando**: se ci sono restituzioni dal comune, calano uscite del comune ed entrate personali; versamenti
di familiari con il solo nome di battesimo possono comparire in *Revisione > Bonifici con altre persone*.

---

## 0.5.1 – 5 novembre 2026 · nuovo aspetto

- Stile "chiaro a schede" (proposta A): tema dell'app, carattere Inter incluso nel pacchetto (nessun caricamento
  da internet), contenuti in schede, intestazione di pagina con conti e periodo analizzati.
- Barra laterale con marchio, filtri in alto, richiamo dei movimenti da rivedere e menu raggruppato
  (Analisi, Dati, Classificazione, Configurazione); schermata di accesso a scheda.
- Grafici allineati allo stile (colori, etichette in euro, barre con estremita' arrotondate).

---

## 0.5.0 – 5 novembre 2026 · analisi interattive

- **Perimetro composto**: oltre a Personale / Comune / Tutti si scelgono i conti da sommare (es. Credit Agricole
  + Trade Republic). I pulsanti selezionano i conti del perimetro; i versamenti tra conti scelti si annullano.
- **Grafici ECharts** al posto di Plotly, con animazioni di apertura e di aggiornamento.
- **Andamento mensile**: passando sui punti compare l'anteprima di "Dove vanno i soldi" del mese; con un clic si
  apre il dettaglio del mese (il periodo della barra laterale non cambia).
- **Dove vanno i soldi** e **Approfondimento categoria** unite: si sceglie la categoria cliccando il grafico;
  sottocategorie a sinistra (30%), spesa per mese con media mobile a 3 mesi a destra (70%), tabelle
  comprimibili sotto. Il filtro sul mese vale solo in questa sezione; serie e indicatori restano sull'intero
  periodo e seguono categoria e sottocategoria.

**Aggiornando**: rilanciare `./install.sh` (nuova dipendenza `streamlit-echarts` al posto di `plotly`).

---

## 0.4.7 – 5 novembre 2026

- Nuovo formato: **Credit Agricole** (CSV), con le causali della banca (pagamento POS, utenze, giroconto/bonifico,
  commissioni, interessi, prelievi, imposte, storni).
- Gli importi scritti come testo con l'apostrofo davanti (`'-4,99`) sono letti correttamente.

---

## 0.4.6 – 5 novembre 2026

- **Sessione che resta aperta**: ricaricare la pagina o aprire un'altra scheda non chiede la passphrase. Il
  browser tiene un cookie con un codice casuale, il server ricorda la passphrase solo in memoria. La sessione si
  chiude con *Esci*, dopo 7 giorni senza azioni o al riavvio del server.

---

## 0.4.5 – 5 novembre 2026

- **Revisione > Esercenti da classificare**: ricerca per nome (anche come appare in Movimenti), filtro per file
  importato, selezione di piu' esercenti insieme.
- **Movimenti**: da un pagamento con carta o un addebito SEPA si puo' classificare l'esercente per tutti i suoi
  movimenti.
- Le voci a cui Claude ha risposto con confidenza bassa non spariscono: si possono rimettere in coda.

---

## 0.4.4 – 5 novembre 2026

- Integrata come predefinita la **tassonomia rivista dall'utente** (84 sottocategorie, 16 in piu'), con la natura
  della spesa di partenza per le nuove sottocategorie.

---

## 0.4.3 – 5 novembre 2026

- Impaginazione compatta per schermi 1920×1080: meno spazi vuoti in alto e nel menu laterale.

---

## 0.4.2 – 5 novembre 2026

- Un conto registrato a mano in *Conti e copertura* viene proposto in *Importa* (per IBAN, banca o nome) e al
  primo file diventa un conto importato; prima, ricrearlo con lo stesso nome dava errore.
- **Elimina un conto**: toglie il conto con movimenti, file importati e decisioni su quei movimenti, con conferma
  se ci sono movimenti. Esercenti confermati e regole restano.

---

## 0.4.1 – 5 novembre 2026

- **Classificazione automatica razionalizzata**: prima si sceglie con cosa iniziare (modello locale o
  direttamente Claude); la pagina dice quante voci entrerebbero in coda e quante sono bloccate dal controllo di
  sicurezza; in coda le voci sono gia' spuntate, il testo si puo' accorciare, si vede l'anteprima esatta e si
  invia con un solo pulsante. La coda si pulisce da sola. Tolto il dettaglio del paese dall'interfaccia.
- **Da rivedere**: una tua decisione sul movimento chiude sempre la revisione; in *Movimenti* la colonna *Motivo*
  spiega perche' un movimento e' da rivedere.
- **Filtri in cima alla barra laterale** (perimetro, periodo, conti); la scelta resta passando da una pagina
  all'altra.

---

## 0.4.0 – 5 novembre 2026 · funzioni in prova

- **Natura della spesa** (necessaria, svago, superflua): un valore per sottocategoria, modificabile sul singolo
  movimento, con analisi dedicata.
- **Etichette** libere sui movimenti (es. un viaggio), con pagina di analisi per etichetta.
- Le due funzioni si attivano e disattivano da *Impostazioni > Funzioni in prova* senza perdere dati.

---

## 0.3.3 e precedenti – fino al 5 novembre 2026

Il dettaglio delle versioni precedenti non e' piu' ricostruibile. Lo stato raggiunto con la 0.3.3:
- Import di Credem, ING (conto in Excel e CSV, carta di credito) e Trade Republic, con deduplica, livello grezzo
  conservato nel database e quadratura dei saldi; annullamento di un import.
- Database cifrato (SQLCipher) con passphrase, backup cifrati, export CSV del livello di analisi.
- Classificazione a catena: decisioni e regole tue, tipo di movimento, regole sugli esercenti, codici MCC,
  marchi noti, memoria degli esercenti (modello locale, Claude, conferme), storico e somiglianza.
- Abbinamento dei trasferimenti tra conti, contributi al conto comune, spese tramite familiari, suddivisione di
  un bonifico in piu' parti; carte di credito dettagliate per ciclo di addebito.
- Perimetri personale, comune e cumulativo; andamento mensile, ripartizione, approfondimento per categoria.
- Modello locale (Ollama) e Claude con controllo di sicurezza, anteprima e approvazione; chiave API nel
  portachiavi; nomi di titolari e familiari tolti dalle descrizioni.
- Tassonomia modificabile, regole tue, IBAN normalizzati e verificati, icona nell'area di notifica, script di
  installazione, avvio e disinstallazione per Linux Mint.
