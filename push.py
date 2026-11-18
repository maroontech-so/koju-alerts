"""Firebase Cloud Messaging publisher. One Android notification per parsed trade.

Payloads are marked high-priority and routed to a dedicated intrusive channel so
the Android client can show a full-screen alert. The client is responsible for
the full-screen intent; the server only tags the notification correctly.
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
    """Raise on startup if credentials are malformed. Fails fast instead of retry-forever."""
    _firebase_app()


def _string(value, limit=500):
    return str(value if value is not None else "")[:limit]


def build_fcm_payload(alert: dict, signal: dict, index: int) -> dict[str, str]:
    message_id = int(alert["message_id"])
    base_id = _string(alert.get("alert_id") or message_id, 64)
    alert_id = f"{base_id}-{index + 1}"[:64]
    direction = _string(signal.get("direction"), 12)
    instrument = _string(signal.get("instrument"), 20)
    order = _string(signal.get("order_type") or "MARKET", 12)
    targets = signal.get("take_profit") or []
    return {
        "alert_id": alert_id,
        "message_id": str(message_id),
        "source_url": _string(alert.get("source_url"), 300),
        "title": f"KOJO ALERT — {direction} {instrument}",
        "body": f"{order} · Entry {signal.get('entry')} · SL {signal.get('stop_loss')}",
        "instrument": instrument,
        "direction": direction,
        "order_type": order,
        "entry": _string(signal.get("entry"), 40),
        "stop_loss": _string(signal.get("stop_loss"), 40),
        "take_profit": json.dumps([_string(x, 40) for x in targets[:8]]),
        "confidence": _string((alert.get("analysis") or {}).get("confidence", 0), 12),
        "original_text": _string(alert.get("original_text"), 1000),
        "signal_number": str(index + 1),
        "signal_count": str(len((alert.get("analysis") or {}).get("signals") or [])),
        # Priority hints for the Android client.
        "priority": "high",
        "channel_id": config.FCM_CHANNEL_ID,
        "full_screen": "true",
        "category": "kojo_trade_alert",
    }


def _correction_payload(alert: dict) -> dict[str, str]:
    return {
        "alert_id": _string(alert.get("alert_id") or alert.get("message_id"), 64),
        "message_id": str(alert["message_id"]),
        "source_url": _string(alert.get("source_url"), 300),
        "title": "KOJO ALERT — SOURCE POST UPDATED",
        "body": "The original trading post was edited. Review it before acting on the previous signal.",
        "instrument": "", "direction": "UPDATE", "order_type": "UPDATE",
        "entry": "", "stop_loss": "", "take_profit": "[]",
        "confidence": "1",
        "original_text": _string(alert.get("original_text"), 1000),
        "correction": "true", "signal_number": "0", "signal_count": "0",
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
                ttl=timedelta(hours=1),
                # Data-only on purpose: the Android client builds the
                # full-screen notification itself. A server-side Notification
                # would prevent setFullScreenIntent from being attached.
            ),
        )
        message_ids.append(messaging.send(message, app=app))
    return message_ids