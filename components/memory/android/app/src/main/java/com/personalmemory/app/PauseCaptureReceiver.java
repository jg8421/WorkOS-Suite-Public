package com.personalmemory.app;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class PauseCaptureReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        MemoryClient.preferences(context).edit().putBoolean("capture_paused", true).apply();
        CaptureStatus.update(context);
    }
}
