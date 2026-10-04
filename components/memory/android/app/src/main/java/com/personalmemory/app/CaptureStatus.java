package com.personalmemory.app;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;

final class CaptureStatus {
    static void update(Context context) {
        Context app = context.getApplicationContext();
        NotificationManager manager = app.getSystemService(NotificationManager.class);
        if (manager == null) return;
        SharedPreferences prefs = MemoryClient.preferences(app);
        boolean enabled = prefs.getBoolean("capture_enabled", false) || prefs.getBoolean("enhanced_capture_enabled", false);
        if (!enabled) { manager.cancel(18765); return; }
        if (Build.VERSION.SDK_INT >= 33
            && app.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;
        NotificationChannel channel = new NotificationChannel("capture_status", "记忆采集状态", NotificationManager.IMPORTANCE_LOW);
        channel.setShowBadge(false);
        manager.createNotificationChannel(channel);
        boolean paused = prefs.getBoolean("capture_paused", false);
        PendingIntent open = PendingIntent.getActivity(app, 0, new Intent(app, MainActivity.class),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent pause = PendingIntent.getBroadcast(app, 0, new Intent(app, PauseCaptureReceiver.class),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        String modes = prefs.getBoolean("enhanced_capture_enabled", false) ? "页面上下文 / 通知（以系统授权为准）" : "通知（以系统授权为准）";
        Notification.Builder builder = new Notification.Builder(app, "capture_status")
            .setSmallIcon(android.R.drawable.ic_menu_info_details)
            .setContentTitle(paused ? "Personal Memory · 已暂停" : "Personal Memory · 采集已开启")
            .setContentText(paused ? "点击打开应用，可恢复采集" : modes + " · 可随时暂停")
            .setContentIntent(open).setOnlyAlertOnce(true).setOngoing(!paused)
            .setVisibility(Notification.VISIBILITY_PRIVATE);
        if (!paused) builder.addAction(new Notification.Action.Builder(null, "暂停采集", pause).build());
        manager.notify(18765, builder.build());
    }
}
