# Bank2HA – installation på Home Assistant OS

Bank2HA henter saldi og transaktioner fra dine Spar Nord-konti (nu et brand under Nykredit)
via Enable Banking. Den kan kun læse, ikke betale. Data gemmes i appens egen database og
sendes til Home Assistant som sensorer.

## 1. Enable Banking (én gang, ca. 10 min.)

1. Opret en gratis konto på <https://enablebanking.com> og åbn **Control Panel → Applications**.
2. **Add application**: vælg *Production*, giv den et navn (fx `bank2ha`), og lad browseren
   generere nøglen. Filen `<application-id>.pem` downloades – den er din private nøgle.
3. **Redirect URLs**: `http://localhost:8080/auth_redirect`.
   Privacy/Terms-URL'er bliver ikke tjekket i restricted mode – brug fx `https://example.com`.
4. Klik **Activate by linking accounts** → Danmark → *Spar Nord* (eller *Nykredit*) → log ind
   med MitID og link de konti, du vil hente. Det er gratis, og appen kan kun se de linkede konti.
5. Notér **application ID** (UUID'en, samme som .pem-filnavnet).

## 2. MQTT-broker

Har du ikke MQTT i forvejen:
**Indstillinger → Apps → Installér app → Mosquitto broker → Installér → Start**.
HA foreslår derefter MQTT-integrationen under *Enheder & tjenester* – klik **Konfigurér**.
Bank2HA henter selv brugernavn og adgangskode til Mosquitto.

## 3. Installér Bank2HA

1. **Indstillinger → Apps → Installér app → ⋮ (øverst til højre) → Repositories**.
2. Tilføj `https://github.com/jenshinnerup-lab/bank2ha` og luk dialogen.
3. Find **Bank2HA** i listen → **Installér** (det tager et par minutter at bygge).

## 4. Læg nøglen på plads

**Nemmest (fra version 1.0.1):** Start appen, åbn **Bank** i sidepanelet, og indsæt
application ID og hele indholdet af `.pem`-filen i formularen *Opsætning mangler* → **Gem**.
Så kan du springe trin 5.1 over.

**Alternativt via Terminal:**

Nøglen skal ligge i appens konfigurationsmappe `/addon_configs/<id>_bank2ha/`.
Den nemmeste vej er **Terminal & SSH**:

```bash
ls -d /addon_configs/*bank2ha          # find den præcise mappe
cd /addon_configs/*bank2ha
nano eb_private_key.pem                # indsæt hele indholdet af .pem-filen, gem med Ctrl+O, Ctrl+X
chmod 600 eb_private_key.pem
```

(Mappen oprettes ved første start – start appen én gang, hvis den ikke findes.)

Alternativt via Samba-share eller File editor-appen.

## 5. Konfigurér og start

1. Fanen **Konfiguration**: indsæt `application_id`. Resten kan blive som standard.
2. Fanen **Info**: slå *Vis i sidepanel* til, og tryk **Start**.
3. Tjek fanen **Log** – der må ikke stå "Mangler privat nøgle".

## 6. Forbind banken med MitID

1. Åbn **Bank** i sidepanelet → **Forbind Spar Nord (MitID)**. Login åbner i en ny fane.
2. Efter MitID ender du på `http://localhost:8080/auth_redirect?code=…`, og siden kan ikke
   åbnes. **Det er forventet.** Kopiér hele adressen fra adresselinjen.
3. Gå tilbage til Bank-siden i HA, indsæt adressen i feltet *Efter MitID-login* → **Fuldfør**.
4. Første synkronisering henter op til 2 års historik. Giv konti et kaldenavn (fx
   "Budgetkonto"), så entiteterne får pæne navne.

Enhederne **Bank2HA** og **Bank \<konto\>** dukker op under MQTT-integrationen.

## Drift

- **Samtykket** skal fornyes med MitID (typisk hver 180. dag). `binary_sensor` *Samtykke kræver
  fornyelse* tænder 14 dage før – lav en notifikation på den (se `ha/automations.yaml` i repoet).
- **Sync** kører hver 6. time. PSD2 tillader typisk kun 4 automatiske opslag pr. døgn.
- **Kategorier** redigeres i `/addon_configs/<id>_bank2ha/rules.yaml` (regex). Ændringer gælder
  nye transaktioner.
- **Backup**: databasen ligger i appens `/data` og kommer med i HA-backups. Brug krypterede
  backups, da de indeholder dine bankdata.
- **Privatliv**: slå *Send modpart og tekst til HA* fra, hvis HA's historik kun må indeholde
  beløb og kategori.

## Fejlfinding

| Log-besked | Løsning |
|---|---|
| `Mangler privat nøgle` | Trin 4 – tjek filnavn og mappe |
| `Bank 'Spar Nord' ikke fundet` | Loggen viser de mulige navne – ret *Bank* i Konfiguration (fx `Nykredit`) |
| `Enable Banking API error 401` | Forkert application ID eller nøgle hører ikke til den application |
| `Ukendt eller udløbet 'state'` | Start *Forbind* forfra og indsæt adressen inden for en time |
| `MQTT-service ikke fundet` | Installér og start Mosquitto broker (trin 2), genstart Bank2HA |
| Ingen konti efter login | Kontiene skal være linket i Enable Banking Control Panel (trin 1.4) |
