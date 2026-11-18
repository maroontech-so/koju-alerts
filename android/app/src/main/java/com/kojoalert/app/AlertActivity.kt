package com.kojoalert.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private val AlertInk = Color(0xFF0B1018)
private val AlertPanel = Color(0xFF151D29)
private val AlertMuted = Color(0xFF9AA8B8)
private val AlertGold = Color(0xFFFFC857)

class AlertActivity : ComponentActivity() {
    private var alert: Map<String, String> = emptyMap()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        alert = AlertData.from(intent)
        render()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        alert = AlertData.from(intent)
        render()
    }

    private fun render() {
        setContent {
            AlertScreen(
                data = alert,
                onOpenSource = {
                    val url = alert[AlertData.EXTRA_SOURCE_URL].orEmpty()
                    if (url.isNotBlank()) startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                },
                onClose = { finish() }
            )
        }
    }
}

@Composable
private fun AlertScreen(data: Map<String, String>, onOpenSource: () -> Unit, onClose: () -> Unit) {
    val isCorrection = data[AlertData.EXTRA_CORRECTION] == "true"
    MaterialTheme(colorScheme = darkColorScheme(background = AlertInk, surface = AlertPanel, primary = AlertGold)) {
        Surface(color = AlertInk, modifier = Modifier.fillMaxSize()) {
            Column(
                modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(22.dp),
                verticalArrangement = Arrangement.spacedBy(17.dp)
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("KOJO ALERT", color = AlertGold, fontSize = 12.sp, fontWeight = FontWeight.Black, letterSpacing = 2.sp)
                        Text(if (isCorrection) "SOURCE POST UPDATED" else "NEW STRATEGY DETECTED", color = Color.White, fontSize = 21.sp, fontWeight = FontWeight.Bold)
                    }
                    Surface(color = if (isCorrection) Color(0xFF542D2D) else Color(0xFF49351A), shape = RoundedCornerShape(30.dp)) {
                        Text(if (isCorrection) "UPDATE" else "URGENT", color = if (isCorrection) Color(0xFFFF8A80) else AlertGold, fontSize = 10.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(horizontal = 11.dp, vertical = 8.dp))
                    }
                }
                Surface(color = AlertPanel, shape = RoundedCornerShape(20.dp), modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(19.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(if (isCorrection) "ACTION REQUIRED" else data[AlertData.EXTRA_DIRECTION].orEmpty().ifBlank { "TRADE SETUP" }, color = if (isCorrection) Color(0xFFFF8A80) else AlertGold, fontSize = 12.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.2.sp)
                        Text(if (isCorrection) "Previous signal may be invalid" else data[AlertData.EXTRA_INSTRUMENT].orEmpty().ifBlank { "Instrument not specified" }, color = Color.White, fontSize = if (isCorrection) 22.sp else 29.sp, fontWeight = FontWeight.Black)
                        Text(if (isCorrection) "The source channel post has been edited. Check the latest version before acting." else "Detected from @kojoforextrades", color = AlertMuted, fontSize = 12.sp)
                    }
                }
                if (!isCorrection) {
                    Column(verticalArrangement = Arrangement.spacedBy(9.dp)) {
                        DetailRow("DIRECTION", data[AlertData.EXTRA_DIRECTION])
                        DetailRow("ORDER TYPE", data[AlertData.EXTRA_ORDER_TYPE])
                        DetailRow("ENTRY", data[AlertData.EXTRA_ENTRY])
                        DetailRow("STOP LOSS", data[AlertData.EXTRA_STOP_LOSS])
                        DetailRow("TAKE PROFIT", data[AlertData.EXTRA_TAKE_PROFIT])
                        DetailRow("MODEL CONFIDENCE", data[AlertData.EXTRA_CONFIDENCE]?.let { "${(it.toFloatOrNull()?.times(100))?.toInt() ?: it}%" })
                    }
                }
                Surface(color = AlertPanel, shape = RoundedCornerShape(18.dp), modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(17.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("ORIGINAL CHANNEL MESSAGE", color = AlertMuted, fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.2.sp)
                        Text(data[AlertData.EXTRA_ORIGINAL_TEXT].orEmpty().ifBlank { "Original message text is unavailable in this alert." }, color = Color.White, fontSize = 13.sp, lineHeight = 20.sp)
                    }
                }
                Button(onClick = onOpenSource, modifier = Modifier.fillMaxWidth().height(54.dp), colors = ButtonDefaults.buttonColors(containerColor = if (isCorrection) Color(0xFFFF8A80) else AlertGold, contentColor = AlertInk), shape = RoundedCornerShape(14.dp)) {
                    Text(if (isCorrection) "REVIEW UPDATED TELEGRAM POST" else "OPEN ORIGINAL TELEGRAM POST", fontWeight = FontWeight.Black, fontSize = 12.sp)
                }
                OutlinedButton(onClick = onClose, modifier = Modifier.fillMaxWidth(), colors = ButtonDefaults.outlinedButtonColors(contentColor = Color.White)) {
                    Text("CLOSE ALERT")
                }
                Text("Automated detection can be wrong. Confirm every level and instruction in the original post before trading. This is not financial advice.", color = AlertMuted, fontSize = 10.sp, lineHeight = 16.sp)
            }
        }
    }
}

@Composable
private fun DetailRow(label: String, value: String?) {
    Surface(color = AlertPanel, shape = RoundedCornerShape(13.dp), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(horizontal = 15.dp, vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(label, color = AlertMuted, fontSize = 10.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
            Text(value?.takeIf { it.isNotBlank() } ?: "Not specified", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}
