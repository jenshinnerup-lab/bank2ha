"""Configuration loaded from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    """Read env var; supports NAME_FILE (Docker secrets) as an alternative."""
    file_var = os.environ.get(f"{name}_FILE")
    if file_var and Path(file_var).exists():
        return Path(file_var).read_text().strip()
    return os.environ.get(name, default)


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # Enable Banking
    eb_api_origin: str = field(default_factory=lambda: _env("EB_API_ORIGIN", "https://api.enablebanking.com"))
    eb_application_id: str = field(default_factory=lambda: _env("EB_APPLICATION_ID", "") or "")
    eb_private_key_path: str = field(default_factory=lambda: _env("EB_PRIVATE_KEY_PATH", "/run/secrets/eb_private_key") or "")
    eb_redirect_url: str = field(default_factory=lambda: _env("EB_REDIRECT_URL", "http://localhost:8080/auth_redirect") or "")
    aspsp_name: str = field(default_factory=lambda: _env("ASPSP_NAME", "Spar Nord") or "Spar Nord")
    aspsp_country: str = field(default_factory=lambda: _env("ASPSP_COUNTRY", "DK") or "DK")
    psu_type: str = field(default_factory=lambda: _env("PSU_TYPE", "personal") or "personal")
    # Max consent days we ask for (capped by the bank's maximum_consent_validity)
    consent_days: int = field(default_factory=lambda: int(_env("CONSENT_DAYS", "180") or 180))

    # Storage
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:////data/bank.db") or "")

    # Sync
    sync_interval_minutes: int = field(default_factory=lambda: int(_env("SYNC_INTERVAL_MINUTES", "360") or 360))
    initial_history_days: int = field(default_factory=lambda: int(_env("INITIAL_HISTORY_DAYS", "730") or 730))
    overlap_days: int = field(default_factory=lambda: int(_env("OVERLAP_DAYS", "10") or 10))
    rules_path: str = field(default_factory=lambda: _env("RULES_PATH", "/config/rules.yaml") or "")

    # MQTT / Home Assistant
    mqtt_enabled: bool = field(default_factory=lambda: _bool("MQTT_ENABLED", True))
    mqtt_host: str = field(default_factory=lambda: _env("MQTT_HOST", "localhost") or "localhost")
    mqtt_port: int = field(default_factory=lambda: int(_env("MQTT_PORT", "1883") or 1883))
    mqtt_username: str | None = field(default_factory=lambda: _env("MQTT_USERNAME"))
    mqtt_password: str | None = field(default_factory=lambda: _env("MQTT_PASSWORD"))
    mqtt_tls: bool = field(default_factory=lambda: _bool("MQTT_TLS", False))
    mqtt_base_topic: str = field(default_factory=lambda: _env("MQTT_BASE_TOPIC", "bank2ha") or "bank2ha")
    ha_discovery_prefix: str = field(default_factory=lambda: _env("HA_DISCOVERY_PREFIX", "homeassistant") or "homeassistant")
    # Publish counterparty/text of the latest transaction to HA (privacy: HA recorder keeps history)
    publish_transaction_details: bool = field(default_factory=lambda: _bool("PUBLISH_TRANSACTION_DETAILS", True))
    large_transaction_threshold: float = field(default_factory=lambda: float(_env("LARGE_TRANSACTION_THRESHOLD", "2000") or 2000))

    # Web UI
    ui_username: str | None = field(default_factory=lambda: _env("UI_USERNAME", "admin"))
    ui_password: str | None = field(default_factory=lambda: _env("UI_PASSWORD"))
    timezone: str = field(default_factory=lambda: _env("TZ", "Europe/Copenhagen") or "Europe/Copenhagen")


settings = Settings()
