"""Deterministic KOJO trade parser. Extracts structured signals from free text.

This module is the ONLY source of prices. The Groq classifier never sees or
produces levels. Anything this parser rejects is never broadcast.

A signal is considered "complete" only if it has:
  - a direction (BUY / SELL, optionally with LIMIT/STOP order type)
  - an instrument (e.g. XAUUSD, EURUSD)
  - an entry price
  - a stop loss
  - at least one take-profit
  - geometrically consistent prices (SL below entry for BUY, etc.)
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("kojo-alert.parser")

# ---------------------------------------------------------------------------
# Price / number helpers
# ---------------------------------------------------------------------------

# Matches any full numeric token, regardless of separator convention:
#   3883 | 3883.00 | 4,111.36 | 4.111,36 | 1,234,567 | 1.0850 | 68,432
# The regex does NOT decide whether a comma is a thousands separator or a
# decimal marker — that decision is made in _to_float(). This avoids the
# ordered-alternation trap where a shorter prefix match wins and truncates
# the token (e.g. "3898.00" -> "389").
_PRICE = r"\d+(?:[.,]\d+)*"

_DIRECTION = r"(BUY|SELL)"
_ORDER = r"(?:LIMIT|STOP|MARKET)?"
_INSTRUMENT = r"([A-Z]{3,10}(?:[/\-_]?[A-Z]{3,6})?)"

# "SELL XAUUSD @ 3898.00", "SELL STOP XAUUSD@3888.15", "BUY LIMIT EURUSD 1.0850"
_HEADER_RE = re.compile(
    rf"\b{_DIRECTION}\b\s*(?P<order>LIMIT|STOP|MARKET)?\s*"
    rf"{_INSTRUMENT}\s*(?:@|AT|ENTRY)?\s*[:=]?\s*(?P<entry>{_PRICE})",
    re.IGNORECASE,
)

# "SL 3919.00", "SL. 3919", "STOP LOSS @ 3919"
_SL_RE = re.compile(
    rf"\b(?:SL|S\.?L\.?|STOP\s*LOSS|STOPLOSS)\b\.?\s*(?:@|AT|:|=)?\s*(?P<sl>{_PRICE})",
    re.IGNORECASE,
)

# "TP1. 3883.00", "TP1@3873.15", "TP 3853", "TAKE PROFIT 1: 3883"
_TP_RE = re.compile(
    rf"\b(?:TP|T\.?P\.?|TAKE\s*PROFIT)\s*\d*\.?\s*(?:@|AT|:|=)?\s*(?P<tp>{_PRICE})",
    re.IGNORECASE,
)

# Instruments that sometimes appear in KOJO posts without a separator.
_KNOWN_INSTRUMENTS = {
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD",
    "USDCAD", "NZDUSD", "EURJPY", "GBPJPY", "XAUUSDT", "BTCUSDT", "ETHUSDT",
    "US30", "NAS100", "GER40", "UK100", "SPX500", "USOIL", "UKOIL",
}


def _to_float(raw: str) -> float | None:
    """Convert a numeric token to float, disambiguating thousands vs decimal.

    Rules (applied in order):
      * both ',' and '.' present  -> the last separator is the decimal point
      * only ','  (one, 1-2 trailing digits)  -> European decimal (2,50 -> 2.50)
      * only ','  (otherwise)     -> thousands separator  (4,111 -> 4111)
      * only '.'  or no separator -> plain float
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None

    has_comma = "," in s
    has_dot = "." in s

    if has_comma and has_dot:
        if s.rfind(",") > s.rfind("."):
            # European: 4.111,36
            s = s.replace(".", "").replace(",", ".")
        else:
            # Anglo: 4,111.36
            s = s.replace(",", "")
    elif has_comma:
        parts = s.split(",")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            # European decimal: 2,50 -> 2.50
            s = s.replace(",", ".")
        else:
            # Thousands: 4,111 -> 4111 ; 1,234,567 -> 1234567
            s = s.replace(",", "")

    try:
        value = float(s)
    except ValueError:
        return None
    if value <= 0 or value > 1_000_000_000:
        return None
    return value


def _normalise_instrument(raw: str) -> str:
    inst = raw.upper().replace("/", "").replace("-", "").replace("_", "")
    if inst in _KNOWN_INSTRUMENTS:
        return inst
    # Accept anything that looks like a standard pair even if not in our list.
    if 5 <= len(inst) <= 12 and inst.isalpha():
        return inst
    return inst


def _is_geometry_valid(direction: str, entry: float, sl: float, tps: list[float]) -> tuple[bool, str]:
    if direction == "BUY":
        if not sl < entry:
            return False, f"BUY stop loss {sl} must be below entry {entry}"
        if any(tp <= entry for tp in tps):
            return False, f"BUY targets must be above entry {entry}"
    else:  # SELL
        if not sl > entry:
            return False, f"SELL stop loss {sl} must be above entry {entry}"
        if any(tp >= entry for tp in tps):
            return False, f"SELL targets must be below entry {entry}"
    return True, ""


# ---------------------------------------------------------------------------
# Block splitting
# ---------------------------------------------------------------------------

def _split_blocks(text: str) -> list[str]:
    """Split a multi-trade post into per-signal blocks.

    A new block starts at each BUY/SELL header. Everything before the first
    header is discarded (it's usually commentary).
    """
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return []
    blocks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append(text[start:end])
    return blocks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_signals(text: str) -> list[dict]:
    """Extract every candidate signal, including incomplete ones (for audit)."""
    if not text or not text.strip():
        return []
    results: list[dict] = []
    for block in _split_blocks(text):
        results.append(_parse_block(block))
    return results


def _parse_block(block: str) -> dict:
    header = _HEADER_RE.search(block)
    if not header:
        return {
            "direction": None, "order_type": None, "instrument": None,
            "entry": None, "stop_loss": None, "take_profit": [],
            "complete": False, "reason": "No BUY/SELL header found.",
            "raw_block": block.strip()[:500],
        }

    direction = header.group(1).upper()
    order_type = (header.group("order") or "MARKET").upper()
    instrument = _normalise_instrument(header.group(3))
    entry = _to_float(header.group("entry"))

    # Stop loss: take the LAST SL match in the block (posts sometimes restate).
    sl_matches = list(_SL_RE.finditer(block))
    stop_loss = _to_float(sl_matches[-1].group("sl")) if sl_matches else None

    # Take profits: dedupe by value, preserve order.
    tps: list[float] = []
    seen: set[float] = set()
    for m in _TP_RE.finditer(block):
        val = _to_float(m.group("tp"))
        if val is not None and val not in seen:
            seen.add(val)
            tps.append(val)

    result = {
        "direction": direction,
        "order_type": order_type,
        "instrument": instrument,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": tps,
        "complete": False,
        "reason": "",
        "raw_block": block.strip()[:500],
    }

    if entry is None:
        result["reason"] = "Entry price missing or invalid."
        return result
    if stop_loss is None:
        result["reason"] = "Stop loss missing or invalid."
        return result
    if not tps:
        result["reason"] = "No take-profit levels found."
        return result

    ok, why = _is_geometry_valid(direction, entry, stop_loss, tps)
    if not ok:
        result["reason"] = why
        return result

    result["complete"] = True
    result["reason"] = "ok"
    return result


def valid_signals(text: str) -> list[dict]:
    """Return only complete, geometry-valid signals. This is the broadcast gate."""
    return [s for s in parse_signals(text) if s.get("complete") is True]