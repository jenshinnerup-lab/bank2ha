"""Sync logic: consent handling, fetching balances and transactions, and summaries for HA."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.engine import Engine

from .categorize import Categorizer
from .config import Settings
from .db import accounts, auth_states, balances, bank_sessions, sync_log, transactions, utcnow
from .eb_client import EnableBankingClient, EnableBankingError

log = logging.getLogger(__name__)

# Order of preference when picking "the" balance to show
BALANCE_PREFERENCE = ["ITAV", "CLAV", "XPCD", "ITBD", "CLBD", "OPBD", "PRCD", "OTHR"]


def _d(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; we always store UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _account_key(acc: dict) -> str:
    if acc.get("identification_hash"):
        return acc["identification_hash"]
    ident = (acc.get("account_id") or {}).get("iban") or acc.get("uid")
    return "h_" + hashlib.sha256(str(ident).encode()).hexdigest()[:40]


@dataclass
class SyncResult:
    ok: bool = True
    new_transactions: list[dict] = field(default_factory=list)
    message: str = ""


class BankSync:
    def __init__(self, settings: Settings, engine: Engine, client: EnableBankingClient | None = None):
        self.s = settings
        self.engine = engine
        self.client = client or EnableBankingClient(settings)
        self.categorizer = Categorizer(settings.rules_path)
        self.tz = ZoneInfo(settings.timezone)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ consent
    def start_consent(self) -> str:
        url, state, valid_until = self.client.start_auth()
        with self.engine.begin() as c:
            # clean up stale auth attempts
            c.execute(delete(auth_states).where(auth_states.c.created_at < utcnow() - timedelta(hours=1)))
            c.execute(insert(auth_states).values(state=state, created_at=utcnow(), valid_until=valid_until))
        return url

    def complete_consent(self, code: str, state: str | None) -> list[dict]:
        with self.engine.begin() as c:
            if state is not None:
                row = c.execute(select(auth_states).where(auth_states.c.state == state)).first()
                if not row:
                    raise ValueError("Ukendt eller udløbet 'state' – start forbindelsen forfra.")
                c.execute(delete(auth_states).where(auth_states.c.state == state))

        sess = self.client.create_session(code)
        session_id = sess["session_id"]
        access = sess.get("access") or {}
        valid_until = access.get("valid_until")
        valid_until_dt = datetime.fromisoformat(valid_until.replace("Z", "+00:00")) if valid_until else None
        aspsp = sess.get("aspsp") or {}

        with self.engine.begin() as c:
            # older sessions are superseded by the new consent
            c.execute(update(bank_sessions).where(bank_sessions.c.status == "active").values(status="superseded"))
            c.execute(insert(bank_sessions).values(
                session_id=session_id, aspsp_name=aspsp.get("name", self.s.aspsp_name),
                aspsp_country=aspsp.get("country", self.s.aspsp_country), valid_until=valid_until_dt,
                status="active", created_at=utcnow(),
            ))
            stored = []
            for acc in sess.get("accounts", []):
                key = _account_key(acc)
                acc_id = acc.get("account_id") or {}
                other = acc_id.get("other") or {}
                values = dict(
                    current_uid=acc["uid"], session_id=session_id, iban=acc_id.get("iban"),
                    bban=other.get("identification") if isinstance(other, dict) else None,
                    name=acc.get("name") or acc.get("product"), currency=acc.get("currency"),
                    product=acc.get("product"), cash_account_type=acc.get("cash_account_type"),
                    updated_at=utcnow(),
                )
                exists = c.execute(select(accounts.c.id).where(accounts.c.id == key)).first()
                if exists:
                    c.execute(update(accounts).where(accounts.c.id == key).values(**values))
                else:
                    c.execute(insert(accounts).values(id=key, enabled=True, **values))
                stored.append({"id": key, **values})
        log.info("Nyt samtykke %s med %d konti (gyldigt til %s)", session_id, len(stored), valid_until)
        return stored

    def active_session(self) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(
                select(bank_sessions).where(bank_sessions.c.status == "active")
                .order_by(bank_sessions.c.created_at.desc())
            ).mappings().first()
        if not row:
            return None
        row = dict(row)
        row["valid_until"] = _aware(row["valid_until"])
        return row

    def _mark_session(self, session_id: str, status: str) -> None:
        with self.engine.begin() as c:
            c.execute(update(bank_sessions).where(bank_sessions.c.session_id == session_id).values(status=status))

    # ------------------------------------------------------------------ parsing
    def _parse_tx(self, account_id: str, tx: dict) -> dict:
        amt_obj = tx.get("transaction_amount") or {}
        amount = abs(Decimal(str(amt_obj.get("amount", "0"))))
        cdi = tx.get("credit_debit_indicator") or ("DBIT" if str(amt_obj.get("amount", "")).startswith("-") else "CRDT")
        if cdi == "DBIT":
            amount = -amount
        party = (tx.get("creditor") if cdi == "DBIT" else tx.get("debtor")) or {}
        counterparty = party.get("name") if isinstance(party, dict) else None
        remit = tx.get("remittance_information") or []
        description = " ".join(r for r in remit if r) if isinstance(remit, list) else str(remit)
        description = description or tx.get("note") or ""
        btc = tx.get("bank_transaction_code") or {}
        bal_after = (tx.get("balance_after_transaction") or {}).get("amount")
        status = tx.get("status") or "BOOK"
        return dict(
            account_id=account_id,
            status=status,
            booking_date=_d(tx.get("booking_date")),
            value_date=_d(tx.get("value_date")),
            transaction_date=_d(tx.get("transaction_date")),
            amount=amount,
            currency=amt_obj.get("currency"),
            counterparty=(counterparty or "")[:200] or None,
            description=description,
            entry_reference=tx.get("entry_reference"),
            transaction_id=tx.get("transaction_id"),
            bank_transaction_code=(btc.get("description") or btc.get("code")) if isinstance(btc, dict) else None,
            balance_after=Decimal(str(bal_after)) if bal_after is not None else None,
            category=self.categorizer.categorize(counterparty, description, float(amount)),
            raw_json=json.dumps(tx, ensure_ascii=False, sort_keys=True),
        )

    @staticmethod
    def _assign_ids(rows: list[dict]) -> None:
        """Deterministic dedup keys. entry_reference when present, otherwise content hash + occurrence no."""
        seen: Counter = Counter()
        for r in rows:
            if r["entry_reference"]:
                base = f"{r['account_id']}|ref|{r['entry_reference']}|{r['status']}"
            else:
                d = r["booking_date"] or r["transaction_date"] or r["value_date"]
                base = f"{r['account_id']}|{r['status']}|{d}|{r['amount']}|{r['counterparty']}|{r['description']}"
            seen[base] += 1
            key = f"{base}|{seen[base]}"
            r["id"] = hashlib.sha256(key.encode()).hexdigest()

    # ------------------------------------------------------------------ sync
    def sync(self) -> SyncResult:
        if not self._lock.acquire(blocking=False):
            return SyncResult(ok=False, message="Sync kører allerede")
        started = utcnow()
        result = SyncResult()
        try:
            sess = self.active_session()
            if not sess:
                result.ok, result.message = False, "Intet aktivt samtykke – forbind banken i web-UI'et."
                return result
            if sess["valid_until"] and sess["valid_until"] < utcnow():
                self._mark_session(sess["session_id"], "expired")
                result.ok, result.message = False, "Samtykket er udløbet – forny det i web-UI'et."
                return result

            with self.engine.connect() as c:
                accs = c.execute(
                    select(accounts).where(and_(accounts.c.session_id == sess["session_id"], accounts.c.enabled.is_(True)))
                ).mappings().all()

            for acc in accs:
                try:
                    result.new_transactions += self._sync_account(dict(acc))
                except EnableBankingError as e:
                    if e.status in (401, 403) or "expired" in e.body.lower() or "revoked" in e.body.lower():
                        self._mark_session(sess["session_id"], "expired")
                        result.ok, result.message = False, f"Samtykke afvist af banken ({e.status}) – forny det."
                        break
                    raise
            if result.ok:
                result.message = f"{len(accs)} konti synkroniseret, {len(result.new_transactions)} nye transaktioner"
        except Exception as e:  # noqa: BLE001
            log.exception("Sync fejlede")
            result.ok, result.message = False, str(e)
        finally:
            with self.engine.begin() as c:
                c.execute(insert(sync_log).values(
                    started_at=started, finished_at=utcnow(), ok=result.ok,
                    new_transactions=len(result.new_transactions), message=result.message[:2000],
                ))
            self._lock.release()
        log.info("Sync: %s", result.message)
        return result

    def _sync_account(self, acc: dict) -> list[dict]:
        uid, acc_id = acc["current_uid"], acc["id"]
        now = utcnow()

        # balances (snapshot history)
        bal_rows = []
        for b in self.client.balances(uid):
            ba = b.get("balance_amount") or {}
            bal_rows.append(dict(
                account_id=acc_id, balance_type=b.get("balance_type"), name=b.get("name"),
                amount=Decimal(str(ba.get("amount", "0"))), currency=ba.get("currency"),
                reference_date=_d(b.get("reference_date")), fetched_at=now,
            ))

        # transactions window
        with self.engine.connect() as c:
            last = c.execute(
                select(func.max(transactions.c.booking_date))
                .where(and_(transactions.c.account_id == acc_id, transactions.c.status == "BOOK"))
            ).scalar()
        if last:
            date_from = last - timedelta(days=self.s.overlap_days)
        else:
            date_from = date.today() - timedelta(days=self.s.initial_history_days)

        rows = [self._parse_tx(acc_id, t) for t in self.client.transactions(uid, date_from=date_from.isoformat())]
        self._assign_ids(rows)

        new_rows: list[dict] = []
        with self.engine.begin() as c:
            if bal_rows:
                c.execute(insert(balances), bal_rows)
            # pending transactions are volatile: replace them on every sync
            c.execute(delete(transactions).where(and_(transactions.c.account_id == acc_id, transactions.c.status != "BOOK")))
            ids = [r["id"] for r in rows]
            existing: set[str] = set()
            for i in range(0, len(ids), 500):
                existing |= set(c.execute(select(transactions.c.id).where(transactions.c.id.in_(ids[i:i + 500]))).scalars())
            to_insert = []
            for r in rows:
                if r["id"] in existing:
                    continue
                r.update(first_seen=now, last_seen=now)
                to_insert.append(r)
                if r["status"] == "BOOK":
                    new_rows.append(r)
            if to_insert:
                c.execute(insert(transactions), to_insert)
            existing_list = list(existing)
            for i in range(0, len(existing_list), 500):
                part = existing_list[i:i + 500]
                c.execute(update(transactions).where(transactions.c.id.in_(part)).values(last_seen=now))
        log.info("Konto %s: %d hentet, %d nye", acc.get("display_name") or acc.get("name"), len(rows), len(to_insert))
        # first import of history should not fire hundreds of HA events
        return new_rows if last else []

    # ------------------------------------------------------------------ summaries
    def summary(self) -> dict:
        """Aggregated view used by MQTT/HA and the JSON API."""
        today = datetime.now(self.tz).date()
        month_start = today.replace(day=1)
        sess = self.active_session()
        consent_days_left = None
        if sess and sess["valid_until"]:
            consent_days_left = max(0, (sess["valid_until"] - utcnow()).days)

        out: dict = {
            "consent_status": sess["status"] if sess else "none",
            "consent_valid_until": sess["valid_until"].isoformat() if sess and sess["valid_until"] else None,
            "consent_days_left": consent_days_left,
            "accounts": [],
        }
        with self.engine.connect() as c:
            last_sync = c.execute(select(sync_log).order_by(sync_log.c.id.desc())).mappings().first()
            if last_sync:
                out["last_sync"] = _aware(last_sync["finished_at"]).isoformat()
                out["last_sync_ok"] = bool(last_sync["ok"])
                out["last_sync_message"] = last_sync["message"]

            accs = c.execute(select(accounts).where(accounts.c.enabled.is_(True))).mappings().all()
            for acc in accs:
                aid = acc["id"]
                latest_fetch = c.execute(select(func.max(balances.c.fetched_at)).where(balances.c.account_id == aid)).scalar()
                bal_map = {}
                if latest_fetch:
                    for b in c.execute(select(balances).where(and_(balances.c.account_id == aid, balances.c.fetched_at == latest_fetch))).mappings():
                        bal_map[b["balance_type"]] = float(b["amount"])
                balance = next((bal_map[t] for t in BALANCE_PREFERENCE if t in bal_map), None)
                if balance is None and bal_map:
                    balance = next(iter(bal_map.values()))

                month_rows = c.execute(
                    select(transactions.c.amount, transactions.c.category)
                    .where(and_(transactions.c.account_id == aid, transactions.c.status == "BOOK",
                                transactions.c.booking_date >= month_start))
                ).all()
                income = sum(float(a) for a, _ in month_rows if a > 0)
                expenses = -sum(float(a) for a, _ in month_rows if a < 0)
                by_cat: dict[str, float] = defaultdict(float)
                for a, cat in month_rows:
                    if a < 0:
                        by_cat[cat or "Andet"] += -float(a)

                last_tx = c.execute(
                    select(transactions).where(and_(transactions.c.account_id == aid, transactions.c.status == "BOOK"))
                    .order_by(transactions.c.booking_date.desc(), transactions.c.first_seen.desc())
                ).mappings().first()
                pending = c.execute(
                    select(func.count(), func.coalesce(func.sum(transactions.c.amount), 0))
                    .where(and_(transactions.c.account_id == aid, transactions.c.status != "BOOK"))
                ).first()

                out["accounts"].append({
                    "id": aid,
                    "slug": _slug(acc["display_name"] or acc["name"] or aid[:8]),
                    "name": acc["display_name"] or acc["name"],
                    "iban_masked": _mask(acc["iban"]),
                    "currency": acc["currency"] or "DKK",
                    "balance": balance,
                    "balances": bal_map,
                    "month_income": round(income, 2),
                    "month_expenses": round(expenses, 2),
                    "month_net": round(income - expenses, 2),
                    "month_by_category": {k: round(v, 2) for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])},
                    "pending_count": int(pending[0]),
                    "pending_amount": float(pending[1] or 0),
                    "last_transaction": _tx_public(last_tx) if last_tx else None,
                })
        return out


def _slug(s: str) -> str:
    import re
    s = s.lower().replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:40] or "konto"


def _mask(iban: str | None) -> str | None:
    if not iban:
        return None
    return f"{iban[:4]}…{iban[-4:]}"


def _tx_public(tx) -> dict:
    return {
        "date": tx["booking_date"].isoformat() if tx["booking_date"] else None,
        "amount": float(tx["amount"]),
        "currency": tx["currency"],
        "counterparty": tx["counterparty"],
        "description": tx["description"],
        "category": tx["category"],
    }
