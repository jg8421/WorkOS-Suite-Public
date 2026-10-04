package com.personalmemory.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.Manifest;
import android.content.ComponentName;
import android.content.pm.PackageManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.speech.RecognizerIntent;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Locale;

public class MainActivity extends Activity {
    private static final int VOICE_REQUEST = 42;
    private EditText endpointInput;
    private EditText tokenInput;
    private EditText allowlistInput;
    private EditText noteInput;
    private EditText exclusionsInput;
    private EditText oneDriveClientInput;
    private EditText oneDriveTenantInput;
    private EditText oneDriveFolderInput;
    private CheckBox oneDriveCheck;
    private CheckBox captureCheck;
    private CheckBox enhancedCaptureCheck;
    private TextView statusView;
    private TextView captureStatusView;
    private Button pauseButton;
    private boolean loadingSettings;
    private final Handler uiHandler = new Handler(Looper.getMainLooper());
    private final Runnable statusRefresh = new Runnable() {
        @Override public void run() {
            refreshCaptureState();
            uiHandler.postDelayed(this, 2000);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        acceptBootstrapExtras(getIntent());
        handleSharedIntent(getIntent());
        checkHealth();
        MemoryClient.flush(this, this::showSyncResult);
        SyncJobService.schedule(this);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        acceptBootstrapExtras(intent);
        loadSettings();
        handleSharedIntent(intent);
    }

    private void buildUi() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(20), dp(24), dp(20), dp(30));
        root.setBackgroundColor(Color.rgb(247, 247, 252));
        scroll.addView(root);

        TextView title = text("Personal Memory", 28, true);
        root.addView(title);
        TextView subtitle = text("0.3.0 · 手机可独立直传 OneDrive，电脑自动合并记忆。", 15, false);
        subtitle.setTextColor(Color.DKGRAY);
        root.addView(subtitle, margin(0, 6, 0, 18));
        captureStatusView = text("正在读取采集状态……", 14, false);
        root.addView(captureStatusView, margin(0, 0, 0, 8));
        pauseButton = button("暂停全部自动采集");
        pauseButton.setOnClickListener(view -> {
            SharedPreferences prefs = MemoryClient.preferences(this);
            prefs.edit().putBoolean("capture_paused", !prefs.getBoolean("capture_paused", false)).apply();
            CaptureStatus.update(this);
            refreshCaptureState();
        });
        root.addView(pauseButton);

        root.addView(label("OneDrive 手机直传"), margin(0, 16, 0, 6));
        root.addView(text("首次需为 Personal Memory 完成微软授权。授权后可通过 Wi-Fi 或移动网络上传，无需电脑在线；小批量合并上传以减少耗电。", 13, false));
        oneDriveClientInput = input("Personal Memory 的 Microsoft 应用 Client ID", false, false);
        oneDriveTenantInput = input("Microsoft 组织域名或租户 ID；默认 organizations", false, false);
        oneDriveFolderInput = input("OneDrive 目标路径，例如 Personal Memory/data", false, false);
        root.addView(oneDriveClientInput);
        root.addView(oneDriveTenantInput);
        root.addView(oneDriveFolderInput);
        oneDriveCheck = new CheckBox(this);
        oneDriveCheck.setText("手机直接上传 OneDrive（需先完成微软授权）");
        oneDriveCheck.setOnCheckedChangeListener((view, checked) -> {
            if(loadingSettings) return;
            if(checked && !OneDriveSync.connected(this)) {
                loadingSettings=true; oneDriveCheck.setChecked(false); loadingSettings=false;
                Toast.makeText(this,"请先连接 Microsoft",Toast.LENGTH_SHORT).show(); return;
            }
            MemoryClient.preferences(this).edit().putBoolean("onedrive_enabled",checked).apply();
            refreshCaptureState(); MemoryClient.flush(this,null);
        });
        root.addView(oneDriveCheck);
        TextView oneDriveLoginStatus = text("点击连接后，授权进度或错误会显示在这里。", 13, false);
        root.addView(oneDriveLoginStatus, margin(0, 6, 0, 6));
        Button connectOneDrive = button("连接 Microsoft / OneDrive");
        connectOneDrive.setOnClickListener(view -> {
            String client = oneDriveClientInput.getText().toString().trim();
            String tenant = oneDriveTenantInput.getText().toString().trim();
            if(!client.matches("[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}")) {
                String message = "请先填写 Personal Memory 的 Microsoft 应用 Client ID。手机已登录 OneDrive，不代表这个应用已经获准上传。";
                oneDriveLoginStatus.setText(message);
                oneDriveClientInput.setError("需要有效的 Microsoft 应用 Client ID");
                new AlertDialog.Builder(this).setTitle("尚未配置 Microsoft 应用")
                    .setMessage(message + "\n\n先注册 Personal Memory 应用，再将‘应用程序（客户端）ID’填入上方。不要填写密码或客户端密钥。")
                    .setPositiveButton("知道了", null).show();
                return;
            }
            if(!tenant.matches("[a-zA-Z0-9.-]{1,128}")) {
                oneDriveTenantInput.setError("请填写组织域名或租户 ID");
                oneDriveLoginStatus.setText("Microsoft 租户格式不正确，请检查上方输入。");
                return;
            }
            saveSettings();
            connectOneDrive.setEnabled(false);
            connectOneDrive.setText("正在连接 Microsoft……");
            oneDriveLoginStatus.setText("正在请求 Microsoft 官方授权，请稍候；网络失败会在这里提示。");
            statusView.setText("正在请求 Microsoft 官方授权……");
            OneDriveSync.login(this,(message,code,complete) -> runOnUiThread(() -> {
                if(isFinishing() || isDestroyed()) return;
                connectOneDrive.setEnabled(true);
                connectOneDrive.setText("连接 Microsoft / OneDrive");
                oneDriveLoginStatus.setText(message);
                statusView.setText(message); loadSettings(); refreshCaptureState();
                if(!code.isEmpty()) new AlertDialog.Builder(this).setTitle("Microsoft 官方授权")
                    .setMessage(message+"\n\n授权码："+code+"\n\n请使用保存目标文件夹的 Microsoft 账户。")
                    .setNegativeButton("稍后",null).setPositiveButton("复制授权码并打开微软",(dialog,which)-> {
                        android.content.ClipboardManager clipboard=(android.content.ClipboardManager)getSystemService(CLIPBOARD_SERVICE);
                        clipboard.setPrimaryClip(android.content.ClipData.newPlainText("Microsoft login code",code));
                        startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse("https://microsoft.com/devicelogin")));
                    }).show();
                else if(!complete) new AlertDialog.Builder(this).setTitle("Microsoft 连接未完成")
                    .setMessage(message + "\n\n待上传记录仍保留在手机。请检查配置或网络后重试。")
                    .setPositiveButton("知道了", null).show();
            }));
        });
        root.addView(connectOneDrive);
        Button disconnectOneDrive=button("断开 OneDrive 授权（保留待上传记录）");
        disconnectOneDrive.setOnClickListener(view -> {
            OneDriveSync.disconnect(this);
            connectOneDrive.setEnabled(true); connectOneDrive.setText("连接 Microsoft / OneDrive");
            oneDriveLoginStatus.setText("已断开 OneDrive 授权，待上传记录保留。");
            loadSettings(); refreshCaptureState();
        });
        root.addView(disconnectOneDrive);

        endpointInput = input("服务地址，例如 http://192.168.1.10:18765", false, false);
        tokenInput = input("配对令牌", true, false);
        allowlistInput = input("通知 App 包名，逗号分隔；* 表示全部", false, false);
        exclusionsInput = input("额外排除的 App 包名，逗号分隔（可留空）", false, false);
        captureCheck = new CheckBox(this);
        captureCheck.setText("启用通知采集（仍需系统授权）");
        captureCheck.setTextSize(15);
        enhancedCaptureCheck = new CheckBox(this);
        enhancedCaptureCheck.setText("启用增强上下文采集（不记录密码框和键盘逐字输入）");
        enhancedCaptureCheck.setTextSize(15);
        captureCheck.setOnCheckedChangeListener((buttonView, checked) -> {
            if (loadingSettings) return;
            MemoryClient.preferences(this).edit().putBoolean("capture_enabled", checked).apply();
            CaptureStatus.update(this);
        });
        enhancedCaptureCheck.setOnCheckedChangeListener((buttonView, checked) -> {
            if (loadingSettings) return;
            if (checked) confirmEnhancedCapture();
            else {
                MemoryClient.preferences(this).edit().putBoolean("enhanced_capture_enabled", false).apply();
                CaptureStatus.update(this);
            }
        });
        root.addView(label("连接设置"), margin(0, 12, 0, 6));
        root.addView(endpointInput);
        root.addView(tokenInput, margin(0, 8, 0, 0));
        root.addView(allowlistInput, margin(0, 8, 0, 0));
        root.addView(exclusionsInput, margin(0, 8, 0, 0));
        root.addView(captureCheck, margin(0, 4, 0, 0));
        root.addView(enhancedCaptureCheck, margin(0, 2, 0, 0));

        Button save = button("保存设置并测试连接");
        save.setOnClickListener(view -> {
            saveSettings();
            checkHealth();
            MemoryClient.flush(this, this::showSyncResult);
        });
        root.addView(save, margin(0, 8, 0, 0));

        Button permission = button("打开系统通知访问权限");
        permission.setOnClickListener(view -> startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)));
        root.addView(permission, margin(0, 8, 0, 0));

        Button accessibilityPermission = button("打开增强监控（无障碍）权限");
        accessibilityPermission.setOnClickListener(view -> {
            if (!MemoryClient.preferences(this).getBoolean("enhanced_capture_enabled", false)) {
                confirmEnhancedCapture();
                return;
            }
            openAccessibilitySettings();
        });
        root.addView(accessibilityPermission, margin(0, 8, 0, 0));

        Button statusPermission = button("显示采集状态通知");
        statusPermission.setOnClickListener(view -> {
            if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 43);
            } else CaptureStatus.update(this);
        });
        root.addView(statusPermission);

        root.addView(label("快速记录"), margin(0, 22, 0, 6));
        noteInput = input("输入想让 AI 记住的事情……", false, true);
        noteInput.setMinLines(5);
        root.addView(noteInput);

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        Button voice = button("语音");
        voice.setOnClickListener(view -> startVoice());
        Button send = button("保存到 Memory");
        send.setOnClickListener(view -> sendNote());
        actions.addView(voice, new LinearLayout.LayoutParams(0, dp(50), 1));
        LinearLayout.LayoutParams sendParams = new LinearLayout.LayoutParams(0, dp(50), 2);
        sendParams.setMargins(dp(8), 0, 0, 0);
        actions.addView(send, sendParams);
        root.addView(actions, margin(0, 10, 0, 0));

        statusView = text("正在检查连接……", 14, false);
        statusView.setTextColor(Color.DKGRAY);
        root.addView(statusView, margin(0, 18, 0, 0));
        TextView privacy = text("采集范围：通知、App 切换、可读取的非编辑页面文字、按钮名称和估算使用时长。过滤已知敏感 App、密码/输入框及识别出的隐私页面。可通过已配对电脑的 HTTPS 入口上传，或单独授权 OneDrive 后写入配置的目标路径；电脑随后合并到记忆库。不会读取其他 App 的登录令牌。", 12, false);
        privacy.setTextColor(Color.GRAY);
        root.addView(privacy, margin(0, 10, 0, 0));

        setContentView(scroll);
        loadSettings();
    }

    private void acceptBootstrapExtras(Intent intent) {
        if (intent == null) return;
        String endpoint = intent.getStringExtra("endpoint");
        String token = intent.getStringExtra("token");
        if ((endpoint == null || endpoint.isEmpty()) && (token == null || token.isEmpty())) return;
        new AlertDialog.Builder(this).setTitle("确认更改配对")
            .setMessage("外部请求要更改 Memory 接收端为：" + (endpoint == null ? "现有地址" : endpoint)
                + "\n只有你正在为自己的电脑配对时才继续。")
            .setNegativeButton("取消", null).setPositiveButton("更新配对", (dialog, which) -> {
                SharedPreferences.Editor editor = MemoryClient.preferences(this).edit();
                if (endpoint != null && !endpoint.isEmpty()) editor.putString("endpoint", endpoint);
                if (token != null && !token.isEmpty()) editor.putString("token", token);
                editor.apply();
                loadSettings();
                checkHealth();
            }).show();
    }

    private void loadSettings() {
        if (endpointInput == null) return;
        loadingSettings = true;
        SharedPreferences prefs = MemoryClient.preferences(this);
        oneDriveClientInput.setText(prefs.getString("onedrive_client_id", ""));
        oneDriveTenantInput.setText(prefs.getString("onedrive_tenant", "organizations"));
        oneDriveFolderInput.setText(prefs.getString("onedrive_folder", "Personal Memory/data"));
        oneDriveCheck.setChecked(OneDriveSync.enabled(this));
        endpointInput.setText(prefs.getString("endpoint", "http://127.0.0.1:18765"));
        tokenInput.setText(prefs.getString("token", ""));
        allowlistInput.setText(prefs.getString("allowlist", "*"));
        exclusionsInput.setText(prefs.getString("custom_excluded_packages", ""));
        captureCheck.setChecked(prefs.getBoolean("capture_enabled", false));
        enhancedCaptureCheck.setChecked(prefs.getBoolean("enhanced_capture_enabled", false)
            && prefs.getInt("enhanced_capture_consent_version", 0) == 1);
        loadingSettings = false;
    }

    private void saveSettings() {
        SharedPreferences previous=MemoryClient.preferences(this);
        String client=oneDriveClientInput.getText().toString().trim();
        String tenant=oneDriveTenantInput.getText().toString().trim();
        String folder=oneDriveFolderInput.getText().toString().trim();
        if(!client.equals(previous.getString("onedrive_client_id","")) || !tenant.equals(previous.getString("onedrive_tenant","organizations")) || !folder.equals(previous.getString("onedrive_folder","Personal Memory/data")))
            OneDriveSync.disconnect(this);
        MemoryClient.preferences(this).edit()
            .putString("onedrive_client_id",client).putString("onedrive_tenant",tenant)
            .putString("onedrive_folder",folder)
            .putString("endpoint", endpointInput.getText().toString().trim())
            .putString("token", tokenInput.getText().toString().trim())
            .putString("allowlist", allowlistInput.getText().toString().trim())
            .putString("custom_excluded_packages", exclusionsInput.getText().toString().trim())
            .putBoolean("capture_enabled", captureCheck.isChecked())
            .putBoolean("enhanced_capture_enabled", enhancedCaptureCheck.isChecked())
            .apply();
        Toast.makeText(this, "设置已保存", Toast.LENGTH_SHORT).show();
        CaptureStatus.update(this);
    }

    private void confirmEnhancedCapture() {
        if (MemoryClient.preferences(this).getInt("enhanced_capture_consent_version", 0) == 1) {
            MemoryClient.preferences(this).edit().putBoolean("enhanced_capture_enabled", true).apply();
            CaptureStatus.update(this);
            return;
        }
        loadingSettings = true;
        enhancedCaptureCheck.setChecked(false);
        loadingSettings = false;
        new AlertDialog.Builder(this).setTitle("开启页面上下文采集")
            .setMessage("开启后，Personal Memory 会在后台读取其他 App 向无障碍开放的页面文字、按钮名称及 App 切换，可能包括聊天消息。内容在手机脱敏后发给已配对接收端，存入你选择的数据目录；若目录由 OneDrive 同步，内容也会上传到该账户。\n\n已知银行、支付、认证器、密码管理器和输入框会被过滤；页面识别可能不完整。你可以在本应用或通知中随时暂停。不会启用录屏、连续录音或键盘逐字记录。\n\n继续后还需在系统页面授予无障碍权限。")
            .setNegativeButton("暂不开启", null)
            .setPositiveButton("开启并前往系统授权", (dialog, which) -> {
                MemoryClient.preferences(this).edit().putInt("enhanced_capture_consent_version", 1)
                    .putBoolean("enhanced_capture_enabled", true).apply();
                loadSettings();
                CaptureStatus.update(this);
                openAccessibilitySettings();
            }).show();
    }

    private void openAccessibilitySettings() {
        Intent intent = new Intent("android.settings.ACCESSIBILITY_DETAILS_SETTINGS");
        intent.putExtra("android.intent.extra.COMPONENT_NAME", new ComponentName(this, ContextAccessibilityService.class).flattenToString());
        try { startActivity(intent); }
        catch (Exception ignored) { startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)); }
    }

    private String dateText(long millis) {
        return millis == 0 ? "暂无" : new java.text.SimpleDateFormat("MM-dd HH:mm:ss", Locale.getDefault()).format(new java.util.Date(millis));
    }

    private void refreshCaptureState() {
        if (captureStatusView == null) return;
        SharedPreferences prefs = MemoryClient.preferences(this);
        boolean paused = prefs.getBoolean("capture_paused", false);
        String listeners = Settings.Secure.getString(getContentResolver(), "enabled_notification_listeners");
        String accessibility = Settings.Secure.getString(getContentResolver(), "enabled_accessibility_services");
        boolean notificationGranted = listeners != null && listeners.contains(getPackageName() + "/");
        boolean contextGranted = accessibility != null && accessibility.contains("ContextAccessibilityService");
        String notificationState = !prefs.getBoolean("capture_enabled", false) ? "关闭"
            : notificationGranted ? "已授权" : "待授权";
        String contextState = !prefs.getBoolean("enhanced_capture_enabled", false) ? "关闭"
            : contextGranted ? "已授权" : "待授权";
        captureStatusView.setText((paused ? "自动采集已暂停" : "自动采集就绪（以权限和过滤为准）")
            + "\n通知：" + notificationState + " · 上下文：" + contextState
            + "\n待同步：" + MemoryClient.countPending(this) + " 条 · 最近入队：" + dateText(prefs.getLong("last_queued_at", 0))
            + "\n同步方式：" + (OneDriveSync.enabled(this) ? "手机直传 OneDrive" : "通过电脑同步 OneDrive")
            + "\n最近写入" + (OneDriveSync.enabled(this) ? "云端：" : "电脑：") + dateText(prefs.getLong(OneDriveSync.enabled(this)?"onedrive_last_upload":"last_synced_at", 0))
            + "\n" + prefs.getString("last_sync_error", ""));
        pauseButton.setText(paused ? "恢复自动采集" : "暂停全部自动采集");
    }

    @Override protected void onResume() {
        super.onResume();
        uiHandler.removeCallbacks(statusRefresh);
        uiHandler.post(statusRefresh);
        CaptureStatus.update(this);
    }
    @Override protected void onPause() {
        uiHandler.removeCallbacks(statusRefresh);
        super.onPause();
    }

    private void handleSharedIntent(Intent intent) {
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) return;
        StringBuilder shared = new StringBuilder();
        CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        if (text != null) shared.append(text);
        Uri stream = intent.getParcelableExtra(Intent.EXTRA_STREAM);
        if (stream != null) {
            if (shared.length() > 0) shared.append("\n");
            shared.append("Shared attachment: ").append(stream);
        }
        if (shared.length() > 0 && noteInput != null) noteInput.setText(shared.toString());
    }

    private void sendNote() {
        saveSettings();
        String text = noteInput.getText().toString().trim();
        if (text.isEmpty()) {
            Toast.makeText(this, "请先输入内容", Toast.LENGTH_SHORT).show();
            return;
        }
        try {
            JSONObject event = new JSONObject();
            event.put("type", "note");
            event.put("text", text);
            event.put("source", "android:manual");
            event.put("timestamp", java.time.Instant.now().toString());
            event.put("importance", 0.7);
            event.put("tags", new JSONArray().put("android").put("manual"));
            event.put("sourceId", "android-" + System.currentTimeMillis());
            MemoryClient.enqueue(this, event, (sent, pending, error) -> {
                showSyncResult(sent, pending, error);
                if (error == null) runOnUiThread(() -> noteInput.setText(""));
            });
        } catch (Exception error) {
            Toast.makeText(this, error.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private void checkHealth() {
        if (statusView == null) return;
        statusView.setText(OneDriveSync.enabled(this)?"正在检查 OneDrive……":"正在检查 Windows 服务……");
        MemoryClient.health(this, (sent, pending, error) -> runOnUiThread(() -> {
            if (error == null) statusView.setText((OneDriveSync.enabled(this)?"OneDrive 可访问":"Windows 服务在线") + " · 待同步 " + pending + " 条");
            else statusView.setText("暂未连接 · 已安全排队 " + pending + " 条 · " + error);
        }));
    }

    private void showSyncResult(int sent, int pending, String error) {
        runOnUiThread(() -> {
            if (error == null) statusView.setText("同步成功 " + sent + " 条 · 待同步 " + pending + " 条");
            else statusView.setText("同步稍后重试 · 待同步 " + pending + " 条 · " + error);
        });
    }

    private void startVoice() {
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault());
        intent.putExtra(RecognizerIntent.EXTRA_PROMPT, "说出想记录的内容");
        try {
            startActivityForResult(intent, VOICE_REQUEST);
        } catch (Exception error) {
            Toast.makeText(this, "系统没有可用的语音识别服务", Toast.LENGTH_LONG).show();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == VOICE_REQUEST && resultCode == RESULT_OK && data != null) {
            ArrayList<String> results = data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);
            if (results != null && !results.isEmpty()) noteInput.setText(results.get(0));
        }
    }

    private TextView label(String value) {
        TextView view = text(value, 17, true);
        view.setTextColor(Color.rgb(55, 48, 163));
        return view;
    }

    private TextView text(String value, int size, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        if (bold) view.setTypeface(null, android.graphics.Typeface.BOLD);
        return view;
    }

    private EditText input(String hint, boolean password, boolean multiline) {
        EditText input = new EditText(this);
        input.setHint(hint);
        input.setTextSize(15);
        input.setPadding(dp(12), dp(10), dp(12), dp(10));
        input.setBackgroundColor(Color.WHITE);
        if (password) input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        else if (multiline) input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        else input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        return input;
    }

    private Button button(String value) {
        Button button = new Button(this);
        button.setText(value);
        button.setTextSize(14);
        button.setAllCaps(false);
        return button;
    }

    private LinearLayout.LayoutParams margin(int left, int top, int right, int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.setMargins(dp(left), dp(top), dp(right), dp(bottom));
        return params;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
