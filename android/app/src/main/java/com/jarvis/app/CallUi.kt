package com.jarvis.app

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
fun JarvisCallOrb(
    tone: Color,
    level: Float,
    active: Boolean,
    modifier: Modifier = Modifier,
    size: Dp = 184.dp,
) {
    val transition = rememberInfiniteTransition(label = "callorb")
    val pulse by transition.animateFloat(
        0.94f, 1.06f,
        animationSpec = infiniteRepeatable(tween(1500, easing = FastOutSlowInEasing), RepeatMode.Reverse),
        label = "pulse",
    )
    val halo by transition.animateFloat(
        0.90f, 1.22f,
        animationSpec = infiniteRepeatable(tween(1700, easing = FastOutSlowInEasing), RepeatMode.Reverse),
        label = "halo",
    )
    val haloAlpha by transition.animateFloat(
        0.42f, 0.08f,
        animationSpec = infiniteRepeatable(tween(1700, easing = FastOutSlowInEasing), RepeatMode.Reverse),
        label = "haloalpha",
    )
    val spin by transition.animateFloat(
        0f, 360f,
        animationSpec = infiniteRepeatable(tween(9000)),
        label = "spin",
    )
    val scale = 1f + level * 0.12f
    val glow = if (active) tone else Color(0xFF38506B)
    Canvas(modifier.size(size).graphicsLayer { scaleX = scale; scaleY = scale }) {
        val radius = this.size.minDimension / 2f
        drawCircle(
            Brush.radialGradient(listOf(glow.copy(alpha = 0.34f), Color.Transparent), center = center, radius = radius),
            radius, center,
        )
        if (active) {
            drawCircle(glow.copy(alpha = haloAlpha), radius * halo, center, style = Stroke(1.5f))
        }
        drawCircle(glow.copy(alpha = 0.45f), radius * 0.42f, center, style = Stroke(1.5f))
        rotate(spin, center) {
            val topLeft = Offset(center.x - radius * 0.72f, center.y - radius * 0.72f)
            val arcSize = Size(radius * 1.44f, radius * 1.44f)
            drawArc(glow, -30f, 110f, false, topLeft, arcSize, style = Stroke(3f, cap = StrokeCap.Round))
            drawArc(Amber.copy(alpha = 0.8f), 160f, 60f, false, topLeft, arcSize, style = Stroke(2f, cap = StrokeCap.Round))
        }
        drawCircle(
            Brush.radialGradient(listOf(Color.White, glow), center = center, radius = radius * 0.24f * pulse),
            radius * 0.22f * pulse, center,
        )
    }
}

@Composable
fun CallControl(
    label: String,
    tone: Color,
    onClick: () -> Unit,
    size: Dp = 66.dp,
    filled: Boolean = false,
) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Surface(
            modifier = Modifier.size(size).clickable(onClick = onClick),
            shape = CircleShape,
            color = if (filled) tone else tone.copy(alpha = 0.14f),
            border = androidx.compose.foundation.BorderStroke(1.5.dp, tone.copy(alpha = if (filled) 1f else 0.8f)),
        ) {
            Box(contentAlignment = Alignment.Center) {
                Text(
                    label,
                    color = if (filled) Color(0xFF04140C) else tone,
                    fontSize = if (size >= 76.dp) 12.sp else 10.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.sp,
                )
            }
        }
    }
}
