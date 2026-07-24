package com.agora.gateway.auth;

import com.agora.gateway.audit.AuditService;
import com.agora.gateway.config.GatewayProperties;
import com.agora.gateway.security.JwtService;
import com.agora.gateway.user.AppUser;
import com.agora.gateway.user.UserRepository;
import com.fasterxml.jackson.annotation.JsonProperty;
import io.jsonwebtoken.Claims;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;
import java.util.Optional;

@RestController
public class AuthController {

    private final UserRepository users;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;
    private final AuditService auditService;
    private final LoginRateLimiter loginRateLimiter;
    private final TokenSessionService tokenSessions;
    private final AuthCookies authCookies;
    private final long mfaChallengeTtlSeconds;

    public AuthController(
            UserRepository users,
            PasswordEncoder passwordEncoder,
            JwtService jwtService,
            AuditService auditService,
            LoginRateLimiter loginRateLimiter,
            TokenSessionService tokenSessions,
            AuthCookies authCookies,
            GatewayProperties properties
    ) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
        this.auditService = auditService;
        this.loginRateLimiter = loginRateLimiter;
        this.tokenSessions = tokenSessions;
        this.authCookies = authCookies;
        this.mfaChallengeTtlSeconds = properties.getMfa().getChallengeTtlMinutes() * 60L;
    }

    record LoginRequest(
            @NotBlank @Size(max = 128) String username,
            @NotBlank @Size(max = 1024) String password
    ) {}

    record MfaChallengeResponse(
            @JsonProperty("mfa_required") boolean mfaRequired,
            @JsonProperty("challenge_token") String challengeToken,
            @JsonProperty("expires_in") long expiresIn
    ) {}

    @PostMapping("/auth/login")
    public ResponseEntity<?> login(@Valid @RequestBody LoginRequest req) {
        LoginRateLimiter.Decision beforeAuthentication = loginRateLimiter.check(req.username());
        if (!beforeAuthentication.allowed()) {
            return rateLimited(req.username(), beforeAuthentication);
        }

        Optional<AppUser> user = users.findByUsername(req.username());
        boolean passwordOk = user.isPresent()
                && passwordEncoder.matches(req.password(), user.get().getPasswordHash());
        if (!passwordOk || !user.get().isEnabled()) {
            auditService.record(
                    req.username(),
                    null,
                    "login",
                    "POST",
                    "/auth/login",
                    null,
                    "failure"
            );
            LoginRateLimiter.Decision afterFailure = loginRateLimiter.check(req.username());
            if (!afterFailure.allowed()) {
                return rateLimited(req.username(), afterFailure);
            }
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "invalid credentials"));
        }

        AppUser authenticated = user.get();
        if (authenticated.isMfaEnabled()) {
            auditService.record(
                    authenticated.getUsername(),
                    authenticated.getRole(),
                    "login",
                    "POST",
                    "/auth/login",
                    null,
                    "password_ok_mfa_pending"
            );
            String challenge = jwtService.generateMfaChallenge(authenticated.getUsername());
            return ResponseEntity.ok(new MfaChallengeResponse(true, challenge, mfaChallengeTtlSeconds));
        }

        auditService.record(
                authenticated.getUsername(),
                authenticated.getRole(),
                "login",
                "POST",
                "/auth/login",
                null,
                "success"
        );
        return authCookies.tokenResponse(tokenSessions.issue(
                authenticated.getUsername(),
                authenticated.getRole()
        ));
    }

    @PostMapping("/auth/refresh")
    public ResponseEntity<?> refresh(
            @CookieValue(name = AuthCookies.REFRESH_COOKIE, required = false) String refreshToken
    ) {
        TokenSessionService.RotationResult result = tokenSessions.rotate(refreshToken);
        if (result.status() == TokenSessionService.RotationStatus.SUCCESS) {
            Claims claims = jwtService.parse(result.tokens().accessToken());
            auditService.record(
                    claims.getSubject(),
                    claims.get("role", String.class),
                    "token_refresh",
                    "POST",
                    "/auth/refresh",
                    null,
                    "success"
            );
            return authCookies.tokenResponse(result.tokens());
        }

        String outcome = result.status() == TokenSessionService.RotationStatus.REPLAYED
                ? "replay_detected"
                : "failure";
        auditService.record(null, null, "token_refresh", "POST", "/auth/refresh", null, outcome);
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                .header(HttpHeaders.SET_COOKIE, authCookies.clearRefreshCookie().toString())
                .body(Map.of("error", "invalid refresh token"));
    }

    @PostMapping("/auth/logout")
    public ResponseEntity<Void> logout(
            @RequestHeader(HttpHeaders.AUTHORIZATION) String authorization,
            @CookieValue(name = AuthCookies.REFRESH_COOKIE, required = false) String refreshToken
    ) {
        Claims claims = jwtService.parse(authorization.substring("Bearer ".length()));
        tokenSessions.logout(claims, refreshToken);
        auditService.record(
                claims.getSubject(),
                claims.get("role", String.class),
                "logout",
                "POST",
                "/auth/logout",
                null,
                "success"
        );
        return ResponseEntity.noContent()
                .header(HttpHeaders.SET_COOKIE, authCookies.clearRefreshCookie().toString())
                .build();
    }

    private ResponseEntity<?> rateLimited(
            String username,
            LoginRateLimiter.Decision decision
    ) {
        auditService.record(
                username,
                null,
                "login",
                "POST",
                "/auth/login",
                HttpStatus.TOO_MANY_REQUESTS.value(),
                "rate_limited"
        );
        return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                .header(
                        HttpHeaders.RETRY_AFTER,
                        Long.toString(decision.retryAfterSeconds())
                )
                .body(Map.of(
                        "error", "too many login attempts",
                        "retry_after_seconds", decision.retryAfterSeconds()
                ));
    }
}
