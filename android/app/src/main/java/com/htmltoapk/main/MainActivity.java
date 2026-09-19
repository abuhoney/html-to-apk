package com.htmltoapk.main;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.view.KeyEvent;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

/**
 * MainActivity — WebView shell for HTML to APK.
 *
 * Loads the bundled UI from assets, exposes a JavaScript bridge
 * that downloads the built APK via the system DownloadManager and
 * triggers the system installer on completion.
 *
 * Pure-Android (no AndroidX) — works on API 24+.
 */
public class MainActivity extends Activity {

    private WebView webView;
    private long pendingDownloadId = -1;
    private String pendingDownloadName = "";
    private ApkDownloadReceiver receiver;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        // Register the download-complete receiver
        receiver = new ApkDownloadReceiver(this);
        registerReceiver(receiver, new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE));

        webView = findViewById(R.id.webview);
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        CookieManager.getInstance().setAcceptCookie(true);

        // JavaScript bridge — called from the WebView UI
        webView.addJavascriptInterface(new HtmlToApkBridge(this), "AndroidBridge");

        webView.setWebViewClient(new HtmlToApkWebViewClient(BuildConfig.BACKEND_URL));
        webView.setWebChromeClient(new WebChromeClient());

        // Load the bundled UI from assets
        webView.loadUrl("file:///android_asset/webapp/index.html");
    }

    /**
     * Enqueue an APK download with the system DownloadManager.
     */
    public void startDownload(String url, String filename) {
        try {
            DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
            r.setMimeType("application/vnd.android.package-archive");
            r.setTitle(filename);
            r.setDescription("APK from HTML to APK");
            r.setNotificationVisibility(
                DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename);
            r.allowScanningByMediaScanner();

            DownloadManager dm = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
            pendingDownloadId = dm.enqueue(r);
            pendingDownloadName = filename;
        } catch (Exception e) {
            toast("Download error: " + e.getMessage());
        }
    }

    /**
     * Called by ApkDownloadReceiver when a download completes.
     */
    public void onDownloadComplete(long downloadId) {
        if (downloadId != pendingDownloadId) return;
        DownloadManager dm = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
        DownloadManager.Query q = new DownloadManager.Query();
        q.setFilterById(downloadId);
        Cursor c = dm.query(q);
        try {
            if (c.moveToFirst()) {
                int status = c.getInt(c.getColumnIndex(DownloadManager.COLUMN_STATUS));
                if (status == DownloadManager.STATUS_SUCCESSFUL) {
                    String localUri = c.getString(c.getColumnIndex(DownloadManager.COLUMN_LOCAL_URI));
                    if (localUri != null) {
                        triggerInstaller(Uri.parse(localUri));
                    }
                } else {
                    String reason = c.getString(c.getColumnIndex(DownloadManager.COLUMN_REASON));
                    toast("Download failed: " + reason);
                }
            }
        } finally {
            c.close();
            pendingDownloadId = -1;
        }
    }

    /**
     * Launch the system APK installer.
     */
    private void triggerInstaller(Uri apkUri) {
        try {
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(apkUri, "application/vnd.android.package-archive");
            intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_GRANT_READ_URI_PERMISSION);
            startActivity(intent);
            toast("Ready to install: " + pendingDownloadName);
        } catch (Exception e) {
            toast("Install error: " + e.getMessage());
        }
    }

    public void toast(String msg) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView != null && webView.canGoBack()) {
            webView.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) webView.onResume();
    }

    @Override
    protected void onPause() {
        if (webView != null) webView.onPause();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (receiver != null) {
            try { unregisterReceiver(receiver); } catch (Exception ignored) {}
            receiver = null;
        }
        if (webView != null) {
            ((android.view.ViewGroup) webView.getParent()).removeView(webView);
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
