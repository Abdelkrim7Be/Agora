package com.agora.gateway.audit;

import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.List;

@Service
public class AuditService {

    // Fixed link for the first row in the chain (and for rows written before
    // hash-chaining existed, so verifyChain can tell "legacy" from "tampered").
    static final String GENESIS_HASH = "0".repeat(64);

    private final AuditRepository repository;

    public AuditService(AuditRepository repository) {
        this.repository = repository;
    }

    // Synchronized: prevHash must be read and the new row linked to it as one step,
    // or two concurrent writes could both link to the same prevHash and fork the
    // chain. Sufficient for a single gateway instance; a horizontally scaled gateway
    // would need a DB-level lock instead (same shape as email-agent's run_lock).
    public synchronized void record(String username, String role, String action, String method,
                       String path, Integer upstreamStatus, String outcome) {
        String prevHash = repository.findTopByOrderByIdDesc()
                .map(AuditEvent::getHash)
                .filter(h -> h != null && !h.isBlank())
                .orElse(GENESIS_HASH);
        AuditEvent event = new AuditEvent(username, role, action, method, path, upstreamStatus, outcome);
        event.setPrevHash(prevHash);
        event.setHash(hashOf(prevHash, event));
        repository.save(event);
    }

    /** Recomputes every row's hash from its stored fields and checks the chain links. */
    public ChainVerification verifyChain() {
        List<AuditEvent> events = repository.findAllByOrderByIdAsc();
        String expectedPrev = GENESIS_HASH;
        int verified = 0;
        int unverifiable = 0;
        for (AuditEvent event : events) {
            if (event.getHash() == null || event.getPrevHash() == null) {
                // Predates hash-chaining. The chain cannot vouch for it, but it is
                // not evidence of tampering either — resume linking from here.
                unverifiable++;
                expectedPrev = GENESIS_HASH;
                continue;
            }
            if (!event.getPrevHash().equals(expectedPrev)) {
                return ChainVerification.broken(event.getId(), "prev_hash link does not match", verified, unverifiable);
            }
            String recomputed = hashOf(event.getPrevHash(), event);
            if (!recomputed.equals(event.getHash())) {
                return ChainVerification.broken(event.getId(), "stored hash does not match recomputed content", verified, unverifiable);
            }
            expectedPrev = event.getHash();
            verified++;
        }
        return ChainVerification.intact(verified, unverifiable);
    }

    static String hashOf(String prevHash, AuditEvent event) {
        String canonical = String.join("|",
                prevHash,
                event.getTimestamp() == null ? "" : event.getTimestamp().toString(),
                nullToEmpty(event.getUsername()),
                nullToEmpty(event.getRole()),
                nullToEmpty(event.getAction()),
                nullToEmpty(event.getMethod()),
                nullToEmpty(event.getPath()),
                event.getUpstreamStatus() == null ? "" : event.getUpstreamStatus().toString(),
                nullToEmpty(event.getOutcome())
        );
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(canonical.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(bytes.length * 2);
            for (byte b : bytes) {
                hex.append(String.format("%02x", b));
            }
            return hex.toString();
        } catch (NoSuchAlgorithmException exc) {
            throw new IllegalStateException("SHA-256 is not available", exc);
        }
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    public record ChainVerification(
            boolean valid,
            int verifiedCount,
            int unverifiableLegacyCount,
            Long brokenAtId,
            String reason
    ) {
        static ChainVerification intact(int verified, int unverifiableLegacy) {
            return new ChainVerification(true, verified, unverifiableLegacy, null, null);
        }

        static ChainVerification broken(Long id, String reason, int verified, int unverifiableLegacy) {
            return new ChainVerification(false, verified, unverifiableLegacy, id, reason);
        }
    }
}
