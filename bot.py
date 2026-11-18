"""KOJO ALERT Telegram bot: subscriptions, preferences, history, priority delivery.

Delivery contract:
  * Each (chat_id, alert_id) row is claimed atomically before the send, so
    concurrent triggers (event drain + reminder loop) cannot double-send.
  * A user receives at most 3 messages per alert: 1 initial + up to 2 reminders
    (at +3min, +6min) if they do not press ACKNOWLEDGE.
  * Reminders are visually distinct from new signals, so they are never
    mistaken for duplicates.
  * Every successful send is recorded in delivery_log with the Telegram
    message_id for audit.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import aiosqlite
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Conflict, Forbidden, NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
)

import config

log = logging.getLogger("kojo-alert.bot")


def esc(value):
    return html.escape(str(value)) if value not in (None, "") else "—"


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _format_signal_block(signal: dict) -> str:
    direction = signal.get("direction", "?")
    emoji = "🟢" if direction == "BUY" else "🔴"
    lines = [
        f"{emoji} <b>{esc(direction)} — {esc(signal.get('instrument'))}</b>",
        f"   <i>Order type:</i> <b>{esc(signal.get('order_type', 'MARKET'))}</b>",
        "",
        f"<b>ENTRY</b>       <code>{esc(signal.get('entry'))}</code>",
        f"<b>STOP LOSS</b>   <code>{esc(signal.get('stop_loss'))}</code>",
        "",
        "<b>TAKE PROFIT</b>",
    ]
    for tp_index, tp in enumerate(signal.get("take_profit", []), 1):
        lines.append(f"   <b>{tp_index}.</b> <code>{esc(tp)}</code>")
    return "\n".join(lines)


def format_alert(alert: dict, reminder: bool = False) -> str:
    analysis = alert.get("analysis") or {}
    signals = analysis.get("signals") or []

    if analysis.get("correction"):
        header = (
            "⏰ <b>REMINDER — CORRECTION NOT ACKNOWLEDGED</b>"
            if reminder
            else "⚠️ <b>KOJO ALERT — SOURCE POST UPDATED</b>"
        )
        return "\n".join([
            header,
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "The original KOJOFOREX post has been edited. The previous trade "
            "setup may have changed or been withdrawn.",
            "",
            "<b>Do not rely on the previous levels.</b>",
            "Open the updated source post before acting.",
            "",
            f'🔗 <a href="{esc(alert.get("source_url"))}">Open updated original post</a>',
            "",
            "<i>Automated correction notice. Verify the source.</i>",
        ])

    confidence = round(float(analysis.get("confidence", 0)) * 100)
    if reminder:
        header = "⏰ <b>REMINDER — UNACKNOWLEDGED ALERT</b>"
        tagline = (
            "<i>This is a reminder for an alert you received earlier.\n"
            "It is not a new signal.</i>"
        )
    else:
        header = "🚨 <b>KOJO ALERT — NEW TRADE SIGNAL</b>"
        tagline = ""

    lines = [header]
    if tagline:
        lines.append(tagline)
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")

    for index, signal in enumerate(signals, 1):
        if index > 1:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("")
        lines.append(_format_signal_block(signal))

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"✅ AI-verified · Confidence {confidence}%",
        f'🔗 <a href="{esc(alert.get("source_url"))}">Open original KOJOFOREX post</a>',
        "",
        "<i>Manage your risk. Verify every level against the original post. "
        "Not financial advice.</i>",
    ])
    return "\n".join(lines)


def ack_keyboard(alert_id: str) -> InlineKeyboardMarkup:
    safe = alert_id[:60]
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ACKNOWLEDGE", callback_data=f"ack:{safe}"),
    ]])


# ---------------------------------------------------------------------------
# Subscription helpers
# ---------------------------------------------------------------------------

async def _upsert_subscriber(db, chat_id: int, username: str | None, first_name: str | None):
    await db.execute(
        """INSERT INTO subscribers(chat_id,username,first_name,active,joined_at,filter_directions)
           VALUES(?,?,?,1,?, 'BUY,SELL')
           ON CONFLICT(chat_id) DO UPDATE SET
             username=excluded.username,
             first_name=excluded.first_name,
             active=1""",
        (chat_id, username, first_name, config.utcnow()),
    )


async def _get_subscriber(db, chat_id: int):
    """Returns (active, joined_at, paused_until, filter_directions) or None."""
    async with db.execute(
        "SELECT active,joined_at,paused_until,filter_directions FROM subscribers WHERE chat_id=?",
        (chat_id,),
    ) as cur:
        return await cur.fetchone()


def _is_paused(paused_until) -> bool:
    if not paused_until:
        return False
    try:
        return datetime.fromisoformat(str(paused_until)) > _now()
    except (ValueError, TypeError):
        return False


def _parse_pause_arg(arg: str) -> timedelta | None:
    if not arg:
        return timedelta(hours=1)
    arg = arg.strip().lower()
    if arg == "today":
        end = (_now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return end - _now()
    m = re.match(r"^(\d+)\s*([smhd])$", arg)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return {"s": timedelta(seconds=n), "m": timedelta(minutes=n),
            "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


# ---------------------------------------------------------------------------
# Menu builders
# ---------------------------------------------------------------------------

def _main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Latest Signal", callback_data="act:latest"),
         InlineKeyboardButton("📜 My History", callback_data="act:history")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="act:settings"),
         InlineKeyboardButton("❓ Help", callback_data="act:help")],
    ])


def _settings_kb(buy_on: bool, sell_on: bool, paused: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(f"{'✅' if buy_on else '⬜'} BUY", callback_data="set:buy"),
            InlineKeyboardButton(f"{'✅' if sell_on else '⬜'} SELL", callback_data="set:sell"),
        ],
    ]
    if paused:
        rows.append([InlineKeyboardButton("▶️ Resume alerts", callback_data="act:resume")])
    else:
        rows.append([
            InlineKeyboardButton("⏸ 1h", callback_data="pause:1h"),
            InlineKeyboardButton("⏸ 6h", callback_data="pause:6h"),
            InlineKeyboardButton("⏸ 24h", callback_data="pause:24h"),
        ])
    rows.append([InlineKeyboardButton("🔙 Back to menu", callback_data="act:menu")])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat = update.effective_user, update.effective_chat
    if chat.type != "private":
        await update.effective_message.reply_text(
            "KOJO ALERT is available in private chat only.\n"
            "Tap the bot's name to open a direct conversation."
        )
        return
    async with aiosqlite.connect(context.application.bot_data["db_path"]) as db:
        await _upsert_subscriber(db, chat.id, user.username, user.first_name)
        await db.commit()

    text = (
        "🔔 <b>KOJO ALERTS ACTIVATED</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Welcome, {esc(user.first_name or 'trader')}.\n\n"
        "You are now subscribed to <b>real-time trade signals</b> from "
        "<b>@kojoforextrades</b>. Every validated BUY/SELL setup arrives here "
        "within seconds — no app required.\n\n"
        "<b>How it works</b>\n"
        "• Signals are parsed deterministically, then AI-verified\n"
        "• Each alert includes entry, stop loss, and take-profit levels\n"
        "• You receive up to 2 reminders if you don't acknowledge\n\n"
        "Tap a button below, or send /help for the full command list.\n\n"
        "<i>Android users: install the KOJO ALERT app for full-screen "
        "push notifications.</i>"
    )
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=_main_menu_kb(),
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(context.application.bot_data["db_path"]) as db:
        row = await _get_subscriber(db, update.effective_chat.id)
    if not row:
        await update.effective_message.reply_text("Not subscribed. Send /start to begin.")
        return
    paused = _is_paused(row[2])
    state = "⏸ Paused" if paused else "✅ Active"
    await update.effective_message.reply_text(
        f"<b>KOJO ALERT — Menu</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Status:</b> {state}\n"
        f"<b>Direction filter:</b> {esc(row[3] or 'BUY,SELL')}\n\n"
        f"Choose an option below:",
        parse_mode=ParseMode.HTML, reply_markup=_main_menu_kb(),
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(context.application.bot_data["db_path"]) as db:
        await db.execute("UPDATE subscribers SET active=0 WHERE chat_id=?", (update.effective_chat.id,))
        await db.commit()
    await update.effective_message.reply_text(
        "You have unsubscribed from KOJO ALERT.\n"
        "Send /start anytime to resume."
    )


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = " ".join(context.args) if context.args else ""
    duration = _parse_pause_arg(arg)
    if duration is None:
        await update.effective_message.reply_text(
            "Usage: /pause [duration]\n\n"
            "Examples:\n"
            "/pause 30m — 30 minutes\n"
            "/pause 2h — 2 hours\n"
            "/pause 1d — 1 day\n"
            "/pause today — until midnight UTC"
        )
        return
    until = (_now() + duration).isoformat()
    async with aiosqlite.connect(context.application.bot_data["db_path"]) as db:
        await db.execute("UPDATE subscribers SET paused_until=? WHERE chat_id=?", (until, update.effective_chat.id))
        await db.commit()
    await update.effective_message.reply_text(
        f"⏸ Alerts paused until <b>{until[:16].replace('T', ' ')} UTC</b>.\n"
        f"Send /resume to reactivate now.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(context.application.bot_data["db_path"]) as db:
        await db.execute("UPDATE subscribers SET active=1, paused_until=NULL WHERE chat_id=?",
                         (update.effective_chat.id,))
        await db.commit()
    await update.effective_message.reply_text("▶️ Alerts resumed. You'll receive the next signal.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(context.application.bot_data["db_path"]) as db:
        row = await _get_subscriber(db, update.effective_chat.id)
    if not row:
        await update.effective_message.reply_text("Not subscribed. Send /start to begin.")
        return
    active, joined_at, paused_until, directions = row
    paused = _is_paused(paused_until)
    state = "⏸ PAUSED" if paused else ("✅ ACTIVE" if active else "❌ INACTIVE")
    lines = [
        "<b>Subscription Status</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"<b>State:</b> {state}",
        f"<b>Joined:</b> {joined_at[:16].replace('T', ' ')} UTC" if joined_at else "<b>Joined:</b> unknown",
        f"<b>Direction filter:</b> {esc(directions or 'BUY,SELL')}",
    ]
    if paused and paused_until:
        try:
            lines.append(f"<b>Paused until:</b> {str(paused_until)[:16].replace('T', ' ')} UTC")
        except Exception:
            pass
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_settings(update.effective_chat.id, update.effective_message, context)


async def _show_settings(chat_id: int, message, context):
    async with aiosqlite.connect(context.application.bot_data["db_path"]) as db:
        row = await _get_subscriber(db, chat_id)
    if not row:
        await message.reply_text("Not subscribed. Send /start to begin.")
        return
    active, joined_at, paused_until, directions = row
    paused = _is_paused(paused_until)
    dirs = (directions or "BUY,SELL").split(",")
    buy_on = "BUY" in dirs
    sell_on = "SELL" in dirs

    await message.reply_text(
        "<b>⚙️ Alert Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Toggle which directions you receive, or pause all alerts temporarily.\n\n"
        f"<b>Current filters:</b> {esc(directions or 'BUY,SELL')}\n"
        f"<b>Status:</b> {'⏸ Paused' if paused else '✅ Active'}\n\n"
        "<i>Changes take effect on the next alert.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=_settings_kb(buy_on, sell_on, paused),
    )


async def cmd_latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_latest(update.effective_message, context)


async def _show_latest(message, context):
    async with aiosqlite.connect(context.application.bot_data["db_path"]) as db:
        async with db.execute(
            "SELECT message_id,message_text,source_url,analysis_json,alert_id "
            "FROM source_messages WHERE status='done' AND analysis_json IS NOT NULL "
            "ORDER BY message_id DESC LIMIT 50"
        ) as cur:
            rows = await cur.fetchall()
    for message_id, text, url, raw, alert_id in rows:
        try:
            analysis = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if analysis.get("is_strategy") or analysis.get("correction"):
            alert = {
                "alert_id": alert_id or str(message_id),
                "message_id": message_id,
                "original_text": text,
                "source_url": url,
                "analysis": analysis,
            }
            await message.reply_text(
                format_alert(alert), parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )
            return
    await message.reply_text("No validated strategy has been detected yet.")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_history(update.effective_chat.id, update.effective_message, context)


async def _show_history(chat_id: int, message, context):
    limit = config.TELEGRAM_HISTORY_LIMIT
    async with aiosqlite.connect(context.application.bot_data["db_path"]) as db:
        async with db.execute(
            "SELECT alert_id, alert_json, attempts, acknowledged "
            "FROM alert_deliveries WHERE chat_id=? "
            "ORDER BY rowid DESC LIMIT ?",
            (chat_id, limit),
        ) as cur:
            deliveries = await cur.fetchall()

    if not deliveries:
        await message.reply_text(
            "You haven't received any alerts yet.\n"
            "The next validated signal will arrive here automatically."
        )
        return

    lines = ["<b>📜 Your Recent Alerts</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━", ""]
    for alert_id, raw, attempts, acknowledged in deliveries:
        try:
            alert = json.loads(raw or "{}")
        except (ValueError, TypeError):
            continue
        analysis = alert.get("analysis") or {}
        if analysis.get("correction"):
            head = "⚠️ UPDATE"
        else:
            signals = analysis.get("signals") or []
            if not signals:
                continue
            s = signals[0]
            emoji = "🔴" if s.get("direction") == "SELL" else "🟢"
            head = f"{emoji} {s.get('direction')} {s.get('instrument')} @ {s.get('entry')}"
        ack = "✅" if acknowledged else "⏳"
        url = alert.get("source_url") or ""
        link = f'<a href="{esc(url)}">view</a>' if url else ""
        lines.append(f"{ack} {head}  {link}")

    if len(lines) <= 3:
        await message.reply_text("No alerts in your history yet.")
        return
    await message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True,
    )


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sample = {
        "alert_id": "test",
        "message_id": 0,
        "source_url": f"https://t.me/{config.CHANNEL_USERNAME}",
        "original_text": "This is a TEST alert. No trade was signaled.",
        "analysis": {
            "is_strategy": True,
            "confidence": 1.0,
            "signals": [{
                "direction": "SELL", "order_type": "MARKET", "instrument": "XAUUSD",
                "entry": 3898.00, "stop_loss": 3919.00, "take_profit": [3883.00, 3853.00],
            }],
        },
    }
    await update.effective_message.reply_text(
        "<i>This is a test notification — not a real signal.</i>\n\n" + format_alert(sample),
        parse_mode=ParseMode.HTML, disable_web_page_preview=True,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "<b>KOJO ALERT — Commands</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>📥 Subscription</b>\n"
        "/start — Subscribe and welcome\n"
        "/stop — Unsubscribe\n"
        "/status — Check your subscription\n"
        "/pause [30m|2h|1d|today] — Pause alerts\n"
        "/resume — Resume now\n\n"
        "<b>📊 Content</b>\n"
        "/latest — Latest validated strategy\n"
        "/history — Your recent alerts\n"
        "/test — Preview an alert (test only)\n\n"
        "<b>⚙️ Preferences</b>\n"
        "/settings — Filter BUY / SELL\n"
        "/menu — Open the menu\n\n"
        "<b>ℹ️ Info</b>\n"
        "/help — This message\n"
        "/about — About KOJO ALERT",
        parse_mode=ParseMode.HTML,
    )


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "<b>KOJO ALERT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Real-time trade alerts from <b>@kojoforextrades</b>, delivered in seconds.\n\n"
        "• Deterministic parser extracts every BUY/SELL setup\n"
        "• AI classifier confirms the post is actionable\n"
        "• Prices come from the parser, never from the AI\n"
        "• Dual delivery: Telegram + Android app\n\n"
        "<i>Not financial advice. Verify every level against the original post "
        "before acting.</i>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_diag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if config.ADMIN_CHAT_ID and update.effective_chat.id != config.ADMIN_CHAT_ID:
        await update.effective_message.reply_text("Admin only.")
        return
    db_path = context.application.bot_data["db_path"]
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM subscribers WHERE active=1") as cur:
            active_subs = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM subscribers") as cur:
            total_subs = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM source_messages") as cur:
            total_sources = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM source_messages WHERE status='pending'") as cur:
            pending = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM source_messages WHERE status='done'") as cur:
            done = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM source_messages WHERE status='failed'") as cur:
            failed = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT message_id,status,is_edit,error_count FROM source_messages ORDER BY message_id DESC LIMIT 5"
        ) as cur:
            recent = await cur.fetchall()
        async with db.execute("SELECT value FROM state WHERE key='last_seen_id'") as cur:
            row = await cur.fetchone()
            last_seen = row[0] if row else "none"
        async with db.execute("SELECT COUNT(*) FROM telegram_outbox WHERE delivered_at IS NULL") as cur:
            tg_pending = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM fcm_outbox_v2 WHERE sent_at IS NULL") as cur:
            fcm_pending = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM alert_deliveries") as cur:
            deliveries = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM alert_deliveries WHERE attempts=0") as cur:
            unsent = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT alert_id, chat_id, COUNT(*) as sends FROM delivery_log "
            "GROUP BY alert_id, chat_id HAVING sends > 3 ORDER BY sends DESC LIMIT 5"
        ) as cur:
            possible_dupes = await cur.fetchall()

    recent_str = "\n".join(
        f"  #{mid} — {status}{' (edit)' if is_edit else ''}"
        + (f" [errs={ec}]" if ec else "")
        for mid, status, is_edit, ec in recent
    ) or "  (none)"

    dupe_warning = ""
    if possible_dupes:
        dupe_warning = (
            "\n\n⚠️ <b>Possible duplicate deliveries</b>\n"
            + "\n".join(f"  {a} → {c} ({n} sends)" for a, c, n in possible_dupes)
        )

    await update.effective_message.reply_text(
        f"<b>🩺 KOJO ALERT Diagnostics</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Subscribers</b>\n"
        f"  Active: {active_subs} / Total: {total_subs}\n"
        f"<b>Last seen message ID:</b> {last_seen}\n\n"
        f"<b>Source messages</b>\n"
        f"  Total: {total_sources}\n"
        f"  Pending: {pending}\n"
        f"  Done: {done}\n"
        f"  Failed: {failed}\n\n"
        f"<b>Recent source messages</b>\n{recent_str}\n\n"
        f"<b>Outbox queues</b>\n"
        f"  Telegram pending: {tg_pending}\n"
        f"  FCM pending: {fcm_pending}\n"
        f"  Deliveries: {deliveries} total, {unsent} unclaimed"
        f"{dupe_warning}",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Inline callbacks
# ---------------------------------------------------------------------------

async def cb_acknowledge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Acknowledged ✅")
    alert_id = query.data.split(":", 1)[1]
    async with aiosqlite.connect(context.application.bot_data["db_path"]) as db:
        await db.execute(
            "UPDATE alert_deliveries SET acknowledged=1, next_attempt_at=NULL "
            "WHERE chat_id=? AND alert_id LIKE ?",
            (query.message.chat_id, alert_id + "%"),
        )
        await db.commit()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass
    await query.message.reply_text("✅ Acknowledged. No further reminders for this alert.")


async def cb_settings_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action = query.data.split(":", 1)[1]
    async with aiosqlite.connect(context.application.bot_data["db_path"]) as db:
        row = await _get_subscriber(db, query.message.chat_id)
        current = set((row[3] if row and row[3] else "BUY,SELL").split(","))
        target = "BUY" if action == "buy" else "SELL"
        if target in current:
            current.discard(target)
        else:
            current.add(target)
        if not current:
            current = {"BUY", "SELL"}
        new_val = ",".join(sorted(current))
        await db.execute("UPDATE subscribers SET filter_directions=? WHERE chat_id=?",
                         (new_val, query.message.chat_id))
        await db.commit()
    await query.answer(f"Filters: {new_val}")
    await _show_settings(query.message.chat_id, query.message, context)


async def cb_pause_preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    preset = query.data.split(":", 1)[1]
    duration = _parse_pause_arg(preset)
    if duration is None:
        await query.answer("Invalid pause duration.")
        return
    until = (_now() + duration).isoformat()
    async with aiosqlite.connect(context.application.bot_data["db_path"]) as db:
        await db.execute("UPDATE subscribers SET paused_until=? WHERE chat_id=?",
                         (until, query.message.chat_id))
        await db.commit()
    await query.answer(f"Paused until {until[:16].replace('T', ' ')} UTC")
    await _show_settings(query.message.chat_id, query.message, context)


async def cb_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    if action == "menu":
        await cmd_menu(update, context)
    elif action == "latest":
        await _show_latest(query.message, context)
    elif action == "history":
        await _show_history(query.message.chat_id, query.message, context)
    elif action == "settings":
        await _show_settings(query.message.chat_id, query.message, context)
    elif action == "help":
        await cmd_help(update, context)
    elif action == "resume":
        async with aiosqlite.connect(context.application.bot_data["db_path"]) as db:
            await db.execute("UPDATE subscribers SET active=1, paused_until=NULL WHERE chat_id=?",
                             (query.message.chat_id,))
            await db.commit()
        await query.answer("Alerts resumed ✅")
        await _show_settings(query.message.chat_id, query.message, context)


# ---------------------------------------------------------------------------
# Broadcast — atomic claim per delivery row
# ---------------------------------------------------------------------------

_SEND_SEM = asyncio.Semaphore(20)


def _subscriber_accepts(sub_row, alert: dict) -> bool:
    """sub_row = (chat_id, active, paused_until, filter_directions)."""
    active = sub_row[1]
    paused_until = sub_row[2]
    directions = sub_row[3] or "BUY,SELL"

    if not active:
        return False
    if _is_paused(paused_until):
        return False

    allowed = {d.strip().upper() for d in directions.split(",") if d.strip()}
    analysis = alert.get("analysis") or {}
    if analysis.get("correction"):
        return True
    signals = analysis.get("signals") or []
    return any((s.get("direction") or "").upper() in allowed for s in signals)


async def broadcast_alert(application: Application, db_path: str, alert: dict):
    alert_id = str(alert.get("alert_id") or alert["message_id"])
    message_id = int(alert["message_id"])

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT chat_id,active,paused_until,filter_directions FROM subscribers"
        ) as cur:
            all_subs = await cur.fetchall()
        for row in all_subs:
            try:
                if not _subscriber_accepts(row, alert):
                    continue
                chat_id = row[0]
                await db.execute(
                    "INSERT OR IGNORE INTO alert_deliveries"
                    "(chat_id,alert_id,message_id,alert_json,attempts,acknowledged,next_attempt_at) "
                    "VALUES(?,?,?,?,0,0,?)",
                    (chat_id, alert_id, message_id,
                     json.dumps(alert, ensure_ascii=False), config.utcnow()),
                )
            except Exception:
                log.exception("Skipping subscriber %s due to error", row[0] if row else "?")
                continue
        await db.commit()

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT chat_id FROM alert_deliveries "
            "WHERE alert_id=? AND acknowledged=0 AND attempts=0 AND next_attempt_at<=?",
            (alert_id, config.utcnow()),
        ) as cur:
            due = await cur.fetchall()

    if not due:
        log.info("Alert %s: no due deliveries", alert_id)
        return

    log.info("Alert %s: dispatching to %d subscriber(s)", alert_id, len(due))

    async def _one(chat_id: int):
        async with _SEND_SEM:
            await deliver_one(application, db_path, chat_id, alert_id, alert=alert, initial=True)
            await asyncio.sleep(0.05)

    await asyncio.gather(*(_one(chat_id) for (chat_id,) in due), return_exceptions=True)


async def deliver_one(application, db_path, chat_id, alert_id, alert=None, initial=False):
    """Deliver one alert to one chat with an ATOMIC CLAIM before the send."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT attempts,acknowledged,alert_json FROM alert_deliveries WHERE chat_id=? AND alert_id=?",
            (chat_id, alert_id),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return
        current_attempts, acknowledged, alert_json = row
        if acknowledged:
            return
        if alert is None:
            alert = json.loads(alert_json or "{}")

        # ATOMIC CLAIM (runs before send)
        if initial:
            claim_sql = (
                "UPDATE alert_deliveries SET attempts=attempts+1, next_attempt_at=?, last_error='sending' "
                "WHERE chat_id=? AND alert_id=? AND attempts=0 AND acknowledged=0"
            )
            claim_args = (config.utc_after(180), chat_id, alert_id)
        else:
            claim_sql = (
                "UPDATE alert_deliveries SET attempts=attempts+1, next_attempt_at=?, last_error='sending' "
                "WHERE chat_id=? AND alert_id=? AND attempts<3 AND acknowledged=0 AND next_attempt_at<=?"
            )
            claim_args = (config.utc_after(180), chat_id, alert_id, config.utcnow())

        claim = await db.execute(claim_sql, claim_args)
        await db.commit()
        if claim.rowcount != 1:
            return  # lost the race, or already acked, or not due

    kind = "initial" if initial else "reminder"
    try:
        sent = await application.bot.send_message(
            chat_id=chat_id,
            text=format_alert(alert, reminder=not initial),
            parse_mode=ParseMode.HTML,
            reply_markup=ack_keyboard(alert_id),
            disable_notification=False,
            protect_content=True,
            disable_web_page_preview=True,
        )
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE alert_deliveries SET last_error=NULL, tg_message_id=? WHERE chat_id=? AND alert_id=?",
                (getattr(sent, "message_id", None), chat_id, alert_id),
            )
            await db.execute(
                "INSERT INTO delivery_log(chat_id,alert_id,attempt,kind,sent_at,tg_message_id) "
                "VALUES(?,?,?,?,?,?)",
                (chat_id, alert_id, current_attempts + 1, kind,
                 config.utcnow(), getattr(sent, "message_id", None)),
            )
            await db.commit()
        log.info("Delivered %s to %s (attempt=%d, kind=%s, tg_msg_id=%s)",
                 alert_id, chat_id, current_attempts + 1, kind,
                 getattr(sent, "message_id", None))
    except (Forbidden, BadRequest) as exc:
        log.warning("Delivery to %s permanently failed: %s", chat_id, exc)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("UPDATE subscribers SET active=0 WHERE chat_id=?", (chat_id,))
            await db.execute(
                "UPDATE alert_deliveries SET next_attempt_at=NULL, last_error=? WHERE chat_id=? AND alert_id=?",
                (str(exc)[:500], chat_id, alert_id),
            )
            await db.commit()
    except RetryAfter as exc:
        retry_value = getattr(exc, "retry_after", 30)
        delay = int(retry_value.total_seconds()) if hasattr(retry_value, "total_seconds") else int(retry_value)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE alert_deliveries SET next_attempt_at=?, last_error=? WHERE chat_id=? AND alert_id=?",
                (config.utc_after(delay), str(exc)[:500], chat_id, alert_id),
            )
            await db.commit()
        log.warning("Telegram rate-limited %s; retry in %ss", chat_id, delay)
    except TelegramError as exc:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE alert_deliveries SET last_error=? WHERE chat_id=? AND alert_id=?",
                (str(exc)[:500], chat_id, alert_id),
            )
            await db.commit()
        log.warning("Telegram delivery failed to %s: %s", chat_id, exc)


async def reminder_loop(application: Application, db_path: str):
    while True:
        await asyncio.sleep(20)
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """SELECT d.chat_id, d.alert_id FROM alert_deliveries d
                   JOIN subscribers s ON s.chat_id=d.chat_id
                   WHERE d.acknowledged=0 AND d.attempts<3 AND d.next_attempt_at IS NOT NULL
                     AND d.next_attempt_at<=? AND s.active=1
                   ORDER BY d.next_attempt_at LIMIT 100""",
                (config.utcnow(),),
            ) as cur:
                rows = await cur.fetchall()
        for chat_id, alert_id in rows:
            try:
                await deliver_one(application, db_path, chat_id, alert_id, initial=False)
            except Exception:
                log.exception("reminder_loop delivery error for %s/%s", chat_id, alert_id)


# ---------------------------------------------------------------------------
# Error handlers (including polling 409 suppression)
# ---------------------------------------------------------------------------

async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("PTB handler error", exc_info=context.error)


def _polling_error_callback(exc: TelegramError) -> None:
    """Silence the deploy-transition 409. Everything else gets a warning."""
    if isinstance(exc, Conflict):
        # Expected while Render swaps old→new instance. PTB retries automatically.
        log.warning("409 Conflict: another instance is polling. Waiting for it to stop.")
        return
    if isinstance(exc, NetworkError):
        log.warning("Polling network error: %s", exc)
        return
    log.warning("Polling error: %s", exc)


def register_handlers(application: Application, db_path: str):
    application.bot_data["db_path"] = db_path
    application.bot_data["_polling_error_cb"] = _polling_error_callback
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("menu", cmd_menu))
    application.add_handler(CommandHandler("stop", cmd_stop))
    application.add_handler(CommandHandler("pause", cmd_pause))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("settings", cmd_settings))
    application.add_handler(CommandHandler("latest", cmd_latest))
    application.add_handler(CommandHandler("history", cmd_history))
    application.add_handler(CommandHandler("test", cmd_test))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("about", cmd_about))
    application.add_handler(CommandHandler("diag", cmd_diag))
    application.add_handler(CallbackQueryHandler(cb_acknowledge, pattern=r"^ack:[A-Za-z0-9_\-]{1,60}$"))
    application.add_handler(CallbackQueryHandler(cb_settings_toggle, pattern=r"^set:(buy|sell)$"))
    application.add_handler(CallbackQueryHandler(cb_pause_preset, pattern=r"^pause:(1h|6h|24h)$"))
    application.add_handler(CallbackQueryHandler(cb_action, pattern=r"^act:(menu|latest|history|settings|help|resume)$"))
    application.add_error_handler(_error_handler)