package com.jarvis.app

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val Bg = Color(0xFF03060C)
val Panel = Color(0xFF0A1220)
val PanelHi = Color(0xFF101C30)
val Cyan = Color(0xFF56E1FF)
val Amber = Color(0xFFFFB454)
val Violet = Color(0xFF8B7BFF)
val TextMain = Color(0xFFE8F4FF)
val TextDim = Color(0xFF8FA3BF)
val Danger = Color(0xFFFF6B6B)
val Ok = Color(0xFF5CE1A0)

private val scheme = darkColorScheme(
    primary = Cyan,
    onPrimary = Color(0xFF00131B),
    secondary = Amber,
    onSecondary = Color(0xFF1A0F00),
    tertiary = Violet,
    background = Bg,
    onBackground = TextMain,
    surface = Panel,
    onSurface = TextMain,
    surfaceVariant = PanelHi,
    onSurfaceVariant = TextDim,
    outline = Color(0xFF1E2C42),
    error = Danger,
)

@Composable
fun JarvisTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = scheme, content = content)
}
