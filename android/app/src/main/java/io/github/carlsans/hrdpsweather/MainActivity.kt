package io.github.carlsans.hrdpsweather

import android.Manifest
import android.annotation.SuppressLint
import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.webkit.GeolocationPermissions
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import java.io.ByteArrayInputStream
import java.io.File
import java.io.FileInputStream

/**
 * The app itself is the web app (web/ in the repository, served by GitHub Pages) shown full screen in a WebView:
 * it updates with every deploy and works offline through its service worker. The native part only adds the
 * home-screen widget, which needs the place the user chose in the web app: the page hands it over through
 * the small `HrdpsAndroid` bridge below.
 */
class MainActivity : ComponentActivity() {
    private lateinit var web: WebView
    private var geoCallback: GeolocationPermissions.Callback? = null
    private var geoOrigin: String? = null

    private val askLocation = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { res ->
        geoCallback?.invoke(geoOrigin, res.values.any { it }, false)
        geoCallback = null
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // dark page: light status/navigation bar icons whatever the system theme
        enableEdgeToEdge(SystemBarStyle.dark(Color.TRANSPARENT), SystemBarStyle.dark(Color.TRANSPARENT))
        if (BuildConfig.DEBUG) WebView.setWebContentsDebuggingEnabled(true)     // debug builds only (tests)
        web = WebView(this).apply {
            setBackgroundColor(Color.parseColor("#1e1e2e"))
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.setGeolocationEnabled(true)
            settings.userAgentString = settings.userAgentString + " HrdpsWeatherAndroid/" + BuildConfig.VERSION_NAME
            addJavascriptInterface(Bridge(), "HrdpsAndroid")
            webViewClient = Client()
            webChromeClient = Chrome()
        }
        // A WebView ignores its own padding: the insets go on a container, so the page stays clear of the system bars.
        val root = FrameLayout(this).apply { setBackgroundColor(Color.parseColor("#1e1e2e")); addView(web) }
        setContentView(root)
        ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            v.updatePadding(left = bars.left, top = bars.top, right = bars.right, bottom = bars.bottom)
            WindowInsetsCompat.CONSUMED
        }
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else finish()
            }
        })
        if (savedInstanceState != null) web.restoreState(savedInstanceState) else web.loadUrl(APP_URL)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
        // The page stays in memory while the app is in the background, so the browser never navigates to it again and
        // never looks for a new version by itself: ask the service worker every time the app comes back.
        web.evaluateJavascript(
            "navigator.serviceWorker && navigator.serviceWorker.getRegistration().then(function (r) { if (r) r.update(); })", null)
    }

    override fun onPause() {
        web.onPause()
        super.onPause()
    }

    private inner class Bridge {
        /** Called by web/js/app.js with the saved configuration ({latitude, longitude, location, timezone}). */
        @JavascriptInterface
        fun setPlace(json: String) {
            val p = Place.fromJson(json) ?: return
            val changed = Prefs.setPlace(applicationContext, p)
            if (changed) {
                Prefs.setSnapshot(applicationContext, null)
                Widgets.render(applicationContext)
            }
            Widgets.refreshNow(applicationContext)
            Prefetch.now(applicationContext)                    // the whole run, downloaded in the background
        }

        /** True when the launcher can add the widget from the app (Android 8+, most launchers). */
        @JavascriptInterface
        fun canPinWidget(): Boolean = AppWidgetManager.getInstance(this@MainActivity).isRequestPinAppWidgetSupported

        /** Ask the launcher to add a widget (it shows its own confirmation): the 3×2 one, or the 4×3 one. */
        @JavascriptInterface
        fun pinWidget() = pin(WeatherWidget::class.java)

        @JavascriptInterface
        fun pinLargeWidget() = pin(WeatherWidgetLarge::class.java)

        private fun pin(provider: Class<*>) = runOnUiThread {
            AppWidgetManager.getInstance(this@MainActivity).requestPinAppWidget(ComponentName(this@MainActivity, provider), null, null)
        }
    }

    private inner class Client : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url
            if (url.host == APP_HOST && url.path.orEmpty().startsWith(APP_PATH)) return false
            runCatching { startActivity(Intent(Intent.ACTION_VIEW, url)) }      // links (source code…) open outside
            return true
        }

        /** The files Prefetch.kt downloaded, as seen by the page at https://native.hrdps.invalid/ (state.json, f/<file>). */
        override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
            if (request.url.host != Prefetch.NATIVE_HOST) return null
            val rel = request.url.path.orEmpty().trimStart('/')
            val file = if (rel == "state.json") Prefetch.stateFile(applicationContext)
                else File(Prefetch.readyDir(applicationContext), rel.removePrefix("f/")).takeIf { rel.startsWith("f/") }
            val ok = file != null && file.isFile && file.canonicalPath.startsWith(Prefetch.readyDir(applicationContext).canonicalPath).let {
                it || file == Prefetch.stateFile(applicationContext) }
            val type = if (rel.endsWith(".bin")) "application/octet-stream" else "application/json"
            val headers = mapOf("Access-Control-Allow-Origin" to "*", "Cache-Control" to "no-store")
            return if (ok) WebResourceResponse(type, null, 200, "OK", headers, FileInputStream(file))
            else WebResourceResponse("text/plain", "utf-8", 404, "Not Found", headers, ByteArrayInputStream(ByteArray(0)))
        }

        override fun onPageFinished(view: WebView, url: String) {
            // Once after each installation of a new APK: forget the offline copy of the app files, so that a copy kept
            // by an older version can never hide the current one. The place, the data and the map tiles are untouched.
            val prefs = getSharedPreferences("webapp", android.content.Context.MODE_PRIVATE)
            if (url.startsWith(APP_URL) && prefs.getString("shell_purged_for", "") != BuildConfig.VERSION_NAME) {
                prefs.edit().putString("shell_purged_for", BuildConfig.VERSION_NAME).apply()
                view.evaluateJavascript(PURGE_SHELL_JS, null)
            }
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            view.loadDataWithBaseURL(null, OFFLINE_PAGE, "text/html", "utf-8", null)
        }
    }

    private inner class Chrome : WebChromeClient() {
        override fun onGeolocationPermissionsShowPrompt(origin: String, callback: GeolocationPermissions.Callback) {
            val granted = listOf(Manifest.permission.ACCESS_COARSE_LOCATION, Manifest.permission.ACCESS_FINE_LOCATION)
                .any { ContextCompat.checkSelfPermission(this@MainActivity, it) == PackageManager.PERMISSION_GRANTED }
            if (granted) {
                callback.invoke(origin, true, false)
                return
            }
            geoCallback = callback; geoOrigin = origin
            askLocation.launch(arrayOf(Manifest.permission.ACCESS_COARSE_LOCATION, Manifest.permission.ACCESS_FINE_LOCATION))
        }
    }

    companion object {
        val APP_URL: String = BuildConfig.APP_URL                     // GitHub Pages (debug builds: this computer)
        private const val PURGE_SHELL_JS =
            "(async function () { try { var rs = await navigator.serviceWorker.getRegistrations(); " +
            "for (var r of rs) { await r.unregister(); } var ks = await caches.keys(); " +
            "for (var k of ks) { if (k.indexOf('shell-') === 0) { await caches.delete(k); } } } catch (e) {} " +
            "location.reload(); })()"
        private val APP_HOST: String = Uri.parse(APP_URL).host.orEmpty()
        private val APP_PATH: String = Uri.parse(APP_URL).path.orEmpty()

        // Only shown when the very first launch has no network (afterwards the service worker serves the app).
        private val OFFLINE_PAGE = """
            <html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
            <body style="background:#1e1e2e;color:#cdd6f4;font-family:sans-serif;text-align:center;padding:30vh 24px 0">
            <p style="font-size:56px;margin:0">⛅</p><p>Pas de connexion.<br>La météo HRDPS a besoin d'Internet au premier lancement.</p>
            <p><a style="color:#89b4fa" href="$APP_URL">Réessayer</a></p></body></html>
        """.trimIndent()
    }
}
