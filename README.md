# Bank2HA

**English** · [Dansk](README.da.md)

Bank2HA brings your own bank data into Home Assistant without handing it to a cloud
dashboard. It fetches balances and transactions from your private **Spar Nord / Nykredit**
accounts through **Enable Banking** (PSD2 Account Information, read-only), stores them in
database tables you control, and publishes sensors and events to **Home Assistant** via MQTT.

It runs as a **Home Assistant app (add-on)** with its own sidebar page, or as a standalone
Docker container next to any Home Assistant installation.

> Spar Nord merged into Nykredit Bank in 2026 and now runs as a brand of Nykredit on the BEC
> PSD2 platform. Bank2HA works with both. Any other bank supported by Enable Banking should
> work as well by changing the *bank name* option, but only Spar Nord/Nykredit has been the
> design target.

---

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation – Home Assistant OS](#installation--home-assistant-os)
- [Installation – standalone Docker](#installation--standalone-docker)
- [Configuration options](#configuration-options)
- [Home Assistant entities and events](#home-assistant-entities-and-events)
- [Categories](#categories)
- [Data model](#data-model)
- [Security and privacy](#security-and-privacy)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Command line and API](#command-line-and-api)
- [Repository layout](#repository-layout)
- [Disclaimer](#disclaimer)

---

## Features

- **Read-only bank access.** Only the PSD2 *Account Information Service* is used. The app
  cannot initiate payments.
- **Your data, your tables.** Transactions, balance history, accounts and consents are
  stored in SQLite by default, or in MariaDB/PostgreSQL if you prefer.
- **Full history on first connect.** Up to two years are fetched right after MitID login,
  when banks usually allow the longest history.
- **Correct incremental sync.** Each sync re-reads a small overlap window. Duplicates are
  avoided with deterministic keys, and pending (reserved) transactions are replaced instead
  of being stored twice once they are booked.
- **Automatic Home Assistant setup.** MQTT discovery creates devices and entities: balance
  with long-term statistics, monthly spending, income and net, spending per category,
  pending transactions, latest transaction, consent expiry and sync status.
- **Event per new transaction.** An HA `event` entity fires `ny_transaktion` (new),
  `stor_transaktion` (large) or `indbetaling` (incoming), ready for automations.
- **Rule-based categories.** A YAML file of regular expressions, reloaded automatically.
- **Built-in web page.** Connect or renew the bank consent, see status, rename accounts and
  view recent transactions. As an HA app it sits in the sidebar behind your HA login.
- **Consent reminders.** A binary sensor turns on 14 days before the MitID consent expires.

## How it works

```
 Spar Nord / Nykredit (BEC PSD2)
            │  MitID consent (you, in the browser)
            ▼
 Enable Banking API  ◄──── signed JWT (your private key) ────┐
            │                                               │
            ▼                                               │
 ┌──────────────── Bank2HA ─────────────────────────────────┴──┐
 │  web UI (ingress)   scheduler (every 6 h)   categoriser      │
 │          │                 │                     │           │
 │          └──────► sync ◄───┘─────────────────────┘           │
 │                    │                                         │
 │        ┌───────────┴───────────┐                             │
 │        ▼                       ▼                             │
 │  SQLite / MariaDB /      MQTT discovery + state + events     │
 │  PostgreSQL                    │                             │
 └────────────────────────────────┼─────────────────────────────┘
                                  ▼
                           Home Assistant
```

1. **Consent.** You click *Connect* and log in with MitID at the bank. The bank grants a
   consent valid for up to the bank's maximum (typically 180 days). Enable Banking returns a
   session with the list of authorised accounts.
2. **Sync.** On a schedule (default every 6 hours) the app fetches balances and transactions
   for each enabled account. The first sync goes back `initial_history_days`. Later syncs
   start a few days before the last booked transaction (`OVERLAP_DAYS`) so late bookings
   are not missed.
3. **Storage.** New transactions are inserted, known ones are left untouched, and pending
   transactions are replaced on every sync. A balance snapshot is stored on every sync,
   which gives you balance history.
4. **Publishing.** A summary (balance, month totals, categories, latest transaction, consent
   status) is published as retained MQTT state. Each new booked transaction fires an HA
   event. Events are not fired for the initial history import.

Account identity uses Enable Banking's `identification_hash`. It stays the same when you
renew the consent, so history is kept across consents.

## Requirements

- An **Enable Banking** account (free for your own accounts in *restricted mode*).
- **MitID** access to the accounts.
- **Home Assistant** with the **MQTT** integration, normally the *Mosquitto broker* app.
- For the HA app: Home Assistant OS or Supervised on amd64 or aarch64.
- For standalone: Docker with Compose.

## Installation – Home Assistant OS

The step-by-step guide is in [`bank2ha/DOCS.md`](bank2ha/DOCS.md) (Danish). It is also shown
in the app's **Documentation** tab in HA. Short version:

1. **Enable Banking.** Create a *Production* application at
   <https://enablebanking.com/cp/applications> with redirect URL
   `http://localhost:8080/auth_redirect`, and download the `.pem` private key. Click
   **Activate by linking accounts** and link your accounts with MitID.
2. **MQTT.** Install and start the **Mosquitto broker** app, then configure the MQTT
   integration that HA discovers.
3. **Add this repository.** Go to *Settings → Apps → Install app → ⋮ → Repositories*, add
   `https://github.com/jenshinnerup-lab/bank2ha`, then install **Bank2HA**. Start it once so
   its config folder is created.
4. **Private key.** Copy the key to `/addon_configs/<id>_bank2ha/eb_private_key.pem`, for
   example with the *Terminal & SSH* app, and run `chmod 600` on it.
5. **Configure.** Enter your `application_id` on the **Configuration** tab, enable *Show in
   sidebar*, and restart the app.
6. **Connect.** Open **Bank** in the sidebar and click **Connect**. MitID opens in a new tab.
   Afterwards the browser lands on a `localhost` page that does not load. This is expected.
   Copy the full address, paste it into the field on the Bank page and click **Finish**.

## Installation – standalone Docker

For Home Assistant Container, or for running Bank2HA on another server:

```bash
git clone https://github.com/jenshinnerup-lab/bank2ha && cd bank2ha/standalone
mkdir -p data config secrets && chmod 700 data secrets
cp ~/Downloads/<application-id>.pem secrets/eb_private_key.pem
chmod 400 secrets/eb_private_key.pem && sudo chown 1000:1000 secrets/eb_private_key.pem data
cp .env.example .env                         # set EB_APPLICATION_ID and MQTT_*
cp ../bank2ha/rules.example.yaml config/rules.yaml
docker compose up -d --build
docker compose exec bank2ha python -m app.cli check
```

Then open <http://localhost:8080>. The web UI only listens on `127.0.0.1` by default. If you
set `BIND_ADDR=0.0.0.0`, also set `UI_PASSWORD`.

## Configuration options

| HA app option | Standalone env var | Default | Description |
|---|---|---|---|
| `application_id` | `EB_APPLICATION_ID` | – | Enable Banking application ID (UUID) |
| `private_key_file` | `EB_PRIVATE_KEY_PATH` | `eb_private_key.pem` | Private key file. For the HA app it is relative to `/addon_configs/<id>_bank2ha/` |
| `bank_name` | `ASPSP_NAME` | `Spar Nord` | Bank name as listed by Enable Banking, e.g. `Nykredit` |
| `bank_country` | `ASPSP_COUNTRY` | `DK` | Two-letter country code |
| `consent_days` | `CONSENT_DAYS` | `180` | Requested consent length, capped by the bank's maximum |
| `sync_interval_minutes` | `SYNC_INTERVAL_MINUTES` | `360` | Minutes between automatic syncs (60–1440) |
| `initial_history_days` | `INITIAL_HISTORY_DAYS` | `730` | History to fetch on first sync |
| `publish_transaction_details` | `PUBLISH_TRANSACTION_DETAILS` | `true` | Send counterparty and text to HA. If off, only amount and category are sent |
| `large_transaction_threshold` | `LARGE_TRANSACTION_THRESHOLD` | `2000` | Amount (DKK) at or above which a payment counts as large |
| `redirect_url` | `EB_REDIRECT_URL` | `http://localhost:8080/auth_redirect` | Must match the Enable Banking application |
| `database_url` | `DATABASE_URL` | SQLite in `/data` | SQLAlchemy URL, e.g. `mysql+pymysql://user:pw@core-mariadb/bank2ha` |
| – | `MQTT_HOST/PORT/USERNAME/PASSWORD/TLS` | – | Standalone only. The HA app gets MQTT credentials from the Supervisor |
| – | `OVERLAP_DAYS` | `10` | Days re-read before the last booked transaction |
| – | `UI_USERNAME` / `UI_PASSWORD` | `admin` / empty | HTTP Basic auth for the standalone web UI |

## Home Assistant entities and events

**Device "Bank2HA"**

| Entity | Description |
|---|---|
| `sensor.bank2ha_samtykke_dage_tilbage` | Days until the MitID consent expires (attributes: `valid_until`, `status`) |
| `binary_sensor.bank2ha_samtykke_kraever_fornyelse` | On if the consent is inactive or has less than 14 days left |
| `sensor.bank2ha_seneste_sync` | Timestamp of the last sync (attributes: `ok`, `message`) |
| `binary_sensor.bank2ha_sync_fejl` | On if the last sync failed |
| `button.bank2ha_synkroniser_nu` | Sync now (counts towards the bank's daily limit) |

**Device "Bank \<account\>"**, one per account. Entity IDs follow the account's display
name, e.g. `sensor.bank_budgetkonto_saldo`.

| Entity | Description |
|---|---|
| `…_saldo` | Balance (available balance preferred). All balance types as attributes. `state_class: total`, so HA keeps long-term statistics |
| `…_forbrug_denne_maned` | Spending this month. Spending per category as attributes |
| `…_indtaegter_denne_maned` | Income this month |
| `…_netto_denne_maned` | Net this month |
| `…_reserverede_posteringer` | Number of pending transactions (attribute: `amount`) |
| `…_seneste_transaktion` | Amount of the latest booked transaction (attributes: date, counterparty, description, category) |
| `event.…_transaktion` | Fires per new booked transaction |

**Event types and attributes**

| `event_type` | When |
|---|---|
| `ny_transaktion` | Outgoing payment below the threshold |
| `stor_transaktion` | Outgoing payment at or above `large_transaction_threshold` |
| `indbetaling` | Incoming payment |

Attributes: `amount` (signed), `currency`, `date`, `category`, and if enabled
`counterparty` and `description`.

Example automations (large payment, salary received, low balance, consent reminder), a
dashboard card and SQL examples are in [`ha/`](ha/).

## Categories

Categories come from `rules.yaml`. For the HA app the file is
`/addon_configs/<id>_bank2ha/rules.yaml`, created from
[`rules.example.yaml`](bank2ha/rules.example.yaml) on first start.

```yaml
default_in: Indtægt
default_out: Andet
rules:
  - category: Dagligvarer
    direction: out              # in | out | omit for both
    match: ["netto", "f[øo]tex", "rema"]
```

- Patterns are case-insensitive regular expressions matched against counterparty and text.
- The first matching rule wins.
- The file is reloaded when it changes. New rules apply to new transactions. Run
  `python -m app.cli recategorize` to update existing ones.

## Data model

| Table | Content |
|---|---|
| `accounts` | One row per account. Key: Enable Banking `identification_hash`. Holds the current session `uid`, IBAN, name, your display name, currency and an enabled flag |
| `transactions` | All transactions. `amount` is signed (negative = money out). `status` is `BOOK` (booked) or `PDNG` (pending). Booking, value and transaction dates, counterparty, description, bank code, balance after, category, original JSON in `raw_json`, `first_seen` and `last_seen` |
| `balances` | Balance snapshot per sync and balance type (`ITAV`, `CLBD`, …), which gives balance history |
| `bank_sessions` | Consents with status (`active`, `expired`, `superseded`) and expiry |
| `auth_states` | Short-lived login attempts, used to validate the `state` parameter |
| `sync_log` | One row per sync with result and number of new transactions |

**Deduplication.** If the bank provides `entry_reference`, the key is a hash of account,
reference and status. Otherwise it is a hash of the transaction's content plus an occurrence
number, so two identical purchases on the same day are both kept.

## Security and privacy

- **Read-only.** Only account information endpoints are used. Payment initiation is never
  called.
- **Private key.** For the HA app it lives in the app's own config folder. Standalone, it is
  mounted as a read-only Docker secret. It is never stored in the image or the database.
- **Authentication.** As an HA app the web UI is only reachable through HA ingress, behind
  your HA login, and no port is exposed. Standalone, it binds to `127.0.0.1` by default, with
  optional HTTP Basic auth.
- **Files and container.** The database file is created with mode `600`. The standalone
  container runs as a non-root user with a read-only root filesystem, `cap_drop: ALL` and
  `no-new-privileges`.
- **What leaves your network.** Only the calls to Enable Banking and the bank. No telemetry.
- **Home Assistant history.** The HA recorder stores sensor history. Set
  `publish_transaction_details: false` if counterparties and texts should not end up in
  HA's database.
- **Backups.** In the HA app the database is included in HA backups. Use encrypted backups.
  The stored session ID together with your private key gives access to account data until
  the consent expires, so treat backups as sensitive.
- **Encryption at rest.** Put the data on an encrypted disk (LUKS, BitLocker, ZFS native
  encryption) rather than relying on database-level encryption.
- **Revoking access.** Delete the application in the Enable Banking Control Panel, or
  revoke the consent in your netbank. Bank2HA stops working on the next sync.

## Limitations

- **Daily limit.** PSD2 typically allows **4 unattended requests per account per day**. The
  default 6-hour interval stays within that. Manual syncs count too.
- **Consent renewal.** The consent has to be renewed with MitID periodically, typically every
  180 days.
- **Login redirect.** The HA ingress page cannot receive the bank's redirect directly, so the
  redirect address has to be pasted back once per consent.
- **Restricted mode.** Only accounts linked in the Enable Banking Control Panel are
  returned.
- **Data quality.** Transaction texts and counterparty names depend on what the bank
  supplies via PSD2. They may differ from the netbank.

## Troubleshooting

| Message (app log) | Fix |
|---|---|
| `Mangler privat nøgle` (missing private key) | Put the `.pem` file in the app's config folder with the configured name |
| `Bank 'Spar Nord' ikke fundet` (bank not found) | The log lists available names. Set *bank name* to one of them, e.g. `Nykredit` |
| `Enable Banking API error 401` | Wrong application ID, or the key does not belong to that application |
| `Ukendt eller udløbet 'state'` (unknown or expired state) | Start *Connect* again and paste the address within an hour |
| `MQTT-service ikke fundet` (MQTT service not found) | Install and start Mosquitto broker, then restart Bank2HA |
| No accounts after login | Link the accounts in the Enable Banking Control Panel (restricted mode) |
| `Samtykke afvist af banken` (consent rejected by bank) | Renew the consent from the Bank page |

## Command line and API

```bash
python -m app.cli check          # verify key, application and bank name
python -m app.cli connect        # consent flow in the terminal
python -m app.cli sync           # sync now
python -m app.cli summary        # JSON summary
python -m app.cli recategorize   # re-apply rules.yaml to all transactions
```

Prefix the commands with `docker compose exec bank2ha` when running standalone.

The web app also exposes `GET /api/summary` and `GET /api/transactions?days=30&category=…`
as JSON. These use the same authentication as the UI.

## Repository layout

```
repository.yaml          Home Assistant app repository definition
bank2ha/                 The HA app (also the source for standalone)
  config.yaml            App manifest: options, ingress, MQTT service
  Dockerfile             App image
  DOCS.md                Installation guide shown in HA (Danish)
  app/                   Python source
    addon.py             HA entry point (options + MQTT credentials from Supervisor)
    eb_client.py         Enable Banking client (JWT, AIS endpoints)
    sync.py              Consent, sync, deduplication, summaries
    db.py                SQLAlchemy schema
    mqtt_ha.py           MQTT discovery, state and events
    categorize.py        rules.yaml categoriser
    web.py               Web UI, JSON API, scheduler
    cli.py               Command line tools
  rules.example.yaml     Default category rules
standalone/              Docker Compose setup
ha/                      Example automations, dashboard card, SQL sensors
```

## Disclaimer

Bank2HA is an independent project. It is not affiliated with or endorsed by Spar Nord,
Nykredit, BEC or Enable Banking. You use it with your own credentials and consent, under
Enable Banking's terms of service. Check that the numbers match your netbank before you
rely on them for decisions.
