"""Lightweight public status page + JSON API for KOJO ALERT.

Serves on 0.0.0.0:$PORT as required by Render web services. Binds to the
port Render injects via the PORT env var (default 10000). Read-only — no
secrets, no auth tokens, no mutation endpoints. The bot, parser, and all
delivery loops live in app.py and are unaffected.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import aiosqlite
from aiohttp import web

import config

log = logging.getLogger("kojo-alert.web")

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

async def _stats(db_path: str) -> dict:
    """Aggregate read-only statistics for the public status page."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM subscribers WHERE active=1") as cur:
            active_subs = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM subscribers") as cur:
            total_subs = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM source_messages") as cur:
            total_sources = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM source_messages WHERE status='done'") as cur:
            done = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM source_messages WHERE status='pending'") as cur:
            pending = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM source_messages WHERE status='failed'") as cur:
            failed = (await cur.fetchone())[0]
        async with db.execute("SELECT value FROM state WHERE key='last_seen_id'") as cur:
            row = await cur.fetchone()
            last_seen = row[0] if row else None
        async with db.execute("SELECT COUNT(*) FROM telegram_outbox WHERE delivered_at IS NULL") as cur:
            tg_pending = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM fcm_outbox_v2 WHERE sent_at IS NULL") as cur:
            fcm_pending = (await cur.fetchone())[0]

async def handle_alerts_since(request: web.Request) -> web.Response:
    """Return all validated strategies posted after the given marker.

    Query params:
      after    — message_id the client last saw (0 or omitted = from the beginning)
      limit    — max items, default 20 (server caps at 50)

    Response:
      {
        "alerts": [ { message_id, source_url, analysis, ... }, ... ],   # oldest first
        "count": N,
        "has_more": bool,
        "latest_message_id": M
      }

    No auth required — payloads are the same read-only public summaries
    shown on the index page. Detailed source text is only included for
    validated strategies, which are already public in the source channel.
    """
    db_path = request.app["db_path"]
    try:
        after = int(request.query.get("after", "0") or "0")
    except ValueError:
        after = 0
    try:
        limit = max(1, min(50, int(request.query.get("limit", "20"))))
    except ValueError:
        limit = 20

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT message_id, source_url, analysis_json FROM source_messages "
            "WHERE status='done' AND message_id > ? AND analysis_json IS NOT NULL "
            "ORDER BY message_id ASC LIMIT ?",
            (after, limit + 1),
        ) as cur:
            rows = await cur.fetchall()

    alerts = []
    for message_id, url, raw in rows:
        try:
            analysis = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not (analysis.get("is_strategy") or analysis.get("correction")):
            continue
        alerts.append({
            "alert_id": f"{message_id}-catchup",
            "message_id": message_id,
            "source_url": url,
            "original_text": "",
            "analysis": analysis,
        })

    has_more = len(alerts) > limit
    alerts = alerts[:limit]

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT MAX(message_id) FROM source_messages WHERE status='done'"
        ) as cur:
            row = await cur.fetchone()
            latest = row[0] or 0

    return web.json_response({
        "alerts": alerts,
        "count": len(alerts),
        "has_more": has_more,
        "latest_message_id": latest,
    })
    return {
        "active_subscribers": active_subs,
        "total_subscribers": total_subs,
        "source_messages": {"total": total_sources, "done": done, "pending": pending, "failed": failed},
        "last_seen_message_id": last_seen,
        "outbox": {"telegram_pending": tg_pending, "fcm_pending": fcm_pending},
        "server_time_utc": datetime.now(timezone.utc).isoformat(),
    }


async def _recent_alerts(db_path: str, limit: int = 5) -> list[dict]:
    """Return the last N validated strategies, redacted for public display."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT message_id, source_url, analysis_json FROM source_messages "
            "WHERE status='done' AND analysis_json IS NOT NULL "
            "ORDER BY message_id DESC LIMIT ?",
            (limit * 3,),
        ) as cur:
            rows = await cur.fetchall()

    out = []
    for message_id, url, raw in rows:
        try:
            analysis = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not (analysis.get("is_strategy") or analysis.get("correction")):
            continue
        signals = analysis.get("signals") or []
        if analysis.get("correction"):
            out.append({
                "kind": "correction",
                "message_id": message_id,
                "source_url": url,
            })
        elif signals:
            s = signals[0]
            out.append({
                "kind": "signal",
                "direction": s.get("direction"),
                "instrument": s.get("instrument"),
                "entry": s.get("entry"),
                "stop_loss": s.get("stop_loss"),
                "take_profit": s.get("take_profit", [])[:3],
                "order_type": s.get("order_type", "MARKET"),
                "confidence": analysis.get("confidence"),
                "message_id": message_id,
                "source_url": url,
            })
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def handle_index(request: web.Request) -> web.Response:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return web.Response(text="Frontend not built.", status=500)
    return web.FileResponse(index)


async def handle_stats(request: web.Request) -> web.Response:
    db_path = request.app["db_path"]
    payload = await _stats(db_path)
    return web.json_response(payload)


async def handle_alerts(request: web.Request) -> web.Response:
    db_path = request.app["db_path"]
    try:
        limit = max(1, min(20, int(request.query.get("limit", "5"))))
    except ValueError:
        limit = 5
    payload = await _recent_alerts(db_path, limit=limit)
    return web.json_response(payload)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# App factory + runner
# ---------------------------------------------------------------------------

def build_app(db_path: str) -> web.Application:
    app = web.Application()
    app["db_path"] = db_path
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/stats", handle_stats)
    app.router.add_get("/api/alerts", handle_alerts)
    app.router.add_get("/healthz", handle_health)
    # Serve anything else under /static/ as a file
    if STATIC_DIR.exists():
        app.router.add_static("/static/", STATIC_DIR)
    return app


async def web_server_loop(db_path: str):
    """Bind to 0.0.0.0:$PORT. Render injects PORT; default 10000.

    Runs forever. If the port is already taken, Render has been misconfigured
    (two services on one port) — the exception will surface in the logs.
    """
    port = int(os.environ.get("PORT", "10000"))
    host = "0.0.0.0"
    app = build_app(db_path)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    log.info("Web frontend listening on http://%s:%d/", host, port)
    try:
        await asyncio.Event().wait()  # sleep forever; cancellable from main()
    finally:
        await runner.cleanup()

