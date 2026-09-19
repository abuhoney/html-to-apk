package com.htmltoapk.main;

import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * BroadcastReceiver that listens for DownloadManager completions
 * and notifies the MainActivity to launch the installer.
 */
public class ApkDownloadReceiver extends BroadcastReceiver {

    private final MainActivity owner;

    public ApkDownloadReceiver(MainActivity owner) {
        this.owner = owner;
    }

    @Override
    public void onReceive(Context ctx, Intent intent) {
        if (!DownloadManager.ACTION_DOWNLOAD_COMPLETE.equals(intent.getAction())) return;
        long id = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1);
        if (id == -1) return;
        owner.onDownloadComplete(id);
    }
}
