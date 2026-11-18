package com.kojoalert.app

import android.content.Intent

object AlertData {
    const val EXTRA_ALERT_ID = "alert_id"
    const val EXTRA_TITLE = "title"
    const val EXTRA_BODY = "body"
    const val EXTRA_SOURCE_URL = "source_url"
    const val EXTRA_INSTRUMENT = "instrument"
    const val EXTRA_DIRECTION = "direction"
    const val EXTRA_ORDER_TYPE = "order_type"
    const val EXTRA_ENTRY = "entry"
    const val EXTRA_STOP_LOSS = "stop_loss"
    const val EXTRA_TAKE_PROFIT = "take_profit"
    const val EXTRA_CONFIDENCE = "confidence"
    const val EXTRA_ORIGINAL_TEXT = "original_text"
    const val EXTRA_CORRECTION = "correction"

    fun from(intent: Intent): Map<String, String> = mapOf(
        EXTRA_ALERT_ID to intent.getStringExtra(EXTRA_ALERT_ID).orEmpty(),
        EXTRA_TITLE to intent.getStringExtra(EXTRA_TITLE).orEmpty(),
        EXTRA_BODY to intent.getStringExtra(EXTRA_BODY).orEmpty(),
        EXTRA_SOURCE_URL to intent.getStringExtra(EXTRA_SOURCE_URL).orEmpty(),
        EXTRA_INSTRUMENT to intent.getStringExtra(EXTRA_INSTRUMENT).orEmpty(),
        EXTRA_DIRECTION to intent.getStringExtra(EXTRA_DIRECTION).orEmpty(),
        EXTRA_ORDER_TYPE to intent.getStringExtra(EXTRA_ORDER_TYPE).orEmpty(),
        EXTRA_ENTRY to intent.getStringExtra(EXTRA_ENTRY).orEmpty(),
        EXTRA_STOP_LOSS to intent.getStringExtra(EXTRA_STOP_LOSS).orEmpty(),
        EXTRA_TAKE_PROFIT to intent.getStringExtra(EXTRA_TAKE_PROFIT).orEmpty(),
        EXTRA_CONFIDENCE to intent.getStringExtra(EXTRA_CONFIDENCE).orEmpty(),
        EXTRA_ORIGINAL_TEXT to intent.getStringExtra(EXTRA_ORIGINAL_TEXT).orEmpty(),
        EXTRA_CORRECTION to intent.getStringExtra(EXTRA_CORRECTION).orEmpty()
    )
}
