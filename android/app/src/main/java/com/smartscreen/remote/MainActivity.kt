package com.smartscreen.remote

import android.annotation.SuppressLint
import android.app.Activity
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.graphics.Bitmap
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.URL
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Smart Screen remote.
 *
 * The control panel itself is a web page served by the Pi (/remote), so it updates together with
 * the screen whenever the Pi updates from GitHub. This app adds what a browser can't do well:
 * finding the screen on the Wi-Fi automatically, remembering it, picking photos from the gallery,
 * and saving photo booth pictures to the phone.
 */
class MainActivity : Activity() {

    private lateinit var web: WebView
    private lateinit var setup: View
    private lateinit var found: LinearLayout
    private lateinit var searchStatus: TextView
    private lateinit var searching: ProgressBar
    private lateinit var errorText: TextView
    private lateinit var address: EditText

    private val prefs by lazy { getSharedPreferences("screen", MODE_PRIVATE) }
    private val discovery by lazy { ScreenDiscovery(this, ::onScreenFound) }
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var showingWeb = false
    private lateinit var loading: View
    private var loadToken = 0
    private var pageLoaded = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        web = findViewById(R.id.web)
        setup = findViewById(R.id.setup)
        found = findViewById(R.id.found)
        searchStatus = findViewById(R.id.search_status)
        searching = findViewById(R.id.searching)
        errorText = findViewById(R.id.error)
        address = findViewById(R.id.address)
        loading = findViewById(R.id.loading)

        // Android 15 draws apps edge-to-edge: keep content clear of the status and navigation bars.
        val root = findViewById<View>(R.id.root)
        root.setOnApplyWindowInsetsListener { v, insets ->
            if (Build.VERSION.SDK_INT >= 30) {
                val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars() or android.view.WindowInsets.Type.ime())
                v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            } else {
                @Suppress("DEPRECATION")
                v.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop,
                    insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
            }
            insets
        }

        findViewById<Button>(R.id.connect).setOnClickListener { connectManually() }
        address.setOnEditorActionListener { _, actionId, event ->
            if (actionId == EditorInfo.IME_ACTION_GO || event?.keyCode == KeyEvent.KEYCODE_ENTER) {
                connectManually(); true
            } else false
        }
        configureWebView()

        val saved = prefs.getString(KEY_URL, null)
        if (saved != null && savedInstanceState == null) openScreen(saved) else showSetup(null)
    }

    // ---------- choosing a screen ----------

    private fun showSetup(error: String?) {
        showingWeb = false
        loadToken++
        loading.visibility = View.GONE
        web.visibility = View.GONE
        setup.visibility = View.VISIBLE
        errorText.text = error.orEmpty()
        errorText.visibility = if (error == null) View.GONE else View.VISIBLE
        prefs.getString(KEY_URL, null)?.let { address.setText(Uri.parse(it).host) }
        found.removeAllViews()
        searching.visibility = View.VISIBLE
        searchStatus.setText(R.string.setup_searching)
        discovery.start()
        // Say so if nothing shows up after a while.
        searchStatus.postDelayed({
            if (!showingWeb && found.childCount == 0) {
                searching.visibility = View.GONE
                searchStatus.setText(R.string.setup_none_yet)
            }
        }, 8000)
    }

    private fun onScreenFound(name: String, url: String) = runOnUiThread {
        // Our remembered screen changed address (new IP from the router): follow it.
        if (name == prefs.getString(KEY_NAME, null) && !showingWeb) {
            remember(name, url)
            openScreen(url)
            return@runOnUiThread
        }
        if (showingWeb) return@runOnUiThread
        for (i in 0 until found.childCount) if (found.getChildAt(i).tag == url) return@runOnUiThread
        searching.visibility = View.GONE
        searchStatus.text = "Tap your screen:"
        val button = Button(this).apply {
            tag = url
            text = "📺  $name\n${Uri.parse(url).host}"
            isAllCaps = false
            textAlignment = View.TEXT_ALIGNMENT_VIEW_START
            setOnClickListener { remember(name, url); openScreen(url) }
        }
        found.addView(button, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))
    }

    private fun connectManually() {
        val url = normalizeAddress(address.text.toString()) ?: run {
            errorText.text = "Type the screen's address, like 192.168.1.20"
            errorText.visibility = View.VISIBLE
            return
        }
        remember(null, url)
        openScreen(url)
    }

    private fun remember(name: String?, url: String) {
        prefs.edit().putString(KEY_URL, url).putString(KEY_NAME, name).apply()
    }

    private fun openScreen(baseUrl: String) {
        discovery.stop()
        if (!onHomeNetwork()) {
            showSetup(getString(R.string.error_not_wifi))
            return
        }
        showingWeb = true
        pageLoaded = false
        setup.visibility = View.GONE
        web.visibility = View.VISIBLE
        loading.visibility = View.VISIBLE
        web.loadUrl("$baseUrl/remote")
        // Don't sit on a blank page if the screen never answers.
        val token = ++loadToken
        loading.postDelayed({
            if (token == loadToken && !pageLoaded) {
                web.stopLoading()
                showSetup(getString(R.string.error_timeout, Uri.parse(baseUrl).host ?: "The screen"))
            }
        }, 12_000)
    }

    /** True on Wi-Fi or Ethernet: the screen is only reachable on the home network. */
    private fun onHomeNetwork(): Boolean {
        val cm = getSystemService(android.net.ConnectivityManager::class.java) ?: return true
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) ||
            caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_ETHERNET) ||
            caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_VPN) // e.g. Tailscale back home
    }

    // ---------- the control panel ----------

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        CookieManager.getInstance().setAcceptCookie(true)
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            userAgentString = "$userAgentString SmartScreenApp/1.0"
        }
        web.addJavascriptInterface(Bridge(), "SmartScreenApp")
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val base = prefs.getString(KEY_URL, null)?.let(Uri::parse)
                val target = request.url
                if (base != null && target.host == base.host && target.port == base.port) return false
                // Links to anywhere else (help pages, etc.) open in the normal browser.
                try { startActivity(Intent(Intent.ACTION_VIEW, target)) } catch (_: ActivityNotFoundException) {}
                return true
            }

            override fun onPageFinished(view: WebView, url: String) {
                if (url.startsWith("http")) {
                    pageLoaded = true
                    loading.visibility = View.GONE
                }
            }

            override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
                errorText.visibility = View.GONE
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (!request.isForMainFrame) return
                val host = prefs.getString(KEY_URL, null)?.let { Uri.parse(it).host } ?: "the screen"
                showSetup(getString(R.string.error_unreachable, host))
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                val intent = Intent(Intent.ACTION_GET_CONTENT).apply {
                    // Photos and videos for the slideshow.
                    type = "*/*"
                    putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("image/*", "video/*"))
                    addCategory(Intent.CATEGORY_OPENABLE)
                    putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.mode == FileChooserParams.MODE_OPEN_MULTIPLE)
                }
                return try {
                    @Suppress("DEPRECATION")
                    startActivityForResult(Intent.createChooser(intent, "Choose photos or videos"), REQUEST_PHOTOS)
                    true
                } catch (_: ActivityNotFoundException) {
                    fileCallback = null
                    false
                }
            }
        }
        web.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            val name = URLUtil.guessFileName(url, contentDisposition, mimeType)
            val request = DownloadManager.Request(Uri.parse(url))
                .addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url) ?: "")
                .addRequestHeader("User-Agent", userAgent)
                .setMimeType(mimeType)
                .setTitle(name)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_PICTURES, "Smart Screen/$name")
            (getSystemService(DOWNLOAD_SERVICE) as DownloadManager).enqueue(request)
            Toast.makeText(this, R.string.saving_photo, Toast.LENGTH_SHORT).show()
        }
    }

    // The AndroidX activity-result API would add a dependency; the platform callback is all we need.
    @Suppress("OVERRIDE_DEPRECATION", "DEPRECATION")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQUEST_PHOTOS) return
        val uris = mutableListOf<Uri>()
        if (resultCode == RESULT_OK && data != null) {
            val clip = data.clipData
            if (clip != null) for (i in 0 until clip.itemCount) uris += clip.getItemAt(i).uri
            else data.data?.let { uris += it }
        }
        fileCallback?.onReceiveValue(if (uris.isEmpty()) null else uris.toTypedArray())
        fileCallback = null
    }

    /** Things the control panel page can ask the app for (window.SmartScreenApp in remote.js). */
    inner class Bridge {
        @JavascriptInterface
        fun deviceName(): String = "${Build.MANUFACTURER.replaceFirstChar { it.uppercase() }} ${Build.MODEL}".trim()

        @JavascriptInterface
        fun chooseScreen() = runOnUiThread {
            prefs.edit().remove(KEY_URL).remove(KEY_NAME).apply()
            web.loadUrl("about:blank")
            showSetup(null)
        }

        @JavascriptInterface
        fun paired() = CookieManager.getInstance().flush()
    }

    @Suppress("OVERRIDE_DEPRECATION", "DEPRECATION")
    override fun onBackPressed() {
        if (showingWeb && web.canGoBack()) {
            web.goBack()
        } else {
            super.onBackPressed()
        }
    }

    override fun onPause() {
        super.onPause()
        CookieManager.getInstance().flush()
        discovery.stop()
    }

    override fun onResume() {
        super.onResume()
        if (!showingWeb && setup.visibility == View.VISIBLE) discovery.start()
    }

    override fun onDestroy() {
        discovery.stop()
        web.destroy()
        super.onDestroy()
    }

    companion object {
        private const val KEY_URL = "url"
        private const val KEY_NAME = "name"
        private const val REQUEST_PHOTOS = 7

        /** "192.168.1.20", "smartscreen.local:8080", "http://x/" -> "http://host:port" (no trailing slash). */
        fun normalizeAddress(input: String): String? {
            var text = input.trim().removeSuffix("/")
            if (text.isEmpty()) return null
            if (!text.contains("://")) text = "http://$text"
            val uri = Uri.parse(text)
            val host = uri.host?.takeIf { it.isNotBlank() } ?: return null
            val port = if (uri.port > 0) uri.port else 8080
            return "${uri.scheme ?: "http"}://$host:$port"
        }
    }
}

/**
 * Finds Smart Screens on the Wi-Fi in two ways at once:
 *  1. mDNS: each Pi announces itself as "_smartscreen._tcp" (deploy/smart-screen.avahi).
 *  2. A quick scan of this Wi-Fi's addresses for the screen's web page on port 8080. This works even
 *     when the router blocks mDNS (many mesh systems do) or the announcement isn't installed.
 */
class ScreenDiscovery(activity: Activity, private val onFound: (name: String, url: String) -> Unit) {
    private val nsd = activity.getSystemService(Activity.NSD_SERVICE) as NsdManager
    private val connectivity = activity.getSystemService(ConnectivityManager::class.java)
    private var listener: NsdManager.DiscoveryListener? = null
    private val queue = ArrayDeque<NsdServiceInfo>()
    private var resolving = false
    @Volatile private var scanner: ExecutorService? = null

    fun start() {
        startScan()
        if (listener != null) return
        val l = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {}
            override fun onDiscoveryStopped(serviceType: String) {}
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) { listener = null }
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
            override fun onServiceLost(service: NsdServiceInfo) {}
            override fun onServiceFound(service: NsdServiceInfo) = enqueue(service)
        }
        listener = l
        try {
            nsd.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, l)
        } catch (_: Exception) {
            listener = null
        }
    }

    // ---------- address scan ----------

    private fun startScan() {
        if (scanner != null) return
        val hosts = localHosts()
        if (hosts.isEmpty()) return
        val pool = Executors.newFixedThreadPool(48)
        scanner = pool
        for (host in hosts) pool.execute { probe(host) }
        pool.shutdown()
        Thread {
            pool.awaitTermination(30, TimeUnit.SECONDS)
            if (scanner === pool) scanner = null // allow another scan next time the list is shown
        }.start()
    }

    /** Every other address on this phone's Wi-Fi (at most 254 of them: the phone's own /24). */
    private fun localHosts(): List<String> {
        val network = connectivity?.activeNetwork ?: return emptyList()
        val caps = connectivity.getNetworkCapabilities(network)
        val onLan = caps != null && (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))
        if (!onLan) return emptyList() // don't scan the mobile network
        val props = try { connectivity.getLinkProperties(network) } catch (_: Exception) { null }
        val own = props?.linkAddresses?.map { it.address }?.firstOrNull { it is Inet4Address && !it.isLoopbackAddress }
            ?: return emptyList()
        val b = own.address
        return (1..254).filter { it != (b[3].toInt() and 0xff) }
            .map { "${b[0].toInt() and 0xff}.${b[1].toInt() and 0xff}.${b[2].toInt() and 0xff}.$it" }
    }

    private fun probe(host: String) {
        if (scanner == null) return
        val base = "http://$host:$PORT"
        try {
            val conn = URL("$base/api/pair/status").openConnection() as HttpURLConnection
            conn.connectTimeout = 800
            conn.readTimeout = 1500
            try {
                if (conn.responseCode != 200) return
                val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                if (!json.has("paired")) return
                onFound(json.optString("name").ifBlank { "Smart Screen" }, base)
            } finally {
                conn.disconnect()
            }
        } catch (_: Exception) {
            // nothing there, or not a Smart Screen
        }
    }

    fun stop() {
        scanner?.shutdownNow()
        scanner = null
        listener?.let { try { nsd.stopServiceDiscovery(it) } catch (_: Exception) {} }
        listener = null
        synchronized(queue) { queue.clear() }
    }

    // Older Android versions can only resolve one service at a time.
    private fun enqueue(service: NsdServiceInfo) {
        synchronized(queue) {
            queue.addLast(service)
            if (resolving) return
            resolving = true
        }
        resolveNext()
    }

    private fun resolveNext() {
        val next = synchronized(queue) {
            queue.removeFirstOrNull() ?: run { resolving = false; null }
        } ?: return
        @Suppress("DEPRECATION")
        nsd.resolveService(next, object : NsdManager.ResolveListener {
            override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) = resolveNext()
            override fun onServiceResolved(info: NsdServiceInfo) {
                @Suppress("DEPRECATION")
                val host = info.host?.hostAddress
                if (host != null) {
                    val h = if (host.contains(':')) "[${host.substringBefore('%')}]" else host
                    onFound(info.serviceName, "http://$h:${info.port}")
                }
                resolveNext()
            }
        })
    }

    companion object {
        const val SERVICE_TYPE = "_smartscreen._tcp"
        const val PORT = 8080
    }
}
