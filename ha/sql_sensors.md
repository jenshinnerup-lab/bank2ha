# Alternativ: læs direkte fra databasen med HA's SQL-integration

MQTT-sensorerne dækker det meste. Vil du lave egne opgørelser, kan HA's indbyggede
**SQL**-integration (Indstillinger → Enheder & tjenester → Tilføj → SQL) læse direkte
fra databasen.

- **SQLite**: HA skal kunne se filen, fx `sqlite:////share/bank2ha/bank.db` (kræver at
  `data/` er monteret ind i HA – ellers brug PostgreSQL/MariaDB).
- **MariaDB/PostgreSQL**: sæt `DATABASE_URL` i `.env` til samme server som HA og lav en
  *read-only* bruger til HA:

```sql
-- PostgreSQL
CREATE ROLE ha_reader LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE bank2ha TO ha_reader;
GRANT USAGE ON SCHEMA public TO ha_reader;
GRANT SELECT ON transactions, accounts, balances TO ha_reader;
```

## Eksempler på forespørgsler

Forbrug på dagligvarer denne måned (kolonne: `forbrug`):

```sql
SELECT ROUND(-SUM(amount), 2) AS forbrug
FROM transactions
WHERE status = 'BOOK' AND category = 'Dagligvarer' AND amount < 0
  AND booking_date >= date('now', 'start of month');      -- SQLite
--  AND booking_date >= date_trunc('month', current_date);  -- PostgreSQL
```

Gennemsnitligt månedsforbrug de sidste 6 måneder:

```sql
SELECT ROUND(-SUM(amount) / 6.0, 2) AS gennemsnit
FROM transactions
WHERE status = 'BOOK' AND amount < 0
  AND category NOT IN ('Overførsel')
  AND booking_date >= date('now', 'start of month', '-6 months')
  AND booking_date <  date('now', 'start of month');
```

Elbil-opladning i år:

```sql
SELECT ROUND(-SUM(amount), 2) AS opladning
FROM transactions
WHERE category = 'Elbil-opladning' AND status = 'BOOK'
  AND booking_date >= date('now', 'start of year');
```
