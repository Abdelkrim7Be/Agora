package com.agora.gateway.user;

import com.agora.gateway.audit.AuditService;
import com.agora.gateway.auth.LoginRateLimiter;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.Map;

/**
 * The two unauthenticated legs of onboarding: read an invitation, then redeem it.
 *
 * Both are reachable without a JWT — the invitee has no account yet — so the
 * token is the credential. Three properties matter here:
 *
 *  - the token never appears in an audit row or a log line;
 *  - unknown, expired and consumed tokens are indistinguishable in the response,
 *    so this cannot be used to enumerate users or probe outstanding invitations;
 *  - failures are counted and throttled globally, so tokens cannot be scanned.
 */
@RestController
public class InvitationController {

    static final String REDEEM_ACTION = "invite_redeem";
    private static final int MIN_PASSWORD_LENGTH = 12;

    private final InvitationService invitations;
    private final LoginRateLimiter rateLimiter;
    private final AuditService auditService;

    public InvitationController(InvitationService invitations,
                                LoginRateLimiter rateLimiter,
                                AuditService auditService) {
        this.invitations = invitations;
        this.rateLimiter = rateLimiter;
        this.auditService = auditService;
    }

    public record InviteStatusResponse(boolean valid, String username, Instant expiresAt) {}

    public record RedeemRequest(
            @NotBlank @Size(min = MIN_PASSWORD_LENGTH, message = "password must be at least 12 characters")
            String password
    ) {}

    @GetMapping("/auth/invite/{token}")
    public ResponseEntity<?> status(@PathVariable String token) {
        LoginRateLimiter.Decision decision = rateLimiter.checkGlobal(REDEEM_ACTION);
        if (!decision.allowed()) {
            return tooManyRequests(decision);
        }
        InvitationService.Status status = invitations.status(token);
        if (!status.valid()) {
            // Counted, so repeated probing trips the global throttle.
            audit(REDEEM_ACTION, "GET", "failure");
            return ResponseEntity.ok(new InviteStatusResponse(false, null, null));
        }
        return ResponseEntity.ok(new InviteStatusResponse(true, status.username(), status.expiresAt()));
    }

    @PostMapping("/auth/invite/{token}")
    public ResponseEntity<?> redeem(@PathVariable String token, @Valid @RequestBody RedeemRequest req) {
        LoginRateLimiter.Decision decision = rateLimiter.checkGlobal(REDEEM_ACTION);
        if (!decision.allowed()) {
            return tooManyRequests(decision);
        }
        if (!invitations.consume(token, req.password())) {
            audit(REDEEM_ACTION, "POST", "failure");
            return ResponseEntity.status(400).body(Map.of(
                    "error", "This invitation link is no longer valid. Ask an administrator to send a new one."
            ));
        }
        audit(REDEEM_ACTION, "POST", "success");
        return ResponseEntity.ok(Map.of("ok", true));
    }

    private ResponseEntity<?> tooManyRequests(LoginRateLimiter.Decision decision) {
        return ResponseEntity.status(429)
                .header("Retry-After", String.valueOf(decision.retryAfterSeconds()))
                .body(Map.of("error", "Too many attempts. Try again later."));
    }

    private void audit(String action, String method, String outcome) {
        // No actor and no token: an unredeemed invitation names nobody we are
        // willing to write down, and the token is a credential.
        auditService.record(null, null, action, method, "/auth/invite", null, outcome);
    }
}
