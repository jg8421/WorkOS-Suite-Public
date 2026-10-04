package com.personalmemory.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.SystemClock;
import android.util.AtomicFile;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

final class MemoryClient {
    interface Callback { void complete(int sent, int pending, String error); }
    private static final ExecutorService IO = Executors.newSingleThreadExecutor();
    private static final ExecutorService NETWORK = Executors.newSingleThreadExecutor();
    private static final ScheduledExecutorService RETRY = Executors.newSingleThreadScheduledExecutor();
    private static boolean delayedFlush;
    private static final Object FILE_LOCK = new Object();
    private static final Object SYNC_LOCK = new Object();
    private static final List<Callback> CALLBACKS = new ArrayList<>();
    private static final String QUEUE_FILE = "pending-events.jsonl";
    private static final long MAX_QUEUE_BYTES = 20L * 1024 * 1024;
    private static boolean syncing;
    private static volatile long retryAfter;

    private MemoryClient() {}
    static SharedPreferences preferences(Context context) {
        return context.getSharedPreferences("personal-memory", Context.MODE_PRIVATE);
    }

    // File work and upload never block accessibility or notification callbacks.
    static void enqueue(Context context, JSONObject event, Callback callback) {
        Context app = context.getApplicationContext();
        final String input = event.toString();
        IO.execute(() -> {
            try {
                JSONObject safe = new JSONObject(input);
                String[] safeContent = CapturePrivacy.redactPair(safe.optString("title", ""), safe.optString("text", ""));
                safe.put("title", safeContent[0]);
                safe.put("text", safeContent[1]);
                String line = safe.toString();
                synchronized (FILE_LOCK) {
                    AtomicFile file = queue(app);
                    List<String> current = readQueue(app);
                    if (file.getBaseFile().length() + line.getBytes(StandardCharsets.UTF_8).length > MAX_QUEUE_BYTES) {
                        throw new IOException("Queue full");
                    }
                    current.add(line);
                    writeQueue(app, current);
                    preferences(app).edit().putInt("pending_count", current.size())
                        .putLong("last_queued_at", System.currentTimeMillis()).apply();
                }
                requestFlush(app, callback, callback != null);
            } catch (Exception error) {
                report(app, 0, "本机保存失败或离线队列已满，请检查存储并恢复电脑连接");
                if (callback != null) callback.complete(0, countPending(app), "本机保存失败；请保留原内容");
            }
        });
    }

    static void flush(Context context, Callback callback) {
        Context app = context.getApplicationContext();
        // Queue behind pending appends, then start an independent network worker.
        IO.execute(() -> requestFlush(app, callback, true));
    }

    private static void requestFlush(Context app, Callback callback, boolean force) {
        synchronized (SYNC_LOCK) {
            if (callback != null) CALLBACKS.add(callback);
            if (syncing) return;
            if (!force && SystemClock.elapsedRealtime() < retryAfter) {
                if (OneDriveSync.enabled(app) && !delayedFlush) {
                    delayedFlush = true;
                    RETRY.schedule(() -> {
                        synchronized(SYNC_LOCK) { delayedFlush=false; }
                        flush(app,null);
                    }, Math.max(1000,retryAfter-SystemClock.elapsedRealtime()),TimeUnit.MILLISECONDS);
                }
                return;
            }
            syncing = true;
        }
        NETWORK.execute(() -> sync(app));
    }

    private static void sync(Context app) {
        int sent = 0;
        String failure = null;
        long deadline = SystemClock.elapsedRealtime() + 20000;
        try {
            while (SystemClock.elapsedRealtime() < deadline) {
                List<String> snapshot;
                synchronized (FILE_LOCK) { snapshot = readQueue(app); }
                if (snapshot.isEmpty()) break;
                JSONArray batch = new JSONArray();
                int byteCount = 2;
                for (String line : snapshot) {
                    int length = line.getBytes(StandardCharsets.UTF_8).length + 1;
                    if (batch.length() > 0 && (batch.length() >= 20 || byteCount + length > 230000)) break;
                    JSONObject item = new JSONObject(line);
                    String[] safeContent = CapturePrivacy.redactPair(item.optString("title", ""), item.optString("text", ""));
                    item.put("title", safeContent[0]);
                    item.put("text", safeContent[1]);
                    batch.put(item);
                    byteCount += length;
                }
                if (OneDriveSync.enabled(app)) OneDriveSync.upload(app, batch);
                else post(app, batch);
                synchronized (FILE_LOCK) {
                    List<String> current = readQueue(app);
                    // Concurrent writers only append. Keep everything not acknowledged.
                    if (current.size() < batch.length()
                        || !current.subList(0, batch.length()).equals(snapshot.subList(0, batch.length()))) {
                        throw new IOException("Queue prefix changed");
                    }
                    List<String> remaining = new ArrayList<>(current.subList(batch.length(), current.size()));
                    writeQueue(app, remaining);
                    preferences(app).edit().putInt("pending_count", remaining.size()).apply();
                }
                sent += batch.length();
            }
            retryAfter = OneDriveSync.enabled(app) ? SystemClock.elapsedRealtime() + 30000 : 0;
        } catch (Exception error) {
            // Diagnostics never include endpoint tokens or captured content.
            failure = OneDriveSync.enabled(app) ? OneDriveSync.message(error)
                : "电脑暂不可达或拒绝请求（" + error.getClass().getSimpleName() + "）";
            retryAfter = SystemClock.elapsedRealtime() + 60000;
        }
        report(app, sent, failure);
        List<Callback> callbacks;
        synchronized (SYNC_LOCK) {
            callbacks = new ArrayList<>(CALLBACKS);
            CALLBACKS.clear();
            syncing = false;
        }
        for (Callback callback : callbacks) {
            try { callback.complete(sent, countPending(app), failure); } catch (Exception ignored) {}
        }
        if(OneDriveSync.enabled(app) && countPending(app)>0) requestFlush(app,null,false);
        SyncJobService.schedule(app);
    }

    private static void report(Context app, int sent, String failure) {
        SharedPreferences.Editor editor = preferences(app).edit()
            .putString("last_sync_error", failure == null ? "" : failure);
        if (sent > 0) editor.putLong("last_synced_at", System.currentTimeMillis())
            .putLong("synced_total", preferences(app).getLong("synced_total", 0) + sent);
        editor.apply();
    }

    static void health(Context context, Callback callback) {
        Context app = context.getApplicationContext();
        NETWORK.execute(() -> {
            HttpURLConnection connection = null;
            try {
                if (OneDriveSync.enabled(app)) {
                    OneDriveSync.health(app);
                    if(callback!=null) callback.complete(1,countPending(app),null);
                    return;
                }
                connection = connect(app, "/api/stats");
                connection.setRequestMethod("GET");
                int code = connection.getResponseCode();
                String error = code == 200 ? null : "HTTP " + code;
                preferences(app).edit().putBoolean("server_online", code == 200)
                    .putLong("server_checked_at", System.currentTimeMillis()).apply();
                if (callback != null) callback.complete(code == 200 ? 1 : 0, countPending(app), error);
            } catch (Exception error) {
                preferences(app).edit().putBoolean("server_online", false).apply();
                if (callback != null) callback.complete(0, countPending(app), OneDriveSync.enabled(app)
                    ? OneDriveSync.message(error) : "连接失败，请检查电脑服务及 Wi-Fi");
            } finally { if (connection != null) connection.disconnect(); }
        });
    }

    static int countPending(Context context) { return preferences(context).getInt("pending_count", 0); }

    private static HttpURLConnection connect(Context app, String path) throws Exception {
        SharedPreferences prefs = preferences(app);
        String endpoint = prefs.getString("endpoint", "http://127.0.0.1:18765").replaceAll("/+$", "");
        String token = prefs.getString("token", "");
        if (token.isEmpty()) throw new IOException("Pairing required");
        URL url = new URL(endpoint + path);
        if (!url.getProtocol().equals("http") && !url.getProtocol().equals("https")) throw new IOException("Invalid protocol");
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setConnectTimeout(5000);
        connection.setReadTimeout(5000);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Authorization", "Bearer " + token);
        return connection;
    }

    private static void post(Context app, JSONArray batch) throws Exception {
        HttpURLConnection connection = connect(app, "/api/events");
        try {
            byte[] body = batch.toString().getBytes(StandardCharsets.UTF_8);
            connection.setRequestMethod("POST");
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setDoOutput(true);
            connection.setFixedLengthStreamingMode(body.length);
            try (OutputStream stream = connection.getOutputStream()) { stream.write(body); }
            if (connection.getResponseCode() != 201) throw new IOException("Server rejected batch");
            StringBuilder response = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    response.append(line);
                    if (response.length() > 600000) throw new IOException("Unexpected response size");
                }
            }
            if (new JSONObject(response.toString()).getJSONArray("events").length() != batch.length()) {
                throw new IOException("Incomplete acknowledgement");
            }
        } finally { connection.disconnect(); }
    }

    private static AtomicFile queue(Context app) { return new AtomicFile(new File(app.getFilesDir(), QUEUE_FILE)); }
    private static List<String> readQueue(Context app) throws IOException {
        List<String> lines = new ArrayList<>();
        AtomicFile file = queue(app);
        if (!file.getBaseFile().exists() && !new File(file.getBaseFile().getPath() + ".bak").exists()) return lines;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(file.openRead(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) if (!line.trim().isEmpty()) lines.add(line);
        }
        preferences(app).edit().putInt("pending_count", lines.size()).apply();
        return lines;
    }

    private static void writeQueue(Context app, List<String> lines) throws IOException {
        AtomicFile file = queue(app);
        FileOutputStream stream = null;
        try {
            stream = file.startWrite();
            for (String line : lines) stream.write((line + "\n").getBytes(StandardCharsets.UTF_8));
            file.finishWrite(stream);
        } catch (IOException error) {
            if (stream != null) file.failWrite(stream);
            throw error;
        }
    }
}
