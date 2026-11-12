# Guida alla diagnostica

La diagnostica serve a trovare **errori nei conteggi che compaiono solo con dati reali**. I test automatici
del programma lavorano su estratti inventati: qui gli stessi principi vengono verificati sul tuo database, e il
risultato si puo' riassumere in un report anonimizzato da inviare a chi sviluppa l'app.

## In breve

1. In **Impostazioni > Modalita' di debug** accendi *Diagnostica dei conteggi*: nel menu, sotto Configurazione,
   compare la pagina **Diagnostica**.
2. Premi **Esegui i controlli**.
3. Guarda i casi trovati (con i tuoi dati veri, che restano sul PC) e seleziona quelli da approfondire.
4. Premi **Prepara il report**, leggilo nell'anteprima e scaricalo.
5. Invia il solo file `diagnostica-report.md`. Il file della **transcodifica** resta a te.

La diagnostica **non usa la rete**: scrive due file che scarichi dal browser. Non cambia movimenti, categorie o
decisioni; rifa' soltanto il ricalcolo, come a ogni salvataggio.

## Cosa controlla

Ogni controllo ha una gravita':

- **errore**: una regola del programma non e' rispettata. E' un difetto da segnalare.
- **avviso**: un probabile errore nei conti, da guardare caso per caso. Puo' essere anche una situazione corretta.
- **info**: una situazione nota, utile per capire i numeri.

| Controllo | Gravita' | Cosa verifica |
|---|---|---|
| `COMPONENTI` | errore | Ogni movimento conta per il suo importo: righe principali (o parti), commissione e imposta tornano con il movimento |
| `TRASF_ASIMMETRICO` | errore | Due movimenti abbinati come trasferimento si indicano a vicenda e hanno importi opposti |
| `TRASF_CONTATO` | errore | Un trasferimento abbinato non conta da un lato solo: con i due conti insieme si annulla |
| `NATURA_CATEGORIA` | errore | Uscite con categorie di uscita, entrate con categorie di entrata, risparmio con categorie di risparmio |
| `TOTALI_PAGINE` | errore | Mese per mese, le uscite di *Andamento mensile* sono la somma delle categorie di *Dove vanno i soldi* |
| `RICALCOLO_INSTABILE` | errore | Due ricalcoli di fila danno lo stesso risultato |
| `CARTA_NON_ABBINATA` | avviso | Un addebito mensile della carta conta per intero anche se le righe della carta sono importate (spese doppie) |
| `DOPPIONE_TRA_CONTI` | avviso | Due uscite dello stesso importo su conti diversi a pochi giorni di distanza, con lo stesso esercente o un importo non tondo |
| `DOPPIONE_NEL_CONTO` | avviso | Movimenti identici nello stesso conto arrivati da file diversi |
| `STORNO_COME_ENTRATA` | avviso | Un accredito che pareggia un addebito precedente, o che la banca chiama storno o rimborso, conta come entrata |
| `SUDDIVISIONE_IGNORATA` | avviso | Le parti di una suddivisione non sommano al movimento: la suddivisione non viene applicata |
| `QUADRATURA` | avviso | Un file importato non quadra con i saldi che dichiara |
| `CARTA_DIFFERENZA` | info | Le righe della carta spiegano solo in parte l'addebito: il resto conta come *Varie* |
| `CARTA_ALTROVE` | info | Le righe della carta sono in un conto non di tipo Carta, o in una carta senza conto di addebito |
| `TIPO_NON_RICONOSCIUTO` | info | Diciture della banca che il formato non sa classificare (tipo "altro") |

Limite da conoscere: i controlli trovano le **incoerenze nei conti**, non gli errori di significato. Una spesa
nella categoria sbagliata rispetta tutte le regole: per quelle servono Revisione e Movimenti.

## Cosa contiene il report

### 1. Riepilogo: sempre presente, solo conteggi

- quanti casi per ogni controllo;
- quanti conti e movimenti per tipo e formato della banca;
- quante righe di analisi per natura e per fonte della categoria;
- quanti trasferimenti e addebiti della carta in ogni stato;
- le diciture della banca non riconosciute, ridotte alle parole della banca.

Nessun importo, nessuna data, nessun nome.

### 2. Casi scelti: solo quelli che selezioni

Per ogni caso il report elenca i movimenti coinvolti e come contano. Ecco che cosa esce e che cosa no:

| Dato | Nel report |
|---|---|
| Importi, commissioni, imposte | **Veri**: senza, un errore al centesimo non si puo' riprodurre |
| Date | Spostate indietro di un numero fisso di anni; giorno e mese restano |
| Esercente | Etichetta: `ESERCENTE 12` |
| Controparte di un bonifico | Etichetta: `SOGGETTO 3` |
| IBAN | `IBAN 2`, oppure `di CONTO 1` se e' un tuo conto |
| Nome del conto | `CONTO 1`, con tipo, perimetro e formato della banca |
| Identificativo del movimento | `M 7` |
| Descrizione della banca | Solo le parole della banca (bonifico, addebito, storno...); cifre come `#`, tutto il resto `···` |
| Titolari e familiari | `[TITOLARE]` |
| Categoria | Il nome, tranne quelle dell'area solo locale (salute) e quelle segnate *Non inviare a Claude*: `(categoria riservata)` |
| Tipo di movimento, codice della banca, natura, fonte, stato degli abbinamenti | Come sono |

Un caso puo' portare con se' movimenti di contesto: per un addebito della carta, le righe della carta nella
finestra dei 75 giorni (al massimo 40); per un trasferimento, la contropartita.

**Leggi sempre l'anteprima prima di inviare.** L'anonimizzazione e' prudente (cio' che non e' riconosciuto come
parola della banca viene nascosto), ma il controllo finale e' il tuo.

## La transcodifica

Il file `diagnostica-transcodifica-NON-INVIARE.csv` (si apre con Excel o LibreOffice) riporta, per ogni
etichetta usata nei report, il valore vero: nome del conto, esercente, controparte, IBAN e, per ogni movimento,
data vera, conto, importo e descrizione. In testa c'e' di quanti anni sono spostate le date.

Serve a te: quando una risposta parla di `M 7` o di `ESERCENTE 12`, li' trovi di che cosa si tratta.

- Le etichette sono **stabili**: lo stesso esercente ha la stessa etichetta in tutti i report. La
  corrispondenza e' salvata nel database cifrato.
- Il file contiene dati personali in chiaro: tienilo sul PC e cancellalo quando non serve piu'. Si puo'
  riscaricare in ogni momento preparando di nuovo un report.

## Domande frequenti

**Spegnendo la modalita' di debug perdo qualcosa?** No. Sparisce la pagina; dati, decisioni e corrispondenza delle
etichette restano.

**I controlli rallentano l'app?** Girano solo quando premi *Esegui i controlli*. Con molte migliaia di movimenti
possono richiedere qualche secondo.

**Ho molti avvisi: e' grave?** Non necessariamente. Gli avvisi sono indizi: due spese uguali su due conti possono
essere davvero due spese. Gli *errori*, invece, non dovrebbero mai comparire: se ne trovi, invia il report.

**Posso inviare il report a Claude dall'app?** No, di proposito: il report non passa dal canale usato per
classificare gli esercenti e non parte da solo. Lo scarichi e decidi tu a chi darlo.
