"""Minimal Enable Banking API client (AIS only - read-only, no payments)."""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import jwt as pyjwt
import requests

from .config import Settings

log = logging.getLogger(__name__)


class EnableBankingError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Enable Banking API error {status}: {body[:500]}")
        self.status = status
        self.body = body


class EnableBankingClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._jwt: str | None = None
        self._jwt_exp = 0
        self._http = requests.Session()

    # --- auth -------------------------------------------------------------
    def _token(self) -> str:
        now = int(time.time())
        if self._jwt and now < self._jwt_exp - 60:
            return self._jwt
        with open(self.s.eb_private_key_path, "rb") as f:
            key = f.read()
        exp = now + 3600
        self._jwt = pyjwt.encode(
            {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": exp},
            key,
            algorithm="RS256",
            headers={"kid": self.s.eb_application_id},
        )
        self._jwt_exp = exp
        return self._jwt

    def _req(self, method: str, path: str, **kw) -> Any:
        headers = {"Authorization": f"Bearer {self._token()}"}
        headers.update(kw.pop("headers", {}) or {})
        url = f"{self.s.eb_api_origin}{path}"
        r = self._http.request(method, url, headers=headers, timeout=60, **kw)
        if r.status_code >= 400:
            raise EnableBankingError(r.status_code, r.text)
        return r.json() if r.content else None

    # --- endpoints --------------------------------------------------------
    def application(self) -> dict:
        return self._req("GET", "/application")

    def find_aspsp(self) -> dict:
        data = self._req("GET", "/aspsps", params={"country": self.s.aspsp_country})
        aspsps = data.get("aspsps", [])
        wanted = self.s.aspsp_name.lower()
        for a in aspsps:
            if a.get("name", "").lower() == wanted:
                return a
        for a in aspsps:  # fuzzy fallback, e.g. "Spar Nord Bank"
            if wanted in a.get("name", "").lower():
                return a
        names = ", ".join(sorted(a.get("name", "?") for a in aspsps))
        raise RuntimeError(f"Bank '{self.s.aspsp_name}' ikke fundet i {self.s.aspsp_country}. Muligheder: {names}")

    def start_auth(self) -> tuple[str, str, datetime]:
        """Returns (auth_url, state, valid_until)."""
        aspsp = self.find_aspsp()
        max_validity = aspsp.get("maximum_consent_validity")  # seconds
        days = self.s.consent_days
        if max_validity:
            days = min(days, int(max_validity) // 86400)
        valid_until = datetime.now(timezone.utc) + timedelta(days=days) - timedelta(minutes=5)
        state = str(uuid.uuid4())
        body = {
            "access": {"valid_until": valid_until.isoformat()},
            "aspsp": {"name": aspsp["name"], "country": aspsp.get("country", self.s.aspsp_country)},
            "state": state,
            "redirect_url": self.s.eb_redirect_url,
            "psu_type": self.s.psu_type,
        }
        data = self._req("POST", "/auth", json=body)
        return data["url"], state, valid_until

    def create_session(self, code: str) -> dict:
        return self._req("POST", "/sessions", json={"code": code})

    def get_session(self, session_id: str) -> dict:
        return self._req("GET", f"/sessions/{session_id}")

    def delete_session(self, session_id: str) -> None:
        self._req("DELETE", f"/sessions/{session_id}")

    def account_details(self, account_uid: str) -> dict:
        return self._req("GET", f"/accounts/{account_uid}/details")

    def balances(self, account_uid: str) -> list[dict]:
        return self._req("GET", f"/accounts/{account_uid}/balances").get("balances", [])

    def transactions(self, account_uid: str, date_from: str | None = None, date_to: str | None = None) -> Iterator[dict]:
        params: dict[str, str] = {}
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        while True:
            data = self._req("GET", f"/accounts/{account_uid}/transactions", params=params)
            yield from data.get("transactions", [])
            ck = data.get("continuation_key")
            if not ck:
                break
            params["continuation_key"] = ck
