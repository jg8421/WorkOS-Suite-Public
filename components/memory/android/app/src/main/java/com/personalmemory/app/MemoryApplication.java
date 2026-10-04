package com.personalmemory.app;

import android.app.Application;
import android.net.ConnectivityManager;
import android.net.Network;

public class MemoryApplication extends Application {
    @Override public void onCreate() {
        super.onCreate();
        SyncJobService.schedule(this);
        try {
            ConnectivityManager manager = getSystemService(ConnectivityManager.class);
            manager.registerDefaultNetworkCallback(new ConnectivityManager.NetworkCallback() {
                @Override public void onAvailable(Network network) { MemoryClient.flush(MemoryApplication.this, null); }
            });
        } catch (RuntimeException ignored) {}
    }
}
