package dev.takton.takton_mobile

import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * Expose [android.view.DisplayCutout] bounding rects so the Flutter
 * Dynamic Island can size to the real front-camera hole (center punch).
 *
 * Industry approach (DynamicSpot / OEM islands): query cutout → draw
 * stadium capsule around those bounds in the status-bar band.
 */
class MainActivity : FlutterActivity() {
    private val channelName = "takton/display_cutout"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "getCutouts" -> result.success(readCutouts())
                    else -> result.notImplemented()
                }
            }
    }

    private fun readCutouts(): List<Map<String, Double>> {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) {
            return emptyList()
        }
        val density = resources.displayMetrics.density.toDouble().coerceAtLeast(0.5)
        val decor = window?.decorView ?: return emptyList()
        val insets = decor.rootWindowInsets ?: return emptyList()
        val cutout = insets.displayCutout ?: return emptyList()
        return cutout.boundingRects.map { r ->
            mapOf(
                "left" to r.left / density,
                "top" to r.top / density,
                "right" to r.right / density,
                "bottom" to r.bottom / density,
                "width" to r.width() / density,
                "height" to r.height() / density,
            )
        }
    }
}
