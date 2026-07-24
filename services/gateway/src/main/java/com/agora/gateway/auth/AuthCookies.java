package com.agora.gateway.auth;

import com.agora.gateway.config.GatewayProperties;
import com.agora.gateway.security.JwtService;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;

import java.time.Duration;

/** Builds the login/refresh response body + HttpOnly refresh cookie — shared
 * between AuthController (plain login) and MfaController (post-2FA login). */
@Component
public class AuthCookies {

    static final String REFRESH_COOKIE = "agora_refresh";

    private final JwtService jwtService;
    private final TokenSessionService tokenSessions;
    private final boolean secureCookies;

    public AuthCookies(JwtService jwtService, TokenSessionService tokenSessions, GatewayProperties properties) {
        this.jwtService = jwtService;
        this.tokenSessions = tokenSessions;
        this.secureCookies = properties.getJwt().isSecureCookies();
    }

    public ResponseEntity<TokenResponse> tokenResponse(TokenSessionService.TokenPair pair) {
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

    public ResponseCookie refreshCookie(String token) {
        return ResponseCookie.from(REFRESH_COOKIE, token)
                .httpOnly(true)
                .secure(secureCookies)
                .sameSite("Strict")
                .path("/auth")
                .maxAge(Duration.ofSeconds(tokenSessions.getRefreshTtlSeconds()))
                .build();
    }

    public ResponseCookie clearRefreshCookie() {
        return ResponseCookie.from(REFRESH_COOKIE, "")
                .httpOnly(true)
                .secure(secureCookies)
                .sameSite("Strict")
                .path("/auth")
                .maxAge(Duration.ZERO)
                .build();
    }
}
