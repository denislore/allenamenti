# Dashboard allenamenti – Garmin → telefono

Come funziona: ogni 2 ore GitHub esegue `sync.py`. Lo script scarica le attività dalla **Garmin Connect Personal API** ufficiale, le cifra con la tua password e pubblica il file `docs/data.enc`. Il telefono apre la dashboard (`docs/index.html`), che decifra i dati in locale. Il servizio è gratuito e non richiede server da gestire.

```
Orologio → Garmin Connect → (ogni 2h) GitHub Actions: sync.py → docs/data.enc (cifrato) → GitHub Pages → telefono
```

Famiglie di sport riconosciute dal `typeKey` Garmin:

| Famiglia | Tipi Garmin inclusi |
|---|---|
| Corsa | tutti i tipi con "run" (running, trail_running, treadmill_running…) |
| Scialpinismo | backcountry / touring / skimo |
| Sci | gli altri tipi con "ski" o "snowboard" (pista, fondo) |
| Trekking | hiking, mountaineering |
| Altro | tutto il resto (camminate, bici, palestra…): compare in "Tutto" |

---

## 1. Crea la chiave Garmin (5 minuti)

1. Accedi a Garmin Connect **da computer** e apri: <https://connect.garmin.com/app/settings/personalApiToken>
2. Clicca **Create Key**, accetta i termini e scegli una scadenza. Le opzioni sono 30, 90 o 180 giorni oppure 1 anno: conviene **1 anno**.
3. **Copia subito la chiave**, perché Garmin la mostra una sola volta. Conservala come una password.

## 2. Crea il repository su GitHub

1. Crea un account su <https://github.com> se non lo hai.
2. Crea un nuovo repository **New repository**, chiamato ad esempio `allenamenti`, con visibilità **Public**.
   GitHub Pages è gratuito solo sui repository pubblici. Il codice sarà visibile a chiunque, ma i tuoi dati restano cifrati con AES-256 e senza password sono illeggibili.
3. Nel repository clicca **Add file → Upload files** e trascina **tutto il contenuto** della cartella `garmin-dashboard`, compresa la cartella nascosta `.github`.
   Su Mac il Finder nasconde le cartelle che iniziano con il punto: premi `Cmd + Shift + .` per mostrarle.
   Controlla che dopo il caricamento esista il file `.github/workflows/aggiorna.yml`.

## 3. Inserisci i due "secrets"

Vai su **Settings → Secrets and variables → Actions → New repository secret** e crea:

| Nome | Valore |
|---|---|
| `GARMIN_PERSONAL_API_KEY` | la chiave Garmin del punto 1 |
| `DASHBOARD_PASSWORD` | una password lunga a tua scelta (almeno 16 caratteri, ad esempio una frase) |

## 4. Primo scaricamento completo

1. Apri la scheda **Actions** e, se richiesto, abilita i workflow.
2. Seleziona **Aggiorna dashboard → Run workflow**, spunta **Riscarica tutto lo storico** e conferma.
3. Dopo 1–2 minuti l'esecuzione deve risultare verde ✅ e nel repository compare `docs/data.enc`.
   Se è rossa ❌, aprila e leggi l'errore. Il caso più frequente è una chiave copiata male. I log non contengono mai i tuoi dati, solo dei conteggi.

## 5. Attiva la pagina web

1. Vai su **Settings → Pages**.
2. In **Source** seleziona **Deploy from a branch**, poi branch `main` e cartella `/docs`. Clicca **Save**.
3. Dopo 1–2 minuti la dashboard è disponibile su `https://<tuo-utente>.github.io/allenamenti/`.

## 6. Installala sul telefono

- **iPhone (Safari):** apri l'indirizzo, tocca **Condividi → Aggiungi alla schermata Home**.
- **Android (Chrome):** apri l'indirizzo, poi **⋮ → Installa app** (oppure "Aggiungi a schermata Home").

Al primo avvio inserisci la `DASHBOARD_PASSWORD` e lascia spuntato "Ricorda su questo dispositivo".

---

## Cosa mostra

- **Filtri:** sport (Tutto, Corsa, Scialpinismo, Sci, Trekking) e periodo (7 giorni, 30 giorni, Anno, Stagione invernale dal 1° novembre, 12 mesi).
- **KPI** confrontati con lo stesso periodo dell'anno precedente:
  - Corsa: distanza, uscite, tempo, D+, passo medio, FC media
  - Scialpinismo: D+, uscite, tempo, distanza, D+ per ora, quota massima
  - Sci: D−, giorni, distanza, velocità massima
  - Trekking: distanza, D+, tempo, uscite, quota massima, FC media
- **Volume per settimana:** ultime 16 settimane. Nella vista "Tutto" sono ore impilate per sport.
- **Mese per mese:** anno in corso contro anno precedente. Per sci e scialpinismo il confronto è tra stagioni, da novembre a ottobre.
- **Andamento:** passo per uscita (corsa) e D+ per ora (scialpinismo), con media mobile sulle ultime 5 uscite. Nella vista "Tutto" compare la HRV notturna, se disponibile.
- **Record** su tutto lo storico e **ultime 20 attività**.

## Percorsi ricorrenti e mappe (versione 2)

- `sync.py` scarica il file originale `.FIT` di ogni attività (al massimo 40 per esecuzione), ne estrae la traccia GPS semplificata e la salva cifrata in `docs/tracce.enc`.
- `percorsi.py` riconosce le uscite fatte sullo stesso tracciato: stessa famiglia di sport, partenza e arrivo entro 300 m, distanza entro ±12 %, tracciato medio entro 150 m. Il verso di percorrenza conta.
- **Nome manuale:** in Garmin Connect rinomina un'uscita iniziando con `#` (es. `#Giro del Zugna`). Le uscite con lo stesso nome vanno nello stesso percorso; quelle riconosciute in automatico si aggiungono. `#escludi` toglie un'uscita da ogni percorso.
- Nella scheda **Percorsi** dell'app: mappa, tempo in movimento per ripetizione, FC media (corsa e trekking) o D+ per ora (scialpinismo), elenco ripetizioni e distacco dalla migliore lungo il percorso.
- Lo sci in pista non forma percorsi; le attività indoor non hanno traccia.

## Scheda Sonno (versione 3)

- `sonno.py` legge i riepiloghi del sonno (endpoint `dailySleepSummariesByDate`): durata, fasi (profondo, leggero, REM, sveglio), orari, punteggio.
- Nell'app: ultima notte, medie degli ultimi 7 e 30 giorni (durata, punteggio, orario a letto e sveglia, notti sotto le 7 ore), grafici a 30 giorni di durata per fasi, punteggio e HRV.
- Lo schema della risposta Garmin non è documentato pubblicamente: se i campi non vengono riconosciuti, la scheda mostra una "Diagnostica" con i soli nomi dei campi (nessun valore), da cui correggere il parser.

## Modifica dei percorsi dall'app (versione 5)

- Nella scheda **Percorsi** → "Collega GitHub" incolli un token *fine-grained* limitato al repository (permesso **Contents: Read and write**). Il token resta solo sul dispositivo.
- Da un percorso: **Rinomina**, **Unisci con…**, **Aggiungi uscita**, **togli** (sulla singola ripetizione). Da un'attività: **Aggiungi a un percorso…**, **Crea nuovo percorso**, **Togli da questo percorso**.
- **+ Nuovo** crea un percorso scegliendo le uscite a mano; da quel momento le nuove uscite con lo stesso tracciato di una di quelle scelte vengono abbinate lì in automatico (le uscite passate restano dove sono).
- Le scelte sono salvate cifrate in `docs/abbinamenti.enc`, hanno effetto subito nell'app e `sync.py` le applica a ogni aggiornamento (precedenza sul `#nome`).

## Classificazione delle corse e aggiornamento immediato (versione 8)

- Per la **corsa** contano solo i percorsi tuoi; quelli riconosciuti in automatico compaiono in "Riconosciuti in automatico" con "Rendi percorso".
- Ogni nuova corsa con tracciato uguale a uno dei tuoi percorsi viene abbinata da sola; quelle incerte degli ultimi 14 giorni compaiono nel riquadro blu "uscite da classificare" con il percorso suggerito (Conferma / Scegli / Nuovo / Nessuno).
- Il pulsante **⇣ Garmin** avvia subito l'aggiornamento (serve il permesso *Actions: Read and write* sul token) e ricarica i dati appena pronti.
- Nel grafico "Andamento" puoi filtrare un solo percorso con i pulsanti sopra il grafico.

## Note tecniche e limiti

- **Campi delle attività:** la documentazione pubblica di Garmin elenca gli endpoint ma non lo schema delle risposte. I campi usati in `sync.py` (`distance`, `duration`, `movingDuration`, `elevationGain`, `elevationLoss`, `maxElevation`, `averageHR`, `maxSpeed`, …) sono quelli del formato attività di Garmin Connect. Vanno quindi verificati al primo giro: se un valore risulta vuoto o sbagliato, esegui il controllo descritto sotto.
- **HRV:** è opzionale. Se l'endpoint non risponde o ha un formato diverso, la sezione viene saltata senza bloccare il resto.
- **Controllo dei campi (facoltativo, solo dal PC con Python 3.12 o superiore):**
  ```
  pip install -r requirements.txt
  export GARMIN_PERSONAL_API_KEY="la-tua-chiave"      # su Windows: set GARMIN_PERSONAL_API_KEY=...
  python sync.py --esplora
  ```
  Il comando crea la cartella `esplora/` con le risposte grezze dell'API. Quella cartella non va mai caricata su GitHub ed è già esclusa da `.gitignore`.
- **Scadenza della chiave:** Garmin invia un'email circa una settimana prima. Per rinnovarla, crea una nuova chiave e aggiorna il secret `GARMIN_PERSONAL_API_KEY`.
- **Errori:** se il workflow fallisce, GitHub ti avvisa via email.
- **Workflow sospeso:** sui repository pubblici GitHub sospende i workflow programmati dopo 60 giorni senza attività nel repository. Ogni nuovo allenamento genera un aggiornamento, quindi succede solo in caso di lunghe pause. In quel caso basta riattivarlo dalla scheda Actions.
- **Cambio password:** aggiorna il secret `DASHBOARD_PASSWORD` e lancia il workflow con "Riscarica tutto lo storico".
- **Aggiornamento manuale:** Actions → Run workflow. Sulla dashboard il tasto ↻ ricarica i dati.
- Strava non viene usato. Garmin è la fonte originale dei dati e la sua Personal API non richiede abbonamenti.

## Forma, recupero, meteo, notifiche e uso senza rete (versione 9)

- **Avvisi in alto:** `sync.py` scrive a ogni giro `docs/stato.json` (in chiaro: solo orario del controllo ed esito, mai dati personali). L'app avvisa se Garmin rifiuta la chiave, se Garmin non risponde da più di 6 ore, se l'aggiornamento non gira da più di 6 ore e 14 giorni prima della scadenza della chiave (data da inserire in **Impostazioni**).
- **Forma fisica** (Panoramica): VO2max con andamento a 12 mesi e **rapporto di carico** (carico degli ultimi 7 giorni diviso la media settimanale delle ultime 4; fascia ideale 0,8–1,4), con il carico per settimana diviso per sport.
- **Efficienza:** metri percorsi ogni 100 battiti (corsa, trekking) o metri di dislivello ogni 100 battiti in salita (scialpinismo). Sostituisce il grafico della sola FC media nei percorsi.
- **VAM in salita** (scialpinismo): metri di dislivello all'ora calcolati solo nei tratti in salita, senza soste e discese.
- **Analisi della traccia** (`analisi.py`, sul file `.FIT` completo): parziali al km con passo, **passo in piano** (GAP, modello di Minetti), FC e dislivello; migliori tempi su 1, 5, 10 km e mezza maratona dentro ogni corsa. Le uscite già scaricate vengono rianalizzate a poco a poco (40 per giro): lo storico si completa in qualche giro.
- **Recupero** (scheda Sonno): FC a riposo, Body Battery, stress medio e passi. Se Garmin risponde in un formato non riconosciuto, compare una diagnostica con i soli nomi dei campi.
- **Meteo di ogni uscita** da [Open-Meteo](https://open-meteo.com) (gratuito, dati CC BY 4.0): vengono inviate solo coordinate arrotondate (circa 100 m) e data dell'uscita.
- **Notifiche** (Android con Chrome; iPhone con iOS 16.4 o successivo, app aperta dall'icona sulla schermata Home): nuova corsa da classificare, nuovo record, aggiornamento fermo. Si attivano da **Impostazioni**: la chiave di invio viene generata sul telefono e salvata, cifrata con la password, in `docs/notifiche.enc`. Nessun secret in più su GitHub. Gli avvisi di record partono solo quando l'analisi dello storico è completa.
- **Senza rete** l'app si apre con l'ultima copia dei dati salvata sul telefono (`docs/sw.js`, service worker). Con la rete, ogni nuova versione dell'app arriva subito, senza la cache di 10 minuti di GitHub Pages.
- **Come un'app:** installata dal browser (Android: Chrome → ⋮ → Installa app; iPhone: Condividi → Aggiungi alla schermata Home) si apre a tutto schermo, con icona adattiva su Android e scorciatoie (tocco lungo sull'icona: Percorsi, Sonno, Da classificare). All'avvio mostra subito l'ultima copia dei dati salvata sul telefono e la aggiorna in sottofondo.
- **Storici lunghi:** con più di 200 uscite ancora da completare, ogni giro scarica fino a 150 tracce e il meteo di 250 uscite (circa 2.000 uscite in un giorno di giri automatici). L'elenco delle attività e i grafici sono completi già dal primo scaricamento.
