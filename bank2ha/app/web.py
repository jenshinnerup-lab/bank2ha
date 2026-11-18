"""Web UI (consent flow + status), JSON API and background scheduler."""
from __future__ import annotations

import html
import os
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import and_, select, update

from .config import settings
from .db import accounts, make_engine, transactions
from .sync import BankSync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bank2ha")

engine = make_engine(settings.database_url)
bank = BankSync(settings, engine)
publisher = None
_wake = threading.Event()
_stop = threading.Event()


def _after_sync(result) -> None:  # noqa: ANN001
    if publisher:
        publisher.publish_summary(bank.summary())
        if result.new_transactions:
            publisher.publish_new_transactions(result.new_transactions)


def run_sync() -> None:
    _after_sync(bank.sync())


def request_sync() -> None:
    _wake.set()


def _scheduler() -> None:
    interval = max(60, settings.sync_interval_minutes) * 60
    # small delay so MQTT can connect first
    _wake.wait(5)
    while not _stop.is_set():
        _wake.clear()
        if bank.active_session():
            run_sync()
        elif publisher:
            publisher.publish_summary(bank.summary())
        _wake.wait(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global publisher
    if settings.mqtt_enabled:
        from .mqtt_ha import HAPublisher
        publisher = HAPublisher(settings, request_sync)
        publisher.start()
    t = threading.Thread(target=_scheduler, daemon=True, name="scheduler")
    t.start()
    yield
    _stop.set()
    _wake.set()
    if publisher:
        publisher.stop()


app = FastAPI(title="bank2ha", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
security = HTTPBasic(auto_error=False)


def auth(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not settings.ui_password:
        return
    ok = creds is not None and secrets.compare_digest(creds.username, settings.ui_username or "") \
        and secrets.compare_digest(creds.password, settings.ui_password)
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})


# ------------------------------------------------------------------ consent flow
def _setup_problems() -> list[str]:
    problems = []
    if not settings.eb_application_id:
        problems.append("Enable Banking application ID mangler")
    if not Path(settings.eb_private_key_path).exists():
        problems.append("Privat nøgle (.pem) mangler")
    return problems


def _error_page(msg: str, status_code: int = 400) -> HTMLResponse:
    return HTMLResponse(_page(f"<div class=card><p class=err>{html.escape(msg)}</p><p><a href=./>Tilbage</a></p></div>"),
                        status_code)


@app.get("/connect", dependencies=[Depends(auth)])
def connect():
    if _setup_problems():
        return RedirectResponse("./", status_code=303)
    try:
        return RedirectResponse(bank.start_consent(), status_code=303)
    except Exception as e:  # noqa: BLE001
        log.exception("Kunne ikke starte samtykke")
        return _error_page(f"Kunne ikke starte forbindelsen til banken: {e}")


@app.post("/setup", dependencies=[Depends(auth)])
def setup(application_id: str = Form(""), private_key: str = Form("")):
    import uuid as _uuid
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    application_id = application_id.strip()
    pem = private_key.strip().replace("\r\n", "\n")
    key_path = Path(settings.eb_private_key_path)
    try:
        if application_id:
            _uuid.UUID(application_id)
            id_file = key_path.parent / "application_id"
            id_file.write_text(application_id)
            os.chmod(id_file, 0o600)
            settings.eb_application_id = application_id
        if pem:
            load_pem_private_key(pem.encode(), password=None)
            key_path.write_text(pem + "\n")
            os.chmod(key_path, 0o600)
    except ValueError as e:
        return _error_page(f"Ugyldig værdi: {'application ID skal være et UUID' if 'UUID' in str(e) or 'hexadecimal' in str(e) else 'nøglen kunne ikke læses som en PEM privat nøgle'}")
    except OSError as e:
        return _error_page(f"Kunne ikke gemme: {e}")
    bank.client._jwt = None  # force new token with new key/id
    log.info("Opsætning gemt (application ID: %s, nøgle: %s)", bool(application_id), bool(pem))
    return RedirectResponse("./?setup=1", status_code=303)


def _finish(code: str | None, state: str | None, error: str | None = None):
    if error or not code:
        return HTMLResponse(_page(f"<p class=err>Banken returnerede en fejl: {html.escape(error or 'ingen code')}</p>"), 400)
    try:
        bank.complete_consent(code, state)
    except Exception as e:  # noqa: BLE001
        log.exception("Samtykke fejlede")
        return HTMLResponse(_page(f"<p class=err>Kunne ikke oprette session: {html.escape(str(e))}</p>"), 400)
    # first sync right away – many banks only give long history right after SCA
    threading.Thread(target=run_sync, daemon=True).start()
    return RedirectResponse("./?connected=1", status_code=303)


@app.get("/auth_redirect", dependencies=[Depends(auth)])
def auth_redirect(code: str | None = None, state: str | None = None, error: str | None = None,
                  error_description: str | None = None):
    return _finish(code, state, error_description or error)


@app.post("/paste", dependencies=[Depends(auth)])
def paste(url: str = Form(...)):
    q = parse_qs(urlparse(url.strip()).query)
    return _finish((q.get("code") or [None])[0], (q.get("state") or [None])[0], (q.get("error") or [None])[0])


# ------------------------------------------------------------------ actions
@app.post("/sync", dependencies=[Depends(auth)])
def sync_now():
    request_sync()
    return RedirectResponse("./?sync=1", status_code=303)


@app.post("/account", dependencies=[Depends(auth)])
def edit_account(account_id: str = Form(...), display_name: str = Form(""), enabled: str | None = Form(None)):
    with engine.begin() as c:
        c.execute(update(accounts).where(accounts.c.id == account_id)
                  .values(display_name=display_name.strip() or None, enabled=enabled == "on"))
    if publisher:
        publisher.publish_summary(bank.summary())
    return RedirectResponse("./", status_code=303)


# ------------------------------------------------------------------ JSON API
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/summary", dependencies=[Depends(auth)])
def api_summary():
    return JSONResponse(bank.summary())


@app.get("/api/transactions", dependencies=[Depends(auth)])
def api_transactions(days: int = 30, category: str | None = None):
    since = date.today() - timedelta(days=min(days, 3650))
    q = select(transactions.c.account_id, transactions.c.status, transactions.c.booking_date, transactions.c.amount,
               transactions.c.currency, transactions.c.counterparty, transactions.c.description,
               transactions.c.category).where(transactions.c.booking_date >= since)
    if category:
        q = q.where(transactions.c.category == category)
    with engine.connect() as c:
        rows = c.execute(q.order_by(transactions.c.booking_date.desc())).mappings().all()
    return [{**r, "booking_date": r["booking_date"].isoformat() if r["booking_date"] else None,
             "amount": float(r["amount"])} for r in rows]


# ------------------------------------------------------------------ UI
CSS = """
body{font-family:system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#1d2433;background:#f6f7f9}
h1{font-size:1.4rem}.card{background:#fff;border-radius:10px;padding:1rem 1.25rem;margin:1rem 0;box-shadow:0 1px 3px #0001}
.ok{color:#137333}.err{color:#b3261e}.muted{color:#667}table{width:100%;border-collapse:collapse}
td,th{padding:.35rem .4rem;border-bottom:1px solid #eee;text-align:left;font-size:.92rem}td.num{text-align:right;font-variant-numeric:tabular-nums}
button,.btn{background:#1d4ed8;color:#fff;border:0;border-radius:6px;padding:.5rem .9rem;cursor:pointer;text-decoration:none;font-size:.95rem}
input[type=text]{padding:.4rem;border:1px solid #ccd;border-radius:6px;width:60%}
"""


def _page(body: str) -> str:
    return f"<!doctype html><html lang=da><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>bank2ha</title><style>{CSS}</style><body><h1>bank2ha · Spar Nord → Home Assistant</h1>{body}</body></html>"


def _fmt(v: float | None, cur: str = "DKK") -> str:
    if v is None:
        return "–"
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {cur}"


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
def index(request: Request, connected: int | None = None, sync: int | None = None, setup: int | None = None):
    s = bank.summary()
    e = html.escape
    parts = []
    problems = _setup_problems()
    if setup and not problems:
        parts.append("<div class='card ok'>Opsætningen er gemt – du kan nu forbinde banken.</div>")
    if problems:
        missing = "".join(f"<li>{e(p)}</li>" for p in problems)
        id_field = "" if settings.eb_application_id else (
            "<p><label>Application ID<br><input type=text name=application_id "
            "placeholder='xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx' style=width:100%></label></p>")
        key_field = "" if Path(settings.eb_private_key_path).exists() else (
            "<p><label>Privat nøgle – åbn .pem-filen i en teksteditor og indsæt hele indholdet<br>"
            "<textarea name=private_key rows=8 style='width:100%;font-family:monospace' "
            "placeholder='-----BEGIN PRIVATE KEY-----'></textarea></label></p>")
        parts.append(f"""<div class=card><h3>Opsætning mangler</h3><ul class=err>{missing}</ul>
<p class=muted>Begge dele får du på <a href=https://enablebanking.com/cp/applications target=_blank rel=noopener>enablebanking.com → Applications</a>.
Nøglen gemmes kun i appens egen mappe med rettigheder 600.</p>
<form method=post action=setup>{id_field}{key_field}<button>Gem</button></form></div>""")
    if connected:
        parts.append("<div class='card ok'>Banken er forbundet – første synkronisering kører nu (kan tage et minut).</div>")
    if sync:
        parts.append("<div class='card'>Synkronisering startet.</div>")

    days = s.get("consent_days_left")
    if s["consent_status"] == "active":
        cls = "ok" if (days or 0) >= 14 else "err"
        consent = f"<span class={cls}>Aktivt</span> – udløber {e(s['consent_valid_until'][:10])} ({days} dage)"
        btn = "Forny samtykke"
    else:
        consent = f"<span class=err>{e(s['consent_status'])}</span>"
        btn = "Forbind Spar Nord (MitID)"
    sync_line = "Aldrig"
    if s.get("last_sync"):
        cls = "ok" if s.get("last_sync_ok") else "err"
        sync_line = f"{e(s['last_sync'][:19].replace('T', ' '))} UTC – <span class={cls}>{e(s.get('last_sync_message') or '')}</span>"
    parts.append(f"""<div class=card><p><b>Samtykke:</b> {consent}</p><p><b>Seneste sync:</b> {sync_line}</p>
<p>{'' if problems else f'<a class=btn href=connect target=_blank rel=noopener>{btn}</a>'} <form method=post action=sync style=display:inline><button>Synkronisér nu</button></form></p>
<details open><summary class=muted>Efter MitID-login</summary>
<p class=muted>Login åbner i en ny fane. Efter MitID sendes du til <code>{e(settings.eb_redirect_url)}</code> – siden kan ikke åbnes, det er forventet. Kopiér hele adressen fra adresselinjen og indsæt den her:</p>
<form method=post action=paste><input type=text name=url placeholder="http://localhost:8080/auth_redirect?code=..."> <button>Fuldfør</button></form></details></div>""")

    with engine.connect() as c:
        all_accs = c.execute(select(accounts)).mappings().all()
    summ = {a["id"]: a for a in s["accounts"]}
    for acc in all_accs:
        a = summ.get(acc["id"])
        cur = acc["currency"] or "DKK"
        rows = ""
        if a:
            rows = (f"<tr><td>Saldo</td><td class=num>{_fmt(a['balance'], cur)}</td></tr>"
                    f"<tr><td>Forbrug denne måned</td><td class=num>{_fmt(a['month_expenses'], cur)}</td></tr>"
                    f"<tr><td>Indtægter denne måned</td><td class=num>{_fmt(a['month_income'], cur)}</td></tr>")
            for cat, amt in list(a["month_by_category"].items())[:8]:
                rows += f"<tr><td class=muted>&nbsp;&nbsp;{e(cat)}</td><td class='num muted'>{_fmt(amt, cur)}</td></tr>"
        checked = "checked" if acc["enabled"] else ""
        parts.append(f"""<div class=card><h3>{e(acc['display_name'] or acc['name'] or '?')} <span class=muted>{e((acc['iban'] or '')[:4])}…{e((acc['iban'] or '')[-4:])}</span></h3>
<table>{rows}</table>
<form method=post action=account style=margin-top:.6rem><input type=hidden name=account_id value="{e(acc['id'])}"><input type=text name=display_name placeholder="Kaldenavn i HA" value="{e(acc['display_name'] or '')}">
<label><input type=checkbox name=enabled {checked}> aktiv</label> <button>Gem</button></form></div>""")

    with engine.connect() as c:
        recent = c.execute(select(transactions).where(transactions.c.booking_date >= date.today() - timedelta(days=14))
                           .order_by(transactions.c.booking_date.desc()).limit(25)).mappings().all()
    if recent:
        tr = "".join(
            f"<tr><td>{r['booking_date'] or ''}</td><td>{e(r['counterparty'] or r['description'] or '')[:60]}</td>"
            f"<td class=muted>{e(r['category'] or '')}</td><td class=num>{_fmt(float(r['amount']), r['currency'] or 'DKK')}"
            f"{' <span class=muted>(res.)</span>' if r['status'] != 'BOOK' else ''}</td></tr>" for r in recent)
        parts.append(f"<div class=card><h3>Seneste 14 dage</h3><table>{tr}</table></div>")
    return _page("".join(parts))
