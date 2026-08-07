package com.agora.gateway.user;

import com.agora.gateway.audit.AuditService;
import com.agora.gateway.auth.LoginRateLimiter;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.Locale;
import java.util.Map;
import java.util.Optional;

/**
 * "I forgot my password": ask for a reset link by e-mail.
 *
 * The link is redeemed through the existing {@code /auth/invite/{token}} pair —
 * a reset and an invitation are the same act (prove you hold the address, then
 * set a password), so they share one token implementation, one throttle and one
 * page.
 *
 * <p>Three properties this endpoint must never lose:
 *
 * <ol>
 *   <li><b>The link is never in the response.</b> Unlike an invitation, which an
 *       authenticated administrator requests and may copy by hand, this is open
 *       to anyone. Returning the link here would let a stranger request a reset
 *       for any address and read the takeover link straight out of their own
 *       HTTP response. The link only ever leaves by e-mail.</li>
 *   <li><b>The answer never depends on the account.</b> Unknown address, disabled
 *       account, anonymized account and success all return the same 202. Any
 *       difference — status, body, or a measurably slower path — turns this into
 *       a way to test which addresses hold accounts.</li>
 *   <li><b>Disabled and anonymized accounts get no link at all.</b> A reset must
 *       not be a way back into an account that was deliberately closed.</li>
 * </ol>
 *
 * <p>With no SMTP relay configured, this endpoint accepts the request and sends
 * nothing. That is deliberate: the alternative — falling back to returning the
 * link, the way invitations do — is the vulnerability in (1).
 */
@RestController
public class PasswordResetController {

    private static final Logger log = LoggerFactory.getLogger(PasswordResetController.class);
    static final String RESET_REQUEST_ACTION = "password_reset_request";

    private final UserRepository users;
    private final InvitationService invitations;
    private final InvitationMailer mailer;
    private final LoginRateLimiter rateLimiter;
    private final AuditService auditService;

    public PasswordResetController(UserRepository users,
                                   InvitationService invitations,
                                   InvitationMailer mailer,
                                   LoginRateLimiter rateLimiter,
                                   AuditService auditService) {
        this.users = users;
        this.invitations = invitations;
        this.mailer = mailer;
        this.rateLimiter = rateLimiter;
        this.auditService = auditService;
    }

    public record ForgotPasswordRequest(@NotBlank String email) {}

    @PostMapping("/auth/forgot-password")
    public ResponseEntity<?> forgotPassword(@Valid @RequestBody ForgotPasswordRequest req) {
        LoginRateLimiter.Decision decision = rateLimiter.checkGlobal(RESET_REQUEST_ACTION);
        if (!decision.allowed()) {
            return ResponseEntity.status(429)
                    .header("Retry-After", String.valueOf(decision.retryAfterSeconds()))
                    .body(Map.of("error", "Too many attempts. Try again later."));
        }

        String address = req.email().strip().toLowerCase(Locale.ROOT);
        findEligible(address).ifPresent(user -> {
            InvitationService.Issued issued = invitations.createForReset(user);
            boolean sent = mailer.sendPasswordReset(user.getEmail(), user.getUsername(), issued.setupLink());
            if (!sent) {
                // No relay, or the relay refused. Say so in the log — without the
                // link, which is a bearer credential — so an operator can tell
                // "nobody configured SMTP" apart from "the mail never arrived".
                log.warn("password reset requested for a known account but no mail was sent"
                        + " (smtp configured: {})", mailer.smtpConfigured());
            }
            auditService.record(user.getUsername(), user.getRole(), RESET_REQUEST_ACTION,
                    "POST", "/auth/forgot-password", null, sent ? "sent" : "not_sent");
        });

        // Always the same answer. See (2) in the class comment.
        return ResponseEntity.accepted().body(Map.of(
                "status", "accepted",
                "message", "Si un compte existe pour cette adresse, un lien de réinitialisation vient d'être envoyé."
        ));
    }

    /**
     * The account allowed to receive a reset link, if any.
     *
     * Anonymized accounts are excluded by their disabled flag —
     * {@link UserAnonymizationService} clears the address and disables the
     * account, so there is nothing here to match and nothing to send to.
     */
    private Optional<AppUser> findEligible(String address) {
        if (address.isBlank()) return Optional.empty();
        return users.findAll().stream()
                .filter(user -> user.getEmail() != null
                        && address.equals(user.getEmail().strip().toLowerCase(Locale.ROOT)))
                .filter(AppUser::isEnabled)
                .findFirst();
    }
}
