package com.personalmemory.app;

/** Runs with plain javac/java; no Android runtime or user data involved. */
public final class CapturePrivacyChecks {
    private static int checks;
    private static void check(boolean condition, String label) {
        checks++;
        if (!condition) throw new AssertionError(label);
    }
    private static void excludes(String pkg, String label) {
        check(CapturePrivacy.excludedPackage(pkg, label, "com.personalmemory.app"), "Excluded: " + pkg);
    }
    public static void main(String[] args) {
        excludes("com.personalmemory.app", "Personal Memory");
        excludes("com.android.systemui", "System UI");
        excludes("com.x8bit.bitwarden", "Bitwarden");
        excludes("com.eg.android.AlipayGphone", "Alipay");
        excludes("com.android.inputmethod.latin", "Keyboard");
        excludes("example.unknown.financial", "示例银行");
        check(!CapturePrivacy.excludedPackage("com.personalmemory.smokefixture", "Memory Capture Test", "com.personalmemory.app"), "Neutral fixture allowed");
        check(CapturePrivacy.customExcluded("example.app", " other.app, example.app "), "Custom list trimmed");
        check(!CapturePrivacy.customExcluded("example.app.child", "example.app"), "Custom list exact matching");

        for (String value : new String[]{"Your verification code is 472951", "验证码 472951", "Recovery code", "Password", "API 私钥"}) {
            check(CapturePrivacy.authenticationText(value), "Authentication text recognized");
            String redacted = CapturePrivacy.redact(value, 200);
            check(!redacted.contains("472951") && !redacted.equals(value), "Authentication text omitted");
        }
        for (String value : new String[]{"Incognito tab", "InPrivate", "无痕浏览", "Sign in", "付款", "Card number"}) {
            check(CapturePrivacy.sensitivePage(value), "Sensitive page recognized");
        }
        check(!CapturePrivacy.sensitivePage("Synthetic local content only"), "Neutral page allowed");
        for (String value : new String[]{"token=abcdefghijklmnop", "Bearer aaaa.bbbb.cccc", "sk-abcdefghijklmnopqrstuv", "Card 4111 1111 1111 1111"}) {
            check(!CapturePrivacy.redact(value, 200).equals(value), "Secret marker redacted");
        }
        check(CapturePrivacy.redact("alpha  beta\n gamma", 200).equals("alpha beta gamma"), "Whitespace normalized");
        check(CapturePrivacy.redact("abcdefgh", 4).equals("abcd"), "Length limited");
        check(CapturePrivacy.redact(null, 200).isEmpty(), "Null safe");
        check(CapturePrivacy.digest("fixed").equals(CapturePrivacy.digest("fixed")), "Digest stable");
        check(!CapturePrivacy.digest("fixed").equals(CapturePrivacy.digest("different")), "Digest separates content");

        CapturePrivacy.RateLimiter changing = new CapturePrivacy.RateLimiter();
        check(changing.allow("app:content", "v1", 0, 10000), "First capture allowed");
        check(!changing.allow("app:content", "v2", 2000, 10000), "Changed content cannot bypass bucket throttle");
        check(changing.allow("app:content", "v2", 10000, 10000), "Bucket reopens after interval");
        check(!changing.allow("other:click", "v3", 10500, 1000), "Global throttle crosses buckets");
        check(!changing.allow("app:content", "v2", 20000, 10000), "Duplicate suppression crosses bucket interval");
        check(changing.allow("app:content", "v2", 310000, 10000), "Duplicate eventually expires");

        CapturePrivacy.RateLimiter budget = new CapturePrivacy.RateLimiter();
        for (int index = 0; index < 20; index++) {
            check(budget.allow("bucket" + index, "sig" + index, index * 1500L, 1), "Within minute budget");
        }
        check(!budget.allow("overflow", "overflow", 30000, 1), "Minute budget blocks burst");
        check(budget.allow("reopened", "reopened", 60000, 1), "Rolling window reopens");
        System.out.println("CapturePrivacy checks passed: " + checks);
    }
}
