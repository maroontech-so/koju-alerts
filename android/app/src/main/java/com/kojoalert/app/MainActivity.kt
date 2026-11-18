package com.kojoalert.app

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.firebase.messaging.FirebaseMessaging

private val Ink = Color(0xFF0B1018)
private val Panel = Color(0xFF151D29)
private val Muted = Color(0xFF9AA8B8)
private val Accent = Color(0xFFFFC857)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        NotificationHelper.createChannel(this)
        setContent { KojoHome(onOpenChannel = { openChannel() }) }
        subscribeToAlerts()
    }

    private fun subscribeToAlerts() {
        FirebaseMessaging.getInstance().subscribeToTopic("kojo-alerts")
            .addOnSuccessListener { }
    }

    private fun openChannel() {
        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://t.me/kojoforextrades")))
    }
}

@Composable
private fun KojoHome(onOpenChannel: () -> Unit) {
    var notificationsEnabled by remember { mutableStateOf(true) }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> notificationsEnabled = granted }

    MaterialTheme(colorScheme = darkColorScheme(
        background = Ink, surface = Panel, primary = Accent,
        onBackground = Color.White, onSurface = Color.White
    )) {
        Surface(modifier = Modifier.fillMaxSize(), color = Ink) {
            Column(
                modifier = Modifier.fillMaxSize().padding(horizontal = 22.dp, vertical = 28.dp),
                verticalArrangement = Arrangement.spacedBy(22.dp)
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("KOJO", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Bold, letterSpacing = 4.sp)
                        Text("ALERT", color = Accent, fontSize = 30.sp, fontWeight = FontWeight.Black, letterSpacing = 2.sp)
                    }
                    Surface(color = Color(0xFF263447), shape = RoundedCornerShape(50)) {
                        Text("LIVE MONITOR", color = Color(0xFFB8D7FF), fontSize = 10.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
                    }
                }

                Surface(color = Panel, shape = RoundedCornerShape(22.dp), modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text("STRATEGY ALERTS", color = Muted, fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.5.sp)
                        Text("Stay ahead of the signal.", color = Color.White, fontSize = 25.sp, fontWeight = FontWeight.Bold)
                        Text("KOJO ALERT watches the KOJOFOREX channel and notifies you when an actionable trading strategy is detected.", color = Muted, fontSize = 14.sp, lineHeight = 21.sp)
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(9.dp)) {
                            Box(Modifier.size(8.dp).background(Color(0xFF48D597), RoundedCornerShape(50)))
                            Text("Android push + Telegram fallback", color = Color(0xFFB9C8D8), fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                        }
                    }
                }

                Surface(color = Panel, shape = RoundedCornerShape(18.dp), modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("NOTIFICATION DELIVERY", color = Color.White, fontWeight = FontWeight.Bold, fontSize = 14.sp)
                        Text("High-importance heads-up notification. Tap it to open the dedicated KOJO ALERT screen, then jump directly to the source post.", color = Muted, fontSize = 13.sp, lineHeight = 20.sp)
                        Text("The app does not force-open over other apps. Android controls interruption, lock-screen display and Do Not Disturb behaviour.", color = Muted, fontSize = 11.sp, lineHeight = 17.sp)
                    }
                }

                Surface(color = Panel, shape = RoundedCornerShape(18.dp), modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text("SETUP", color = Color.White, fontWeight = FontWeight.Bold, fontSize = 14.sp)
                        Text("1. Allow notifications for KOJO ALERT.\n2. Set ‘Urgent trading alerts’ to High importance and enable sound/vibration in Android settings.\n3. Keep Telegram notifications enabled as the fallback channel.", color = Muted, fontSize = 13.sp, lineHeight = 21.sp)
                        if (Build.VERSION.SDK_INT >= 33) {
                            Button(
                                onClick = { permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS) },
                                modifier = Modifier.fillMaxWidth(),
                                colors = ButtonDefaults.buttonColors(containerColor = Accent, contentColor = Ink)
                            ) { Text("ENABLE NOTIFICATIONS", fontWeight = FontWeight.Bold) }
                        }
                    }
                }
                Spacer(Modifier.weight(1f))
                OutlinedButton(onClick = onOpenChannel, modifier = Modifier.fillMaxWidth(), colors = ButtonDefaults.outlinedButtonColors(contentColor = Color.White)) {
                    Text("OPEN KOJOFOREX TELEGRAM CHANNEL")
                }
                Text("Automated signal detection • Verify every setup at source", color = Muted, fontSize = 10.sp, modifier = Modifier.align(Alignment.CenterHorizontally))
            }
        }
    }
}
