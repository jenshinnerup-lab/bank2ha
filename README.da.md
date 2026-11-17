# Bank2HA

[English](README.md) · **Dansk**

Bank2HA henter dine egne bankdata ind i Home Assistant uden at sende dem til et
cloud-dashboard. Appen henter saldi og transaktioner fra dine private **Spar Nord /
Nykredit**-konti via **Enable Banking** (PSD2-kontoinformation, kun læseadgang). Den gemmer
dem i databasetabeller, som du selv styrer, og sender sensorer og hændelser til **Home
Assistant** via MQTT.

Bank2HA kører som en **Home Assistant-app (add-on)** med sin egen side i sidepanelet, eller
som en selvstændig Docker-container ved siden af en hvilken som helst Home Assistant.

> Spar Nord blev i 2026 fusioneret ind i Nykredit Bank og kører nu som et brand under
> Nykredit på BEC's PSD2-platform. Bank2HA virker med begge. Andre banker, som Enable Banking
> understøtter, bør også virke, hvis du ændrer indstillingen *Bank*, men appen er lavet med
> Spar Nord/Nykredit for øje.

---

## Indhold

- [Funktioner](#funktioner)
- [Sådan virker det](#sådan-virker-det)
- [Krav](#krav)
- [Installation – Home Assistant OS](#installation--home-assistant-os)
- [Installation – selvstændig Docker](#installation--selvstændig-docker)
- [Indstillinger](#indstillinger)
- [Entiteter og hændelser i Home Assistant](#entiteter-og-hændelser-i-home-assistant)
- [Kategorier](#kategorier)
- [Datamodel](#datamodel)
- [Sikkerhed og privatliv](#sikkerhed-og-privatliv)
- [Begrænsninger](#begrænsninger)
- [Fejlfinding](#fejlfinding)
- [Kommandolinje og API](#kommandolinje-og-api)
- [Opbygning af repoet](#opbygning-af-repoet)
- [Ansvarsfraskrivelse](#ansvarsfraskrivelse)

---

## Funktioner

- **Kun læseadgang til banken.** Appen bruger udelukkende PSD2's *kontoinformationstjeneste*
  og kan ikke sætte betalinger i gang.
- **Dine data i dine tabeller.** Transaktioner, saldohistorik, konti og samtykker gemmes i
  SQLite som standard, eller i MariaDB/PostgreSQL, hvis du foretrækker det.
- **Fuld historik ved første forbindelse.** Op til to år hentes lige efter MitID-login, hvor
  bankerne typisk tillader den længste historik.
- **Korrekt løbende synkronisering.** Hver sync genlæser et lille overlap. Dubletter undgås
  med faste nøgler, og reserverede posteringer bliver erstattet i stedet for at blive gemt
  to gange, når de bogføres.
- **Automatisk opsætning i Home Assistant.** MQTT discovery opretter enheder og entiteter:
  saldo med langtidsstatistik, månedens forbrug, indtægter og netto, forbrug pr. kategori,
  reserverede posteringer, seneste transaktion, samtykkets udløb og sync-status.
- **Hændelse for hver ny transaktion.** En HA-`event`-entitet fyrer `ny_transaktion`,
  `stor_transaktion` eller `indbetaling`, klar til automationer.
- **Regelbaserede kategorier.** En YAML-fil med regulære udtryk, som genindlæses automatisk.
- **Indbygget web-side.** Her forbinder eller fornyer du bank-samtykket, ser status,
  omdøber konti og ser de seneste transaktioner. Som HA-app ligger siden i sidepanelet bag
  dit HA-login.
- **Påmindelse om samtykke.** En binær sensor tænder 14 dage før MitID-samtykket udløber.

## Sådan virker det

```
 Spar Nord / Nykredit (BEC PSD2)
            │  MitID-samtykke (dig, i browseren)
            ▼
 Enable Banking API  ◄──── signeret JWT (din private nøgle) ──┐
            │                                                 │
            ▼                                                 │
 ┌──────────────── Bank2HA ───────────────────────────────────┴┐
 │  web-UI (ingress)   planlægger (hver 6. time)   kategorier  │
 │          │                 │                       │       │
 │          └──────► sync ◄───┘───────────────────────┘       │
 │                    │                                       │
 │        ┌───────────┴───────────┐                           │
 │        ▼                       ▼                           │
 │  SQLite / MariaDB /      MQTT discovery + tilstand +       │
 │  PostgreSQL              hændelser                         │
 └────────────────────────────────┼──────────────────────────┘
                                  ▼
                           Home Assistant
```

1. **Samtykke.** Du klikker *Forbind* og logger ind med MitID hos banken. Banken giver et
   samtykke, der gælder op til bankens maksimum (typisk 180 dage). Enable Banking returnerer
   en session med listen over de konti, du har givet adgang til.
2. **Sync.** Efter en tidsplan (som standard hver 6. time) henter appen saldi og
   transaktioner for hver aktiv konto. Første sync går `initial_history_days` tilbage.
   Senere syncs starter nogle dage før den seneste bogførte transaktion (`OVERLAP_DAYS`), så
   sene bogføringer ikke bliver overset.
3. **Lagring.** Nye transaktioner indsættes, kendte bliver ikke rørt, og reserverede
   posteringer erstattes ved hver sync. Der gemmes et saldo-øjebliksbillede ved hver sync,
   så du får saldohistorik.
4. **Publicering.** Et overblik (saldo, månedens tal, kategorier, seneste transaktion,
   samtykke-status) sendes som MQTT-tilstand, der huskes af brokeren. Hver ny bogført
   transaktion fyrer en HA-hændelse. Der fyres ikke hændelser for den første
   historik-import.

Konti identificeres med Enable Bankings `identification_hash`. Den er den samme, når du
fornyer samtykket, så historikken bevares på tværs af samtykker.

## Krav

- En konto hos **Enable Banking** (gratis til dine egne konti i *restricted mode*).
- **MitID**-adgang til kontiene.
- **Home Assistant** med **MQTT**-integrationen, normalt via appen *Mosquitto broker*.
- Til HA-appen: Home Assistant OS eller Supervised på amd64 eller aarch64.
- Til selvstændig drift: Docker med Compose.

## Installation – Home Assistant OS

Den detaljerede trin-for-trin-vejledning ligger i [`bank2ha/DOCS.md`](bank2ha/DOCS.md) og
vises også under appens fane **Dokumentation** i HA. Kort fortalt:

1. **Enable Banking.** Opret en *Production*-application på
   <https://enablebanking.com/cp/applications> med redirect URL
   `http://localhost:8080/auth_redirect`, og download `.pem`-nøglen. Klik **Activate by
   linking accounts**, og link dine konti med MitID.
2. **MQTT.** Installér og start appen **Mosquitto broker**, og konfigurér derefter
   MQTT-integrationen, som HA finder.
3. **Tilføj dette repository.** Gå til *Indstillinger → Apps → Installér app → ⋮ →
   Repositories*, tilføj `https://github.com/jenshinnerup-lab/bank2ha`, og installér
   **Bank2HA**. Start den én gang, så dens konfigurationsmappe bliver oprettet.
4. **Privat nøgle.** Kopiér nøglen til `/addon_configs/<id>_bank2ha/eb_private_key.pem`, fx
   med appen *Terminal & SSH*, og kør `chmod 600` på den.
5. **Konfigurér.** Indsæt dit `application_id` under fanen **Konfiguration**, slå *Vis i
   sidepanel* til, og genstart appen.
6. **Forbind.** Åbn **Bank** i sidepanelet, og klik **Forbind**. MitID åbner i en ny fane.
   Bagefter lander browseren på en `localhost`-side, som ikke kan åbnes. Det er forventet.
   Kopiér hele adressen, indsæt den i feltet på Bank-siden, og klik **Fuldfør**.

## Installation – selvstændig Docker

Til Home Assistant Container, eller hvis Bank2HA skal køre på en anden server:

```bash
git clone https://github.com/jenshinnerup-lab/bank2ha && cd bank2ha/standalone
mkdir -p data config secrets && chmod 700 data secrets
cp ~/Downloads/<application-id>.pem secrets/eb_private_key.pem
chmod 400 secrets/eb_private_key.pem && sudo chown 1000:1000 secrets/eb_private_key.pem data
cp .env.example .env                         # udfyld EB_APPLICATION_ID og MQTT_*
cp ../bank2ha/rules.example.yaml config/rules.yaml
docker compose up -d --build
docker compose exec bank2ha python -m app.cli check
```

Åbn derefter <http://localhost:8080>. Web-siden lytter som standard kun på `127.0.0.1`.
Sætter du `BIND_ADDR=0.0.0.0`, så sæt også `UI_PASSWORD`.

## Indstillinger

| HA-app-indstilling | Miljøvariabel (selvstændig) | Standard | Beskrivelse |
|---|---|---|---|
| `application_id` | `EB_APPLICATION_ID` | – | Enable Banking application ID (UUID) |
| `private_key_file` | `EB_PRIVATE_KEY_PATH` | `eb_private_key.pem` | Nøglefil. I HA-appen er stien relativ til `/addon_configs/<id>_bank2ha/` |
| `bank_name` | `ASPSP_NAME` | `Spar Nord` | Bankens navn som i Enable Banking, fx `Nykredit` |
| `bank_country` | `ASPSP_COUNTRY` | `DK` | Landekode med to bogstaver |
| `consent_days` | `CONSENT_DAYS` | `180` | Ønsket samtykkelængde, begrænset af bankens maksimum |
| `sync_interval_minutes` | `SYNC_INTERVAL_MINUTES` | `360` | Minutter mellem automatiske syncs (60–1440) |
| `initial_history_days` | `INITIAL_HISTORY_DAYS` | `730` | Historik der hentes ved første sync |
| `publish_transaction_details` | `PUBLISH_TRANSACTION_DETAILS` | `true` | Send modpart og tekst til HA. Er den slået fra, sendes kun beløb og kategori |
| `large_transaction_threshold` | `LARGE_TRANSACTION_THRESHOLD` | `2000` | Beløb (kr.), hvorfra en betaling regnes som stor |
| `redirect_url` | `EB_REDIRECT_URL` | `http://localhost:8080/auth_redirect` | Skal matche din Enable Banking-application |
| `database_url` | `DATABASE_URL` | SQLite i `/data` | SQLAlchemy-URL, fx `mysql+pymysql://bruger:pw@core-mariadb/bank2ha` |
| – | `MQTT_HOST/PORT/USERNAME/PASSWORD/TLS` | – | Kun selvstændig drift. HA-appen henter MQTT-login fra Supervisor |
| – | `OVERLAP_DAYS` | `10` | Dage der genlæses før seneste bogførte transaktion |
| – | `UI_USERNAME` / `UI_PASSWORD` | `admin` / tom | HTTP Basic-login til web-siden ved selvstændig drift |

## Entiteter og hændelser i Home Assistant

**Enheden "Bank2HA"**

| Entitet | Beskrivelse |
|---|---|
| `sensor.bank2ha_samtykke_dage_tilbage` | Dage til MitID-samtykket udløber (attributter: `valid_until`, `status`) |
| `binary_sensor.bank2ha_samtykke_kraever_fornyelse` | Tændt hvis samtykket ikke er aktivt eller har under 14 dage tilbage |
| `sensor.bank2ha_seneste_sync` | Tidspunkt for seneste sync (attributter: `ok`, `message`) |
| `binary_sensor.bank2ha_sync_fejl` | Tændt hvis seneste sync fejlede |
| `button.bank2ha_synkroniser_nu` | Synkronisér nu (tæller med i bankens daglige grænse) |

**Enheden "Bank \<konto\>"**, én pr. konto. Entitets-ID'erne følger kontoens kaldenavn, fx
`sensor.bank_budgetkonto_saldo`.

| Entitet | Beskrivelse |
|---|---|
| `…_saldo` | Saldo (disponibel saldo foretrækkes). Alle saldotyper som attributter. `state_class: total`, så HA gemmer langtidsstatistik |
| `…_forbrug_denne_maned` | Månedens forbrug. Forbrug pr. kategori som attributter |
| `…_indtaegter_denne_maned` | Månedens indtægter |
| `…_netto_denne_maned` | Månedens netto |
| `…_reserverede_posteringer` | Antal reserverede posteringer (attribut: `amount`) |
| `…_seneste_transaktion` | Beløb for seneste bogførte transaktion (attributter: dato, modpart, tekst, kategori) |
| `event.…_transaktion` | Fyrer for hver ny bogført transaktion |

**Hændelsestyper og attributter**

| `event_type` | Hvornår |
|---|---|
| `ny_transaktion` | Udgående betaling under grænsen |
| `stor_transaktion` | Udgående betaling på eller over `large_transaction_threshold` |
| `indbetaling` | Indgående betaling |

Attributter: `amount` (med fortegn), `currency`, `date`, `category` og, hvis det er slået
til, `counterparty` og `description`.

Eksempler på automationer (stor betaling, løn modtaget, lav saldo, påmindelse om samtykke),
et dashboard-kort og SQL-eksempler ligger i [`ha/`](ha/).

## Kategorier

Kategorierne styres af `rules.yaml`. I HA-appen ligger filen i
`/addon_configs/<id>_bank2ha/rules.yaml` og oprettes ud fra
[`rules.example.yaml`](bank2ha/rules.example.yaml) ved første start.

```yaml
default_in: Indtægt
default_out: Andet
rules:
  - category: Dagligvarer
    direction: out              # in | out | udelad = begge
    match: ["netto", "f[øo]tex", "rema"]
```

- Mønstrene er regulære udtryk, der ikke skelner mellem store og små bogstaver. De matches
  mod modpart og posteringstekst.
- Den første regel, der matcher, vinder.
- Filen genindlæses, når den ændres. Nye regler gælder nye transaktioner. Kør
  `python -m app.cli recategorize` for at opdatere de eksisterende.

## Datamodel

| Tabel | Indhold |
|---|---|
| `accounts` | Én række pr. konto. Nøgle: Enable Bankings `identification_hash`. Indeholder sessionens aktuelle `uid`, IBAN, navn, dit kaldenavn, valuta og om kontoen er aktiv |
| `transactions` | Alle transaktioner. `amount` har fortegn (minus = penge ud). `status` er `BOOK` (bogført) eller `PDNG` (reserveret). Bogførings-, valør- og transaktionsdato, modpart, tekst, bankkode, saldo efter, kategori, original JSON i `raw_json`, `first_seen` og `last_seen` |
| `balances` | Saldo-øjebliksbillede pr. sync og saldotype (`ITAV`, `CLBD`, …), som giver saldohistorik |
| `bank_sessions` | Samtykker med status (`active`, `expired`, `superseded`) og udløb |
| `auth_states` | Kortlivede login-forsøg, bruges til at kontrollere `state`-parameteren |
| `sync_log` | Én række pr. sync med resultat og antal nye transaktioner |

**Dubletter.** Har banken sendt en `entry_reference`, er nøglen et hash af konto, reference
og status. Ellers er det et hash af transaktionens indhold plus et løbenummer, så to ens køb
samme dag begge bliver gemt.

## Sikkerhed og privatliv

- **Kun læseadgang.** Appen bruger kun endpoints til kontoinformation og kalder aldrig
  betalingsfunktionerne.
- **Privat nøgle.** I HA-appen ligger den i appens egen konfigurationsmappe. Ved selvstændig
  drift monteres den som en skrivebeskyttet Docker secret. Den gemmes aldrig i image eller
  database.
- **Login.** Som HA-app kan web-siden kun nås via HA ingress, bag dit HA-login, og der
  åbnes ingen port. Ved selvstændig drift lytter den som standard kun på `127.0.0.1`, med
  mulighed for HTTP Basic-login.
- **Filer og container.** Databasefilen oprettes med rettighederne `600`. Den selvstændige
  container kører uden root, med skrivebeskyttet filsystem, `cap_drop: ALL` og
  `no-new-privileges`.
- **Hvad der forlader dit netværk.** Kun kaldene til Enable Banking og banken. Ingen
  telemetri.
- **Home Assistants historik.** HA's recorder gemmer sensorhistorik. Sæt
  `publish_transaction_details: false`, hvis modparter og tekster ikke må ende i HA's
  database.
- **Backup.** I HA-appen kommer databasen med i HA's backups. Brug krypterede backups. Det
  gemte session-ID giver sammen med din private nøgle adgang til kontodata, indtil
  samtykket udløber, så behandl backups som følsomme.
- **Kryptering i hvile.** Læg data på en krypteret disk (LUKS, BitLocker, ZFS native
  encryption) i stedet for at stole på kryptering i selve databasen.
- **Tilbagekald adgang.** Slet application'en i Enable Bankings Control Panel, eller
  tilbagekald samtykket i netbanken. Bank2HA holder op med at virke ved næste sync.

## Begrænsninger

- **Daglig grænse.** PSD2 tillader typisk **4 automatiske opslag pr. konto pr. døgn**.
  Standard-intervallet på 6 timer holder sig inden for det. Manuelle syncs tæller med.
- **Fornyelse af samtykke.** Samtykket skal fornyes med MitID med jævne mellemrum, typisk
  hver 180. dag.
- **Login-omdirigering.** HA's ingress-side kan ikke modtage bankens omdirigering direkte,
  så adressen skal indsættes manuelt én gang pr. samtykke.
- **Restricted mode.** Kun konti, der er linket i Enable Bankings Control Panel, bliver
  returneret.
- **Datakvalitet.** Posteringstekster og modpartsnavne afhænger af, hvad banken sender via
  PSD2. De kan afvige fra netbanken.

## Fejlfinding

| Besked (appens log) | Løsning |
|---|---|
| `Mangler privat nøgle` | Læg `.pem`-filen i appens konfigurationsmappe med det navn, der står i indstillingerne |
| `Bank 'Spar Nord' ikke fundet` | Loggen viser de mulige navne. Sæt *Bank* til et af dem, fx `Nykredit` |
| `Enable Banking API error 401` | Forkert application ID, eller nøglen hører ikke til den application |
| `Ukendt eller udløbet 'state'` | Start *Forbind* forfra, og indsæt adressen inden for en time |
| `MQTT-service ikke fundet` | Installér og start Mosquitto broker, og genstart Bank2HA |
| Ingen konti efter login | Link kontiene i Enable Bankings Control Panel (restricted mode) |
| `Samtykke afvist af banken` | Forny samtykket fra Bank-siden |

## Kommandolinje og API

```bash
python -m app.cli check          # tjek nøgle, application og banknavn
python -m app.cli connect        # samtykke-flow i terminalen
python -m app.cli sync           # synkronisér nu
python -m app.cli summary        # overblik som JSON
python -m app.cli recategorize   # kør rules.yaml på alle transaktioner igen
```

Sæt `docker compose exec bank2ha` foran kommandoerne ved selvstændig drift.

Web-appen har også `GET /api/summary` og `GET /api/transactions?days=30&category=…`, som
returnerer JSON. De bruger samme login som web-siden.

## Opbygning af repoet

```
repository.yaml          Definition af Home Assistant-app-repository
bank2ha/                 HA-appen (også kilde til selvstændig drift)
  config.yaml            App-manifest: indstillinger, ingress, MQTT-service
  Dockerfile             App-image
  DOCS.md                Installationsvejledning, der vises i HA
  app/                   Python-kildekode
    addon.py             Startpunkt i HA (indstillinger + MQTT-login fra Supervisor)
    eb_client.py         Enable Banking-klient (JWT, kontoinformation)
    sync.py              Samtykke, sync, dubletter, overblik
    db.py                SQLAlchemy-skema
    mqtt_ha.py           MQTT discovery, tilstand og hændelser
    categorize.py        Kategorisering ud fra rules.yaml
    web.py               Web-side, JSON-API, planlægger
    cli.py               Kommandolinjeværktøjer
  rules.example.yaml     Standardregler for kategorier
standalone/              Docker Compose-opsætning
ha/                      Eksempler på automationer, dashboard-kort og SQL-sensorer
```

## Ansvarsfraskrivelse

Bank2HA er et uafhængigt projekt og er ikke tilknyttet eller godkendt af Spar Nord,
Nykredit, BEC eller Enable Banking. Du bruger det med dine egne login-oplysninger og dit
eget samtykke, under Enable Bankings servicevilkår. Tjek, at tallene stemmer med netbanken,
før du træffer beslutninger ud fra dem.
