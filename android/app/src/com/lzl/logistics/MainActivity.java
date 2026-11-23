package com.lzl.logistics;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

/**
 * 跨境物流引擎 · Android 壳。
 * 离线模式：加载 assets/www，所有 /api 请求由内置 JS 规则引擎就地计算；
 * 连电脑模式：直接加载 http://<电脑IP>:8000/ 的完整后端看板。
 */
public class MainActivity extends Activity {

    private static final String PREF = "logi_cfg";
    private static final String KEY_MODE = "mode";     // offline | server
    private static final String KEY_SERVER = "server"; // http://192.168.1.5:8000

    private WebView web;
    private FrameLayout root;
    private SharedPreferences pref;
    private TextView gear;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        pref = getSharedPreferences(PREF, Context.MODE_PRIVATE);

        root = new FrameLayout(this);
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        addSettingsButton();
        setContentView(root);

        setupWebView();
        addBridge();
        loadCurrent();
    }

    private void setupWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        s.setLoadsImagesAutomatically(true);
        s.setTextZoom(100);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setUseWideViewPort(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        if (Build.VERSION.SDK_INT >= 26) {
            s.setSafeBrowsingEnabled(false);
        }

        web.setBackgroundColor(Color.parseColor("#0b0f14"));
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return handleUrl(url);
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, android.webkit.WebResourceRequest request) {
                return handleUrl(request.getUrl().toString());
            }
        });
    }

    private boolean handleUrl(String url) {
        if (url == null) return false;
        if (url.startsWith("file://") || isLocalServer(url)) return false;
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (Exception ignored) {
        }
        return true;
    }

    private boolean isLocalServer(String url) {
        String server = pref.getString(KEY_SERVER, "");
        return server != null && server.length() > 0 && url.startsWith(server);
    }

    private void addBridge() {
        web.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public String getConfig() {
                return "{\"mode\":\"" + pref.getString(KEY_MODE, "offline") + "\",\"server\":\""
                        + pref.getString(KEY_SERVER, "") + "\",\"version\":\"1.0.0\"}";
            }

            @JavascriptInterface
            public void setConfig(String mode, String server) {
                pref.edit().putString(KEY_MODE, mode).putString(KEY_SERVER, server).apply();
            }

            @JavascriptInterface
            public void toast(String msg) {
                final String text = msg;
                runOnUiThread(new Runnable() {
                    public void run() {
                        Toast.makeText(MainActivity.this, text, Toast.LENGTH_SHORT).show();
                    }
                });
            }

            @JavascriptInterface
            public String saveCsv(String name, String content) {
                return MainActivity.this.saveCsv(name, content);
            }

            @JavascriptInterface
            public void openSettings() {
                runOnUiThread(new Runnable() {
                    public void run() {
                        showSettingsDialog();
                    }
                });
            }
        }, "AndroidBridge");
    }

    /** 保存到「下载/跨境物流引擎」；API 29+ 走 MediaStore，老系统落到应用外部目录。 */
    private String saveCsv(String name, String content) {
        String safe = name == null ? "quote.csv" : name.replaceAll("[\\\\/:*?\"<>|]", "_");
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                ContentValues values = new ContentValues();
                values.put(MediaStore.Downloads.DISPLAY_NAME, safe);
                values.put(MediaStore.Downloads.MIME_TYPE, "text/csv");
                values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/跨境物流引擎");
                Uri uri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                if (uri == null) return "";
                OutputStream out = getContentResolver().openOutputStream(uri);
                if (out == null) return "";
                out.write(content.getBytes(StandardCharsets.UTF_8));
                out.close();
                return "已保存到 下载/跨境物流引擎/" + safe;
            }
            File dir = new File(getExternalFilesDir(null), "exports");
            if (!dir.exists()) dir.mkdirs();
            File file = new File(dir, safe);
            FileOutputStream out = new FileOutputStream(file);
            out.write(content.getBytes(StandardCharsets.UTF_8));
            out.close();
            return "已保存到 " + file.getAbsolutePath();
        } catch (Exception e) {
            return "保存失败：" + e.getMessage();
        }
    }

    private void addSettingsButton() {
        TextView gear = new TextView(this);
        gear.setText("设置");
        gear.setTextColor(Color.parseColor("#dbe6f2"));
        gear.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        gear.setGravity(Gravity.CENTER);
        gear.setPadding(dp(10), dp(6), dp(10), dp(6));
        gear.setBackgroundColor(Color.parseColor("#33121821"));
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.gravity = Gravity.BOTTOM | Gravity.END;
        lp.setMargins(0, 0, dp(10), dp(10));
        gear.setLayoutParams(lp);
        gear.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                showSettingsDialog();
            }
        });
        root.addView(gear);
        this.gear = gear;
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private void showSettingsDialog() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(18);
        box.setPadding(pad, pad, pad, pad);

        TextView tip = new TextView(this);
        tip.setText("离线模式：全部规则引擎在手机本地运行，无需电脑。\n连电脑模式：手机与电脑同一 WiFi，电脑上启动 uvicorn --host 0.0.0.0 --port 8000。");
        tip.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        tip.setTextColor(Color.parseColor("#8b9bad"));
        box.addView(tip);

        final RadioGroup group = new RadioGroup(this);
        final RadioButton offline = new RadioButton(this);
        offline.setText("离线模式（推荐，随身可用）");
        offline.setId(1001);
        final RadioButton serverMode = new RadioButton(this);
        serverMode.setText("连电脑的完整后端");
        serverMode.setId(1002);
        group.addView(offline);
        group.addView(serverMode);
        boolean isServer = "server".equals(pref.getString(KEY_MODE, "offline"));
        group.check(isServer ? 1002 : 1001);
        box.addView(group);

        final EditText input = new EditText(this);
        input.setHint("http://192.168.1.5:8000");
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(pref.getString(KEY_SERVER, ""));
        input.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        LinearLayout.LayoutParams ilp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        ilp.topMargin = dp(8);
        input.setLayoutParams(ilp);
        box.addView(input);

        new AlertDialog.Builder(this)
                .setTitle("连接方式")
                .setView(box)
                .setPositiveButton("保存", new android.content.DialogInterface.OnClickListener() {
                    public void onClick(android.content.DialogInterface d, int which) {
                        String mode = group.getCheckedRadioButtonId() == 1002 ? "server" : "offline";
                        String server = input.getText().toString().trim();
                        if ("server".equals(mode) && !server.startsWith("http")) {
                            Toast.makeText(MainActivity.this, "地址需以 http:// 开头", Toast.LENGTH_SHORT).show();
                            return;
                        }
                        while (server.endsWith("/")) server = server.substring(0, server.length() - 1);
                        pref.edit().putString(KEY_MODE, mode).putString(KEY_SERVER, server).apply();
                        loadCurrent();
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void loadCurrent() {
        boolean serverMode = "server".equals(pref.getString(KEY_MODE, "offline"));
        String server = pref.getString(KEY_SERVER, "");
        if (gear != null) {
            // 离线页自带「设置」标签；连服务模式加载的是电脑端页面，需要悬浮入口退回来
            gear.setVisibility(serverMode && server != null && server.length() > 0 ? View.VISIBLE : View.GONE);
        }
        if (serverMode && server != null && server.length() > 0) {
            web.loadUrl(server + "/");
            return;
        }
        web.loadUrl("file:///android_asset/www/index.html");
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            root.removeView(web);
            web.destroy();
        }
        super.onDestroy();
    }
}
