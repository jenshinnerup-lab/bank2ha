"""Command line helpers:  python -m app.cli [check|connect|sync|summary|recategorize]"""
from __future__ import annotations

import json
import logging
import sys
from urllib.parse import parse_qs, urlparse

from .config import settings
from .db import make_engine
from .sync import BankSync

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    bank = BankSync(settings, make_engine(settings.database_url))
    if cmd == "check":
        app = bank.client.application()
        print("Application:", app.get("name"), "| aktiv:", app.get("active"), "| redirect:", app.get("redirect_urls"))
        aspsp = bank.client.find_aspsp()
        print("Bank fundet:", aspsp["name"], aspsp.get("country"),
              "| max samtykke (dage):", (aspsp.get("maximum_consent_validity") or 0) // 86400)
    elif cmd == "connect":
        print("Åbn denne adresse og log ind med MitID:\n\n", bank.start_consent(), "\n")
        url = input("Indsæt adressen du blev sendt til efter login: ").strip()
        q = parse_qs(urlparse(url).query)
        accs = bank.complete_consent(q["code"][0], (q.get("state") or [None])[0])
        print(f"Forbundet: {len(accs)} konti")
        print(bank.sync().message)
    elif cmd == "sync":
        r = bank.sync()
        print(r.message)
        return 0 if r.ok else 1
    elif cmd == "recategorize":
        from sqlalchemy import select, update
        from .db import transactions
        n = 0
        with bank.engine.begin() as c:
            for r in c.execute(select(transactions.c.id, transactions.c.counterparty,
                                      transactions.c.description, transactions.c.amount)).all():
                cat = bank.categorizer.categorize(r.counterparty, r.description, float(r.amount))
                c.execute(update(transactions).where(transactions.c.id == r.id).values(category=cat))
                n += 1
        print(f"{n} transaktioner genkategoriseret")
    elif cmd == "summary":
        print(json.dumps(bank.summary(), indent=2, ensure_ascii=False, default=str))
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
