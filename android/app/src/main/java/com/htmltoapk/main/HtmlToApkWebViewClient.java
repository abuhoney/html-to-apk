package com.htmltoapk.main;

import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * WebViewClient that keeps file:/// navigation in-app and opens
 * external URLs in the system browser. Also injects the backend URL
 * as a JavaScript global after page load.
 */
public class HtmlToApkWebViewClient extends WebViewClient {

    private final String backendUrl;

    public HtmlToApkWebViewClient(String backendUrl) {
        this.backendUrl = backendUrl;
    }

    @Override
    public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
        String u = req.getUrl().toString();
        if (u.startsWith("file:///") || u.startsWith("about:")) return false;
        // External links handled by the system — MainActivity will catch them
        return false;
    }

    @Override
    public void onPageFinished(WebView v, String url) {
        super.onPageFinished(v, url);
        // Inject the backend URL into the page
        v.evaluateJavascript(
            "window.BACKEND_URL=" + jsonString(backendUrl) + ";",
            null
        );
    }

    private static String jsonString(String s) {
        if (s == null) s = "";
        StringBuilder sb = new StringBuilder(s.length() + 8);
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"') sb.append("\\\"");
            else if (c == '\\') sb.append("\\\\");
            else if (c == '\n') sb.append("\\n");
            else if (c == '\r') sb.append("\\r");
            else if (c == '\t') sb.append("\\t");
            else sb.append(c);
        }
        sb.append('"');
        return sb.toString();
    }
}
