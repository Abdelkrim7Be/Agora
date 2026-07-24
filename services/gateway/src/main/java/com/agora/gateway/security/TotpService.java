package com.agora.gateway.security;

import org.apache.commons.codec.binary.Base32;
import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.ByteBuffer;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * RFC 6238 TOTP (30s step, 6 digits, HMAC-SHA1) — the same algorithm every
 * authenticator app (Google Authenticator, Authy, 1Password...) implements,
 * hand-rolled on plain JDK crypto rather than pulling in a third-party TOTP
 * library for a ~40-line, precisely-specified, independently testable algorithm.
 */
@Service
public class TotpService {

    private static final int TIME_STEP_SECONDS = 30;
    private static final int CODE_DIGITS = 6;
    private static final int ALLOWED_DRIFT_STEPS = 1; // accepts the previous/next 30s window too
    private static final String HMAC_ALGO = "HmacSHA1";
    private static final Base32 BASE32 = new Base32();

    private final SecureRandom secureRandom = new SecureRandom();

    public String generateSecret() {
        byte[] bytes = new byte[20]; // 160 bits, RFC 4226's recommended HMAC-SHA1 key size
        secureRandom.nextBytes(bytes);
        return BASE32.encodeToString(bytes).replace("=", "");
    }

    public List<String> generateRecoveryCodes(int count) {
        List<String> codes = new ArrayList<>(count);
        for (int i = 0; i < count; i++) {
            codes.add(generateRecoveryCode());
        }
        return codes;
    }

    public String otpAuthUri(String issuer, String accountName, String secret) {
        return "otpauth://totp/%s:%s?secret=%s&issuer=%s&algorithm=SHA1&digits=%d&period=%d".formatted(
                urlEncode(issuer), urlEncode(accountName), secret, urlEncode(issuer), CODE_DIGITS, TIME_STEP_SECONDS
        );
    }

    /** The code a real authenticator app would show right now — exposed for tests
     * and for a future "verify enrollment" UI hint; production auth never calls
     * this itself, only verify(). */
    public String currentCode(String base32Secret) {
        long currentStep = System.currentTimeMillis() / 1000L / TIME_STEP_SECONDS;
        return generateCode(base32Secret, currentStep);
    }

    public boolean verify(String base32Secret, String code) {
        if (base32Secret == null || base32Secret.isBlank() || code == null || !code.matches("\\d{6}")) {
            return false;
        }
        long currentStep = System.currentTimeMillis() / 1000L / TIME_STEP_SECONDS;
        for (long drift = -ALLOWED_DRIFT_STEPS; drift <= ALLOWED_DRIFT_STEPS; drift++) {
            if (code.equals(generateCode(base32Secret, currentStep + drift))) {
                return true;
            }
        }
        return false;
    }

    private String generateCode(String base32Secret, long timeStep) {
        byte[] key = BASE32.decode(base32Secret.toUpperCase(Locale.ROOT));
        byte[] counter = ByteBuffer.allocate(8).putLong(timeStep).array();
        byte[] hash = hmacSha1(key, counter);

        int offset = hash[hash.length - 1] & 0x0F;
        int binary = ((hash[offset] & 0x7F) << 24)
                | ((hash[offset + 1] & 0xFF) << 16)
                | ((hash[offset + 2] & 0xFF) << 8)
                | (hash[offset + 3] & 0xFF);
        int mod = (int) Math.pow(10, CODE_DIGITS);
        return String.format(Locale.ROOT, "%0" + CODE_DIGITS + "d", binary % mod);
    }

    private byte[] hmacSha1(byte[] key, byte[] data) {
        try {
            Mac mac = Mac.getInstance(HMAC_ALGO);
            mac.init(new SecretKeySpec(key, HMAC_ALGO));
            return mac.doFinal(data);
        } catch (Exception e) {
            throw new IllegalStateException("TOTP HMAC computation failed", e);
        }
    }

    private String generateRecoveryCode() {
        // 10 random alphanumeric chars, grouped for readability: xxxxx-xxxxx.
        String alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // no 0/O/1/I ambiguity
        StringBuilder sb = new StringBuilder(11);
        for (int i = 0; i < 10; i++) {
            if (i == 5) {
                sb.append('-');
            }
            sb.append(alphabet.charAt(secureRandom.nextInt(alphabet.length())));
        }
        return sb.toString();
    }

    private String urlEncode(String value) {
        return java.net.URLEncoder.encode(value, java.nio.charset.StandardCharsets.UTF_8);
    }
}
