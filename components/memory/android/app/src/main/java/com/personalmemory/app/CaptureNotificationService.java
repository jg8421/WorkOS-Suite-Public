package com.personalmemory.app;

import android.app.Notification;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import org.json.JSONArray;
import org.json.JSONObject;

import java.time.Instant;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Set;

public class CaptureNotificationService extends NotificationListenerService {
    private final LinkedHashMap<String, Long> recentlyQueued = new LinkedHashMap<>();

    @Override public void onListenerConnected() {
        MemoryClient.preferences(this).edit().putBoolean("notification_capture_connected", true).apply();
        status("connected");
        CaptureStatus.update(this);
    }

    @Override public void onListenerDisconnected() {
        MemoryClient.preferences(this).edit().putBoolean("notification_capture_connected", false).apply();
        status("disconnected");
        CaptureStatus.update(this);
    }

    @Override public void onNotificationPosted(StatusBarNotification notification) {
        if (notification == null) return;
        SharedPreferences prefs = MemoryClient.preferences(this);
        if (!prefs.getBoolean("capture_enabled", false) || prefs.getBoolean("capture_paused", false)) return;
        String packageName = notification.getPackageName();
        if (CapturePrivacy.excludedPackage(packageName, appName(packageName), getPackageName())
                || CapturePrivacy.customExcluded(packageName, prefs.getString("custom_excluded_packages", ""))) {
            status("excluded-app");
            return;
        }
        String allowlist = prefs.getString("allowlist", "");
        Set<String> allowed = new HashSet<>();
        if (allowlist != null) Arrays.stream(allowlist.split(",")).map(String::trim)
            .filter(value -> !value.isEmpty()).forEach(allowed::add);
        if (!allowed.contains("*") && !allowed.contains(packageName)) return;

        try {
            Notification source = notification.getNotification();
            if (source == null || source.extras == null || (source.flags & Notification.FLAG_GROUP_SUMMARY) != 0) return;
            CharSequence title = source.extras.getCharSequence(Notification.EXTRA_TITLE, "");
            CharSequence text = source.extras.getCharSequence(Notification.EXTRA_TEXT, "");
            CharSequence bigText = source.extras.getCharSequence(Notification.EXTRA_BIG_TEXT, "");
            String rawContent = string(bigText != null && bigText.length() > 0 ? bigText : text);
            String rawHeading = string(title);
            if (rawHeading.isEmpty() && rawContent.isEmpty()) return;
            // Evaluate both together: a title saying "Verification code" protects an unlabelled body code.
            if (CapturePrivacy.authenticationText(rawHeading + " " + rawContent)) {
                status("filtered-authentication");
                return;
            }
            String content = CapturePrivacy.redact(rawContent, 4000);
            String heading = CapturePrivacy.redact(rawHeading, 240);
            String signature = CapturePrivacy.digest(packageName + "|" + heading + "|" + content);
            long now = android.os.SystemClock.elapsedRealtime();
            Long previous = recentlyQueued.get(signature);
            if (previous != null && now - previous < 60000) return;
            recentlyQueued.remove(signature);
            recentlyQueued.put(signature, now);
            while (recentlyQueued.size() > 256) recentlyQueued.remove(recentlyQueued.keySet().iterator().next());

            JSONObject event = new JSONObject();
            event.put("type", "notification");
            event.put("title", heading);
            event.put("text", content);
            event.put("source", "android:notification");
            event.put("timestamp", Instant.ofEpochMilli(notification.getPostTime()).toString());
            event.put("importance", 0.45);
            event.put("tags", new JSONArray().put("android").put("notification").put(packageName));
            // Notification keys/tags may contain addresses or user-defined content; never persist them raw.
            event.put("sourceId", "notification-" + notification.getPostTime() + "-" + signature);
            event.put("metadata", new JSONObject().put("package", packageName)
                .put("notificationId", notification.getId()).put("privacyVersion", 1)
                .put("category", source.category == null ? "" : CapturePrivacy.redact(source.category, 80)));
            MemoryClient.enqueue(this, event, null);
            prefs.edit().putLong("notification_capture_last_capture_at", System.currentTimeMillis()).apply();
            status("queued");
        } catch (Exception error) { status("capture-error"); }
    }

    private void status(String value) {
        SharedPreferences prefs = MemoryClient.preferences(this);
        long now = System.currentTimeMillis();
        if (value.equals(prefs.getString("notification_capture_status", ""))
                && now - prefs.getLong("notification_capture_status_at", 0) < 5000) return;
        prefs.edit().putString("notification_capture_status", value).putLong("notification_capture_status_at", now).apply();
    }

    private String appName(String packageName) {
        try {
            PackageManager manager = getPackageManager();
            ApplicationInfo info = manager.getApplicationInfo(packageName, 0);
            return string(manager.getApplicationLabel(info));
        } catch (Exception ignored) { return packageName; }
    }

    private static String string(CharSequence value) { return value == null ? "" : value.toString().trim(); }
}
