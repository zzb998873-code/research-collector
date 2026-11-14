package cn.researchwiki.collector;

import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.ContentResolver;
import android.content.Context;
import android.database.Cursor;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.provider.OpenableColumns;
import android.webkit.MimeTypeMap;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.RandomAccessFile;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.cert.Certificate;
import java.security.cert.CertificateFactory;
import java.time.Instant;
import java.time.ZoneId;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManagerFactory;
import org.json.JSONArray;
import org.json.JSONObject;

final class CollectorStore {
    private static final int CHUNK = 1024 * 1024;
    private static final int JOB = 804;
    private static final Object SYNC_LOCK = new Object();
    private static final Pattern URLS = Pattern.compile("https?://[^\\s<>\"，。！、；（）【】]+", Pattern.CASE_INSENSITIVE);
    private static final Pattern PLATFORM = Pattern.compile("(?:[a-z0-9-]+\\.)*(?:xiaohongshu\\.com|xhslink\\.(?:cn|com)|rednote\\.com|douyin\\.com|iesdouyin\\.com)");
    private final Context context;
    private final File root;
    private final File queue;
    private final File pairing;
    private final File recent;

    CollectorStore(Context context) {
        this.context = context.getApplicationContext();
        root = this.context.getFilesDir();
        queue = new File(root, "captures");
        pairing = new File(root, "pairing.json");
        recent = new File(root, "recent.json");
    }

    static String preview(String title, String text) {
        if (!title.trim().isEmpty()) return title.trim();
        for (String line : text.split("\\R")) {
            String candidate = URLS.matcher(line).replaceAll("").trim();
            if (!candidate.isEmpty()) return candidate;
        }
        String url = findUrl(text, false);
        return url.isEmpty() ? "图片或视频" : url;
    }

    static String findUrl(String text, boolean platformsOnly) {
        String first = "";
        Matcher matcher = URLS.matcher(text);
        while (matcher.find()) {
            String value = matcher.group().replaceFirst("[).,;]+$", "");
            Uri uri = Uri.parse(value);
            String host = uri.getHost();
            if (host == null || uri.getUserInfo() != null || value.length() > 4000) {
                continue;
            }
            if (PLATFORM.matcher(host.toLowerCase(Locale.ROOT)).matches()) {
                return value;
            }
            if (first.isEmpty()) {
                first = value;
            }
        }
        return platformsOnly ? "" : first;
    }

    private static byte[] readLimited(InputStream stream, int limit) throws IOException {
        if (stream == null) {
            throw new IOException("无法读取文件");
        }
        try (InputStream input = stream; ByteArrayOutputStream result = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) != -1) {
                if (result.size() + read > limit) {
                    throw new IOException("文件超过大小上限");
                }
                result.write(buffer, 0, read);
            }
            return result.toByteArray();
        }
    }

    private static JSONObject json(File file) throws Exception {
        return new JSONObject(new String(readLimited(new java.io.FileInputStream(file), CHUNK), StandardCharsets.UTF_8));
    }

    private static void write(File destination, String value) throws IOException {
        File temp = new File(destination.getParentFile(), destination.getName() + "." + UUID.randomUUID());
        try {
            try (FileOutputStream stream = new FileOutputStream(temp)) {
                stream.write(value.getBytes(StandardCharsets.UTF_8));
                stream.getFD().sync();
            }
            if (!temp.renameTo(destination)) {
                throw new IOException("暂存文件写入失败");
            }
        } finally {
            temp.delete();
        }
    }

    boolean paired() {
        return pairing.isFile();
    }

    void pair(Uri uri) throws Exception {
        byte[] bytes = readLimited(context.getContentResolver().openInputStream(uri), 12000);
        installPairing(new String(bytes, StandardCharsets.UTF_8));
    }

    private void installPairing(String value) throws Exception {
        JSONObject config = new JSONObject(value);
        if (config.getInt("version") != 1 || !config.getString("url").matches("https://(?:[0-9]{1,3}\\.){3}[0-9]{1,3}:[0-9]{4,5}")
                || !config.getString("token").matches("[A-Za-z0-9_-]{40,60}")) {
            throw new IOException("请选择电脑生成的配对文件");
        }
        if (!wifi()) throw new IOException("手机未连接 Wi-Fi，请先连接与电脑同一局域网的 Wi-Fi");
        if (new Receiver(config).request("GET", "/v1/health", null, -1).optInt("version") != 1) {
            throw new IOException("电脑接收端版本不匹配");
        }
        synchronized (SYNC_LOCK) {
            if (recent.exists() && !recent.delete()) throw new IOException("无法清除旧电脑的处理记录");
            write(pairing, config.toString());
        }
    }

    private boolean wifi() {
        ConnectivityManager manager = context.getSystemService(ConnectivityManager.class);
        NetworkCapabilities network = manager.getNetworkCapabilities(manager.getActiveNetwork());
        return network != null && network.hasTransport(NetworkCapabilities.TRANSPORT_WIFI);
    }

    private File[] pending() {
        File[] files = queue.listFiles(file -> new File(file, "manifest.json").isFile());
        if (files == null) {
            return new File[0];
        }
        Arrays.sort(files, Comparator.comparingLong(File::lastModified).thenComparing(File::getName));
        return files;
    }

    int size() {
        return pending().length;
    }

    JSONArray recent() {
        try {
            return json(recent).getJSONArray("items");
        } catch (Exception ignored) {
            return new JSONArray();
        }
    }

    JSONArray pendingItems() {
        JSONArray items = new JSONArray();
        for (File folder : pending()) {
            if (items.length() == 30) break;
            try {
                JSONObject item = json(new File(folder, "manifest.json"));
                String title = preview(item.optString("title"), item.optString("text"));
                String date = Instant.parse(item.getString("captured_at"))
                        .atZone(ZoneId.of("Asia/Shanghai")).toLocalDate().toString();
                items.put(new JSONObject().put("id", item.getString("id")).put("url", item.optString("url"))
                        .put("category", item.optString("category")).put("importance", item.optString("importance", "中"))
                        .put("title", title.substring(0, Math.min(160, title.length())))
                        .put("date", date).put("status", "pending"));
            } catch (Exception ignored) {
                // A background upload may have just removed the manifest.
            }
        }
        return items;
    }

    void save(String text, String title, String category, String importance, List<Uri> media,
              boolean shared, boolean complete, String parentId) throws Exception {
        String id = UUID.randomUUID().toString();
        String url = findUrl(text, false);
        if (text.length() > 90000 || title.length() > 500 || media.size() > 64) {
            throw new IOException("内容或附件数量超过收集上限");
        }
        if (complete && (url.isEmpty() || text.replace(url, "").trim().isEmpty() || media.isEmpty())) {
            throw new IOException("请同时提供原帖链接、完整正文和附件");
        }
        File folder = new File(queue, id);
        if (!folder.mkdirs()) {
            throw new IOException("无法建立暂存目录");
        }
        try {
            JSONArray files = new JSONArray();
            long remaining = 768L * CHUNK;
            for (Uri uri : media) {
                JSONObject file = copyMedia(uri, folder, remaining);
                remaining -= file.getLong("size");
                files.put(file);
            }
            JSONObject manifest = new JSONObject();
            manifest.put("version", 1).put("id", id).put("captured_at", Instant.now().toString())
                    .put("trigger", !parentId.isEmpty() || !url.isEmpty() && !media.isEmpty() ? "supplement" : shared ? "share" : "paste")
                    .put("url", url).put("title", title).put("text", text).put("category", category)
                    .put("importance", importance).put("attachments", files).put("complete", complete);
            if (!parentId.isEmpty()) manifest.put("parent_id", parentId);
            write(new File(folder, "manifest.json"), manifest.toString());
        } catch (Exception error) {
            removeFolder(folder);
            throw error;
        }
    }

    JSONObject detail(JSONObject item) throws Exception {
        String id = item.getString("id");
        if (!id.matches("[a-f0-9-]{36}")) throw new IOException("记录编号无效");
        File queued = new File(queue, id + "/manifest.json");
        if (queued.exists()) {
            JSONObject manifest = json(queued);
            return new JSONObject(item.toString()).put("body", "待传输\n\n" + manifest.optString("text"))
                    .put("url", manifest.optString("url"));
        }
        File cache = new File(root, "detail-" + id + ".json");
        try {
            if (!paired() || !wifi()) throw new IOException("请连接电脑以读取完整详情");
            JSONObject record = new Receiver(json(pairing)).request("GET", "/v1/captures/" + id + "/detail", null, -1).getJSONObject("record");
            write(cache, record.toString());
            return record;
        } catch (Exception error) {
            if (cache.isFile()) return json(cache).put("body", "离线缓存 · 连接电脑后刷新\n\n" + json(cache).optString("body"));
            throw error;
        }
    }

    JSONObject duplicate(String url) throws Exception {
        if (url.isEmpty()) return null;
        for (JSONArray items : new JSONArray[]{pendingItems(), recent()}) {
            for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.getJSONObject(i);
                if (url.equals(item.optString("url"))) return item;
            }
        }
        if (!paired() || !wifi()) return null;
        JSONObject health = new Receiver(json(pairing)).request("GET", "/v1/health", null, -1);
        if (!health.optBoolean("record_details")) return null;
        return new Receiver(json(pairing)).request("POST", "/v1/lookup",
                new JSONObject().put("url", url).toString().getBytes(StandardCharsets.UTF_8), -1).optJSONObject("record");
    }

    void retry(String id) throws Exception {
        if (!id.matches("[a-f0-9-]{36}")) throw new IOException("记录编号无效");
        if (!paired() || !wifi()) throw new IOException("请连接电脑后重试处理");
        new Receiver(json(pairing)).request("POST", "/v1/captures/" + id + "/retry", new byte[]{'{', '}'}, -1);
    }

    private JSONObject copyMedia(Uri uri, File folder, long remaining) throws Exception {
        ContentResolver resolver = context.getContentResolver();
        String name = "";
        try (Cursor cursor = resolver.query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                name = cursor.getString(0);
            }
        }
        String ext = MimeTypeMap.getFileExtensionFromUrl(name == null ? "" : name.replace(" ", "%20"))
                .toLowerCase(Locale.ROOT);
        if (ext.isEmpty()) {
            String inferred = MimeTypeMap.getSingleton().getExtensionFromMimeType(resolver.getType(uri));
            ext = inferred == null ? "" : inferred;
        }
        boolean image = ext.matches("jpg|jpeg|png|webp|gif|heic|heif|bmp");
        if (!image && !ext.matches("mp4|mov|webm|m4v")) {
            throw new IOException("附件类型暂不支持");
        }
        String id = UUID.randomUUID().toString();
        long size = 0;
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = resolver.openInputStream(uri);
             FileOutputStream output = new FileOutputStream(new File(folder, id + ".media"))) {
            if (input == null) {
                throw new IOException("无法打开附件");
            }
            byte[] buffer = new byte[CHUNK];
            int count;
            while ((count = input.read(buffer)) != -1) {
                size += count;
                if (size > Math.min(512L * CHUNK, remaining)) {
                    throw new IOException("附件超过大小上限");
                }
                output.write(buffer, 0, count);
                digest.update(buffer, 0, count);
            }
            output.getFD().sync();
        }
        if (size == 0) {
            throw new IOException("附件为空");
        }
        StringBuilder hash = new StringBuilder();
        for (byte value : digest.digest()) {
            hash.append(String.format(Locale.ROOT, "%02x", value & 0xff));
        }
        if (name == null || !name.toLowerCase(Locale.ROOT).endsWith("." + ext)) {
            name = "附件." + ext;
        }
        name = name.replaceAll("[/\\\\:]", "-");
        if (name.length() > 150) {
            name = name.substring(name.length() - 150);
        }
        return new JSONObject().put("id", id).put("name", name).put("kind", image ? "image" : "video")
                .put("sha256", hash.toString()).put("size", size);
    }

    private static void removeFolder(File folder) {
        File[] files = folder.listFiles();
        if (files != null) {
            for (File file : files) {
                file.delete();
            }
        }
        folder.delete();
    }

    String sync(boolean refreshRecent) throws Exception {
        synchronized (SYNC_LOCK) {
            if (!paired()) return "等待与电脑配对";
            if (!wifi()) throw new IOException("手机未连接 Wi-Fi，请连接与电脑同一局域网的 Wi-Fi");
            Receiver receiver = new Receiver(json(pairing));
            JSONObject health = receiver.request("GET", "/v1/health", null, -1);
            if (health.optInt("version") != 1) throw new IOException("电脑接收端版本不匹配");
            int sent = 0;
            for (File folder : pending()) {
                if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
                transfer(receiver, folder);
                sent++;
            }
            if (refreshRecent && health.optBoolean("recent_captures")) {
                JSONObject result = receiver.request("GET", "/v1/captures/recent", null, -1);
                result.getJSONArray("items");
                write(recent, result.toString());
            } else if (refreshRecent) {
                return "已连接电脑，请更新电脑接收端以查看入库状态";
            }
            return sent > 0 ? "电脑在线，已接收 " + sent + " 条" : "电脑在线";
        }
    }

    static String connectionError(Exception error) {
        if (error instanceof javax.net.ssl.SSLException || error instanceof java.security.GeneralSecurityException) {
            return "电脑证书校验失败，请使用电脑最新的配对文件重新配对";
        }
        if (error instanceof java.net.ConnectException) {
            return "连接被拒绝，请在电脑上启动科研收集接收端";
        }
        if (error instanceof java.net.SocketTimeoutException || error instanceof java.net.NoRouteToHostException) {
            return "连接电脑超时，请确认电脑已开机并与手机处于同一局域网";
        }
        return error instanceof IOException && error.getMessage() != null && !(error instanceof java.net.SocketException)
                ? error.getMessage() : "连接中断，请检查电脑和 Wi-Fi 后重试";
    }

    private void transfer(Receiver receiver, File folder) throws Exception {
        JSONObject manifest = json(new File(folder, "manifest.json"));
        String id = manifest.getString("id");
        String base = "/v1/captures/" + id;
        JSONObject status = receiver.request("POST", "/v1/captures", manifest.toString().getBytes(StandardCharsets.UTF_8), -1);
        if (!status.optBoolean("committed")) {
            JSONObject offsets = status.getJSONObject("offsets");
            JSONArray files = manifest.getJSONArray("attachments");
            for (int index = 0; index < files.length(); index++) {
                JSONObject file = files.getJSONObject(index);
                String fileId = file.getString("id");
                long size = file.getLong("size");
                long offset = offsets.optLong(fileId);
                try (RandomAccessFile input = new RandomAccessFile(new File(folder, fileId + ".media"), "r")) {
                    if (offset < 0 || offset > size || input.length() != size) {
                        throw new IOException("附件传输进度无效");
                    }
                    while (offset < size) {
                        if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
                        byte[] bytes = new byte[(int) Math.min(CHUNK, size - offset)];
                        input.seek(offset);
                        input.readFully(bytes);
                        JSONObject reply = receiver.request("PUT", base + "/files/" + fileId, bytes, offset);
                        long next = reply.optLong("offset", -1);
                        if (next <= offset || next > size) throw new IOException("电脑未确认传输进度");
                        offset = next;
                    }
                }
            }
            JSONObject reply = receiver.request("POST", base + "/commit", new byte[]{'{', '}'}, -1);
            if (!reply.optBoolean("committed") || !id.equals(reply.optString("id"))) {
                throw new IOException("电脑未确认接收");
            }
        }
        removeFolder(folder);
    }

    void schedule() {
        if (!paired() || size() == 0) {
            return;
        }
        JobScheduler scheduler = context.getSystemService(JobScheduler.class);
        if (scheduler.getPendingJob(JOB) == null) {
            scheduler.schedule(new JobInfo.Builder(JOB, new ComponentName(context, SyncJob.class))
                    .setRequiredNetworkType(JobInfo.NETWORK_TYPE_UNMETERED).setPersisted(true)
                    .setBackoffCriteria(30000, JobInfo.BACKOFF_POLICY_EXPONENTIAL).build());
        }
    }

    private static final class Receiver {
        final URL base;
        final String token;
        final SSLContext tls;

        Receiver(JSONObject config) throws Exception {
            base = new URL(config.getString("url"));
            token = config.getString("token");
            Certificate certificate = CertificateFactory.getInstance("X.509").generateCertificate(
                    new ByteArrayInputStream(config.getString("ca").getBytes(StandardCharsets.US_ASCII)));
            KeyStore trust = KeyStore.getInstance(KeyStore.getDefaultType());
            trust.load(null, null);
            trust.setCertificateEntry("collector", certificate);
            TrustManagerFactory factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
            factory.init(trust);
            tls = SSLContext.getInstance("TLS");
            tls.init(null, factory.getTrustManagers(), null);
        }

        JSONObject request(String method, String path, byte[] body, long offset) throws Exception {
            HttpsURLConnection conn = (HttpsURLConnection) new URL(base, path).openConnection(java.net.Proxy.NO_PROXY);
            conn.setSSLSocketFactory(tls.getSocketFactory());
            conn.setInstanceFollowRedirects(false);
            conn.setConnectTimeout(4500);
            conn.setReadTimeout(15000);
            conn.setRequestMethod(method);
            conn.setRequestProperty("Authorization", "Bearer " + token);
            if (body != null) {
                conn.setDoOutput(true);
                conn.setFixedLengthStreamingMode(body.length);
                conn.setRequestProperty("Content-Type", offset < 0 ? "application/json" : "application/octet-stream");
                if (offset >= 0) conn.setRequestProperty("Upload-Offset", Long.toString(offset));
            }
            try {
                if (body != null) {
                    try (java.io.OutputStream output = conn.getOutputStream()) {
                        output.write(body);
                    }
                }
                int code = conn.getResponseCode();
                if (code == 401) throw new IOException("配对已失效，请使用电脑最新的配对文件重新配对");
                if (code != 200) {
                    throw new IOException("电脑端请求未完成（" + code + "），请稍后重试");
                }
                return new JSONObject(new String(readLimited(conn.getInputStream(), CHUNK), StandardCharsets.UTF_8));
            } finally {
                conn.disconnect();
            }
        }
    }
}
