package dev.takton.takton_mobile

import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * Expose [android.view.DisplayCutout] as **physical pixels**.
 *
 * Flutter must convert with [MediaQuery.devicePixelRatio] — NOT
 * [android.util.DisplayMetrics.density], which can diverge on OEM
 * "display size" scaling (Huawei / Xiaomi) and makes the island drift.
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

    private fun readCutouts(): Map<String, Any> {
        val metrics = resources.displayMetrics
        val meta = mapOf(
            "density" to metrics.density.toDouble(),
            "densityDpi" to metrics.densityDpi.toDouble(),
            "widthPx" to metrics.widthPixels.toDouble(),
            "heightPx" to metrics.heightPixels.toDouble(),
        )
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) {
            return meta + mapOf("rects" to emptyList<Map<String, Double>>())
        }
        val decor = window?.decorView
            ?: return meta + mapOf("rects" to emptyList<Map<String, Double>>())
        val insets = decor.rootWindowInsets
            ?: return meta + mapOf("rects" to emptyList<Map<String, Double>>())
        val cutout = insets.displayCutout
            ?: return meta + mapOf("rects" to emptyList<Map<String, Double>>())

        // Physical px as-is — Flutter divides by devicePixelRatio.
        val rects = cutout.boundingRects.map { r ->
            mapOf(
                "l" to r.left.toDouble(),
                "t" to r.top.toDouble(),
                "r" to r.right.toDouble(),
                "b" to r.bottom.toDouble(),
            )
        }
        return meta + mapOf("rects" to rects)
    }
}
