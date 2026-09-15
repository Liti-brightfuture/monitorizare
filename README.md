# Demo validare WiFi CSI — detecție prezență și mișcare

Spike de validare pentru sistemul descris în [plan-business.md](plan-business.md).
**Nu e produsul.** E cel mai mic sistem care răspunde cu date, nu cu impresii,
la o singură întrebare:

> Se poate detecta fiabil, cu 2× ESP32 și fără cameră video, dacă cineva e în
> cameră și se mișcă?

Livrabilul care contează nu e dashboard-ul, ci tabelul de PASS/FAIL scos de
`analyze.py`. Repo-ul amplu (monorepo, Supabase, multi-cameră) se construiește
**după** ce răspunsul e da.

---

## Ce vede și ce NU vede sistemul

Metricile folosite — distanța de formă CSI și deviația RSSI — detectează
**mișcare**, nu **prezență**. O persoană complet nemișcată (doarme) și o cameră goală produc
același semnal plat. Nu e un defect de implementare, e limita fizică a metricii.

Practic nu blochează cazul de business: alerta care se vinde e *„nicio mișcare
de X minute"*, și aceea funcționează. Dar dashboard-ul nu afișează
„ocupat / gol" — afișează stări bazate pe mișcare, iar textele din interfață
sunt formulate exact așa.

[breathing.py](breathing.py) caută periodicitatea respirației în loc de mișcare
și acoperă parțial golul — vezi secțiunea de la finalul documentului. Cu ambele
semnale combinate, cea mai lungă pauză fără niciun semn de viață peste o noapte
reală scade de la 68.5 la **56 de minute**. E un progres, dar încă departe de
pragul de 30 de minute care ar face produsul convingător.

---

## Instalare

### 1. Python

```powershell
pip install -r requirements.txt
```

### 2. ESP-IDF (o singură dată)

Descarcă **ESP-IDF v5.x** de la
<https://docs.espressif.com/projects/esp-idf/en/stable/esp32/get-started/windows-setup.html>
și rulează installer-ul oficial pentru Windows. Verifică într-un
*ESP-IDF PowerShell* nou:

```powershell
idf.py --version
```

### 3. Firmware pe cele două plăci

Repo-ul `espressif/esp-csi` e deja clonat aici. O placă emite, cealaltă
recepționează și scoate CSI pe serial. Din *ESP-IDF PowerShell*:

```powershell
# Placa A - emitator
cd esp-csi\examples\get-started\csi_send
idf.py set-target esp32
idf.py -p COM4 flash

# Placa B - receptor
cd ..\csi_recv
idf.py set-target esp32
idf.py -p COM5 flash monitor
```

Pe placa B trebuie să curgă continuu linii care încep cu `CSI_DATA,...`.
**Închide `idf.py monitor` înainte de a rula scripturile Python** — portul
serial nu poate fi deschis de două programe simultan.

Recomandări din documentația esp-csi, care contează mult pentru calitatea
semnalului:
- distanță de **peste 1 metru** între plăci;
- antenă externă dacă ai (antena PCB are directivitate slabă);
- canal WiFi cât mai puțin aglomerat.

---

## Fluxul de lucru

### Pasul 1 — înregistrează sesiuni etichetate

Fără date etichetate, „merge" e doar o impresie. Patru sesiuni:

```powershell
python record.py --port COM5 --label gol            --seconds 1800
python record.py --port COM5 --label nemiscat       --seconds 300
python record.py --port COM5 --label miscare        --seconds 300
python record.py --port COM5 --label intrari_iesiri --seconds 300 --cues 30
```

| Etichetă | Ce faci |
|---|---|
| `gol` | Ieși din cameră, închide ușa, lasă-l 30 de minute |
| `nemiscat` | Stai așezat sau întins, cât mai nemișcat |
| `miscare` | Te miști normal prin cameră |
| `intrari_iesiri` | Urmezi comenzile sonore: la fiecare 30s auzi un beep și scrie pe ecran `IEȘI` / `INTRĂ` |

`--cues` există fiindcă nu ai cum să apeși o tastă în timp ce ieși din cameră.
Scriptul îți dă comanda și salvează momentul exact într-un `.marks.csv` — ăsta
e adevărul de referință pentru măsurarea latenței.

### Pasul 2 — verdictul

```powershell
python analyze.py            # doar raportează
python analyze.py --save     # scrie și pragul calibrat în calibration.json
```

Calibrează pragul din sesiunea `gol` și verifică patru criterii:

| Criteriu | Prag de trecere | Măsurat |
|---|---|---|
| Sensibilitate | scor median în `miscare` > 1.0 | **3.33** |
| Continuitate | cea mai lungă pauză fără detecție < 75s | **5s** |
| Fals-pozitive | 0 alerte în 30 min de cameră goală | **0 în 28 min** |
| Latență de detecție | intrarea detectată în < 5s | **mediana 2.5s, max 2.9s** |
| Stabilitate | rata de pachete nu scade sub 80% din mediană | **70.1 vs 70.5/s** |

Rezultatele de mai sus sunt reale, măsurate pe 2× ESP32-WROOM-32 într-o cameră
obișnuită, cu 126.851 de pachete de linie de bază.

### De ce nu criteriul „p95(gol) < p5(miscare)"

Prima versiune a planului cerea ca și cele mai liniștite 5% dintre ferestrele
sesiunii `miscare` să depășească cele mai zgomotoase 5% din camera goală.
Măsurat pe date reale, criteriul e imposibil prin construcție: un om care se
plimbă prin cameră timp de 5 minute se și oprește, iar în acele momente
detectorul raportează corect liniște. Criteriul măsura cât de constant s-a
mișcat operatorul, nu cât de bine detectează sistemul.

L-am înlocuit cu ce contează operațional: mișcarea obișnuită trece pragul în mod
tipic (**sensibilitate**), și cât timp omul e activ nu există nicio pauză destul
de lungă cât să declanșeze alerta de inactivitate (**continuitate**).

`analyze.py` alege singur cel mai mic `k` care dă zero alerte false și ignoră
primele 2 minute din sesiunea `gol` — acolo ești tu, ieșind din cameră.
Măsurat: incluse în calibrare, acele 2 minute urcă maximul de la 0.0030 la
0.0796 și fac pragul de 10 ori mai puțin sensibil, până la punctul în care
sistemul nu mai detectează nimic.

**Dacă un criteriu pică, oprește-te aici.** Nu construi dashboard peste un
semnal care nu discriminează. Verifică, în ordine: distanța dintre plăci,
canalul WiFi, poziționarea, rata de pachete. Dacă problema e doar
fals-pozitivele, crește `--k`.

### Pasul 3 — dashboard

```powershell
python server.py --port COM5
```

Deschide <http://localhost:8000>. Pentru un demo mai scurt decât 5 minute de
așteptare până la alertă:

```powershell
python server.py --port COM5 --alert-after 60
```

Fără plăci conectate, poți reda o înregistrare — util ca să exersezi
prezentarea:

```powershell
python server.py --replay "data/intrari_iesiri_*.csv" --alert-after 20
```

---

## Scenariul de demo, 5 minute

1. Pornește `server.py --alert-after 60`, deschide pagina. Ești în cameră →
   **verde, Activitate**.
2. Ieși din cameră → **galben, Liniște** în câteva secunde.
3. Aștepți un minut → **roșu, ALERTĂ**.
4. Reintri → **verde** aproape instant.
5. Arată istoricul de 24h — supraviețuiește unui restart al serverului.
6. Scoate din priză placa emițătoare → **Senzor offline**, nu „liniște".
   Distincția asta e cea care contează pentru un administrator: sistemul spune
   când nu mai știe, în loc să raporteze fals liniște.

---

## Fișiere

| Fișier | Rol |
|---|---|
| [csi_capture.py](csi_capture.py) | Citire serial + parsare format esp-csi; redare din fișier |
| [record.py](record.py) | Înregistrare sesiuni etichetate, cu comenzi sonore |
| [detector.py](detector.py) | Metrică de mișcare, mașină de stări, persistență SQLite |
| [analyze.py](analyze.py) | Calibrare prag + tabelul de criterii PASS/FAIL |
| [breathing.py](breathing.py) | Detecție de respirație: e cineva acolo, chiar dacă nu se mișcă |
| [analyze_respiratie.py](analyze_respiratie.py) | Validarea detecției de respirație pe o noapte |
| [analyze_noapte.py](analyze_noapte.py) | Distribuția pauzelor fără mișcare peste noapte |
| [server.py](server.py) | Dashboard local (SSE + istoric) |
| [dashboard.html](dashboard.html) | Interfața, o singură pagină, fără dependențe |
| `data/` | Sesiuni brute `.csv` + `monitor.db` |
| `esp-csi/` | Clonă upstream, sursa firmware-ului |

Nimic nu iese din laptop: fără cloud, fără cont, fără internet.

---

## Cum funcționează detecția

Două metrici independente, combinate, ambele calibrate din sesiunea `gol`:

**1. Distanța de formă CSI.** Amplitudinile se mediază pe ~0.5s, iar forma
rezultată se compară cu cea de acum ~1s (corelație pe vectori centrați). Un om
care se mișcă schimbă multipath-ul, deci forma se decorelează; o cameră goală
are forma stabilă.

**2. Deviația RSSI pe ~4s.** Mai lentă și mai grosieră, dar imună la zgomotul
de mai jos.

Scorul afișat e cât de mult depășește oricare dintre ele propriul prag, deci
**1.0 înseamnă exact la limită**.

### Medierea nu e opțională — măsurat pe date reale

Prima versiune calcula deviația standard direct pe o fereastră de o secundă,
fără mediere. Rezultatul, pe 5300 de pachete reale înregistrate în cameră:

| Metodă | Separare nemișcat / mișcare |
|---|---|
| Deviație pe 1s, fără mediere | **1.3x** — inutilizabil |
| Mediere 0.5s + comparație la 1s | 9.5x |
| Mediere 1s + comparație la 2s | **12.4x** |
| Deviație RSSI pe 4s | 8.2x, separare completă |

Motivul: zgomotul de la un pachet la altul e **la fel de mare în camera goală
ca în timpul mișcării**. Într-o fereastră de o secundă el domină complet
semnalul util. Medierea îl stinge și lasă la vedere decorelarea lentă produsă
de un om care se mișcă.

Semnătura fizică, vizibilă în datele brute — similaritatea formei CSI la
distanță crescândă în timp:

```
distanta   NEMISCAT   MISCARE
   13 ms    0.9603    0.9649    <- identice: zgomot per pachet
  263 ms    0.9517    0.9393
  987 ms    0.9495    0.9256
 2632 ms    0.9471    0.9104    <- se despart
```

## Note tehnice

**Subpurtătoarele utile se aleg din date**, nu din tabele de indici codate per
cip. Firmware-ul poate trimite trei blocuri lipite (LLTF, HT-LTF,
STBC-HT-LTF) — se păstrează doar LLTF, fiindcă blocul STBC e zgomot când
transmisia nu folosește STBC (`stbc = 0` în metadate). Se aruncă și
subpurtătoarele nule (exact zero) și primul indice, marcat `first_word_invalid`
de firmware.

**Pragul de ieșire din `MIȘCARE` e ancorat în calibrare**, nu exprimat ca
fracție din pragul de intrare. Formularea ca fracție l-a plasat sub nivelul de
zgomot al camerei goale, iar starea rămânea blocată pe `MIȘCARE` la nesfârșit —
alerta de inactivitate nu s-ar fi declanșat niciodată.

**Detectorul nu raportează nimic până nu se umple cea mai lungă fereastră**
(~4s după calibrare). Altfel primele secunde se calculează pe câteva pachete și
produc valori aberante, care arată ca mișcare deși camera e goală.

**Detectorul folosește exclusiv timpul din pachet**, niciodată ceasul de perete.
De aceea redarea unei înregistrări dă exact același rezultat ca rularea live,
iar `analyze.py` măsoară exact ce ar face sistemul în timp real.


---

## Detecția de respirație — de ce e necesară și ce s-a măsurat

Detecția de mișcare singură **nu poate acoperi noaptea**. Măsurat pe 9 ore de
somn real: mediana semnalului cu un om adormit (0.0002) e identică cu cea a
camerei goale, iar pauzele fără nicio mișcare ajung la **68 de minute**. Un prag
de alertă care să nu sune degeaba ar trebui pus la 90 de minute — inutil pentru
detectarea unei căderi.

Respirația umple golul. Rezultatele pe aceleași 9 ore:

| Semnal | Pauză maximă peste noapte |
|---|---|
| Doar mișcare | 68.5 min |
| Doar respirație | 103 min |
| **Ambele combinate** | **56 min** |

Cu ambele, se poate promite o alertă la 60 de minute în loc de 90. Util, dar
insuficient: la 30 de minute — pragul care ar conta pentru detectarea unei
căderi — sistemul ar da alarme false.

### O cifră anterioară era greșită

O versiune mai devreme a acestui document raporta 23.7 minute. Acea cifră venea
dintr-o regulă de confirmare (3 ferestre în ±3 resp/min) validată pe un control
de **doar 13 ferestre**, care dăduse zero fals-pozitive. Pe un control real de
2 ore și 118 ferestre, aceeași regulă dă **11 fals-pozitive (9.3%)**. Regula
strânsă la 5 ferestre elimină fals-pozitivele, dar pauza combinată urcă la 56
de minute. Zero-ul pe 13 ferestre era o iluzie de eșantion mic.

### Criteriul care contează: consistența între ferestre, nu în interiorul lor

Prima variantă cerea ca subpurtătoarele să cadă de acord asupra frecvenței în
aceeași fereastră de 2 minute. **Nu funcționează**: subpurtătoarele adiacente
sunt corelate — fizic, plus firmware-ul le netezește cu `channel_filter_en` —
deci un singur vârf de zgomot apare în multe deodată. Măsurat pe camera goală,
4 ferestre din 13 au dat acord perfect, dar la rate complet diferite: 10.0,
15.5, 16.0, 17.0 resp/min. Rezultatul: **46% fals-pozitive**, mai mult decât
detecțiile reale din noapte.

Ce nu poate imita zgomotul e să nimerească *aceeași* frecvență în mai multe
ferestre consecutive. Dar trei nu ajung: dacă vârfurile ar fi plasate aleator în
8-30 resp/min, șansa ca trei consecutive să cadă în ±3 e ~5%, iar măsurat a ieșit
9.3%. Fals-pozitivele erau exact coincidențele permise de regulă.

Pe 118 ferestre de cameră cu adevărat goală:

| Regulă | Fals-pozitive | Acoperire noapte | Pauză combinată |
|---|---|---|---|
| 3 ferestre în ±3 | 11 (9.3%) | 31.4% | 23.7 min |
| 4 ferestre în ±3 | 2 (1.7%) | 22.7% | 56 min |
| **5 ferestre în ±3** | **0** | 18.2% | **56 min** |

Rata mediană detectată e 14.0 resp/min, stabilă toată noaptea (IQR 1.5).

### Ce rămâne nevalidat

- **Rata nu e verificată contra unei referințe.** 79.9% din confirmări cad în
  ±2 față de mediană, la un prag de 80% — statistic imposibil de distins
  (eroare standard 3.1% pe 169 de confirmări). Se rezolvă doar numărând
  manual respirațiile în paralel cu o înregistrare.
- **Rata nu e suficient de precisă ca să fie afișată ca număr.** Măsurat contra
  unei referințe numărate manual (100-101 respirații în 7 minute = 14.36/min):
  mediana ferestrelor a dat 12.25, adică **-2.11 resp/min (-15%)**, peste
  pragul de ±2 pe care ni-l propusesem. Împrăștierea între ferestre e 2.66.
  Concluzia de produs: foloseste respirația ca semnal de **prezență**, unde
  eroarea nu contează, și nu afișa o rată numerică — ar invita exact
  interpretarea medicală pe care poziționarea legală o evită.

  Corroborare importantă: peste toată noaptea algoritmul a dat **14.0 resp/min**,
  iar numărătoarea manuală treaz a dat **14.36**. Două măsurători complet
  independente care se potrivesc la 0.4 — cea mai bună dovadă că detecțiile
  nocturne erau respirație reală, nu artefacte.

- **Acoperirea de 18.2% e prea mică.** Semnalul de respirație are BNR 1.5-2.1,
  adică e slab. Aici hardware-ul contează direct: antenă externă în locul celei
  de PCB, și un al doilea receptor contra punctelor oarbe.
- **O noapte, un om, o cameră.** Controlul de cameră goală are acum 2 ore și 118
  ferestre, dar noaptea rămâne una singură.
