package com.htmltoapk.main;

import android.app.Activity;
import android.content.ClipData;
import android.content.Intent;
import android.net.Uri;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebView;
import android.widget.Toast;

/**
 * Custom WebChromeClient that handles <input type="file"> clicks.
 *
 * Without this override, tapping "Pick HTML File" in the WebView does nothing
 * because WebView doesn't open the system file picker by default.
 *
 * IMPORTANT: This class must be a NAMED class (not anonymous) because d8 8.2.2
 * crashes on anonymous inner classes with a NullPointerException when
 * generating dex bytecode.
 */
public class FilePickerWebChromeClient extends WebChromeClient {

    private final MainActivity owner;

    public FilePickerWebChromeClient(MainActivity owner) {
        this.owner = owner;
    }

    @Override
    public boolean onShowFileChooser(
            WebView view,
            ValueCallback<Uri[]> callback,
            FileChooserParams params) {
        // Tell the activity about the new callback — it'll be invoked from onActivityResult()
        owner.setFilePathCallback(callback);
        try {
            Intent intent = params.createIntent();
            intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, false);
            owner.startActivityForResult(intent, MainActivity.FILE_CHOOSER_REQUEST);
            return true;
        } catch (Exception e) {
            owner.toast("No file picker available: " + e.getMessage());
            return false;
        }
    }
}
