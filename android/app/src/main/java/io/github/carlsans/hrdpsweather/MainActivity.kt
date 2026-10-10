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
            if (Prefs.setPlace(applicationContext, p)) {
                Prefs.setSnapshot(applicationContext, null)
                WeatherWidget.render(applicationContext)
            }
            WeatherWidget.refreshNow(applicationContext)
        }

        /** True when the launcher can add the widget from the app (Android 8+, most launchers). */
        @JavascriptInterface
        fun canPinWidget(): Boolean = AppWidgetManager.getInstance(this@MainActivity).isRequestPinAppWidgetSupported

        /** Ask the launcher to add the widget (it shows its own confirmation). */
        @JavascriptInterface
        fun pinWidget() {
            runOnUiThread {
                AppWidgetManager.getInstance(this@MainActivity)
                    .requestPinAppWidget(ComponentName(this@MainActivity, WeatherWidget::class.java), null, null)
            }
        }
    }

    private inner class Client : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url
            if (url.host == APP_HOST && url.path.orEmpty().startsWith(APP_PATH)) return false
            runCatching { startActivity(Intent(Intent.ACTION_VIEW, url)) }      // links (source code…) open outside
            return true
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
