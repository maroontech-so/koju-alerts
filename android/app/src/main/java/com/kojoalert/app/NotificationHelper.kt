package com.kojoalert.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.media.AudioAttributes
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

object NotificationHelper {
    const val CHANNEL_ID = "kojo_urgent_alerts"
    private const val CHANNEL_NAME = "Urgent trading alerts"

    fun createChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = context.getSystemService(NotificationManager::class.java)
            val channel = NotificationChannel(
                CHANNEL_ID,
                CHANNEL_NAME,
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Prominent notifications for new KOJOFOREX trading strategies"
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 350, 180, 350, 180, 650)
                setSound(
                    Settings.System.DEFAULT_NOTIFICATION_URI,
                    AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION).build()
                )
                setShowBadge(true)
                lockscreenVisibility = android.app.Notification.VISIBILITY_PUBLIC
            }
            manager.createNotificationChannel(channel)
        }
    }

    fun show(context: Context, data: Map<String, String>) {
        createChannel(context)
        val alertId = data[AlertData.EXTRA_ALERT_ID].orEmpty().ifBlank { System.currentTimeMillis().toString() }
        val detailIntent = Intent(context, AlertActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            data.forEach { (key, value) -> putExtra(key, value) }
        }
        val contentPendingIntent = PendingIntent.getActivity(
            context, alertId.hashCode(), detailIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val sourceUrl = data[AlertData.EXTRA_SOURCE_URL].orEmpty()
        val openSourceIntent = Intent(Intent.ACTION_VIEW, Uri.parse(sourceUrl)).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        val sourcePendingIntent = PendingIntent.getActivity(
            context, (alertId + "source").hashCode(), openSourceIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val title = data[AlertData.EXTRA_TITLE].orEmpty().ifBlank { "KOJO ALERT — NEW STRATEGY" }
        val body = data[AlertData.EXTRA_BODY].orEmpty().ifBlank { "A new trading strategy was detected." }
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_kojo_notification)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText("$body\nTap to view the complete strategy and original Telegram post."))
            .setCategory(NotificationCompat.CATEGORY_EVENT)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setAutoCancel(true)
            .setOngoing(false)
            .setContentIntent(contentPendingIntent)
            .addAction(android.R.drawable.ic_menu_view, "OPEN TELEGRAM POST", sourcePendingIntent)
            .build()

        try {
            NotificationManagerCompat.from(context).notify(alertId.hashCode(), notification)
        } catch (_: SecurityException) {
            // Android 13+: user has not granted notification permission.
        }
    }
}
