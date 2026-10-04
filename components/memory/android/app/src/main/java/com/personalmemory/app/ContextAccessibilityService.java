package com.personalmemory.app;

import android.accessibilityservice.AccessibilityService;
import android.app.KeyguardManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.time.Instant;
import java.util.ArrayDeque;
import java.util.HashSet;
import java.util.Set;

/** Observes user actions only. Never performs actions, gestures, screenshots, or text entry. */
public class ContextAccessibilityService extends AccessibilityService {
    private static final int MAX_NODES = 256;
    private static final int MAX_SCAN_CHARS = 32768;
    private static final int MAX_TEXT_CHARS = 1400;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final CapturePrivacy.RateLimiter limiter = new CapturePrivacy.RateLimiter();
    private Runnable pendingCapture;
    private String foregroundPackage = "";
    private String foregroundLabel = "";
    private long foregroundSince;
    private long lastForegroundObservation;
    private long lastStatusWrite;
    private long lastScanAt = -1;
    private String lastStatus = "";
    private boolean receiverRegistered;

    private final BroadcastReceiver screenReceiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) { resetCapture("screen-off"); }
    };
    private final SharedPreferences.OnSharedPreferenceChangeListener preferenceListener = (prefs, key) -> {
        if ("enhanced_capture_enabled".equals(key) || "enhanced_capture_consent_version".equals(key)
                || "capture_paused".equals(key) || "custom_excluded_packages".equals(key)) resetCapture("settings-changed");
    };

    @Override public void onCreate() {
        super.onCreate();
        MemoryClient.preferences(this).registerOnSharedPreferenceChangeListener(preferenceListener);
        IntentFilter filter = new IntentFilter(Intent.ACTION_SCREEN_OFF);
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(screenReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        else registerReceiver(screenReceiver, filter);
        receiverRegistered = true;
    }

    @Override protected void onServiceConnected() {
        super.onServiceConnected();
        MemoryClient.preferences(this).edit().putBoolean("enhanced_capture_connected", true).apply();
        status(enabled() ? "connected" : "disabled", true);
        CaptureStatus.update(this);
    }

    @Override public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) return;
        if (!enabled()) { resetCapture("disabled"); return; }
        if (!unlocked()) { resetCapture("screen-locked"); return; }
        String packageName = string(event.getPackageName());
        String label = appName(packageName);
        if (excluded(packageName, label)) { resetCapture("excluded-app"); return; }
        int eventType = event.getEventType();
        if (eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                && eventType != AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED
                && eventType != AccessibilityEvent.TYPE_VIEW_CLICKED) return;
        // Event text can contain typed values or another window's data; it is never consumed.
        int windowId = event.getWindowId();
        if (eventType == AccessibilityEvent.TYPE_VIEW_CLICKED) {
            AccessibilityNodeInfo source = event.getSource();
            String clicked = "";
            try {
                if (source != null && source.isVisibleToUser() && packageName.equals(string(source.getPackageName()))
                        && source.getWindowId() == windowId && !editable(source) && !nodeSensitive(source)) {
                    String raw = string(source.getText());
                    if (raw.isEmpty()) raw = string(source.getContentDescription());
                    if (!CapturePrivacy.sensitivePage(raw)) clicked = CapturePrivacy.redact(raw, 180);
                }
            } finally { recycle(source); }
            if (!clicked.isEmpty()) capture(packageName, label, windowId, eventType, clicked);
            return;
        }
        if (pendingCapture != null) handler.removeCallbacks(pendingCapture);
        // Read after a quiet interval; never retain a tree from an earlier event.
        pendingCapture = () -> {
            pendingCapture = null;
            capture(packageName, label, windowId, eventType, "");
        };
        handler.postDelayed(pendingCapture, 800);
    }

    private void capture(String packageName, String label, int windowId, int eventType, String clicked) {
        if (!enabled() || !unlocked()) { resetCapture("inactive"); return; }
        long scanTime = SystemClock.elapsedRealtime();
        if (lastScanAt >= 0 && scanTime - lastScanAt < 800) return;
        lastScanAt = scanTime;
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) { resetCapture("no-window"); return; }
        try {
            String rootPackage = string(root.getPackageName());
            if (!packageName.equals(rootPackage) || root.getWindowId() != windowId
                    || excluded(rootPackage, appName(rootPackage))) {
                resetCapture("window-mismatch");
                return;
            }
            Scan scan = scan(root, packageName);
            if (scan.filtered) { resetCapture("filtered-page"); return; }
            if (!unlocked() || !enabled()) { resetCapture("inactive"); return; }
            observeUsage(packageName, label);
            String kind = !clicked.isEmpty() ? "ui-action" : eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                ? "app-window" : "visible-context";
            String text = clicked.isEmpty() ? scan.text : "Clicked: " + clicked;
            if (text.isEmpty()) return;
            long interval = "ui-action".equals(kind) ? 3000 : "app-window".equals(kind) ? 5000 : 15000;
            emit(kind, packageName, label, text, interval, 0.30, 0);
        } catch (Exception error) { resetCapture("capture-error"); }
        finally { recycle(root); }
    }

    private Scan scan(AccessibilityNodeInfo root, String expectedPackage) {
        ArrayDeque<AccessibilityNodeInfo> queue = new ArrayDeque<>();
        Set<String> unique = new HashSet<>();
        StringBuilder result = new StringBuilder();
        int visited = 0;
        int scannedChars = 0;
        queue.add(AccessibilityNodeInfo.obtain(root));
        try {
            while (!queue.isEmpty()) {
                AccessibilityNodeInfo node = queue.removeFirst();
                try {
                    if (++visited > MAX_NODES) return Scan.filtered();
                    if (!node.isVisibleToUser()) continue;
                    String nodePackage = string(node.getPackageName());
                    if (!nodePackage.isEmpty() && !expectedPackage.equals(nodePackage)) return Scan.filtered();
                    if (nodeSensitive(node)) return Scan.filtered();
                    // Hints/IDs are inspected only for exclusion; editable values are never read.
                    if (CapturePrivacy.sensitivePage(node.getHintText())
                            || CapturePrivacy.sensitivePage(node.getViewIdResourceName())) return Scan.filtered();
                    if (editable(node)) continue;
                    String text = string(node.getText());
                    String description = string(node.getContentDescription());
                    scannedChars += text.length() + description.length();
                    if (scannedChars > MAX_SCAN_CHARS || CapturePrivacy.sensitivePage(text)
                            || CapturePrivacy.sensitivePage(description)) return Scan.filtered();
                    append(unique, result, text);
                    append(unique, result, description);
                    int children = node.getChildCount();
                    // Discard the page if the whole visible tree cannot be inspected within the budget.
                    if (children > MAX_NODES - visited - queue.size()) return Scan.filtered();
                    for (int index = 0; index < children; index++) {
                        AccessibilityNodeInfo child = node.getChild(index);
                        if (child != null) queue.addLast(child);
                    }
                } finally { recycle(node); }
            }
            return new Scan(false, result.toString());
        } finally { while (!queue.isEmpty()) recycle(queue.removeFirst()); }
    }

    private void append(Set<String> unique, StringBuilder result, String value) {
        if (result.length() >= MAX_TEXT_CHARS || unique.size() >= 40) return;
        String clean = CapturePrivacy.redact(value, 300);
        if (clean.isEmpty() || !unique.add(clean)) return;
        if (result.length() > 0) result.append(" | ");
        int available = MAX_TEXT_CHARS - result.length();
        if (available > 0) result.append(clean, 0, Math.min(clean.length(), available));
    }

    private void observeUsage(String packageName, String label) {
        long now = SystemClock.elapsedRealtime();
        // Unknown foreground continuity must not be counted as application usage.
        if (lastForegroundObservation != 0 && now - lastForegroundObservation > 90000) clearUsage();
        if (!foregroundPackage.equals(packageName)) {
            long seconds = Math.max(0, (now - foregroundSince) / 1000);
            if (!foregroundPackage.isEmpty() && seconds >= 5) emit("app-usage", foregroundPackage, foregroundLabel,
                "Observed in foreground for about " + seconds + " seconds", 5000, 0.22, seconds);
            foregroundPackage = packageName;
            foregroundLabel = label;
            foregroundSince = now;
        }
        lastForegroundObservation = now;
    }

    private void emit(String kind, String packageName, String label, String text, long interval, double importance, long seconds) {
        String clean = CapturePrivacy.redact(text, MAX_TEXT_CHARS);
        String signature = CapturePrivacy.digest(packageName + "|" + ("ui-action".equals(kind) ? kind : "context") + "|" + clean);
        if (!limiter.allow(kind + "|" + packageName, signature, SystemClock.elapsedRealtime(), interval)) return;
        try {
            long now = System.currentTimeMillis();
            JSONObject metadata = new JSONObject().put("package", packageName).put("kind", kind).put("privacyVersion", 1);
            if (seconds > 0) metadata.put("durationSeconds", seconds).put("approximate", true);
            JSONObject event = new JSONObject()
                .put("type", "activity").put("title", CapturePrivacy.redact(label, 120) + " · " + kind)
                .put("text", clean).put("source", "android:accessibility")
                .put("timestamp", Instant.ofEpochMilli(now).toString()).put("importance", importance)
                .put("tags", new JSONArray().put("android").put("accessibility").put(kind).put(packageName))
                .put("sourceId", "accessibility-" + now + "-" + signature).put("metadata", metadata);
            MemoryClient.enqueue(this, event, null);
            MemoryClient.preferences(this).edit().putLong("enhanced_capture_last_capture_at", now).apply();
            status("queued", true);
        } catch (Exception error) { status("queue-error", true); }
    }

    private boolean enabled() {
        SharedPreferences prefs = MemoryClient.preferences(this);
        return prefs.getBoolean("enhanced_capture_enabled", false)
            && prefs.getInt("enhanced_capture_consent_version", 0) >= 1
            && !prefs.getBoolean("capture_paused", false);
    }

    private boolean excluded(String packageName, String label) {
        return CapturePrivacy.excludedPackage(packageName, label, getPackageName()) || CapturePrivacy.customExcluded(
            packageName, MemoryClient.preferences(this).getString("custom_excluded_packages", ""));
    }

    private boolean unlocked() {
        PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
        KeyguardManager keyguard = (KeyguardManager) getSystemService(KEYGUARD_SERVICE);
        return power != null && power.isInteractive() && keyguard != null && !keyguard.isKeyguardLocked();
    }

    private static boolean editable(AccessibilityNodeInfo node) {
        return node.isEditable() || string(node.getClassName()).contains("EditText");
    }

    private static boolean nodeSensitive(AccessibilityNodeInfo node) {
        return node.isPassword() || (Build.VERSION.SDK_INT >= 34 && node.isAccessibilityDataSensitive());
    }

    private void clearUsage() {
        foregroundPackage = "";
        foregroundLabel = "";
        foregroundSince = 0;
        lastForegroundObservation = 0;
    }

    private void resetCapture(String reason) {
        if (pendingCapture != null) handler.removeCallbacks(pendingCapture);
        pendingCapture = null;
        clearUsage();
        status(reason, false);
    }

    private void status(String reason, boolean force) {
        long now = SystemClock.elapsedRealtime();
        if (!force && reason.equals(lastStatus) && now - lastStatusWrite < 5000) return;
        lastStatus = reason;
        lastStatusWrite = now;
        MemoryClient.preferences(this).edit().putString("enhanced_capture_status", reason)
            .putLong("enhanced_capture_status_at", System.currentTimeMillis()).apply();
    }

    @Override public void onInterrupt() { resetCapture("interrupted"); }

    @Override public boolean onUnbind(Intent intent) {
        resetCapture("disconnected");
        MemoryClient.preferences(this).edit().putBoolean("enhanced_capture_connected", false).apply();
        CaptureStatus.update(this);
        return super.onUnbind(intent);
    }

    @Override public void onDestroy() {
        resetCapture("disconnected");
        MemoryClient.preferences(this).edit().putBoolean("enhanced_capture_connected", false).apply();
        CaptureStatus.update(this);
        MemoryClient.preferences(this).unregisterOnSharedPreferenceChangeListener(preferenceListener);
        if (receiverRegistered) unregisterReceiver(screenReceiver);
        super.onDestroy();
    }

    private String appName(String packageName) {
        try {
            PackageManager manager = getPackageManager();
            ApplicationInfo info = manager.getApplicationInfo(packageName, 0);
            return string(manager.getApplicationLabel(info));
        } catch (Exception ignored) { return packageName; }
    }

    private static String string(CharSequence value) { return value == null ? "" : value.toString(); }
    @SuppressWarnings("deprecation")
    private static void recycle(AccessibilityNodeInfo node) { if (node != null) node.recycle(); }
    private static final class Scan {
        final boolean filtered;
        final String text;
        Scan(boolean filtered, String text) { this.filtered = filtered; this.text = text; }
        static Scan filtered() { return new Scan(true, ""); }
    }
}
