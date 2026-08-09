package dev.tevarn.mobile

import android.annotation.SuppressLint
import android.os.Bundle
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback

/**
 * Android shell: WebView + optional embedded Rust host (JNI).
 *
 * Release: Native.startHost() → http://127.0.0.1:<port>/
 * Debug: intent extra TEVARN_MOBILE_URL or assets fallback.
 */
class MainActivity : ComponentActivity() {
    private lateinit var webView: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        webView = WebView(this)
        setContentView(webView)

        val settings: WebSettings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.mediaPlaybackRequiresUserGesture = false
        // Only allow mixed content for local embedded host
        settings.mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView?,
                request: WebResourceRequest?,
            ): Boolean {
                val host = request?.url?.host ?: return false
                // Stay in-app for loopback host; block arbitrary navigation
                return host != "127.0.0.1" && host != "localhost"
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest?) {
                // Grant camera / mic for in-page getUserMedia (user already granted app perms)
                request?.grant(request.resources)
            }
        }

        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    if (webView.canGoBack()) webView.goBack() else finish()
                }
            },
        )

        val port = try {
            Native.startHost(8765)
        } catch (_: Throwable) {
            0
        }

        val url = intent?.getStringExtra("TEVARN_MOBILE_URL")
            ?: if (port > 0) "http://127.0.0.1:$port/"
            else "file:///android_asset/index.html"

        webView.loadUrl(url)
    }

    override fun onDestroy() {
        try {
            Native.stopHost()
        } catch (_: Throwable) {
        }
        webView.destroy()
        super.onDestroy()
    }
}

object Native {
    init {
        try {
            System.loadLibrary("tevarn_mobile_android")
        } catch (_: UnsatisfiedLinkError) {
            // optional until cargo-ndk packages the .so
        }
    }

    @JvmStatic external fun startHost(preferredPort: Int): Int
    @JvmStatic external fun stopHost()
}
