# Plan de Business: Sistem de Monitorizare Non-Invazivă (WiFi CSI) pentru Vârstnici
 
*De la prima discuție tehnică până la primul cămin-client și producția fizică*
 
---
 
## 0. Rezumat
 
**Ce construim:** un sistem de senzori ESP32 (WiFi CSI) care detectează prezență, activitate și rată de respirație, fără camere sau brățări, livrat ca hardware ieftin + abonament SaaS.
 
**Cine plătește:** în ordinea priorității — (1) cămine de bătrâni private, (2) agenții de îngrijire la domiciliu, (3) familii individuale (D2C, cu rezerve tehnice — vezi secțiunea 8).
 
**Poziționare legală:** strict „insight comportamental / suport operațional", niciodată diagnostic medical — ține produsul în afara MDR.
 
**Stack tehnic preferat** (bazat pe ce ai mai folosit): React/Next.js + Node.js + TypeScript pentru dashboard, Supabase (Postgres) pentru backend/date, Python pentru procesarea semnalului CSI, firmware C pe ESP32 (ESP-IDF).
 
---
 
## Faza 0 — De la idee la schelet de proiect, cu Claude Code
 
**Obiectiv:** treci de la conversație la un repo funcțional, fără să pierzi timp pe configurare manuală.
 
1. **Pregătește un brief scris** înainte să deschizi Claude Code — folosește documentele deja generate (prezentarea de produs, acest plan) ca context inițial. Claude Code lucrează mult mai bine cu un brief clar decât cu explicații verbale fragmentate.
2. **Cere-i lui Claude Code să scaffold-eze un monorepo**, cu separare clară:
   - `firmware/` — codul C pentru ESP32 (bazat pe espressif/esp-csi, adaptat)
   - `signal-processing/` — scripturile Python de procesare CSI (pornind de la scriptul de respirație deja scris)
   - `backend/` — API-ul Node.js/TypeScript + integrare Supabase
   - `dashboard/` — aplicația Next.js pentru personal (multi-room view, alerte)
   - `docs/` — acest plan, prezentarea de produs, notițe de la interviurile de validare
3. **Definește MVP-ul explicit cu Claude Code** înainte să scrie cod: ce features din documentul de produs intră în prima versiune (probabil doar: hartă prezență + alertă de inactivitate + rată respirație), ce rămâne pentru mai târziu (scor de vitalitate, rapoarte ROI, praguri personalizate).
4. **Cere teste automate de la început** pentru partea de procesare semnal (Python) — aici greșelile sunt costisitoare pentru că afectează direct acuratețea pe care o promiți la pitch.
---
 
## Faza 1 — Teste acasă (validare tehnică, 1-2 săptămâni)
 
Ai deja pașii concreți din discuția tehnică anterioară — recapitulare rapidă:
 
1. Instalează ESP-IDF pe Windows 11, clonează `espressif/esp-csi`.
2. Flash-uiește un ESP32 ca STA, conectat la routerul/hotspot-ul propriu.
3. Test de sanitate: diferență vizibilă în CSI între „nemișcat" și „mă mișc prin cameră".
4. Salvează log-uri de 60-90s, procesează cu scriptul Python (filtru trece-bandă 0.1-0.5 Hz + FFT/peak detection) pentru rată de respirație.
5. **Criteriu de succes pentru a trece mai departe:** graficul filtrat arată o oscilație periodică clară, nu doar zgomot, comparabilă cu ce ai numărat manual cronometrând propria respirație.
Dacă nu obții semnal curat în 1-2 săptămâni de încercări (subcarrier greșit, sample rate greșit, distanță/mediu nepotrivit), oprește-te aici și diagnostichează înainte să investești în restul planului — restul depinde de acest fundament tehnic.
 
---
 
## Faza 2 — Infrastructură software (cost redus, pe ce ai mai lucrat)
 
**Recomandare concretă, verificată pe prețuri curente (2026):**
 
| Componentă | Recomandare | Cost la scară mică |
|---|---|---|
| Backend / bază de date | Supabase (Postgres) | Gratuit pentru dezvoltare și primul pilot — plan Free oferă 500 MB DB, 50.000 utilizatori activi, funcții edge |
| Dashboard web | Next.js pe Vercel | Gratuit pe planul Hobby cât timp nu ai trafic comercial serios |
| Backend API | Node.js/TypeScript (Supabase Edge Functions sau server propriu) | Inclus în Supabase la început |
| Ingestie date de la ESP32 | MQTT (broker gratuit tip HiveMQ Cloud/EMQX, tier serverless) sau HTTP direct către Supabase | Gratuit la volum mic — verifică limitele curente înainte să te bazezi pe ele, se schimbă des |
| Procesare semnal | Python (scipy/numpy), rulat ca job separat sau integrat în backend | Cost zero (rulează pe hardware propriu sau pe compute inclus) |
 
**Atenție la un detaliu important:** planul Free Supabase **pune proiectul în pauză automat după 7 zile de inactivitate**. E perfect pentru dezvoltare, dar **nu-l folosi pentru primul pilot cu clienți reali** — un cămin care nu poate accesa dashboard-ul într-o dimineață pentru că „proiectul era în pauză" e exact genul de incident care omoară încrederea unui prim client. Treci pe planul Pro (~25$/lună, ~115 lei) în momentul în care ai primul pilot plătit, nu mai devreme.
 
**De ce Supabase, nu altceva:** îl cunoști deja din SaaS-urile tale — NexaCRM, Reminders.ro etc. Nu înveți o unealtă nouă în paralel cu validarea unei idei noi de business; refolosești ce știi deja să faci rapid.
 
---
 
## Faza 3 — Pregătirea demo-ului pentru cămine (2-4 săptămâni după Faza 1)
 
1. Extinde demo-ul de acasă la un dashboard minim funcțional: status cameră (verde/galben/roșu), istoric ultimele 24h, alertă simplă de inactivitate.
2. Folosește documentul de prezentare a produsului deja pregătit (cu corecturile de poziționare legală) ca material de pitch — nu ca specificație tehnică pentru ce trebuie construit integral înainte de prima întâlnire. Demo-ul poate arăta mult mai puțin decât documentul promite; important e principiul, nu feature-completeness.
3. Pregătește un scenariu de demo reproductibil: ce anume arăți în 5 minute cuiva care nu știe nimic tehnic — „uite, plec din cameră → status devine galben după X minute → simulez o alertă".
4. Nu promite acuratețe numerică exactă la acest stadiu ("respirații/minut cu ±1 eroare") până nu ai date reale suficiente — riști să te prindă o întrebare tehnică la care nu ai răspuns solid.
---
 
## Faza 4 — Validare de piață, în paralel cu Faza 3
 
Foloseşte instrumentele deja identificate:
 
1. **Prospectare** din lista Ministerului Muncii (cămine licențiate pe județ) și iCamin.ro.
2. **Interviuri de discovery** (nu pitch) — cele 5 zone deja pregătite: proces actual, buget/decizie, constrângeri tehnice ale clădirii, îngrijorări/obiecții, disponibilitate pentru pilot.
3. Ținta minimă înainte să treci la Faza 5: **3-5 conversații reale** cu administratori, dintre care cel puțin unul dispus la un test LOI sau pilot plătit simbolic.
4. Ține minte riscul legislativ discutat (propunerea de a interzice societăților comerciale să ofere servicii sociale) — întreabă administratorii dacă sunt la curent și cum văd asta; răspunsul lor îți dă un semnal real despre orizontul de timp pe care se pot angaja.
---
 
## Faza 5 — Primele demo-uri și pilot la cămine (luna 2-4)
 
1. Alege 1-2 cămine din cele validate în Faza 4 pentru un **pilot de 4-6 săptămâni**, pe 5-10 camere, la cost redus sau simbolic (nu gratuit — vezi motivul discutat: gratuit nu validează angajament real).
2. Instalează personal la început (nu te baza pe personalul azilului să facă asta singur în această fază) — observă direct problemele de instalare/interferență CSI între camere adiacente.
3. Colectează metrici clare de succes agreate dinainte cu administratorul: rată de fals-pozitive, timp de răspuns al personalului la alerte, feedback subiectiv după 4 săptămâni.
4. **La finalul pilotului**, obții fie primul contract plătitor, fie un refuz clar cu motiv — ambele sunt informații utile. Un „poate mai vedem" nedefinit e singurul rezultat cu adevărat rău, pentru că nu-ți spune nimic.
---
 
## Faza 6 — Produsul fizic și producția (după primul contract confirmat)
 
**Nu investi în producție de serie înainte de Faza 5 confirmată** — riscul e prea mare pentru un solo dev.
 
1. **BOM per kit**: 2× ESP32 + carcasă/priză + alimentator + cabluri ≈ 80-130 lei la cantități mici (retail RO), scade spre 50-80 lei la comenzi de 50+ bucăți.
2. **Concept „in-plug"**: integrarea plăcilor în carcase de priză de perete — verifică un producător local de carcase custom sau folosește carcase generice modificate la început, nu investi în tooling de injecție plastic pentru primele 20-30 unități.
3. **Asamblare**: manuală la început (tu sau cineva plătit cu ora), nu externaliza la o fabrică până nu ai volum care justifică minimul de comandă.
4. **Control de calitate**: testează fiecare kit înainte de livrare (flash firmware + test de conectivitate) — un kit defect livrat la un client pilot poate costa toată relația.
---
 
## Faza 7 — Distribuție și vânzare
 
1. **Model de preț confirmat**: HaaS — kit la 99-199 lei taxă inițială (sau 0 lei cu angajament 12 luni) + 35-45 lei/pat/lună.
2. **Facturare**: contract redactat ca „serviciu software de suport operațional", integrat cu e-Factura (obligatoriu pentru facturare B2B în România) — separat de asta, configurează un procesator de plăți recurente (Netopia, EuPlătesc) pentru încasare automată, nu te baza pe e-Factura pentru asta.
3. **Canale de extindere, în ordine**: (a) referral direct de la primul client mulțumit, (b) parteneriat cu 1-2 agenții de îngrijire la domiciliu, (c) alte cămine găsite prin aceleași liste de prospectare.
4. **Documente legale de avut gata înainte de al doilea contract**: clauză de răspundere („instrument de suport, nu înlocuiește verificările personalului"), acord de procesare a datelor (DPA) separat, pentru conformitate GDPR.
---
 
## 8. Riscuri de urmărit constant
 
- **Legislativ**: propunerea de a interzice societăților comerciale cu scop lucrativ să ofere servicii sociale — afectează direct segmentul principal de clienți (225 de operatori privați). Urmărește evoluția înainte să te angajezi în contracte pe termen lung cu acest segment.
- **Tehnic la scară**: interferența CSI între camere adiacente într-o clădire cu 40+ camere — netestat încă la acest volum.
- **Segment diaspora/rural (D2C)**: arhitectura WiFi CSI presupune o rețea WiFi funcțională la locația monitorizată — nu e garantat la părinți vârstnici din mediul rural fără telefon. Nu extinde spre acest segment fără să validezi explicit infrastructura WiFi existentă la potențialii clienți.
- **Timp personal**: tot planul presupune muncă în paralel cu jobul full-time de la TechWise — realist, așteaptă-te la un ritm de câteva luni per fază, nu săptămâni.
---
 
## Cronologie recapitulativă (estimare realistă)
 
| Fază | Durată estimată | Cumulat |
|---|---|---|
| 0. Schelet proiect cu Claude Code | 1-2 săptămâni | Luna 0.5 |
| 1. Teste acasă | 1-2 săptămâni | Luna 1 |
| 2. Infrastructură software | În paralel cu Faza 1 | Luna 1 |
| 3. Demo pentru cămine | 2-4 săptămâni | Luna 2 |
| 4. Validare de piață (interviuri) | În paralel cu Faza 3 | Luna 2 |
| 5. Pilot la 1-2 cămine | 4-6 săptămâni | Luna 3-4 |
| 6. Producție fizică (primul lot mic) | 2-4 săptămâni, după confirmare | Luna 4-5 |
| 7. Primul contract plătitor real | — | Luna 4-6 |
 
Realist, de la prima linie de cod până la primul leu de venit recurent confirmat: **4-6 luni**, presupunând constanță și fără blocaje tehnice majore în Faza 1.