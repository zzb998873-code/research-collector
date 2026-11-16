package cn.researchwiki.collector;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Parcelable;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.view.inputmethod.InputMethodManager;
import android.text.InputFilter;
import android.text.TextUtils;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONArray;
import org.json.JSONObject;

public final class CollectorActivity extends Activity {
    private static final int INK = 0xff49362b;
    private static final int MUTED = 0xff796253;
    private static final int SURFACE = 0xfff6ebd9;
    private static final int TINT = 0xfffff6e6;
    private static final int BORDER = 0xff49362b;
    private static final int HONEY = 0xfff1bc69;
    private static final int PAPER = 0xfffffcf4;
    private static final int ERROR = 0xff9f1239;
    private static final String[] TYPES = {"科研绘图", "写作润色", "学科进展", "配色", "数据处理", "自定义"};
    private static final String[] LEVELS = {"低", "中", "高"};
    private static final int PICK_PAIRING = 1;
    private static final int PICK_MEDIA = 2;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler timer = new Handler(Looper.getMainLooper());
    private final Runnable refreshLater = () -> sync(false);
    private final ArrayList<Uri> files = new ArrayList<>();
    private final List<Button> typeButtons = new ArrayList<>();
    private final List<Button> levelButtons = new ArrayList<>();
    private CollectorStore store;
    private EditText draft, custom, drawingNotes;
    private LinearLayout captureCard, records;
    private TextView linkStatus, attachmentStatus, pairingState, queueState, syncState, feedback;
    private Button confirm, pair, sync, clearFiles, addMedia, addDocument, paste, viewRecords;
    private ScrollView scroll;
    private CheckBox complete;
    private String category = "", importance = "中", sharedTitle = "", lastClipboard = "";
    private boolean shared, busy, syncing, foreground, showingRecords;
    private String lastRecords = "";
    private String parentId = "", parentUrl = "";
    private Button newCapture;
    private JSONObject duplicateRecord;
    private final Runnable checkDuplicate = this::checkDuplicate;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        store = new CollectorStore(this);
        build();
        if (saved != null) {
            draft.setText(saved.getString("draft", ""));
            custom.setText(saved.getString("custom", ""));
            drawingNotes.setText(saved.getString("drawingNotes", ""));
            category = saved.getString("category", "");
            importance = saved.getString("importance", "中");
            sharedTitle = saved.getString("title", "");
            lastClipboard = saved.getString("clipboard", "");
            shared = saved.getBoolean("shared");
            parentId = saved.getString("parentId", "");
            parentUrl = saved.getString("parentUrl", "");
            complete.setChecked(saved.getBoolean("complete"));
            ArrayList<Uri> restored = saved.getParcelableArrayList("files");
            if (restored != null) files.addAll(restored);
            showRecords(saved.getBoolean("showingRecords"));
        } else {
            restoreType();
            importShare(getIntent());
        }
        refreshForm();
        refreshQueue();
    }

    @Override
    protected void onResume() {
        super.onResume();
        foreground = true;
        sync(true);
    }

    @Override
    protected void onPause() {
        foreground = false;
        timer.removeCallbacks(refreshLater);
        super.onPause();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putString("draft", draft.getText().toString());
        out.putString("custom", custom.getText().toString());
        out.putString("drawingNotes", drawingNotes.getText().toString());
        out.putString("category", category);
        out.putString("importance", importance);
        out.putString("title", sharedTitle);
        out.putString("clipboard", lastClipboard);
        out.putBoolean("shared", shared);
        out.putString("parentId", parentId);
        out.putString("parentUrl", parentUrl);
        out.putBoolean("complete", complete.isChecked());
        out.putBoolean("showingRecords", showingRecords);
        out.putParcelableArrayList("files", new ArrayList<>(files));
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (!busy) importShare(intent);
    }

    @Override
    public void onWindowFocusChanged(boolean focused) {
        super.onWindowFocusChanged(focused);
        if (focused && draft != null && !busy && !shared && draft.length() == 0 && files.isEmpty()) {
            draft.post(() -> {
                if (hasWindowFocus() && !busy && !shared && draft.length() == 0 && files.isEmpty()) {
                    loadClipboard(false);
                }
            });
        }
    }

    @Override
    protected void onDestroy() {
        timer.removeCallbacks(checkDuplicate);
        timer.removeCallbacks(refreshLater);
        io.shutdown();
        super.onDestroy();
    }

    private int dp(int n) {
        return Math.round(n * getResources().getDisplayMetrics().density);
    }

    private GradientDrawable surface(int color, int stroke, int radius) {
        GradientDrawable background = new GradientDrawable();
        background.setColor(color);
        background.setCornerRadius(dp(radius));
        if (stroke != 0) background.setStroke(dp(1), stroke);
        return background;
    }

    private LinearLayout column() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        return layout;
    }

    private LinearLayout row() {
        LinearLayout layout = new LinearLayout(this);
        layout.setGravity(Gravity.CENTER_VERTICAL);
        return layout;
    }

    private TextView label(String value, int size, int color, boolean bold) {
        TextView text = new TextView(this);
        text.setText(value);
        text.setTextSize(size);
        text.setTextColor(color);
        text.setLineSpacing(dp(2), 1);
        if (bold) text.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        return text;
    }

    private void append(LinearLayout parent, View item, int gap) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(gap);
        parent.addView(item, lp);
    }

    private void weighted(LinearLayout parent, View item, int gap) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, -2, 1);
        lp.leftMargin = dp(gap);
        parent.addView(item, lp);
    }

    private Button button(String title, Runnable click) {
        Button button = new Button(this);
        button.setAllCaps(false);
        button.setMinHeight(dp(48));
        button.setMinimumHeight(dp(48));
        button.setMinWidth(0);
        button.setMinimumWidth(0);
        button.setPadding(dp(9), 0, dp(13), dp(4));
        button.setText(title);
        button.setTextSize(14);
        button.setSingleLine(true);
        button.setEllipsize(TextUtils.TruncateAt.END);
        style(button, false);
        button.setOnClickListener(v -> click.run());
        return button;
    }

    private void style(Button button, boolean selected) {
        button.setBackground(new ComicSurface(getResources().getDisplayMetrics().density,
                selected ? HONEY : TINT, INK));
        button.setStateListAnimator(null);
        button.setElevation(0);
        button.setTextColor(INK);
        button.setTypeface(Typeface.DEFAULT, selected ? Typeface.BOLD : Typeface.NORMAL);
        button.setAlpha(button.isEnabled() ? 1f : .5f);
    }

    private EditText field(String hint, int limit) {
        EditText text = new EditText(this);
        text.setTextColor(INK);
        text.setHintTextColor(MUTED);
        text.setTextSize(16);
        text.setHint(hint);
        text.setContentDescription(hint);
        text.setPadding(dp(12), dp(8), dp(12), dp(8));
        text.setMinHeight(dp(48));
        text.setBackground(surface(SURFACE, BORDER, 9));
        text.setFilters(new InputFilter[]{new InputFilter.LengthFilter(limit)});
        return text;
    }

    private void build() {
        getWindow().setStatusBarColor(SURFACE);
        getWindow().setNavigationBarColor(SURFACE);
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(SURFACE);
        LinearLayout page = column();
        page.setPadding(dp(16), dp(16), dp(16), dp(24));
        page.setFocusableInTouchMode(true);
        if (android.os.Build.VERSION.SDK_INT >= 35) {
            scroll.setOnApplyWindowInsetsListener((v, insets) -> {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                v.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, insets.getInsets(WindowInsets.Type.ime()).bottom));
                return insets;
            });
        }
        scroll.addView(page, new ScrollView.LayoutParams(-1, -2));
        setContentView(scroll);
        page.requestFocus();

        LinearLayout header = row();
        LinearLayout title = column();
        append(title, label("科研收集", 30, INK, true), 0);
        pairingState = label("", 14, MUTED, false);
        append(title, pairingState, 2);
        weighted(header, title, 0);
        pair = button("配对电脑", this::choosePairing);
        header.addView(pair);
        append(page, header, 0);

        LinearLayout status = column();
        status.setPadding(dp(12), dp(8), dp(12), dp(10));
        status.setBackground(surface(TINT, BORDER, 14));
        LinearLayout statusRow = row();
        queueState = label("", 14, INK, true);
        weighted(statusRow, queueState, 0);
        viewRecords = button("处理记录", () -> showRecords(!showingRecords));
        statusRow.addView(viewRecords);
        sync = button("立即同步", () -> sync(true));
        LinearLayout.LayoutParams syncLayout = new LinearLayout.LayoutParams(-2, -2);
        syncLayout.leftMargin = dp(8);
        statusRow.addView(sync, syncLayout);
        append(status, statusRow, 0);
        syncState = label("", 14, MUTED, false);
        syncState.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        status.addView(syncState, new LinearLayout.LayoutParams(-2, -2));
        append(page, status, 12);

        LinearLayout card = column();
        captureCard = card;
        card.setBackground(new ComicSurface(getResources().getDisplayMetrics().density, PAPER, INK));
        card.setPadding(dp(16), dp(14), dp(16), dp(16));
        append(page, card, 16);
        append(card, label("待收集内容", 19, INK, true), 0);
        newCapture = button("← 改为新建收集", this::leaveSupplement);
        append(card, newCapture, 8);
        linkStatus = label("", 14, MUTED, false);
        linkStatus.setMaxLines(2);
        linkStatus.setEllipsize(TextUtils.TruncateAt.END);
        append(card, linkStatus, 4);
        draft = field("链接或分享文字", 90000);
        draft.setGravity(Gravity.TOP | Gravity.START);
        draft.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        draft.setMinLines(2);
        draft.setMaxLines(4);
        append(card, draft, 8);

        LinearLayout actions = row();
        paste = button("从剪贴板载入", () -> loadClipboard(true));
        weighted(actions, paste, 0);
        addMedia = button("添加图片或视频", this::chooseMedia);
        weighted(actions, addMedia, 8);
        append(card, actions, 8);
        addDocument = button("上传 PDF / Word", this::chooseDocuments);
        append(card, addDocument, 8);

        LinearLayout attachments = row();
        attachmentStatus = label("", 14, MUTED, false);
        weighted(attachments, attachmentStatus, 0);
        clearFiles = button("清除附件", () -> {
            files.clear();
            complete.setChecked(false);
            refreshForm();
        });
        attachments.addView(clearFiles);
        append(card, attachments, 4);
        complete = new CheckBox(this);
        complete.setText("正文和原始附件已齐全");
        complete.setTextColor(INK);
        complete.setTextSize(14);
        complete.setMinHeight(dp(48));
        complete.setButtonTintList(ColorStateList.valueOf(INK));
        append(card, complete, 0);

        append(card, label("资料类型", 16, INK, true), 14);
        optionRows(card, TYPES, typeButtons, true);
        custom = field("输入自定义类型", 32);
        custom.setSingleLine(true);
        append(card, custom, 8);
        append(card, label("重要程度", 16, INK, true), 14);
        optionRows(card, LEVELS, levelButtons, false);
        drawingNotes = field("图型与参数（可选）\n例如 图型：技术路线图；参数：横向布局、线宽 1 pt", 12000);
        drawingNotes.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        drawingNotes.setMinLines(2);
        drawingNotes.setMaxLines(5);
        append(card, drawingNotes, 12);

        feedback = label("", 14, ERROR, false);
        feedback.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        append(card, feedback, 8);
        confirm = button("确认并加入队列", this::save);
        style(confirm, true);
        confirm.setTextSize(16);
        confirm.setMinimumHeight(dp(52));
        append(card, confirm, 12);

        records = column();
        records.setVisibility(View.GONE);
        append(page, records, 18);

        draft.addTextChangedListener(new android.text.TextWatcher() {
            public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            public void onTextChanged(CharSequence s, int start, int before, int count) {
                duplicateRecord = null;
                timer.removeCallbacks(checkDuplicate);
                timer.postDelayed(checkDuplicate, 700);
                refreshForm();
            }
            public void afterTextChanged(android.text.Editable s) {}
        });
    }

    private void showRecords(boolean show) {
        showingRecords = show;
        captureCard.setVisibility(show ? View.GONE : View.VISIBLE);
        records.setVisibility(show ? View.VISIBLE : View.GONE);
        viewRecords.setText(show ? "返回收集" : "处理记录");
        scroll.scrollTo(0, 0);
    }

    private void optionRows(LinearLayout card, String[] values, List<Button> buttons, boolean isType) {
        for (int start = 0; start < values.length; start += 3) {
            LinearLayout choices = row();
            for (int i = start; i < Math.min(start + 3, values.length); i++) {
                String value = values[i];
                Button button = button(value, () -> {
                    if (isType) category = value;
                    else importance = value;
                    message("", false);
                    refreshForm();
                });
                buttons.add(button);
                weighted(choices, button, i == start ? 0 : 8);
            }
            append(card, choices, 6);
        }
    }

    private void refreshForm() {
        if (confirm == null) return;
        newCapture.setVisibility(parentId.isEmpty() ? View.GONE : View.VISIBLE);
        newCapture.setEnabled(!busy);
        String content = draft.getText().toString();
        String url = CollectorStore.findUrl(content, false);
        linkStatus.setVisibility(!content.trim().isEmpty() || !files.isEmpty() ? View.VISIBLE : View.GONE);
        linkStatus.setText(CollectorStore.preview(sharedTitle, content));
        attachmentStatus.setText("已选 " + files.size() + " 个附件");
        ((View) attachmentStatus.getParent()).setVisibility(files.isEmpty() ? View.GONE : View.VISIBLE);
        complete.setVisibility(!files.isEmpty() && !url.isEmpty() ? View.VISIBLE : View.GONE);
        custom.setVisibility("自定义".equals(category) ? View.VISIBLE : View.GONE);
        drawingNotes.setVisibility(category.contains("图") || "配色".equals(category) || drawingNotes.length() > 0 ? View.VISIBLE : View.GONE);
        drawingNotes.setEnabled(!busy);
        for (Button b : typeButtons) updateChoice(b, category);
        for (Button b : levelButtons) updateChoice(b, importance);
        boolean enabled = !busy && (!draft.getText().toString().trim().isEmpty() || !files.isEmpty());
        confirm.setEnabled(enabled);
        confirm.setAlpha(enabled ? 1f : .55f);
        confirm.setText(busy ? "正在保存…" : "确认并加入队列");
        if (!busy && !parentId.isEmpty()) confirm.setText("确认补充原记录");
        pair.setEnabled(!busy);
        addMedia.setEnabled(!busy);
        addDocument.setEnabled(!busy);
        paste.setEnabled(!busy);
        clearFiles.setEnabled(!busy);
    }

    private void updateChoice(Button button, String selected) {
        boolean active = selected.contentEquals(button.getText());
        style(button, active);
        button.setSelected(active);
        button.setEnabled(!busy);
        button.setContentDescription(button.getText() + (active ? "，已选" : "，未选"));
    }

    private void refreshQueue() {
        pairingState.setText(store.paired() ? "Windows 已配对" : "Windows 未配对");
        pair.setText(store.paired() ? "重新配对" : "配对电脑");
        int count = store.size();
        queueState.setText("待传输 " + count + " 条");
        sync.setEnabled(!syncing && !busy);
        sync.setVisibility(store.paired() ? View.VISIBLE : View.GONE);
        refreshRecords();
    }

    private void refreshRecords() {
        JSONArray pending = store.pendingItems();
        JSONArray recent = store.recent();
        String snapshot = pending.toString() + recent;
        if (snapshot.equals(lastRecords)) return;
        lastRecords = snapshot;
        records.removeAllViews();
        append(records, label("处理记录", 19, INK, true), 0);
        if (pending.length() == 0 && recent.length() == 0) {
            append(records, label("暂无记录", 14, MUTED, false), 8);
        }
        for (int i = 0; i < pending.length(); i++) addRecord(pending.optJSONObject(i));
        for (int i = 0; i < recent.length(); i++) addRecord(recent.optJSONObject(i));
    }

    private void addRecord(JSONObject item) {
        if (item == null) return;
        String status;
        int accent;
        int tint;
        switch (item.optString("status")) {
            case "pending": status = "↑  待传输"; accent = 0xff475569; tint = 0xfff1f5f9; break;
            case "received": status = "↓  电脑已接收"; accent = 0xff1d4ed8; tint = 0xffeff6ff; break;
            case "processing": status = "↻  处理中"; accent = 0xff6d28d9; tint = 0xfff5f3ff; break;
            case "archived": status = "✓  已入库"; accent = 0xff166534; tint = 0xfff0fdf4; break;
            case "missing": status = "!  待补资料"; accent = 0xff92400e; tint = 0xfffffbeb; break;
            default: return;
        }
        LinearLayout card = column();
        card.setPadding(dp(18), dp(16), dp(22), dp(20));
        card.setBackground(new ComicSurface(getResources().getDisplayMetrics().density, PAPER, INK));
        card.setElevation(0);
        TextView state = label(status, 12, accent, true);
        state.setPadding(dp(9), dp(5), dp(9), dp(5));
        state.setBackground(surface(tint, accent, 8));
        LinearLayout header = row();
        header.addView(state, new LinearLayout.LayoutParams(-2, -2));
        String categoryLabel = item.optString("category", "").trim();
        if (categoryLabel.isEmpty()) categoryLabel = "未分类";
        String priority = item.optString("importance", "中");
        LinearLayout metadata = row();
        TextView categoryTag = label(categoryLabel, 12, MUTED, false);
        categoryTag.setMaxLines(1);
        categoryTag.setEllipsize(TextUtils.TruncateAt.END);
        weighted(metadata, categoryTag, 0);
        categoryTag.setGravity(Gravity.END);
        TextView priorityTag = label("｜ " + priority, 12,
                "高".equals(priority) ? 0xff9d351f : MUTED, "高".equals(priority));
        metadata.addView(priorityTag);
        weighted(header, metadata, 12);
        append(card, header, 0);
        TextView name = label(displayTitle(item.optString("title")), 18, INK, true);
        name.setMaxLines(3);
        name.setEllipsize(TextUtils.TruncateAt.END);
        name.setLineSpacing(dp(3), 1);
        append(card, name, 16);
        View divider = new View(this);
        divider.setBackgroundColor(0xffe8dccc);
        LinearLayout.LayoutParams dividerParams = new LinearLayout.LayoutParams(-1, dp(1));
        dividerParams.topMargin = dp(16);
        card.addView(divider, dividerParams);
        LinearLayout footer = row();
        footer.setGravity(Gravity.BOTTOM);
        JSONArray details = item.optJSONArray("details");
        TextView summary = label(details != null && details.length() > 0 ? details.optString(0) : "", 12, MUTED, false);
        summary.setMaxLines(2);
        summary.setEllipsize(TextUtils.TruncateAt.END);
        weighted(footer, summary, 0);
        TextView more = label("详情  ›", 12, INK, true);
        more.setGravity(Gravity.END);
        more.setPadding(dp(8), 0, 0, 0);
        footer.addView(more);
        append(card, footer, 12);
        append(records, card, 16);
        card.setClickable(true);
        card.setFocusable(true);
        card.setContentDescription(categoryLabel + "｜" + priority + "｜" + name.getText() + "，" + status + "，查看详情");
        card.setOnClickListener(v -> openDetail(item));
    }

    private String displayTitle(String title) {
        java.util.regex.Matcher shared = java.util.regex.Pattern.compile("【[^】]+的作品】\\s*(.+)").matcher(title);
        if (shared.find()) title = shared.group(1);
        String cleaned = title.replaceFirst("https?://\\S+.*$", "").trim();
        return cleaned.isEmpty() ? title : cleaned;
    }

    private void checkDuplicate() {
        if (isDestroyed() || busy || !parentId.isEmpty()) return;
        String url = CollectorStore.findUrl(draft.getText().toString(), false);
        if (url.isEmpty()) return;
        io.execute(() -> {
            try {
                JSONObject match = store.duplicate(url);
                runOnUiThread(() -> {
                    if (isDestroyed() || !parentId.isEmpty() || !url.equals(CollectorStore.findUrl(draft.getText().toString(), false))) return;
                    duplicateRecord = match;
                    if (match != null) {
                        message("已收集过这条链接，点此查看或补充", false);
                        feedback.setOnClickListener(v -> openDetail(match));
                    }
                });
            } catch (Exception ignored) {
                // Offline collection remains available, and the PC reconciles aliases on ingestion.
            }
        });
    }

    private void openDetail(JSONObject item) {
        if (busy) return;
        message("正在读取详情…", false);
        io.execute(() -> {
            try {
                JSONObject record = store.detail(item);
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    message("", false);
                    TextView content = label(record.optString("body"), 15, INK, false);
                    content.setTextIsSelectable(true);
                    content.setPadding(dp(20), dp(12), dp(20), dp(16));
                    ScrollView pane = new ScrollView(this);
                    LinearLayout detailContent = column();
                    detailContent.addView(content);
                    append(detailContent, button("重新处理", () -> io.execute(() -> {
                        try {
                            store.retry(record.optString("id"));
                            runOnUiThread(() -> new AlertDialog.Builder(this).setMessage("已提交重新处理，请稍后刷新记录").setPositiveButton("知道了", null).show());
                        } catch (Exception e) {
                            runOnUiThread(() -> new AlertDialog.Builder(this).setMessage(CollectorStore.connectionError(e)).setPositiveButton("知道了", null).show());
                        }
                    })), 8);
                    pane.addView(detailContent);
                    AlertDialog dialog = new AlertDialog.Builder(this).setTitle(record.optString("title"))
                            .setView(pane).setNegativeButton("关闭", null)
                            .setPositiveButton("补充 / 修改分类", (d, w) -> supplement(record))
                            .setNeutralButton("打开原帖", (d, w) -> {
                                String url = record.optString("url");
                                if (url.startsWith("https://") || url.startsWith("http://")) {
                                    openOriginal(url);
                                }
                            }).create();
                    dialog.show();
                    dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setEnabled(!record.optString("url").isEmpty());
                });
            } catch (Exception error) {
                runOnUiThread(() -> { if (!isDestroyed()) message(CollectorStore.connectionError(error), true); });
            }
        });
    }

    private void openOriginal(String url) {
        Uri uri = Uri.parse(url);
        String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase(java.util.Locale.ROOT);
        String pkg = "";
        String deepLink = "";
        if (host.equals("xiaohongshu.com") || host.endsWith(".xiaohongshu.com") ||
                host.equals("xhslink.com") || host.endsWith(".xhslink.com") || host.equals("xhslink.cn")) {
            pkg = "com.xingin.xhs";
            java.util.regex.Matcher id = java.util.regex.Pattern.compile("/(?:explore|discovery/item)/([a-fA-F0-9]{24})(?:/|$)").matcher(uri.getPath() == null ? "" : uri.getPath());
            if (id.find()) deepLink = "xhsdiscover://item/" + id.group(1) +
                    (uri.getEncodedQuery() == null ? "" : "?" + uri.getEncodedQuery());
        } else if (host.equals("douyin.com") || host.endsWith(".douyin.com") ||
                host.equals("iesdouyin.com") || host.endsWith(".iesdouyin.com")) {
            pkg = "com.ss.android.ugc.aweme";
            java.util.regex.Matcher id = java.util.regex.Pattern.compile("/(?:video|note|share/video)/([0-9]+)(?:/|$)").matcher(uri.getPath() == null ? "" : uri.getPath());
            if (id.find()) deepLink = "snssdk1128://aweme/detail/" + id.group(1);
        }
        if (!pkg.isEmpty()) {
            if (!deepLink.isEmpty()) {
                try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(deepLink)).setPackage(pkg)); return; }
                catch (android.content.ActivityNotFoundException | SecurityException ignored) { }
            }
            try { startActivity(new Intent(Intent.ACTION_VIEW, uri).setPackage(pkg)); return; }
            catch (android.content.ActivityNotFoundException | SecurityException ignored) { }
        }
        new AlertDialog.Builder(this).setTitle("暂时无法在原生 App 中打开")
                .setMessage("请确认已安装对应 App。也可以复制原帖链接到 App 内打开。")
                .setNegativeButton("取消", null)
                .setNeutralButton("复制链接", (d, w) -> {
                    android.content.ClipboardManager clipboard = (android.content.ClipboardManager)getSystemService(CLIPBOARD_SERVICE);
                    clipboard.setPrimaryClip(android.content.ClipData.newPlainText("原帖链接", url));
                    message("原帖链接已复制", false);
                })
                .setPositiveButton("用浏览器打开", (d, w) -> {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); }
                    catch (Exception e) { message("无法打开链接", true); }
                }).show();
    }

    private void supplement(JSONObject record) {
        if (!draft.getText().toString().trim().isEmpty() || !files.isEmpty()) {
            new AlertDialog.Builder(this).setMessage("载入原记录会替换当前未提交的草稿。")
                    .setNegativeButton("保留草稿", null).setPositiveButton("载入原记录", (d, w) -> loadSupplement(record)).show();
        } else loadSupplement(record);
    }

    private void loadSupplement(JSONObject record) {
        parentId = record.optString("id");
        parentUrl = record.optString("url");
        sharedTitle = record.optString("title");
        shared = false;
        files.clear();
        complete.setChecked(false);
        drawingNotes.setText("");
        category = record.optString("category");
        if (!java.util.Arrays.asList(TYPES).contains(category)) {
            custom.setText(category);
            category = "自定义";
        }
        importance = record.optString("importance", "中");
        draft.setText(parentUrl.isEmpty() ? "补充资料" : parentUrl);
        showRecords(false);
        refreshForm();
        message("已关联原记录，可添加素材、正文，或修改分类与重要程度后确认", false);
    }

    private void leaveSupplement() {
        if (busy) return;
        String content = draft.getText().toString();
        if (!parentUrl.isEmpty() && content.startsWith(parentUrl)) {
            content = content.substring(parentUrl.length()).replaceFirst("^\\s+", "");
        } else if (parentUrl.isEmpty() && content.equals("补充资料")) {
            content = "";
        }
        parentId = parentUrl = "";
        sharedTitle = "";
        shared = false;
        complete.setChecked(false);
        duplicateRecord = null;
        draft.setText(content);
        refreshForm();
        message("已切换为新建收集，请粘贴新链接；补充文字、参数和附件已保留", false);
    }

    private void restoreType() {
        String previous = getPreferences(MODE_PRIVATE).getString("category", "");
        category = previous;
        custom.setText("");
        if (!previous.isEmpty() && !java.util.Arrays.asList(TYPES).subList(0, TYPES.length - 1).contains(previous)) {
            category = "自定义";
            custom.setText(previous);
        }
    }

    private void message(String value, boolean error) {
        feedback.setOnClickListener(null);
        feedback.setTextColor(error ? ERROR : INK);
        feedback.setText(value);
        feedback.setVisibility(value.isEmpty() ? View.GONE : View.VISIBLE);
    }

    private void loadClipboard(boolean explicit) {
        if (busy) return;
        try {
            ClipboardManager clipboard = getSystemService(ClipboardManager.class);
            ClipData clip = clipboard.getPrimaryClip();
            if (clip == null || clip.getItemCount() == 0) {
                if (explicit) message("剪贴板暂无内容", true);
                return;
            }
            android.os.PersistableBundle extras = clip.getDescription().getExtras();
            if (!explicit && extras != null && extras.getBoolean("android.content.extra.IS_SENSITIVE")) return;
            ClipData.Item item = clip.getItemAt(0);
            CharSequence raw = item.getText();
            String text = raw == null ? "" : raw.toString();
            if (raw == null && item.getUri() != null) {
                text = CollectorStore.findUrl(item.getUri().toString(), false);
            }
            if (!explicit && text.equals(lastClipboard)) return;
            if (text.trim().isEmpty() || text.length() > 90000) {
                if (explicit) message("剪贴板暂无可收集的文字，或文字过长", true);
                return;
            }
            lastClipboard = text;
            shared = false;
            parentId = parentUrl = "";
            drawingNotes.setText("");
            sharedTitle = "";
            draft.setText(text);
            message("", false);
        } catch (SecurityException denied) {
            if (explicit) message("读取剪贴板失败，请手动粘贴", true);
        }
    }

    private void importShare(Intent intent) {
        String action = intent.getAction();
        if (!Intent.ACTION_SEND.equals(action) && !Intent.ACTION_SEND_MULTIPLE.equals(action)) return;
        showRecords(false);
        parentId = parentUrl = "";
        drawingNotes.setText("");
        CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        sharedTitle = intent.getStringExtra(Intent.EXTRA_SUBJECT);
        if (sharedTitle == null) sharedTitle = "";
        files.clear();
        ArrayList<Parcelable> multiple = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
        if (multiple != null) {
            for (Parcelable entry : multiple) {
                if (entry instanceof Uri && "content".equals(((Uri) entry).getScheme())) files.add((Uri) entry);
            }
        } else {
            Parcelable single = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (single instanceof Uri && "content".equals(((Uri) single).getScheme())) files.add((Uri) single);
        }
        shared = true;
        restoreType();
        importance = "中";
        complete.setChecked(false);
        draft.setText(text == null ? sharedTitle : text);
        message("", false);
        refreshForm();
    }

    private void choosePairing() {
        Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("*/*");
        picker.addCategory(Intent.CATEGORY_OPENABLE);
        startActivityForResult(picker, PICK_PAIRING);
    }

    private void chooseDocuments() {
        Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("*/*");
        picker.addCategory(Intent.CATEGORY_OPENABLE);
        picker.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"application/pdf", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"});
        picker.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        startActivityForResult(picker, PICK_MEDIA);
    }

    private void chooseMedia() {
        Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("*/*");
        picker.addCategory(Intent.CATEGORY_OPENABLE);
        picker.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"image/*", "video/*"});
        picker.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        startActivityForResult(picker, PICK_MEDIA);
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (result != RESULT_OK || data == null) return;
        if (request == PICK_PAIRING && data.getData() != null) {
            Uri file = data.getData();
            busy = true;
            refreshForm();
            io.execute(() -> {
                try {
                    store.pair(file);
                    finishOperation("已配对", false, true);
                } catch (Exception e) {
                    finishOperation("配对失败 · " + CollectorStore.connectionError(e), true, false);
                }
            });
        } else if (request == PICK_MEDIA) {
            files.clear();
            complete.setChecked(false);
            ClipData clip = data.getClipData();
            if (clip != null) {
                for (int i = 0; i < Math.min(clip.getItemCount(), 64); i++) files.add(clip.getItemAt(i).getUri());
            } else if (data.getData() != null) {
                files.add(data.getData());
            }
            refreshForm();
        }
    }

    private void save() {
        if (duplicateRecord != null && parentId.isEmpty()) {
            openDetail(duplicateRecord);
            return;
        }
        if (!parentId.isEmpty() && !CollectorStore.findUrl(draft.getText().toString(), false).equals(parentUrl)) {
            message("补充资料请保留原帖链接；新作品请从剪贴板重新载入", true);
            return;
        }
        String type = "自定义".equals(category) ? custom.getText().toString().trim() : category;
        if (type.isEmpty()) {
            message("自定义".equals(category) ? "请输入自定义资料类型" : "请选择资料类型", true);
            return;
        }
        if (type.codePoints().anyMatch(c -> Character.isISOControl(c) || Character.getType(c) == Character.FORMAT)) {
            message("资料类型请使用单行文字", true);
            return;
        }
        String notes = drawingNotes.getText().toString().trim();
        String text = draft.getText().toString() + (notes.isEmpty() ? "" : "\n\n用户补充绘图记录\n" + notes);
        String priority = importance;
        String title = sharedTitle;
        String originalId = parentId;
        boolean fromShare = shared;
        boolean all = complete.isChecked() && complete.getVisibility() == View.VISIBLE;
        List<Uri> attachments = new ArrayList<>(files);
        busy = true;
        message("", false);
        refreshForm();
        io.execute(() -> {
            try {
                if (originalId.isEmpty()) {
                    JSONObject match = null;
                    try { match = store.duplicate(CollectorStore.findUrl(text, false)); }
                    catch (Exception ignored) { /* The saved queue can be reconciled offline. */ }
                    if (match != null) {
                        JSONObject existing = match;
                        runOnUiThread(() -> {
                            if (isDestroyed()) return;
                            busy = false;
                            duplicateRecord = existing;
                            refreshForm();
                            openDetail(existing);
                        });
                        return;
                    }
                }
                store.save(text, title, type, priority, attachments, fromShare, all, originalId);
                getPreferences(MODE_PRIVATE).edit().putString("category", type).apply();
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    draft.setText("");
                    files.clear();
                    drawingNotes.setText("");
                    sharedTitle = "";
                    shared = false;
                    parentId = parentUrl = "";
                    restoreType();
                    importance = "中";
                    complete.setChecked(false);
                    getSystemService(InputMethodManager.class).hideSoftInputFromWindow(draft.getWindowToken(), 0);
                });
                finishOperation("已加入待传输队列", false, true);
            } catch (Exception e) {
                String detail = e instanceof java.io.IOException ? e.getMessage() : "请检查链接和附件";
                finishOperation("保存失败 · " + detail, true, false);
            }
        });
    }

    private void finishOperation(String status, boolean error, boolean send) {
        if (send) store.schedule();
        runOnUiThread(() -> {
            if (isDestroyed()) return;
            busy = false;
            message(status, error);
            refreshForm();
            refreshQueue();
            if (send) sync(true);
        });
    }

    private void sync(boolean interactive) {
        timer.removeCallbacks(refreshLater);
        if (syncing) return;
        if (busy) {
            if (foreground && store.paired()) timer.postDelayed(refreshLater, 30000);
            return;
        }
        syncing = true;
        if (interactive) showConnection("正在连接电脑…");
        refreshQueue();
        io.execute(() -> {
            String status;
            try {
                status = store.sync(true);
            } catch (Exception error) {
                status = CollectorStore.connectionError(error);
            }
            store.schedule();
            String result = status;
            runOnUiThread(() -> {
                if (isDestroyed()) return;
                syncing = false;
                showConnection(result);
                refreshQueue();
                if (foreground && store.paired()) timer.postDelayed(refreshLater, 30000);
            });
        });
    }

    private void showConnection(String message) {
        int color = message.startsWith("电脑在线") || message.startsWith("已连接电脑")
                ? 0xff15803d : message.startsWith("正在") ? 0xffb45309 : 0xffb91c1c;
        GradientDrawable lamp = new GradientDrawable();
        lamp.setShape(GradientDrawable.OVAL);
        lamp.setColor(color);
        lamp.setStroke(dp(2), Color.WHITE);
        lamp.setBounds(0, 0, dp(14), dp(14));
        syncState.setCompoundDrawables(null, null, lamp, null);
        syncState.setCompoundDrawablePadding(dp(6));
        syncState.setText(message);
        syncState.setContentDescription(message);
    }
}
