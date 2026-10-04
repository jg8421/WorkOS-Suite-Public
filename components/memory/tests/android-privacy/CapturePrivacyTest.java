package com.personalmemory.app;

public final class CapturePrivacyTest {
    private static int checks;
    private static void check(boolean value, String description) {
        checks++;
        if (!value) throw new AssertionError(description);
    }

    public static void main(String[] args) {
        check(CapturePrivacy.excludedPackage("com.chinamworld.main", "CCB", "self"), "known bank");
        check(CapturePrivacy.excludedPackage("com.eg.android.AlipayGphone", "Alipay", "self"), "payment");
        check(CapturePrivacy.excludedPackage("com.x8bit.bitwarden", "Vault", "self"), "password vault");
        check(CapturePrivacy.excludedPackage("com.example", "某某银行", "self"), "label bank");
        check(CapturePrivacy.excludedPackage("com.android.systemui", "System UI", "self"), "shade");
        check(CapturePrivacy.excludedPackage("com.google.android.inputmethod.latin", "Gboard", "self"), "keyboard");
        check(CapturePrivacy.excludedPackage("self", "Memory", "self"), "own app");
        check(!CapturePrivacy.excludedPackage("com.tencent.mm", "WeChat", "self"), "normal chat preserved");
        check(CapturePrivacy.customExcluded("com.notes", " com.notes, com.example "), "custom exclusions trim");
        check(!CapturePrivacy.customExcluded("com.notes.extra", "com.notes"), "custom exact package");
        check(CapturePrivacy.sensitivePage("Incognito tab"), "incognito page");
        check(CapturePrivacy.sensitivePage("无痕浏览"), "Chinese incognito");
        check(CapturePrivacy.sensitivePage("Sign in to continue"), "sign in page");
        check(CapturePrivacy.sensitivePage("Checkout"), "checkout page");
        check(CapturePrivacy.sensitivePage("验证码 839201"), "OTP page");
        check(!CapturePrivacy.sensitivePage("Read a research report"), "normal page");
        check(CapturePrivacy.authenticationText("Verification code 31AB9C"), "alphanumeric OTP");
        check(CapturePrivacy.authenticationText("Your code is 987654"), "plain OTP notification");
        check(CapturePrivacy.authenticationText("654321，请勿告知他人"), "unlabelled warning OTP");
        check(!CapturePrivacy.redact("您的验证码为 839201", 1000).contains("839201"), "Chinese OTP redacted");
        check(!CapturePrivacy.redact("password=abcdef123").contains("abcdef123"), "password redacted");
        check(!CapturePrivacy.redact("api_key=abc123XYZsecret").contains("abc123XYZsecret"), "API assignment redacted");
        check(!CapturePrivacy.redact("token=abc123XYZsecret&mode=normal").contains("abc123XYZsecret"), "token query redacted");
        check(!CapturePrivacy.redact("Bearer abcXYZ123+-==").contains("abcXYZ123"), "bearer redacted");
        check(!CapturePrivacy.redact("sk-abcdefghijklmnop").contains("abcdefghijklmnop"), "API key redacted");
        check(!CapturePrivacy.redact("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.HMACvalue123").contains("HMACvalue123"), "JWT redacted");
        check(!CapturePrivacy.redact("Card 4532 1234 5678 9012").contains("4532"), "long card-like digits redacted");
        check(CapturePrivacy.redact("Meet at 14:30 on Tuesday").equals("Meet at 14:30 on Tuesday"), "normal notification preserved");
        check(!CapturePrivacy.redactPair("Verification code", "123456")[1].contains("123456"), "legacy split OTP body");
        check(!CapturePrivacy.redactPair("654321", "Your login code")[0].contains("654321"), "legacy split OTP title");
        check(!CapturePrivacy.redactPair("Verification", "code 987654")[1].contains("987654"), "phrase across fields");
        check(CapturePrivacy.redactPair("Team", "Meet at 14:30")[1].equals("Meet at 14:30"), "normal pair preserved");
        check(!CapturePrivacy.redactPair("api_key=secretKey123", "Hello")[0].contains("secretKey123"), "pair keeps token redaction");
        check(!CapturePrivacy.redactPair("a".repeat(40000), "123456")[1].contains("123456"), "oversized pair fails closed");
        check(CapturePrivacy.redact("a".repeat(40000)).equals("[Oversized content omitted]"), "oversized dropped");
        check(CapturePrivacy.digest("same").equals(CapturePrivacy.digest("same")), "stable digest");
        check(!CapturePrivacy.digest("same").equals(CapturePrivacy.digest("different")), "distinct digest");
        CapturePrivacy.RateLimiter limits = new CapturePrivacy.RateLimiter();
        check(limits.allow("content-app", "one", 0, 15000), "first allowed");
        check(!limits.allow("content-app", "changing-text", 5000, 15000), "changing text cannot bypass interval");
        check(!limits.allow("other-bucket", "other", 1000, 0), "global budget across buckets");
        check(limits.allow("content-app", "changing-text", 15000, 15000), "interval elapsed");
        check(!limits.allow("other-bucket", "changing-text", 20000, 0), "dedup across buckets");
        CapturePrivacy.RateLimiter minute = new CapturePrivacy.RateLimiter();
        for (int i = 0; i < 20; i++) check(minute.allow("b" + i, "s" + i, i * 1500, 0), "minute allowance " + i);
        check(!minute.allow("b21", "s21", 40000, 0), "20 per minute cap");
        check(minute.allow("b21", "s21", 60000, 0), "minute budget recovers");
        System.out.println("CapturePrivacy: " + checks + " checks passed");
    }
}
