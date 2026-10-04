package com.personalmemory.app;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Local, conservative filters. These heuristics cannot recognize every sensitive app or page. */
final class CapturePrivacy {
    private static final Set<String> BLOCKED = new HashSet<>(Arrays.asList(
        "android", "com.android.systemui", "com.android.settings", "com.miui.securitycenter",
        "com.google.android.permissioncontroller", "com.android.permissioncontroller",
        "com.google.android.apps.authenticator2", "com.azure.authenticator", "com.microsoft.authenticator",
        "com.lastpass.lpandroid", "com.onepassword.android", "com.onepassword.android.beta",
        "com.bitwarden", "com.x8bit.bitwarden", "com.dashlane", "com.agilebits.onepassword",
        "keepass2android.keepass2android", "keepass2android.keepass2android_nonet", "com.kunzisoft.keepass.free",
        "com.eg.android.AlipayGphone", "com.paypal.android.p2pmobile", "com.google.android.apps.walletnfcrel",
        "com.unionpay", "com.chinamworld.main", "com.chinamworld.bocmbci", "com.android.bankabc",
        "cmb.pb", "cmb.pb.cmbtouch", "com.icbc", "com.icbc.android", "com.icbc.mobilebank",
        "cn.com.cmbc.newmbank", "com.spdb.mobilebank.per", "com.bankcomm.Bankcomm", "com.pingan.paces.ccms"
    ));
    private static final String[] BLOCKED_HINTS = {
        "bank", "wallet", "alipay", "paypal", "unionpay", "authenticator", "1password",
        "onepassword", "bitwarden", "lastpass", "dashlane", "keepass", "passwordmanager",
        "keychain", "keystore", "authy", "aegis", "支付", "银行", "钱包", "密码管理", "令牌", "身份验证"
    };
    private static final String[] KEYBOARD_HINTS = {
        "inputmethod", ".ime", "keyboard", "sogou", "baidu.input", "iflytek", "swiftkey"
    };
    private static final Pattern AUTH = Pattern.compile(
        "(?iu)(password|passwd|passphrase|\\bpwd\\b|\\botp\\b|one[ -]?time|verification[ -]?code|"
        + "security[ -]?code|authentication[ -]?code|login[ -]?code|sign[ -]?in[ -]?code|your[ -]?code|code\\s+is|"
        + "access[ -]?code|confirmation[ -]?code|validation[ -]?code|do[ -]?not[ -]?share|\\b2fa\\b|\\bmfa\\b|"
        + "验证码|校验码|动态码|确认码|安全码|登录码|授权码|短信码|勿告知|勿泄露|一次性|口令|密码|恢复码|助记词|私钥|密钥|"
        + "种子短语|recovery[ -]?code|seed[ -]?phrase|private[ -]?key)");
    private static final Pattern PRIVATE_PAGE = Pattern.compile(
        "(?iu)(incognito|inprivate|private[ -]?browsing|private[ -]?tab|private[ -]?mode|无痕|隐身模式|隐私浏览|隐私模式)");
    private static final Pattern SENSITIVE_PAGE = Pattern.compile(
        "(?iu)(\\blog[ -]?in\\b|\\bsign[ -]?in\\b|\\bcheckout\\b|\\bcheck[ -]?out\\b|"
        + "\\bpayment\\b|pay[ -]?now|card[ -]?number|credit[ -]?card|\\bcvv\\b|\\bcvc\\b|\\bpin\\b|"
        + "登录|登入|付款|支付|银行卡|信用卡|收款码|转账|账单|结算)");
    private static final Pattern SECRET_ASSIGNMENT = Pattern.compile(
        "(?iu)((?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|client[ _-]?secret|secret|token|password|passwd|pwd|"
        + "authorization|密钥|密码)\\s*[\"']?\\s*[:=：]\\s*[\"']?)([^\\s\"'&,;|}]{3,})");
    private static final Pattern TOKEN = Pattern.compile(
        "(?i)(\\bBearer\\s+[A-Za-z0-9._~+/=-]+|\\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|"
        + "github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\\b|\\beyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\b)");
    private static final Pattern LONG_NUMBER = Pattern.compile("(?<!\\d)(?:\\d[ -]?){11,18}\\d(?!\\d)");

    private CapturePrivacy() {}

    static boolean excludedPackage(String packageName, String appLabel, String selfPackage) {
        if (packageName == null || packageName.isEmpty() || packageName.equals(selfPackage)) return true;
        if (BLOCKED.contains(packageName)) return true;
        String value = (packageName + " " + (appLabel == null ? "" : appLabel)).toLowerCase(Locale.ROOT);
        for (String hint : BLOCKED_HINTS) if (value.contains(hint)) return true;
        for (String hint : KEYBOARD_HINTS) if (value.contains(hint)) return true;
        return false;
    }

    static boolean customExcluded(String packageName, String commaSeparated) {
        if (commaSeparated == null) return false;
        for (String item : commaSeparated.split(",")) if (packageName.equals(item.trim())) return true;
        return false;
    }

    static boolean sensitivePage(CharSequence value) {
        if (value == null) return false;
        return AUTH.matcher(value).find() || PRIVATE_PAGE.matcher(value).find() || SENSITIVE_PAGE.matcher(value).find();
    }

    static boolean authenticationText(CharSequence value) {
        return value != null && AUTH.matcher(value).find();
    }

    static String redact(String value) { return redact(value, 32768); }

    /** Protect split notifications: an authentication label may be in the title, its value in the body. */
    static String[] redactPair(String title, String text) {
        if ((title != null && title.length() > 32768) || (text != null && text.length() > 32768)) {
            return new String[] {"[Oversized content omitted]", "[Oversized content omitted]"};
        }
        String boundedTitle = title == null ? "" : title;
        String boundedText = text == null ? "" : text;
        if (authenticationText(boundedTitle + " " + boundedText)) {
            return new String[] {"[Authentication or secret content omitted]", "[Authentication or secret content omitted]"};
        }
        return new String[] {redact(boundedTitle), redact(boundedText)};
    }

    static String redact(String value, int maxChars) {
        if (value == null || maxChars <= 0) return "";
        if (value.length() > 32768) return "[Oversized content omitted]";
        if (authenticationText(value)) return "[Authentication or secret content omitted]";
        String cleaned = TOKEN.matcher(value).replaceAll("[REDACTED]");
        cleaned = SECRET_ASSIGNMENT.matcher(cleaned).replaceAll("$1[REDACTED]");
        cleaned = LONG_NUMBER.matcher(cleaned).replaceAll("[REDACTED_NUMBER]");
        cleaned = cleaned.replaceAll("\\s+", " ").trim();
        return cleaned.length() > maxChars ? cleaned.substring(0, maxChars) : cleaned;
    }

    static String digest(String value) {
        try {
            byte[] bytes = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder();
            for (byte item : bytes) result.append(String.format(Locale.ROOT, "%02x", item & 255));
            return result.toString();
        } catch (Exception impossible) { throw new IllegalStateException("SHA-256 unavailable", impossible); }
    }

    /** Changing text cannot evade the independent time and minute budgets. */
    static final class RateLimiter {
        private final Map<String, Long> buckets = new LinkedHashMap<>();
        private final Map<String, Long> signatures = new LinkedHashMap<>();
        private final ArrayDeque<Long> recent = new ArrayDeque<>();
        private long lastGlobal = -1;

        boolean allow(String bucket, String signature, long now, long minimumInterval) {
            while (!recent.isEmpty() && now - recent.peekFirst() >= 60000) recent.removeFirst();
            if (lastGlobal >= 0 && now - lastGlobal < 1200) return false;
            if (recent.size() >= 20) return false;
            Long previousBucket = buckets.get(bucket);
            if (previousBucket != null && now - previousBucket < minimumInterval) return false;
            Long previousSignature = signatures.get(signature);
            if (previousSignature != null && now - previousSignature < 300000) return false;
            lastGlobal = now;
            recent.addLast(now);
            putBounded(buckets, bucket, now, 128);
            putBounded(signatures, signature, now, 256);
            return true;
        }

        private static void putBounded(Map<String, Long> map, String key, long now, int limit) {
            map.remove(key);
            map.put(key, now);
            while (map.size() > limit) {
                Iterator<String> iterator = map.keySet().iterator();
                iterator.next();
                iterator.remove();
            }
        }
    }
}
