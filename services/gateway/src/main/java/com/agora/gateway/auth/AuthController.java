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
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

import java.time.Duration;
import java.util.Map;
import java.util.Optional;

@RestController
public class AuthController {

    static final String REFRESH_COOKIE = "agora_refresh";

    private final UserRepository users;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;
    private final AuditService auditService;
    private final LoginRateLimiter loginRateLimiter;
    private final TokenSessionService tokenSessions;
    private final boolean secureCookies;

    public AuthController(
            UserRepository users,
            PasswordEncoder passwordEncoder,
            JwtService jwtService,
            AuditService auditService,
            LoginRateLimiter loginRateLimiter,
            TokenSessionService tokenSessions,
            GatewayProperties properties
    ) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
        this.auditService = auditService;
        this.loginRateLimiter = loginRateLimiter;
        this.tokenSessions = tokenSessions;
        this.secureCookies = properties.getJwt().isSecureCookies();
    }

    record LoginRequest(
            @NotBlank @Size(max = 128) String username,
            @NotBlank @Size(max = 1024) String password
    ) {}

    record TokenResponse(
            String token,
            @JsonProperty("access_token") String accessToken,
            @JsonProperty("token_type") String tokenType,
            @JsonProperty("expires_in") long expiresIn,
            @JsonProperty("refresh_expires_in") long refreshExpiresIn
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
        auditService.record(
                authenticated.getUsername(),
                authenticated.getRole(),
                "login",
                "POST",
                "/auth/login",
                null,
                "success"
        );
        return tokenResponse(tokenSessions.issue(
                authenticated.getUsername(),
                authenticated.getRole()
        ));
    }

    @PostMapping("/auth/refresh")
    public ResponseEntity<?> refresh(
            @CookieValue(name = REFRESH_COOKIE, required = false) String refreshToken
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
            return tokenResponse(result.tokens());
        }

        String outcome = result.status() == TokenSessionService.RotationStatus.REPLAYED
                ? "replay_detected"
                : "failure";
        auditService.record(null, null, "token_refresh", "POST", "/auth/refresh", null, outcome);
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                .header(HttpHeaders.SET_COOKIE, clearRefreshCookie().toString())
                .body(Map.of("error", "invalid refresh token"));
    }

    @PostMapping("/auth/logout")
    public ResponseEntity<Void> logout(
            @RequestHeader(HttpHeaders.AUTHORIZATION) String authorization,
            @CookieValue(name = REFRESH_COOKIE, required = false) String refreshToken
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
                .header(HttpHeaders.SET_COOKIE, clearRefreshCookie().toString())
                .build();
    }

    private ResponseEntity<TokenResponse> tokenResponse(TokenSessionService.TokenPair pair) {
        TokenResponse response = new TokenResponse(
                pair.accessToken(),
                pair.accessToken(),
                "Bearer",
                jwtService.getTtlSeconds(),
                tokenSessions.getRefreshTtlSeconds()
        );
        return ResponseEntity.ok()
                .header(HttpHeaders.SET_COOKIE, refreshCookie(pair.refreshToken()).toString())
                .body(response);
    }

    private ResponseCookie refreshCookie(String token) {
        return ResponseCookie.from(REFRESH_COOKIE, token)
                .httpOnly(true)
                .secure(secureCookies)
                .sameSite("Strict")
                .path("/auth")
                .maxAge(Duration.ofSeconds(tokenSessions.getRefreshTtlSeconds()))
                .build();
    }

    private ResponseCookie clearRefreshCookie() {
        return ResponseCookie.from(REFRESH_COOKIE, "")
                .httpOnly(true)
                .secure(secureCookies)
                .sameSite("Strict")
                .path("/auth")
                .maxAge(Duration.ZERO)
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
