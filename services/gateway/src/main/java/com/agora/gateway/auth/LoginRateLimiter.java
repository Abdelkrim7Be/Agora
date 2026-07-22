package com.agora.gateway.auth;

import com.agora.gateway.audit.AuditRepository;
import com.agora.gateway.config.GatewayProperties;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.time.Clock;
import java.time.Instant;
import java.util.Locale;

@Service
public class LoginRateLimiter {

    private static final String LOGIN_ACTION = "login";
    private static final String FAILURE_OUTCOME = "failure";

    private final AuditRepository auditRepository;
    private final GatewayProperties.LoginRateLimit config;
    private final Clock clock;

    @Autowired
    public LoginRateLimiter(AuditRepository auditRepository, GatewayProperties properties) {
        this(auditRepository, properties.getLoginRateLimit(), Clock.systemUTC());
    }

    LoginRateLimiter(
            AuditRepository auditRepository,
            GatewayProperties.LoginRateLimit config,
            Clock clock
    ) {
        this.auditRepository = auditRepository;
        this.config = config;
        this.clock = clock;
        if (config.getWindowSeconds() < 1
                || config.getMaxFailures() < 1
                || config.getGlobalMaxFailures() < config.getMaxFailures()) {
            throw new IllegalArgumentException(
                    "Login rate-limit settings must be positive and global max must be >= account max"
            );
        }
    }

    public Decision check(String username) {
        if (!config.isEnabled()) {
            return Decision.permit();
        }

        Instant cutoff = clock.instant().minusSeconds(config.getWindowSeconds());
        String normalizedUsername = normalizeUsername(username);
        long accountFailures = auditRepository
                .countByUsernameIgnoreCaseAndActionAndOutcomeAndTimestampAfter(
                        normalizedUsername,
                        LOGIN_ACTION,
                        FAILURE_OUTCOME,
                        cutoff
                );
        long globalFailures = auditRepository
                .countByActionAndOutcomeAndTimestampAfter(
                        LOGIN_ACTION,
                        FAILURE_OUTCOME,
                        cutoff
                );

        boolean blocked = accountFailures >= config.getMaxFailures()
                || globalFailures >= config.getGlobalMaxFailures();
        return blocked
                ? Decision.deny(config.getWindowSeconds())
                : Decision.permit();
    }

    static String normalizeUsername(String username) {
        return username == null ? "" : username.strip().toLowerCase(Locale.ROOT);
    }

    public record Decision(boolean allowed, long retryAfterSeconds) {
        static Decision permit() {
            return new Decision(true, 0);
        }

        static Decision deny(long retryAfterSeconds) {
            return new Decision(false, Math.max(1, retryAfterSeconds));
        }
    }
}
