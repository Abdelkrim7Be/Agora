package com.agora.gateway.auth;

import com.agora.gateway.audit.AuditService;
import com.agora.gateway.config.GatewayProperties;
import com.agora.gateway.security.JwtService;
import com.agora.gateway.security.TotpService;
import com.agora.gateway.user.AppUser;
import com.agora.gateway.user.UserRepository;
import com.fasterxml.jackson.annotation.JsonProperty;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.JwtException;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Collectors;

/**
 * TOTP second factor. /auth/mfa/setup and /auth/mfa/confirm run as an
 * authenticated self-service action (the owner is already logged in);
 * /auth/mfa/verify is the unauthenticated second leg of login, gated only by
 * possession of the short-lived challenge token AuthController issued.
 */
@RestController
public class MfaController {

    private static final String ISSUER = "Agora AI";

    private final UserRepository users;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;
    private final TotpService totpService;
    private final TokenSessionService tokenSessions;
    private final AuthCookies authCookies;
    private final AuditService auditService;
    private final int recoveryCodeCount;

    public MfaController(
            UserRepository users,
            PasswordEncoder passwordEncoder,
            JwtService jwtService,
            TotpService totpService,
            TokenSessionService tokenSessions,
            AuthCookies authCookies,
            AuditService auditService,
            GatewayProperties properties
    ) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
        this.totpService = totpService;
        this.tokenSessions = tokenSessions;
        this.authCookies = authCookies;
        this.auditService = auditService;
        this.recoveryCodeCount = properties.getMfa().getRecoveryCodeCount();
    }

    record SetupResponse(String secret, @JsonProperty("otpauth_uri") String otpAuthUri) {}

    record ConfirmRequest(@NotBlank String code) {}

    record RecoveryCodesResponse(@JsonProperty("recovery_codes") List<String> recoveryCodes) {}

    record DisableRequest(@NotBlank String password) {}

    record VerifyRequest(
            @NotBlank @JsonProperty("challenge_token") String challengeToken,
            String code,
            @JsonProperty("recovery_code") String recoveryCode
    ) {}

    @PostMapping("/auth/mfa/setup")
    public ResponseEntity<?> setup(Authentication auth) {
        AppUser user = requireUser(auth);
        String secret = totpService.generateSecret();
        user.setMfaSecret(secret);
        // mfaEnabled flips only after /confirm proves possession of the secret —
        // a half-finished enrollment must never weaken the existing login.
        users.save(user);
        auditService.record(user.getUsername(), user.getRole(), "mfa_setup", "POST", "/auth/mfa/setup", null, "issued");
        return ResponseEntity.ok(new SetupResponse(secret, totpService.otpAuthUri(ISSUER, user.getUsername(), secret)));
    }

    @PostMapping("/auth/mfa/confirm")
    public ResponseEntity<?> confirm(Authentication auth, @Valid @RequestBody ConfirmRequest req) {
        AppUser user = requireUser(auth);
        if (user.getMfaSecret() == null || !totpService.verify(user.getMfaSecret(), req.code())) {
            auditService.record(user.getUsername(), user.getRole(), "mfa_confirm", "POST", "/auth/mfa/confirm", null, "failure");
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(Map.of("error", "invalid code"));
        }

        List<String> plainCodes = totpService.generateRecoveryCodes(recoveryCodeCount);
        // Stream.toList() is immutable — Hibernate needs a mutable collection for
        // this @ElementCollection field (merge/clear/iterator.remove all mutate it).
        user.setMfaRecoveryCodeHashes(plainCodes.stream()
                .map(passwordEncoder::encode)
                .collect(Collectors.toCollection(ArrayList::new)));
        user.setMfaEnabled(true);
        users.save(user);
        auditService.record(user.getUsername(), user.getRole(), "mfa_confirm", "POST", "/auth/mfa/confirm", null, "enabled");
        return ResponseEntity.ok(new RecoveryCodesResponse(plainCodes));
    }

    @PostMapping("/auth/mfa/disable")
    public ResponseEntity<?> disable(Authentication auth, @Valid @RequestBody DisableRequest req) {
        AppUser user = requireUser(auth);
        if (!passwordEncoder.matches(req.password(), user.getPasswordHash())) {
            auditService.record(user.getUsername(), user.getRole(), "mfa_disable", "POST", "/auth/mfa/disable", null, "failure");
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(Map.of("error", "invalid password"));
        }
        user.setMfaEnabled(false);
        user.setMfaSecret(null);
        user.getMfaRecoveryCodeHashes().clear();
        users.save(user);
        auditService.record(user.getUsername(), user.getRole(), "mfa_disable", "POST", "/auth/mfa/disable", null, "disabled");
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/auth/mfa/verify")
    public ResponseEntity<?> verify(@Valid @RequestBody VerifyRequest req) {
        Claims claims;
        try {
            claims = jwtService.parseMfaChallenge(req.challengeToken());
        } catch (JwtException | IllegalArgumentException e) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(Map.of("error", "invalid or expired challenge"));
        }
        String username = claims.getSubject();
        Optional<AppUser> maybeUser = users.findByUsername(username);
        if (maybeUser.isEmpty() || !maybeUser.get().isEnabled() || !maybeUser.get().isMfaEnabled()) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(Map.of("error", "invalid or expired challenge"));
        }
        AppUser user = maybeUser.get();

        boolean verified = false;
        if (req.code() != null && !req.code().isBlank()) {
            verified = totpService.verify(user.getMfaSecret(), req.code());
        } else if (req.recoveryCode() != null && !req.recoveryCode().isBlank()) {
            verified = consumeRecoveryCode(user, req.recoveryCode());
        }

        if (!verified) {
            auditService.record(username, user.getRole(), "mfa_verify", "POST", "/auth/mfa/verify", null, "failure");
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(Map.of("error", "invalid code"));
        }

        auditService.record(username, user.getRole(), "mfa_verify", "POST", "/auth/mfa/verify", null, "success");
        return authCookies.tokenResponse(tokenSessions.issue(user.getUsername(), user.getRole()));
    }

    private boolean consumeRecoveryCode(AppUser user, String candidate) {
        List<String> hashes = user.getMfaRecoveryCodeHashes();
        var iterator = hashes.iterator();
        while (iterator.hasNext()) {
            if (passwordEncoder.matches(candidate, iterator.next())) {
                iterator.remove();
                users.save(user);
                return true;
            }
        }
        return false;
    }

    private AppUser requireUser(Authentication auth) {
        return users.findByUsername(auth.getName())
                .orElseThrow(() -> new IllegalStateException("Authenticated principal has no AppUser record"));
    }
}
