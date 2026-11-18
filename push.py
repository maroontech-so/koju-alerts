"""Firebase Cloud Messaging publisher — minimal payload for low-bandwidth delivery.

The FCM data payload is deliberately tiny (~250 bytes) so it arrives even on
very slow links. Detailed content (original_text, full signal list) is fetched
by the app via /api/alerts/since once the push lands. The FCM message is a
"wake signal" — the app fetches the rest.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

import firebase_admin
from firebase_admin import credentials, messaging

import config

log = logging.getLogger("kojo-alert.fcm")
_app = None


def _firebase_app():
    global _app
    if _app is not None:
        return _app
    raw = (os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON") or "").strip()
    if not raw:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is not configured")
    try:
        service_account = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON must be valid JSON") from exc
    project_id = os.getenv("FIREBASE_PROJECT_ID") or service_account.get("project_id")
    options = {"projectId": project_id} if project_id else None
    try:
        _app = firebase_admin.get_app()
    except ValueError:
        _app = firebase_admin.initialize_app(credentials.Certificate(service_account), options)
    return _app


def validate_firebase_config() -> None:
    _firebase_app()


def _s(value, limit=200):
    """Aggressively truncate — every byte matters on slow links."""
    return str(value if value is not None else "")[:limit]


def _compact_tp(targets: list) -> str:
    """Serialize take-profits as 'tp1|tp2|tp3' — ~8 bytes vs JSON's ~30 bytes."""
    return "|".join(_s(x, 20) for x in (targets or [])[:5])


def build_fcm_payload(alert: dict, signal: dict, index: int) -> dict[str, str]:
    """Minimal payload — app fetches full details via /api/alerts/since.

    Target size: ~250 bytes. Original was ~2000 bytes. That's the difference
    between arriving on a 5 kbps link and not.
    """
    message_id = int(alert["message_id"])
    return {
        # Identity (needed for dedupe and catch-up sync)
        "a": _s(alert.get("alert_id") or message_id, 64),   # alert_id
        "m": str(message_id),                                # message_id
        "u": _s(alert.get("source_url"), 120),              # source_url
        "i": _s(signal.get("instrument"), 20),              # instrument
        "d": _s(signal.get("direction"), 8),                # direction
        "o": _s(signal.get("order_type") or "MARKET", 8),   # order_type
        "e": _s(signal.get("entry"), 20),                   # entry
        "s": _s(signal.get("stop_loss"), 20),               # stop_loss
        "t": _compact_tp(signal.get("take_profit") or []),  # take_profit
        "n": str(index + 1),                                # signal_number
        "c": str(len((alert.get("analysis") or {}).get("signals") or [])),  # count
        "k": "1",                                           # kind: "1"=signal, "0"=correction
    }


def _correction_payload(alert: dict) -> dict[str, str]:
    return {
        "a": _s(alert.get("alert_id") or alert.get("message_id"), 64),
        "m": str(alert["message_id"]),
        "u": _s(alert.get("source_url"), 120),
        "d": "UPDATE",
        "k": "0",  # correction
    }


def send_topic_alert(alert: dict) -> list[str]:
    analysis = alert.get("analysis") or {}
    signals = analysis.get("signals") or []

    if analysis.get("correction"):
        payloads = [_correction_payload(alert)]
    else:
        if not signals:
            raise ValueError("Cannot send FCM alert without parsed signals")
        payloads = [build_fcm_payload(alert, signal, index) for index, signal in enumerate(signals)]

    app = _firebase_app()
    message_ids = []
    for payload in payloads:
        message = messaging.Message(
            topic=config.FCM_TOPIC,
            data=payload,
            android=messaging.AndroidConfig(
                priority="high",
                ttl=timedelta(weeks=4),               # 28 days — FCM's max
                collapse_key=f"kojo-{payload['m']}",  # collapse duplicates
                direct_boot_ok=True,                  # deliver before unlock
            ),
        )
        message_ids.append(messaging.send(message, app=app))
    return message_ids