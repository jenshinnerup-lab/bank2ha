# bank2ha – Spar Nord/Nykredit-transaktioner i din egen database og i Home Assistant

Henter saldi og transaktioner fra private Spar Nord-konti via **Enable Banking** (PSD2,
read-only), gemmer dem i tabeller hos dig og sender sensorer og hændelser til **Home Assistant**.

```
Spar Nord (Nykredit/BEC PSD2) ──MitID──► Enable Banking API ──► bank2ha
                                                                 ├─► SQLite / MariaDB / Postgres
                                                                 ├─► MQTT discovery ─► Home Assistant
                                                                 └─► Web-UI (HA-sidepanel) + JSON API
```

## Installation

| Du kører | Vælg |
|---|---|
| **Home Assistant OS / Supervised** | Som HA-app: tilføj dette repo under *Indstillinger → Apps → Repositories*. Fuld vejledning: [`bank2ha/DOCS.md`](bank2ha/DOCS.md) |
| HA Container / anden server | Docker Compose i [`standalone/`](standalone/) – kopiér `.env.example` til `.env`, læg nøglen i `standalone/secrets/eb_private_key.pem`, `docker compose up -d --build` |

## Entiteter i Home Assistant

Kræver MQTT-integrationen (fx Mosquitto-add-on'en). Lav en MQTT-bruger til bank2ha og sæt
`MQTT_*` i `.env`. Enhederne dukker automatisk op:

| Enhed | Entitet | Indhold |
|---|---|---|
| **Bank2HA** | `sensor …samtykke_dage_tilbage` | Dage til MitID-samtykket skal fornyes |
| | `binary_sensor …samtykke_kraever_fornyelse` | Tænder 14 dage før udløb |
| | `sensor …seneste_sync`, `binary_sensor …sync_fejl` | Status |
| | `button …synkroniser_nu` | Manuel sync |
| **Bank \<konto\>** | `sensor …saldo` | Disponibel saldo (+ alle saldotyper som attributter, langtidsstatistik) |
| | `sensor …forbrug_denne_maned` | Med forbrug pr. kategori som attributter |
| | `sensor …indtaegter_denne_maned`, `…netto_denne_maned` | |
| | `sensor …reserverede_posteringer` | Antal + beløb af reserverede |
| | `sensor …seneste_transaktion` | Beløb, modpart, tekst, kategori |
| | `event …transaktion` | Fyrer `ny_transaktion`, `stor_transaktion`, `indbetaling` pr. ny postering |

Se [`ha/automations.yaml`](ha/automations.yaml) (notifikation ved store betalinger, løn modtaget, lav saldo,
samtykke-påmindelse), `ha/dashboard-card.yaml` og `ha/sql_sensors.md` (egne SQL-opgørelser).

## Tabeller

| Tabel | Indhold |
|---|---|
| `accounts` | Konti (nøgle = Enable Bankings `identification_hash`, som er stabil på tværs af samtykker), kaldenavn, IBAN |
| `transactions` | Alle posteringer. `amount` er med fortegn (minus = penge ud). `status` BOOK/PDNG. Original JSON i `raw_json` |
| `balances` | Saldo-snapshot ved hver sync → saldohistorik |
| `bank_sessions` | Samtykker og udløbsdato |
| `sync_log` | Log over kørsler |

Dubletter undgås med en deterministisk nøgle (`entry_reference` eller et hash af indholdet).
Reserverede posteringer erstattes ved hver sync, så de ikke ender dobbelt, når de bogføres.

## Sikkerhed

- **Kun læseadgang**: appen bruger udelukkende AIS (kontoinformation) – ingen betalinger.
- **Privat nøgle** monteres som Docker secret (read-only) og ligger ikke i image eller database.
- **Database** ligger lokalt med filrettigheder `600`; containeren kører som non-root,
  read-only rootfs, `cap_drop: ALL`, `no-new-privileges`.
- **Web-UI** lytter kun på `127.0.0.1` som standard. Åbner du for LAN (`BIND_ADDR=0.0.0.0`),
  så sæt `UI_PASSWORD` (HTTP Basic). Brug en reverse proxy med HTTPS, hvis den skal ud over LAN.
- **Kryptering i hvile**: læg `data/` på en krypteret disk (LUKS/BitLocker/ZFS-native
  encryption) – det er mere robust end kryptering i selve SQLite.
- **HA's recorder** gemmer sensorhistorik. Vil du ikke have modparter/tekster i HA's database,
  så sæt `PUBLISH_TRANSACTION_DETAILS=false` (kun beløb og kategori sendes).
- Session-ID'et i `bank_sessions` giver – sammen med din private nøgle – adgang til
  kontodata indtil samtykket udløber. Behandl backup af `data/` som følsom.

## Begrænsninger

- PSD2 tillader typisk **4 automatiske opslag pr. konto pr. døgn** → standard-interval er 6 timer.
  Manuel sync fra HA tæller også med.
- Samtykket skal fornyes med MitID med jævne mellemrum (typisk 180 dage – appen spørger om det
  maksimale banken tillader). HA giver besked 14 dage før.
- Kategorisering er regelbaseret (`config/rules.yaml`, regex). Filen genindlæses automatisk,
  men nye regler gælder kun nye transaktioner – kør `python -m app.cli recategorize` for at
  opdatere de gamle.

## Nyttige kommandoer

```bash
docker compose exec bank2ha python -m app.cli sync       # sync nu
docker compose exec bank2ha python -m app.cli summary    # JSON-overblik
curl -u admin:pw http://localhost:8080/api/transactions?days=30
sqlite3 data/bank.db "SELECT category, -SUM(amount) FROM transactions WHERE amount<0 GROUP BY 1 ORDER BY 2 DESC;"
```
