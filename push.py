"""Firebase Cloud Messaging publisher.

Payload includes every field the Android app needs to render the full
notification (direction, instrument, entry, stop loss, all take-profits,
order type, confidence). The only field intentionally omitted is
`original_text` (~1000 bytes) — the app can fetch it via /api/alerts/since
if it ever needs the raw source post.

TTL is 4 weeks (FCM max) so offline devices still receive when they reconnect.
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
    return str(value if value is not None else "")[:limit]


def build_fcm_payload(alert: dict, signal: dict, index: int) -> dict[str, str]:
    """Full-detail payload — every field the app's FullScreenAlertActivity reads.

    Field names match the original (long) schema so the existing app decodes
    them without changes. Only `original_text` is omitted to keep the message
    small enough for slow networks.
    """
    message_id = int(alert["message_id"])
    base_id = _s(alert.get("alert_id") or message_id, 64)
    alert_id = f"{base_id}-{index + 1}"[:64]
    direction = _s(signal.get("direction"), 12)
    instrument = _s(signal.get("instrument"), 20)
    order = _s(signal.get("order_type") or "MARKET", 12)
    targets = signal.get("take_profit") or []
    return {
        # Identity
        "alert_id": alert_id,
        "message_id": str(message_id),
        "source_url": _s(alert.get("source_url"), 300),
        # Display
        "title": f"KOJO ALERT — {direction} {instrument}",
        "body": f"{order} · Entry {signal.get('entry')} · SL {signal.get('stop_loss')}",
        # Structured details (the app reads these directly)
        "instrument": instrument,
        "direction": direction,
        "order_type": order,
        "entry": _s(signal.get("entry"), 40),
        "stop_loss": _s(signal.get("stop_loss"), 40),
        "take_profit": json.dumps([_s(x, 40) for x in targets[:8]]),
        "confidence": _s((alert.get("analysis") or {}).get("confidence", 0), 12),
        # Signal ordinal
        "signal_number": str(index + 1),
        "signal_count": str(len((alert.get("analysis") or {}).get("signals") or [])),
        # Notification behaviour
        "priority": "high",
        "channel_id": config.FCM_CHANNEL_ID,
        "full_screen": "true",
        "category": "kojo_trade_alert",
        # NOTE: original_text is intentionally NOT sent here — the app can
        # fetch it from /api/alerts/since when rendering the detail screen.
    }


def _correction_payload(alert: dict) -> dict[str, str]:
    return {
        "alert_id": _s(alert.get("alert_id") or alert.get("message_id"), 64),
        "message_id": str(alert["message_id"]),
        "source_url": _s(alert.get("source_url"), 300),
        "title": "KOJO ALERT — SOURCE POST UPDATED",
        "body": "The original trading post was edited. Review it before acting on the previous signal.",
        "instrument": "",
        "direction": "UPDATE",
        "order_type": "UPDATE",
        "entry": "",
        "stop_loss": "",
        "take_profit": "[]",
        "confidence": "1",
        "signal_number": "0",
        "signal_count": "0",
        "priority": "high",
        "channel_id": config.FCM_CHANNEL_ID,
        "full_screen": "true",
        "category": "kojo_trade_alert",
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
                ttl=timedelta(weeks=4),
                collapse_key=f"kojo-{payload['message_id']}",
                direct_boot_ok=True,
            ),
        )
        message_ids.append(messaging.send(message, app=app))
    return message_ids