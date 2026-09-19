package com.htmltoapk.main;

import android.webkit.JavascriptInterface;

/**
 * JavaScript bridge exposed to the WebView UI as `window.AndroidBridge`.
 * Methods here are called from JavaScript to download APKs and read
 * build-time configuration.
 *
 * NOTE: This class must NOT contain anonymous inner classes — d8 8.2.2
 * crashes on them with a NullPointerException. Use named static classes
 * or top-level classes instead.
 */
public class HtmlToApkBridge {

    private final MainActivity owner;

    public HtmlToApkBridge(MainActivity owner) {
        this.owner = owner;
    }

    @JavascriptInterface
    public void downloadApk(final String url, final String filename) {
        owner.runOnUiThread(new DownloadStarter(owner, url, filename));
    }

    @JavascriptInterface
    public String getBackendUrl() {
        return BuildConfig.BACKEND_URL;
    }

    @JavascriptInterface
    public String getAppVersion() {
        return BuildConfig.VERSION_NAME;
    }

    /**
     * Named static Runnable (avoids anonymous inner classes which
     * crash d8 8.2.2).
     */
    private static class DownloadStarter implements Runnable {
        private final MainActivity owner;
        private final String url;
        private final String filename;

        DownloadStarter(MainActivity owner, String url, String filename) {
            this.owner = owner;
            this.url = url;
            this.filename = filename;
        }

        @Override
        public void run() {
            owner.toast("Downloading " + filename + "…");
            owner.startDownload(url, filename);
        }
    }
}
