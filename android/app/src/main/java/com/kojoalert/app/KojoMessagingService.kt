package com.kojoalert.app

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

class KojoMessagingService : FirebaseMessagingService() {
    override fun onMessageReceived(message: RemoteMessage) {
        val data = message.data
        if (data.isEmpty()) return
        val payload = data.mapValues { it.value }
        NotificationHelper.show(this, payload)
    }
}
