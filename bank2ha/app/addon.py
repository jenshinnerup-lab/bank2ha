"""Entry point when running as a Home Assistant app (add-on).

Reads /data/options.json and the MQTT service credentials from the Supervisor,
exports them as environment variables and starts the web server.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

OPTIONS = Path("/data/options.json")
CONFIG_DIR = Path("/config")          # addon_config map -> /addon_configs/<slug> on the host


def _supervisor_mqtt() -> dict | None:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    req = urllib.request.Request("http://supervisor/services/mqtt", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r).get("data")
    except Exception as e:  # noqa: BLE001
        print(f"[bank2ha] MQTT-service ikke fundet ({e}). Er Mosquitto-appen installeret?", file=sys.stderr)
        return None


def main() -> None:
    opts = json.loads(OPTIONS.read_text()) if OPTIONS.exists() else {}
    env = os.environ
    env["EB_APPLICATION_ID"] = str(opts.get("application_id", ""))
    env["EB_PRIVATE_KEY_PATH"] = str(CONFIG_DIR / opts.get("private_key_file", "eb_private_key.pem"))
    env["EB_REDIRECT_URL"] = opts.get("redirect_url", "http://localhost:8080/auth_redirect")
    env["ASPSP_NAME"] = opts.get("bank_name", "Spar Nord")
    env["ASPSP_COUNTRY"] = opts.get("bank_country", "DK")
    env["CONSENT_DAYS"] = str(opts.get("consent_days", 180))
    env["SYNC_INTERVAL_MINUTES"] = str(opts.get("sync_interval_minutes", 360))
    env["INITIAL_HISTORY_DAYS"] = str(opts.get("initial_history_days", 730))
    env["PUBLISH_TRANSACTION_DETAILS"] = "true" if opts.get("publish_transaction_details", True) else "false"
    env["LARGE_TRANSACTION_THRESHOLD"] = str(opts.get("large_transaction_threshold", 2000))
    env["DATABASE_URL"] = opts.get("database_url") or "sqlite:////data/bank.db"
    env["RULES_PATH"] = str(CONFIG_DIR / "rules.yaml")
    env["UI_PASSWORD"] = ""  # HA ingress handles authentication
    env.setdefault("TZ", "Europe/Copenhagen")

    # first start: give the user an editable rules file
    rules = CONFIG_DIR / "rules.yaml"
    if CONFIG_DIR.exists() and not rules.exists():
        shutil.copy(Path(__file__).parent.parent / "rules.example.yaml", rules)

    key = Path(env["EB_PRIVATE_KEY_PATH"])
    if not key.exists():
        print(f"[bank2ha] Mangler privat nøgle: læg din Enable Banking .pem-fil som {key} "
              f"(på HA: /addon_configs/<app>/{key.name})", file=sys.stderr)
    if not env["EB_APPLICATION_ID"]:
        print("[bank2ha] Udfyld 'application_id' under appens Konfiguration.", file=sys.stderr)

    mqtt = _supervisor_mqtt()
    if mqtt:
        env["MQTT_ENABLED"] = "true"
        env["MQTT_HOST"] = mqtt.get("host", "core-mosquitto")
        env["MQTT_PORT"] = str(mqtt.get("port", 1883))
        env["MQTT_USERNAME"] = mqtt.get("username", "")
        env["MQTT_PASSWORD"] = mqtt.get("password", "")
        env["MQTT_TLS"] = "true" if mqtt.get("ssl") else "false"
    else:
        env["MQTT_ENABLED"] = "false"

    os.umask(0o077)
    os.execvp("uvicorn", ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8080",
                          "--no-server-header", "--log-level", "info"])


if __name__ == "__main__":
    main()
