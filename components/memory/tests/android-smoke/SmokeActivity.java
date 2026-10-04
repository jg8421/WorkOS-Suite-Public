package com.personalmemory.smokefixture;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** Local-only synthetic UI for testing capture boundaries. No network or storage. */
public final class SmokeActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private LinearLayout content;
    private TextView changingText;
    private int counter;
    private int burstRemaining;

    private final Runnable burst = new Runnable() {
        @Override public void run() {
            changingText.setText("SMOKE_BURST_VISIBLE_" + (++counter));
            if (--burstRemaining > 0) handler.postDelayed(this, 200);
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        String mode = getIntent().getStringExtra("mode");
        showPage("form".equals(mode));
        if ("burst".equals(mode)) handler.postDelayed(this::startBurst, 1500);
    }

    private void showPage(boolean fields) {
        handler.removeCallbacks(burst);
        ScrollView scroll = new ScrollView(this);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        int padding = Math.round(20 * getResources().getDisplayMetrics().density);
        content.setPadding(padding, padding, padding, padding);
        scroll.addView(content);
        setContentView(scroll);

        addText("Memory capture smoke test", 23);
        addText("Synthetic local content only. This app has no permissions.", 16);
        addText("SMOKE_NEUTRAL_VISIBLE_20260930", 18);
        changingText = addText("SMOKE_UPDATE_VISIBLE_0", 18);
        if (fields) {
            addText("Editable sample", 16);
            EditText editable = new EditText(this);
            editable.setSingleLine(true);
            editable.setInputType(InputType.TYPE_CLASS_TEXT);
            editable.setText("SMOKE_EDITABLE_NEVER_STORE");
            content.addView(editable);

            addText("Protected field sample", 16);
            EditText protectedField = new EditText(this);
            protectedField.setSingleLine(true);
            protectedField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
            protectedField.setText("SMOKE_SECRET_NEVER_STORE_8472");
            content.addView(protectedField);
            addButton("Back to neutral page", view -> showPage(false));
        } else {
            addButton("Update neutral text", view -> changingText.setText("SMOKE_UPDATE_VISIBLE_" + (++counter)));
            addButton("Rapid updates (24 in 5 seconds)", view -> startBurst());
            addButton("Show field samples", view -> showPage(true));
        }
        content.setFocusableInTouchMode(true);
        content.requestFocus();
    }

    private TextView addText(String text, int size) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(size);
        view.setPadding(0, 10, 0, 10);
        content.addView(view);
        return view;
    }

    private void addButton(String text, View.OnClickListener listener) {
        Button button = new Button(this);
        button.setText(text);
        button.setOnClickListener(listener);
        content.addView(button);
    }

    private void startBurst() {
        handler.removeCallbacks(burst);
        burstRemaining = 24;
        handler.post(burst);
    }

    @Override protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        super.onDestroy();
    }
}
