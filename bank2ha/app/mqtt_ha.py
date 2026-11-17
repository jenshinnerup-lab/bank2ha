"""Home Assistant integration through MQTT discovery."""
from __future__ import annotations

import json
import logging
import ssl
from typing import Callable

import paho.mqtt.client as mqtt

from .config import Settings

log = logging.getLogger(__name__)

ORIGIN = {"name": "bank2ha", "sw_version": "1.0.0", "support_url": "https://enablebanking.com/docs/"}


class HAPublisher:
    def __init__(self, settings: Settings, on_sync_request: Callable[[], None]):
        self.s = settings
        self.base = settings.mqtt_base_topic
        self.disc = settings.ha_discovery_prefix
        self.on_sync_request = on_sync_request
        self._last_summary: dict | None = None
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="bank2ha")
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        if settings.mqtt_tls:
            self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.will_set(f"{self.base}/status", "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_connect_fail = lambda c, u: log.warning(
            "Kan ikke forbinde til MQTT %s:%s – prøver igen", settings.mqtt_host, settings.mqtt_port)

    # ----------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.client.connect_async(self.s.mqtt_host, self.s.mqtt_port, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.publish(f"{self.base}/status", "offline", retain=True)
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):  # noqa: ANN001
        if reason_code.is_failure:
            log.error("MQTT forbindelse fejlede: %s", reason_code)
            return
        log.info("Forbundet til MQTT %s:%s", self.s.mqtt_host, self.s.mqtt_port)
        client.publish(f"{self.base}/status", "online", retain=True)
        client.subscribe(f"{self.base}/cmd/#")
        client.subscribe(f"{self.disc}/status")  # HA birth message
        if self._last_summary:
            self.publish_summary(self._last_summary)

    def _on_message(self, client, userdata, msg):  # noqa: ANN001
        payload = msg.payload.decode(errors="ignore").strip()
        if msg.topic == f"{self.disc}/status" and payload == "online" and self._last_summary:
            self.publish_summary(self._last_summary)  # HA restarted -> re-announce
        elif msg.topic == f"{self.base}/cmd/sync":
            log.info("Sync anmodet fra Home Assistant")
            self.on_sync_request()

    # ----------------------------------------------------------------- discovery
    def _pub_config(self, component: str, object_id: str, config: dict) -> None:
        config.setdefault("availability_topic", f"{self.base}/status")
        config["origin"] = ORIGIN
        self.client.publish(f"{self.disc}/{component}/{object_id}/config", json.dumps(config), retain=True)

    def _global_device(self) -> dict:
        return {"identifiers": ["bank2ha"], "name": "Bank2HA", "manufacturer": "VibrantColor.dev", "model": "Enable Banking sync"}

    def _account_device(self, acc: dict) -> dict:
        return {
            "identifiers": [f"bank2ha_{acc['id'][:16]}"],
            "name": f"Bank {acc['name']}",
            "manufacturer": "Spar Nord / Nykredit",
            "model": acc.get("iban_masked") or "Konto",
            "via_device": "bank2ha",
        }

    def announce(self, summary: dict) -> None:
        dev = self._global_device()
        st = f"{self.base}/state"
        self._pub_config("sensor", "bank2ha_consent_days_left", {
            "name": "Samtykke dage tilbage", "unique_id": "bank2ha_consent_days_left", "device": dev,
            "state_topic": st, "value_template": "{{ value_json.consent_days_left }}",
            "unit_of_measurement": "d", "icon": "mdi:shield-key",
            "json_attributes_topic": st,
            "json_attributes_template": "{{ {'valid_until': value_json.consent_valid_until, 'status': value_json.consent_status} | tojson }}",
        })
        self._pub_config("binary_sensor", "bank2ha_consent_problem", {
            "name": "Samtykke kræver fornyelse", "unique_id": "bank2ha_consent_problem", "device": dev,
            "state_topic": st, "device_class": "problem",
            "value_template": "{{ 'ON' if value_json.consent_status != 'active' or (value_json.consent_days_left is not none and value_json.consent_days_left < 14) else 'OFF' }}",
        })
        self._pub_config("sensor", "bank2ha_last_sync", {
            "name": "Seneste sync", "unique_id": "bank2ha_last_sync", "device": dev,
            "state_topic": st, "device_class": "timestamp", "value_template": "{{ value_json.last_sync }}",
            "json_attributes_topic": st,
            "json_attributes_template": "{{ {'ok': value_json.last_sync_ok, 'message': value_json.last_sync_message} | tojson }}",
        })
        self._pub_config("binary_sensor", "bank2ha_sync_problem", {
            "name": "Sync fejl", "unique_id": "bank2ha_sync_problem", "device": dev,
            "state_topic": st, "device_class": "problem",
            "value_template": "{{ 'OFF' if value_json.last_sync_ok else 'ON' }}",
        })
        self._pub_config("button", "bank2ha_sync_now", {
            "name": "Synkronisér nu", "unique_id": "bank2ha_sync_now", "device": dev,
            "command_topic": f"{self.base}/cmd/sync", "payload_press": "sync", "icon": "mdi:bank-transfer",
        })

        for acc in summary["accounts"]:
            d = self._account_device(acc)
            uid = f"bank2ha_{acc['id'][:16]}"
            at = f"{self.base}/account/{acc['id'][:16]}/state"
            cur = acc["currency"]
            money = {"device_class": "monetary", "unit_of_measurement": cur, "suggested_display_precision": 2}
            self._pub_config("sensor", f"{uid}_balance", {
                "name": "Saldo", "unique_id": f"{uid}_balance", "device": d, "state_topic": at,
                "value_template": "{{ value_json.balance }}", "state_class": "total", **money,
                "json_attributes_topic": at, "json_attributes_template": "{{ value_json.balances | tojson }}",
            })
            for key, name, icon in [
                ("month_expenses", "Forbrug denne måned", "mdi:cash-minus"),
                ("month_income", "Indtægter denne måned", "mdi:cash-plus"),
                ("month_net", "Netto denne måned", "mdi:scale-balance"),
            ]:
                cfg = {"name": name, "unique_id": f"{uid}_{key}", "device": d, "state_topic": at,
                       "value_template": f"{{{{ value_json.{key} }}}}", "icon": icon, **money}
                if key == "month_expenses":
                    cfg["json_attributes_topic"] = at
                    cfg["json_attributes_template"] = "{{ value_json.month_by_category | tojson }}"
                self._pub_config("sensor", f"{uid}_{key}", cfg)
            self._pub_config("sensor", f"{uid}_pending", {
                "name": "Reserverede posteringer", "unique_id": f"{uid}_pending", "device": d, "state_topic": at,
                "value_template": "{{ value_json.pending_count }}", "icon": "mdi:timer-sand",
                "json_attributes_topic": at, "json_attributes_template": "{{ {'amount': value_json.pending_amount} | tojson }}",
            })
            self._pub_config("sensor", f"{uid}_last_tx", {
                "name": "Seneste transaktion", "unique_id": f"{uid}_last_tx", "device": d, "state_topic": at,
                "value_template": "{{ value_json.last_transaction.amount if value_json.last_transaction else none }}",
                **money,
                "json_attributes_topic": at,
                "json_attributes_template": "{{ (value_json.last_transaction or {}) | tojson }}",
            })
            self._pub_config("event", f"{uid}_transaction", {
                "name": "Transaktion", "unique_id": f"{uid}_transaction", "device": d,
                "state_topic": f"{self.base}/account/{acc['id'][:16]}/event",
                "event_types": ["ny_transaktion", "stor_transaktion", "indbetaling"],
                "icon": "mdi:swap-horizontal",
            })

    # ----------------------------------------------------------------- state
    def publish_summary(self, summary: dict) -> None:
        self._last_summary = summary
        self.announce(summary)
        glob = {k: v for k, v in summary.items() if k != "accounts"}
        self.client.publish(f"{self.base}/state", json.dumps(glob), retain=True)
        for acc in summary["accounts"]:
            payload = dict(acc)
            if not self.s.publish_transaction_details and payload.get("last_transaction"):
                payload["last_transaction"] = {k: payload["last_transaction"][k] for k in ("date", "amount", "currency", "category")}
            self.client.publish(f"{self.base}/account/{acc['id'][:16]}/state", json.dumps(payload), retain=True)

    def publish_new_transactions(self, new_rows: list[dict]) -> None:
        """Fires HA event entities. Not retained."""
        for r in sorted(new_rows, key=lambda x: (x["booking_date"] or x["value_date"] or 0, x["id"])):
            amount = float(r["amount"])
            if amount > 0:
                etype = "indbetaling"
            elif abs(amount) >= self.s.large_transaction_threshold:
                etype = "stor_transaktion"
            else:
                etype = "ny_transaktion"
            ev = {
                "event_type": etype,
                "amount": amount,
                "currency": r["currency"],
                "date": r["booking_date"].isoformat() if r["booking_date"] else None,
                "category": r["category"],
            }
            if self.s.publish_transaction_details:
                ev.update(counterparty=r["counterparty"], description=r["description"])
            self.client.publish(f"{self.base}/account/{r['account_id'][:16]}/event", json.dumps(ev, default=str))
