# KOJO ALERT — Production-oriented build

KOJO ALERT monitors `@kojoforextrades` using an authorised Telethon user session, extracts trade levels deterministically, asks Groq to classify whether the post is genuinely an actionable strategy, and sends validated alerts through **both Telegram and Android FCM**.

## Project structure

```text
kojo-alert/
├── app.py                 # Telegram monitor, recovery, Groq gate, durable FCM outbox
├── bot.py                 # Telegram subscriptions, deliveries, acknowledgement/reminders
├── signal_parser.py       # Deterministic KOJO trade parser and price-geometry validation
├── push.py                # Firebase Admin SDK topic publisher
├── test_signal_parser.py  # Offline parser tests
├── generate_session.py    # Local Telethon StringSession setup helper
├── requirements.txt
├── .env.example
├── render.yaml
└── android/               # Kotlin Android application
```

## 1. How a message becomes an alert

1. Telethon receives a new post or edit from the source channel.
2. The complete text and message ID are persisted in SQLite before processing.
3. `signal_parser.py` extracts each BUY/SELL setup, order type, entry, stop loss and TP levels. It accepts formats such as `SELL XAUUSD @ 3898.00`, `SELL STOP XAUUSD@3888.15`, `TP1. 3883.00`, `TP1@3873.15`, and comma-formatted prices such as `4,111.36`.
4. The parser checks that BUY stop losses are below entry and BUY targets above entry; for SELL, the reverse must hold. A complete signal needs an entry, stop loss and at least one TP. Incomplete or geometrically inconsistent blocks are not broadcast.
5. Groq classifies the original post as a genuine actionable strategy. Groq does **not** supply or modify prices. Default model: `openai/gpt-oss-120b`; configure `GROQ_MODEL` to another currently available model if needed.
6. The validated event is queued for Telegram delivery and Firebase delivery independently.
7. Telegram subscribers can acknowledge alerts. Up to two reminders are sent if not acknowledged.

A single source post can contain multiple trades. Telegram displays them in one message; Android receives one notification per parsed trade. A changed Telegram post is treated as a new version event, with a unique content-derived ID. Replayed identical posts are deduplicated. If an already-alerted post is edited, old reminders are stopped; a newly valid version generates a fresh signal, while an edit that removes the validated setup generates a correction alert telling subscribers to re-check the source.

## 2. Local setup

Python 3.11+ is recommended.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS:
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with:

- `API_ID`, `API_HASH`: Telegram API credentials from https://my.telegram.org.
- `SESSION_STRING`: authorised Telethon StringSession for the Telegram account already subscribed to KOJOFOREX.
- `BOT_TOKEN`: Telegram bot token from BotFather.
- `GROQ_API_KEY`: your Groq API key.
- `FIREBASE_SERVICE_ACCOUNT_JSON`: the complete Firebase Admin service-account JSON on one line (needed for Android push).
- `FIREBASE_PROJECT_ID`: Firebase project ID.

Never commit `.env`, the Telethon session, Firebase private key, or Android `google-services.json`. The included `.gitignore` excludes these secrets.

### Generate the Telethon session

Set `API_ID` and `API_HASH` in your local `.env`, then run `python generate_session.py`. Complete the Telegram login and copy the printed StringSession into Render as `SESSION_STRING`. Keep it private. Do not send it to anyone or commit it to GitHub. The account must already have access to the source channel.

## 3. Firebase / Android

1. Create a Firebase project.
2. Register Android package `com.kojoalert.app`.
3. Download `google-services.json` into `android/app/`.
4. Enable Firebase Cloud Messaging.
5. Generate a Firebase Admin SDK service-account key. Store its JSON in the `FIREBASE_SERVICE_ACCOUNT_JSON` environment variable on Render; do not commit it.
6. The app subscribes to the public topic `kojo-alerts`.

FCM topic publishing is accepted by Firebase; it does not prove a handset displayed the alert. Android permission, notification-channel settings, network availability, Do Not Disturb and OEM power management remain device-controlled.

## 4. Run locally

```bash
python -m unittest -v test_signal_parser.py
python -m py_compile app.py bot.py push.py signal_parser.py
python app.py
```

The first production start establishes a baseline at the latest channel message and does not broadcast old channel history. After that, missed messages newer than the saved message ID are recovered after reconnect/restart.

## 5. Render deployment

Deploy as one Render Background Worker using `render.yaml`. Add all required secrets in Render Environment. Keep one worker instance and retain the persistent disk mounted at `/var/data`; SQLite and the Telethon session are not configured for multiple replicas.

Required Render environment variables:

`API_ID`, `API_HASH`, `SESSION_STRING`, `BOT_TOKEN`, `GROQ_API_KEY`, `FIREBASE_SERVICE_ACCOUNT_JSON`. Set `FIREBASE_PROJECT_ID` too if it is not included in the service-account JSON.

Optional: `GROQ_MODEL`, `CONFIDENCE_THRESHOLD`, `FCM_TOPIC`, `CHANNEL_USERNAME`, `DATABASE_PATH`, `LOG_LEVEL`.

## 6. Reliability and limitations

- SQLite uses WAL mode, a persistent database, unique event IDs, a pending-work retry loop, and separate durable Telegram and FCM outboxes with exponential retry.
- Telegram delivery has per-subscriber durable records, acknowledgement and up to two reminders. If a user blocks the bot, that subscriber is deactivated.
- The database stores an alert snapshot for reminders, so a later edit does not rewrite an earlier alert's content.
- Telegram and FCM delivery are at-least-once, not mathematically exactly-once. A crash after a provider accepts a message but before the database records success can cause a repeat. Android uses a stable notification ID per signal so repeat pushes update the same notification while it remains present.
- Text-only parsing is implemented. Image-only or image-embedded levels are not yet OCR/vision analysed; such posts may be missed. Do not rely on KOJO ALERT for execution or as a substitute for checking the source post.
- A valid parser result and Groq classification are safeguards, not a guarantee that the trader's original signal is correct. Always open and verify the original Telegram post.
- This is an alert distribution system, not a trading bot. It does not place orders or provide financial advice.

## 7. Parser examples

Accepted examples include:

```text
SELL XAUUSD @ 3898.00
TP1. 3883.00
TP2.3853.00
SL. 3919.00

SELL STOP XAUUSD@3888.15
TP1@3873.15
TP2@3843.15
SL@3903.15
```

Malformed, incomplete or directionally inconsistent signals are rejected from broadcast and remain represented in the stored source analysis for debugging.
"# koju-alerts" 
